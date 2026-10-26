"""Kucuk yardimci fonksiyonlar."""

from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import logging
import re
from urllib.parse import urlparse

from .netguard import is_public_ip

log = logging.getLogger(__name__)

DNS_LOOKUP_TIMEOUT = 3.0
BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost")
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
UNSUPPORTED_URL_TEXT = (
    "Bu bağlantı desteklenmiyor. Bot yalnızca yt-dlp'nin tanıdığı "
    "sitelerden indirme yapar. Desteklenen platformlar için /help yazın."
)
QUALITY_RE = re.compile(r"(?<![\d.])(\d{3,4})(?:\s*[pP])?(?![\d.])")
K_QUALITY = {"8k": 4320, "4k": 2160, "2k": 1440}
AUDIO_RE = re.compile(r"\b(mp3|m4a|ses|audio|müzik|muzik)\b", re.IGNORECASE)
DOCUMENT_RE = re.compile(r"\b(dosya|belge|file|fl)\b", re.IGNORECASE)

PLATFORM_NAMES = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "twitter": "X (Twitter)",
    "x": "X (Twitter)",
    "facebook": "Facebook",
    "vimeo": "Vimeo",
    "reddit": "Reddit",
    "twitch": "Twitch",
    "pinterest": "Pinterest",
    "soundcloud": "SoundCloud",
    "bilibili": "Bilibili",
    "dailymotion": "Dailymotion",
    "snapchat": "Snapchat",
    "threads": "Threads",
    "likee": "Likee",
    "rumble": "Rumble",
}


def escape(text: str | None) -> str:
    # quote=True: ayni yardimci HTML metinlerinde ve URL ozelliklerinde kullaniliyor.
    return html.escape(text or "", quote=True)


def extract_urls(text: str, limit: int = 3) -> list[str]:
    """Metindeki baglantilari cikarir, tekrarlari ve noktalama kalintilarini temizler."""
    found: list[str] = []
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?)\"]'")
        if url not in found:
            found.append(url)
        if len(found) >= limit:
            break
    return found


def parse_quality(text: str) -> int | None:
    """Metinden istenen cozunurlugu cikarir: '1080', '1080p', '1080 p', '4k' ..."""
    if not text:
        return None
    cleaned = _without_urls(text)
    match = QUALITY_RE.search(cleaned)
    if match:
        return int(match.group(1))
    lowered = cleaned.lower()
    for key, value in K_QUALITY.items():
        if re.search(rf"\b{key}\b", lowered):
            return value
    return None


def _without_urls(text: str) -> str:
    """Anahtar kelime ararken URL'lerin icindeki metni yok sayar."""
    return URL_RE.sub(" ", text or "")


def wants_audio(text: str) -> bool:
    """Kullanici ses indirmek istiyor mu ('mp3', 'ses' ...)."""
    return bool(AUDIO_RE.search(_without_urls(text)))


def wants_document(text: str) -> bool:
    """Kullanici medyayi dosya olarak istiyor mu ('dosya', 'file', 'fl' ...)."""
    return bool(DOCUMENT_RE.search(_without_urls(text)))


def nearest_height(heights: list[int], target: int | None) -> int | None:
    """Hedef yukseklige en yakin mevcut cozunurluk (esitlikte yuksek olan)."""
    if not heights or not target:
        return None
    return min(heights, key=lambda h: (abs(h - target), -h))


def media_cache_key(url: str, preset: str) -> str:
    """Ayni video+format icin kalici anahtar (callback data'ya sigacak kadar kisa)."""
    return hashlib.sha256(f"{url}|{preset}".encode()).hexdigest()[:32]


def _blocked_ip(value: str) -> bool:
    # Ayni siniflandirma netguard'daki baglanti kancasiyla paylasilir; iki
    # katmanin farkli adresleri guvenli saymasi onlenir.
    return not is_public_ip(value)


