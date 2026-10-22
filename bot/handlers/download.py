"""Baglanti alma, akilli kalite/boyut secimi, galeri ve kuyruk.

Kural:
- Mesajda "1080p" gibi bir ifade varsa o cozunurluge EN YAKIN kalite indirilir.
- Yoksa platforma gore varsayilan kalite kullanilir
  (Instagram 1280p, TikTok 1012p, YouTube 720p, digerleri 720p).
- Secilen kalite Telegram boyut limitini asacaksa bir alt kaliteye inilir.
- "mp3" / "ses" yazilirsa sadece ses indirilir.
- Instagram fotoğraf/carousel gönderileri gallery-dl ile indirilir.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from ..config import Settings
from ..database import Database
from ..downloader import DownloadError, Downloader, MediaInfo
from ..gallery import GalleryDownloader
from ..keyboards import media_keyboard
from ..queue import Job, JobQueue, QueueFullError
from ..utils import (
    escape,
    extract_urls,
    human_duration,
    human_size,
    is_blocked_host,
    parse_quality,
    wants_audio,
)

log = logging.getLogger(__name__)

router = Router(name="download")

_last_request: dict[int, float] = {}
PROBE_TIMEOUT = 90
GALLERY_HOSTS = ("instagram.com", "instagr.am")


class SizeLimitError(Exception):
    """Secilen kalite Telegram limitine sigmiyor."""


def _gallery_url(url: str) -> bool:
    lowered = url.lower()
    return any(host in lowered for host in GALLERY_HOSTS)


def _needs_gallery(info: MediaInfo) -> bool:
    """Instagram gönderisinde video/ses akışı yoksa fotoğraf gönderisidir."""
    return info.platform == "Instagram" and not info.heights and not info.has_audio


def _gallery_title(items: list[dict]) -> str:
    for key in ("description", "caption", "title", "username"):
        value = items[0].get(key) if items else None
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:150]
    return "Instagram gönderisi"


def _resolve_preset(
    info: MediaInfo, requested: int | None, audio: bool, settings: Settings
) -> tuple[str, int | None, int | None, bool]:
    """(preset, hedef, secilen, boyut_nedeniyle_dusuruldu) dondurur."""
    if audio:
        return "audio", None, None, False

    limit = settings.max_upload_bytes
    target = requested or settings.default_height_for(info.platform)
    heights = info.heights
    if not heights:
        return "best", target, None, False

    ordered = sorted(heights, key=lambda h: (abs(h - target), -h))
    chosen: int | None = None
    for height in ordered:
        size = info.estimated_sizes.get(height)
        if not size or size <= limit:
            chosen = height
            break

    if chosen is None:
        smallest = min(heights, key=lambda h: info.estimated_sizes.get(h, 0) or 1)
        size = info.estimated_sizes.get(smallest, 0)
        if size and size > limit:
            raise SizeLimitError(
                f"En düşük kalite ({smallest}p) bile ~{human_size(size)} tutuyor. "
                f"Telegram limiti {settings.max_upload_mb} MB."
            )
        chosen = smallest

    return f"h:{chosen}", target, chosen, chosen != ordered[0]


def _info_text(
    info: MediaInfo,
    requested: int | None,
    target: int | None,
    chosen: int | None,
    preset: str,
    downgraded: bool,
    settings: Settings,
) -> str:
    meta = [info.platform]
    if info.uploader:
        meta.append(escape(info.uploader[:40]))
    if info.duration:
        meta.append(human_duration(info.duration))

    lines = [f"🎬 <b>{escape(info.title[:200])}</b>", " • ".join(meta)]
    if info.heights:
        lines.append("📐 Mevcut: " + ", ".join(f"{h}p" for h in info.heights[:6]))
    lines.append("")

    if preset == "audio":
        lines.append("🎵 Ses (MP3) indirilecek…")
    elif chosen is None:
        lines.append("🎬 En iyi kalitede indirilecek…")
    elif downgraded:
        lines.append(
            f"📦 Boyut için {target}p yerine <b>{chosen}p</b> indirilecek "
            f"(limit {settings.max_upload_mb} MB)…"
        )
    elif requested:
        lines.append(f"🎯 İstenen {requested}p → en yakın <b>{chosen}p</b> indirilecek…")
    else:
        lines.append(f"🎯 Varsayılan {target}p → <b>{chosen}p</b> indirilecek…")
    return "\n".join(lines)


async def _limit_error(
    user_id: int, db: Database, settings: Settings, queue: JobQueue
) -> str | None:
    if queue.pending_for(user_id) >= settings.max_queue_per_user:
        return "🧵 Kuyruğunuz dolu. Mevcut indirmeler bitince tekrar deneyin."
    if settings.daily_limit and not await db.is_promoted(user_id):
        used = await db.count_downloads_today(user_id)
        if used >= settings.daily_limit:
            return (
                f"📉 Günlük indirme limitinize ({settings.daily_limit}) ulaştınız. "
                "Yarın tekrar deneyebilirsiniz."
            )
    return None


async def _probe_gallery(
    gallery: GalleryDownloader | None, url: str
) -> list[dict] | None:
    if gallery is None or not gallery.available:
        return None
    try:
        items = await asyncio.wait_for(
            asyncio.to_thread(gallery.probe, url), timeout=PROBE_TIMEOUT
        )
    except Exception:  # noqa: BLE001
        log.exception("gallery-dl kesif hatasi: %s", url)
        return None
    return items or None


def _request_options(text: str, user: dict) -> tuple[int | None, bool]:
    """Link metni ve kullanici ayarlarindan kalite/ses tercihini cozer.

    Acikca yazilan kalite her zaman kullanici varsayilanindan onceliklidir.
    """
    requested = parse_quality(text)
    audio = wants_audio(text)
    if requested is None and not audio:
        requested = int(user.get("default_quality") or 0) or None
        audio = bool(user.get("audio_default"))
    return requested, audio


def _message_text(message: Message) -> str:
    """Kendi metni/caption'i bos ise yanitlanan mesajinkini de kullanir."""
    text = message.text or message.caption or ""
    reply = message.reply_to_message
    if reply is not None and not extract_urls(text):
        reply_text = reply.text or reply.caption or ""
        if reply_text:
            return f"{reply_text} {text}".strip()
    return text


