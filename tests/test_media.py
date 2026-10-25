import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bot.media import inspect_video


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "ffmpeg/ffprobe kurulu degil",
)
class VideoMetaTests(unittest.TestCase):
    def test_dimensions_duration_and_thumbnail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="video-meta-") as raw:
            root = Path(raw)
            video = root / "sample.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x240:rate=10",
                    "-t",
                    "1",
                    "-pix_fmt",
                    "yuv420p",
                    str(video),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            meta = inspect_video(video, root)
            self.assertIsNotNone(meta)
            assert meta is not None
            self.assertEqual((meta.width, meta.height), (320, 240))
            self.assertEqual(meta.duration, 1)
            self.assertIsNotNone(meta.thumbnail)
            assert meta.thumbnail is not None
            self.assertTrue(meta.thumbnail.is_file())
            self.assertLessEqual(meta.thumbnail.stat().st_size, 200 * 1024)

            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0",
                    str(meta.thumbnail),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            width, height = (int(part) for part in probe.stdout.strip().split(","))
            self.assertLessEqual(width, 320)
            self.assertLessEqual(height, 320)

    def test_missing_or_invalid_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory(prefix="video-meta-bad-") as raw:
            root = Path(raw)
            broken = root / "broken.mp4"
            broken.write_bytes(b"not a video")
            self.assertIsNone(inspect_video(broken, root))
            self.assertIsNone(inspect_video(root / "missing.mp4", root))


if __name__ == "__main__":
    unittest.main()
