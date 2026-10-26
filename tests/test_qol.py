import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.downloader import Downloader, MediaInfo
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
    retry_keyboard,
    video_codec_label,
    video_label,
)
from bot.queue import Job, JobQueue


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
            in_group=False,
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
            in_group=False,
        )
        self.assertIsNone(keyboard)

    def test_group_chat_only_keeps_link_button(self) -> None:
        keyboard = media_buttons(
            source_url="https://example.com/v",
            cache_key="k",
            can_audio=True,
            can_document=True,
            can_thumbnail=True,
            can_quality=True,
            in_group=True,
        )
        assert keyboard is not None
        buttons = _buttons(keyboard)
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].url, "https://example.com/v")

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

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def delete_message(self, **kwargs):
        return True

    async def send_message(self, **kwargs):
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


if __name__ == "__main__":
    unittest.main()