@router.message(F.text | F.caption)
async def on_text(
    message: Message,
    settings: Settings,
    db: Database,
    downloader: Downloader,
    gallery: GalleryDownloader,
    queue: JobQueue,
) -> None:
    if message.from_user is None or message.from_user.is_bot:
        return

    text = _message_text(message)
    if not text:
        return
    if (message.text or "").startswith("/"):
        return

    urls = extract_urls(text)
    if not urls:
        return

    user_id = message.from_user.id
    now = time.monotonic()
    last = _last_request.get(user_id, 0.0)
    if settings.cooldown_sec and now - last < settings.cooldown_sec:
        wait = int(settings.cooldown_sec - (now - last)) + 1
        await message.reply(f"⏱ Çok hızlı gönderiyorsunuz. {wait} sn sonra tekrar deneyin.")
        return

    error = await _limit_error(user_id, db, settings, queue)
    if error:
        await message.reply(error)
        return

    _last_request[user_id] = now

    user = await db.get_user(user_id) or {}
    requested, audio = _request_options(text, user)
    show_caption = bool(user.get("show_caption", 1))

    for url in urls:
        if is_blocked_host(url):
            await message.reply("🚫 Bu bağlantı güvenlik nedeniyle işlenemiyor.")
            continue

        status = await message.reply("🔎 Bağlantı inceleniyor…")
        info: MediaInfo | None = None
        gallery_items: list[dict] | None = None

        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(downloader.probe, url), timeout=PROBE_TIMEOUT
            )
        except asyncio.TimeoutError:
            await status.edit_text("⌛️ Platform yanıt vermedi. Lütfen tekrar deneyin.")
            continue
        except DownloadError as exc:
            if _gallery_url(url):
                gallery_items = await _probe_gallery(gallery, url)
            if not gallery_items:
                await status.edit_text(f"❌ {escape(str(exc))}")
                continue
        except Exception:  # noqa: BLE001
            log.exception("Probe hatasi: %s", url)
            await status.edit_text("❌ Bağlantı incelenemedi. Lütfen tekrar deneyin.")
            continue

        if info is not None and _needs_gallery(info):
            gallery_items = await _probe_gallery(gallery, url)
            if gallery_items is None:
                await status.edit_text(
                    "❌ Bu gönderide indirilebilir video bulunamadı."
                )
                continue

        # ---------------------------------------------------------- galeri
        if gallery_items is not None:
            title = _gallery_title(gallery_items)
            with suppress(TelegramBadRequest):
                await status.edit_text(
                    f"🖼 <b>{len(gallery_items)} medya bulundu</b>\n"
                    f"{escape(title)}\n\nİndiriliyor…"
                )
            job = Job(
                id=uuid.uuid4().hex[:10],
                user_id=user_id,
                chat_id=message.chat.id,
                status_message_id=status.message_id,
                url=url,
                preset="gallery",
                title=title,
                platform="Instagram",
                kind="gallery",
                source_url=url,
                show_caption=show_caption,
            )
            try:
                queued = await queue.submit(job)
            except QueueFullError as exc:
                with suppress(TelegramBadRequest):
                    await status.edit_text(f"❌ {escape(str(exc))}")
                continue
            if not queued:
                with suppress(TelegramBadRequest):
                    await status.edit_text(
                        "⏳ Bu gönderi zaten indiriliyor; bitince buraya da gönderilecek."
                    )
            continue

        assert info is not None  # bu noktada her zaman dolu (yukarida kontrol edildi)

        try:
            preset, target, chosen, downgraded = _resolve_preset(
                info, requested, audio, settings
            )
        except SizeLimitError as exc:
            await status.edit_text(f"❌ {escape(str(exc))}")
            continue

        with suppress(TelegramBadRequest):
            await status.edit_text(
                _info_text(
                    info, requested, target, chosen, preset, downgraded, settings
                ),
                link_preview_options=LinkPreviewOptions(
                    url=info.webpage_url, prefer_small_media=True
                ),
            )

        job = Job(
            id=uuid.uuid4().hex[:10],
            user_id=user_id,
            chat_id=message.chat.id,
            status_message_id=status.message_id,
            url=info.webpage_url,
            preset=preset,
            title=info.title,
            platform=info.platform,
            duration=info.duration,
            source_url=info.webpage_url,
            show_caption=show_caption,
        )
        try:
            queued = await queue.submit(job)
        except QueueFullError as exc:
            with suppress(TelegramBadRequest):
                await status.edit_text(f"❌ {escape(str(exc))}")
            continue
        if not queued:
            with suppress(TelegramBadRequest):
                await status.edit_text(
                    "⏳ Bu bağlantı zaten indiriliyor; bitince buraya da gönderilecek."
                )

        log.info(
            "Kuyruga eklendi: kullanici=%s platform=%s hedef=%s secilen=%s preset=%s",
            user_id,
            info.platform,
            target,
            chosen,
            preset,
        )


