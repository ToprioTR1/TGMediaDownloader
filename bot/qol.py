"""Kullanici deneyimi yardimcilari: etiketler ve hizli aksiyon klavyeleri.

Bu modul Telegram'dan bagimsizdir; yalnizca klavye/mesaj uretir. Is akisini
degistiren kararlar cagiran katmanda (handler/kuyruk) verilir.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .utils import preset_height, quality_label

# Buton metinlari; smoke testleri ve ice aktarmalar bunlari kullanir.
TEXT_DOCUMENT = "📄 Dosya olarak gönder"
TEXT_THUMBNAIL = "🖼 Kapak fotoğrafı"
TEXT_CANCEL = "❌ İptal"
TEXT_RETRY = "🔁 Yeniden dene"
TEXT_LOWER = "⬇️ Daha düşük kalite dene"
TEXT_SOURCE = "🔗 Kaynağı aç"

# Hata kategorileri; akilli hata butonlarinda hangi seceneklerin
# gosterilecegine karar vermek icin kullanilir.
CATEGORY_SIZE = "size"
CATEGORY_ACCESS = "access"
CATEGORY_TRANSIENT = "transient"
CATEGORY_GEO = "geo"
CATEGORY_DURATION = "duration"
CATEGORY_UNSUPPORTED = "unsupported"
CATEGORY_GENERIC = "generic"


def audio_label(bitrate: int | str | None) -> str:
    try:
        value = int(bitrate or 192)
    except (TypeError, ValueError):
        value = 192
    return f"MP3 {value} kbps"


def video_codec_label(codec: str | None) -> str:
    return "H.264 (uyumlu)" if codec == "h264" else "Otomatik"


def download_mode_label(mode: str | None) -> str:
    return "Dosya" if mode == "document" else "Video"


def video_label(preset: str, bitrate: int | str | None = None) -> str:
    """Preset icin kullaniciya gosterilecek etiket (onbellek satirlari dahil)."""
    if preset == "audio":
        return audio_label(bitrate)
    if preset.endswith("|doc"):
        return f"{quality_label(preset[: -len('|doc')])} · dosya"
    return quality_label(preset)


def error_category(message: str) -> str:
    """Hata metnini kullaniciya sunulacak buton setine esler."""
    text = (message or "").lower()
    if any(
        key in text
        for key in (
            "boyut",
            "limit",
            "too big",
            "entity too large",
            "çok büyük",
            "cok buyuk",
        )
    ):
        return CATEGORY_SIZE
    if any(
        key in text
        for key in (
            "zaman aşımı",
            "zaman asimi",
            "yavaş",
            "yavas",
            "yanıt vermedi",
            "yanit vermedi",
            "geçici",
            "gecici",
            "sınırladı",
            "sinirladi",
        )
    ):
        return CATEGORY_TRANSIENT
    if any(
        key in text
        for key in (
            "giriş",
            "giris",
            "login",
            "çerez",
            "cerez",
            "cookie",
            "private",
            "403",
            "erişim",
            "erisim",
            "yetki",
            "üye",
            "uye",
        )
    ):
        return CATEGORY_ACCESS
    if "bölge" in text or "bolge" in text or "geo" in text:
        return CATEGORY_GEO
    if "uzun" in text or "süre" in text or "sure" in text:
        return CATEGORY_DURATION
    if "desteklenmiyor" in text or "unsupported" in text:
        return CATEGORY_UNSUPPORTED
    return CATEGORY_GENERIC


def retry_keyboard(
    item_id: str,
    *,
    preset: str | None,
    has_lower: bool,
    source_url: str | None,
    max_upload_mb: int,
    category: str = CATEGORY_GENERIC,
    can_document: bool = True,
) -> InlineKeyboardMarkup:
    """Hata kartindaki aksiyonlar.

    - Her hatada yeniden deneme,
    - boyut hatalarinda daha dusuk kalite,
    - erisim hatalarinda dosya olarak gonderme (yeniden indirmeden) ve
      kaynak baglantisi,
    - geçici hatalarda (bot destekliyorsa) daha dusuk kalite.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=TEXT_RETRY, callback_data=f"retry|{item_id}")]
    ]
    if preset and preset.startswith("h:") and has_lower:
        if category in {CATEGORY_SIZE, CATEGORY_TRANSIENT}:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=TEXT_LOWER,
                        callback_data=f"act|lower|{item_id}",
                    )
                ]
            )
    if category == CATEGORY_SIZE:
        # Boyut hatasinda dosya olarak gondermek de limiti asar; bunu onermeyiz.
        can_document = False
    if category in {CATEGORY_ACCESS, CATEGORY_GENERIC} and can_document:
        rows.append(
            [
                InlineKeyboardButton(
                    text=TEXT_DOCUMENT,
                    callback_data=f"act|asdoc|{item_id}",
                )
            ]
        )
    if source_url and category in {
        CATEGORY_ACCESS,
        CATEGORY_GEO,
        CATEGORY_UNSUPPORTED,
        CATEGORY_GENERIC,
    }:
        rows.append([InlineKeyboardButton(text=TEXT_SOURCE, url=source_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def error_hint(
    category: str, *, max_upload_mb: int, detail: str | None = None
) -> str:
    """Hata metninin altina eklenecek kisa, uygulanabilir oneri."""
    if category == CATEGORY_SIZE:
        return (
            f"💡 Seçili kalite {max_upload_mb} MB limitini aşıyor. "
            "Daha düşük kalitede deneyin."
        )
    if category == CATEGORY_ACCESS:
        return (
            "💡 Platform giriş istiyor. Herkese açık bir gönderi deneyin veya "
            "içeriği dosya olarak almayı deneyin."
        )
    if category == CATEGORY_TRANSIENT:
        return "💡 Platform geçici olarak yanıt vermedi. Birkaç dakika sonra tekrar deneyin."
    if category == CATEGORY_GEO:
        return "💡 İçerik bulunduğunuz bölgede kısıtlı olabilir. Kaynağı tarayıcıda deneyin."
    if category == CATEGORY_DURATION:
        return "💡 Daha kısa bir video veya oynatma listesi deneyin."
    if category == CATEGORY_UNSUPPORTED:
        return "💡 Bu bağlantı desteklenmiyor. /help ile desteklenen platformlara bakın."
    return "💡 Sorun sürerse bağlantıyı tekrar gönderin."


def media_buttons(
    *,
    source_url: str | None,
    cache_key: str,
    can_audio: bool,
    can_document: bool,
    can_thumbnail: bool,
    can_quality: bool,
    in_group: bool,
    cancel_job_id: str | None = None,
) -> InlineKeyboardMarkup | None:
    """Gonderilen medyanin altindaki hizli aksiyonlar.

    Album/oylatin listesi grup mesajlarinda Telegram buton desteklemedigi
    icin `in_group` oldugunda yalnizca link/iptal satiri uretilir.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if in_group:
        if source_url:
            rows.append([InlineKeyboardButton(text=TEXT_SOURCE, url=source_url)])
        if cancel_job_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=TEXT_CANCEL,
                        callback_data=f"act|cancel|{cancel_job_id}",
                    )
                ]
            )
        return (
            InlineKeyboardMarkup(inline_keyboard=rows)
            if rows
            else InlineKeyboardMarkup(inline_keyboard=[])
        )

    quality_row: list[InlineKeyboardButton] = []
    if can_audio:
        quality_row.append(
            InlineKeyboardButton(
                text="🎵 Ses olarak al", callback_data=f"act|audio|{cache_key}"
            )
        )
    if can_quality:
        quality_row.append(
            InlineKeyboardButton(
                text="🎚 Kalite değiştir", callback_data=f"act|qmenu|{cache_key}"
            )
        )
    if quality_row:
        rows.append(quality_row)

    extra_row: list[InlineKeyboardButton] = []
    if can_document:
        extra_row.append(
            InlineKeyboardButton(
                text=TEXT_DOCUMENT, callback_data=f"act|asdoc|{cache_key}"
            )
        )
    if can_thumbnail:
        extra_row.append(
            InlineKeyboardButton(
                text=TEXT_THUMBNAIL, callback_data=f"act|cover|{cache_key}"
            )
        )
    if extra_row:
        rows.append(extra_row)

    if source_url:
        rows.append([InlineKeyboardButton(text="🔗 Kaynak", url=source_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def quality_menu(current_preset: str, heights: list[int] | None = None) -> InlineKeyboardMarkup:
    """Kalite secenekleri: mevcut kaliteler + MP3.

    `heights` bossa 360/720/1080 gosterilir. Onbellekteki video icin gercek
    kaliteler bilinmedigi icin 144–2160 araligindan makul bir liste kullanilir.
    """
    options = sorted({h for h in (heights or []) if h >= 144}, reverse=True)
    if not options:
        options = [1080, 720, 360]
    options = options[:5]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for height in options:
        preset = f"h:{height}"
        mark = "✓ " if preset == current_preset else ""
        row.append(
            InlineKeyboardButton(
                text=f"{mark}{height}p", callback_data=f"act|quality|{height}"
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    audio_mark = "✓ " if current_preset == "audio" else ""
    rows.append(
        [
            InlineKeyboardButton(
                text=f"{audio_mark}🎵 MP3", callback_data="act|quality|mp3"
            ),
            InlineKeyboardButton(text="✖️ Kapat", callback_data="act|qclose|0"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lower_preset(preset: str, heights: list[int]) -> str | None:
    """Verilen preset'in bir alt mevcut kalitesini dondurur."""
    current = preset_height(preset)
    if current is None:
        return None
    lower = sorted((h for h in heights if h < current), reverse=True)
    return f"h:{lower[0]}" if lower else None
