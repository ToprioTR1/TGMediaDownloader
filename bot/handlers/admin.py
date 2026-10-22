"""Admin komutlari: istatistik, kullanicilar, yayin, yetki yukseltme."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, Message, TelegramObject

from ..config import Settings
from ..database import Database
from ..keyboards import admin_keyboard
from ..queue import JobQueue
from ..utils import escape, human_size

log = logging.getLogger(__name__)

router = Router(name="admin")


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, **kwargs: Any) -> bool:
        settings: Settings | None = kwargs.get("settings")
        user = getattr(event, "from_user", None)
        return bool(settings and user and user.id in settings.admin_ids)


def _stats_text(stats: dict[str, Any], *, queue: JobQueue) -> str:
    platforms = "\n".join(
        f"  • {name}: {count}" for name, count in stats["top_platforms"]
    ) or "  • veri yok"
    return (
        "<b>📊 Bot istatistikleri</b>\n"
        f"👥 Kullanıcı: <b>{stats['users']}</b> (bugün +{stats['users_today']})\n"
        f"⬇️ Başarılı indirme: <b>{stats['downloads_ok']}</b> "
        f"(bugün {stats['downloads_today']})\n"
        f"❌ Hata: {stats['errors']}\n"
        f"💾 Toplam veri: {human_size(stats['bytes'])}\n"
        f"⚡️ Önbellek: {stats.get('cached', 0)} içerik\n"
        f"🚦 Kuyruk: {queue.queued_count} bekleyen • {queue.active_count} aktif\n\n"
        f"<b>Platform dağılımı</b>\n{platforms}"
    )


@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message) -> None:
    await message.answer(
        "<b>🛠 Admin paneli</b>\n"
        "Komutlar:\n"
        "/stats — istatistikler\n"
        "/users — son kullanıcılar\n"
        "/broadcast &lt;mesaj&gt; — herkese duyuru\n"
        "/promote @kullanıcıadı — sınırsız günlük indirme hakkı ver\n"
        "/demote @kullanıcıadı — sınırsız hakkı geri al",
        reply_markup=admin_keyboard(),
    )


@router.message(Command("stats"), IsAdmin())
async def cmd_stats(message: Message, db: Database, queue: JobQueue) -> None:
    stats = await db.stats()
    await message.answer(_stats_text(stats, queue=queue))


@router.message(Command("users"), IsAdmin())
async def cmd_users(message: Message, db: Database) -> None:
    users = await db.recent_users(10)
    if not users:
        await message.answer("Henüz kullanıcı yok.")
        return
    lines = ["<b>👥 Son 10 kullanıcı</b>"]
    for user in users:
        name = escape(user.get("first_name") or "?")
        username = f" (@{escape(user['username'])})" if user.get("username") else ""
        star = " ⭐" if user.get("is_promoted") else ""
        lines.append(
            f"• {name}{username} — <code>{user['id']}</code> — "
            f"{user.get('downloads', 0)} indirme{star}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("broadcast"), IsAdmin())
async def cmd_broadcast(message: Message, db: Database) -> None:
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Kullanım: <code>/broadcast mesaj metni</code>")
        return

    user_ids = await db.all_user_ids()
    status = await message.answer(f"📣 Gönderim başladı: {len(user_ids)} kullanıcı…")
    sent = failed = 0

    for index, user_id in enumerate(user_ids, start=1):
        for attempt in range(2):
            try:
                await message.bot.send_message(user_id, text)
                sent += 1
                break
            except TelegramRetryAfter as exc:
                if attempt == 0:
                    await asyncio.sleep(exc.retry_after + 1)
                    continue
                failed += 1
            except (TelegramForbiddenError, TelegramBadRequest):
                failed += 1
                break
            except Exception:  # noqa: BLE001
                log.exception("Yayin hatasi: %s", user_id)
                failed += 1
                break
        await asyncio.sleep(0.05)
        if index % 25 == 0:
            with suppress(TelegramBadRequest):
                await status.edit_text(
                    f"📣 Gönderiliyor… {index}/{len(user_ids)} "
                    f"(✅ {sent} • ❌ {failed})"
                )

    with suppress(TelegramBadRequest):
        await status.edit_text(
            f"📣 Yayın tamamlandı.\n✅ Gönderildi: {sent}\n❌ Başarısız: {failed}"
        )


def _username_arg(message: Message) -> str | None:
    """Mesajdan @kullaniciadi / t.me/kullaniciadi degerini cikarir."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None

    raw = parts[1].strip()
    for prefix in ("https://", "http://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :]
    for prefix in ("www.t.me/", "t.me/", "telegram.me/"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :]
            break
    raw = raw.split("?")[0].strip("/").strip()
    if raw.startswith("@"):
        raw = raw[1:]
    return raw or None


def _has_target(message: Message) -> bool:
    return bool(
        (message.reply_to_message and message.reply_to_message.from_user)
        or _username_arg(message)
    )


async def _resolve_target(message: Message, db: Database) -> dict[str, Any] | None:
    if message.reply_to_message and message.reply_to_message.from_user:
        return await db.get_user(message.reply_to_message.from_user.id)
    username = _username_arg(message)
    if username is None:
        return None
    return await db.get_user_by_username(username)


def _display_user(user: dict[str, Any]) -> str:
    if user.get("username"):
        return f"@{escape(user['username'])}"
    return f"<code>{user['id']}</code>"


@router.message(Command("promote"), IsAdmin())
async def cmd_promote(message: Message, db: Database) -> None:
    if not _has_target(message):
        await message.answer(
            "Kullanım: <code>/promote @kullanıcıadı</code> "
            "veya bir mesaja yanıt verin.\n"
            "Örnek: <code>/promote @mamiiblt</code>"
        )
        return

    target = await _resolve_target(message, db)
    if target is None:
        await message.answer(
            "Kullanıcı bulunamadı. Kullanıcı adını kontrol edin; "
            "kullanıcı bota en az bir kez yazmış olmalı."
        )
        return

    await db.set_promoted(target["id"], True)
    await message.answer(
        f"⭐ {_display_user(target)} artık <b>sınırsız günlük indirme</b> hakkına sahip."
    )
    with suppress(Exception):
        await message.bot.send_message(
            target["id"],
            "⭐ <b>Sınırsız indirme hakkı tanımlandı!</b>\n"
            "Artık günlük indirme limiti sizin için uygulanmıyor.",
        )


@router.message(Command("demote"), IsAdmin())
async def cmd_demote(message: Message, db: Database) -> None:
    if not _has_target(message):
        await message.answer(
            "Kullanım: <code>/demote @kullanıcıadı</code> "
            "veya bir mesaja yanıt verin.\n"
            "Örnek: <code>/demote @mamiiblt</code>"
        )
        return

    target = await _resolve_target(message, db)
    if target is None:
        await message.answer(
            "Kullanıcı bulunamadı. Kullanıcı adını kontrol edin; "
            "kullanıcı bota en az bir kez yazmış olmalı."
        )
        return

    await db.set_promoted(target["id"], False)
    await message.answer(
        f"↩️ {_display_user(target)} için sınırsız hak kaldırıldı; normal limit geçerli."
    )
    with suppress(Exception):
        await message.bot.send_message(
            target["id"],
            "ℹ️ Sınırsız indirme hakkınız kaldırıldı. Günlük limit yeniden geçerli.",
        )


@router.callback_query(IsAdmin(), lambda c: (c.data or "").startswith("admin|"))
async def cb_admin(callback: CallbackQuery, db: Database, queue: JobQueue) -> None:
    action = (callback.data or "").split("|", 1)[1]
    if action == "stats":
        await callback.answer()
        if callback.message:
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(
                    _stats_text(await db.stats(), queue=queue),
                    reply_markup=admin_keyboard(),
                )
    elif action == "users":
        await callback.answer()
        users = await db.recent_users(10)
        lines = ["<b>👥 Son 10 kullanıcı</b>"]
        for user in users:
            name = escape(user.get("first_name") or "?")
            star = " ⭐" if user.get("is_promoted") else ""
            lines.append(
                f"• {name} — <code>{user['id']}</code> — "
                f"{user.get('downloads', 0)} indirme{star}"
            )
        if callback.message:
            with suppress(TelegramBadRequest):
                await callback.message.answer("\n".join(lines))
    else:
        await callback.answer()
