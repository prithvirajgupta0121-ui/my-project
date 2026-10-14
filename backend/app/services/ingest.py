"""Secure media ingestion: type allow-listing, magic-byte checks, size caps,
decode verification, metadata/GPS extraction and encrypted local storage."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

import cv2

from ..config import get_settings
from ..crypto import decrypt_bytes, encrypt_bytes, is_encrypted_file
from ..errors import APIError
from ..security import sanitize_display_name

ALLOWED = {"mp4": "VIDEO", "avi": "VIDEO", "mov": "VIDEO", "jpg": "IMAGE", "jpeg": "IMAGE", "png": "IMAGE", "webp": "IMAGE"}


def sniff_ok(head: bytes, ext: str) -> bool:
    """Verify magic bytes match the declared extension."""
    if ext in ("mp4", "mov"):
        return len(head) >= 12 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")
    if ext == "avi":
        return head[:4] == b"RIFF" and head[8:12] == b"AVI "
    if ext in ("jpg", "jpeg"):
        return head[:3] == b"\xff\xd8\xff"
    if ext == "png":
        return head[:8] == b"\x89PNG\r\n\x1a\n"
    if ext == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


def extension_of(filename: str) -> str:
    name = sanitize_display_name(filename)
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


def tmp_dir() -> Path:
    d = get_settings().data_dir / "tmp"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


async def receive_upload(upload, max_bytes: int) -> tuple[Path, int, str, bytes]:
    """Stream an UploadFile to a private temp file enforcing the size cap."""
    tmp = tmp_dir() / f"up-{uuid.uuid4().hex}"
    size, h, head = 0, hashlib.sha256(), b""
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                if not head:
                    head = chunk[:32]
                size += len(chunk)
                if size > max_bytes:
                    raise APIError(413, "FILE_TOO_LARGE",
                                   f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
                h.update(chunk)
                fh.write(chunk)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, size, h.hexdigest(), head


def _ffprobe(path: Path) -> dict:
    if not shutil.which("ffprobe"):
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=20, check=False)
        return json.loads(out.stdout or b"{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return {}


_ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?")


def _video_gps(probe: dict) -> tuple[float, float, float | None] | None:
    tags = (probe.get("format") or {}).get("tags") or {}
    for key in ("location", "com.apple.quicktime.location.ISO6709", "location-eng"):
        if key in tags:
            m = _ISO6709.match(tags[key])
            if m:
                return float(m.group(1)), float(m.group(2)), float(m.group(3)) if m.group(3) else None
    return None


def _image_exif(path: Path) -> tuple[dict, tuple[float, float, float | None] | None]:
    try:
        from PIL import ExifTags, Image
    except ImportError:  # pragma: no cover
        return {}, None
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            meta = {}
            for k, v in exif.items():
                name = ExifTags.TAGS.get(k, str(k))
                if name in ("Make", "Model", "DateTime", "Software", "ImageDescription"):
                    meta[name] = str(v)[:80]
            gps_ifd = exif.get_ifd(0x8825)
            if not gps_ifd:
                return meta, None

            def dms(v):
                d, m, s = (float(x) for x in v)
                return d + m / 60 + s / 3600

            lat = dms(gps_ifd[2]) * (-1 if gps_ifd.get(1) in ("S", b"S") else 1)
            lon = dms(gps_ifd[4]) * (-1 if gps_ifd.get(3) in ("W", b"W") else 1)
            alt = float(gps_ifd[6]) if 6 in gps_ifd else None
            return meta, (lat, lon, alt)
    except Exception:
        return {}, None


def probe_media(path: Path, kind: str) -> dict:
    """Decode-verify the file and extract metadata. Raises APIError on corruption."""
    info: dict = {"meta": {}}
    if kind == "VIDEO":
        cap = cv2.VideoCapture(str(path))
        ok, frame = (cap.read() if cap.isOpened() else (False, None))
        if not ok or frame is None:
            cap.release()
            raise APIError(422, "CORRUPTED_VIDEO", "Video could not be decoded (corrupted or unsupported codec).")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
        cap.release()
        codec = "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)).strip("\x00 ") or None
        fps = fps if 0 < fps < 480 else 25.0
        info.update(width=frame.shape[1], height=frame.shape[0], fps=round(fps, 3), frame_count=n,
                    duration_s=round(n / fps, 2) if n else None, codec=codec)
        probe = _ffprobe(path)
        if probe:
            fmt = probe.get("format") or {}
            if fmt.get("duration"):
                info["duration_s"] = round(float(fmt["duration"]), 2)
            vstreams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
            if vstreams:
                info["codec"] = vstreams[0].get("codec_name", info["codec"])
            info["meta"]["container"] = fmt.get("format_name")
            info["meta"]["bit_rate"] = fmt.get("bit_rate")
            gps = _video_gps(probe)
            if gps:
                info["gps"] = gps
    else:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise APIError(422, "INVALID_FILE", "Image could not be decoded (corrupted or unsupported).")
        info.update(width=img.shape[1], height=img.shape[0], fps=None, frame_count=1, duration_s=None, codec=None)
        meta, gps = _image_exif(path)
        info["meta"].update(meta)
        if gps:
            info["gps"] = gps
    return info


def store_media(tmp: Path, mission_id: int, ext: str) -> tuple[str, bool]:
    """Move a validated temp file into mission storage (encrypted if configured)."""
    s = get_settings()
    stored = f"{uuid.uuid4().hex}.{ext}"
    dest_dir = s.media_dir / str(int(mission_id))
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = dest_dir / stored
    if s.encrypt_at_rest:
        data = tmp.read_bytes()
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(encrypt_bytes(data))
        tmp.unlink(missing_ok=True)
    else:
        shutil.move(str(tmp), dest)
        os.chmod(dest, 0o600)
    return stored, s.encrypt_at_rest


def media_path(mission_id: int, stored_name: str) -> Path:
    # stored_name is server-generated (uuid hex + ext); validate defensively anyway.
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]{2,5}", stored_name):
        raise APIError(400, "INVALID_PATH", "Invalid stored media reference.")
    return get_settings().media_dir / str(int(mission_id)) / stored_name


@contextmanager
def plaintext_copy(path: Path, ext: str):
    """Yield a readable plaintext path (decrypting to a private temp file if needed)."""
    if not is_encrypted_file(path):
        yield path
        return
    tmp = tmp_dir() / f"dec-{uuid.uuid4().hex}.{ext}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(decrypt_bytes(path.read_bytes()))
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)
