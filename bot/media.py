"""Video dosyasi metadata'si ve Telegram icin kucuk onizleme uretimi.

Telegram, yaklasik 10 MB'tan buyuk videolarda dosyadan boyut/duration
bilgisini guvenilir sekilde cikaramayabiliyor. Bu durumda sohbette video
kare (1:1) gorunebiliyor. `sendVideo` cagrisina gercek width/height/duration
ve kucuk bir JPEG thumbnail eklemek bu sorunu giderir.

ffmpeg/ffprobe yoksa veya dosya okunamazsa `None` doner; bot eski davranisa
devam eder.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_THUMBNAIL_MAX = 320
_THUMBNAIL_MAX_BYTES = 200 * 1024
_FFPROBE_TIMEOUT = 30
_FFMPEG_TIMEOUT = 30
_CONVERT_TIMEOUT = 900


@dataclass(frozen=True, slots=True)
class VideoMeta:
    width: int
    height: int
    duration: int
    thumbnail: Path | None = None


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("Medya araci calistirilamadi (%s): %s", cmd[0], exc)
        return None


def _rotation(stream: dict) -> int:
    """ffprobe'daki display matrix / rotate etiketini derece olarak dondurur."""
    for item in stream.get("side_data_list") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("rotation")
        if value is not None:
            try:
                return int(float(value))
            except (TypeError, ValueError):
                pass
    tags = stream.get("tags")
    if isinstance(tags, dict):
        value = tags.get("rotate")
        if value is not None:
            try:
                return int(float(value))
            except (TypeError, ValueError):
                pass
    return 0


def _thumbnail(path: Path, duration: int, outdir: Path) -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"{path.stem}-{uuid.uuid4().hex[:8]}.thumb.jpg"
    seek = min(1.0, max(0.0, duration / 2)) if duration else 0.0
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{seek:.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={_THUMBNAIL_MAX}:{_THUMBNAIL_MAX}:"
        "force_original_aspect_ratio=decrease",
        "-q:v",
        "5",
        str(target),
    ]
    proc = _run(cmd, _FFMPEG_TIMEOUT)
    if proc is None or proc.returncode != 0 or not target.is_file():
        target.unlink(missing_ok=True)
        if proc is not None and proc.stderr:
            log.info("Video onizlemesi uretilemedi: %s", proc.stderr[-300:])
        return None
    if target.stat().st_size <= 0 or target.stat().st_size > _THUMBNAIL_MAX_BYTES:
        target.unlink(missing_ok=True)
        return None
    return target


def _inspect_video(path: Path, thumbnail_dir: Path | None = None) -> VideoMeta | None:
    """Video genisligi, yuksekligi, suresi ve istege bagli JPEG onizleme."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    proc = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        _FFPROBE_TIMEOUT,
    )
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
        stream = (data.get("streams") or [])[0]
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if width <= 0 or height <= 0:
        return None
    if abs(_rotation(stream)) % 180 == 90:
        width, height = height, width

    duration = 0
    format_data = data.get("format")
    if isinstance(format_data, dict):
        try:
            duration = int(round(float(format_data.get("duration") or 0)))
        except (TypeError, ValueError):
            duration = 0
    if duration <= 0:
        try:
            duration = int(round(float(stream.get("duration") or 0)))
        except (TypeError, ValueError):
            duration = 0

    thumb = _thumbnail(path, duration, thumbnail_dir or path.parent)
    return VideoMeta(width=width, height=height, duration=max(duration, 0), thumbnail=thumb)


def inspect_video(path: Path, thumbnail_dir: Path | None = None) -> VideoMeta | None:
    """Metadata okunamazsa gonderimi durdurmadan None dondurur."""
    try:
        return _inspect_video(path, thumbnail_dir)
    except Exception:  # noqa: BLE001 - metadata istege bagli bir iyilestirmedir
        log.exception("Video metadata okunamadi: %s", path)
        return None


def keep_thumbnail(
    thumbnail: Path | None, cache_key: str, directory: Path
) -> str | None:
    """Onizlemeyi kalici klasore kopyalar ve yolunu dondurur.

    Telegram bir videonun `thumbnail` file_id'sini baska bir cagrida fotograf
    olarak kabul etmiyor ("can't use file of type Thumbnail as Photo"). Bu
    yuzden kapak fotoGraFi butonunda yerel JPEG dosyasi kullanilir. Kopya
    gecici `dl-*` klasoru silindikten sonra da kalir.
    """
    if thumbnail is None or not thumbnail.is_file():
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{cache_key}.jpg"
        if target != thumbnail:
            shutil.copyfile(thumbnail, target)
        return str(target)
    except OSError:
        log.exception("Kapak onizlemesi saklanamadi: %s", thumbnail)
        return None


def remove_thumbnail(path: str | None, directory: Path) -> None:
    """Saklanan kapak onizlemesini siler (onbellek kaydi silinirken)."""
    if not path:
        return
    try:
        candidate = Path(path).resolve()
        root = directory.resolve()
    except OSError:
        return
    # Yalnizca kendi klasorumuzdeki dosyalari sil.
    if root not in candidate.parents:
        return
    candidate.unlink(missing_ok=True)


def convert_for_telegram(path: Path, outdir: Path | None = None) -> Path | None:
    """Videoyu Telegram'in oynatabildigi H.264/AAC MP4'e cevirir.

    Bazi VP9/AV1 dosyalarini Telegram oynatamiyor; bu durumda bir kez
    donusturup yeniden gondermeyi deneriz. ffmpeg yoksa veya donusum
    basarisizsa None doner (cagiran belge olarak gondermeye duser).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not path.is_file():
        return None
    target_dir = outdir or path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{path.stem}-telegram.mp4"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        "-max_muxing_queue_size",
        "1024",
        str(target),
    ]
    proc = _run(cmd, _CONVERT_TIMEOUT)
    if proc is None or proc.returncode != 0:
        target.unlink(missing_ok=True)
        if proc is not None and proc.stderr:
            log.info("Telegram donusumu basarisiz: %s", proc.stderr[-300:])
        return None
    if not target.is_file() or target.stat().st_size <= 0:
        target.unlink(missing_ok=True)
        return None
    return target
