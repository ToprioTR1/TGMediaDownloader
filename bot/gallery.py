"""gallery-dl sarmalayicisi: Instagram fotoğraf / carousel gönderileri icin.

yt-dlp video odaklidir; fotoğraf veya karışık carousel gönderilerinde
gallery-dl kullanilir. Alt surec olarak calisir, bu yuzden `asyncio.to_thread`
ile cagrilmalidir.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
SUPPORTED_EXT = IMAGE_EXT | VIDEO_EXT


class GalleryError(Exception):
    """Kullaniciya gosterilebilir gallery-dl hatasi."""


def gallery_installed() -> bool:
    return shutil.which("gallery-dl") is not None or importlib.util.find_spec("gallery_dl") is not None


class GalleryDownloader:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.available = settings.gallery_enabled and gallery_installed()
        if settings.gallery_enabled and not self.available:
            log.warning("gallery-dl kurulu degil; fotograf/carousel destegi kapali.")

    # ------------------------------------------------------------------ ortak

    def _base_cmd(self) -> list[str]:
        return [sys.executable, "-m", "gallery_dl"]

    def _common_args(self) -> list[str]:
        args: list[str] = []
        cookies = self._settings.cookies_file
        if cookies and cookies.exists():
            args += ["--cookies", str(cookies)]
        if self._settings.proxy:
            args += ["--proxy", self._settings.proxy]
        return args

    def _run(self, cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise GalleryError("Medya listesi alınamadı (zaman aşımı).") from exc

    # ------------------------------------------------------------------ kesif

    def probe(self, url: str) -> list[dict[str, Any]]:
        """Gönderideki medya sayisini ve meta verilerini dondurur."""
        if not self.available:
            return []
        cmd = self._base_cmd() + [
            "--simulate",
            "--dump-json",
            "--range",
            f"1-{self._settings.gallery_limit}",
            *self._common_args(),
            url,
        ]
        proc = self._run(cmd, timeout=60)
        if proc.returncode != 0 or not proc.stdout.strip():
            log.info("gallery-dl kesif basarisiz (%s): %s", url, proc.stderr[-300:])
            return []
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            log.warning("gallery-dl JSON cozulemedi: %s", url)
            return []

        return _items_from_json(data, self._settings.gallery_limit)

    # ---------------------------------------------------------------- indirme

    def download(self, url: str, outdir: Path) -> list[Path]:
        if not self.available:
            raise GalleryError("Fotoğraf indirme özelliği bu sunucuda kurulu değil.")
        outdir.mkdir(parents=True, exist_ok=True)
        cmd = self._base_cmd() + [
            "--quiet",
            "--dest",
            str(outdir),
            "--range",
            f"1-{self._settings.gallery_limit}",
            *self._common_args(),
            url,
        ]
        proc = self._run(cmd, timeout=300)
        files = sorted(
            (
                path
                for path in outdir.rglob("*")
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXT
            ),
            key=lambda path: path.name,
        )
        if not files:
            detail = (proc.stderr or "").strip().splitlines()
            hint = detail[-1][:200] if detail else "medya bulunamadı"
            if "login" in hint.lower() or "cookie" in hint.lower():
                raise GalleryError(
                    "Bu gönderi için giriş gerekiyor. Bot sahibinin cookies.txt eklemesi lazım."
                )
            raise GalleryError(f"Gönderi indirilemedi: {hint}")
        return files[: self._settings.gallery_limit]


def _items_from_json(data: Any, limit: int) -> list[dict[str, Any]]:
    """gallery-dl `--dump-json` ciktisini medya listesine cevirir.

    Ciktinin her ogesi `(tip, url, metadata)` uclusudur; tip 3 = dosya URL'si
    (gallery_dl.job.Message.Url). Hata ogeleri tip -1 ile gelir.
    """
    if not isinstance(data, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        if entry[0] != 3:  # Message.Url
            continue
        file_url = entry[1]
        meta = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
        item = {"url": file_url, **meta}
        if _looks_like_media(item):
            items.append(item)
        if len(items) >= limit:
            break
    return items


def _looks_like_media(item: dict[str, Any]) -> bool:
    if not isinstance(item, dict) or not item.get("url"):
        return False
    extension = str(
        item.get("extension")
        or Path(str(item["url"]).split("?")[0]).suffix.lstrip(".")
    ).lower()
    return f".{extension}" in SUPPORTED_EXT
