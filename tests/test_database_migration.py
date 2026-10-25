import tempfile
import unittest
from pathlib import Path

from bot.database import Database


class CacheMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_video_cache_entries_are_removed_on_reconnect(self) -> None:
        with tempfile.TemporaryDirectory(prefix="db-migration-") as raw:
            path = Path(raw) / "bot.db"
            db = Database(path)
            await db.connect()
            await db.cache_put(
                "old-video",
                "https://example.com/old",
                "h:720",
                "old_file_id",
                "video",
                "Old",
                "Test",
                100,
            )
            await db.cache_put(
                "new-video",
                "https://example.com/new",
                "h:720",
                "new_file_id",
                "video",
                "New",
                "Test",
                100,
                1280,
                720,
                10,
            )
            await db.cache_put(
                "document",
                "https://example.com/doc",
                "document",
                "doc_file_id",
                "document",
                "Doc",
                "Test",
                100,
            )
            await db.close()

            db = Database(path)
            await db.connect()
            try:
                self.assertIsNone(await db.cache_get_by_key("old-video"))
                self.assertIsNotNone(await db.cache_get_by_key("new-video"))
                self.assertIsNotNone(await db.cache_get_by_key("document"))
            finally:
                await db.close()


if __name__ == "__main__":
    unittest.main()
