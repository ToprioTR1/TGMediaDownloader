"""Kullanici kaydi, ban ve bakim modu kontrolleri."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .database import Database

log = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


async def _notify(event: TelegramObject, text: str) -> None:
    if isinstance(event, Message):
        await event.answer(text)
    elif isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)


class UserMiddleware(BaseMiddleware):
    """Her guncellemede kullaniciyi kaydeder ve ban durumunu kontrol eder."""

    def __init__(self, db: Database, admin_ids: frozenset[int] = frozenset()) -> None:
        self._db = db
        self._admin_ids = admin_ids

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        record = await self._db.touch_user(user.id, user.username, user.first_name)
        if record.get("is_banned") and user.id not in self._admin_ids:
            await _notify(event, "🚫 Hesabınız bu botu kullanmaktan engellenmiş.")
            return None

        data["db_user"] = record
        return await handler(event, data)


class MaintenanceMiddleware(BaseMiddleware):
    """Bakim modunda sadece adminlerin islem yapmasina izin verir."""

    def __init__(self, db: Database, admin_ids: frozenset[int]) -> None:
        self._db = db
        self._admin_ids = admin_ids

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id in self._admin_ids:
            return await handler(event, data)

        if await self._db.maintenance():
            await _notify(
                event,
                "🛠 Bot şu anda bakımda. Lütfen biraz sonra tekrar deneyin.",
            )
            return None

        return await handler(event, data)
