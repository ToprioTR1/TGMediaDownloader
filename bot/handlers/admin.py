"""Admin komutlari: istatistik, kullanicilar, yayin."""

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
        "/broadcast &lt;mesaj&gt; — herkese duyuru",
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
        lines.append(
            f"• {name}{username} — <code>{user['id']}</code> — "
            f"{user.get('downloads', 0)} indirme"
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
            lines.append(f"• {name} — <code>{user['id']}</code> — {user.get('downloads', 0)}")
        if callback.message:
            with suppress(TelegramBadRequest):
                await callback.message.answer("\n".join(lines))
    else:
        await callback.answer()
