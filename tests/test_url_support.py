r"""Yalnızca yt-dlp'nin tanıdığı URL'lerin işlenmesini doğrular.

Çalıştırma:
    .\.venv\Scripts\python.exe -m unittest tests.test_url_support -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
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

from bot.downloader import (  # noqa: E402
    DownloadError,
    Downloader,
    MediaInfo,
    ytdlp_supports_url,
)
from bot.gallery import GalleryDownloader, GalleryError, _queued_urls  # noqa: E402
from bot.handlers.download import (  # noqa: E402
    _gallery_platform,
    _gallery_title,
    _gallery_url,
    _instagram_post_url,
    _needs_gallery,
    _pinterest_url,
    _probe_gallery,
    _process_text,
    _tiktok_photo_url,
    on_text,
)
from bot.utils import UNSUPPORTED_URL_TEXT, is_instagram_url  # noqa: E402

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
            _gallery_platform("https://www.pinterest.com/pin/123456/"),
            "Pinterest",
        )
        self.assertEqual(_gallery_title([], "Pinterest"), "Pinterest gönderisi")
        self.assertEqual(
            _gallery_title([{"desc": "TikTok başlığı"}], "TikTok"),
            "TikTok başlığı",
        )
        self.assertEqual(_gallery_title([], "TikTok"), "TikTok gönderisi")

    def test_pinterest_urls_use_gallery(self) -> None:
        for url in (
            "https://www.pinterest.com/pin/123456/",
            "https://pinterest.com/pin/123456/",
            "https://tr.pinterest.com/pin/123456/",
            "https://pin.it/abc123",
        ):
            self.assertTrue(_gallery_url(url), url)
            self.assertTrue(_pinterest_url(url), url)
        for url in (
            "https://www.pinterest.com.evil.example/pin/123456/",
            "https://evil.example/?u=https://pin.it/abc123",
        ):
            self.assertFalse(_pinterest_url(url), url)

    def test_pinterest_image_pin_needs_gallery(self) -> None:
        info = MediaInfo(
            url="https://www.pinterest.com/pin/1/",
            title="Pin",
            uploader=None,
            duration=None,
            thumbnail=None,
            heights=[],
            has_audio=True,
            platform="Pinterest",
            webpage_url="https://www.pinterest.com/pin/1/",
        )
        self.assertTrue(_needs_gallery(info))
        info.heights = [720]
        self.assertFalse(_needs_gallery(info))

    def test_instagram_post_url_detection(self) -> None:
        self.assertTrue(
            _instagram_post_url("https://www.instagram.com/p/DeHco0SA5Jy/")
        )
        self.assertTrue(
            _instagram_post_url("https://instagram.com/p/abc/?utm_source=x")
        )
        self.assertFalse(
            _instagram_post_url("https://www.instagram.com/reel/abc/")
        )
        self.assertFalse(
            _instagram_post_url("https://www.instagram.com/p.evil.example/p/abc/")
        )


class GalleryQueueParsingTests(unittest.TestCase):
    def test_queue_urls_are_extracted(self) -> None:
        data = [
            [2, {"category": "tiktok"}],
            [6, "https://www.tiktok.com/@user/photo/123", {}],
            [6, "ftp://example.com/ignore"],
            [6, "https://www.tiktok.com/@user/photo/456"],
        ]
        self.assertEqual(
            _queued_urls(data, 10),
            [
                "https://www.tiktok.com/@user/photo/123",
                "https://www.tiktok.com/@user/photo/456",
            ],
        )
        self.assertEqual(
            _queued_urls(data, 1),
            ["https://www.tiktok.com/@user/photo/123"],
        )


class _QueueFollowingGallery(GalleryDownloader):
    def __init__(self) -> None:
        self.available = True
        self._settings = SimpleNamespace(gallery_limit=10)
        self.calls: list[str] = []

    def _base_cmd(self) -> list[str]:
        return []

    def _common_args(self, url: str | None = None) -> list[str]:
        return []

    def _run(self, cmd, timeout):
        url = cmd[-1]
        self.calls.append(url)
        if url == TIKTOK_SHORT_URL:
            data = [[6, TIKTOK_PHOTO_URL, {}]]
        else:
            data = [[3, "https://cdn.example/1.jpg", {"extension": "jpg"}]]
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")


class GalleryProbeTests(unittest.TestCase):
    def test_short_link_queue_is_followed(self) -> None:
        gallery = _QueueFollowingGallery()

        items = gallery.probe(TIKTOK_SHORT_URL)

        self.assertEqual(gallery.calls, [TIKTOK_SHORT_URL, TIKTOK_PHOTO_URL])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://cdn.example/1.jpg")


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
    has_cookies = True

    def __init__(self) -> None:
        self.probed: list[str] = []

    def probe_embed(self, url: str) -> list[dict]:
        return []

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


class CookieScopeTests(unittest.TestCase):
    def test_instagram_url_detection(self) -> None:
        for url in (
            "https://www.instagram.com/p/abc/",
            "https://instagram.com/reel/abc/",
            "https://instagr.am/p/abc/",
        ):
            self.assertTrue(is_instagram_url(url), url)
        for url in (
            "https://www.tiktok.com/@x/video/1",
            "https://www.youtube.com/watch?v=x",
            "https://instagram.com.evil.example/p/abc/",
        ):
            self.assertFalse(is_instagram_url(url), url)

    def test_ytdlp_cookies_only_for_instagram(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cookies-") as raw:
            cookies = Path(raw) / "cookies.txt"
            cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            downloader = object.__new__(Downloader)
            downloader._settings = SimpleNamespace(  # type: ignore[attr-defined]
                cookies_file=cookies, proxy=None
            )
            instagram = downloader._base_opts("https://www.instagram.com/p/abc/")
            youtube = downloader._base_opts("https://www.youtube.com/watch?v=x")
            self.assertEqual(instagram.get("cookiefile"), str(cookies))
            self.assertNotIn("cookiefile", youtube)
            self.assertNotIn("cookiefile", downloader._base_opts())

    def test_gallery_cookies_only_for_instagram(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cookies-") as raw:
            cookies = Path(raw) / "cookies.txt"
            cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            gallery = object.__new__(GalleryDownloader)
            gallery._settings = SimpleNamespace(  # type: ignore[attr-defined]
                cookies_file=cookies, proxy=None
            )
            instagram = gallery._common_args("https://www.instagram.com/p/abc/")
            tiktok = gallery._common_args("https://www.tiktok.com/@x/photo/1")
            pinterest = gallery._common_args("https://www.pinterest.com/pin/1/")
            self.assertIn("--cookies", instagram)
            self.assertIn(str(cookies), instagram)
            self.assertNotIn("--cookies", tiktok)
            self.assertNotIn("--cookies", pinterest)


class InstagramFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_timeout_still_tries_embed(self) -> None:
        gallery = object.__new__(GalleryDownloader)
        gallery._settings = SimpleNamespace(gallery_limit=10)
        gallery.available = True
        with (
            patch.object(
                gallery, "_probe", side_effect=GalleryError("zaman aşımı")
            ),
            patch(
                "bot.gallery.fetch_instagram_embed",
                return_value=[{"url": "https://cdn.example/1.jpg", "extension": "jpg"}],
            ),
        ):
            items = gallery.probe("https://www.instagram.com/p/abc/")
        self.assertEqual(len(items), 1)

    async def test_without_cookies_skips_slow_gallery_probe(self) -> None:
        gallery = _InstagramGallery(embed_items=[])
        items = await _probe_gallery(
            gallery, "https://www.instagram.com/p/abc/"  # type: ignore[arg-type]
        )
        self.assertIsNone(items)
        self.assertEqual(gallery.embed_calls, ["https://www.instagram.com/p/abc/"])
        self.assertEqual(gallery.probe_calls, [])

    async def test_embed_items_are_used_without_gallery_probe(self) -> None:
        item = {"url": "https://cdn.example/1.jpg", "extension": "jpg"}
        gallery = _InstagramGallery(embed_items=[item])
        items = await _probe_gallery(
            gallery, "https://www.instagram.com/p/abc/"  # type: ignore[arg-type]
        )
        self.assertEqual(items, [item])
        self.assertEqual(gallery.probe_calls, [])

    async def test_instagram_post_uses_gallery_before_ytdlp(self) -> None:
        url = "https://www.instagram.com/p/DeHco0SA5Jy/"
        bot = _FakeBot()
        downloader = _TikTokDownloader()
        gallery = _GalleryWithItems()
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
        self.assertEqual(downloader.probed, [], "fotoğraf gönderisinde yt-dlp denenmemeli")
        self.assertEqual(gallery.probed, [url])
        self.assertEqual(len(queue.submitted), 1)
        self.assertEqual(queue.submitted[0].kind, "gallery")


class _InstagramGallery:
    available = True
    has_cookies = False

    def __init__(self, embed_items: list[dict]) -> None:
        self.embed_items = embed_items
        self.embed_calls: list[str] = []
        self.probe_calls: list[str] = []

    def probe_embed(self, url: str) -> list[dict]:
        self.embed_calls.append(url)
        return self.embed_items

    def probe(self, url: str) -> list[dict]:
        self.probe_calls.append(url)
        raise AssertionError("cerez yokken gallery-dl denenmemeli")


class PinterestFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinterest_pin_uses_gallery_after_ytdlp(self) -> None:
        url = "https://www.pinterest.com/pin/123456/"
        bot = _FakeBot()
        downloader = _TikTokDownloader()
        gallery = _GalleryWithItems()
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

        # Video icin once yt-dlp denenir; basarisizsa galeriye dusulur.
        self.assertEqual(downloader.probed, [url])
        self.assertEqual(gallery.probed, [url])
        self.assertEqual(len(queue.submitted), 1)
        job = queue.submitted[0]
        self.assertEqual(job.platform, "Pinterest")
        self.assertEqual(job.kind, "gallery")


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
