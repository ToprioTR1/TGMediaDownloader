"""Kullanici deneyimi yardimcilari: etiketler, hata ipuclari, tercih klavyesi.

Bu modul Telegram'dan bagimsizdir; yalnizca klavye/mesaj uretir. Is akisini
degistiren kararlar cagiran katmanda (handler/kuyruk) verilir.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Settings
from .downloader import MediaInfo
from .utils import escape, human_duration, human_size, preset_height, quality_label

# Buton metinlari; smoke testleri ve ice aktarmalar bunlari kullanir.
TEXT_UP = "⬆️ Daha yüksek kalite"
TEXT_DOWN = "⬇️ Daha düşük kalite"
TEXT_DOCUMENT = "📄 Dosya olarak gönder"
TEXT_THUMBNAIL = "🖼 Kapak fotoğrafı"
TEXT_CANCEL = "❌ İptal"
TEXT_GROUP = "📚 Oynatma listesi"
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
        if category == CATEGORY_SIZE:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=TEXT_LOWER,
                        callback_data=f"act|lower|{item_id}",
                    )
                ]
            )
        elif category == CATEGORY_TRANSIENT:
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
    if category == CATEGORY_ACCESS and can_document:
        # "Dosya olarak gonder" yalnizca onbellekte karsiligi olan bir medya
        # icin (asdoc) anlamlidir. Indirme hatasinda dosya henuz yoktur.
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
        rows.append(
            [InlineKeyboardButton(text=TEXT_SOURCE, url=source_url)]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quota_error_text(used_bytes: int, limit_gb: int) -> str | None:
    """Aylik kota dolduysa kullaniciya gosterilecek metni dondurur."""
    if not limit_gb:
        return None
    limit_bytes = int(limit_gb) * 1024**3
    if used_bytes < limit_bytes:
        return None
    return (
        f"📶 Aylık trafik kotası doldu ({human_size(used_bytes)} / "
        f"{limit_gb} GB). Yeni indirmeler admin kotayı artırana kadar durduruldu."
    )


def quota_warning_text(used_bytes: int, limit_gb: int, percent: int) -> str:
    """Adminlere gonderilecek kota uyarisi."""
    used_gb = used_bytes / (1024**3)
    head = (
        "⚠️ Aylık trafik kotası doldu."
        if percent >= 100
        else f"⚠️ Aylık trafik kotasının %{percent}'ine ulaşıldı."
    )
    return (
        f"{head}\n"
        f"Kullanım: <b>{used_gb:.2f} / {limit_gb} GB</b>\n"
        "Kota dolduğunda yeni indirmeler durdurulur."
    )


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
    cancel_job_id: str | None = None,
) -> InlineKeyboardMarkup | None:
    """Gonderilen medyanin altindaki hizli aksiyonlar.

    Telegram gruplarda inline butonlari destekler; yalnizca medya gruplarinda
    (album) buton konulamaz. Bu yuzden aksiyonlar her sohbet turunde aynidir.
    """
    rows: list[list[InlineKeyboardButton]] = []

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
    if cancel_job_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text=TEXT_CANCEL,
                    callback_data=f"act|cancel|{cancel_job_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def quality_menu(
    current_preset: str,
    heights: list[int] | None = None,
    cache_key: str | None = None,
    sizes: dict[int, int] | None = None,
    max_upload_mb: int = 0,
) -> InlineKeyboardMarkup:
    """Kalite secenekleri: mevcut kaliteler + MP3.

    `sizes` verilirse her kalitenin tahmini boyutu da yazilir; limiti asanlar
    `⚠️` ile isaretlenir. `cache_key` butonlarin dogru kayda gitmesini saglar.
    """
    options = sorted({h for h in (heights or []) if h >= 144}, reverse=True)
    if not options:
        options = [1080, 720, 360]
    options = options[:5]
    limit = max_upload_mb * 1024 * 1024 if max_upload_mb else 0
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for height in options:
        preset = f"h:{height}"
        mark = "✓ " if preset == current_preset else ""
        size = (sizes or {}).get(height)
        label = f"{mark}{height}p"
        if size:
            too_big = bool(limit and size > limit)
            label += f" · {'⚠️ ' if too_big else '~'}{human_size(size)}"
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"act|quality|{height}|{cache_key or ''}",
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
                text=f"{audio_mark}🎵 MP3",
                callback_data=f"act|quality|mp3|{cache_key or ''}",
            ),
            InlineKeyboardButton(
                text="✖️ Kapat",
                callback_data=f"act|qclose|{cache_key or '0'}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def playlist_menu(
    *,
    info: MediaInfo,
    token: str,
    has_more: bool,
    can_expand: bool,
    level: int = 0,
) -> InlineKeyboardMarkup:
    """Oynatma listesi secim klavyesi.

    `can_expand`: sunucudaki PLAYLIST_LIMIT ilk turdan daha fazlasina izin
    veriyor mu? `level`: kullanicinin secmis oldugu tur (0 = varsayilan,
    1 = daha fazla video).
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=f"🎬 İlk {len(info.playlist_entries)}",
                callback_data=f"play|all|{token}",
            )
        ]
    ]
    if has_more and can_expand:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Daha fazla video",
                    callback_data=f"play|more|{token}",
                )
            ]
        )
    elif level > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➖ Daha az video",
                    callback_data=f"play|less|{token}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🎵 Hepsini MP3",
                callback_data=f"play|audio|{token}",
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="✖️ İptal", callback_data=f"play|cancel|{token}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def playlist_summary(
    info: MediaInfo, settings: Settings, *, selected: int | None = None
) -> str:
    """Oynatma listesi tanitim metni (HTML)."""
    total = info.playlist_count
    count = selected or len(info.playlist_entries)
    lines = [
        f"📚 <b>{escape(info.title[:150])}</b>",
        f"{info.platform} • {total} video",
    ]
    if info.uploader:
        lines.append(escape(info.uploader[:60]))
    lines.append("")
    shown = info.playlist_entries[:count]
    if shown:
        lines.append(f"<b>İlk {len(shown)} video:</b>")
        for index, entry in enumerate(shown, start=1):
            duration = human_duration(entry.duration) if entry.duration else "—"
            lines.append(
                f"{index}. {escape(entry.title[:80])} · {duration}"
            )
        lines.append("")
    total_seconds = sum(entry.duration or 0 for entry in shown)
    if total_seconds:
        lines.append(f"⏱ Toplam: {human_duration(total_seconds)}")
    if settings.playlist_limit and total > settings.playlist_limit:
        lines.append(
            f"ℹ️ Listelerde en fazla {settings.playlist_limit} video işlenir."
        )
    if settings.playlist_max_total_sec and total_seconds:
        if total_seconds > settings.playlist_max_total_sec:
            lines.append(
                "⚠️ Seçili videoların toplam süresi liste sınırını aşıyor; "
                "yine de tek tek indirilecek."
            )
    if total > count:
        lines.append(f"➕ {total - count} video daha var.")
    return "\n".join(lines)


def playlist_entry_status(index: int, total: int, title: str) -> str:
    return (
        f"📚 <b>Oynatma listesi {index}/{total}</b>\n"
        f"{escape(title[:150])}"
    )


def size_hint_for_estimate(size: int | None, max_upload_mb: int) -> str | None:
    if not size:
        return None
    if size > max_upload_mb * 1024 * 1024:
        return f"~{human_size(size)} · limit {max_upload_mb} MB"
    return None


def lower_preset(preset: str, heights: list[int]) -> str | None:
    """Verilen preset'in bir alt mevcut kalitesini dondurur."""
    current = preset_height(preset)
    if current is None:
        return None
    lower = sorted((h for h in heights if h < current), reverse=True)
    return f"h:{lower[0]}" if lower else None
