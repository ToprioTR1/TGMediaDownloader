import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.qol import (
    CATEGORY_ACCESS,
    CATEGORY_SIZE,
    audio_label,
    error_category,
    error_hint,
    media_buttons,
    retry_keyboard,
    video_codec_label,
    video_label,
)
from bot.queue import Job, JobQueue


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
    async def _queue(self, raw: str) -> JobQueue:
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
        return queue

    async def test_queue_position(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qol-") as raw:
            queue = await self._queue(raw)

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
            self.assertEqual(queue.queue_snapshot()["queued"], 2)
            text = queue._queue_text(second, "⏳ Sırada…")
            self.assertIn("Sıra", text)
            self.assertIn("2", text)

    async def test_finished_job_has_no_position(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qol-") as raw:
            queue = await self._queue(raw)
            job = Job(
                id="a",
                user_id=42,
                chat_id=42,
                status_message_id=1,
                url="https://example.com/a",
                preset="h:720",
                title="Test",
                platform="YouTube",
                status_stage="download",
            )
            self.assertIsNone(queue.queue_position(job))


if __name__ == "__main__":
    unittest.main()
