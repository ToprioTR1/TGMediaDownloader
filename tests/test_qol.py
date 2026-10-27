import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import FSInputFile

from bot.downloader import Downloader, MediaInfo
from bot.database import Database
from bot.qol import (
    CATEGORY_ACCESS,
    CATEGORY_SIZE,
    audio_label,
    error_category,
    error_hint,
    media_buttons,
    playlist_menu,
    playlist_summary,
    quality_menu,
    quota_error_text,
    quota_warning_text,
    retry_keyboard,
    video_codec_label,
    video_label,
)
from bot.queue import Job, JobQueue, MonthlyQuotaError


def _info(**overrides) -> MediaInfo:
    data = dict(
        url="https://example.com/v",
        title="Test videosu",
        uploader="Kanal",
        duration=90,
        thumbnail=None,
        heights=[720, 480, 360],
        has_audio=True,
        platform="YouTube",
        webpage_url="https://example.com/v",
        estimated_sizes={},
    )
    data.update(overrides)
    return MediaInfo(**data)


def _buttons(markup) -> list:
    return [b for row in markup.inline_keyboard for b in row]


class MediaButtonTests(unittest.TestCase):
    def test_video_buttons_include_quick_actions(self) -> None:
        keyboard = media_buttons(
            source_url="https://example.com/v",
            cache_key="k" * 32,
            can_audio=True,
            can_document=True,
            can_thumbnail=True,
            can_quality=True,
        )
        texts = [b.text for b in _buttons(keyboard)]
        self.assertIn("🎵 Ses olarak al", texts)
        self.assertIn("🎚 Kalite değiştir", texts)
        self.assertIn("📄 Dosya olarak gönder", texts)
        self.assertIn("🖼 Kapak fotoğrafı", texts)
        self.assertIn("🔗 Kaynak", texts)
        callbacks = [b.callback_data for b in _buttons(keyboard)]
        self.assertIn(f"act|audio|{'k' * 32}", callbacks)

    def test_audio_only_hides_quality_and_document(self) -> None:
        keyboard = media_buttons(
            source_url=None,
            cache_key="k",
            can_audio=False,
            can_document=False,
            can_thumbnail=False,
            can_quality=False,
        )
        self.assertIsNone(keyboard)

    def test_group_chat_shows_same_actions(self) -> None:
        """Gruplarda da tum aksiyonlar gorunmeli (Telegram destekliyor)."""
        private = media_buttons(
            source_url="https://example.com/v",
            cache_key="k",
            can_audio=True,
            can_document=True,
            can_thumbnail=True,
            can_quality=True,
        )
        group = media_buttons(
            source_url="https://example.com/v",
            cache_key="k",
            can_audio=True,
            can_document=True,
            can_thumbnail=True,
            can_quality=True,
        )
        assert private is not None and group is not None
        self.assertEqual(
            [b.text for b in _buttons(private)],
            [b.text for b in _buttons(group)],
        )
        group_texts = [b.text for b in _buttons(group)]
        self.assertIn("🎚 Kalite değiştir", group_texts)
        self.assertIn("🖼 Kapak fotoğrafı", group_texts)

    def test_labels(self) -> None:
        self.assertEqual(video_label("h:720"), "720p")
        self.assertEqual(video_label("h:720|doc"), "720p · dosya")
        self.assertEqual(video_label("audio", 320), "MP3 320 kbps")
        self.assertEqual(audio_label("128"), "MP3 128 kbps")
        self.assertEqual(audio_label(None), "MP3 192 kbps")
        self.assertEqual(video_codec_label("h264"), "H.264 (uyumlu)")
        self.assertEqual(video_codec_label("auto"), "Otomatik")


