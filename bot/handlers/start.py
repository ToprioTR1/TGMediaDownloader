"""Komutlar ve yardim metinleri."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..config import Settings
from ..database import Database
from ..utils import escape, human_size

router = Router(name="start")


def _home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📖 Nasıl kullanılır?", callback_data="help"),
                InlineKeyboardButton(text="📊 Kullanımım", callback_data="usage"),
            ]
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message, db_user: dict) -> None:
    name = escape(db_user.get("first_name") or message.from_user.first_name or "arkadaş")
    await message.answer(
        f"👋 Merhaba <b>{name}</b>!\n\n"
        "Bana <b>Instagram</b>, <b>YouTube</b>, <b>TikTok</b>, <b>X</b> gibi "
        "platformlardan bir video bağlantısı gönder; en uygun kalitede indirip "
        "buraya yollayayım.\n"
        "Instagram fotoğraf/carousel gönderileri de desteklenir. 🖼\n\n"
        "<b>Nasıl çalışır?</b>\n"
        "1️⃣ Bağlantıyı gönder\n"
        "2️⃣ Bot otomatik olarak en yakın kaliteyi seçer\n"
        "3️⃣ Videonu al 🎬\n\n"
        "<b>İpucu:</b> Linkin sonuna <code>1080p</code> gibi bir çözünürlük "
        "ya da <code>mp3</code> yazabilirsin.\n"
        "Gönderinin açıklamasındaki linki yanıtlayıp sadece <code>720p</code> "
        "yazman da yeterli.\n"
        "Örnek: <code>https://youtu.be/... 720p</code>\n\n"
        "Komutlar: /help · /me",
        reply_markup=_home_keyboard(),
        disable_web_page_preview=True,
    )


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    await message.answer(_help_text(settings), disable_web_page_preview=True)


@router.callback_query(lambda c: c.data == "help")
async def cb_help(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.answer(_help_text(settings), disable_web_page_preview=True)


def _help_text(settings: Settings) -> str:
    duration = (
        f"{settings.max_duration_sec // 60} dakika" if settings.max_duration_sec else "sınırsız"
    )
    daily = str(settings.daily_limit) if settings.daily_limit else "sınırsız"
    return (
        "<b>📖 Nasıl kullanılır?</b>\n"
        "Desteklenen bir platformdan video bağlantısını bu sohbete yapıştırın. "
        "Bot bağlantıyı inceler ve en uygun kalitede videoyu gönderir.\n"
        "Bağlantı bir gönderinin <b>açıklamasında</b> (caption) olsa da çalışır; "
        "gönderiyi yanıtlayıp sadece <code>720p</code> yazmanız yeterlidir.\n\n"
        "<b>🎯 Kalite seçimi</b>\n"
        "Linkin sonuna çözünürlük yazabilirsiniz: <code>1080p</code>, "
        "<code>720p</code>, <code>4k</code>…\n"
        "Yazdığınız çözünürlük mevcut değilse <b>en yakın</b> kalite indirilir.\n"
        "Yazmazsanız varsayılan kalite kullanılır: "
        f"Instagram {settings.quality_instagram}p · "
        f"TikTok {settings.quality_tiktok}p · "
        f"YouTube {settings.quality_youtube}p.\n"
        "Ses için <code>mp3</code> veya <code>ses</code> yazın.\n"
        f"Seçilen kalite {settings.max_upload_mb} MB limitini aşacaksa bot "
        "otomatik olarak bir alt kaliteye iner.\n\n"
        "<b>🖼 Instagram fotoğraf / carousel</b>\n"
        "Video içermeyen Instagram gönderileri de indirilir; fotoğraflar albüm "
        f"olarak gönderilir (en fazla {settings.gallery_limit} medya).\n\n"
        "<b>🎛 Gönderilen videonun altındaki butonlar</b>\n"
        "🔗 Kaynak · 🎵 Ses olarak al\n"
        "Aynı video tekrar istendiğinde <b>önbellekten</b> anında gönderilir.\n\n"
        "<b>Örnekler</b>\n"
        "<code>https://youtu.be/... 1080p</code>\n"
        "<code>https://instagram.com/reel/... mp3</code>\n\n"
        "<b>Desteklenen platformlar</b>\n"
        "YouTube · Instagram · TikTok · X (Twitter) · Facebook · Reddit · "
        "Vimeo · Pinterest · SoundCloud ve yt-dlp'nin desteklediği daha birçok site.\n\n"
        "<b>Limitler</b>\n"
        f"📦 Maksimum dosya boyutu: {settings.max_upload_mb} MB\n"
        f"⏱ Maksimum video süresi: {duration}\n"
        f"📉 Günlük indirme limiti: {daily}\n"
        f"🧵 Aynı anda kuyruk: {settings.max_queue_per_user}\n\n"
        "<b>İpuçları</b>\n"
        "• Bir mesajda en fazla 3 bağlantı gönderebilirsiniz.\n"
        "• Özel (private) Instagram gönderileri için botun çerezleri olması gerekir.\n"
        "• Uzun videoları indirmek biraz zaman alabilir.\n\n"
        "Komutlar: /start · /help · /me"
    )


@router.message(Command("me", "profil", "limit"))
async def cmd_me(message: Message, db: Database, db_user: dict, settings: Settings) -> None:
    today = await db.count_downloads_today(message.from_user.id)
    user = await db.get_user(message.from_user.id) or db_user
    daily = settings.daily_limit or 0
    left = "sınırsız" if not daily else str(max(0, daily - today))
    await message.answer(
        "<b>📊 Kullanım bilgileriniz</b>\n"
        f"👤 Ad: {escape(user.get('first_name'))}\n"
        f"🆔 ID: <code>{user.get('id')}</code>\n"
        f"⬇️ Toplam indirme: {user.get('downloads', 0)}\n"
        f"💾 Toplam veri: {human_size(user.get('bytes_sent', 0))}\n"
        f"📅 Bugün: {today} • kalan hak: {left}"
    )


@router.callback_query(lambda c: c.data == "usage")
async def cb_usage(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    today = await db.count_downloads_today(callback.from_user.id)
    user = await db.get_user(callback.from_user.id) or {}
    daily = settings.daily_limit or 0
    left = "sınırsız" if not daily else str(max(0, daily - today))
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "<b>📊 Kullanım bilgileriniz</b>\n"
            f"⬇️ Toplam indirme: {user.get('downloads', 0)}\n"
            f"💾 Toplam veri: {human_size(user.get('bytes_sent', 0))}\n"
            f"📅 Bugün: {today} • kalan hak: {left}"
        )
