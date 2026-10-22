"""SQLite tabanli kullanici / indirme kayitlari."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now')),
    downloads   INTEGER NOT NULL DEFAULT 0,
    bytes_sent  INTEGER NOT NULL DEFAULT 0,
    is_banned   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS downloads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    url          TEXT NOT NULL,
    platform     TEXT,
    preset       TEXT,
    status       TEXT NOT NULL,
    filesize     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_downloads_created ON downloads(created_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_cache (
    cache_key   TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    preset      TEXT NOT NULL,
    file_id     TEXT NOT NULL,
    media_type  TEXT NOT NULL,
    title       TEXT,
    platform    TEXT,
    filesize    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_media_cache_url ON media_cache(url);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._settings: dict[str, str] = {}

    # ---------------------------------------------------------------- baglanti

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Veritabani baglantisi acilmadi.")
        return self._conn

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        await self._load_settings()
        log.info("Veritabani hazir: %s", self._path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _load_settings(self) -> None:
        async with self.conn.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
        self._settings = {row["key"]: row["value"] for row in rows}

    # ----------------------------------------------------------------- ayarlar

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        if key not in self._settings:
            async with self.conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return default
            self._settings[key] = row["value"]
        return self._settings[key]

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()
        self._settings[key] = value

    async def maintenance(self) -> bool:
        return (await self.get_setting("maintenance", "0")) == "1"

    async def set_maintenance(self, enabled: bool) -> None:
        await self.set_setting("maintenance", "1" if enabled else "0")

    # -------------------------------------------------------------- kullanicilar

    async def touch_user(
        self, user_id: int, username: str | None, first_name: str | None
    ) -> dict[str, Any]:
        await self.conn.execute(
            """
            INSERT INTO users(id, username, first_name)
            VALUES(?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name,
                last_seen  = datetime('now')
            """,
            (user_id, username, first_name),
        )
        await self.conn.commit()
        return await self.get_user(user_id) or {"id": user_id, "is_banned": 0}

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        async with self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def set_banned(self, user_id: int, banned: bool) -> bool:
        cur = await self.conn.execute(
            "UPDATE users SET is_banned = ? WHERE id = ?", (1 if banned else 0, user_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def all_user_ids(self) -> list[int]:
        async with self.conn.execute("SELECT id FROM users WHERE is_banned = 0") as cur:
            rows = await cur.fetchall()
        return [row["id"] for row in rows]

    async def recent_users(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    # ---------------------------------------------------------------- indirmeler

    async def record_download(
        self,
        user_id: int,
        url: str,
        platform: str | None,
        preset: str | None,
        status: str,
        filesize: int = 0,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO downloads(user_id, url, platform, preset, status, filesize) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (user_id, url, platform, preset, status, filesize),
        )
        if status == "ok":
            await self.conn.execute(
                "UPDATE users SET downloads = downloads + 1, bytes_sent = bytes_sent + ? "
                "WHERE id = ?",
                (filesize, user_id),
            )
        await self.conn.commit()

    async def count_downloads_today(self, user_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM downloads "
            "WHERE user_id = ? AND status = 'ok' AND created_at >= datetime('now', '-1 day')",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def stats(self) -> dict[str, Any]:
        async def scalar(query: str, params: tuple = ()) -> int:
            async with self.conn.execute(query, params) as cur:
                row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        return {
            "users": await scalar("SELECT COUNT(*) FROM users"),
            "users_today": await scalar(
                "SELECT COUNT(*) FROM users WHERE first_seen >= datetime('now', '-1 day')"
            ),
            "downloads_ok": await scalar("SELECT COUNT(*) FROM downloads WHERE status = 'ok'"),
            "downloads_today": await scalar(
                "SELECT COUNT(*) FROM downloads WHERE status = 'ok' "
                "AND created_at >= datetime('now', '-1 day')"
            ),
            "errors": await scalar("SELECT COUNT(*) FROM downloads WHERE status = 'error'"),
            "bytes": await scalar("SELECT COALESCE(SUM(filesize), 0) FROM downloads WHERE status = 'ok'"),
            "cached": await self.cache_count(),
            "top_platforms": await self._top_platforms(),
        }

    async def _top_platforms(self, limit: int = 5) -> list[tuple[str, int]]:
        async with self.conn.execute(
            "SELECT COALESCE(platform, '?') AS p, COUNT(*) AS c FROM downloads "
            "WHERE status = 'ok' GROUP BY p ORDER BY c DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [(row["p"], int(row["c"])) for row in rows]

    # --------------------------------------------------------------- onbellek

    async def cache_get(
        self, url: str, preset: str, days: int = 30
    ) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM media_cache WHERE url = ? AND preset = ? "
            "AND created_at >= datetime('now', ?)",
            (url, preset, f"-{days} day"),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def cache_get_by_key(self, cache_key: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM media_cache WHERE cache_key = ?", (cache_key,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def cache_put(
        self,
        cache_key: str,
        url: str,
        preset: str,
        file_id: str,
        media_type: str,
        title: str | None,
        platform: str | None,
        filesize: int,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO media_cache
                (cache_key, url, preset, file_id, media_type, title, platform, filesize)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                file_id    = excluded.file_id,
                title      = excluded.title,
                platform   = excluded.platform,
                filesize   = excluded.filesize,
                created_at = datetime('now')
            """,
            (cache_key, url, preset, file_id, media_type, title, platform, filesize),
        )
        await self.conn.commit()

    async def cache_delete(self, cache_key: str) -> None:
        await self.conn.execute("DELETE FROM media_cache WHERE cache_key = ?", (cache_key,))
        await self.conn.commit()

    async def cache_cleanup(self, days: int = 45) -> int:
        cur = await self.conn.execute(
            "DELETE FROM media_cache WHERE created_at < datetime('now', ?)",
            (f"-{days} day",),
        )
        await self.conn.commit()
        return cur.rowcount or 0

    async def cache_count(self) -> int:
        async with self.conn.execute("SELECT COUNT(*) FROM media_cache") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0
