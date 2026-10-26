import unittest

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


if __name__ == "__main__":
    unittest.main()
