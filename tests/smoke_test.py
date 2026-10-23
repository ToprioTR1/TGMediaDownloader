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
from types import SimpleNamespace
from unittest.mock import patch

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
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import EditMessageText  # noqa: E402
from aiogram.types import (  # noqa: E402
    Audio,
    CallbackQuery,
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Document,
    Message,
    PhotoSize,
    Update,
    User,
    Video,
)

from bot.config import Settings  # noqa: E402
from bot.database import Database  # noqa: E402
from bot.downloader import DownloadError, Downloader, MediaInfo  # noqa: E402
from bot.gallery import GalleryDownloader, GalleryError  # noqa: E402
from bot.handlers import admin, download, start  # noqa: E402
from bot.handlers.download import (  # noqa: E402
    SizeLimitError,
    _gallery_title,
    _gallery_url,
    _limit_error,
    _needs_gallery,
    _request_options,
    _resolve_preset,
)
from bot.middlewares import UserMiddleware  # noqa: E402
from bot.queue import DailyLimitError, Job, JobQueue  # noqa: E402
from bot.status import StatusHub  # noqa: E402
from bot.utils import (  # noqa: E402
    is_blocked_url,
    nearest_height,
    parse_quality,
    wants_document,
)

URL = "https://www.tiktok.com/@tiktok/video/7106594312292453675"
ADMIN = User(id=42, is_bot=False, first_name="Ali", username="alitoprak")
OTHER = User(id=43, is_bot=False, first_name="Baskasi", username="biri")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


