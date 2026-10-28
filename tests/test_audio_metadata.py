import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot.downloader import Downloader


class _FakeYDL:
    instances: list["_FakeYDL"] = []

    def __init__(self, opts: dict) -> None:
        self.opts = opts
        self.outdir: Path | None = None
        _FakeYDL.instances.append(self)

    def __enter__(self) -> "_FakeYDL":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def extract_info(self, url: str, download: bool = True):
        assert self.outdir is not None
        # Kapak dosyasi MP3'ten buyuk olsa bile medya olarak secilmemeli.
        (self.outdir / "song.mp3").write_bytes(b"a" * 1000)
        (self.outdir / "cover.jpg").write_bytes(b"b" * 5000)
        return {"id": "x"}


class AudioMetadataTests(unittest.TestCase):
    def _download(self, outdir: Path, preset: str = "audio") -> Path:
        downloader = object.__new__(Downloader)
        downloader._settings = SimpleNamespace(  # type: ignore[attr-defined]
            cookies_file=None, proxy=None
        )
        fake = _FakeYDL

        def factory(opts):
            instance = fake(opts)
            instance.outdir = outdir
            return instance

        with patch("bot.downloader.yt_dlp.YoutubeDL", side_effect=factory):
            return downloader.download(
                "https://www.youtube.com/watch?v=x", preset, outdir, {}
            )

    def test_audio_embeds_metadata_and_cover(self) -> None:
        _FakeYDL.instances.clear()
        with tempfile.TemporaryDirectory(prefix="audio-meta-") as raw:
            outdir = Path(raw)
            result = self._download(outdir)
            self.assertEqual(result.name, "song.mp3")
            opts = _FakeYDL.instances[-1].opts
            self.assertTrue(opts.get("writethumbnail"))
            keys = [pp.get("key") for pp in opts.get("postprocessors", [])]
            self.assertIn("FFmpegExtractAudio", keys)
            self.assertIn("FFmpegMetadata", keys)
            self.assertIn("EmbedThumbnail", keys)

    def test_cover_file_is_not_picked_as_media(self) -> None:
        _FakeYDL.instances.clear()
        with tempfile.TemporaryDirectory(prefix="audio-meta-") as raw:
            result = self._download(Path(raw))
            self.assertEqual(result.suffix.lower(), ".mp3")


if __name__ == "__main__":
    unittest.main()
