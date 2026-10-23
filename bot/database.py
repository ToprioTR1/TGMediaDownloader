"""SQLite tabanli kullanici / indirme kayitlari."""

from __future__ import annotations

import asyncio
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
    downloads       INTEGER NOT NULL DEFAULT 0,
    bytes_sent      INTEGER NOT NULL DEFAULT 0,
    is_promoted     INTEGER NOT NULL DEFAULT 0,
    default_quality INTEGER NOT NULL DEFAULT 0,
    audio_default   INTEGER NOT NULL DEFAULT 0,
    show_caption    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS chats (
    id          INTEGER PRIMARY KEY,
    title       TEXT,
    chat_type   TEXT NOT NULL,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chats_type ON chats(chat_type);

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
        # Tek baglantida birden fazla yazma islemi ic ice girmesin. Aksi halde
        # bir coroutine'in commit'i digerinin yarim kalan islemini de commit eder.
        self._write_lock = asyncio.Lock()

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
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()
        log.info("Veritabani hazir: %s", self._path)

    async def _migrate(self) -> None:
        """Eski veritabanlarina yeni kolonlari ekler."""
        async with self.conn.execute("PRAGMA table_info(users)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}

        new_columns = {
            "is_promoted": "INTEGER NOT NULL DEFAULT 0",
            "default_quality": "INTEGER NOT NULL DEFAULT 0",
            "audio_default": "INTEGER NOT NULL DEFAULT 0",
            "show_caption": "INTEGER NOT NULL DEFAULT 1",
        }
        for name, definition in new_columns.items():
            if name not in columns:
                await self.conn.execute(
                    f"ALTER TABLE users ADD COLUMN {name} {definition}"
                )
                log.info("users.%s kolonu eklendi", name)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -------------------------------------------------------------- kullanicilar

    async def touch_user(
        self, user_id: int, username: str | None, first_name: str | None
    ) -> dict[str, Any]:
        async with self._write_lock:
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
        return await self.get_user(user_id) or {"id": user_id}

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        async with self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """Kullanici adini buyuk/kucuk harf duyarsiz arar (@ oneki olmadan)."""
        async with self.conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE LIMIT 1",
            (username,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def all_user_ids(self) -> list[int]:
        async with self.conn.execute("SELECT id FROM users") as cur:
            rows = await cur.fetchall()
        return [row["id"] for row in rows]

    async def all_group_ids(self) -> list[int]:
        async with self.conn.execute(
            "SELECT id FROM chats WHERE chat_type IN ('group', 'supergroup') "
            "ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
        return [row["id"] for row in rows]

    async def touch_chat(
        self, chat_id: int, title: str | None, chat_type: str
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO chats(id, title, chat_type)
                VALUES(?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title     = excluded.title,
                    chat_type = excluded.chat_type,
                    last_seen = datetime('now')
                """,
                (chat_id, title, chat_type),
            )
            await self.conn.commit()

    async def remove_chat(self, chat_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            await self.conn.commit()

    async def recent_users(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def set_promoted(self, user_id: int, promoted: bool) -> bool:
        async with self._write_lock:
            cur = await self.conn.execute(
                "UPDATE users SET is_promoted = ? WHERE id = ?",
                (1 if promoted else 0, user_id),
            )
            await self.conn.commit()
        return cur.rowcount > 0

    async def is_promoted(self, user_id: int) -> bool:
        async with self.conn.execute(
            "SELECT is_promoted FROM users WHERE id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return bool(row["is_promoted"]) if row else False

    async def update_user_settings(
        self,
        user_id: int,
        *,
        default_quality: int | None = None,
        audio_default: bool | None = None,
        show_caption: bool | None = None,
    ) -> bool:
        fields: list[str] = []
        params: list[Any] = []
        if default_quality is not None:
            fields.append("default_quality = ?")
            params.append(default_quality)
        if audio_default is not None:
            fields.append("audio_default = ?")
            params.append(1 if audio_default else 0)
        if show_caption is not None:
            fields.append("show_caption = ?")
            params.append(1 if show_caption else 0)
        if not fields:
            return False

        params.append(user_id)
        async with self._write_lock:
            cur = await self.conn.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE id = ?", params
            )
            await self.conn.commit()
        return cur.rowcount > 0

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
        async with self._write_lock:
            await self.conn.execute(
                "INSERT INTO downloads(user_id, url, platform, preset, status, filesize) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (user_id, url, platform, preset, status, filesize),
            )
            if status == "ok":
                await self.conn.execute(
                    "UPDATE users SET downloads = downloads + 1, "
                    "bytes_sent = bytes_sent + ? WHERE id = ?",
                    (filesize, user_id),
                )
            await self.conn.commit()

    async def count_downloads_today(self, user_id: int) -> int:
        # created_at UTC'dir. Yerel gun baslangicini UTC'ye cevirerek karsilastir;
        # boylece limit 24 saatlik kayan pencere yerine yerel gece yarisinda sifirlanir.
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM downloads "
            "WHERE user_id = ? AND status = 'ok' "
            "AND created_at >= datetime('now', 'localtime', 'start of day', 'utc')",
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
                "SELECT COUNT(*) FROM users WHERE first_seen >= "
                "datetime('now', 'localtime', 'start of day', 'utc')"
            ),
            "groups": await scalar(
                "SELECT COUNT(*) FROM chats WHERE chat_type IN ('group', 'supergroup')"
            ),
            "downloads_ok": await scalar("SELECT COUNT(*) FROM downloads WHERE status = 'ok'"),
            "downloads_today": await scalar(
                "SELECT COUNT(*) FROM downloads WHERE status = 'ok' "
                "AND created_at >= datetime('now', 'localtime', 'start of day', 'utc')"
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
        days = max(0, int(days))
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
        async with self._write_lock:
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
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM media_cache WHERE cache_key = ?", (cache_key,)
            )
            await self.conn.commit()

    async def cache_cleanup(self, days: int = 45) -> int:
        days = max(0, int(days))
        async with self._write_lock:
            cur = await self.conn.execute(
                "DELETE FROM media_cache WHERE created_at < datetime('now', ?)",
                (f"-{days} day",),
            )
            await self.conn.commit()
        return cur.rowcount or 0

    async def cache_clear(self) -> int:
        async with self._write_lock:
            cur = await self.conn.execute("DELETE FROM media_cache")
            await self.conn.commit()
        return cur.rowcount or 0

    async def cache_count(self) -> int:
        async with self.conn.execute("SELECT COUNT(*) FROM media_cache") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def cache_stats(self) -> dict[str, Any]:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(filesize), 0) AS b, "
            "MIN(created_at) AS oldest, MAX(created_at) AS newest FROM media_cache"
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return {"count": 0, "bytes": 0, "oldest": None, "newest": None}
        return {
            "count": int(row["c"] or 0),
            "bytes": int(row["b"] or 0),
            "oldest": row["oldest"],
            "newest": row["newest"],
        }
