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


class CaptionDefaultTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_user_caption_is_off(self) -> None:
        with tempfile.TemporaryDirectory(prefix="caption-") as raw:
            db = Database(Path(raw) / "bot.db")
            await db.connect()
            try:
                user = await db.touch_user(42, "user", "User")
                self.assertEqual(int(user["show_caption"]), 0)
            finally:
                await db.close()

    async def test_existing_users_are_disabled_once(self) -> None:
        with tempfile.TemporaryDirectory(prefix="caption-") as raw:
            path = Path(raw) / "bot.db"
            db = Database(path)
            await db.connect()
            await db.touch_user(42, "user", "User")
            await db.update_user_settings(42, show_caption=True)
            # Ilk acilista yazilan isareti sil ki migrasyon yeniden calissin.
            await db.conn.execute(
                "DELETE FROM app_state WHERE key = 'caption_default_off_v1'"
            )
            await db.conn.commit()
            await db.close()

            db = Database(path)
            await db.connect()
            try:
                user = await db.get_user(42) or {}
                self.assertEqual(int(user["show_caption"]), 0)
            finally:
                await db.close()


class BandwidthTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_splits_upload_and_download(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bw-") as raw:
            db = Database(Path(raw) / "bot.db")
            await db.connect()
            try:
                await db.record_download(
                    42, "u1", "YouTube", "h:720", "ok", 1000, 1500
                )
                # Onbellekten gonderim: indirme yok.
                await db.record_download(
                    42, "u2", "TikTok", "h:720", "ok", 500, 0
                )
                # Basarisiz is: islem sayisina girer, boyuta girmez.
                await db.record_download(
                    42, "u3", "YouTube", "h:720", "error", 0, 0
                )

                report = await db.bandwidth_report(days=31)
                self.assertEqual(report["bytes_out"], 1500)
                self.assertEqual(report["bytes_in"], 1500)
                self.assertEqual(report["jobs"], 3)
                self.assertEqual(report["ok_jobs"], 2)
                self.assertEqual(len(report["daily"]), 1)
                day = report["daily"][0]
                self.assertEqual(day["bytes_out"], 1500)
                self.assertEqual(day["bytes_in"], 1500)
            finally:
                await db.close()

    async def test_empty_report_is_zero(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bw-") as raw:
            db = Database(Path(raw) / "bot.db")
            await db.connect()
            try:
                report = await db.bandwidth_report()
                self.assertEqual(report["bytes_out"], 0)
                self.assertEqual(report["bytes_in"], 0)
                self.assertEqual(report["daily"], [])
            finally:
                await db.close()


if __name__ == "__main__":
    unittest.main()
