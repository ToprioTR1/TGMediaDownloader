"""Komutlar ve yardim metinleri."""

from __future__ import annotations

import contextlib

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from ..config import Settings
from ..database import Database
from ..qol import audio_label, download_mode_label, video_codec_label
from ..queue import JobQueue
from ..utils import escape, human_size

router = Router(name="start")


@router.my_chat_member()
async def on_my_chat_member(
    event: ChatMemberUpdated, db: Database, bot: Bot
) -> None:
    """Bot gruba eklendiginde/ayrildiginda sohbet kaydini gunceller."""
    chat_type = getattr(event.chat.type, "value", event.chat.type)
    if chat_type not in {"group", "supergroup"}:
        return
    status = getattr(
        event.new_chat_member.status, "value", event.new_chat_member.status
    )
    if event.new_chat_member.user.id == bot.id and status in {"left", "kicked"}:
        await db.remove_chat(event.chat.id)
    else:
        await db.touch_chat(event.chat.id, event.chat.title, str(chat_type))


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
    fallback_name = message.from_user.first_name if message.from_user else None
    name = escape(db_user.get("first_name") or fallback_name or "arkadaş")
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
        "<b>İpucu:</b> Linkin sonuna <code>1080</code>, <code>1080p</code> veya "
        "<code>1080 p</code> gibi bir çözünürlük; <code>mp3</code> ya da "
        "<code>dosya</code> yazabilirsin.\n"
        "Gönderinin açıklamasındaki linki yanıtlayıp sadece <code>720p</code> "
        "yazman da yeterli.\n"
        "Örnek: <code>https://youtu.be/... 720p</code>\n\n"
        "Komutlar: /help · /ayar · /me",
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
        "Yalnızca yt-dlp'nin tanıdığı site bağlantıları işlenir; diğer "
        "bağlantılar sessizce yok sayılır.\n"
        "Bağlantı bir gönderinin <b>açıklamasında</b> (caption) olsa da çalışır; "
        "gönderiyi yanıtlayıp sadece <code>720p</code> yazmanız yeterlidir.\n\n"
        "<b>🎯 Kalite seçimi</b>\n"
        "Linkin sonuna çözünürlük yazabilirsiniz: <code>1080</code>, "
        "<code>1080p</code>, <code>1080 p</code>, <code>4k</code>…\n"
        "Yazdığınız çözünürlük mevcut değilse <b>en yakın</b> kalite indirilir.\n"
        "Yazmazsanız varsayılan kalite kullanılır: "
        f"Instagram {settings.quality_instagram}p · "
        f"TikTok {settings.quality_tiktok}p · "
        f"YouTube {settings.quality_youtube}p · "
        f"diğerleri {settings.quality_default}p.\n"
        "Ses için <code>mp3</code> veya <code>ses</code> yazın. "
        "Dosya olarak indirmek için <code>dosya</code>, <code>file</code> "
        "veya <code>fl</code> ekleyin.\n"
        f"Seçilen kalite {settings.max_upload_mb} MB limitini aşacaksa bot "
        "otomatik olarak bir alt kaliteye iner.\n\n"
        "<b>⚙️ Kişisel ayarlar</b>\n"
        "<code>/ayar</code> ile varsayılan kaliteyi, ses tercihini ve caption "
        "gösterimini değiştirebilirsiniz. Aynı ekrandan MP3 kalitesi, H.264 "
        "uyumluluk modu ve dosya/video gönderim tercihi de seçilir.\n\n"
        "<b>📚 Oynatma listeleri</b>\n"
        "YouTube gibi platformlarda bir liste bağlantısı gönderdiğinizde bot "
        "önce kaç video indirileceğini sorar. İlk birkaç videoyu, daha fazlasını "
        "veya hepsini MP3 olarak seçebilirsiniz.\n\n"
        "<b>🖼 Instagram / TikTok fotoğraf</b>\n"
        "Instagram fotoğraf/carousel ve TikTok fotoğraf gönderileri de "
        "indirilir; fotoğraflar albüm olarak gönderilir "
        f"(en fazla {settings.gallery_limit} medya).\n\n"
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
        "Komutlar: /start · /help · /ayar · /me"
    )


