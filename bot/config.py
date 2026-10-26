"""Ortam degiskenlerinden okunan yapilandirma."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    download_dir: Path
    db_path: Path
    cookies_file: Path | None
    proxy: str | None
    api_url: str | None
    concurrency: int
    max_upload_mb: int
    upload_timeout_sec: int
    max_height: int
    max_duration_sec: int
    max_queue_per_user: int
    daily_limit: int
    cooldown_sec: int
    quality_instagram: int
    quality_tiktok: int
    quality_youtube: int
    quality_default: int
    cache_days: int
    gallery_enabled: bool
    gallery_limit: int
    playlist_limit: int
    playlist_max_total_sec: int
    monthly_traffic_gb: int
    log_level: str

    @classmethod
    def load(cls) -> "Settings":
        token = _env_str("BOT_TOKEN")
        if not token or ":" not in token:
            raise SystemExit(
                "BOT_TOKEN tanimli degil. `.env.example` dosyasini `.env` olarak "
                "kopyalayip token'i girin."
            )

        raw_admins = _env_str("ADMIN_IDS").replace(";", ",")
        admins = frozenset(
            int(part) for part in (p.strip() for p in raw_admins.split(",")) if part.isdigit()
        )

        cookies = _env_str("COOKIES_FILE")

        return cls(
            bot_token=token,
            admin_ids=admins,
            download_dir=Path(_env_str("DOWNLOAD_DIR", str(BASE_DIR / "data" / "downloads"))),
            db_path=Path(_env_str("DB_PATH", str(BASE_DIR / "data" / "bot.db"))),
            cookies_file=Path(cookies) if cookies else None,
            proxy=_env_str("PROXY_URL") or None,
            api_url=_env_str("TELEGRAM_API_URL") or None,
            concurrency=max(1, _env_int("CONCURRENCY", 2)),
            max_upload_mb=max(1, _env_int("MAX_UPLOAD_MB", 50)),
            upload_timeout_sec=max(30, _env_int("UPLOAD_TIMEOUT_SEC", 600)),
            max_height=max(0, _env_int("MAX_HEIGHT", 0)),
            max_duration_sec=max(0, _env_int("MAX_DURATION_SEC", 1800)),
            max_queue_per_user=max(1, _env_int("MAX_QUEUE_PER_USER", 3)),
            daily_limit=max(0, _env_int("DAILY_LIMIT", 20)),
            cooldown_sec=max(0, _env_int("COOLDOWN_SEC", 10)),
            quality_instagram=max(144, _env_int("QUALITY_INSTAGRAM", 1280)),
            quality_tiktok=max(144, _env_int("QUALITY_TIKTOK", 1012)),
            quality_youtube=max(144, _env_int("QUALITY_YOUTUBE", 720)),
            quality_default=max(144, _env_int("QUALITY_DEFAULT", 720)),
            cache_days=max(0, _env_int("CACHE_DAYS", 30)),
            gallery_enabled=_env_bool("GALLERY_ENABLED", True),
            gallery_limit=max(1, min(10, _env_int("GALLERY_LIMIT", 10))),
            playlist_limit=max(1, min(20, _env_int("PLAYLIST_LIMIT", 5))),
            playlist_max_total_sec=max(
                0, _env_int("PLAYLIST_MAX_TOTAL_SEC", 1800)
            ),
            monthly_traffic_gb=max(0, _env_int("MONTHLY_TRAFFIC_GB", 0)),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def cover_dir(self) -> Path:
        """Kapak fotoGraflarinin kalici olarak saklandigi klasor."""
        return self.download_dir / "covers"

    def default_height_for(self, platform: str) -> int:
        """Kalite belirtilmemis linklerde kullanilacak varsayilan yukseklik."""
        name = (platform or "").lower()
        if "instagram" in name:
            return self.quality_instagram
        if "tiktok" in name:
            return self.quality_tiktok
        if "youtube" in name:
            return self.quality_youtube
        return self.quality_default
