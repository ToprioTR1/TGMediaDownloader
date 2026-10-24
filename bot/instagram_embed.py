"""Herkese acik Instagram fotoğraf / carousel gonderileri icin embed yedegi.

Instagram, giris yapilmamis bazi istekleri login sayfasina yonlendiriyor.
Bu durumda gallery-dl medya bulamiyor. Embed sayfasi ise herkese acik
gonderilerde medya URL'lerini iceriyor. Bu modul yalnizca dogrudan
instagram.com/p/... (ve reel/tv) adreslerini isler; indirme yine netguard
altindaki HTTP istemcisiyle yapilir.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0"
_MAX_HTML_BYTES = 4 * 1024 * 1024
_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_CONTEXT_RE = re.compile(r'"contextJSON":"((?:[^"\\]|\\.)*)"')
_SHORTCODE_RE = re.compile(r"^/(?:p|reel|tv)/([A-Za-z0-9_-]+)")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class InstagramEmbedError(Exception):
    """Embed sayfasindan medya alinamadi."""


def instagram_shortcode(url: str) -> str | None:
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not (
        host == "instagram.com"
        or host.endswith(".instagram.com")
        or host == "instagr.am"
        or host.endswith(".instagr.am")
    ):
        return None
    match = _SHORTCODE_RE.match(parsed.path)
    return match.group(1) if match else None


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
            "Referer": "https://www.instagram.com/",
        },
    )


def _fetch_html(url: str) -> str:
    try:
        with _OPENER.open(_request(url), timeout=25) as response:
            data = response.read(_MAX_HTML_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise InstagramEmbedError(f"Embed sayfasina erisilemedi: {exc}") from exc
    if len(data) > _MAX_HTML_BYTES:
        raise InstagramEmbedError("Embed sayfasi beklenenden buyuk.")
    return data.decode("utf-8", "replace")


def _caption(media: dict[str, Any]) -> str:
    try:
        edge = media["edge_media_to_caption"]["edges"][0]
        text = edge["node"]["text"]
    except (KeyError, IndexError, TypeError):
        return ""
    return text.strip() if isinstance(text, str) else ""


def _nodes(media: dict[str, Any]) -> list[dict[str, Any]]:
    sidecar = media.get("edge_sidecar_to_children")
    if isinstance(sidecar, dict):
        edges = sidecar.get("edges")
        if isinstance(edges, list):
            nodes = [
                edge.get("node")
                for edge in edges
                if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
            ]
            if nodes:
                return nodes
    return [media]


def parse_embed_html(html: str, limit: int) -> list[dict[str, Any]]:
    """Embed HTML'indeki contextJSON'dan fotoğraf URL'lerini cikarir."""
    if limit <= 0:
        return []
    match = _CONTEXT_RE.search(html)
    if not match:
        return []
    try:
        context = json.loads(f'"{match.group(1)}"')
        data = json.loads(context)
    except (json.JSONDecodeError, TypeError):
        return []
    media = (
        data.get("gql_data", {}).get("shortcode_media")
        if isinstance(data, dict)
        else None
    )
    if not isinstance(media, dict):
        return []

    caption = _caption(media)
    owner = media.get("owner")
    username = owner.get("username") if isinstance(owner, dict) else None
    shortcode = media.get("shortcode")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for node in _nodes(media):
        if not isinstance(node, dict) or node.get("is_video"):
            continue
        url = node.get("display_url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if suffix not in _IMAGE_EXTENSIONS:
            suffix = ".jpg"
        items.append(
            {
                "url": url,
                "extension": suffix.lstrip("."),
                "description": caption,
                "username": username if isinstance(username, str) else None,
                "shortcode": shortcode if isinstance(shortcode, str) else None,
            }
        )
        if len(items) >= limit:
            break
    return items


def fetch_items(url: str, limit: int) -> list[dict[str, Any]]:
    shortcode = instagram_shortcode(url)
    if not shortcode:
        return []
    embed_url = (
        "https://www.instagram.com/p/"
        f"{urllib.parse.quote(shortcode, safe='')}/embed/captioned/"
    )
    return parse_embed_html(_fetch_html(embed_url), limit)


def _download_image(url: str, target: Path) -> None:
    try:
        with _OPENER.open(_request(url), timeout=30) as response:
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise InstagramEmbedError(
                    f"Beklenen medya yerine {content_type} alindi."
                )
            total = 0
            with target.open("wb") as handle:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_IMAGE_BYTES:
                        raise InstagramEmbedError("Fotoğraf izin verilen boyuttan buyuk.")
                    handle.write(chunk)
    except InstagramEmbedError:
        target.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        target.unlink(missing_ok=True)
        raise InstagramEmbedError(f"Fotoğraf indirilemedi: {exc}") from exc
    if total <= 0:
        target.unlink(missing_ok=True)
        raise InstagramEmbedError("Fotoğraf bos geldi.")


def download_items(url: str, outdir: Path, limit: int) -> list[Path]:
    shortcode = instagram_shortcode(url)
    items = fetch_items(url, limit)
    if not shortcode or not items:
        raise InstagramEmbedError("Embed sayfasinda fotoğraf bulunamadi.")
    outdir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for index, item in enumerate(items, start=1):
        suffix = f".{item['extension']}"
        target = outdir / f"{shortcode}_{index:02d}{suffix}"
        _download_image(str(item["url"]), target)
        files.append(target)
    return files
