"""Baglanti alma, akilli kalite/boyut secimi, galeri ve kuyruk.

Kural:
- Mesajda "1080p" gibi bir ifade varsa o cozunurluge EN YAKIN kalite indirilir.
- Yoksa platforma gore varsayilan kalite kullanilir
  (Instagram 1280p, TikTok 1012p, YouTube 720p, digerleri 720p).
- Secilen kalite Telegram boyut limitini asacaksa bir alt kaliteye inilir.
- "mp3" / "ses" yazilirsa sadece ses indirilir.
- Instagram fotoğraf/carousel ve TikTok fotoğraf gönderileri gallery-dl ile
  indirilir.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from typing import Awaitable, Callable
from urllib.parse import urlparse

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..config import Settings
from ..database import Database
from ..downloader import DownloadError, Downloader, MediaInfo
from ..gallery import GalleryDownloader
from ..qol import (
    error_category,
    error_hint,
    media_buttons,
    quality_menu,
    retry_keyboard,
    video_label,
)
from ..queue import Job, JobQueue, QueueFullError, cached_video_kwargs
from ..utils import (
    UNSUPPORTED_URL_TEXT,
    escape,
    extract_urls,
    human_duration,
    human_size,
    is_blocked_url,
    parse_quality,
    preset_height,
    wants_audio,
    wants_document,
)

log = logging.getLogger(__name__)

router = Router(name="download")

_last_request: dict[int, float] = {}
_probe_retries: dict[str, tuple[int, int, int, str, str]] = {}
PROBE_TIMEOUT = 90
GALLERY_HOSTS = ("instagram.com", "instagr.am")
TIKTOK_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}


class SizeLimitError(Exception):
    """Secilen kalite Telegram limitine sigmiyor."""


def _url_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _is_tiktok_host(host: str) -> bool:
    return (
        host == "tiktok.com"
        or host.endswith(".tiktok.com")
        or host == "tiktokv.com"
        or host.endswith(".tiktokv.com")
    )


def _tiktok_path(url: str) -> str:
    try:
        return urlparse(url).path
    except ValueError:
        return ""


def _tiktok_photo_url(url: str) -> bool:
    """Doğrudan TikTok fotoğraf gönderisi mi? (Kısa linkler hariç.)"""
    return _is_tiktok_host(_url_host(url)) and "/photo/" in _tiktok_path(url)


def _tiktok_gallery_url(url: str) -> bool:
    """TikTok fotoğraf paylaşımı veya paylaşım kısa linki mi?"""
    host = _url_host(url)
    if host in TIKTOK_SHORT_HOSTS:
        return True
    if not _is_tiktok_host(host):
        return False
    path = _tiktok_path(url)
    return "/photo/" in path or path.startswith("/t/")


def _gallery_url(url: str) -> bool:
    host = _url_host(url)
    if _tiktok_gallery_url(url):
        return True
    return any(host == domain or host.endswith(f".{domain}") for domain in GALLERY_HOSTS)


def _gallery_platform(url: str) -> str:
    return "TikTok" if _is_tiktok_host(_url_host(url)) else "Instagram"


def _supports_url(
    downloader: Downloader, gallery: GalleryDownloader, url: str
) -> bool:
    """URL'yi yt-dlp site eşleştiricisi veya fotoğraf galerisi destekliyor mu?"""
    if downloader.supports_url(url):
        return True
    return bool(
        _gallery_url(url) and gallery is not None and gallery.available
    )


def _needs_gallery(info: MediaInfo) -> bool:
    """Video/ses akışı olmayan Instagram/TikTok gönderisi fotoğraftır."""
    return (
        info.platform in {"Instagram", "TikTok"}
        and not info.heights
        and not info.has_audio
    )


def _gallery_title(items: list[dict], platform: str = "Instagram") -> str:
    for key in ("description", "desc", "caption", "title", "username", "user"):
        value = items[0].get(key) if items else None
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:150]
    return f"{platform} gönderisi"


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
    as_document: bool = False,
) -> str:
    meta = [info.platform]
    if info.uploader:
        meta.append(escape(info.uploader[:40]))
    if info.duration:
        meta.append(human_duration(info.duration))

    lines = [f"🎬 <b>{escape(info.title[:200])}</b>", " • ".join(meta)]
    if info.heights:
        lines.append("📐 Mevcut: " + ", ".join(f"{h}p" for h in info.heights[:6]))
    if info.estimated_sizes and chosen and info.estimated_sizes.get(chosen):
        lines.append(f"📦 Tahmini: ~{human_size(info.estimated_sizes[chosen])}")
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
    if as_document:
        lines.append("📄 Dosya olarak gönderilecek…")
    return "\n".join(lines)


