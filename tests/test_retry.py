import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.downloader import DownloadError, MediaInfo
from bot.handlers.download import _probe_downloader, _probe_gallery
from bot.queue import Job, JobQueue
from bot.utils import UNSUPPORTED_URL_TEXT


def _info() -> MediaInfo:
    return MediaInfo(
        url="https://example.com/v",
        title="Test",
        uploader=None,
        duration=10,
        thumbnail=None,
        heights=[720],
        has_audio=True,
        platform="Test",
        webpage_url="https://example.com/v",
    )


class FakeDownloader:
    def __init__(self, errors: list[Exception | None]) -> None:
        self.errors = errors
        self.calls = 0

    def probe(self, url: str) -> MediaInfo:
        self.calls += 1
        error = self.errors[self.calls - 1]
        if error is not None:
            raise error
        return _info()


class FakeGallery:
    available = True

    def __init__(self, errors: list[Exception | None]) -> None:
        self.errors = errors
        self.calls = 0

    def probe(self, url: str) -> list[dict]:
        self.calls += 1
        error = self.errors[self.calls - 1]
        if error is not None:
            raise error
        return [{"url": "https://example.com/1.jpg", "extension": "jpg"}]


class FlakyDownloader:
    def __init__(self) -> None:
        self.calls = 0
        self.dirs: list[Path] = []

    def download(
        self, url: str, preset: str, outdir: Path, state: dict, **kwargs
    ) -> Path:
        self.calls += 1
        self.dirs.append(outdir)
        output = outdir / "video.mp4"
        output.write_bytes(b"video")
        if self.calls == 1:
            raise DownloadError("gecici indirme hatasi")
        return output


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_retries_once_then_succeeds(self) -> None:
        downloader = FakeDownloader([DownloadError("gecici hata"), None])
        notices = 0

        async def on_retry() -> None:
            nonlocal notices
            notices += 1

        info = await _probe_downloader(
            downloader, "https://example.com/v", on_retry=on_retry
        )
        self.assertEqual(info.title, "Test")
        self.assertEqual(downloader.calls, 2)
        self.assertEqual(notices, 1)

    async def test_probe_raises_after_second_failure(self) -> None:
        downloader = FakeDownloader(
            [DownloadError("birinci"), DownloadError("ikinci")]
        )
        with self.assertRaisesRegex(DownloadError, "ikinci"):
            await _probe_downloader(downloader, "https://example.com/v")
        self.assertEqual(downloader.calls, 2)

    async def test_unsupported_url_is_not_retried(self) -> None:
        downloader = FakeDownloader([DownloadError(UNSUPPORTED_URL_TEXT)])
        with self.assertRaises(DownloadError):
            await _probe_downloader(downloader, "https://example.com/v")
        self.assertEqual(downloader.calls, 1)

    async def test_gallery_probe_retries_once_then_succeeds(self) -> None:
        gallery = FakeGallery([RuntimeError("gecici"), None])
        items = await _probe_gallery(gallery, "https://example.com/p/1")  # type: ignore[arg-type]
        self.assertIsNotNone(items)
        assert items is not None
        self.assertEqual(len(items), 1)
        self.assertEqual(gallery.calls, 2)

    async def test_gallery_probe_returns_none_after_second_failure(self) -> None:
        gallery = FakeGallery([RuntimeError("birinci"), RuntimeError("ikinci")])
        items = await _probe_gallery(gallery, "https://example.com/p/1")  # type: ignore[arg-type]
        self.assertIsNone(items)
        self.assertEqual(gallery.calls, 2)

    async def test_download_retries_once_in_a_clean_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="retry-download-") as raw:
            settings = SimpleNamespace(
                download_dir=Path(raw),
                max_upload_bytes=10 * 1024 * 1024,
                max_upload_mb=10,
                cache_days=0,
                upload_timeout_sec=60,
            )
            downloader = FlakyDownloader()
            queue = JobQueue(None, None, settings, downloader, None)  # type: ignore[arg-type]
            queue._set_stage = AsyncMock()  # type: ignore[method-assign]
            queue._send = AsyncMock(return_value=("file-id", "video"))  # type: ignore[method-assign]
            queue._finish_success = AsyncMock()  # type: ignore[method-assign]
            job = Job(
                id="retry1",
                user_id=1,
                chat_id=1,
                status_message_id=1,
                url="https://example.com/v",
                preset="h:720",
                title="Test",
                platform="Test",
                status_header="Test",
            )

            await queue._download_and_send(job)

            self.assertEqual(downloader.calls, 2)
            self.assertNotEqual(downloader.dirs[0], downloader.dirs[1])
            self.assertEqual(downloader.dirs[0].name, "try-1")
            self.assertEqual(downloader.dirs[1].name, "try-2")
            self.assertEqual(queue._send.await_count, 1)


if __name__ == "__main__":
    unittest.main()
