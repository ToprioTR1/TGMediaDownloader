"""Uctan uca duman testi (gercek Telegram tokeni gerektirmez).

Sahte bir Bot nesnesi kullanir; yalnizca medya indirmek icin gercek aga cikar.
Calistirma:

    PYTHONPATH=. python tests/smoke_test.py          # bash
    $env:PYTHONPATH="."; python tests/smoke_test.py  # PowerShell

Test verisi .tmp/smoke/ altinda olusturulur.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["BOT_TOKEN"] = "123456:TESTTESTTESTTESTTESTTESTTESTTESTTES"
os.environ["ADMIN_IDS"] = "42"
os.environ["DB_PATH"] = ".tmp/smoke/bot.db"
os.environ["DOWNLOAD_DIR"] = ".tmp/smoke/downloads"
os.environ["COOLDOWN_SEC"] = "0"
os.environ["MAX_UPLOAD_MB"] = "50"
os.environ["COOKIES_FILE"] = ""

sys.path.insert(0, str(ROOT))

from aiogram import Dispatcher  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import (  # noqa: E402
    Audio,
    Chat,
    Document,
    Message,
    PhotoSize,
    Update,
    User,
    Video,
)

from bot.config import Settings  # noqa: E402
from bot.database import Database  # noqa: E402
from bot.downloader import Downloader, MediaInfo  # noqa: E402
from bot.gallery import GalleryDownloader  # noqa: E402
from bot.handlers import admin, download, start  # noqa: E402
from bot.handlers.download import (  # noqa: E402
    SizeLimitError,
    _gallery_title,
    _gallery_url,
    _needs_gallery,
    _resolve_preset,
)
from bot.middlewares import UserMiddleware  # noqa: E402
from bot.queue import Job, JobQueue  # noqa: E402
from bot.utils import nearest_height, parse_quality  # noqa: E402

URL = "https://www.tiktok.com/@tiktok/video/7106594312292453675"
ADMIN = User(id=42, is_bot=False, first_name="Ali", username="alitoprak")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


class FakeBot:
    """Telegram API cagrilarini kaydeden, dosya gondermeyen sahte bot."""

    id = 999
    username = "smoke_test_bot"

    def __init__(self) -> None:
        self.message_id = 100
        self.calls: list[tuple[str, dict]] = []
        self.texts: dict[int, str] = {}

    def _msg(self, chat_id: int, **extra) -> Message:
        self.message_id += 1
        return Message(
            message_id=self.message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            from_user=User(
                id=self.id, is_bot=True, first_name="Bot", username=self.username
            ),
            **extra,
        ).as_(self)

    async def send_message(self, chat_id: int, text: str, **kwargs) -> Message:
        msg = self._msg(chat_id, text=text, reply_markup=kwargs.get("reply_markup"))
        self.texts[msg.message_id] = text
        self.calls.append(("send_message", {"chat_id": chat_id, "text": text, **kwargs}))
        return msg

    async def edit_message_text(self, chat_id=None, message_id=None, text=None, **kwargs):
        if message_id:
            self.texts[message_id] = text or ""
        self.calls.append(
            ("edit_message_text", {"message_id": message_id, "text": text, **kwargs})
        )
        return self._msg(chat_id or 0, text=text or "")

    async def edit_message_reply_markup(self, **kwargs):
        self.calls.append(("edit_message_reply_markup", kwargs))
        return True

    async def answer_callback_query(self, *args, **kwargs):
        self.calls.append(("answer_callback_query", kwargs))
        return True

    async def send_video(self, chat_id, video, **kwargs):
        if isinstance(video, str):
            entry = {"file_id": video, "cached": True, **kwargs}
        else:
            entry = {
                "path": str(video.path),
                "size": Path(video.path).stat().st_size,
                **kwargs,
            }
        self.calls.append(("send_video", entry))
        return self._msg(
            chat_id,
            video=Video(
                file_id=f"vid_{self.message_id + 1}",
                file_unique_id=f"uvid_{self.message_id + 1}",
                width=1920,
                height=1080,
                duration=10,
            ),
        )

    async def send_audio(self, chat_id, audio, **kwargs):
        if isinstance(audio, str):
            entry = {"file_id": audio, "cached": True, **kwargs}
        else:
            entry = {
                "path": str(audio.path),
                "size": Path(audio.path).stat().st_size,
                **kwargs,
            }
        self.calls.append(("send_audio", entry))
        return self._msg(
            chat_id,
            audio=Audio(
                file_id=f"aud_{self.message_id + 1}",
                file_unique_id=f"uaud_{self.message_id + 1}",
                duration=10,
            ),
        )

    async def send_document(self, chat_id, document, **kwargs):
        if isinstance(document, str):
            entry = {"file_id": document, "cached": True, **kwargs}
        else:
            entry = {"path": str(document.path), **kwargs}
        self.calls.append(("send_document", entry))
        return self._msg(
            chat_id,
            document=Document(
                file_id=f"doc_{self.message_id + 1}",
                file_unique_id=f"udoc_{self.message_id + 1}",
            ),
        )

    async def send_photo(self, chat_id, photo, **kwargs):
        entry = (
            {"file_id": photo, "cached": True, **kwargs}
            if isinstance(photo, str)
            else {"path": str(photo.path), **kwargs}
        )
        self.calls.append(("send_photo", entry))
        return self._msg(
            chat_id,
            photo=[
                PhotoSize(
                    file_id=f"pho_{self.message_id + 1}",
                    file_unique_id=f"upho_{self.message_id + 1}",
                    width=1080,
                    height=1350,
                )
            ],
        )

    async def send_media_group(self, chat_id, media, **kwargs):
        self.calls.append(
            ("send_media_group", {"chat_id": chat_id, "count": len(media), **kwargs})
        )
        return [self._msg(chat_id) for _ in media]

    async def delete_message(self, **kwargs):
        self.calls.append(("delete_message", kwargs))
        return True

    async def set_my_commands(self, *args, **kwargs):
        return True

    async def __call__(self, method, **kwargs):
        """aiogram'in `await bot(method)` cagrilarini karsilar."""
        mapping = {
            "SendMessage": self.send_message,
            "EditMessageText": self.edit_message_text,
            "EditMessageReplyMarkup": self.edit_message_reply_markup,
            "AnswerCallbackQuery": self.answer_callback_query,
            "SendVideo": self.send_video,
            "SendAudio": self.send_audio,
            "SendDocument": self.send_document,
            "SendPhoto": self.send_photo,
            "SendMediaGroup": self.send_media_group,
            "DeleteMessage": self.delete_message,
        }
        handler = mapping[type(method).__name__]
        fields = {
            key: getattr(method, key)
            for key in type(method).model_fields
            if getattr(method, key) is not None
        }
        fields.pop("business_connection_id", None)
        return await handler(**fields)

    # ------------------------------------------------------------- yardimcilar

    def texts_since(self, index: int, kind: str = "send_message") -> list[str]:
        return [call[1].get("text") or "" for call in self.calls[index:] if call[0] == kind]

    def media_since(self, index: int) -> list[tuple[str, dict]]:
        return [c for c in self.calls[index:] if c[0] in ("send_video", "send_audio")]