def is_blocked_host(url: str) -> bool:
    """Yerel ag / localhost adreslerine istek atilmasini engeller."""
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return True
    if not host:
        return True
    if host in {"localhost", "localhost.localdomain"}:
        return True
    if host.endswith(BLOCKED_HOST_SUFFIXES):
        return True
    # Bazi cozuculer 2130706433, 127.1, 0x7f000001 gibi standart disi sayisal
    # adresleri loopback'e cevirir. ipaddress bunlari reddeder; bu yuzden
    # sayisal gorunumlu ve gecersiz adresleri de guvenli tarafta reddet.
    if re.fullmatch(r"0x[0-9a-f]+", host) or re.fullmatch(r"[0-9.]+", host):
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return True
        return _blocked_ip(str(ip))
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return _blocked_ip(str(ip))


async def is_blocked_url(url: str) -> bool:
    """URL'yi ve DNS sonucunu denetler.

    Yalnizca metindeki IP'yi kontrol etmek, genel gorunen bir alan adinin
    (or. localtest.me) yerel bir adrese cozulmesine izin verirdi. Bu yuzden
    ilk istekten once alan adi da cozulur ve donen tum adresler denetlenir.
    DNS cozulemezse istek reddedilir: guvenlik denetimi basarisizken indiriciye
    devam etme sansi verilmez. Asil sinir yine de netguard'in connect
    kancasidir; buradaki kontrol kullaniciya erken ve anlasilir hata verir.
    """
    if is_blocked_host(url):
        return True
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return True
    if not host:
        return True
    try:
        ipaddress.ip_address(host)
        return False  # Genel bir IP; is_blocked_host zaten temiz dedi.
    except ValueError:
        pass

    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None),
            timeout=DNS_LOOKUP_TIMEOUT,
        )
    except (asyncio.TimeoutError, OSError, ValueError) as exc:
        log.warning("DNS denetimi yapilamadi, istek reddedildi (%s): %s", host, exc)
        return True

    if not infos:
        log.warning("DNS sonucu bos, istek reddedildi: %s", host)
        return True

    for *_, sockaddr in infos:
        address = str(sockaddr[0]).split("%", 1)[0]
        if _blocked_ip(address):
            log.warning("Yerel aga cozulen baglanti reddedildi: %s -> %s", host, address)
            return True
    return False


def platform_name(extractor: str | None) -> str:
    if not extractor:
        return "Video"
    key = extractor.lower().replace(" ", "")
    for name, label in PLATFORM_NAMES.items():
        if name == "x":
            # "x" tek harfi "xvideos" gibi baska extractor'lara da uyardi.
            if key == "x" or key.startswith("x:"):
                return label
        elif name in key:
            return label
    return extractor


def human_size(size: float | int | None) -> str:
    if not size:
        return "0 B"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def human_duration(seconds: float | int | None) -> str:
    if not seconds or seconds < 0:
        return "00:00"
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def progress_bar(percent: float, width: int = 12) -> str:
    percent = max(0.0, min(100.0, percent))
    filled = int(round(width * percent / 100))
    return "█" * filled + "░" * (width - filled)


def quality_label(preset: str) -> str:
    if preset == "audio":
        return "MP3 192 kbps"
    if preset.startswith("h:"):
        return f"{preset.split(':', 1)[1]}p"
    return "en iyi kalite"


def preset_height(preset: str) -> int | None:
    """`h:720` benzeri preset'ten yuksekligi okur; yoksa None."""
    if not preset.startswith("h:"):
        return None
    try:
        height = int(preset.split(":", 1)[1])
    except (IndexError, ValueError):
        return None
    return height if height > 0 else None


def render_progress(state: dict) -> str:
    """yt-dlp ilerleme sozlugunden kullaniciya gosterilecek metni uretir."""
    if state.get("status") == "finished":
        return "📦 <b>Dosya hazırlanıyor…</b>"

    downloaded = state.get("downloaded") or 0
    total = state.get("total") or 0
    lines: list[str]

    if total:
        percent = min(100.0, downloaded * 100 / total)
        lines = [
            f"⬇️ <b>İndiriliyor…</b> {percent:.0f}%",
            f"<code>{progress_bar(percent)}</code>",
            f"{human_size(downloaded)} / {human_size(total)}",
        ]
    else:
        lines = ["⬇️ <b>İndiriliyor…</b>", human_size(downloaded)]

    speed = state.get("speed")
    if speed:
        tail = f"⚡️ {human_size(speed)}/sn"
        eta = state.get("eta")
        if eta:
            tail += f" • kalan {human_duration(int(eta))}"
        lines.append(tail)

    return "\n".join(lines)
