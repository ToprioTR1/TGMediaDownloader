"""Telegram durum mesajlari icin hiz siniri, birlestirme ve flood kontrolu.

Butun ilerleme mesajlari bu modulden gecer. Amac:
- Ayni mesaji ayni anda birden fazla gorevin duzenlemesini engellemek.
- Kisa sure icinde gelen guncellemeleri birlestirip yalnizca sonuncuyu yazmak.
- Ayni sohbette mesaj duzenlemeleri arasinda minimum sure birakmak.
- TelegramRetryAfter geldiginde Telegram'in bildirdigi sure kadar bekleyip
  yeniden denemek.
- Ag/sunucu hatalarinda sinirli ve artan araliklarla tekrar denemek.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import LinkPreviewOptions, Message

log = logging.getLogger(__name__)

DEFAULT_EDIT_INTERVAL = 2.5
DEFAULT_PROGRESS_INTERVAL = 10.0
RETRY_MARGIN = 0.15
MAX_FLOOD_ATTEMPTS = 4
MAX_TRANSIENT_ATTEMPTS = 3


def keyboard_signature(keyboard: Any) -> Any:
    """Mesaj icerigi karsilastirmasi icin klavye imzasi."""
    if keyboard is None:
        return None
    rows = getattr(keyboard, "inline_keyboard", None)
    if rows is None:
        return repr(keyboard)
    return tuple(
        tuple(
            (
                getattr(button, "text", None),
                getattr(button, "callback_data", None),
                getattr(button, "url", None),
            )
            for button in row
        )
        for row in rows
    )


@dataclass(slots=True)
class _Update:
    text: str
    keyboard: Any = None
    preview: bool = False
    preview_url: str | None = None

    @property
    def key(self) -> tuple[Any, ...]:
        return (
            self.text,
            self.preview,
            self.preview_url,
            keyboard_signature(self.keyboard),
        )


class StatusCard:
    """Tek bir Telegram mesajinin bekleyen guncellemelerini yonetir."""

    def __init__(
        self,
        hub: "StatusHub",
        chat_id: int,
        message_id: int,
        *,
        initial_key: tuple[Any, ...] | None = None,
    ) -> None:
        self.hub = hub
        self.chat_id = chat_id
        self.message_id = message_id
        self._pending: _Update | None = None
        self._last_key = initial_key
        self._task: asyncio.Task[None] | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._deleted = False

    @property
    def deleted(self) -> bool:
        return self._deleted

    def set(
        self,
        text: str,
        *,
        keyboard: Any = None,
        preview: bool = False,
        preview_url: str | None = None,
    ) -> None:
        """Son istenen durumu kaydeder; ayni icerik ise istek uretmez."""
        if self._deleted:
            return
        update = _Update(text, keyboard, preview, preview_url)
        if self._pending is None and update.key == self._last_key:
            return
        self._pending = update
        self._idle.clear()
        self._ensure_flusher()

    def _ensure_flusher(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._flush(), name=f"status-{self.chat_id}-{self.message_id}"
            )

    async def flush(self) -> None:
        """Bekleyen guncellemelerin uygulanmasini bekler."""
        await self._idle.wait()

    async def stop(self) -> None:
        """Bekleyen guncellemeleri iptal eder (silme/kapanis icin)."""
        self._deleted = True
        self._pending = None
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._idle.set()

    async def _flush(self) -> None:
        try:
            while True:
                update = self._pending
                if update is None:
                    break
                self._pending = None
                if update.key == self._last_key:
                    continue
                try:
                    await self.hub._apply(self, update)
                except Exception:  # noqa: BLE001 - durum guncellemesi botu durdurmasin
                    log.exception("Durum mesaji guncellenemedi.")
                if self._deleted:
                    break
        finally:
            self._task = None
            if self._pending is not None and not self._deleted:
                self._ensure_flusher()
            else:
                self._idle.set()


class StatusHub:
    """Sohbet bazli durum mesaji trafigini yonetir."""

    def __init__(
        self,
        bot: Bot,
        *,
        min_interval: float = DEFAULT_EDIT_INTERVAL,
        progress_interval: float = DEFAULT_PROGRESS_INTERVAL,
        retry_backoff: float = 1.0,
    ) -> None:
        self.bot = bot
        self.min_interval = max(0.0, float(min_interval))
        self.progress_interval = max(0.05, float(progress_interval))
        self.retry_backoff = max(0.01, float(retry_backoff))
        self._cards: dict[tuple[int, int], StatusCard] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._chat_next: dict[int, float] = {}
        self._closed = False

    # ------------------------------------------------------------ genel API

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Any = None,
        preview: bool = False,
        preview_url: str | None = None,
    ) -> Message:
        """Durum mesajini gonderir ve takip kartini kaydeder."""
        if self._closed:
            raise RuntimeError("Durum yoneticisi kapandi.")
        update = _Update(text, keyboard, preview, preview_url)
        async with self._lock_for(chat_id):
            await self._wait_turn_locked(chat_id)
            message = await self._send_with_retry(chat_id, update)
            self._chat_next[chat_id] = time.monotonic() + self.min_interval
        self._cards[(chat_id, message.message_id)] = StatusCard(
            self,
            chat_id,
            message.message_id,
            initial_key=update.key,
        )
        return message

    async def edit(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        keyboard: Any = None,
        preview: bool = False,
        preview_url: str | None = None,
        wait: bool = False,
    ) -> None:
        """Mesaj icin son durumu kaydeder; istege bagli olarak bekler."""
        if self._closed:
            return
        key = (chat_id, message_id)
        card = self._cards.get(key)
        if card is None or card.deleted:
            card = StatusCard(self, chat_id, message_id)
            self._cards[key] = card
        card.set(
            text,
            keyboard=keyboard,
            preview=preview,
            preview_url=preview_url,
        )
        if wait:
            await card.flush()

    async def delete(self, chat_id: int, message_id: int) -> None:
        """Durum mesajini siler ve bekleyen duzenlemeleri iptal eder."""
        card = self._cards.pop((chat_id, message_id), None)
        if card is not None:
            await card.stop()
        if self._closed:
            return
        async with self._lock_for(chat_id):
            for attempt in range(MAX_FLOOD_ATTEMPTS):
                try:
                    await self.bot.delete_message(
                        chat_id=chat_id, message_id=message_id
                    )
                    return
                except TelegramRetryAfter as exc:
                    delay = max(0.05, float(exc.retry_after)) + RETRY_MARGIN
                    self._chat_next[chat_id] = time.monotonic() + delay
                    log.warning(
                        "Durum mesaji silme flood limiti; %.1f sn bekleniyor.",
                        delay,
                    )
                    if attempt == MAX_FLOOD_ATTEMPTS - 1:
                        return
                    await asyncio.sleep(delay)
                except TelegramBadRequest as exc:
                    if "message to delete not found" not in str(exc).lower():
                        log.debug("Durum mesaji silinemedi: %s", exc)
                    return
                except TelegramForbiddenError:
                    return
                except (TelegramNetworkError, TelegramServerError, asyncio.TimeoutError) as exc:
                    log.debug("Durum mesaji silme ag hatasi: %s", exc)
                    return

    async def close(self) -> None:
        """Kapanista bekleyen butun durum gorevlerini durdurur."""
        self._closed = True
        cards = list(self._cards.values())
        self._cards.clear()
        for card in cards:
            await card.stop()

    # -------------------------------------------------------------- ic isleyis

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock

    async def _wait_turn_locked(self, chat_id: int) -> None:
        """Sohbet bazli minimum bekleme; cagiran kisi sohbet kilidini tutmalidir."""
        while True:
            wait = self._chat_next.get(chat_id, 0.0) - time.monotonic()
            if wait <= 0:
                return
            await asyncio.sleep(wait)

    async def _apply(self, card: StatusCard, update: _Update) -> None:
        async with self._lock_for(card.chat_id):
            await self._wait_turn_locked(card.chat_id)
            # Beklerken daha yeni bir durum geldiyse eskiyi gondermeyip sonuncuyu yaz.
            if card._pending is not None:
                update = card._pending
                card._pending = None
            if update.key == card._last_key:
                return
            await self._perform_edit(card, update)

    async def _perform_edit(self, card: StatusCard, update: _Update) -> None:
        flood_attempts = 0
        transient_attempts = 0
        while not card.deleted:
            try:
                await self.bot.edit_message_text(
                    chat_id=card.chat_id,
                    message_id=card.message_id,
                    text=update.text,
                    reply_markup=update.keyboard,
                    link_preview_options=LinkPreviewOptions(
                        is_disabled=not update.preview,
                        url=update.preview_url if update.preview else None,
                        prefer_small_media=update.preview,
                    ),
                )
                card._last_key = update.key
                self._chat_next[card.chat_id] = time.monotonic() + self.min_interval
                return
            except TelegramRetryAfter as exc:
                flood_attempts += 1
                delay = max(0.05, float(exc.retry_after)) + RETRY_MARGIN
                self._chat_next[card.chat_id] = time.monotonic() + delay
                log.warning(
                    "Telegram mesaj duzenleme flood limiti: %s sn bekleniyor "
                    "(sohbet=%s, mesaj=%s).",
                    f"{delay:.1f}",
                    card.chat_id,
                    card.message_id,
                )
                if flood_attempts >= MAX_FLOOD_ATTEMPTS:
                    log.warning("Durum mesaji guncellemesi flood nedeniyle birakildi.")
                    return
                await asyncio.sleep(delay)
                if card._pending is not None:
                    update = card._pending
                    card._pending = None
                if update.key == card._last_key:
                    return
            except TelegramBadRequest as exc:
                message = str(exc).lower()
                if "message is not modified" in message:
                    card._last_key = update.key
                    self._chat_next[card.chat_id] = time.monotonic() + self.min_interval
                    return
                if (
                    "message to edit not found" in message
                    or "message is not found" in message
                    or "message can't be edited" in message
                ):
                    card._deleted = True
                    self._cards.pop((card.chat_id, card.message_id), None)
                    return
                log.debug("Durum mesaji duzenlenemedi: %s", exc)
                return
            except TelegramForbiddenError:
                card._deleted = True
                self._cards.pop((card.chat_id, card.message_id), None)
                return
            except (TelegramNetworkError, TelegramServerError, asyncio.TimeoutError) as exc:
                transient_attempts += 1
                if transient_attempts >= MAX_TRANSIENT_ATTEMPTS:
                    log.warning("Durum mesaji ag hatasi nedeniyle guncellenemedi: %s", exc)
                    return
                delay = min(self.retry_backoff * (2 ** (transient_attempts - 1)), 5.0)
                self._chat_next[card.chat_id] = time.monotonic() + delay
                log.debug("Durum mesaji ag hatasi; %.1f sn sonra tekrar: %s", delay, exc)
                await asyncio.sleep(delay)
                if card._pending is not None:
                    update = card._pending
                    card._pending = None
                if update.key == card._last_key:
                    return

    async def _send_with_retry(self, chat_id: int, update: _Update) -> Message:
        flood_attempts = 0
        transient_attempts = 0
        while True:
            try:
                return await self.bot.send_message(
                    chat_id=chat_id,
                    text=update.text,
                    reply_markup=update.keyboard,
                    link_preview_options=LinkPreviewOptions(
                        is_disabled=not update.preview,
                        url=update.preview_url if update.preview else None,
                        prefer_small_media=update.preview,
                    ),
                )
            except TelegramRetryAfter as exc:
                flood_attempts += 1
                delay = max(0.05, float(exc.retry_after)) + RETRY_MARGIN
                self._chat_next[chat_id] = time.monotonic() + delay
                log.warning(
                    "Telegram mesaj gonderme flood limiti: %.1f sn bekleniyor.",
                    delay,
                )
                if flood_attempts >= MAX_FLOOD_ATTEMPTS:
                    raise
                await asyncio.sleep(delay)
            except (TelegramNetworkError, TelegramServerError, asyncio.TimeoutError):
                transient_attempts += 1
                if transient_attempts >= MAX_TRANSIENT_ATTEMPTS:
                    raise
                delay = min(self.retry_backoff * (2 ** (transient_attempts - 1)), 5.0)
                self._chat_next[chat_id] = time.monotonic() + delay
                await asyncio.sleep(delay)


# ---------------------------------------------------------------- metin uretimi


def render_job_status(
    header: str,
    stage: str,
    detail: str | None = None,
    *,
    last_stage: str | None = None,
) -> str:
    """Isin tek durum mesajinda gosterilecek hiyerarsik metni uretir."""
    lines: list[str] = []
    if header and header.strip():
        lines.extend([header.strip(), ""])

    if stage == "queued":
        lines.extend(["✅ Bağlantı incelendi", "⏳ Sırada…"])
    elif stage == "cached":
        lines.extend(["⚡️ Önbellekten hazır", detail or "📤 Gönderiliyor…"])
    elif stage == "download":
        lines.extend(
            [
                "✅ Bağlantı incelendi",
                detail or "⏳ İndiriliyor…",
                "○ Gönderilecek",
            ]
        )
    elif stage == "sending":
        lines.extend(["✅ İndirildi", detail or "📤 Gönderiliyor…"])
    elif stage == "error":
        if last_stage == "sending":
            lines.append("✅ İndirildi")
        elif last_stage in {"download", "cached"}:
            lines.append("⚠️ İndirme tamamlanamadı")
        lines.append(detail or "❌ İşlem tamamlanamadı.")
    elif detail:
        lines.append(detail)

    return "\n".join(line for line in lines if line is not None).strip()