def _promoted_line(promoted: bool) -> str:
    return "\n⭐ Sınırsız indirme hakkı: aktif" if promoted else ""


_TRUE_WORDS = {"ac", "aç", "acik", "açık", "on", "1", "evet", "true"}
_FALSE_WORDS = {"kapat", "kapali", "kapalı", "off", "0", "hayir", "hayır", "false"}


def _parse_bool(value: str) -> bool | None:
    if value in _TRUE_WORDS:
        return True
    if value in _FALSE_WORDS:
        return False
    return None


def _settings_text(user: dict) -> str:
    quality = int(user.get("default_quality") or 0)
    audio = bool(user.get("audio_default"))
    caption = bool(user.get("show_caption", 0))
    bitrate = int(user.get("audio_bitrate") or 192)
    codec = str(user.get("video_codec") or "auto")
    mode = str(user.get("download_mode") or "video")
    return (
        "<b>⚙️ Kişisel ayarlar</b>\n"
        f"🎯 Varsayılan kalite: <b>{f'{quality}p' if quality else 'platform varsayılanı'}</b>\n"
        f"🎵 Varsayılan ses: <b>{'açık' if audio else 'kapalı'}</b> · "
        f"<b>{audio_label(bitrate)}</b>\n"
        f"🎬 Video: <b>{video_codec_label(codec)}</b> · "
        f"gönderim: <b>{download_mode_label(mode)}</b>\n"
        f"📝 Caption: <b>{'açık' if caption else 'kapalı'}</b>\n\n"
        "<b>Değiştirmek için</b>\n"
        "<code>/ayar kalite 1080</code> · sıfırlamak için <code>/ayar kalite 0</code>\n"
        "<code>/ayar ses aç</code> / <code>/ayar ses kapat</code>\n"
        "<code>/ayar bitrate 320</code> (128 / 192 / 320)\n"
        "<code>/ayar codec h264</code> / <code>/ayar codec auto</code>\n"
        "<code>/ayar mod dosya</code> / <code>/ayar mod video</code>\n"
        "<code>/ayar caption aç</code> / <code>/ayar caption kapat</code>\n\n"
        "Varsayılan ses açıkken linkler MP3 olarak iner. Video istemek için "
        "linkin sonuna <code>720p</code> gibi bir kalite yazın. Bu ayarları "
        "butonlarla da değiştirebilirsiniz."
    )


async def _preferences_keyboard(user: dict) -> InlineKeyboardMarkup:
    bitrate = int(user.get("audio_bitrate") or 192)
    codec = str(user.get("video_codec") or "auto")
    mode = str(user.get("download_mode") or "video")
    quality = int(user.get("default_quality") or 0)
    caption = bool(user.get("show_caption", 0))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✓ ' if quality == 0 else ''}Otomatik",
                    callback_data="qol|quality|0",
                ),
                InlineKeyboardButton(
                    text=f"{'✓ ' if quality == 720 else ''}720p",
                    callback_data="qol|quality|720",
                ),
                InlineKeyboardButton(
                    text=f"{'✓ ' if quality == 1080 else ''}1080p",
                    callback_data="qol|quality|1080",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✓ ' if bitrate == 128 else ''}128k",
                    callback_data="qol|bitrate|128",
                ),
                InlineKeyboardButton(
                    text=f"{'✓ ' if bitrate == 192 else ''}192k",
                    callback_data="qol|bitrate|192",
                ),
                InlineKeyboardButton(
                    text=f"{'✓ ' if bitrate == 320 else ''}320k",
                    callback_data="qol|bitrate|320",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✓ ' if codec == 'h264' else ''}H.264",
                    callback_data="qol|codec|h264",
                ),
                InlineKeyboardButton(
                    text=f"{'✓ ' if codec == 'auto' else ''}Otomatik kodek",
                    callback_data="qol|codec|auto",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✓ ' if mode == 'document' else ''}Dosya",
                    callback_data="qol|mode|document",
                ),
                InlineKeyboardButton(
                    text=f"{'✓ ' if mode == 'video' else ''}Video",
                    callback_data="qol|mode|video",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✓ ' if caption else ''}Caption açık",
                    callback_data="qol|caption|1",
                ),
                InlineKeyboardButton(
                    text=f"{'✓ ' if not caption else ''}Caption kapalı",
                    callback_data="qol|caption|0",
                ),
            ],
        ]
    )