class SmartErrorTests(unittest.TestCase):
    def test_error_categories(self) -> None:
        self.assertEqual(
            error_category("Dosya 80 MB boyutunda. Telegram limiti 50 MB."),
            CATEGORY_SIZE,
        )
        self.assertEqual(
            error_category("Bu içerik için giriş gerekiyor. Çerez gerekli."),
            CATEGORY_ACCESS,
        )
        self.assertEqual(
            error_category("Platform yanıt vermedi (zaman aşımı). Tekrar deneyin."),
            "transient",
        )

    def test_size_error_offers_lower_quality_not_document(self) -> None:
        keyboard = retry_keyboard(
            "job1",
            preset="h:1080",
            has_lower=True,
            source_url="https://example.com/v",
            max_upload_mb=50,
            category=CATEGORY_SIZE,
        )
        callbacks = [b.callback_data for b in _buttons(keyboard)]
        self.assertIn("retry|job1", callbacks)
        self.assertIn("act|lower|job1", callbacks)
        self.assertNotIn("act|asdoc|job1", callbacks)

    def test_access_error_offers_document_and_source(self) -> None:
        keyboard = retry_keyboard(
            "job1",
            preset="h:720",
            has_lower=False,
            source_url="https://example.com/v",
            max_upload_mb=50,
            category=CATEGORY_ACCESS,
        )
        callbacks = [b.callback_data for b in _buttons(keyboard)]
        self.assertIn("act|asdoc|job1", callbacks)
        self.assertTrue(any(b.url == "https://example.com/v" for b in _buttons(keyboard)))

    def test_hints_are_category_specific(self) -> None:
        self.assertIn("50 MB", error_hint(CATEGORY_SIZE, max_upload_mb=50))
        self.assertIn("dosya", error_hint(CATEGORY_ACCESS, max_upload_mb=50))

    def test_generic_download_error_has_no_document_button(self) -> None:
        """Indirme hatasinda dosya yok; "dosya olarak gonder" onerilmemeli."""
        keyboard = retry_keyboard(
            "job1",
            preset="h:720",
            has_lower=False,
            source_url=None,
            max_upload_mb=50,
            category="generic",
        )
        callbacks = [b.callback_data for b in _buttons(keyboard)]
        self.assertNotIn("act|asdoc|job1", callbacks)
        self.assertIn("retry|job1", callbacks)

    def test_quality_menu_keeps_cache_key(self) -> None:
        """Kalite butonlari medyanin onbellek anahtarini tasimali."""
        cache_key = "a" * 32
        menu = quality_menu("h:720", [1080, 720], cache_key=cache_key)
        datas = [b.callback_data for b in _buttons(menu)]
        self.assertIn(f"act|quality|1080|{cache_key}", datas)
        self.assertIn(f"act|quality|mp3|{cache_key}", datas)
        self.assertIn(f"act|qclose|{cache_key}", datas)
        # Kalite butonlari: aksiyon|kalite|onbellek_anahtari
        quality_datas = [d for d in datas if d.startswith("act|quality|")]
        self.assertEqual(len(quality_datas), 3)
        for data in quality_datas:
            parts = data.split("|")
            self.assertEqual(len(parts), 4)
            self.assertEqual(parts[-1], cache_key)

    def test_quality_menu_shows_estimated_sizes(self) -> None:
        menu = quality_menu(
            "h:720",
            [1080, 720, 360],
            cache_key="k" * 32,
            sizes={1080: 60 * 1024 * 1024, 720: 20 * 1024 * 1024},
            max_upload_mb=50,
        )
        labels = [b.text for b in _buttons(menu)]
        big = next(label for label in labels if "1080p" in label)
        small = next(label for label in labels if "720p" in label)
        self.assertIn("⚠️", big)
        self.assertIn("60.0 MB", big)
        self.assertIn("~20.0 MB", small)
        # Boyut bilinmeyen kalite sadece yukseklik gosterir.
        self.assertTrue(any("360p" in label and "MB" not in label for label in labels))


