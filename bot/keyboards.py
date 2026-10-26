"""Inline klavyeler."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def media_keyboard(
    source_url: str | None, cache_key: str, allow_audio: bool = True
) -> InlineKeyboardMarkup | None:
    """Gönderilen videonun altindaki aksiyon butonlari."""
    rows: list[list[InlineKeyboardButton]] = []
    if source_url:
        rows.append([InlineKeyboardButton(text="🔗 Kaynak", url=source_url)])
    if allow_audio:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎵 Ses olarak al", callback_data=f"act|audio|{cache_key}"
                )
            ]
        )
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gallery_keyboard(source_url: str | None) -> InlineKeyboardMarkup | None:
    if not source_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔗 Kaynak", url=source_url)]]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 İstatistik", callback_data="admin|stats"),
                InlineKeyboardButton(text="📶 Bant genişliği", callback_data="admin|bandwidth"),
            ],
            [
                InlineKeyboardButton(text="👥 Kullanıcılar", callback_data="admin|users"),
                InlineKeyboardButton(text="⚡️ Önbellek", callback_data="admin|cache"),
            ],
        ]
    )