@router.message(Command("ayar", "settings"))
async def cmd_settings(message: Message, db: Database, db_user: dict) -> None:
    user = await db.get_user(message.from_user.id) or db_user
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            _settings_text(user),
            reply_markup=await _preferences_keyboard(user),
            disable_web_page_preview=True,
        )
        return

    key = parts[1].lower()
    value = parts[2].strip().lower()

    if key in {"kalite", "quality"}:
        raw = value.removesuffix("p")
        try:
            quality = int(raw)
        except ValueError:
            await message.answer("❌ Kalite sayı olmalı. Örnek: <code>/ayar kalite 1080</code>")
            return
        if quality != 0 and not 144 <= quality <= 4320:
            await message.answer("❌ Kalite 144–4320 arasında olmalı (0 = sıfırla).")
            return
        await db.update_user_settings(message.from_user.id, default_quality=quality)
        label = f"{quality}p" if quality else "platform varsayılanı"
        await message.answer(f"✅ Varsayılan kalite: <b>{label}</b>")
        return

    if key in {"ses", "audio"}:
        enabled = _parse_bool(value)
        if enabled is None:
            await message.answer("❌ Kullanım: <code>/ayar ses aç</code> veya <code>/ayar ses kapat</code>")
            return
        await db.update_user_settings(message.from_user.id, audio_default=enabled)
        await message.answer(
            f"✅ Varsayılan ses: <b>{'açık' if enabled else 'kapalı'}</b>"
        )
        return

    if key in {"caption", "açıklama", "aciklama"}:
        enabled = _parse_bool(value)
        if enabled is None:
            await message.answer(
                "❌ Kullanım: <code>/ayar caption aç</code> veya <code>/ayar caption kapat</code>"
            )
            return
        await db.update_user_settings(message.from_user.id, show_caption=enabled)
        await message.answer(f"✅ Caption: <b>{'açık' if enabled else 'kapalı'}</b>")
        return

    if key in {"bitrate", "bit hızı", "bithizi"}:
        try:
            bitrate = int(value.removesuffix("k"))
        except ValueError:
            bitrate = 0
        if bitrate not in {128, 192, 320}:
            await message.answer(
                "❌ Kullanım: <code>/ayar bitrate 320</code> (128 / 192 / 320)"
            )
            return
        await db.update_user_settings(message.from_user.id, audio_bitrate=bitrate)
        await message.answer(f"✅ MP3 kalitesi: <b>{bitrate} kbps</b>")
        return

    if key in {"codec", "kodek"}:
        if value not in {"h264", "auto"}:
            await message.answer(
                "❌ Kullanım: <code>/ayar codec h264</code> veya "
                "<code>/ayar codec auto</code>"
            )
            return
        await db.update_user_settings(message.from_user.id, video_codec=value)
        await message.answer(f"✅ Video kodeği: <b>{video_codec_label(value)}</b>")
        return

    if key in {"mod", "mode", "gönderim", "gonderim"}:
        if value in {"dosya", "belge", "document", "file"}:
            mode = "document"
        elif value in {"video", "medya"}:
            mode = "video"
        else:
            await message.answer(
                "❌ Kullanım: <code>/ayar mod dosya</code> veya "
                "<code>/ayar mod video</code>"
            )
            return
        await db.update_user_settings(message.from_user.id, download_mode=mode)
        await message.answer(
            f"✅ Gönderim modu: <b>{download_mode_label(mode)}</b>"
        )
        return

    await message.answer(
        "❌ Bilinmeyen ayar. Kullanım: <code>/ayar kalite 1080</code>, "
        "<code>/ayar ses aç</code>, <code>/ayar caption kapat</code>, "
        "<code>/ayar bitrate 320</code>, <code>/ayar codec h264</code>, "
        "<code>/ayar mod dosya</code>"
    )


