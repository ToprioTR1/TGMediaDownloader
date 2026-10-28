"""gallery-dl sarmalayicisi: Instagram, TikTok, Pinterest ve X medya icin.

yt-dlp video odaklidir; fotoğraf, carousel veya Pinterest pinlerinde
gallery-dl kullanilir. Alt surec olarak calisir, bu yuzden
`asyncio.to_thread` ile cagrilmalidir.
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

from . import netguard
from .config import Settings
from .instagram_embed import (
    InstagramEmbedError,
    download_items as download_instagram_embed,
    fetch_items as fetch_instagram_embed,
    instagram_shortcode,
)
from .utils import is_instagram_url

log = logging.getLogger(__name__)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
SUPPORTED_EXT = IMAGE_EXT | VIDEO_EXT


class GalleryError(Exception):
    """Kullaniciya gosterilebilir gallery-dl hatasi."""


def gallery_installed() -> bool:
    return (
        importlib.util.find_spec("gallery_dl") is not None
        or shutil.which("gallery-dl") is not None
    )


class GalleryDownloader:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # gallery-dl ayrı bir Python süreci çalıştırır; koruma alt sürece
        # sitecustomize ile taşınır. Ana süreçteki kanca da kurulu olsun.
        netguard.ensure_for_settings(settings)
        self.available = settings.gallery_enabled and gallery_installed()
        if settings.gallery_enabled and not self.available:
            log.warning("gallery-dl kurulu degil; fotograf/carousel destegi kapali.")

    # ------------------------------------------------------------------ ortak

    def _base_cmd(self) -> list[str]:
        # Once ayni Python ortamindaki modulu kullan. Yalnizca baska bir Python
        # ortamina kurulmus gallery-dl calistirilabilirse onu cagir.
        if importlib.util.find_spec("gallery_dl") is not None:
            return [sys.executable, "-m", "gallery_dl"]
        executable = shutil.which("gallery-dl")
        if executable:
            return [executable]
        return [sys.executable, "-m", "gallery_dl"]

    def _common_args(self, url: str | None = None) -> list[str]:
        # Kullanıcı/ortam yapılandırmasının harici indirici veya --exec gibi
        # ağ kancasını atlayan seçenekler eklemesini engelle.
        args: list[str] = ["--config-ignore"]
        # Cerezler yalnizca Instagram icin kullanilir; diger platformlarda
        # kullanici hesabinin cerezleri istenmeyen yan etkiler yaratabilir.
        if url and is_instagram_url(url):
            cookies = self._settings.cookies_file
            if cookies and cookies.exists():
                args += ["--cookies", str(cookies)]
        if netguard.proxy_allowed():
            if self._settings.proxy:
                args += ["--proxy", self._settings.proxy]
        else:
            # Alt süreç ortamındaki proxy değişkenleri ayrıca temizlenir;
            # bu seçenek gallery-dl'nin kendi proxy-env davranışını kapatır.
            args += ["-o", "proxy-env=false"]
        return args

    @property
    def has_cookies(self) -> bool:
        cookies = self._settings.cookies_file
        return bool(cookies and cookies.exists())

    def probe_embed(self, url: str) -> list[dict[str, Any]]:
        """Instagram embed sayfasindan medya listesi (hizli yol)."""
        if not instagram_shortcode(url):
            return []
        try:
            return fetch_instagram_embed(url, self._settings.gallery_limit)
        except InstagramEmbedError as exc:
            log.info("Instagram embed kesif basarisiz (%s): %s", url, exc)
            return []

    def _run(self, cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=netguard.child_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GalleryError("Medya listesi alınamadı (zaman aşımı).") from exc

    # ------------------------------------------------------------------ kesif

    def probe(self, url: str) -> list[dict[str, Any]]:
        """Gönderideki medya sayisini ve meta verilerini dondurur.

        TikTok kisa linkleri (`vt.tiktok.com` gibi) `--simulate` ciktisinda
        yalnizca kuyruk URL'si (tip 6) dondurur. Bu URL'yi bir kez daha
        cozerek gercek medya listesine ulasiriz.

        Instagram giris duvarina takilirsa herkese acik gonderiler icin
        embed sayfasindaki fotoğraf URL'lerine dusulur.
        """
        try:
            items = self._probe(url, set(), 0)
        except GalleryError as exc:
            # Zaman asimi / alt surec hatasi embed yedegini engellemesin.
            log.info("gallery-dl kesif basarisiz (%s): %s", url, exc)
            items = []
        if items:
            return items
        if instagram_shortcode(url):
            try:
                return fetch_instagram_embed(url, self._settings.gallery_limit)
            except InstagramEmbedError as exc:
                log.info("Instagram embed kesif basarisiz (%s): %s", url, exc)
        return []

    def _probe(
        self, url: str, seen: set[str], depth: int
    ) -> list[dict[str, Any]]:
        if not self.available or depth > 2 or url in seen:
            return []
        seen.add(url)
        cmd = self._base_cmd() + [
            "--simulate",
            "--dump-json",
            "--range",
            f"1-{self._settings.gallery_limit}",
            *self._common_args(url),
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

        items = _items_from_json(data, self._settings.gallery_limit)
        if items:
            return items
        for queued_url in _queued_urls(data, self._settings.gallery_limit):
            items = self._probe(queued_url, seen, depth + 1)
            if items:
                return items
        return []

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
            *self._common_args(url),
            url,
        ]
        try:
            proc = self._run(cmd, timeout=300)
        except GalleryError as exc:
            # Zaman asimi dogrudan hata dondurmek yerine embed yedegine izin ver.
            log.info("gallery-dl indirme basarisiz (%s): %s", url, exc)
            proc = None
        files = sorted(
            (
                path
                for path in outdir.rglob("*")
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXT
            ),
            key=lambda path: path.name,
        )
        if proc is not None and proc.returncode == 0 and files:
            return files[: self._settings.gallery_limit]

        # gallery-dl login duvarina takildiysa herkese acik Instagram
        # gonderilerinde embed sayfasindan fotoğrafları indir.
        if instagram_shortcode(url):
            try:
                embed_files = download_instagram_embed(
                    url, outdir, self._settings.gallery_limit
                )
            except InstagramEmbedError as exc:
                log.info("Instagram embed indirme basarisiz (%s): %s", url, exc)
            else:
                if embed_files:
                    return embed_files[: self._settings.gallery_limit]

        if proc is None:
            raise GalleryError("Gönderi indirilemedi (zaman aşımı).")
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            hint = detail[-1][:200] if detail else "bilinmeyen hata"
            if "login" in hint.lower() or "cookie" in hint.lower():
                raise GalleryError(
                    "Bu gönderi için giriş gerekiyor. Instagram ise bot sahibinin "
                    "cookies.txt eklemesi lazım."
                )
            raise GalleryError(f"Gönderi indirilemedi: {hint}")
        raise GalleryError("Gönderi indirilemedi: medya bulunamadı")


def _queued_urls(data: Any, limit: int) -> list[str]:
    """gallery-dl `--dump-json` ciktisindaki kuyruk (tip 6) URL'lerini alir."""
    if not isinstance(data, list):
        return []
    urls: list[str] = []
    for entry in data:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        if entry[0] != 6:  # Message.Queue
            continue
        url = entry[1]
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            urls.append(url)
            if len(urls) >= limit:
                break
    return urls


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
