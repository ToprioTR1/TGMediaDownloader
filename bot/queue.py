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
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from .config import Settings
from .database import Database
from .downloader import DownloadError, Downloader
from .gallery import VIDEO_EXT, GalleryDownloader, GalleryError
from .keyboards import gallery_keyboard
from .media import VideoMeta, inspect_video
from .qol import media_buttons, video_label
from .status import (
    DEFAULT_EDIT_INTERVAL,
    DEFAULT_PROGRESS_INTERVAL,
    StatusHub,
    render_job_status,
)
from .utils import escape, human_size, media_cache_key, render_progress

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
    show_caption: bool = True
    as_document: bool = False
    status_header: str = ""
    status_stage: str = "queued"
    video_width: int | None = None
    video_height: int | None = None
    video_duration: int | None = None
    heights: list[int] = field(default_factory=list)

    @property
    def cache_preset(self) -> str:
        """Onbellek anahtarinda video ve dosya gonderimini ayirir."""
        return f"{self.preset}|doc" if self.as_document else self.preset


class QueueFullError(Exception):
    """Kullanicinin kuyrugu dolu."""


class DailyLimitError(QueueFullError):
    """Kullanicinin gunluk indirme hakki doldu."""


class JobQueue:
    def __init__(
        self,
        bot: Bot,
        db: Database,
        settings: Settings,
        downloader: Downloader,
        gallery: GalleryDownloader | None = None,
        *,
        status_min_interval: float = DEFAULT_EDIT_INTERVAL,
        status_progress_interval: float = DEFAULT_PROGRESS_INTERVAL,
        status_retry_backoff: float = 1.0,
    ) -> None:
        self._bot = bot
        self._db = db
        self._settings = settings
        self._downloader = downloader
        self._gallery = gallery
        self._status = StatusHub(
            bot,
            min_interval=status_min_interval,
            progress_interval=status_progress_interval,
            retry_backoff=status_retry_backoff,
        )
        self._progress_interval = self._status.progress_interval
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._submit_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        self._pending: Counter[int] = Counter()
        self._jobs: dict[tuple[int, int, str, str], Job] = {}
        self._retry_jobs: dict[str, Job] = {}
        self._tmpdirs: set[Path] = set()
        self._janitor: asyncio.Task[None] | None = None
        self._active = 0

    # ------------------------------------------------------------------ yasam

    async def start(self) -> None:
        for index in range(self._settings.concurrency):
            task = asyncio.create_task(self._worker(index), name=f"dl-worker-{index}")
            self._tasks.append(task)
        self._janitor = asyncio.create_task(
            self._cleanup_loop(), name="dl-temp-janitor"
        )
        log.info("Kuyruk basladi (%s isci)", len(self._tasks))

    async def stop(self) -> None:
        if self._janitor is not None:
            self._janitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._janitor
            self._janitor = None
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        await self._status.close()
        # Kapanista yarim kalan gecici klasorleri temizle.
        for path in list(self._tmpdirs):
            await asyncio.to_thread(shutil.rmtree, path, True)
            self._tmpdirs.discard(path)

    # ----------------------------------------------------------------- durum

    def pending_for(self, user_id: int) -> int:
        return self._pending[user_id]

    @property
    def queued_count(self) -> int:
        return self._queue.qsize()

    @property
    def active_count(self) -> int:
        return self._active

    # --------------------------------------------------------- durum mesaji

    async def send_status(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: Any = None,
        preview: bool = False,
        preview_url: str | None = None,
    ) -> Message:
        """Tek bir durum mesaji olusturur ve hiz siniri yoneticisine kaydeder."""
        return await self._status.send(
            chat_id,
            text,
            keyboard=keyboard,
            preview=preview,
            preview_url=preview_url,
        )

    async def edit_status(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        keyboard: Any = None,
        preview: bool = False,
        preview_url: str | None = None,
        wait: bool = True,
    ) -> None:
        """Mevcut durum mesajini gunceller; varsayilan olarak sonucu bekler."""
        await self._status.edit(
            chat_id,
            message_id,
            text,
            keyboard=keyboard,
            preview=preview,
            preview_url=preview_url,
            wait=wait,
        )

    async def delete_status(self, chat_id: int, message_id: int) -> None:
        await self._status.delete(chat_id, message_id)

    async def forget_status(self, chat_id: int, message_id: int) -> None:
        """Son hata/uyari mesajini bellekte tutmayi birakir."""
        await self._status.forget(chat_id, message_id)

    async def retry(
        self, job_id: str, chat_id: int, message_id: int, user_id: int
    ) -> bool:
        """Hata alan bir isi ayni mesaji duzenleyerek tekrar kuyruga alir."""
        job = self._retry_jobs.get(job_id)
        if (
            job is None
            or job.chat_id != chat_id
            or job.status_message_id != message_id
            or job.user_id != user_id
        ):
            return False
        self._retry_jobs.pop(job_id, None)

        # Yeni mesaj gondermek yerine mevcut hata mesajini durum mesajina cevir.
        await self._status.edit(
            chat_id,
            message_id,
            render_job_status(job.status_header, "queued"),
            wait=True,
        )

        new_job = Job(
            id=uuid.uuid4().hex[:10],
            user_id=job.user_id,
            chat_id=chat_id,
            status_message_id=message_id,
            url=job.url,
            preset=job.preset,
            title=job.title,
            platform=job.platform,
            duration=job.duration,
            kind=job.kind,
            source_url=job.source_url,
            show_caption=job.show_caption,
            as_document=job.as_document,
            status_header=job.status_header,
            video_width=job.video_width,
            video_height=job.video_height,
            video_duration=job.video_duration,
        )
        try:
            queued = await self.submit(new_job)
        except QueueFullError as exc:
            await self._status.edit(
                chat_id,
                message_id,
                render_job_status(
                    job.status_header, "error", f"❌ {escape(str(exc))}"
                ),
                wait=True,
            )
            await self._status.forget(chat_id, message_id)
            return False
        if not queued:
            # Ayni is zaten calisiyor; bu eski hata mesajini birakma.
            await self._status.delete(chat_id, message_id)
        return True

    async def submit(self, job: Job) -> bool:
        """Isi kuyruga ekler.

        Ayni kullanici ayni sohbette ayni URL/preset icin zaten bir is varsa
        yeni is acilmaz ve yeni durum mesaji baglanmaz. Yeni is eklendiyse True,
        is zaten varsa False doner.
        """
        key = (job.user_id, job.chat_id, job.url, job.cache_preset)
        # Tekilleştirme ve limit kontrolü tek bir kilit altında yapılır; aksi
        # halde aynı kullanıcının eşzamanlı istekleri günlük limiti aşabilir.
        async with self._submit_lock:
            existing = self._jobs.get(key)
            if existing is not None and existing is not job:
                log.info(
                    "Ayni is zaten var, yeni is acilmadi: kullanici=%s sohbet=%s url=%s preset=%s",
                    job.user_id,
                    job.chat_id,
                    job.url,
                    job.preset,
                )
                return False

            if self._pending[job.user_id] >= self._settings.max_queue_per_user:
                raise QueueFullError(
                    "Aynı anda en fazla "
                    f"{self._settings.max_queue_per_user} indirme sıraya alabilirsiniz."
                )

            if (
                self._settings.daily_limit
                and not await self._db.is_promoted(job.user_id)
            ):
                used = await self._db.count_downloads_today(job.user_id)
                if used + self._pending[job.user_id] >= self._settings.daily_limit:
                    raise DailyLimitError(
                        f"📉 Günlük indirme limitinize "
                        f"({self._settings.daily_limit}) ulaştınız. "
                        "Yarın tekrar deneyebilirsiniz."
                    )

            self._pending[job.user_id] += 1
            self._jobs[key] = job
            self._queue.put_nowait(job)
            job.status_stage = "queued"

        await self._status.edit(
            job.chat_id,
            job.status_message_id,
            render_job_status(job.status_header, "queued"),
        )
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
                preset = "gallery" if job.kind == "gallery" else job.preset
                with contextlib.suppress(Exception):
                    await self._record(job, "error", preset=preset)
                with contextlib.suppress(Exception):
                    await self._fail(
                        job,
                        "❌ Beklenmeyen bir hata oluştu. Lütfen tekrar deneyin.",
                    )
            finally:
                key = (job.user_id, job.chat_id, job.url, job.cache_preset)
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
                job.url, job.cache_preset, self._settings.cache_days
            )
            if cached is not None:
                try:
                    await self._set_stage(
                        job, "cached", "📤 Önbellekten gönderiliyor…"
                    )
                    await self._send_cached(job, cached)
                except TelegramNetworkError as exc:
                    log.warning(
                        "Onbellek gonderim yaniti alinamadi (%s): %s", job.id, exc
                    )
                    size = int(cached["filesize"] or 0)
                    await self._record(job, "unknown", size)
                    await self._delivery_uncertain(job)
                    return
                except TelegramBadRequest as exc:
                    log.info("Onbellek gecersiz, yeniden indirilecek (%s): %s", job.id, exc)
                    try:
                        await self._db.cache_delete(cached["cache_key"])
                    except Exception:  # noqa: BLE001 - onbellek temizligi indirmeyi durdurmasin
                        log.exception("Gecersiz onbellek kaydi silinemedi: %s", job.id)
                else:
                    size = int(cached["filesize"] or 0)
                    await self._finish_success(job, size)
                    log.info("Onbellekten gonderildi: %s (%s)", job.url, human_size(size))
                    return

        await self._download_and_send(job)

    # ----------------------------------------------------------------- indirme

    async def _download_and_send(self, job: Job) -> None:
        tmpdir = await self._mkdtemp(job)
        state: dict[str, Any] = {"status": "starting"}
        started = time.time()
        size = 0
        ticker: asyncio.Task[None] | None = None

        try:
            await self._set_stage(job, "download", "⬇️ <b>İndiriliyor…</b>")
            ticker = asyncio.create_task(self._progress_loop(job, state))
            path: Path | None = None
            for attempt in range(2):
                attempt_dir = tmpdir / f"try-{attempt + 1}"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                state.clear()
                state.update({"status": "starting"})
                try:
                    path = await asyncio.to_thread(
                        self._downloader.download,
                        job.url,
                        job.preset,
                        attempt_dir,
                        state,
                    )
                except Exception as exc:  # noqa: BLE001 - bir kez yeniden denenir
                    if attempt == 0:
                        log.warning(
                            "Indirme basarisiz, bir kez daha denenecek (%s): %s",
                            job.id,
                            exc,
                        )
                        await self._set_stage(
                            job,
                            "download",
                            "🔁 <b>İndirme başarısız. Tekrar deneniyor…</b>",
                        )
                        await asyncio.sleep(1)
                        continue
                    raise
                break
            if path is None:
                raise DownloadError("İndirme tamamlanamadı.")

            await self._stop_ticker(ticker)
            ticker = None

            size = path.stat().st_size
            if size > self._settings.max_upload_bytes:
                raise DownloadError(
                    f"Dosya {human_size(size)} boyutunda. Telegram limiti "
                    f"{self._settings.max_upload_mb} MB. Daha düşük bir kalite deneyin."
                )

            await self._set_stage(
                job, "sending", f"📤 <b>Gönderiliyor…</b> ({human_size(size)})"
            )
            file_id, media_type = await self._send(job, path)
            if file_id and self._settings.cache_days:
                try:
                    await self._db.cache_put(
                        media_cache_key(job.url, job.cache_preset),
                        job.url,
                        job.cache_preset,
                        file_id,
                        media_type,
                        job.title[:200],
                        job.platform,
                        size,
                        job.video_width,
                        job.video_height,
                        job.video_duration,
                    )
                except Exception:  # noqa: BLE001 - onbellek hatasi gonderimi bozmasin
                    log.exception("Onbellek yazilamadi: %s", job.id)

            elapsed = time.time() - started
            # Medya ulasti; kayit/silme hatasi basarili gonderimi hataya cevirmemeli.
            await self._finish_success(job, size)
            log.info(
                "Tamamlandi kullanici=%s platform=%s boyut=%s sure=%.1fs",
                job.user_id,
                job.platform,
                human_size(size),
                elapsed,
            )
        except DownloadError as exc:
            await self._stop_ticker(ticker)
            ticker = None
            await self._record(job, "error")
            await self._fail(job, f"❌ {escape(str(exc))}")
        except TelegramNetworkError as exc:
            # Yukleme sirasinda zaman asimi: Telegram dosyayi almis olabilir.
            # Yeniden gondermek videoyu iki kez dusurecegi icin otomatik denemeyiz.
            await self._stop_ticker(ticker)
            ticker = None
            log.warning("Medya gonderim yaniti alinamadi (%s): %s", job.id, exc)
            await self._record(job, "unknown", size)
            await self._delivery_uncertain(job)
        except TelegramForbiddenError:
            # Kullanici botu engellemis veya bot sohbetten cikarilmis. Durum
            # mesaji duzenlenemez; yine de kaydi yaz ve bellek kartini bırak.
            log.warning("Kullanici botu engellemis: %s", job.user_id)
            with contextlib.suppress(Exception):
                await self._record(job, "error")
            await self.forget_status(job.chat_id, job.status_message_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            await self._stop_ticker(ticker)
            ticker = None
            log.exception("Islem hatasi: %s", job.url)
            await self._record(job, "error")
            await self._fail(
                job, "❌ Beklenmeyen bir hata oluştu. Lütfen tekrar deneyin."
            )
        finally:
            await self._stop_ticker(ticker)
            await asyncio.to_thread(shutil.rmtree, tmpdir, True)
            self._tmpdirs.discard(tmpdir)

    # ------------------------------------------------------------ galeri (foto)

    async def _process_gallery(self, job: Job) -> None:
        if self._gallery is None or not self._gallery.available:
            await self._status.edit(
                job.chat_id,
                job.status_message_id,
                render_job_status(
                    job.status_header,
                    "error",
                    "❌ Fotoğraf indirme bu sunucuda etkin değil.",
                    last_stage=job.status_stage,
                ),
                wait=True,
            )
            await self.forget_status(job.chat_id, job.status_message_id)
            return

        tmpdir = await self._mkdtemp(job)
        total = 0
        try:
            await self._set_stage(job, "download", "🖼 <b>Medya indiriliyor…</b>")
            files: list[Path] = []
            for attempt in range(2):
                attempt_dir = tmpdir / f"try-{attempt + 1}"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                try:
                    files = await asyncio.to_thread(
                        self._gallery.download, job.url, attempt_dir
                    )
                except Exception as exc:  # noqa: BLE001 - bir kez yeniden denenir
                    if attempt == 0:
                        log.warning(
                            "Galeri indirmesi basarisiz, bir kez daha denenecek (%s): %s",
                            job.id,
                            exc,
                        )
                        await self._set_stage(
                            job,
                            "download",
                            "🔁 <b>Medya indirilemedi. Tekrar deneniyor…</b>",
                        )
                        await asyncio.sleep(1)
                        continue
                    raise
                if not files and attempt == 0:
                    log.warning(
                        "Galeri bos dondu, bir kez daha denenecek (%s)", job.id
                    )
                    await self._set_stage(
                        job,
                        "download",
                        "🔁 <b>Medya bulunamadı. Tekrar deneniyor…</b>",
                    )
                    await asyncio.sleep(1)
                    continue
                break
            if not files:
                raise GalleryError("Gönderi indirilemedi: medya bulunamadı")
            total = sum(path.stat().st_size for path in files)
            if total > self._settings.max_upload_bytes:
                raise DownloadError(
                    f"Gönderi {human_size(total)} boyutunda. Telegram limiti "
                    f"{self._settings.max_upload_mb} MB."
                )
            await self._set_stage(
                job, "sending", f"📤 <b>Gönderiliyor…</b> ({human_size(total)})"
            )
            await self._send_gallery(job, files)
            # Medya ulasti; kayit/silme hatasi basarili gonderimi hataya cevirmemeli.
            await self._finish_success(job, total, preset="gallery")
        except (GalleryError, DownloadError) as exc:
            await self._record(job, "error", preset="gallery")
            await self._fail(job, f"❌ {escape(str(exc))}")
        except TelegramNetworkError as exc:
            log.warning("Galeri gonderim yaniti alinamadi (%s): %s", job.id, exc)
            await self._record(job, "unknown", total, preset="gallery")
            await self._delivery_uncertain(job)
        except TelegramForbiddenError:
            log.warning("Kullanici botu engellemis: %s", job.user_id)
            with contextlib.suppress(Exception):
                await self._record(job, "error", preset="gallery")
            await self.forget_status(job.chat_id, job.status_message_id)
        except Exception:  # noqa: BLE001
            log.exception("Galeri hatasi: %s", job.url)
            await self._record(job, "error", preset="gallery")
            await self._fail(job, "❌ Medya gönderilemedi. Lütfen tekrar deneyin.")
        finally:
            await asyncio.to_thread(shutil.rmtree, tmpdir, True)
            self._tmpdirs.discard(tmpdir)

    async def _send_gallery(self, job: Job, files: list[Path]) -> None:
        caption: str | None = None
        if job.show_caption:
            caption = (
                f"<b>{escape(job.title[:150])}</b>\n{job.platform} · {len(files)} medya"
            )
        group_keyboard = gallery_keyboard(job.source_url)
        single_keyboard: Any = group_keyboard
        only_path = files[0] if len(files) == 1 else None
        if (
            only_path is not None
            and self._settings.cache_days
            and not job.as_document
            and only_path.suffix.lower() in VIDEO_EXT
        ):
            single_keyboard = self._media_keyboard(
                job,
                media_cache_key(job.url, f"{job.preset}|media"),
                can_audio=True,
                can_document=True,
                can_thumbnail=True,
                can_quality=bool(job.heights),
            )
        if len(files) > 1 and job.source_url and job.show_caption:
            # Albumlerde buton desteklenmez; kaynak linkini aciklamaya koy.
            caption = (caption or "") + (
                f'\n<a href="{escape(job.source_url)}">🔗 Kaynak</a>'
            )

        if job.as_document:
            if len(files) == 1:
                path = files[0]
                for chat_id, _ in self._targets(job):
                    await self._bot.send_document(
                        chat_id=chat_id,
                        document=FSInputFile(path, filename=path.name),
                        caption=caption,
                        reply_markup=group_keyboard,
                        request_timeout=self._settings.upload_timeout_sec,
                    )
                return

            for chat_id, _ in self._targets(job):
                documents: list[InputMediaDocument] = []
                for index, path in enumerate(files[:10]):
                    documents.append(
                        InputMediaDocument(
                            media=FSInputFile(path, filename=path.name),
                            caption=caption if index == 0 else None,
                            parse_mode=ParseMode.HTML,
                        )
                    )
                await self._bot.send_media_group(
                    chat_id=chat_id,
                    media=documents,
                    request_timeout=self._settings.upload_timeout_sec,
                )
            return

        if len(files) == 1:
            path = files[0]
            video_meta: VideoMeta | None = None
            if path.suffix.lower() in VIDEO_EXT:
                video_meta = await self._inspect_video(path)
                if video_meta is not None:
                    job.video_width = video_meta.width
                    job.video_height = video_meta.height
                    job.video_duration = video_meta.duration
            for chat_id, _ in self._targets(job):
                file = FSInputFile(path, filename=path.name)
                if video_meta is not None:
                    video_kwargs: dict[str, Any] = {
                        "width": video_meta.width,
                        "height": video_meta.height,
                        "duration": video_meta.duration,
                    }
                    if video_meta.thumbnail is not None:
                        video_kwargs["thumbnail"] = FSInputFile(
                            video_meta.thumbnail,
                            filename=video_meta.thumbnail.name,
                        )
                    await self._bot.send_video(
                        chat_id=chat_id,
                        video=file,
                        caption=caption,
                        reply_markup=single_keyboard,
                        supports_streaming=True,
                        request_timeout=self._settings.upload_timeout_sec,
                        **video_kwargs,
                    )
                elif path.suffix.lower() in VIDEO_EXT:
                    await self._bot.send_video(
                        chat_id=chat_id,
                        video=file,
                        caption=caption,
                        reply_markup=single_keyboard,
                        supports_streaming=True,
                        request_timeout=self._settings.upload_timeout_sec,
                    )
                else:
                    await self._bot.send_photo(
                        chat_id=chat_id,
                        photo=file,
                        caption=caption,
                        reply_markup=single_keyboard,
                        request_timeout=self._settings.upload_timeout_sec,
                    )
            return

        for chat_id, _ in self._targets(job):
            media: list[InputMediaPhoto | InputMediaVideo] = []
            for index, path in enumerate(files[:10]):
                file = FSInputFile(path, filename=path.name)
                item_caption = caption if index == 0 else None
                if path.suffix.lower() in VIDEO_EXT:
                    video_meta = await self._inspect_video(path)
                    video_kwargs = {}
                    if video_meta is not None:
                        video_kwargs.update(
                            {
                                "width": video_meta.width,
                                "height": video_meta.height,
                                "duration": video_meta.duration,
                            }
                        )
                        if video_meta.thumbnail is not None:
                            video_kwargs["thumbnail"] = FSInputFile(
                                video_meta.thumbnail,
                                filename=video_meta.thumbnail.name,
                            )
                    media.append(
                        InputMediaVideo(
                            media=file,
                            caption=item_caption,
                            parse_mode=ParseMode.HTML,
                            **video_kwargs,
                        )
                    )
                else:
                    media.append(
                        InputMediaPhoto(
                            media=file, caption=item_caption, parse_mode=ParseMode.HTML
                        )
                    )
            await self._bot.send_media_group(
                chat_id=chat_id,
                media=media,
                request_timeout=self._settings.upload_timeout_sec,
            )

    # ------------------------------------------------------------- gonderim

    def _media_keyboard(
        self,
        job: Job,
        cache_key: str,
        *,
        can_audio: bool,
        can_document: bool,
        can_thumbnail: bool,
        can_quality: bool,
    ) -> Any:
        return media_buttons(
            source_url=job.source_url,
            cache_key=cache_key,
            can_audio=can_audio,
            can_document=can_document,
            can_thumbnail=can_thumbnail,
            can_quality=can_quality,
            in_group=job.chat_id < 0,
        )

    async def _send_cached(self, job: Job, row: dict[str, Any]) -> None:
        media_type = row["media_type"]
        size = int(row["filesize"] or 0)
        caption = self._caption(job, size)
        keyboard = (
            self._media_keyboard(
                job,
                row["cache_key"],
                can_audio=media_type != "audio",
                can_document=media_type == "video",
                can_thumbnail=media_type == "video",
                can_quality=media_type != "audio" and bool(row.get("height")),
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
                    request_timeout=self._settings.upload_timeout_sec,
                )
            elif media_type == "document":
                await self._bot.send_document(
                    chat_id=chat_id,
                    document=row["file_id"],
                    caption=caption,
                    reply_markup=keyboard,
                    request_timeout=self._settings.upload_timeout_sec,
                )
            else:
                await self._bot.send_video(
                    chat_id=chat_id,
                    video=row["file_id"],
                    caption=caption,
                    reply_markup=keyboard,
                    supports_streaming=True,
                    request_timeout=self._settings.upload_timeout_sec,
                    **cached_video_kwargs(row, job.duration),
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
            self._media_keyboard(
                job,
                media_cache_key(job.url, job.cache_preset),
                can_audio=not is_audio,
                can_document=not is_audio and not job.as_document,
                can_thumbnail=not is_audio
                and not job.as_document
                and path.suffix.lower() in VIDEO_EXT,
                can_quality=not is_audio and bool(job.heights),
            )
            if self._settings.cache_days
            else gallery_keyboard(job.source_url)
        )

        video_meta: VideoMeta | None = None
        if not is_audio and not job.as_document and path.suffix.lower() in VIDEO_EXT:
            video_meta = await self._inspect_video(path)
            if video_meta is not None:
                job.video_width = video_meta.width
                job.video_height = video_meta.height
                job.video_duration = video_meta.duration

        cached_file_id: str | None = None
        cached_media_type = "video"
        for index, (chat_id, _) in enumerate(self._targets(job)):
            file = FSInputFile(path, filename=path.name)
            msg, media_type = await self._send_file(
                job,
                file,
                chat_id,
                caption,
                keyboard,
                is_audio,
                video_meta=video_meta,
            )
            if index == 0:
                cached_file_id = _file_id(msg, media_type)
                cached_media_type = media_type
        return cached_file_id, cached_media_type

    async def _inspect_video(self, path: Path) -> VideoMeta | None:
        try:
            return await asyncio.to_thread(inspect_video, path, path.parent)
        except Exception:  # noqa: BLE001 - metadata gonderimi durdurmasin
            log.exception("Video metadata okunamadi: %s", path)
            return None

    async def _send_file(
        self,
        job: Job,
        file: FSInputFile,
        chat_id: int,
        caption: str | None,
        keyboard: Any,
        is_audio: bool,
        *,
        video_meta: VideoMeta | None = None,
    ) -> tuple[Message, str]:
        for attempt in range(2):
            try:
                if job.as_document:
                    msg = await self._bot.send_document(
                        chat_id=chat_id,
                        document=file,
                        caption=caption,
                        reply_markup=keyboard,
                        request_timeout=self._settings.upload_timeout_sec,
                    )
                    return msg, "document"

                if is_audio:
                    msg: Message = await self._bot.send_audio(
                        chat_id=chat_id,
                        audio=file,
                        caption=caption,
                        duration=job.duration or 0,
                        title=job.title[:64],
                        performer=job.platform[:64],
                        reply_markup=keyboard,
                        request_timeout=self._settings.upload_timeout_sec,
                    )
                    return msg, "audio"

                video_kwargs: dict[str, Any] = {}
                if video_meta is not None:
                    video_kwargs["width"] = video_meta.width
                    video_kwargs["height"] = video_meta.height
                    video_kwargs["duration"] = (
                        video_meta.duration or job.duration or 0
                    )
                    if video_meta.thumbnail is not None:
                        video_kwargs["thumbnail"] = FSInputFile(
                            video_meta.thumbnail,
                            filename=video_meta.thumbnail.name,
                        )
                else:
                    video_kwargs["duration"] = job.duration or 0
                msg = await self._bot.send_video(
                    chat_id=chat_id,
                    video=file,
                    caption=caption,
                    supports_streaming=True,
                    reply_markup=keyboard,
                    request_timeout=self._settings.upload_timeout_sec,
                    **video_kwargs,
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
                    request_timeout=self._settings.upload_timeout_sec,
                )
                return msg, "document"
        raise RuntimeError("Medya gönderilemedi.")

    def _caption(self, job: Job, size: int) -> str | None:
        if not job.show_caption:
            return None
        title = escape(job.title[:200])
        label = video_label(job.cache_preset)
        return f"<b>{title}</b>\n{job.platform} · {label} · {human_size(size)}"

    # --------------------------------------------------------------- yardimci

    async def _mkdtemp(self, job: Job) -> Path:
        path = Path(
            await asyncio.to_thread(
                tempfile.mkdtemp,
                prefix=f"dl-{job.id}-",
                dir=str(self._settings.download_dir),
            )
        )
        self._tmpdirs.add(path)
        return path

    async def _cleanup_loop(self) -> None:
        """Eski gecici dosyalari periyodik olarak temizler."""
        while True:
            try:
                removed = await asyncio.to_thread(
                    self._cleanup_downloads, 6 * 3600
                )
                if removed:
                    log.info("%s eski gecici dosya/klasor temizlendi.", removed)
            except Exception:  # noqa: BLE001 - temizlik botu durdurmasin
                log.exception("Gecici dosya temizligi basarisiz")
            await asyncio.sleep(30 * 60)

    def _cleanup_downloads(self, max_age_sec: float) -> int:
        root = self._settings.download_dir
        if not root.exists():
            return 0

        # Bu fonksiyon thread'de calisir; aktif klasorlerin anlik kopyasini al.
        tracked = set(self._tmpdirs)
        now = time.time()
        removed = 0
        for entry in root.iterdir():
            # DOWNLOAD_DIR baska dosyalarla paylasilabilir (or. /data). Yalnizca
            # botun olusturdugu dl-* gecici girdilerine dokun.
            if not entry.name.startswith("dl-"):
                continue
            if entry in tracked:
                continue
            try:
                if now - entry.stat().st_mtime < max_age_sec:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                removed += 1
            except OSError:
                log.debug("Gecici giris temizlenemedi: %s", entry)
        return removed

    async def _stop_ticker(self, ticker: asyncio.Task[None] | None) -> None:
        if ticker is None:
            return
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker

    async def _progress_loop(self, job: Job, state: dict[str, Any]) -> None:
        while True:
            await asyncio.sleep(self._progress_interval)
            if job.status_stage != "download":
                return
            with contextlib.suppress(Exception):
                await self._set_stage(job, "download", render_progress(state))

    async def _set_stage(self, job: Job, stage: str, detail: str | None = None) -> None:
        """Tek durum mesajindaki asamayi gunceller (yeni mesaj gondermez)."""
        if job.status_stage == "error" and stage != "error":
            return
        job.status_stage = stage
        await self._status.edit(
            job.chat_id,
            job.status_message_id,
            render_job_status(job.status_header, stage, detail),
        )

    def _targets(self, job: Job) -> list[tuple[int, int]]:
        return [(job.chat_id, job.status_message_id)]

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

    async def _record(
        self, job: Job, status: str, filesize: int = 0, *, preset: str | None = None
    ) -> None:
        """Istatistigi yazar; DB hatasi medya/hatalari kullaniciya yansitmasin."""
        try:
            await self._db.record_download(
                job.user_id,
                job.url,
                job.platform,
                preset or job.preset,
                status,
                filesize,
            )
        except Exception:  # noqa: BLE001 - istatistik kaydi is akisini durdurmasin
            log.exception("Indirme kaydi yazilamadi: %s", job.id)

    async def _finish_success(
        self, job: Job, filesize: int, *, preset: str | None = None
    ) -> None:
        """Basarili gonderimden sonra kaydi yazar ve durum mesajini siler.

        Bu noktada medya kullaniciya ulasmistir. DB veya mesaj silme hatasi
        yuzunden isi yeniden 'hata' gibi gostermek yanlis olur.
        """
        await self._record(job, "ok", filesize, preset=preset)
        try:
            await self._delete(job)
        except Exception:  # noqa: BLE001 - gonderim zaten basarili
            log.exception("Basarili gonderim sonrasi durum mesaji silinemedi: %s", job.id)

    async def _fail(self, job: Job, text: str) -> None:
        self._remember_retry(job)
        last_stage = job.status_stage
        job.status_stage = "error"
        await self._status.edit(
            job.chat_id,
            job.status_message_id,
            render_job_status(
                job.status_header,
                "error",
                text,
                last_stage=last_stage,
            ),
            keyboard=self._retry_keyboard(job),
            wait=True,
        )
        # Hata mesaji kullanicida kalir; sonraki islemlerde gereksiz kart tutma.
        await self._status.forget(job.chat_id, job.status_message_id)

    async def _delivery_uncertain(self, job: Job) -> None:
        """Gonderim yaniti alinamadiginda videoyu tekrar gondermeden bilgilendirir."""
        last_stage = job.status_stage
        job.status_stage = "error"
        await self._status.edit(
            job.chat_id,
            job.status_message_id,
            render_job_status(
                job.status_header,
                "error",
                "⚠️ <b>Gönderim yanıtı alınamadı.</b>\n"
                "Video geldiyse işlem tamamlandı. Gelmediyse linki tekrar gönderin.",
                last_stage=last_stage,
            ),
            wait=True,
        )
        await self._status.forget(job.chat_id, job.status_message_id)

    async def _delete(self, job: Job) -> None:
        """Basarili gonderimden sonra durum mesajini siler."""
        for chat_id, message_id in self._targets(job):
            await self._status.delete(chat_id, message_id)


def cached_video_kwargs(
    row: dict[str, Any], fallback_duration: int | None = None
) -> dict[str, int]:
    """Onbellekteki video kaydindan Telegram boyut/sure parametreleri."""
    kwargs: dict[str, int] = {}
    for key in ("width", "height"):
        value = row.get(key)
        if isinstance(value, int) and value > 0:
            kwargs[key] = value
    duration = row.get("duration") or fallback_duration
    if isinstance(duration, int) and duration > 0:
        kwargs["duration"] = duration
    return kwargs


def _file_id(message: Message, kind: str) -> str | None:
    media = getattr(message, kind, None)
    return getattr(media, "file_id", None)
