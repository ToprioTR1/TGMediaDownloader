"""Giris noktasi: `python -m bot`."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from . import netguard
from .config import Settings
from .database import Database
from .downloader import Downloader
from .gallery import GalleryDownloader
from .handlers import admin, download, start
from .middlewares import UserMiddleware
from .queue import JobQueue

log = logging.getLogger(__name__)


def _create_bot(settings: Settings) -> Bot:
    session = None
    if settings.api_url:
        session = AiohttpSession(
            api=TelegramAPIServer.from_base(settings.api_url, is_local=True)
        )
        log.info("Yerel Bot API sunucusu kullaniliyor: %s", settings.api_url)
    return Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def run() -> None:
    settings = Settings.load()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    # yt-dlp cok konuskan, sadece uyari ve hatalari goster
    logging.getLogger("yt_dlp").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    settings.download_dir.mkdir(parents=True, exist_ok=True)

    # Telegram API (yerel Bot API dahil) dışındaki tüm TCP hedefleri indirme
    # korumasından geçer. Yerel Bot API adresi otomatik olarak izin listesine
    # eklenir; ayrıntılar için bot/netguard.py.
    netguard.ensure_for_settings(settings)

    db = Database(settings.db_path)
    await db.connect()
    removed = await db.cache_cleanup(max(settings.cache_days * 2, 45))
    if removed:
        log.info("%s eski onbellek kaydi temizlendi.", removed)

    bot = _create_bot(settings)
    downloader = Downloader(settings)
    gallery = GalleryDownloader(settings)
    queue = JobQueue(bot, db, settings, downloader, gallery)

    dispatcher = Dispatcher(
        storage=MemoryStorage(),
        settings=settings,
        db=db,
        queue=queue,
        downloader=downloader,
        gallery=gallery,
    )

    dispatcher.message.middleware(UserMiddleware(db))
    dispatcher.callback_query.middleware(UserMiddleware(db))

    dispatcher.include_router(start.router)
    dispatcher.include_router(admin.router)
    dispatcher.include_router(download.router)

    await queue.start()
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Başla"),
                BotCommand(command="help", description="Nasıl kullanılır?"),
                BotCommand(command="ayar", description="Kişisel ayarlar"),
                BotCommand(command="me", description="Kullanım bilgilerim"),
            ]
        )
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Webhook silinemedi: %s", exc)

        me = await bot.get_me()
        log.info("Bot calisiyor: @%s (id=%s)", me.username, me.id)
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        await queue.stop()
        await db.close()
        await bot.session.close()
        log.info("Bot durduruldu.")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