@router.callback_query(F.data.startswith("qol|"))
async def cb_preferences(
    callback: CallbackQuery, db: Database, db_user: dict
) -> None:
    if callback.message is None:
        await callback.answer()
        return
    parts = (callback.data or "").split("|", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    _, key, value = parts
    user_id = callback.from_user.id
    if key == "quality":
        await db.update_user_settings(user_id, default_quality=int(value))
    elif key == "bitrate":
        await db.update_user_settings(user_id, audio_bitrate=int(value))
    elif key == "codec":
        await db.update_user_settings(user_id, video_codec=value)
    elif key == "mode":
        await db.update_user_settings(user_id, download_mode=value)
    elif key == "caption":
        await db.update_user_settings(user_id, show_caption=value == "1")
    user = await db.get_user(user_id) or db_user
    await callback.answer("Ayar güncellendi ✅")
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_text(
            _settings_text(user),
            reply_markup=await _preferences_keyboard(user),
            disable_web_page_preview=True,
        )


@router.message(Command("me", "profil", "limit"))
async def cmd_me(
    message: Message,
    db: Database,
    db_user: dict,
    settings: Settings,
    queue: JobQueue,
) -> None:
    today = await db.count_downloads_today(message.from_user.id)
    pending = queue.pending_for(message.from_user.id)
    user = await db.get_user(message.from_user.id) or db_user
    promoted = bool(user.get("is_promoted"))
    daily = settings.daily_limit or 0
    if promoted:
        left = "sınırsız (yetkili)"
    elif not daily:
        left = "sınırsız"
    else:
        left = str(max(0, daily - today - pending))
    await message.answer(
        "<b>📊 Kullanım bilgileriniz</b>\n"
        f"👤 Ad: {escape(user.get('first_name'))}\n"
        f"🆔 ID: <code>{user.get('id')}</code>\n"
        f"⬇️ Toplam indirme: {user.get('downloads', 0)}\n"
        f"💾 Toplam veri: {human_size(user.get('bytes_sent', 0))}\n"
        f"📅 Bugün: {today} • sırada: {pending} • kalan hak: {left}"
        f"{_promoted_line(promoted)}"
    )


@router.callback_query(lambda c: c.data == "usage")
async def cb_usage(
    callback: CallbackQuery,
    db: Database,
    settings: Settings,
    queue: JobQueue,
) -> None:
    today = await db.count_downloads_today(callback.from_user.id)
    pending = queue.pending_for(callback.from_user.id)
    user = await db.get_user(callback.from_user.id) or {}
    promoted = bool(user.get("is_promoted"))
    daily = settings.daily_limit or 0
    if promoted:
        left = "sınırsız (yetkili)"
    elif not daily:
        left = "sınırsız"
    else:
        left = str(max(0, daily - today - pending))
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "<b>📊 Kullanım bilgileriniz</b>\n"
            f"⬇️ Toplam indirme: {user.get('downloads', 0)}\n"
            f"💾 Toplam veri: {human_size(user.get('bytes_sent', 0))}\n"
            f"📅 Bugün: {today} • sırada: {pending} • kalan hak: {left}"
            f"{_promoted_line(promoted)}"
        )
