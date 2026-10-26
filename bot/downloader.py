"""yt-dlp sarmalayicisi: bilgi cekme + indirme.

Tum bloklayici islemler `asyncio.to_thread` ile cagrilmalidir; bu modul
kendi icinde senkron calisir.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.extractor import gen_extractors

from . import netguard
from .config import Settings
from .utils import UNSUPPORTED_URL_TEXT, platform_name

log = logging.getLogger(__name__)


class DownloadError(Exception):
    """Kullaniciya gosterilebilecek, anlasilir indirme hatasi."""


class DownloadCancelled(DownloadError):
    """Kullanici indirmeyi iptal etti; hata mesaji gosterilmez."""


@dataclass(slots=True)
class MediaInfo:
    url: str
    title: str
    uploader: str | None
    duration: int | None
    thumbnail: str | None
    heights: list[int]
    has_audio: bool
    platform: str
    webpage_url: str
    # yukseklik -> tahmini dosya boyutu (bayt); bilinmiyorsa anahtar yok
    estimated_sizes: dict[int, int] = field(default_factory=dict)
    # Oynatma listesi ise toplam video sayisi ve (varsa) siralanan ilk kayitlar.
    playlist_count: int = 0
    playlist_entries: list["MediaInfo"] = field(default_factory=list)

    @property
    def is_audio_only(self) -> bool:
        return not self.heights

    @property
    def is_playlist(self) -> bool:
        return self.playlist_count > 1 and bool(self.playlist_entries)


def _friendly_error(raw: str) -> str:
    text = raw.lower()
    if "güvenlik politikası" in text or "blocked destination" in text:
        return "Bu hedef güvenlik politikası nedeniyle engellendi."
    if any(k in text for k in ("login", "sign in", "cookies", "authentication", "private")):
        return (
            "Bu içerik için giriş gerekiyor. Instagram/TikTok gibi platformlarda "
            "genelde hesap çerezi (cookies.txt) gerekir. Bot sahibine bildirin."
        )
    if "unsupported url" in text:
        return UNSUPPORTED_URL_TEXT
    if any(k in text for k in ("video unavailable", "not available", "removed", "deleted")):
        return "Video bulunamadı veya kaldırılmış olabilir."
    if "http error 429" in text or "too many requests" in text:
        return "Platform bizi geçici olarak sınırladı. Birkaç dakika sonra tekrar deneyin."
    if "http error 403" in text:
        return "Platform erişimi reddetti (403). Çerezler eski olabilir."
    if "max-filesize" in text or "file is larger" in text:
        return "Dosya izin verilen boyuttan büyük."
    if "ffmpeg" in text:
        return "Sunucuda ffmpeg bulunamadı; video/ses birleştirme yapılamıyor."
    if "geo" in text and "block" in text:
        return "Bu video bulunduğunuz bölgede engellenmiş görünüyor."
    if "timed out" in text or "timeout" in text:
        return "Platform yanıt vermedi (zaman aşımı). Tekrar deneyin."
    if "is_live" in text or "live event" in text:
        return "Canlı yayınlar desteklenmiyor."
    return "Video indirilemedi. Bağlantıyı kontrol edip tekrar deneyin."


# yt-dlp bu kısa linkleri doğrudan eşleştirmez; resmi kısa alan adı
# yönlendirme sonrası desteklenen siteye gider. Yalnızca sitelerin kendi
# kısa link alan adlarını kabul ediyoruz, rastgele kısaltıcıları değil.
_KNOWN_SHORT_HOSTS = frozenset(
    {
        "instagr.am",
        "pin.it",
        "fb.watch",
        "redd.it",
        "b23.tv",
    }
)


def _is_known_short_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if host.startswith("www."):
        host = host[4:]
    return host in _KNOWN_SHORT_HOSTS


@lru_cache(maxsize=1)
def _supported_extractors() -> tuple[Any, ...]:
    """GenericIE hariç yt-dlp site eşleştiricilerini bir kez hazırlar.

    GenericIE neredeyse her URL'yi kabul ettiği için onu dışarıda bırakıyoruz;
    aksi halde rastgele bir web sayfası da "desteklenen" sayılırdı.
    """
    extractors: list[Any] = []
    try:
        for ie in gen_extractors():
            try:
                key = ie.ie_key()
            except Exception:  # noqa: BLE001 - bozuk extractor'ı atla
                continue
            if str(key).lower() != "generic":
                extractors.append(ie)
    except Exception:  # noqa: BLE001 - liste yüklenemezse güvenli tarafta kal
        log.exception("yt-dlp extractor listesi yüklenemedi")
        return ()
    return tuple(extractors)


def ytdlp_supports_url(url: str) -> bool:
    """URL'yi yt-dlp'nin site extractor'larından biri tanıyor mu?

    Bu kontrol ağ isteği yapmaz. Yalnızca GenericIE'ye uyan normal web
    sayfaları desteklenmiyor sayılır; böylece bot rastgele URL'leri probe
    etmeye çalışmaz. Resmi kısa link alan adları da kabul edilir.
    """
    if not url:
        return False
    if _is_known_short_url(url):
        return True
    for ie in _supported_extractors():
        try:
            if ie.suitable(url):
                return True
        except Exception:  # noqa: BLE001 - bozuk bir extractor diğerlerini engellemesin
            continue
    return False


class Downloader:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # İlk mesajda event loop'u bekletmemek için listeyi önceden yükle.
        _supported_extractors()
        # İndirme ağını süreç genelinde denetle. Ana giriş noktası da kurar;
        # buradaki çağrı kütüphaneyi doğrudan kullanan yolları da kapsar.
        netguard.ensure_for_settings(settings)

    # ------------------------------------------------------------------ ortak

    def _base_opts(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "ignoreerrors": False,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 2,
            "socket_timeout": 25,
            "concurrent_fragment_downloads": 4,
            # HLS varsayılan olarak ffmpeg indiricisine düşebilir; ffmpeg
            # Python socket kancasını atlar. Yerel HlsFD kullanarak tüm
            # segment isteklerini netguard denetiminde tut.
            "hls_prefer_native": True,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "cachedir": False,
        }
        if self._settings.cookies_file and self._settings.cookies_file.exists():
            opts["cookiefile"] = str(self._settings.cookies_file)
        elif self._settings.cookies_file:
            log.warning("COOKIES_FILE bulunamadi: %s", self._settings.cookies_file)
        if netguard.proxy_allowed():
            if self._settings.proxy:
                opts["proxy"] = self._settings.proxy
        else:
            # yt-dlp'de boş proxy ``__noproxy__`` anlamına gelir ve
            # HTTP(S)_PROXY/ALL_PROXY ortam değişkenlerini de devre dışı
            # bırakır. Proxy arkasındaki hedefler soket katmanında
            # doğrulanamadığı için varsayılan davranış budur.
            opts["proxy"] = ""
        return opts

    # ------------------------------------------------------------------ kesif

    def supports_url(self, url: str) -> bool:
        """Ağ isteği yapmadan yt-dlp site desteğini kontrol eder."""
        return ytdlp_supports_url(url)

    def probe(self, url: str) -> MediaInfo:
        info = self._extract_info(url)
        if info.get("_type") == "playlist" and info.get("entries") is not None:
            entries = self._playlist_entries(url, info)
            if entries:
                first = replace(
                    entries[0],
                    title=(info.get("title") or entries[0].title).strip(),
                    uploader=info.get("uploader")
                    or info.get("channel")
                    or entries[0].uploader,
                    playlist_count=len(entries),
                    playlist_entries=entries,
                )
                return first
            raise DownloadError("Bu bağlantıda indirilebilir bir medya bulunamadı.")
        return self._media_info(url, info, check_duration=True)

    def _extract_info(self, url: str) -> dict[str, Any]:
        opts = self._base_opts()
        opts.update({"skip_download": True, "format": "bv*+ba/b"})
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadError(_friendly_error(str(exc))) from exc
        except Exception as exc:  # noqa: BLE001 - yt-dlp genis hata yelpazesi
            log.exception("Bilgi cekme hatasi: %s", url)
            raise DownloadError(_friendly_error(str(exc))) from exc
        if not info:
            raise DownloadError("Bağlantıdan video bilgisi alınamadı.")
        return info

    def _playlist_entries(
        self, url: str, info: dict[str, Any]
    ) -> list[MediaInfo]:
        """Oynatma listesinin ilk N videosunu ayri ayri inceler.

        yt-dlp'nin `extract_flat` ciktisinda format listesi olmadigi icin her
        kayit tek tek cozulur. Tek bir bozuk videonun tum listeyi
        engellememesi icin hatalar atlanir.
        """
        limit = max(1, self._settings.playlist_limit)
        entries: list[MediaInfo] = []
        for item in list(info.get("entries") or [])[:limit]:
            if not item:
                continue
            item_url = item.get("webpage_url") or item.get("url")
            if not item_url:
                continue
            try:
                detail = self._extract_info(str(item_url))
            except DownloadError as exc:
                log.warning("Oynatma listesi kaydi atlandi (%s): %s", item_url, exc)
                continue
            if detail.get("_type") == "playlist":
                continue
            try:
                entries.append(self._media_info(str(item_url), detail))
            except DownloadError as exc:
                log.info("Oynatma listesi kaydi atlandi (%s): %s", item_url, exc)
        return entries

    def _media_info(
        self, url: str, info: dict[str, Any], *, check_duration: bool = False
    ) -> MediaInfo:
        # Kisa playlists / carousel gonderilerinde ilk ogeyi al
        if info.get("_type") == "playlist" and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            if not entries:
                raise DownloadError("Bu bağlantıda indirilebilir bir medya bulunamadı.")
            info = entries[0]

        if info.get("is_live"):
            raise DownloadError("Canlı yayınlar desteklenmiyor.")

        duration = info.get("duration")
        duration = int(duration) if duration else None
        if (
            check_duration
            and duration
            and self._settings.max_duration_sec
            and duration > self._settings.max_duration_sec
        ):
            raise DownloadError(
                "Video çok uzun. İzin verilen maksimum süre: "
                f"{self._settings.max_duration_sec // 60} dakika."
            )

        heights: set[int] = set()
        has_audio = False
        audio_size = 0
        progressive: dict[int, int] = {}
        video_only: dict[int, int] = {}

        for fmt in info.get("formats") or []:
            size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
            vcodec = fmt.get("vcodec")
            acodec = fmt.get("acodec")
            is_video = vcodec not in (None, "none") and bool(fmt.get("height"))
            is_audio = acodec not in (None, "none")

            if is_video:
                height = int(fmt["height"])
                if height >= 144:
                    heights.add(height)
                    if size:
                        if is_audio:
                            progressive[height] = max(progressive.get(height, 0), int(size))
                        else:
                            video_only[height] = max(video_only.get(height, 0), int(size))
            elif is_audio and size:
                audio_size = max(audio_size, int(size))

            if is_audio:
                has_audio = True

        # tahmini toplam boyut: progressive varsa onu, yoksa video+ses kullan
        estimated_sizes: dict[int, int] = {}
        for height in heights:
            if height in progressive:
                estimated_sizes[height] = progressive[height]
            elif height in video_only:
                estimated_sizes[height] = video_only[height] + audio_size

        ordered = sorted(heights, reverse=True)
        if self._settings.max_height:
            ordered = [h for h in ordered if h <= self._settings.max_height]
            if heights and not ordered:
                raise DownloadError(
                    f"Bu videoda {self._settings.max_height}p ve altinda bir kalite "
                    "bulunamadi (MAX_HEIGHT)."
                )
            estimated_sizes = {
                h: size for h, size in estimated_sizes.items() if h in ordered
            }

        extractor = info.get("extractor_key") or info.get("extractor") or ""
        return MediaInfo(
            url=url,
            title=(info.get("title") or "Video").strip(),
            uploader=info.get("uploader") or info.get("channel") or info.get("uploader_id"),
            duration=duration,
            thumbnail=info.get("thumbnail"),
            heights=ordered,
            has_audio=has_audio or not ordered,
            platform=platform_name(str(extractor)),
            webpage_url=info.get("webpage_url") or url,
            estimated_sizes=estimated_sizes,
        )

    # ---------------------------------------------------------------- indirme

    @staticmethod
    def format_selector(preset: str) -> str:
        if preset == "audio":
            return "ba/b"
        if preset.startswith("h:"):
            height = int(re.sub(r"\D", "", preset) or 0)
            if height:
                return (
                    f"bv*[height<={height}]+ba/"
                    f"b[height<={height}]/"
                    f"bv*+ba/b"
                )
        return "bv*+ba/b"

    @staticmethod
    def _h264_selector(preset: str) -> str:
        """H.264/AVC video + AAC ses; bulunamazsa normal secime dus."""
        base = Downloader.format_selector(preset)
        if preset.startswith("h:"):
            height = int(re.sub(r"\D", "", preset) or 0)
            if height:
                return (
                    f"bv*[height<={height}][vcodec^=avc1]+ba[acodec^=mp4a]/"
                    f"b[height<={height}][vcodec^=avc1]/"
                    f"bv*[height<={height}]+ba/"
                    f"b[height<={height}]/"
                    f"bv*+ba/b"
                )
        return (
            "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/"
            "b[vcodec^=avc1]/"
            f"{base}"
        )

    def download(
        self,
        url: str,
        preset: str,
        outdir: Path,
        state: dict[str, Any] | None = None,
        *,
        audio_bitrate: int = 192,
        video_codec: str = "auto",
    ) -> Path:
        state = state if state is not None else {}
        outdir.mkdir(parents=True, exist_ok=True)

        def hook(data: dict[str, Any]) -> None:
            status = data.get("status")
            if state.get("cancelled"):
                # Iptal edilen indirmeyi yt-dlp'nin kendi hata sinifiyla durdur.
                raise yt_dlp.utils.DownloadCancelled("İptal edildi")
            if status == "downloading":
                state["status"] = "downloading"
                state["downloaded"] = data.get("downloaded_bytes") or 0
                state["total"] = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                state["speed"] = data.get("speed")
                state["eta"] = data.get("eta")
            elif status == "finished":
                state["status"] = "finished"
                state["downloaded"] = state.get("total") or state.get("downloaded") or 0
                state["speed"] = None
                state["eta"] = 0

        opts = self._base_opts()
        opts.update(
            {
                "outtmpl": "%(title).70s-%(id)s.%(ext)s",
                "paths": {"home": str(outdir)},
                "format": self.format_selector(preset),
                "progress_hooks": [hook],
            }
        )

        if preset == "audio":
            bitrate = audio_bitrate if audio_bitrate in {128, 192, 320} else 192
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": str(bitrate),
                }
            ]
        else:
            opts["merge_output_format"] = "mp4"
            if video_codec == "h264":
                # Telegram bazi eski istemcilerde VP9/AV1'i oynatamiyor; uyumlu
                # modda H.264 tercih edilir, yoksa mevcut format kullanilir.
                opts["format"] = self._h264_selector(preset)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadCancelled as exc:
            raise DownloadCancelled("İptal edildi.") from exc
        except yt_dlp.utils.DownloadError as exc:
            if state.get("cancelled"):
                raise DownloadCancelled("İptal edildi.") from exc
            raise DownloadError(_friendly_error(str(exc))) from exc
        except Exception as exc:  # noqa: BLE001
            if state.get("cancelled"):
                raise DownloadCancelled("İptal edildi.") from exc
            log.exception("Indirme hatasi: %s", url)
            raise DownloadError(_friendly_error(str(exc))) from exc

        files = [
            p
            for p in outdir.iterdir()
            if p.is_file() and p.suffix.lower() not in {".part", ".ytdl", ".temp"}
        ]
        if not files:
            raise DownloadError("İndirme tamamlandı ancak dosya bulunamadı.")

        files.sort(key=lambda p: p.stat().st_size, reverse=True)
        result = files[0]
        if result.stat().st_size == 0:
            raise DownloadError("İndirilen dosya boş görünüyor, tekrar deneyin.")
        return result