class PlaylistTests(unittest.TestCase):
    def test_summary_lists_first_entries_and_total(self) -> None:
        entries = [
            _info(title="Birinci", duration=60),
            _info(title="İkinci", duration=30),
        ]
        info = _info(
            title="Liste",
            playlist_count=5,
            playlist_entries=entries,
        )
        settings = SimpleNamespace(playlist_limit=5, playlist_max_total_sec=1800)
        text = playlist_summary(info, settings)
        self.assertIn("5 video", text)
        self.assertIn("Birinci", text)
        self.assertIn("İkinci", text)
        self.assertIn("01:30", text)

    def test_menu_offers_all_more_audio_cancel(self) -> None:
        entries = [_info(title=f"V{i}") for i in range(5)]
        info = _info(playlist_count=5, playlist_entries=entries)
        keyboard = playlist_menu(
            info=info, token="tok", has_more=True, can_expand=True
        )
        callbacks = [b.callback_data for b in _buttons(keyboard)]
        self.assertIn("play|all|tok", callbacks)
        self.assertIn("play|more|tok", callbacks)
        self.assertIn("play|audio|tok", callbacks)
        self.assertIn("play|cancel|tok", callbacks)


class PlaylistProbeTests(unittest.TestCase):
    def test_probe_returns_playlist_entries(self) -> None:
        downloader = object.__new__(Downloader)
        downloader._settings = SimpleNamespace(
            max_duration_sec=1800,
            max_height=0,
            playlist_limit=5,
        )
        calls: list[str] = []

        def fake_extract(url: str):
            calls.append(url)
            if url == "https://example.com/list":
                return {
                    "_type": "playlist",
                    "title": "Liste",
                    "entries": [
                        {"url": "https://example.com/1"},
                        {"url": "https://example.com/2"},
                    ],
                }
            return {
                "title": "Video " + url.rsplit("/", 1)[-1],
                "duration": 10,
                "formats": [
                    {"height": 720, "vcodec": "h264", "acodec": "aac", "filesize": 1000}
                ],
                "extractor_key": "Youtube",
            }

        downloader._extract_info = fake_extract  # type: ignore[method-assign]
        info = downloader.probe("https://example.com/list")
        self.assertTrue(info.is_playlist)
        self.assertEqual(info.playlist_count, 2)
        self.assertEqual(len(info.playlist_entries), 2)
        self.assertEqual(info.title, "Liste")
        self.assertEqual(
            calls,
            [
                "https://example.com/list",
                "https://example.com/1",
                "https://example.com/2",
            ],
        )

    def test_single_video_is_not_playlist(self) -> None:
        downloader = object.__new__(Downloader)
        downloader._settings = SimpleNamespace(
            max_duration_sec=1800,
            max_height=0,
            playlist_limit=5,
        )
        downloader._extract_info = lambda url: {  # type: ignore[method-assign]
            "title": "Tek",
            "duration": 10,
            "formats": [
                {"height": 720, "vcodec": "h264", "acodec": "aac", "filesize": 1000}
            ],
            "extractor_key": "Youtube",
        }
        info = downloader.probe("https://example.com/v")
        self.assertFalse(info.is_playlist)
        self.assertEqual(info.playlist_count, 0)


class _FakeBot:
    def __init__(self) -> None:
        self.edits: list[dict] = []
        self.messages: list[str] = []

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def delete_message(self, **kwargs):
        return True

    async def send_message(self, *args, **kwargs):
        text = kwargs.get("text")
        if text is None and len(args) > 1:
            text = args[1]
        self.messages.append(text or "")
        return SimpleNamespace(message_id=1)


class QueueQolTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_position_and_cancel(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qol-") as raw:
            settings = SimpleNamespace(
                concurrency=1,
                download_dir=Path(raw),
                max_queue_per_user=5,
                daily_limit=0,
                cache_days=0,
                max_upload_bytes=50 * 1024 * 1024,
                max_upload_mb=50,
                upload_timeout_sec=30,
                gallery_enabled=False,
                playlist_limit=5,
                playlist_max_total_sec=1800,
            )
            db = SimpleNamespace(is_promoted=AsyncMock(return_value=False))
            downloader = SimpleNamespace(
                download=lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError("downloader cagrilmamali")
                )
            )
            queue = JobQueue(_FakeBot(), db, settings, downloader)  # type: ignore[arg-type]

            def make_job(job_id: str) -> Job:
                return Job(
                    id=job_id,
                    user_id=42,
                    chat_id=42,
                    status_message_id=1,
                    url=f"https://example.com/{job_id}",
                    preset="h:720",
                    title="Test",
                    platform="YouTube",
                )

            first = make_job("a")
            second = make_job("b")
            await queue.submit(first)
            await queue.submit(second)
            self.assertEqual(queue.queue_position(first), 1)
            self.assertEqual(queue.queue_position(second), 2)

            state = await queue.cancel("b", chat_id=42, user_id=42)
            self.assertEqual(state, "cancelled")
            self.assertTrue(queue.is_cancelled(second))
            self.assertEqual(
                await queue.cancel("b", chat_id=42, user_id=43), "missing"
            )

    async def test_queue_text_shows_position(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qol-") as raw:
            settings = SimpleNamespace(
                concurrency=1,
                download_dir=Path(raw),
                max_queue_per_user=5,
                daily_limit=0,
                cache_days=0,
                max_upload_bytes=50 * 1024 * 1024,
                max_upload_mb=50,
                upload_timeout_sec=30,
                gallery_enabled=False,
                playlist_limit=5,
                playlist_max_total_sec=1800,
            )
            queue = JobQueue(
                _FakeBot(),
                SimpleNamespace(is_promoted=AsyncMock(return_value=False)),
                settings,
                SimpleNamespace(),
            )  # type: ignore[arg-type]
            job = Job(
                id="a",
                user_id=42,
                chat_id=42,
                status_message_id=1,
                url="https://example.com/a",
                preset="h:720",
                title="Test",
                platform="YouTube",
                status_stage="queued",
            )
            queue._queue.put_nowait(job)
            queue._pending[42] = 1
            text = queue._queue_text(job, "⏳ Sırada…")
            self.assertIn("Sıra", text)
            self.assertIn("1", text)


class CoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_cover_is_sent_as_file_not_thumbnail_file_id(self) -> None:
        """Telegram thumbnail file_id'sini fotograf olarak kabul etmiyor."""
        from bot.handlers.download import _send_cover

        with tempfile.TemporaryDirectory(prefix="cover-") as raw:
            cover = Path(raw) / "k.jpg"
            cover.write_bytes(b"\xff\xd8\xff\xd9")
            bot = _CaptureBot()
            message = SimpleNamespace(chat=SimpleNamespace(id=42))
            sent = await _send_cover(
                bot,  # type: ignore[arg-type]
                42,
                message,  # type: ignore[arg-type]
                {"title": "Kapak", "cover_path": str(cover), "url": "https://e/v"},
                30,
            )
            self.assertTrue(sent)
            self.assertEqual(bot.photos, 1)
            photo = bot.photo_arg
            # file_id (str) yerine yerel dosya gonderilmeli.
            self.assertFalse(isinstance(photo, str))
            self.assertEqual(Path(photo.path).name, "k.jpg")

    async def test_missing_cover_falls_back_to_source(self) -> None:
        from bot.handlers.download import _send_cover

        bot = _CaptureBot()
        sent = await _send_cover(
            bot,  # type: ignore[arg-type]
            42,
            SimpleNamespace(chat=SimpleNamespace(id=42)),  # type: ignore[arg-type]
            {"title": "Kapak", "cover_path": None, "url": "https://e/v"},
            30,
        )
        self.assertTrue(sent)
        self.assertEqual(bot.photos, 0)
        self.assertEqual(bot.messages, 1)


class _CaptureBot:
    def __init__(self) -> None:
        self.photos = 0
        self.messages = 0
        self.photo_arg = None

    async def send_photo(self, **kwargs):
        self.photos += 1
        self.photo_arg = kwargs.get("photo")
        return SimpleNamespace(message_id=1)

    async def send_message(self, **kwargs):
        self.messages += 1
        return SimpleNamespace(message_id=1)


class QuotaTextTests(unittest.TestCase):
    def test_error_text_only_when_full(self) -> None:
        self.assertIsNone(quota_error_text(0, 0))
        self.assertIsNone(quota_error_text(1 * 1024**3, 2))
        text = quota_error_text(2 * 1024**3, 2)
        assert text is not None
        self.assertIn("doldu", text)
        self.assertIn("2 GB", text)

    def test_warning_texts(self) -> None:
        self.assertIn("%80", quota_warning_text(int(0.8 * 1024**3), 1, 80))
        self.assertIn("doldu", quota_warning_text(1024**3, 1, 100))


class VideoConversionTests(unittest.IsolatedAsyncioTestCase):
    async def _queue(self, raw: str, bot):
        settings = SimpleNamespace(
            concurrency=1,
            download_dir=Path(raw),
            max_queue_per_user=5,
            daily_limit=0,
            cache_days=0,
            max_upload_bytes=50 * 1024 * 1024,
            max_upload_mb=50,
            upload_timeout_sec=30,
            gallery_enabled=False,
            playlist_limit=5,
            playlist_max_total_sec=1800,
            monthly_traffic_gb=0,
            admin_ids=frozenset(),
        )
        return JobQueue(
            bot,
            SimpleNamespace(is_promoted=AsyncMock(return_value=False)),
            settings,
            SimpleNamespace(),
        )  # type: ignore[arg-type]

    async def test_unplayable_video_is_converted_and_resent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="convert-") as raw:
            bot = _VideoBot()
            queue = await self._queue(raw, bot)
            path = Path(raw) / "video.mp4"
            path.write_bytes(b"original")
            converted = Path(raw) / "video-telegram.mp4"
            converted.write_bytes(b"converted")

            async def fake_convert(_path):
                return converted

            queue._convert_video = fake_convert  # type: ignore[method-assign]
            job = Job(
                id="v1",
                user_id=42,
                chat_id=42,
                status_message_id=1,
                url="https://example.com/v",
                preset="h:720",
                title="Test",
                platform="YouTube",
            )
            _msg, kind = await queue._send_file(
                job, FSInputFile(path), 42, None, None, False
            )
            self.assertEqual(kind, "video")
            self.assertEqual(bot.video_calls, 2)
            self.assertEqual(bot.documents, 0)

    async def test_conversion_failure_falls_back_to_document(self) -> None:
        with tempfile.TemporaryDirectory(prefix="convert-") as raw:
            bot = _VideoBot()
            queue = await self._queue(raw, bot)
            path = Path(raw) / "video.mp4"
            path.write_bytes(b"original")

            async def no_convert(_path):
                return None

            queue._convert_video = no_convert  # type: ignore[method-assign]
            job = Job(
                id="v2",
                user_id=42,
                chat_id=42,
                status_message_id=1,
                url="https://example.com/v",
                preset="h:720",
                title="Test",
                platform="YouTube",
            )
            _msg, kind = await queue._send_file(
                job, FSInputFile(path), 42, None, None, False
            )
            self.assertEqual(kind, "document_fallback")
            self.assertEqual(bot.documents, 1)