def msg_update(
    text: str = "",
    message_id: int = 1,
    user: User = ADMIN,
    *,
    caption: str | None = None,
    reply_to: Message | None = None,
) -> Update:
    return Update(
        update_id=message_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user.id, type="private"),
            from_user=user,
            text=text or None,
            caption=caption,
            reply_to_message=reply_to,
        ),
    )


def simple_message(text: str, message_id: int = 900, user: User = ADMIN) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user.id, type="private"),
        from_user=user,
        text=text,
    )


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main() -> int:
    shutil.rmtree(".tmp/smoke", ignore_errors=True)
    Path(".tmp/smoke/downloads").mkdir(parents=True, exist_ok=True)

    settings = Settings.load()
    db = Database(settings.db_path)
    await db.connect()

    bot = FakeBot()
    downloader = Downloader(settings)
    gallery = GalleryDownloader(settings)
    queue = JobQueue(bot, db, settings, downloader, gallery)

    dp = Dispatcher(
        storage=MemoryStorage(),
        settings=settings,
        db=db,
        queue=queue,
        downloader=downloader,
        gallery=gallery,
    )
    dp.message.middleware(UserMiddleware(db))
    dp.callback_query.middleware(UserMiddleware(db))
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(download.router)

    await queue.start()
    try:
        # 0) yardimci fonksiyonlar
        check(parse_quality("link 1080p") == 1080, "1080p okunamadi")
        check(parse_quality("link 720 p") == 720, "720 p okunamadi")
        check(parse_quality("link 4k") == 2160, "4k okunamadi")
        check(parse_quality("link") is None, "kalite olmadan deger dondu")
        check(nearest_height([1280, 720], 1080) == 1280, "en yakin kalite yanlis")
        check(nearest_height([1920, 1280, 1024], 1012) == 1024, "tiktok varsayilani yanlis")
        check(settings.default_height_for("Instagram") == 1280, "instagram varsayilani")
        check(settings.default_height_for("TikTok") == 1012, "tiktok varsayilani")
        check(settings.default_height_for("YouTube") == 720, "youtube varsayilani")
        print("0) kalite cozumleme OK")

        # 1) /start
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/start"))
        check(any("Merhaba" in t for t in bot.texts_since(mark)), "/start cevap vermedi")
        print("1) /start OK")

        # varsayilan secimi dogrulamak icin mevcut kaliteleri ogren
        info = await asyncio.to_thread(downloader.probe, URL)
        tiktok_height = nearest_height(info.heights, settings.quality_tiktok)

        # 2) kalite yazilmadan link -> platform varsayilani
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update(URL, 2))
        await asyncio.wait_for(queue._queue.join(), timeout=180)
        media = bot.media_since(mark)
        check(bool(media), "medya gonderilmedi: " + str(bot.texts_since(mark, "edit_message_text")))
        check(
            f"{tiktok_height}p" in (media[0][1].get("caption") or ""),
            f"varsayilan kalite uygulanmadi: {media[0][1].get('caption')}",
        )
        check(
            any(c[0] == "delete_message" for c in bot.calls[mark:]),
            "durum mesaji silinmedi",
        )
        markup = media[0][1].get("reply_markup")
        buttons = [b for row in markup.inline_keyboard for b in row] if markup else []
        check(
            any(b.text == "🎵 Ses olarak al" for b in buttons),
            "ses butonu eklenmemis",
        )
        check(
            all(
                not (b.callback_data or "").startswith("act|again") for b in buttons
            ),
            "tekrar gonder butonu hala ekleniyor",
        )
        print(f"2) varsayilan kalite OK: TikTok {settings.quality_tiktok}p -> {tiktok_height}p")

        # 3) "1280p" yazili link -> en yakin kalite
        expected = nearest_height(info.heights, 1280)
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update(f"{URL} 1280p", 3))
        await asyncio.wait_for(queue._queue.join(), timeout=180)
        media = bot.media_since(mark)
        check(bool(media), "istek uzerine indirme yok")
        check(
            f"{expected}p" in (media[0][1].get("caption") or ""),
            f"istenen kalite uygulanmadi: {media[0][1].get('caption')}",
        )
        print(f"3) istenen kalite OK: 1280p -> {expected}p")

        # 4) "mp3" -> sadece ses
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update(f"{URL} mp3", 4))
        await asyncio.wait_for(queue._queue.join(), timeout=180)
        audio = [c for c in bot.media_since(mark) if c[0] == "send_audio"]
        check(bool(audio), "ses gonderilmedi")
        print(f"4) MP3 OK: {audio[0][1]['size'] / 1024:.0f} KB")

        # 5) ayni video tekrar -> file_id onbellegi
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update(URL, 5))
        await asyncio.wait_for(queue._queue.join(), timeout=60)
        media = bot.media_since(mark)
        check(bool(media) and media[0][1].get("cached"), "onbellekten gonderilmedi")
        check(
            any(c[0] == "delete_message" for c in bot.calls[mark:]),
            "onbellek durum mesaji silinmedi",
        )
        print("5) file_id onbellegi OK")

        # 6) caption icindeki link (Instagram gondermeleri boyle gelir)
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update(caption=f"{URL} mp3", message_id=6))
        await asyncio.wait_for(queue._queue.join(), timeout=60)
        audio = [c for c in bot.media_since(mark) if c[0] == "send_audio"]
        check(bool(audio) and audio[0][1].get("cached"), "caption'daki link islenmedi")
        print("6) caption destegi OK")

        # 7) yanitlanan mesajdaki link + kalite ipucu
        mark = len(bot.calls)
        await dp.feed_update(
            bot,
            msg_update("mp3", 7, reply_to=simple_message(URL)),
        )
        await asyncio.wait_for(queue._queue.join(), timeout=60)
        audio = [c for c in bot.media_since(mark) if c[0] == "send_audio"]
        check(bool(audio) and audio[0][1].get("cached"), "yanitlanan link islenmedi")
        print("7) yanit destegi OK")

        # 8) boyut korumasi: 1080p 80MB ise 720p'ye duser
        big_info = MediaInfo(
            url="https://example.com/v",
            title="Buyuk video",
            uploader=None,
            duration=60,
            thumbnail=None,
            heights=[1080, 720, 360],
            has_audio=True,
            platform="YouTube",
            webpage_url="https://example.com/v",
            estimated_sizes={
                1080: 80 * 1024 * 1024,
                720: 20 * 1024 * 1024,
                360: 8 * 1024 * 1024,
            },
        )
        preset, target, chosen, downgraded = _resolve_preset(
            big_info, 1080, False, settings
        )
        check(
            chosen == 720 and downgraded and preset == "h:720",
            f"boyut dusurmesi yanlis: {chosen}",
        )
        big_info.estimated_sizes = {
            1080: 90 * 1024 * 1024,
            720: 70 * 1024 * 1024,
            360: 60 * 1024 * 1024,
        }
        try:
            _resolve_preset(big_info, 1080, False, settings)
        except SizeLimitError:
            pass
        else:
            check(False, "limiti asan kalite icin hata verilmedi")
        print("8) boyut korumasi OK")

        # 9) galeri yardimcilari
        check(
            _gallery_url("https://www.instagram.com/p/abc/"),
            "instagram adresi taninmadi",
        )
        check(
            _gallery_title([{"description": "ilk satir\nikinci"}]) == "ilk satir",
            "galeri basligi yanlis",
        )
        photo_info = MediaInfo(
            url="u",
            title="t",
            uploader=None,
            duration=None,
            thumbnail=None,
            heights=[],
            has_audio=False,
            platform="Instagram",
            webpage_url="u",
        )
        check(_needs_gallery(photo_info), "fotograf gonderisi taninmadi")
        print("9) galeri tespiti OK")

        # 10) galeri indirme ve gonderim (gercek gallery-dl)
        if gallery.available:
            gallery_status = await bot.send_message(ADMIN.id, "galeri testi")
            job = Job(
                id="gal1",
                user_id=ADMIN.id,
                chat_id=ADMIN.id,
                status_message_id=gallery_status.message_id,
                url="https://danbooru.donmai.us/posts/1",
                preset="gallery",
                title="Galeri testi",
                platform="Test",
                kind="gallery",
                source_url="https://danbooru.donmai.us/posts/1",
            )
            mark = len(bot.calls)
            await queue.submit(job)
            await asyncio.wait_for(queue._queue.join(), timeout=180)
            photos = [c for c in bot.calls[mark:] if c[0] == "send_photo"]
            check(bool(photos), "galeri fotografi gonderilmedi")
            check(
                any(c[0] == "delete_message" for c in bot.calls[mark:]),
                "galeri durum mesaji silinmedi",
            )
            print("10) galeri gonderimi OK")
        else:
            print("10) galeri gonderimi ATLANDI (gallery-dl kurulu degil)")

        # 11) veritabani kaydi
        user = await db.get_user(ADMIN.id)
        check(user is not None and user["downloads"] == 7, f"indirme kaydedilmedi: {user}")
        print("11) DB kaydi OK")

        # 12) /stats
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/stats", 6))
        check(
            any("statistikleri" in t.lower() for t in bot.texts_since(mark)),
            "/stats cevap vermedi",
        )
        print("12) /stats OK")

        # 13) /help ve /me
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/help", 9))
        await dp.feed_update(bot, msg_update("/me", 10))
        texts = bot.texts_since(mark)
        check(any("Nas" in t for t in texts) and any("Kullan" in t for t in texts), "/help /me")
        print("13) /help + /me OK")

        # 14) /users
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/users", 11))
        check(any("42" in t for t in bot.texts_since(mark)), "/users")
        print("14) /users OK")

        # 15) broadcast
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/broadcast deneme duyurusu", 15))
        check(
            any("deneme duyurusu" in t for t in bot.texts_since(mark)),
            "duyuru gonderilmedi",
        )
        check("tamamland" in " ".join(bot.texts.values()).lower(), "duyuru raporu yok")
        print("15) /broadcast OK")

        # 16) cooldown (settings gecici olarak 10 sn yapilir)
        object.__setattr__(settings, "cooldown_sec", 10)
        try:
            download._last_request[ADMIN.id] = time.monotonic()
            mark = len(bot.calls)
            await dp.feed_update(bot, msg_update(f"{URL} 720p", 16))
            check(
                any("h" in t and "zl" in t for t in bot.texts_since(mark)),
                "cooldown calismadi",
            )
        finally:
            object.__setattr__(settings, "cooldown_sec", 0)
            download._last_request.pop(ADMIN.id, None)
        print("16) cooldown OK")

        stats = await db.stats()
        print("17) istatistikler:", stats)
    except AssertionError as exc:
        print("HATA:", exc)
        return 1
    finally:
        await queue.stop()
        await db.close()

    print("SONUC: TUM TESTLER GECTI")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