# --------------------------------------------------------------- buton aksiyonlari


@router.callback_query(F.data.startswith("retry|"))
async def on_retry(callback: CallbackQuery, queue: JobQueue) -> None:
    if callback.message is None:
        await callback.answer()
        return

    job_id = (callback.data or "").split("|", 1)[1]
    if not await queue.retry(job_id, callback.message.chat.id):
        await callback.answer(
            "Bu iş artık yeniden denenemiyor. Linki tekrar gönderin.",
            show_alert=True,
        )
        return

    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Yeniden denendi 🔁")


async def _resend_cached(bot: Bot, chat_id: int, row: dict) -> None:
    caption = f"<b>{escape((row.get('title') or 'Medya')[:200])}</b>"
    media_type = row["media_type"]
    keyboard = media_keyboard(
        row.get("url"), row["cache_key"], allow_audio=media_type != "audio"
    )
    if media_type == "audio":
        await bot.send_audio(
            chat_id=chat_id, audio=row["file_id"], caption=caption, reply_markup=keyboard
        )
    elif media_type == "document":
        await bot.send_document(
            chat_id=chat_id,
            document=row["file_id"],
            caption=caption,
            reply_markup=keyboard,
        )
    else:
        await bot.send_video(
            chat_id=chat_id,
            video=row["file_id"],
            caption=caption,
            reply_markup=keyboard,
            supports_streaming=True,
        )


@router.callback_query(F.data.startswith("act|"))
async def on_action(
    callback: CallbackQuery,
    settings: Settings,
    db: Database,
    queue: JobQueue,
) -> None:
    parts = (callback.data or "").split("|", 2)
    if len(parts) != 3 or callback.message is None:
        await callback.answer()
        return

    _, action, key = parts
    row = await db.cache_get_by_key(key)
    if row is None:
        await callback.answer(
            "Bu içerik artık önbellekte yok. Linki tekrar gönderin.", show_alert=True
        )
        return

    if action == "again":
        try:
            await _resend_cached(callback.bot, callback.message.chat.id, row)
        except TelegramBadRequest as exc:
            log.info("Onbellek gecersiz (%s): %s", key, exc)
            await db.cache_delete(key)
            await callback.answer(
                "Önbellek geçersizleşti, linki tekrar gönderin.", show_alert=True
            )
            return
        await callback.answer("Gönderildi 🔁")
        return

    if action == "audio":
        error = await _limit_error(callback.from_user.id, db, settings, queue)
        if error:
            await callback.answer(error, show_alert=True)
            return
        status = await callback.bot.send_message(
            callback.message.chat.id, "🎵 <b>Ses hazırlanıyor…</b>"
        )
        user = await db.get_user(callback.from_user.id) or {}
        job = Job(
            id=uuid.uuid4().hex[:10],
            user_id=callback.from_user.id,
            chat_id=status.chat.id,
            status_message_id=status.message_id,
            url=row["url"],
            preset="audio",
            title=row.get("title") or "Ses",
            platform=row.get("platform") or "Medya",
            source_url=row.get("url"),
            show_caption=bool(user.get("show_caption", 1)),
        )
        try:
            queued = await queue.submit(job)
        except QueueFullError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.answer(
            "Sıraya alındı 🎵" if queued else "Bu içerik zaten indiriliyor ⏳"
        )
        return

    await callback.answer()