class _VideoBot(_FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.video_calls = 0
        self.documents = 0

    async def send_video(self, **kwargs):
        self.video_calls += 1
        if self.video_calls == 1:
            raise TelegramBadRequest(
                method=EditMessageText(chat_id=1, message_id=1, text="x"),
                message="Bad Request: wrong file identifier/HTTP URL specified",
            )
        return SimpleNamespace(
            message_id=1, video=SimpleNamespace(file_id="vid_converted")
        )

    async def send_document(self, **kwargs):
        self.documents += 1
        return SimpleNamespace(
            message_id=1, document=SimpleNamespace(file_id="doc_fallback")
        )


class _GalleryBot(_FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.photos = 0
        self.cached_photos = 0
        self.media_groups: list[list] = []
        self.cached_media = 0

    async def send_photo(self, **kwargs):
        self.photos += 1
        photo = kwargs.get("photo")
        if isinstance(photo, str):
            self.cached_photos += 1
        return SimpleNamespace(
            message_id=1,
            photo=[SimpleNamespace(file_id="photo_file_id")],
        )

    async def send_media_group(self, **kwargs):
        media = list(kwargs.get("media") or [])
        self.media_groups.append(media)
        messages = []
        for index, item in enumerate(media, start=1):
            media_value = getattr(item, "media", None)
            if isinstance(media_value, str):
                self.cached_media += 1
                file_id = media_value
            else:
                file_id = f"album_photo_{index}"
            messages.append(
                SimpleNamespace(
                    message_id=index,
                    photo=[SimpleNamespace(file_id=file_id)],
                )
            )
        return messages


class _OnePhotoGallery:
    available = True

    def __init__(self, count: int = 1) -> None:
        self.calls = 0
        self.count = count

    def download(self, _url: str, outdir: Path) -> list[Path]:
        self.calls += 1
        files: list[Path] = []
        for index in range(1, self.count + 1):
            target = outdir / f"pin_{index}.jpg"
            target.write_bytes(b"jpeg")
            files.append(target)
        return files


class GalleryCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_gallery_photo_is_cached(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gcache-") as raw:
            db = Database(Path(raw) / "bot.db")
            await db.connect()
            settings = SimpleNamespace(
                concurrency=1,
                download_dir=Path(raw),
                max_queue_per_user=5,
                daily_limit=0,
                cache_days=30,
                max_upload_bytes=50 * 1024 * 1024,
                max_upload_mb=50,
                upload_timeout_sec=30,
                gallery_enabled=True,
                playlist_limit=5,
                playlist_max_total_sec=1800,
                monthly_traffic_gb=0,
                admin_ids=frozenset(),
            )
            bot = _GalleryBot()
            gallery = _OnePhotoGallery()
            queue = JobQueue(
                bot, db, settings, SimpleNamespace(), gallery
            )  # type: ignore[arg-type]
            url = "https://www.pinterest.com/pin/123/"
            try:
                first = Job(
                    id="g1",
                    user_id=42,
                    chat_id=42,
                    status_message_id=1,
                    url=url,
                    preset="gallery",
                    title="Pin",
                    platform="Pinterest",
                    kind="gallery",
                    source_url=url,
                )
                await queue._process_gallery(first)
                cached = await db.cache_get(url, "gallery", 30)
                self.assertIsNotNone(cached)
                assert cached is not None
                self.assertEqual(cached["media_type"], "photo")
                self.assertEqual(cached["file_id"], "photo_file_id")

                second = Job(
                    id="g2",
                    user_id=42,
                    chat_id=42,
                    status_message_id=2,
                    url=url,
                    preset="gallery",
                    title="Pin",
                    platform="Pinterest",
                    kind="gallery",
                    source_url=url,
                )
                await queue._process_gallery(second)
                self.assertEqual(gallery.calls, 1, "ikinci kez indirildi")
                self.assertEqual(bot.cached_photos, 1, "onbellekten gonderilmedi")
            finally:
                await db.close()

    async def test_multi_photo_album_is_cached(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gcache-") as raw:
            db = Database(Path(raw) / "bot.db")
            await db.connect()
            settings = SimpleNamespace(
                concurrency=1,
                download_dir=Path(raw),
                max_queue_per_user=5,
                daily_limit=0,
                cache_days=30,
                max_upload_bytes=50 * 1024 * 1024,
                max_upload_mb=50,
                upload_timeout_sec=30,
                gallery_enabled=True,
                playlist_limit=5,
                playlist_max_total_sec=1800,
                monthly_traffic_gb=0,
                admin_ids=frozenset(),
            )
            bot = _GalleryBot()
            gallery = _OnePhotoGallery(count=2)
            queue = JobQueue(
                bot, db, settings, SimpleNamespace(), gallery
            )  # type: ignore[arg-type]
            url = "https://www.instagram.com/p/album/"
            try:
                first = Job(
                    id="a1",
                    user_id=42,
                    chat_id=42,
                    status_message_id=1,
                    url=url,
                    preset="gallery",
                    title="Album",
                    platform="Instagram",
                    kind="gallery",
                    source_url=url,
                )
                await queue._process_gallery(first)
                cached = await db.cache_get(url, "gallery", 30)
                self.assertIsNotNone(cached)
                assert cached is not None
                self.assertEqual(cached["media_type"], "album")
                import json

                items = json.loads(cached["items"])
                self.assertEqual(len(items), 2)

                second = Job(
                    id="a2",
                    user_id=42,
                    chat_id=42,
                    status_message_id=2,
                    url=url,
                    preset="gallery",
                    title="Album",
                    platform="Instagram",
                    kind="gallery",
                    source_url=url,
                )
                await queue._process_gallery(second)
                self.assertEqual(gallery.calls, 1, "album ikinci kez indirildi")
                self.assertEqual(bot.cached_media, 2, "album onbellekten gelmedi")
            finally:
                await db.close()


class MonthlyQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def _setup(self, raw: str, used_mb: int):
        db = Database(Path(raw) / "bot.db")
        await db.connect()
        # Toplam trafik = upload + download.
        await db.record_download(
            42,
            "https://example.com/quota",
            "YouTube",
            "h:720",
            "ok",
            used_mb * 1024 * 1024 // 2,
            used_mb * 1024 * 1024 // 2,
        )
        settings = SimpleNamespace(
            concurrency=1,
            download_dir=Path(raw),
            max_queue_per_user=5,
            daily_limit=0,
            cache_days=0,
            max_upload_bytes=50 * 1024 * 1024,
            max_upload_mb=50,
            upload_timeout_sec=30,
            gallery_enabled=False,
            playlist_limit=5,
            playlist_max_total_sec=1800,
            monthly_traffic_gb=1,
            admin_ids=frozenset({42}),
        )
        bot = _FakeBot()
        queue = JobQueue(bot, db, settings, SimpleNamespace())  # type: ignore[arg-type]
        return db, bot, queue

    async def test_submit_is_blocked_when_quota_is_full(self) -> None:
        with tempfile.TemporaryDirectory(prefix="quota-") as raw:
            db, _bot, queue = await self._setup(raw, used_mb=1200)
            try:
                job = Job(
                    id="q1",
                    user_id=42,
                    chat_id=42,
                    status_message_id=1,
                    url="https://example.com/q1",
                    preset="h:720",
                    title="Test",
                    platform="YouTube",
                )
                with self.assertRaises(MonthlyQuotaError):
                    await queue.submit(job)
            finally:
                await db.close()

    async def test_warning_is_sent_once_per_threshold(self) -> None:
        with tempfile.TemporaryDirectory(prefix="quota-") as raw:
            db, bot, queue = await self._setup(raw, used_mb=1200)
            try:
                await queue._warn_quota()
                await queue._warn_quota()
                self.assertEqual(len(bot.messages), 2)
                self.assertTrue(any("%80" in m for m in bot.messages))
                self.assertTrue(any("doldu" in m for m in bot.messages))
            finally:
                await db.close()

    async def test_no_warning_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory(prefix="quota-") as raw:
            db, bot, queue = await self._setup(raw, used_mb=500)
            try:
                await queue._warn_quota()
                self.assertEqual(bot.messages, [])
            finally:
                await db.close()

    async def test_limit_error_reports_quota(self) -> None:
        from bot.handlers.download import _limit_error

        with tempfile.TemporaryDirectory(prefix="quota-") as raw:
            db, _bot, _queue = await self._setup(raw, used_mb=1200)
            try:
                settings = SimpleNamespace(
                    max_queue_per_user=5,
                    daily_limit=0,
                    monthly_traffic_gb=1,
                )
                queue = SimpleNamespace(pending_for=lambda _uid: 0)
                error = await _limit_error(
                    42, db, settings, queue  # type: ignore[arg-type]
                )
                assert error is not None
                self.assertIn("kota", error.lower())
            finally:
                await db.close()


if __name__ == "__main__":
    unittest.main()
