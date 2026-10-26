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
TEXT_SOURCE = "🔗 Kaynağı aç"


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
