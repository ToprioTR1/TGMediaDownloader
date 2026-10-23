"""Kullanici kaydi middleware'i."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from .database import Database

log = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class UserMiddleware(BaseMiddleware):
    """Her guncellemede kullaniciyi ve grup sohbetini veritabanina kaydeder."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        chat = getattr(event, "chat", None) or data.get("event_chat")
        if chat is not None:
            chat_type = getattr(chat.type, "value", chat.type)
            if chat_type in {"group", "supergroup"}:
                await self._db.touch_chat(
                    chat.id, getattr(chat, "title", None), str(chat_type)
                )

        user: User | None = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        record = await self._db.touch_user(user.id, user.username, user.first_name)
        data["db_user"] = record
        return await handler(event, data)