async def _limit_error(
    user_id: int, db: Database, settings: Settings, queue: JobQueue
) -> str | None:
    pending = queue.pending_for(user_id)
    if pending >= settings.max_queue_per_user:
        return "🧵 Kuyruğunuz dolu. Mevcut indirmeler bitince tekrar deneyin."
    if settings.daily_limit and not await db.is_promoted(user_id):
        used = await db.count_downloads_today(user_id)
        if used + pending >= settings.daily_limit:
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
    for attempt in range(2):
        try:
            items = await asyncio.wait_for(
                asyncio.to_thread(gallery.probe, url), timeout=PROBE_TIMEOUT
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if attempt == 0:
                log.warning("gallery-dl kesif hatasi, tekrar denenecek: %s", url)
                await asyncio.sleep(1)
                continue
            log.error("gallery-dl kesif hatasi: %s (%s)", url, exc)
            return None
        return items or None
    return None


async def _probe_downloader(
    downloader: Downloader,
    url: str,
    on_retry: Callable[[], Awaitable[None]] | None = None,
) -> MediaInfo:
    """yt-dlp kesifini bir hata durumunda bir kez daha dener."""
    for attempt in range(2):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(downloader.probe, url), timeout=PROBE_TIMEOUT
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - hata tipi cagirana aynen iletilir
            if (
                isinstance(exc, DownloadError)
                and str(exc) == UNSUPPORTED_URL_TEXT
            ):
                # Desteklenmeyen linkte tekrar denemenin anlami yok.
                raise
            if attempt == 0:
                log.warning(
                    "yt-dlp kesif hatasi, bir kez daha denenecek (%s): %s",
                    url,
                    exc,
                )
                if on_retry is not None:
                    with contextlib.suppress(Exception):
                        await on_retry()
                await asyncio.sleep(1)
                continue
            raise
    raise DownloadError("Bağlantı incelenemedi.")


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


def _remember_probe_failure(
    chat_id: int,
    user_id: int,
    message_id: int,
    text: str,
    url: str,
    *,
    info: MediaInfo | None = None,
    category: str | None = None,
) -> InlineKeyboardMarkup:
    token = uuid.uuid4().hex[:10]
    _probe_retries[token] = (chat_id, user_id, message_id, text, url)
    while len(_probe_retries) > 200:
        _probe_retries.pop(next(iter(_probe_retries)))
    return retry_keyboard(
        token,
        preset=f"h:{info.heights[0]}" if info and info.heights else None,
        has_lower=bool(info and len(info.heights) > 1),
        source_url=url,
        max_upload_mb=0,
        category=category or "generic",
    )


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
    if not any(_supports_url(downloader, gallery, url) for url in urls):
        # Desteklenmeyen linkler sessizce yok sayilir; probe veya mesaj yok.
        return

    user_id = message.from_user.id
    now = time.monotonic()
    last = _last_request.get(user_id, 0.0)
    if settings.cooldown_sec and now - last < settings.cooldown_sec:
        wait = int(settings.cooldown_sec - (now - last)) + 1
        await message.bot.send_message(
            message.chat.id,
            f"⏱ Çok hızlı gönderiyorsunuz. {wait} sn sonra tekrar deneyin.",
        )
        return

    _last_request[user_id] = now
    # Uzun calisan botlarda kullanici sozlugu sinirsiz buyumesin.
    if len(_last_request) > 10000:
        cutoff = now - max(settings.cooldown_sec, 60)
        for tracked_id, timestamp in list(_last_request.items()):
            if timestamp < cutoff:
                _last_request.pop(tracked_id, None)
    await _process_text(
        message.bot,
        message.chat.id,
        user_id,
        text,
        settings=settings,
        db=db,
        downloader=downloader,
        gallery=gallery,
        queue=queue,
    )


async def _process_text(
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    *,
    settings: Settings,
    db: Database,
    downloader: Downloader,
    gallery: GalleryDownloader,
    queue: JobQueue,
    only_url: str | None = None,
    existing_status: Message | None = None,
) -> None:
    urls = [only_url] if only_url else extract_urls(text)
    supported_urls = [url for url in urls if _supports_url(downloader, gallery, url)]
    if not supported_urls:
        # Desteklenmeyen linkler icin mesaj birakma. Yeniden deneme
        # dugmesinden gelindiyse eski durum mesajini de temizle.
        if existing_status is not None:
            await queue.delete_status(chat_id, existing_status.message_id)
            await queue.forget_status(chat_id, existing_status.message_id)
        return

    error = await _limit_error(user_id, db, settings, queue)
    if error:
        if existing_status is not None:
            await queue.edit_status(
                chat_id,
                existing_status.message_id,
                f"❌ {escape(error)}",
                wait=True,
            )
            await queue.forget_status(chat_id, existing_status.message_id)
        else:
            await bot.send_message(chat_id, error)
        return

    user = await db.get_user(user_id) or {}
    requested, audio = _request_options(text, user)
    show_caption = bool(user.get("show_caption", 1))
    as_document = wants_document(text)

    reuse_status = existing_status
    for url in supported_urls:
        if reuse_status is not None:
            status = reuse_status
            reuse_status = None
            await queue.edit_status(
                chat_id,
                status.message_id,
                "🔎 Bağlantı inceleniyor…",
                wait=True,
            )
        else:
            status = await queue.send_status(chat_id, "🔎 Bağlantı inceleniyor…")

        if await is_blocked_url(url):
            await queue.edit_status(
                chat_id,
                status.message_id,
                "🚫 Bu bağlantı güvenlik nedeniyle işlenemiyor.",
                wait=True,
            )
            await queue.forget_status(chat_id, status.message_id)
            continue

        info: MediaInfo | None = None
        gallery_items: list[dict] | None = None
        gallery_checked = False

        # yt-dlp TikTok fotoğraf gönderilerini desteklemiyor; doğrudan
        # gallery-dl kullan. Kısa linkler önce yt-dlp ile denenir.
        if _tiktok_photo_url(url) and gallery.available:
            gallery_items = await _probe_gallery(gallery, url)
            gallery_checked = True

        async def _retry_notice() -> None:
            await queue.edit_status(
                chat_id,
                status.message_id,
                "🔁 <b>Bağlantı yeniden deneniyor…</b>",
                wait=True,
            )

        try:
            if gallery_items is None:
                info = await _probe_downloader(
                    downloader, url, on_retry=_retry_notice
                )
        except asyncio.TimeoutError:
            await queue.edit_status(
                chat_id,
                status.message_id,
                "⌛️ Platform yanıt vermedi. Lütfen tekrar deneyin.",
                keyboard=_remember_probe_failure(
                    chat_id, user_id, status.message_id, text, url
                ),
                wait=True,
            )
            await queue.forget_status(chat_id, status.message_id)
            continue
        except DownloadError as exc:
            if not gallery_checked and _gallery_url(url):
                gallery_items = await _probe_gallery(gallery, url)
            if not gallery_items:
                if gallery_checked:
                    # TikTok fotoğraf gönderisi tanınıyor; galeri geçici
                    # olarak başarısız olduysa sessiz kalmak yerine bildir.
                    await queue.edit_status(
                        chat_id,
                        status.message_id,
                        "❌ Bu gönderideki fotoğraflar alınamadı.",
                        keyboard=_remember_probe_failure(
                            chat_id, user_id, status.message_id, text, url
                        ),
                        wait=True,
                    )
                    await queue.forget_status(chat_id, status.message_id)
                    continue
                if str(exc) == UNSUPPORTED_URL_TEXT:
                    # Yerel kontrolu gecmis olsa da yt-dlp URL'yi tanimadi;
                    # desteklenmeyen linklerde oldugu gibi sessizce bitir.
                    await queue.delete_status(chat_id, status.message_id)
                    continue
                category = error_category(str(exc))
                await queue.edit_status(
                    chat_id,
                    status.message_id,
                    f"❌ {escape(str(exc))}\n\n"
                    f"{error_hint(category, max_upload_mb=settings.max_upload_mb)}",
                    keyboard=_remember_probe_failure(
                        chat_id,
                        user_id,
                        status.message_id,
                        text,
                        url,
                        category=category,
                    ),
                    wait=True,
                )
                await queue.forget_status(chat_id, status.message_id)
                continue
        except Exception:  # noqa: BLE001
            log.exception("Probe hatasi: %s", url)
            await queue.edit_status(
                chat_id,
                status.message_id,
                "❌ Bağlantı incelenemedi. Lütfen tekrar deneyin.",
                keyboard=_remember_probe_failure(
                    chat_id, user_id, status.message_id, text, url
                ),
                wait=True,
            )
            await queue.forget_status(chat_id, status.message_id)
            continue

        if info is not None and _needs_gallery(info):
            gallery_items = await _probe_gallery(gallery, url)
            if gallery_items is None:
                await queue.edit_status(
                    chat_id,
                    status.message_id,
                    "❌ Bu gönderide indirilebilir video bulunamadı.",
                    keyboard=_remember_probe_failure(
                        chat_id, user_id, status.message_id, text, url
                    ),
                    wait=True,
                )
                await queue.forget_status(chat_id, status.message_id)
                continue

        # ---------------------------------------------------------- galeri
        if gallery_items is not None:
            gallery_platform = _gallery_platform(url)
            title = _gallery_title(gallery_items, gallery_platform)
            job = Job(
                id=uuid.uuid4().hex[:10],
                user_id=user_id,
                chat_id=chat_id,
                status_message_id=status.message_id,
                url=url,
                preset="gallery",
                title=title,
                platform=gallery_platform,
                kind="gallery",
                source_url=url,
                show_caption=show_caption,
                as_document=as_document,
                status_header=(
                    f"🖼 <b>{len(gallery_items)} medya bulundu</b>\n{escape(title)}"
                ),
            )
            try:
                queued = await queue.submit(job)
            except QueueFullError as exc:
                await queue.edit_status(
                    chat_id,
                    status.message_id,
                    f"❌ {escape(str(exc))}",
                    wait=True,
                )
                await queue.forget_status(chat_id, status.message_id)
                continue
            except Exception:  # noqa: BLE001 - DB/kuyruk hatasi mesaji asili birakmasin
                log.exception("Galeri isi kuyruga alinamadi: %s", url)
                await queue.edit_status(
                    chat_id,
                    status.message_id,
                    "❌ İşlem sıraya alınamadı. Lütfen tekrar deneyin.",
                    wait=True,
                )
                await queue.forget_status(chat_id, status.message_id)
                continue
            if not queued:
                # Ayni is zaten suruyor: fazladan durum mesaji birakma.
                await queue.delete_status(chat_id, status.message_id)
            continue

        assert info is not None  # bu noktada her zaman dolu (yukarida kontrol edildi)

        try:
            preset, target, chosen, downgraded = _resolve_preset(
                info, requested, audio, settings
            )
        except SizeLimitError as exc:
            await queue.edit_status(
                chat_id,
                status.message_id,
                f"❌ {escape(str(exc))}\n\n"
                f"{error_hint('size', max_upload_mb=settings.max_upload_mb)}",
                keyboard=_remember_probe_failure(
                    chat_id,
                    user_id,
                    status.message_id,
                    text,
                    url,
                    info=info,
                    category="size",
                ),
                wait=True,
            )
            await queue.forget_status(chat_id, status.message_id)
            continue

        status_header = _info_text(
            info,
            requested,
            target,
            chosen,
            preset,
            downgraded,
            settings,
            as_document,
        )
        job = Job(
            id=uuid.uuid4().hex[:10],
            user_id=user_id,
            chat_id=chat_id,
            status_message_id=status.message_id,
            url=info.webpage_url,
            preset=preset,
            title=info.title,
            platform=info.platform,
            duration=info.duration,
            source_url=info.webpage_url,
            show_caption=show_caption,
            as_document=as_document,
            status_header=status_header,
        )
        try:
            queued = await queue.submit(job)
        except QueueFullError as exc:
            await queue.edit_status(
                chat_id,
                status.message_id,
                f"❌ {escape(str(exc))}",
                wait=True,
            )
            await queue.forget_status(chat_id, status.message_id)
            continue
        except Exception:  # noqa: BLE001 - DB/kuyruk hatasi mesaji asili birakmasin
            log.exception("Indirme isi kuyruga alinamadi: %s", url)
            await queue.edit_status(
                chat_id,
                status.message_id,
                "❌ İşlem sıraya alınamadı. Lütfen tekrar deneyin.",
                wait=True,
            )
            await queue.forget_status(chat_id, status.message_id)
            continue
        if not queued:
            # Ayni is zaten suruyor: fazladan durum mesaji birakma.
            await queue.delete_status(chat_id, status.message_id)

        log.info(
            "Kuyruga eklendi: kullanici=%s platform=%s hedef=%s secilen=%s preset=%s",
            user_id,
            info.platform,
            target,
            chosen,
            preset,
        )


# --------------------------------------------------------------- buton aksiyonlari


@router.callback_query(F.data.startswith("retrytext|"))
async def on_retry_text(
    callback: CallbackQuery,
    settings: Settings,
    db: Database,
    downloader: Downloader,
    gallery: GalleryDownloader,
    queue: JobQueue,
) -> None:
    if callback.message is None:
        await callback.answer()
        return

    token = (callback.data or "").split("|", 1)[1]
    await _retry_probe(callback, token, settings, db, downloader, gallery, queue)


async def _retry_probe(
    callback: CallbackQuery,
    token: str,
    settings: Settings,
    db: Database,
    downloader: Downloader,
    gallery: GalleryDownloader,
    queue: JobQueue,
) -> None:
    """Kesif (probe) hata kartindaki yeniden denemeyi isler."""
    if callback.message is None:
        await callback.answer()
        return
    item = _probe_retries.get(token)
    if item is None:
        await callback.answer(
            "Bu deneme artık geçersiz. Linki tekrar gönderin.", show_alert=True
        )
        return

    chat_id, user_id, message_id, text, url = item
    if (
        callback.message.chat.id != chat_id
        or callback.from_user.id != user_id
        or callback.message.message_id != message_id
    ):
        await callback.answer(
            "Bu buton başka bir isteğe ait. Linki kendiniz gönderin.",
            show_alert=True,
        )
        return
    _probe_retries.pop(token, None)

    await callback.answer("Yeniden denendi 🔁")
    await _process_text(
        callback.bot,
        chat_id,
        user_id,
        text,
        settings=settings,
        db=db,
        downloader=downloader,
        gallery=gallery,
        queue=queue,
        only_url=url,
        existing_status=callback.message,
    )


@router.callback_query(F.data.startswith("retry|"))
async def on_retry(
    callback: CallbackQuery,
    settings: Settings,
    db: Database,
    downloader: Downloader,
    gallery: GalleryDownloader,
    queue: JobQueue,
) -> None:
    if callback.message is None:
        await callback.answer()
        return

    job_id = (callback.data or "").split("|", 1)[1]
    if job_id in _probe_retries:
        await _retry_probe(callback, job_id, settings, db, downloader, gallery, queue)
        return
    if not await queue.retry(
        job_id,
        callback.message.chat.id,
        callback.message.message_id,
        callback.from_user.id,
    ):
        await callback.answer(
            "Bu iş artık yeniden denenemiyor. Linki tekrar gönderin.",
            show_alert=True,
        )
        return

    await callback.answer("Yeniden denendi 🔁")


async def _resend_cached(bot: Bot, chat_id: int, row: dict, timeout: int) -> None:
    caption = f"<b>{escape((row.get('title') or 'Medya')[:200])}</b>"
    media_type = row["media_type"]
    keyboard = media_buttons(
        source_url=row.get("url"),
        cache_key=row["cache_key"],
        can_audio=media_type != "audio",
        can_document=media_type == "video",
        can_thumbnail=media_type == "video",
        can_quality=media_type != "audio" and bool(row.get("height")),
        in_group=chat_id < 0,
    )
    if media_type == "audio":
        await bot.send_audio(
            chat_id=chat_id,
            audio=row["file_id"],
            caption=caption,
            reply_markup=keyboard,
            request_timeout=timeout,
        )
    elif media_type == "document":
        await bot.send_document(
            chat_id=chat_id,
            document=row["file_id"],
            caption=caption,
            reply_markup=keyboard,
            request_timeout=timeout,
        )
    else:
        await bot.send_video(
            chat_id=chat_id,
            video=row["file_id"],
            caption=caption,
            reply_markup=keyboard,
            supports_streaming=True,
            request_timeout=timeout,
            **cached_video_kwargs(row),
        )


async def _send_cover(
    bot: Bot, chat_id: int, message: Message, row: dict, timeout: int
) -> bool:
    """Videonun onizleme/gorselini 'kapak' olarak gonderir (yeniden indirmez)."""
    thumb = getattr(message, "video", None)
    thumb = getattr(thumb, "thumbnail", None) if thumb is not None else None
    if thumb is None:
        return False
    await bot.send_photo(
        chat_id=chat_id,
        photo=thumb.file_id,
        caption=f"🖼 <b>{escape((row.get('title') or 'Kapak')[:200])}</b>",
        request_timeout=timeout,
    )
    return True


def _quality_job_for(
    row: dict, user_id: int, preset: str, heights: list[int] | None = None
) -> Job:
    platform = row.get("platform") or "Medya"
    title = row.get("title") or "Medya"
    if preset == "audio":
        header = f"🎵 <b>{escape(title[:200])}</b>\n{escape(platform)} · MP3"
    else:
        header = (
            f"🎬 <b>{escape(title[:200])}</b>\n{escape(platform)} · "
            f"{video_label(preset)}"
        )
    return Job(
        id=uuid.uuid4().hex[:10],
        user_id=user_id,
        chat_id=0,
        status_message_id=0,
        url=row["url"],
        preset=preset,
        title=title,
        platform=platform,
        source_url=row.get("url"),
        status_header=header,
        heights=list(heights or []),
    )


async def _queue_with_status(
    callback: CallbackQuery,
    *,
    db: Database,
    settings: Settings,
    queue: JobQueue,
    preset: str,
    row: dict,
    heights: list[int] | None = None,
    as_document_override: bool | None = None,
    origin_message: Message | None = None,
) -> None:
    """Onbellekteki medyadan yeni bir indirme (kalite/ses/dosya) baslatir."""
    error = await _limit_error(callback.from_user.id, db, settings, queue)
    if error:
        await callback.answer(error, show_alert=True)
        return
    chat_id = callback.message.chat.id
    user = await db.get_user(callback.from_user.id) or {}
    job = _quality_job_for(row, callback.from_user.id, preset, heights=heights)
    job.chat_id = chat_id
    job.show_caption = bool(user.get("show_caption", 1))
    if as_document_override is not None:
        job.as_document = as_document_override
    elif preset.endswith("|doc"):
        job.as_document = True
    else:
        job.as_document = str(user.get("download_mode") or "video") == "document"
    job.audio_bitrate = int(user.get("audio_bitrate") or 192)
    job.video_codec = str(user.get("video_codec") or "auto")
    status = await queue.send_status(chat_id, "⬇️ <b>Hazırlanıyor…</b>")
    job.status_message_id = status.message_id
    try:
        queued = await queue.submit(job)
    except QueueFullError as exc:
        await queue.edit_status(
            chat_id, status.message_id, f"❌ {escape(str(exc))}", wait=True
        )
        await queue.forget_status(chat_id, status.message_id)
        await callback.answer(str(exc), show_alert=True)
        return
    if not queued:
        await queue.delete_status(chat_id, status.message_id)
    await callback.answer("Sıraya alındı ⏳" if queued else "Bu içerik zaten indiriliyor ⏳")
    if origin_message is not None:
        with contextlib.suppress(TelegramBadRequest):
            await origin_message.edit_reply_markup(reply_markup=None)


def _quality_heights(row: dict) -> list[int]:
    """Onbellek satirindaki preset'ten yola cikip kalite listesi uretir."""
    current = preset_height(row.get("preset") or "")
    if current is None:
        return []
    base = [144, 240, 360, 480, 720, 1080, 1440, 2160]
    heights = {h for h in base if h <= current}
    heights.add(current)
    return sorted(heights, reverse=True)


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

    if action == "cancel":
        state = await queue.cancel(
            key, callback.message.chat.id, callback.from_user.id
        )
        await callback.answer(
            {
                "cancelled": "İptal edildi 🚫",
                "already": "Bu iş zaten iptal edildi.",
            }.get(state, "İş bulunamadı veya size ait değil."),
            show_alert=state == "missing",
        )
        return

    row = await db.cache_get_by_key(key)
    if row is None:
        await callback.answer(
            "Bu içerik artık önbellekte yok. Linki tekrar gönderin.", show_alert=True
        )
        return

    if action == "again":
        error = await _limit_error(callback.from_user.id, db, settings, queue)
        if error:
            await callback.answer(error, show_alert=True)
            return
        try:
            await _resend_cached(
                callback.bot,
                callback.message.chat.id,
                row,
                settings.upload_timeout_sec,
            )
        except TelegramNetworkError as exc:
            log.warning("Onbellek gonderim yaniti alinamadi: %s", exc)
            await callback.answer(
                "Gönderim yanıtı alınamadı. Video geldiyse işlem tamam; "
                "gelmediyse tekrar deneyin.",
                show_alert=True,
            )
            return
        except TelegramBadRequest as exc:
            log.info("Onbellek gecersiz (%s): %s", key, exc)
            await db.cache_delete(key)
            await callback.answer(
                "Önbellek geçersizleşti, linki tekrar gönderin.", show_alert=True
            )
            return
        try:
            await db.record_download(
                callback.from_user.id,
                row["url"],
                row.get("platform") or "",
                row.get("preset") or "cache",
                "ok",
                int(row.get("filesize") or 0),
            )
        except Exception:  # noqa: BLE001 - medya zaten gonderildi
            log.exception("Onbellek tekrar gonderimi kaydedilemedi")
        await callback.answer("Gönderildi 🔁")
        return

    if action == "cover":
        shown = await _send_cover(
            callback.bot,
            callback.message.chat.id,
            callback.message,
            row,
            settings.upload_timeout_sec,
        )
        await callback.answer(
            "Kapak gönderildi 🖼" if shown else "Bu videoda kapak görseli yok."
        )
        return

    if action == "qmenu":
        await callback.answer()
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(
                reply_markup=quality_menu(row.get("preset") or "", _quality_heights(row))
            )
        return

    if action == "qclose":
        await callback.answer()
        with contextlib.suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(
                reply_markup=media_buttons(
                    source_url=row.get("url"),
                    cache_key=key,
                    can_audio=row.get("media_type") != "audio",
                    can_document=row.get("media_type") == "video",
                    can_thumbnail=row.get("media_type") == "video",
                    can_quality=row.get("media_type") != "audio",
                    in_group=callback.message.chat.id < 0,
                )
            )
        return

    if action == "quality":
        preset = "audio" if key == "mp3" else f"h:{key}"
        await _queue_with_status(
            callback,
            db=db,
            settings=settings,
            queue=queue,
            preset=preset,
            row=row,
            heights=_quality_heights(row),
            origin_message=callback.message,
        )
        return

    if action in {"higher", "lower", "asdoc"}:
        heights = _quality_heights(row)
        current = preset_height(row.get("preset") or "")
        if action == "asdoc":
            new_preset = row.get("preset") or "best"
            new_preset += "|doc"
            override = True
        else:
            if current is None:
                await callback.answer("Bu içerik için kalite bilgisi yok.")
                return
            if action == "higher":
                options = [h for h in heights if h > current]
                new_preset = f"h:{min(options)}" if options else ""
            else:
                options = [h for h in heights if h < current]
                new_preset = f"h:{max(options)}" if options else ""
            override = None
            if not new_preset:
                await callback.answer(
                    "Daha yüksek kalite yok."
                    if action == "higher"
                    else "Daha düşük kalite yok."
                )
                return
        await _queue_with_status(
            callback,
            db=db,
            settings=settings,
            queue=queue,
            preset=new_preset,
            row=row,
            heights=heights,
            as_document_override=override,
        )
        return

    if action == "audio":
        await _queue_with_status(
            callback,
            db=db,
            settings=settings,
            queue=queue,
            preset="audio",
            row=row,
            heights=_quality_heights(row),
        )
        return

    await callback.answer()
