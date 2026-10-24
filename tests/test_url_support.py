r"""Yalnızca yt-dlp'nin tanıdığı URL'lerin işlenmesini doğrular.

Çalıştırma:
    .\.venv\Scripts\python.exe -m unittest tests.test_url_support -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["BOT_TOKEN"] = "123456:TESTTESTTESTTESTTESTTESTTESTTESTTES"
os.environ["ADMIN_IDS"] = "42"
os.environ["COOKIES_FILE"] = ""

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.downloader import DownloadError, ytdlp_supports_url  # noqa: E402
from bot.handlers.download import (  # noqa: E402
    _gallery_platform,
    _gallery_title,
    _gallery_url,
    _process_text,
    _tiktok_photo_url,
    on_text,
)
from bot.utils import UNSUPPORTED_URL_TEXT  # noqa: E402

TIKTOK_PHOTO_URL = (
    "https://www.tiktok.com/@fukumean_777/photo/7699870291001920788"
    "?_r=1&_t=ZS-9AowHbPOQ0f"
)
TIKTOK_SHORT_URL = "https://vm.tiktok.com/ZMabc123/"


class UrlSupportTests(unittest.TestCase):
    def test_known_site_extractors_match(self) -> None:
        self.assertTrue(
            ytdlp_supports_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        )
        self.assertTrue(
            ytdlp_supports_url(
                "https://www.tiktok.com/@tiktok/video/7106594312292453675"
            )
        )
        self.assertTrue(
            ytdlp_supports_url("https://www.instagram.com/reel/C8abcDEF123/")
        )

    def test_official_short_links_match(self) -> None:
        for url in (
            "https://pin.it/abc123",
            "https://fb.watch/abc123/",
            "https://redd.it/abc123",
            "https://b23.tv/abc123",
            "https://instagr.am/p/C8abcDEF123/",
        ):
            self.assertTrue(ytdlp_supports_url(url), url)

    def test_generic_web_pages_and_direct_files_are_rejected(self) -> None:
        for url in (
            "https://example.com",
            "https://example.com/sayfa",
            "https://example.com/video.mp4",
            "https://example.com/image.jpg",
            "https://pin.it.evil.com/abc123",
            "https://evil.com/?u=https://pin.it/abc123",
            "ftp://pin.it/abc123",
            "",
        ):
            self.assertFalse(ytdlp_supports_url(url), url)

    def test_tiktok_photo_urls_use_gallery(self) -> None:
        for url in (
            TIKTOK_PHOTO_URL,
            "https://www.tiktok.com/t/ZTabc123/",
            TIKTOK_SHORT_URL,
            "https://vt.tiktok.com/ZMabc123/",
        ):
            self.assertTrue(_gallery_url(url), url)
        self.assertTrue(_tiktok_photo_url(TIKTOK_PHOTO_URL))
        self.assertFalse(_tiktok_photo_url(TIKTOK_SHORT_URL))
        self.assertFalse(
            _gallery_url("https://www.tiktok.com/@user/video/123")
        )
        self.assertFalse(
            _gallery_url("https://www.tiktok.com.evil.example/@user/photo/123")
        )

    def test_gallery_platform_and_title(self) -> None:
        self.assertEqual(_gallery_platform(TIKTOK_PHOTO_URL), "TikTok")
        self.assertEqual(
            _gallery_platform("https://www.instagram.com/p/abc/"), "Instagram"
        )
        self.assertEqual(
            _gallery_title([{"desc": "TikTok başlığı"}], "TikTok"),
            "TikTok başlığı",
        )
        self.assertEqual(_gallery_title([], "TikTok"), "TikTok gönderisi")


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, dict]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent.append((chat_id, text, kwargs))


class _FakeDownloader:
    def __init__(self) -> None:
        self.probed: list[str] = []

    def supports_url(self, url: str) -> bool:
        return False

    def probe(self, url: str):
        self.probed.append(url)
        raise AssertionError("desteklenmeyen URL için probe çağrılmamalı")


class _FakeGallery:
    available = False


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.caption = None
        self.reply_to_message = None
        self.from_user = SimpleNamespace(id=42, is_bot=False)
        self.chat = SimpleNamespace(id=123)
        self.bot = _FakeBot()


class _FakeQueue:
    def __init__(self) -> None:
        self.edited: list[tuple[int, int, str]] = []
        self.deleted: list[tuple[int, int]] = []
        self.forgotten: list[tuple[int, int]] = []

    async def edit_status(
        self, chat_id: int, message_id: int, text: str, **kwargs
    ) -> None:
        self.edited.append((chat_id, message_id, text))

    async def delete_status(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    async def forget_status(self, chat_id: int, message_id: int) -> None:
        self.forgotten.append((chat_id, message_id))


class _TikTokDownloader:
    def __init__(self) -> None:
        self.probed: list[str] = []

    def supports_url(self, url: str) -> bool:
        return True

    def probe(self, url: str):
        self.probed.append(url)
        raise DownloadError(UNSUPPORTED_URL_TEXT)


class _GalleryWithItems:
    available = True

    def __init__(self) -> None:
        self.probed: list[str] = []

    def probe(self, url: str) -> list[dict]:
        self.probed.append(url)
        return [
            {"url": "https://cdn.example/1.jpeg", "extension": "jpeg", "desc": "test"},
            {"url": "https://cdn.example/2.jpeg", "extension": "jpeg"},
        ]


class _FailingGallery:
    available = True

    def __init__(self) -> None:
        self.probed: list[str] = []

    def probe(self, url: str) -> list[dict]:
        self.probed.append(url)
        return []


class _FakeDb:
    async def get_user(self, user_id: int) -> dict:
        return {}


class _GalleryQueue:
    def __init__(self) -> None:
        self.submitted: list = []
        self.edited: list[tuple[int, int, str]] = []
        self.deleted: list[tuple[int, int]] = []
        self.forgotten: list[tuple[int, int]] = []
        self.status = SimpleNamespace(message_id=11)

    def pending_for(self, user_id: int) -> int:
        return 0

    async def send_status(self, chat_id: int, text: str, **kwargs):
        return self.status

    async def edit_status(
        self, chat_id: int, message_id: int, text: str, **kwargs
    ) -> None:
        self.edited.append((chat_id, message_id, text))

    async def delete_status(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    async def forget_status(self, chat_id: int, message_id: int) -> None:
        self.forgotten.append((chat_id, message_id))

    async def submit(self, job) -> bool:
        self.submitted.append(job)
        return True


class UnsupportedUrlHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_text_ignores_unsupported_url_silently(self) -> None:
        message = _FakeMessage("https://example.com/sayfa")
        downloader = _FakeDownloader()

        await on_text(  # type: ignore[arg-type]
            message,
            settings=SimpleNamespace(cooldown_sec=0),  # type: ignore[arg-type]
            db=None,  # type: ignore[arg-type]
            downloader=downloader,  # type: ignore[arg-type]
            gallery=_FakeGallery(),  # type: ignore[arg-type]
            queue=None,  # type: ignore[arg-type]
        )

        self.assertEqual(downloader.probed, [])
        self.assertEqual(message.bot.sent, [])

    async def test_unsupported_url_is_ignored_without_probe(self) -> None:
        bot = _FakeBot()
        downloader = _FakeDownloader()

        await _process_text(
            bot,  # type: ignore[arg-type]
            123,
            42,
            "https://example.com/sayfa",
            settings=SimpleNamespace(),  # type: ignore[arg-type]
            db=None,  # type: ignore[arg-type]
            downloader=downloader,  # type: ignore[arg-type]
            gallery=_FakeGallery(),  # type: ignore[arg-type]
            queue=None,  # type: ignore[arg-type]
        )

        self.assertEqual(downloader.probed, [])
        self.assertEqual(bot.sent, [])

    async def test_unsupported_retry_deletes_existing_status(self) -> None:
        bot = _FakeBot()
        queue = _FakeQueue()
        existing = SimpleNamespace(message_id=77)

        await _process_text(
            bot,  # type: ignore[arg-type]
            123,
            42,
            "https://example.com/sayfa",
            settings=SimpleNamespace(),  # type: ignore[arg-type]
            db=None,  # type: ignore[arg-type]
            downloader=_FakeDownloader(),  # type: ignore[arg-type]
            gallery=_FakeGallery(),  # type: ignore[arg-type]
            queue=queue,  # type: ignore[arg-type]
            only_url="https://example.com/sayfa",
            existing_status=existing,  # type: ignore[arg-type]
        )

        self.assertEqual(bot.sent, [])
        self.assertEqual(queue.edited, [])
        self.assertEqual(queue.deleted, [(123, 77)])
        self.assertEqual(queue.forgotten, [(123, 77)])


class TikTokPhotoFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, url: str, gallery=None):
        bot = _FakeBot()
        downloader = _TikTokDownloader()
        gallery = gallery or _GalleryWithItems()
        queue = _GalleryQueue()

        with patch(
            "bot.handlers.download.is_blocked_url",
            new=AsyncMock(return_value=False),
        ):
            await _process_text(
                bot,  # type: ignore[arg-type]
                123,
                42,
                url,
                settings=SimpleNamespace(  # type: ignore[arg-type]
                    max_queue_per_user=5,
                    daily_limit=0,
                ),
                db=_FakeDb(),  # type: ignore[arg-type]
                downloader=downloader,  # type: ignore[arg-type]
                gallery=gallery,  # type: ignore[arg-type]
                queue=queue,  # type: ignore[arg-type]
            )
        return downloader, gallery, queue

    async def test_tiktok_photo_uses_gallery_directly(self) -> None:
        downloader, gallery, queue = await self._run(TIKTOK_PHOTO_URL)

        self.assertEqual(downloader.probed, [])
        self.assertEqual(gallery.probed, [TIKTOK_PHOTO_URL])
        self.assertEqual(len(queue.submitted), 1)
        job = queue.submitted[0]
        self.assertEqual(job.platform, "TikTok")
        self.assertEqual(job.kind, "gallery")
        self.assertEqual(job.title, "test")

    async def test_tiktok_short_link_falls_back_to_gallery(self) -> None:
        downloader, gallery, queue = await self._run(TIKTOK_SHORT_URL)

        self.assertEqual(downloader.probed, [TIKTOK_SHORT_URL])
        self.assertEqual(gallery.probed, [TIKTOK_SHORT_URL])
        self.assertEqual(len(queue.submitted), 1)
        self.assertEqual(queue.submitted[0].platform, "TikTok")

    async def test_tiktok_photo_gallery_failure_is_reported(self) -> None:
        downloader, gallery, queue = await self._run(
            TIKTOK_PHOTO_URL, _FailingGallery()
        )

        self.assertEqual(downloader.probed, [TIKTOK_PHOTO_URL])
        self.assertEqual(queue.submitted, [])
        self.assertEqual(len(queue.edited), 1)
        self.assertIn("fotoğraflar alınamadı", queue.edited[0][2].lower())
        self.assertEqual(queue.deleted, [])
        self.assertEqual(queue.forgotten, [(123, 11)])