class FakeBot:
    """Telegram API cagrilarini kaydeden, dosya gondermeyen sahte bot."""

    id = 999
    username = "smoke_test_bot"

    def __init__(self) -> None:
        self.message_id = 100
        self.calls: list[tuple[str, dict]] = []
        self.texts: dict[int, str] = {}
        self.edit_failures: list[Exception] = []
        self.edit_attempts = 0
        self.active_edits = 0
        self.max_active_edits = 0

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
        self.edit_attempts += 1
        self.active_edits += 1
        self.max_active_edits = max(self.max_active_edits, self.active_edits)
        try:
            await asyncio.sleep(0)
            if self.edit_failures:
                raise self.edit_failures.pop(0)
            if message_id:
                self.texts[message_id] = text or ""
            self.calls.append(
                ("edit_message_text", {"message_id": message_id, "text": text, **kwargs})
            )
            return self._msg(chat_id or 0, text=text or "")
        finally:
            self.active_edits -= 1

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
    chat: Chat | None = None,
) -> Update:
    return Update(
        update_id=message_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=chat or Chat(id=user.id, type="private"),
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


def callback_update(data: str, message: Message, user: User = ADMIN) -> Update:
    return Update(
        update_id=8000,
        callback_query=CallbackQuery(
            id="cb-test",
            from_user=user,
            chat_instance="test",
            data=data,
            message=message,
        ),
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
    queue = JobQueue(
        bot,
        db,
        settings,
        downloader,
        gallery,
        status_min_interval=0.01,
        status_progress_interval=0.05,
        status_retry_backoff=0.01,
    )

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
        check(parse_quality("link 1920") == 1920, "1920 okunamadi")
        check(parse_quality("link 1920p") == 1920, "1920p okunamadi")
        check(parse_quality("link 1920 p") == 1920, "1920 p okunamadi")
        check(parse_quality("link 4k") == 2160, "4k okunamadi")
        check(parse_quality("link") is None, "kalite olmadan deger dondu")
        check(
            parse_quality("https://example.com/video/1920") is None,
            "URL icindeki sayi kalite sayildi",
        )
        check(nearest_height([1280, 720], 1080) == 1280, "en yakin kalite yanlis")
        check(nearest_height([1920, 1280, 1024], 1012) == 1024, "tiktok varsayilani yanlis")
        check(
            wants_document("link dosya")
            and wants_document("link file")
            and wants_document("link fl"),
            "dosya ipucu okunamadi",
        )
        check(not wants_document("link 720p"), "dosya olmayan metin dosya sayildi")
        check(
            not wants_document("https://example.com/file/123"),
            "URL icindeki file dosya ipucu sayildi",
        )
        check(
            not wants_document("https://example.com/fl/123"),
            "URL icindeki fl dosya ipucu sayildi",
        )
        check(
            "nocheckcertificate" not in downloader._base_opts(),
            "TLS sertifika dogrulamasi kapatilmis",
        )
        check(
            await is_blocked_url("http://127.0.0.1:8080/"),
            "loopback adresi engellenmedi",
        )
        check(
            await is_blocked_url("http://169.254.169.254/latest/meta-data"),
            "link-local adresi engellenmedi",
        )
        check(
            not await is_blocked_url("https://1.1.1.1/video"),
            "genel IP yanlislikla engellendi",
        )
        fake_dns = [(2, 1, 6, "", ("127.0.0.1", 0))]
        with patch("socket.getaddrinfo", return_value=fake_dns):
            check(
                await is_blocked_url("http://rebind.example/video"),
                "yerel adrese cozulen alan adi engellenmedi",
            )
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
        status_sends = [c for c in bot.calls[mark:] if c[0] == "send_message"]
        check(
            len(status_sends) == 1,
            f"tek durum mesaji bekleniyordu, {len(status_sends)} mesaj gonderildi",
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
            not _gallery_url("https://instagram.com.evil.example/p/abc/"),
            "instagram benzeri zararli alan adi kabul edildi",
        )
        check(
            not _gallery_url("https://evil.example/?instagram.com/p/abc/"),
            "URL parametresindeki instagram metni kabul edildi",
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

        # Basarisiz gallery-dl cagrisi yarim dosyayi basarili saymamali.
        failure_dir = Path(".tmp/smoke/gallery-failure")
        shutil.rmtree(failure_dir, ignore_errors=True)
        failure_dir.mkdir(parents=True)
        (failure_dir / "partial.jpg").write_bytes(b"partial")
        with (
            patch.object(gallery, "available", True),
            patch.object(
                gallery,
                "_run",
                return_value=SimpleNamespace(
                    returncode=1, stdout="", stderr="network error"
                ),
            ),
        ):
            try:
                gallery.download("https://example.com/post", failure_dir)
            except GalleryError:
                pass
            else:
                check(False, "basarisiz gallery-dl ciktisi basarili sayildi")
        shutil.rmtree(failure_dir, ignore_errors=True)
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

        # Gunluk sayim yerel gece yarisinda sifirlanmali (24 saatlik kayan pencere degil).
        await db.conn.execute(
            "INSERT INTO downloads(user_id, url, status, created_at) "
            "VALUES(?, ?, 'ok', datetime('now', 'localtime', 'start of day', "
            "'-1 second', 'utc'))",
            (99, "https://example.com/old"),
        )
        await db.conn.execute(
            "INSERT INTO downloads(user_id, url, status) VALUES(?, ?, 'ok')",
            (99, "https://example.com/today"),
        )
        await db.conn.commit()
        try:
            check(
                await db.count_downloads_today(99) == 1,
                "gunluk sayim yerel gun sinirini kullanmiyor",
            )
        finally:
            await db.conn.execute("DELETE FROM downloads WHERE user_id = 99")
            await db.conn.commit()

        # Ayni baglantida eszamanli yazmalar birbirinin commit'ini bozmamali.
        await db.touch_user(98, "concurrent", "Concurrent")
        try:
            await asyncio.gather(
                *(
                    db.record_download(
                        98,
                        f"https://example.com/concurrent/{index}",
                        "Test",
                        "h:720",
                        "ok",
                        index,
                    )
                    for index in range(10)
                )
            )
            concurrent_user = await db.get_user(98)
            check(
                concurrent_user is not None and concurrent_user["downloads"] == 10,
                f"eszamanli DB yazmalari kayboldu: {concurrent_user}",
            )
        finally:
            await db.conn.execute("DELETE FROM downloads WHERE user_id = 98")
            await db.conn.execute("DELETE FROM users WHERE id = 98")
            await db.conn.commit()
        print("11) DB kaydi + gunluk sinir + eszamanli yazma OK")

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

        # 15) broadcast (kullanicilar + gruplar)
        group_chat = Chat(id=-100777, type="supergroup", title="Test Grubu")
        await dp.feed_update(bot, msg_update("/start", 115, chat=group_chat))
        check(
            -100777 in await db.all_group_ids(),
            "grup sohbeti kaydedilmedi",
        )
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/broadcast deneme duyurusu", 15))
        check(
            any("deneme duyurusu" in t for t in bot.texts_since(mark)),
            "duyuru gonderilmedi",
        )
        check(
            any(
                call[0] == "send_message"
                and call[1].get("chat_id") == -100777
                and "deneme duyurusu" in (call[1].get("text") or "")
                for call in bot.calls[mark:]
            ),
            "duyuru gruba gonderilmedi",
        )
        check("tamamland" in " ".join(bot.texts.values()).lower(), "duyuru raporu yok")

        bot_member = User(id=bot.id, is_bot=True, first_name="Bot")
        joined = ChatMemberUpdated(
            chat=Chat(id=-100888, type="supergroup", title="Ekleme Grubu"),
            from_user=ADMIN,
            date=datetime.now(timezone.utc),
            old_chat_member=ChatMemberLeft(user=bot_member),
            new_chat_member=ChatMemberMember(user=bot_member),
        )
        await dp.feed_update(bot, Update(update_id=116, my_chat_member=joined))
        check(-100888 in await db.all_group_ids(), "gruba eklenme kaydedilmedi")

        left = ChatMemberUpdated(
            chat=joined.chat,
            from_user=ADMIN,
            date=datetime.now(timezone.utc),
            old_chat_member=ChatMemberMember(user=bot_member),
            new_chat_member=ChatMemberLeft(user=bot_member),
        )
        await dp.feed_update(bot, Update(update_id=117, my_chat_member=left))
        check(-100888 not in await db.all_group_ids(), "gruptan ayrilma silinmedi")
        print("15) /broadcast + grup OK")

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

        # 17) promote / demote (sinirsiz gunluk hak)
        await dp.feed_update(bot, msg_update("/start", 20, user=OTHER))  # kullanici kaydi

        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/promote @alitoprak", 21, user=OTHER))
        check(not await db.is_promoted(ADMIN.id), "admin olmayan promote etti")
        check(not bot.texts_since(mark), "admin olmayan kullaniciya cevap verildi")

        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/promote @BIRI", 22))
        texts = bot.texts_since(mark)
        check(any("sınırsız" in t.lower() for t in texts), "promote calismadi")
        check(any("@biri" in t for t in texts), "promote mesajinda kullanici adi yok")
        check(await db.is_promoted(OTHER.id), "db promote kaydetmedi")

        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/promote @olmayan", 23))
        check(any("bulunamadı" in t for t in bot.texts_since(mark)), "olmayan kullanici")

        await db.record_download(OTHER.id, "test", "Test", "h:720", "ok", 100)
        original_daily = settings.daily_limit
        object.__setattr__(settings, "daily_limit", 1)
        try:
            check(
                await _limit_error(OTHER.id, db, settings, queue) is None,
                "promote gunluk limiti kaldirmadi",
            )
            err = await _limit_error(ADMIN.id, db, settings, queue)
            check(err is not None and "limit" in err.lower(), "limitsiz kullanici engellenmedi")

            mark = len(bot.calls)
            await dp.feed_update(bot, msg_update("/me", 24, user=OTHER))
            check(
                any("Sınırsız indirme hakkı" in t for t in bot.texts_since(mark)),
                "/me sinirsiz hakki gostermedi",
            )

            mark = len(bot.calls)
            await dp.feed_update(bot, msg_update("/users", 25))
            check(any("⭐" in t for t in bot.texts_since(mark)), "/users yildiz gostermedi")

            mark = len(bot.calls)
            await dp.feed_update(bot, msg_update("/demote https://t.me/biri", 26))
            check(any("kaldırıldı" in t for t in bot.texts_since(mark)), "demote calismadi")
            check(not await db.is_promoted(OTHER.id), "db demote kaydetmedi")
            check(
                await _limit_error(OTHER.id, db, settings, queue) is not None,
                "demote sonrasi limit geri gelmedi",
            )
        finally:
            object.__setattr__(settings, "daily_limit", original_daily)
        print("17) promote + demote OK")

        # 18) ayni link tekrar kuyruga alinmaz, fazladan mesaj acilmaz
        q2 = JobQueue(
            bot,
            db,
            settings,
            downloader,
            gallery,
            status_min_interval=0.01,
            status_progress_interval=0.05,
            status_retry_backoff=0.01,
        )
        j1 = Job(
            id="dedup1",
            user_id=ADMIN.id,
            chat_id=ADMIN.id,
            status_message_id=9001,
            url="https://example.com/video",
            preset="h:720",
            title="Test",
            platform="Test",
        )
        j2 = Job(
            id="dedup2",
            user_id=ADMIN.id,
            chat_id=ADMIN.id,
            status_message_id=9002,
            url="https://example.com/video",
            preset="h:720",
            title="Test",
            platform="Test",
        )
        check(await q2.submit(j1) is True, "ilk is kuyruga alinmadi")
        check(await q2.submit(j2) is False, "ayni is tekrar kuyruga alindi")
        check(
            q2._jobs.get((ADMIN.id, ADMIN.id, j1.url, j1.preset)) is j1,
            "ayni is mevcut isi ezdi",
        )
        j3 = Job(
            id="dedup3",
            user_id=ADMIN.id,
            chat_id=777,
            status_message_id=9003,
            url="https://example.com/video",
            preset="h:720",
            title="Test",
            platform="Test",
        )
        check(await q2.submit(j3) is True, "farkli sohbette yeni is acilmadi")
        await q2.stop()

        # Kuyruga ekleme sirasinda gunluk limit de denetlenmeli; ayni kullanicinin
        # arka arkaya gelen istekleri limiti asmamali.
        original_daily = settings.daily_limit
        object.__setattr__(settings, "daily_limit", 2)
        q4 = JobQueue(
            bot,
            db,
            settings,
            downloader,
            gallery,
            status_min_interval=0.01,
            status_progress_interval=0.05,
            status_retry_backoff=0.01,
        )
        try:
            limited_a = Job(
                id="lim1",
                user_id=OTHER.id,
                chat_id=OTHER.id,
                status_message_id=9030,
                url="https://example.com/limit-a",
                preset="h:720",
                title="Limit A",
                platform="Test",
            )
            limited_b = Job(
                id="lim2",
                user_id=OTHER.id,
                chat_id=OTHER.id,
                status_message_id=9031,
                url="https://example.com/limit-b",
                preset="h:720",
                title="Limit B",
                platform="Test",
            )
            check(await q4.submit(limited_a) is True, "limitli ilk is kuyruga alinmadi")
            try:
                await q4.submit(limited_b)
            except DailyLimitError:
                pass
            else:
                check(False, "bekleyen is varken gunluk limit asildi")
        finally:
            await q4.stop()
            object.__setattr__(settings, "daily_limit", original_daily)
        print("18) ayni link dedup + gunluk limit OK")

        # 19) hata sonrasi yeniden dene
        q3 = JobQueue(
            bot,
            db,
            settings,
            downloader,
            gallery,
            status_min_interval=0.01,
            status_progress_interval=0.05,
            status_retry_backoff=0.01,
        )
        failed = Job(
            id="fail1",
            user_id=ADMIN.id,
            chat_id=ADMIN.id,
            status_message_id=9010,
            url="https://example.com/fail",
            preset="h:720",
            title="Test",
            platform="Test",
        )
        q3._remember_retry(failed)
        mark = len(bot.calls)
        check(
            await q3.retry("fail1", ADMIN.id, 9010, OTHER.id) is False,
            "baska kullanici baskasinin isini yeniden denedi",
        )
        check("fail1" in q3._retry_jobs, "yetkisiz retry denemesi isi tuketti")
        check(
            await q3.retry("fail1", ADMIN.id, 9010, ADMIN.id) is True,
            "retry kuyruga alinmadi",
        )
        check(
            any(
                c[0] == "edit_message_text"
                and c[1].get("message_id") == 9010
                and "Sırada" in (c[1].get("text") or "")
                for c in bot.calls[mark:]
            ),
            "retry mevcut mesaji duzenlemedi",
        )
        check(
            not any("Sırada" in t for t in bot.texts_since(mark)),
            "retry yeni mesaj gonderdi",
        )
        retry_job = q3._jobs.get((ADMIN.id, ADMIN.id, failed.url, failed.preset))
        check(retry_job is not None and retry_job.id != "fail1", "retry isi olusmadi")
        check(
            await q3.retry("fail1", ADMIN.id, 9010, ADMIN.id) is False,
            "ayni retry iki kez calisti",
        )
        await q3.stop()
        print("19) yeniden dene OK")

        # 20) eski gecici dosyalar temizlenir
        root = Path(settings.download_dir)
        old_dir = root / "dl-old-test"
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / "x.part").write_text("x", encoding="utf-8")
        old_file = root / "dl-old-test.part"
        old_file.write_text("x", encoding="utf-8")
        fresh_file = root / "dl-fresh-test.tmp"
        fresh_file.write_text("x", encoding="utf-8")
        protected = root / "baska-uygulama.dat"
        protected.write_text("x", encoding="utf-8")
        past = time.time() - 10 * 3600
        os.utime(old_dir, (past, past))
        os.utime(old_file, (past, past))
        os.utime(protected, (past, past))
        removed = queue._cleanup_downloads(3600)
        check(removed >= 2, f"eski dosyalar temizlenmedi: {removed}")
        check(not old_dir.exists(), "eski klasor silinmedi")
        check(not old_file.exists(), "eski dosya silinmedi")
        check(fresh_file.exists(), "yeni dosya yanlislikla silindi")
        check(protected.exists(), "bot disi dosya yanlislikla silindi")
        fresh_file.unlink(missing_ok=True)
        protected.unlink(missing_ok=True)
        print("20) gecici dosya temizligi OK")

        # 21) kisisel ayarlar (/ayar)
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/ayar", 30))
        check(
            any("Kişisel ayarlar" in t for t in bot.texts_since(mark)),
            "/ayar cevap vermedi",
        )

        await dp.feed_update(bot, msg_update("/ayar kalite 1080", 31))
        await dp.feed_update(bot, msg_update("/ayar ses aç", 32))
        await dp.feed_update(bot, msg_update("/ayar caption kapat", 33))
        user = await db.get_user(ADMIN.id) or {}
        check(int(user.get("default_quality") or 0) == 1080, "kalite ayari kaydedilmedi")
        check(bool(user.get("audio_default")), "ses ayari kaydedilmedi")
        check(not bool(user.get("show_caption", 1)), "caption ayari kaydedilmedi")
        requested, audio = _request_options(URL, user)
        check(requested == 1080 and audio, "ayarlar istek cozumlemesine uygulanmadi")
        requested, audio = _request_options(f"{URL} 720p", user)
        check(requested == 720 and not audio, "acik kalite kullanici ayarini ezmedi")

        await dp.feed_update(bot, msg_update("/ayar kalite 0", 34))
        await dp.feed_update(bot, msg_update("/ayar ses kapat", 35))
        await dp.feed_update(bot, msg_update("/ayar caption aç", 36))
        user = await db.get_user(ADMIN.id) or {}
        check(int(user.get("default_quality") or 0) == 0, "kalite sifirlanmadi")
        check(not bool(user.get("audio_default")), "ses kapatilmadi")
        check(bool(user.get("show_caption", 1)), "caption acilmadi")
        print("21) /ayar OK")

        # 22) "dosya" ipucu medyayi belge olarak gonderir
        doc_path = root / "doc-test.bin"
        doc_path.write_bytes(b"test")
        doc_job = Job(
            id="doc1",
            user_id=ADMIN.id,
            chat_id=ADMIN.id,
            status_message_id=9020,
            url="https://example.com/doc",
            preset="h:720",
            title="Dosya testi",
            platform="Test",
            as_document=True,
        )
        check(doc_job.cache_preset == "h:720|doc", "dosya onbellek anahtari yanlis")
        mark = len(bot.calls)
        _, media_type = await queue._send(doc_job, doc_path)
        check(media_type == "document", "dosya olarak gonderilmedi")
        check(
            any(c[0] == "send_document" for c in bot.calls[mark:]),
            "send_document cagrilmadi",
        )
        doc_path.unlink(missing_ok=True)

        gallery_doc = root / "gallery-doc.jpg"
        gallery_doc.write_bytes(b"photo")
        gallery_doc_job = Job(
            id="gdoc1",
            user_id=ADMIN.id,
            chat_id=ADMIN.id,
            status_message_id=9021,
            url="https://example.com/photo",
            preset="gallery",
            title="Galeri dosyasi",
            platform="Test",
            kind="gallery",
            as_document=True,
            show_caption=False,
        )
        mark = len(bot.calls)
        await queue._send_gallery(gallery_doc_job, [gallery_doc])
        check(
            any(c[0] == "send_document" for c in bot.calls[mark:]),
            "galeri dosya olarak gonderilmedi",
        )
        gallery_doc.unlink(missing_ok=True)

        # Medya ulastiktan sonra DB hatasi basarili gonderimi hataya cevirmemeli.
        db_status = await queue.send_status(ADMIN.id, "kayit testi")
        success_job = Job(
            id="okdb1",
            user_id=ADMIN.id,
            chat_id=ADMIN.id,
            status_message_id=db_status.message_id,
            url="https://example.com/ok",
            preset="h:720",
            title="Kayit testi",
            platform="Test",
        )
        original_record = queue._db.record_download

        async def fail_record(*args, **kwargs):
            raise RuntimeError("db yazilamadi")

        queue._db.record_download = fail_record  # type: ignore[method-assign]
        mark = len(bot.calls)
        try:
            await queue._finish_success(success_job, 123)
        finally:
            queue._db.record_download = original_record  # type: ignore[method-assign]
        check(
            any(
                c[0] == "delete_message"
                and c[1].get("message_id") == db_status.message_id
                for c in bot.calls[mark:]
            ),
            "DB hatasinda basarili isin durum mesaji silinmedi",
        )
        check(
            not any(
                c[0] == "edit_message_text"
                and c[1].get("message_id") == db_status.message_id
                and "hata" in (c[1].get("text") or "").lower()
                for c in bot.calls[mark:]
            ),
            "DB hatasi basarili gonderimi hataya cevirdi",
        )
        print("22) dosya olarak gonderim + basari korumasi OK")

        # 23) admin onbellek yonetimi
        cache_stats = await db.cache_stats()
        check(cache_stats["count"] > 0, "onbellek istatistikleri bos")
        check(cache_stats["bytes"] > 0, "onbellek boyutu sifir")
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/cache", 40))
        check(
            any("Medya önbelleği" in t for t in bot.texts_since(mark)),
            "/cache cevap vermedi",
        )
        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/cache temizle 3650", 41))
        check(
            any("0" in t and "silindi" in t for t in bot.texts_since(mark)),
            "/cache temizle calismadi",
        )
        check((await db.cache_stats())["count"] == cache_stats["count"], "onbellek bozuldu")

        mark = len(bot.calls)
        await dp.feed_update(bot, msg_update("/cache tum", 42))
        check(
            any("silinsin mi" in t for t in bot.texts_since(mark)),
            "tum onbellek icin onay istenmedi",
        )
        check(
            (await db.cache_stats())["count"] == cache_stats["count"],
            "/cache tum onay beklemeden sildi",
        )

        confirm_message = simple_message("onay", 9600).as_(bot)
        await dp.feed_update(
            bot, callback_update("cache|clear_yes", confirm_message)
        )
        check((await db.cache_stats())["count"] == 0, "onayli onbellek temizligi calismadi")
        print("23) onbellek yonetimi OK")

        # 24) kesif hatasinda da yeniden dene butonu
        original_probe = downloader.probe

        def fail_probe(url: str) -> MediaInfo:
            raise DownloadError("geçici hata")

        downloader.probe = fail_probe  # type: ignore[method-assign]
        try:
            mark = len(bot.calls)
            await dp.feed_update(bot, msg_update(f"{URL} 720p", 50))
            retry_calls = [
                c
                for c in bot.calls[mark:]
                if c[0] == "edit_message_text" and c[1].get("reply_markup")
            ]
            check(bool(retry_calls), "kesif hatasinda yeniden dene butonu yok")
            cb_data = retry_calls[-1][1]["reply_markup"].inline_keyboard[0][0].callback_data
            check(
                (cb_data or "").startswith("retrytext|"),
                f"kesif retry callback yanlis: {cb_data}",
            )

            token = (cb_data or "").split("|", 1)[1]
            check(token in download._probe_retries, "kesif retry tokeni kaydedilmedi")
            probe_message_id = download._probe_retries[token][2]
            fake_status = simple_message("hata", probe_message_id).as_(bot)

            mark = len(bot.calls)
            await dp.feed_update(
                bot, callback_update(cb_data, fake_status, user=OTHER)
            )
            check(
                token in download._probe_retries,
                "baskasinin tiklamasi kesif retry tokenini tuketti",
            )
            check(
                any(
                    "başka" in (c[1].get("text") or "").lower()
                    for c in bot.calls[mark:]
                    if c[0] == "answer_callback_query"
                ),
                "yetkisiz kesif retry uyarisi verilmedi",
            )

            mark = len(bot.calls)
            await dp.feed_update(bot, callback_update(cb_data, fake_status))
            check(
                any(
                    c[0] == "edit_message_text"
                    and c[1].get("message_id") == probe_message_id
                    and "inceleniyor" in (c[1].get("text") or "")
                    for c in bot.calls[mark:]
                ),
                "kesif retry mevcut mesaji duzenlemedi",
            )
            check(
                not any("inceleniyor" in t for t in bot.texts_since(mark)),
                "kesif retry yeni mesaj gonderdi",
            )
            check(
                any(
                    "geçici hata" in t
                    for t in bot.texts_since(mark, "edit_message_text")
                ),
                "kesif retry hata mesajini gostermedi",
            )
        finally:
            downloader.probe = original_probe  # type: ignore[method-assign]
        print("24) kesif hatasi retry OK")

        # 25) durum mesaji: birlestirme, no-op, RetryAfter, ag hatasi, seri guncelleme
        hub = StatusHub(
            bot,
            min_interval=0.01,
            progress_interval=0.05,
            retry_backoff=0.01,
        )
        try:
            status = await hub.send(ADMIN.id, "durum 0")
            card = hub._cards[(ADMIN.id, status.message_id)]

            mark = len(bot.calls)
            await hub.edit(ADMIN.id, status.message_id, "durum 1")
            await hub.edit(ADMIN.id, status.message_id, "durum 2")
            await hub.edit(ADMIN.id, status.message_id, "durum 3")
            await card.flush()
            edits = [
                c for c in bot.calls[mark:] if c[0] == "edit_message_text"
            ]
            check(
                len(edits) == 1 and edits[0][1].get("text") == "durum 3",
                f"durum guncellemeleri birlestirilmedi: {edits}",
            )

            mark = len(bot.calls)
            await hub.edit(ADMIN.id, status.message_id, "durum 3", wait=True)
            check(
                not any(c[0] == "edit_message_text" for c in bot.calls[mark:]),
                "degismeyen mesaj tekrar duzenlendi",
            )

            bot.edit_failures.append(
                TelegramRetryAfter(
                    method=EditMessageText(
                        chat_id=ADMIN.id,
                        message_id=status.message_id,
                        text="429",
                    ),
                    message="Too Many Requests",
                    retry_after=0,
                )
            )
            mark = len(bot.calls)
            await hub.edit(ADMIN.id, status.message_id, "retry after", wait=True)
            check(
                any(
                    c[0] == "edit_message_text"
                    and c[1].get("text") == "retry after"
                    for c in bot.calls[mark:]
                ),
                "RetryAfter sonrasi guncelleme uygulanmadi",
            )

            bot.edit_failures.extend(
                [
                    TelegramNetworkError(
                        method=EditMessageText(
                            chat_id=ADMIN.id,
                            message_id=status.message_id,
                            text="ag",
                        ),
                        message="network",
                    ),
                    TelegramNetworkError(
                        method=EditMessageText(
                            chat_id=ADMIN.id,
                            message_id=status.message_id,
                            text="ag",
                        ),
                        message="network",
                    ),
                ]
            )
            mark = len(bot.calls)
            await hub.edit(ADMIN.id, status.message_id, "ag sonrasi", wait=True)
            check(
                any(
                    c[0] == "edit_message_text"
                    and c[1].get("text") == "ag sonrasi"
                    for c in bot.calls[mark:]
                ),
                "ag hatasi sonrasi guncelleme uygulanmadi",
            )

            # Iki farkli mesaj ayni sohbette ayni anda duzenlenmeye calisilirsa
            # Telegram cagrilari ust uste binmemeli.
            second = await hub.send(ADMIN.id, "ikinci durum")
            bot.max_active_edits = 0
            await asyncio.gather(
                hub.edit(ADMIN.id, status.message_id, "paralel 1", wait=True),
                hub.edit(ADMIN.id, second.message_id, "paralel 2", wait=True),
            )
            check(
                bot.max_active_edits <= 1,
                f"ayni sohbette eszamanli duzenleme: {bot.max_active_edits}",
            )

            mark = len(bot.calls)
            await hub.delete(ADMIN.id, status.message_id)
            check(
                any(c[0] == "delete_message" for c in bot.calls[mark:]),
                "durum mesaji silinmedi",
            )

            forgotten = await hub.send(ADMIN.id, "unutulacak durum")
            await hub.edit(ADMIN.id, forgotten.message_id, "hata", wait=True)
            await hub.forget(ADMIN.id, forgotten.message_id)
            check(
                (ADMIN.id, forgotten.message_id) not in hub._cards,
                "hata durum karti bellekten cikarilmadi",
            )
        finally:
            await hub.close()
        print("25) durum mesaji hiz siniri OK")

        # 26) medya gonderiminde zaman asimi: video gelmis olabilir, sahte hata verme
        timeout_url = "https://example.com/status-timeout"
        await db.cache_put(
            "timeout-key",
            timeout_url,
            "h:720",
            "vid_timeout",
            "video",
            "Zaman asimi",
            "Test",
            1000,
        )
        status = await queue.send_status(ADMIN.id, "zaman asimi durumu")
        timeout_job = Job(
            id="timeout1",
            user_id=ADMIN.id,
            chat_id=ADMIN.id,
            status_message_id=status.message_id,
            url=timeout_url,
            preset="h:720",
            title="Zaman asimi",
            platform="Test",
            source_url=timeout_url,
            status_header="⏱ Zaman asimi testi",
        )
        original_send_video = bot.send_video

        async def fail_video(*args, **kwargs):
            raise TelegramNetworkError(
                method=EditMessageText(
                    chat_id=ADMIN.id, message_id=status.message_id, text="timeout"
                ),
                message="Request timeout error",
            )

        bot.send_video = fail_video
        try:
            check(await queue.submit(timeout_job), "zaman asimi isi kuyruga eklenemedi")
            await asyncio.wait_for(queue._queue.join(), timeout=30)
        finally:
            bot.send_video = original_send_video
        check(
            "Gönderim yanıtı alınamadı" in bot.texts.get(status.message_id, ""),
            "zaman asimi durumu kullaniciya gosterilmedi",
        )
        await queue.delete_status(ADMIN.id, status.message_id)
        print("26) gonderim zaman asimi OK")

        stats = await db.stats()
        print("27) istatistikler:", stats)
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
