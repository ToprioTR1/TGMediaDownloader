import unittest

from bot.qol import (
    audio_label,
    media_buttons,
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


if __name__ == "__main__":
    unittest.main()
