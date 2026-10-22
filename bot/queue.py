"""Indirme kuyrugu: sinirli isci, onbellek, ilerleme ve Telegram gonderimi."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
    Message,
)

from .config import Settings
from .database import Database
from .downloader import DownloadError, Downloader
from .gallery import VIDEO_EXT, GalleryDownloader, GalleryError
from .keyboards import gallery_keyboard, media_keyboard
from .utils import escape, human_size, media_cache_key, quality_label, render_progress

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Job:
    id: str
    user_id: int
    chat_id: int
    status_message_id: int
    url: str
    preset: str
    title: str
    platform: str
    duration: int | None = None
    kind: str = "ytdlp"  # ytdlp | gallery
    source_url: str | None = None
    enqueued_at: float = field(default_factory=time.time)
    extra_targets: list[tuple[int, int]] = field(default_factory=list)
    sending: bool = False


class QueueFullError(Exception):
    """Kullanicinin kuyrugu dolu."""


class JobQueue:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        settings: Settings,
        downloader: Downloader,
        gallery: GalleryDownloader | None = None,
    ) -> None:
        self._bot = bot
        self._db = db
        self._settings = settings
        self._downloader = downloader
        self._gallery = gallery
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._pending: Counter[int] = Counter()
        self._jobs: dict[tuple[int, str, str], Job] = {}
        self._retry_jobs: dict[str, Job] = {}
        self._active = 0

    # ------------------------------------------------------------------ yasam

    async def start(self) -> None:
        for index in range(self._settings.concurrency):
            task = asyncio.create_task(self._worker(index), name=f"dl-worker-{index}")
            self._tasks.append(task)
        log.info("Kuyruk basladi (%s isci)", len(self._tasks))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    # ----------------------------------------------------------------- durum

    def pending_for(self, user_id: int) -> int:
        return self._pending[user_id]

    @property
    def queued_count(self) -> int:
        return self._queue.qsize()

    @property
    def active_count(self) -> int:
        return self._active

    async def retry(self, job_id: str, chat_id: int) -> bool:
        """Hata alan bir isi yeni bir durum mesajiyla tekrar kuyruga alir."""
        job = self._retry_jobs.pop(job_id, None)
        if job is None:
            return False

        status = await self._bot.send_message(chat_id, "🔁 <b>Yeniden deneniyor…</b>")
        new_job = Job(
            id=uuid.uuid4().hex[:10],
            user_id=job.user_id,
            chat_id=chat_id,
            status_message_id=status.message_id,
            url=job.url,
            preset=job.preset,
            title=job.title,
            platform=job.platform,
            duration=job.duration,
            kind=job.kind,
            source_url=job.source_url,
        )
        try:
            await self.submit(new_job)
        except QueueFullError as exc:
            with contextlib.suppress(TelegramBadRequest):
                await status.edit_text(f"❌ {escape(str(exc))}")
            return False
        return True

    async def submit(self, job: Job) -> bool:
        """Isi kuyruga ekler.

        Ayni kullanici ayni URL/preset icin zaten bekleyen bir is varsa yeni is
        acilmaz; yeni durum mesaji mevcut ise baglanir. Yeni is eklendiyse True,
        mevcut ise baglandiysa False doner.
        """
        key = (job.user_id, job.url, job.preset)
        existing = self._jobs.get(key)
        if existing is not None and existing is not job and not existing.sending:
            existing.extra_targets.append((job.chat_id, job.status_message_id))
            log.info(
                "Ayni ise baglandi: kullanici=%s url=%s preset=%s",
                job.user_id,
                job.url,
                job.preset,
            )
            return False

        if self._pending[job.user_id] >= self._settings.max_queue_per_user:
            raise QueueFullError(
                "Aynı anda en fazla "
                f"{self._settings.max_queue_per_user} indirme sıraya alabilirsiniz."
            )
        self._pending[job.user_id] += 1
        self._jobs[key] = job
        self._queue.put_nowait(job)
        return True

    # ----------------------------------------------------------------- isciler

    async def _worker(self, index: int) -> None:
        while True:
            job = await self._queue.get()
            self._active += 1
            try:
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Isci %s beklenmeyen hata: %s", index, job.url)
            finally:
                key = (job.user_id, job.url, job.preset)
                if self._jobs.get(key) is job:
                    self._jobs.pop(key, None)
                self._active -= 1
                self._pending[job.user_id] -= 1
                if self._pending[job.user_id] <= 0:
                    self._pending.pop(job.user_id, None)
                self._queue.task_done()

    async def _process(self, job: Job) -> None:
        if job.kind == "gallery":
            await self._process_gallery(job)
            return

        if self._settings.cache_days:
            cached = await self._db.cache_get(
                job.url, job.preset, self._settings.cache_days
            )
            if cached is not None:
                job.sending = True
                try:
                    await self._send_cached(job, cached)
                except TelegramBadRequest as exc:
                    log.info("Onbellek gecersiz, yeniden indirilecek (%s): %s", job.id, exc)
                    await self._db.cache_delete(cached["cache_key"])
                else:
                    size = int(cached["filesize"] or 0)
                    await self._db.record_download(
                        job.user_id, job.url, job.platform, job.preset, "ok", size
                    )
                    await self._delete(job)
                    log.info("Onbellekten gonderildi: %s (%s)", job.url, human_size(size))
                    return

        await self._download_and_send(job)

    # ----------------------------------------------------------------- indirme

    async def _download_and_send(self, job: Job) -> None:
        tmpdir = await self._mkdtemp(job)
        state: dict[str, Any] = {"status": "starting"}
        started = time.time()
        ticker = asyncio.create_task(self._progress_loop(job, state))

        try:
            await self._edit(job, "⬇️ <b>İndirme başlıyor…</b>")
            path = await asyncio.to_thread(
                self._downloader.download, job.url, job.preset, tmpdir, state
            )

            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker

            size = path.stat().st_size
            if size > self._settings.max_upload_bytes:
                raise DownloadError(
                    f"Dosya {human_size(size)} boyutunda. Telegram limiti "
                    f"{self._settings.max_upload_mb} MB. Daha düşük bir kalite deneyin."
                )

            await self._edit(job, f"📤 <b>Gönderiliyor…</b> ({human_size(size)})")
            job.sending = True
            file_id, media_type = await self._send(job, path)
            if file_id and self._settings.cache_days:
                try:
                    await self._db.cache_put(
                        media_cache_key(job.url, job.preset),
                        job.url,
                        job.preset,
                        file_id,
                        media_type,
                        job.title[:200],
                        job.platform,
                        size,
                    )
                except Exception:  # noqa: BLE001 - onbellek hatasi gonderimi bozmasin
                    log.exception("Onbellek yazilamadi: %s", job.id)

            await self._db.record_download(
                job.user_id, job.url, job.platform, job.preset, "ok", size
            )
            elapsed = time.time() - started
            # Durum mesajini sil: sohbette sadece gonderilen medya kalsin.
            await self._delete(job)
            log.info(
                "Tamamlandi kullanici=%s platform=%s boyut=%s sure=%.1fs",
                job.user_id,
                job.platform,
                human_size(size),
                elapsed,
            )
        except DownloadError as exc:
            await self._db.record_download(
                job.user_id, job.url, job.platform, job.preset, "error"
            )
            await self._fail(job, f"❌ {escape(str(exc))}")
        except TelegramForbiddenError:
            log.warning("Kullanici botu engellemis: %s", job.user_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Islem hatasi: %s", job.url)
            await self._db.record_download(
                job.user_id, job.url, job.platform, job.preset, "error"
            )
            await self._fail(
                job, "❌ Beklenmeyen bir hata oluştu. Lütfen tekrar deneyin."
            )
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker
            await asyncio.to_thread(shutil.rmtree, tmpdir, True)

    # ------------------------------------------------------------ galeri (foto)

    async def _process_gallery(self, job: Job) -> None:
        if self._gallery is None or not self._gallery.available:
            await self._edit(job, "❌ Fotoğraf indirme bu sunucuda etkin değil.")
            return

        tmpdir = await self._mkdtemp(job)
        try:
            await self._edit(job, "🖼 <b>Medya indiriliyor…</b>")
            files = await asyncio.to_thread(self._gallery.download, job.url, tmpdir)
            total = sum(path.stat().st_size for path in files)
            if total > self._settings.max_upload_bytes:
                raise DownloadError(
                    f"Gönderi {human_size(total)} boyutunda. Telegram limiti "
                    f"{self._settings.max_upload_mb} MB."
                )
            job.sending = True
            await self._send_gallery(job, files)
            await self._db.record_download(
                job.user_id, job.url, job.platform, "gallery", "ok", total
            )
            # Durum mesajini sil: sohbette sadece gonderilen medya kalsin.
            await self._delete(job)
        except (GalleryError, DownloadError) as exc:
            await self._db.record_download(
                job.user_id, job.url, job.platform, "gallery", "error"
            )
            await self._fail(job, f"❌ {escape(str(exc))}")
        except TelegramForbiddenError:
            log.warning("Kullanici botu engellemis: %s", job.user_id)
        except Exception:  # noqa: BLE001
            log.exception("Galeri hatasi: %s", job.url)
            await self._db.record_download(
                job.user_id, job.url, job.platform, "gallery", "error"
            )
            await self._fail(job, "❌ Medya gönderilemedi. Lütfen tekrar deneyin.")
        finally:
            await asyncio.to_thread(shutil.rmtree, tmpdir, True)

    async def _send_gallery(self, job: Job, files: list[Path]) -> None:
        caption = (
            f"<b>{escape(job.title[:150])}</b>\n{job.platform} • {len(files)} medya"
        )
        if len(files) > 1 and job.source_url:
            # Albumlerde buton desteklenmiyor; kaynak linkini aciklamaya koy.
            caption += f'\n<a href="{escape(job.source_url)}">🔗 Kaynak</a>'
        keyboard = gallery_keyboard(job.source_url)

        if len(files) == 1:
            path = files[0]
            for chat_id, _ in self._targets(job):
                file = FSInputFile(path, filename=path.name)
                if path.suffix.lower() in VIDEO_EXT:
                    await self._bot.send_video(
                        chat_id=chat_id,
                        video=file,
                        caption=caption,
                        reply_markup=keyboard,
                        supports_streaming=True,
                    )
                else:
                    await self._bot.send_photo(
                        chat_id=chat_id, photo=file, caption=caption, reply_markup=keyboard
                    )
            return

        for chat_id, _ in self._targets(job):
            media: list[InputMediaPhoto | InputMediaVideo] = []
            for index, path in enumerate(files[:10]):
                file = FSInputFile(path, filename=path.name)
                item_caption = caption if index == 0 else None
                if path.suffix.lower() in VIDEO_EXT:
                    media.append(
                        InputMediaVideo(
                            media=file, caption=item_caption, parse_mode=ParseMode.HTML
                        )
                    )
                else:
                    media.append(
                        InputMediaPhoto(
                            media=file, caption=item_caption, parse_mode=ParseMode.HTML
                        )
                    )
            await self._bot.send_media_group(chat_id=chat_id, media=media)

    # ------------------------------------------------------------- gonderim

    async def _send_cached(self, job: Job, row: dict[str, Any]) -> None:
        media_type = row["media_type"]
        size = int(row["filesize"] or 0)
        caption = self._caption(job, size)
        keyboard = (
            media_keyboard(
                job.source_url, row["cache_key"], allow_audio=media_type != "audio"
            )
            if self._settings.cache_days
            else gallery_keyboard(job.source_url)
        )
        for chat_id, _ in self._targets(job):
            if media_type == "audio":
                await self._bot.send_audio(
                    chat_id=chat_id,
                    audio=row["file_id"],
                    caption=caption,
                    reply_markup=keyboard,
                )
            elif media_type == "document":
                await self._bot.send_document(
                    chat_id=chat_id,
                    document=row["file_id"],
                    caption=caption,
                    reply_markup=keyboard,
                )
            else:
                await self._bot.send_video(
                    chat_id=chat_id,
                    video=row["file_id"],
                    caption=caption,
                    reply_markup=keyboard,
                    supports_streaming=True,
                )

    async def _send(self, job: Job, path: Path) -> tuple[str | None, str]:
        size = path.stat().st_size
        caption = self._caption(job, size)
        is_audio = job.preset == "audio" or path.suffix.lower() in {
            ".mp3",
            ".m4a",
            ".opus",
        }
        keyboard = (
            media_keyboard(
                job.source_url,
                media_cache_key(job.url, job.preset),
                allow_audio=not is_audio,
            )
            if self._settings.cache_days
            else gallery_keyboard(job.source_url)
        )

        cached_file_id: str | None = None
        cached_media_type = "video"
        for index, (chat_id, _) in enumerate(self._targets(job)):
            file = FSInputFile(path, filename=path.name)
            msg, media_type = await self._send_file(
                job, file, chat_id, caption, keyboard, is_audio
            )
            if index == 0:
                cached_file_id = _file_id(msg, media_type)
                cached_media_type = media_type
        return cached_file_id, cached_media_type

    async def _send_file(
        self,
        job: Job,
        file: FSInputFile,
        chat_id: int,
        caption: str,
        keyboard: Any,
        is_audio: bool,
    ) -> tuple[Message, str]:
        for attempt in range(2):
            try:
                if is_audio:
                    msg: Message = await self._bot.send_audio(
                        chat_id=chat_id,
                        audio=file,
                        caption=caption,
                        duration=job.duration or 0,
                        title=job.title[:64],
                        reply_markup=keyboard,
                    )
                    return msg, "audio"

                msg = await self._bot.send_video(
                    chat_id=chat_id,
                    video=file,
                    caption=caption,
                    duration=job.duration or 0,
                    supports_streaming=True,
                    reply_markup=keyboard,
                )
                return msg, "video"
            except TelegramRetryAfter as exc:
                if attempt == 0:
                    await asyncio.sleep(exc.retry_after + 1)
                    continue
                raise
            except TelegramBadRequest as exc:
                message = str(exc).lower()
                if "too big" in message or "entity too large" in message:
                    raise DownloadError(
                        "Telegram bu dosyayı kabul etmedi (boyut limiti). "
                        "Daha düşük kalite deneyin."
                    ) from exc
                log.warning("Video olarak gonderilemedi, belge olarak deneniyor: %s", exc)
                msg = await self._bot.send_document(
                    chat_id=chat_id,
                    document=file,
                    caption=caption,
                    reply_markup=keyboard,
                )
                return msg, "document"
        raise RuntimeError("Medya gönderilemedi.")

    def _caption(self, job: Job, size: int) -> str:
        title = escape(job.title[:200])
        return (
            f"<b>{title}</b>\n"
            f"{job.platform} • {quality_label(job.preset)} • {human_size(size)}"
        )

    # --------------------------------------------------------------- yardimci

    async def _mkdtemp(self, job: Job) -> Path:
        return Path(
            await asyncio.to_thread(
                tempfile.mkdtemp,
                prefix=f"dl-{job.id}-",
                dir=str(self._settings.download_dir),
            )
        )

    async def _progress_loop(self, job: Job, state: dict[str, Any]) -> None:
        while True:
            await asyncio.sleep(4)
            with contextlib.suppress(Exception):
                await self._edit(job, render_progress(state))

    def _targets(self, job: Job) -> list[tuple[int, int]]:
        return [(job.chat_id, job.status_message_id), *job.extra_targets]

    def _retry_keyboard(self, job: Job) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔁 Yeniden dene", callback_data=f"retry|{job.id}"
                    )
                ]
            ]
        )

    def _remember_retry(self, job: Job) -> None:
        self._retry_jobs[job.id] = job
        while len(self._retry_jobs) > 500:
            self._retry_jobs.pop(next(iter(self._retry_jobs)))

    async def _fail(self, job: Job, text: str) -> None:
        self._remember_retry(job)
        await self._edit(job, text, keyboard=self._retry_keyboard(job))

    async def _delete(self, job: Job) -> None:
        """Basarili gonderimden sonra durum mesajlarini siler."""
        for chat_id, message_id in self._targets(job):
            try:
                await self._bot.delete_message(
                    chat_id=chat_id, message_id=message_id
                )
            except TelegramBadRequest as exc:
                log.debug("Durum mesaji silinemedi (%s): %s", job.id, exc)
            except TelegramForbiddenError:
                pass

    async def _edit(
        self,
        job: Job,
        text: str,
        *,
        preview: bool = False,
        keyboard: Any = None,
    ) -> None:
        for chat_id, message_id in self._targets(job):
            try:
                await self._bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=keyboard,
                    link_preview_options=LinkPreviewOptions(is_disabled=not preview),
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    log.debug("Mesaj duzenlenemedi (%s): %s", job.id, exc)
            except TelegramForbiddenError:
                pass


def _file_id(message: Message, kind: str) -> str | None:
    media = getattr(message, kind, None)
    return getattr(media, "file_id", None)
