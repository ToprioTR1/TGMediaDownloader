"""Admin komutlari: istatistik, kullanicilar, yayin, yetki yukseltme."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import BaseFilter, Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)

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
        f"💬 Grup: <b>{stats.get('groups', 0)}</b>\n"
        f"⬇️ Başarılı indirme: <b>{stats['downloads_ok']}</b> "
        f"(bugün {stats['downloads_today']})\n"
        f"❌ Hata: {stats['errors']}\n"
        f"💾 Toplam veri: {human_size(stats['bytes'])}\n"
        f"⚡️ Önbellek: {stats.get('cached', 0)} içerik\n"
        f"🚦 Kuyruk: {queue.queued_count} bekleyen • {queue.active_count} aktif"
        f" • {queue.pending_total} iş\n\n"
        f"<b>Platform dağılımı</b>\n{platforms}"
    )


def _cache_text(stats: dict[str, Any]) -> str:
    if not stats["count"]:
        return "<b>⚡️ Medya önbelleği</b>\n\nÖnbellek boş."
    return (
        "<b>⚡️ Medya önbelleği</b>\n"
        f"📦 Kayıt: <b>{stats['count']}</b>\n"
        f"💾 Toplam medya: {human_size(stats['bytes'])}\n"
        f"🕓 En eski: {stats['oldest'] or '-'}\n"
        f"🕓 En yeni: {stats['newest'] or '-'}\n\n"
        "Önbellekten gönderim Telegram'a yeniden yükleme yapmaz."
    )


def _cache_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧹 7 günden eski", callback_data="cache|clean|7"
                ),
                InlineKeyboardButton(
                    text="🧹 30 günden eski", callback_data="cache|clean|30"
                ),
            ],
            [
                InlineKeyboardButton(text="🗑 Tümünü sil", callback_data="cache|clear"),
            ],
        ]
    )


def _cache_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Evet, tümünü sil", callback_data="cache|clear_yes"
                )
            ],
            [InlineKeyboardButton(text="↩️ Vazgeç", callback_data="cache|menu")],
        ]
    )


def _cache_clear_prompt(stats: dict[str, Any]) -> str:
    return (
        "⚠️ <b>Tüm önbellek silinsin mi?</b>\n\n"
        f"{stats['count']} kayıt silinecek. Bu işlem geri alınamaz.\n"
        "Kullanıcılar aynı medyayı tekrar isterse yeniden indirilir."
    )


@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message) -> None:
    await message.answer(
        "<b>🛠 Admin paneli</b>\n"
        "Komutlar:\n"
        "/stats — istatistikler\n"
        "/bandwidth — aylık ve günlük trafik\n"
        "/users — son kullanıcılar\n"
        "/cache — önbellek yönetimi\n"
        "/broadcast &lt;mesaj&gt; — kullanıcılara ve gruplara duyuru\n"
        "/promote @kullanıcıadı — sınırsız günlük indirme hakkı ver\n"
        "/demote @kullanıcıadı — sınırsız hakkı geri al",
        reply_markup=admin_keyboard(),
    )


def _bandwidth_text(report: dict[str, Any], settings: Settings) -> str:
    """Aylik toplam + gunluk trafik dokumu (HTML)."""
    down = int(report.get("bytes_out") or 0)
    up = int(report.get("bytes_in") or 0)
    total = down + up
    jobs = int(report.get("jobs") or 0)
    ok_jobs = int(report.get("ok_jobs") or 0)
    days = int(report.get("days") or 31)
    lines = [
        "<b>📶 Bant genişliği</b>",
        f"Son {days} gün · {ok_jobs} başarılı / {jobs} işlem",
        "",
        f"⬇️ İndirilen (platformdan): <b>{human_size(up)}</b>",
        f"⬆️ Yüklenen (Telegram'a): <b>{human_size(down)}</b>",
        f"Σ Toplam: <b>{human_size(total)}</b>",
    ]
    limit = getattr(settings, "monthly_traffic_gb", 0)
    if limit:
        used_gb = total / (1024**3)
        lines.append(
            f"🎯 Aylık kota: <b>{used_gb:.1f} / {limit} GB</b> "
            f"({used_gb * 100 / limit:.0f}%)"
        )
    daily = report.get("daily") or []
    if not daily:
        lines.extend(["", "Günlük veri yok."])
        return "\n".join(lines)

    lines.extend(["", "<b>Günlük</b>"])
    for row in daily:
        day = str(row.get("day") or "")[5:]
        out_size = int(row.get("bytes_out") or 0)
        in_size = int(row.get("bytes_in") or 0)
        count = int(row.get("ok_jobs") or 0)
        lines.append(
            f"<code>{day}</code>  ⬇️ {human_size(in_size)}  "
            f"⬆️ {human_size(out_size)}  ·  {count}"
        )
    return "\n".join(lines)


@router.message(Command("bandwidth", "trafik", "bandwith"), IsAdmin())
async def cmd_bandwidth(message: Message, db: Database, settings: Settings) -> None:
    report = await db.bandwidth_report(days=31)
    await message.answer(
        _bandwidth_text(report, settings), disable_web_page_preview=True
    )


@router.message(Command("cache", "onbellek"), IsAdmin())
async def cmd_cache(message: Message, db: Database) -> None:
    parts = (message.text or "").split()
    if len(parts) >= 2:
        action = parts[1].lower()
        if action in {"temizle", "clean"}:
            days = 7
            if len(parts) >= 3:
                try:
                    days = int(parts[2])
                except ValueError:
                    await message.answer(
                        "Kullanım: <code>/cache temizle 7</code> (gün sayısı)"
                    )
                    return
                if days < 1:
                    await message.answer(
                        "Gün sayısı en az 1 olmalı. Tümünü silmek için "
                        "<code>/cache tum</code> kullanın; onay istenir."
                    )
                    return
            removed = await db.cache_cleanup(days)
            await message.answer(
                f"🧹 {days} günden eski <b>{removed}</b> önbellek kaydı silindi.\n\n"
                + _cache_text(await db.cache_stats()),
                reply_markup=_cache_keyboard(),
            )
            return
        if action in {"tum", "tüm", "clear"}:
            await message.answer(
                _cache_clear_prompt(await db.cache_stats()),
                reply_markup=_cache_confirm_keyboard(),
            )
            return

    await message.answer(
        _cache_text(await db.cache_stats()), reply_markup=_cache_keyboard()
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
async def cmd_broadcast(message: Message, db: Database, queue: JobQueue) -> None:
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Kullanım: <code>/broadcast mesaj metni</code>")
        return

    user_ids = await db.all_user_ids()
    group_ids = await db.all_group_ids()
    targets = [(user_id, False) for user_id in user_ids]
    targets.extend((group_id, True) for group_id in group_ids)
    status = await message.answer(
        f"📣 Gönderim başladı: {len(user_ids)} kullanıcı, "
        f"{len(group_ids)} grup…"
    )
    sent = failed = 0

    for index, (target_id, is_group) in enumerate(targets, start=1):
        for attempt in range(3):
            try:
                await message.bot.send_message(target_id, text)
                sent += 1
                break
            except TelegramRetryAfter as exc:
                if attempt < 2:
                    await asyncio.sleep(exc.retry_after + 1)
                    continue
                failed += 1
                break
            except TelegramForbiddenError:
                failed += 1
                if is_group:
                    await db.remove_chat(target_id)
                break
            except TelegramBadRequest as exc:
                failed += 1
                if is_group and any(
                    marker in str(exc).lower()
                    for marker in (
                        "chat not found",
                        "bot was kicked",
                        "bot is not a member",
                        "not enough rights",
                        "have no rights",
                    )
                ):
                    await db.remove_chat(target_id)
                break
            except Exception:  # noqa: BLE001
                log.exception("Yayin hatasi: %s", target_id)
                failed += 1
                break
        await asyncio.sleep(0.05)
        if index % 25 == 0:
            # Durum guncellemeleri de flood limitine takilmasin: ortak hub
            # guncellemeleri birlestirir ve RetryAfter'i bekler.
            await queue.edit_status(
                status.chat.id,
                status.message_id,
                f"📣 Gönderiliyor… {index}/{len(targets)} "
                f"(✅ {sent} • ❌ {failed})",
            )

    await queue.edit_status(
        status.chat.id,
        status.message_id,
        f"📣 Yayın tamamlandı.\n✅ Gönderildi: {sent}\n❌ Başarısız: {failed}",
        wait=True,
    )
    await queue.forget_status(status.chat.id, status.message_id)


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
async def cb_admin(
    callback: CallbackQuery, db: Database, queue: JobQueue, settings: Settings
) -> None:
    action = (callback.data or "").split("|", 1)[1]
    if action == "stats":
        await callback.answer()
        if callback.message:
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(
                    _stats_text(await db.stats(), queue=queue),
                    reply_markup=admin_keyboard(),
                )
    elif action == "bandwidth":
        await callback.answer()
        if callback.message:
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(
                    _bandwidth_text(await db.bandwidth_report(days=31), settings),
                    reply_markup=admin_keyboard(),
                    disable_web_page_preview=True,
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
    elif action == "cache":
        await callback.answer()
        if callback.message:
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(
                    _cache_text(await db.cache_stats()),
                    reply_markup=_cache_keyboard(),
                )
    else:
        await callback.answer()


@router.callback_query(IsAdmin(), lambda c: (c.data or "").startswith("cache|"))
async def cb_cache(callback: CallbackQuery, db: Database) -> None:
    parts = (callback.data or "").split("|")
    action = parts[1] if len(parts) > 1 else ""

    if action == "menu":
        await callback.answer()
        if callback.message:
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(
                    _cache_text(await db.cache_stats()),
                    reply_markup=_cache_keyboard(),
                )
        return

    if action == "clean" and len(parts) == 3:
        try:
            days = int(parts[2])
        except ValueError:
            await callback.answer("Geçersiz gün sayısı.", show_alert=True)
            return
        if days < 1:
            await callback.answer("Gün sayısı en az 1 olmalı.", show_alert=True)
            return
        removed = await db.cache_cleanup(days)
        await callback.answer(f"🧹 {removed} kayıt silindi.")
        if callback.message:
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(
                    f"🧹 {days} günden eski <b>{removed}</b> kayıt silindi.\n\n"
                    + _cache_text(await db.cache_stats()),
                    reply_markup=_cache_keyboard(),
                )
        return

    if action == "clear":
        await callback.answer()
        if callback.message:
            stats = await db.cache_stats()
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(
                    _cache_clear_prompt(stats),
                    reply_markup=_cache_confirm_keyboard(),
                )
        return

    if action == "clear_yes":
        removed = await db.cache_clear()
        await callback.answer(f"🗑 {removed} kayıt silindi.")
        if callback.message:
            with suppress(TelegramBadRequest):
                await callback.message.edit_text(
                    f"🗑 <b>{removed}</b> önbellek kaydı silindi.\n\n"
                    + _cache_text(await db.cache_stats()),
                    reply_markup=_cache_keyboard(),
                )
        return

    await callback.answer()
