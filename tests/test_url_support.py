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

os.environ["BOT_TOKEN"] = "123456:TESTTESTTESTTESTTESTTESTTESTTESTTES"
os.environ["ADMIN_IDS"] = "42"
os.environ["COOKIES_FILE"] = ""

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.downloader import ytdlp_supports_url  # noqa: E402
from bot.handlers.download import _process_text  # noqa: E402


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


class _FakeQueue:
    def __init__(self) -> None:
        self.edited: list[tuple[int, int, str]] = []
        self.forgotten: list[tuple[int, int]] = []

    async def edit_status(
        self, chat_id: int, message_id: int, text: str, **kwargs
    ) -> None:
        self.edited.append((chat_id, message_id, text))

    async def forget_status(self, chat_id: int, message_id: int) -> None:
        self.forgotten.append((chat_id, message_id))


class UnsupportedUrlHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_url_gets_message_without_probe(self) -> None:
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
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("desteklenmiyor", bot.sent[0][1].lower())

    async def test_unsupported_retry_edits_existing_status(self) -> None:
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
        self.assertEqual(len(queue.edited), 1)
        self.assertIn("desteklenmiyor", queue.edited[0][2].lower())
        self.assertEqual(queue.forgotten, [(123, 77)])
