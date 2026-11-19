"""Mermaid diagram rendering and image dimension helpers for markdown2keyx."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Callable

MermaidRenderer = Callable[[str], tuple[bytes, str]]

MERMAID_MODES = frozenset({"auto", "mmdc", "ink", "off"})

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SOF_MARKERS = frozenset(
    {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
)
_SVG_VIEWBOX_RE = re.compile(
    r'viewBox\s*=\s*["\']\s*[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?[\s,]+[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?[\s,]+(\d*\.?\d+(?:[eE][-+]?\d+)?)[\s,]+(\d*\.?\d+(?:[eE][-+]?\d+)?)\s*["\']',
    re.IGNORECASE,
)
_SVG_DIM_RE = lambda attr: re.compile(
    rf'\b{attr}\s*=\s*["\']\s*(\d*\.?\d+)\s*(?:px|pt)?\s*["\']',
    re.IGNORECASE,
)


def image_dimensions(data: bytes, ext: str = "") -> tuple[float, float] | None:
    """Return natural (width, height) from PNG, JPEG, GIF, or SVG bytes."""
    if len(data) >= 24 and data[:8] == _PNG_MAGIC and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        if w > 0 and h > 0:
            return (float(w), float(h))

    if len(data) >= 10 and data[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", data[6:10])
        if w > 0 and h > 0:
            return (float(w), float(h))

    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            while pos < len(data) and data[pos] == 0xFF:
                pos += 1
            if pos >= len(data):
                break
            marker = data[pos]
            pos += 1
            if marker in (0xD8, 0xD9, 0x00) or 0xD0 <= marker <= 0xD7:
                continue
            if pos + 2 > len(data):
                break
            seg_len = struct.unpack(">H", data[pos : pos + 2])[0]
            if seg_len < 2 or pos + seg_len > len(data):
                break
            if marker in _SOF_MARKERS and pos + 7 <= len(data):
                h, w = struct.unpack(">HH", data[pos + 3 : pos + 7])
                if w > 0 and h > 0:
                    return (float(w), float(h))
            pos += seg_len

    if ext.lower() == ".svg" or b"<svg" in data[:1024].lower():
        text = data[:8192].decode("utf-8", errors="ignore")
        vb = _SVG_VIEWBOX_RE.search(text)
        if vb:
            w, h = float(vb.group(1)), float(vb.group(2))
            if w > 0 and h > 0:
                return (w, h)
        wm = _SVG_DIM_RE("width").search(text)
        hm = _SVG_DIM_RE("height").search(text)
        if wm and hm:
            w, h = float(wm.group(1)), float(hm.group(1))
            if w > 0 and h > 0:
                return (w, h)

    return None


def _find_mmdc() -> str | None:
    env_bin = os.environ.get("MD2KEYX_MMDC", "").strip()
    if env_bin:
        candidate = Path(env_bin)
        if candidate.is_file():
            return str(candidate)
        resolved = shutil.which(env_bin)
        if resolved:
            return resolved
        return None
    return shutil.which("mmdc")


def _render_via_mmdc(code: str, mmdc_bin: str) -> tuple[bytes, str]:
    with tempfile.TemporaryDirectory(prefix="md2keyx-mmdc-") as tmp:
        tmp_dir = Path(tmp)
        in_path = tmp_dir / "diagram.mmd"
        out_path = tmp_dir / "diagram.png"
        in_path.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [mmdc_bin, "-i", str(in_path), "-o", str(out_path), "-s", "2", "-b", "transparent"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0 or not out_path.is_file():
            detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
            raise RuntimeError(f"mmdc failed: {detail}")
        data = out_path.read_bytes()
        if not data:
            raise RuntimeError("mmdc produced an empty output file.")
        return data, ".png"


def build_mermaid_ink_url(code: str) -> str:
    """Encode a Mermaid diagram into a mermaid.ink pako PNG URL."""
    payload = json.dumps(
        {"code": code, "mermaid": {"theme": "default"}},
        separators=(",", ":"),
    ).encode("utf-8")
    compressed = zlib.compress(payload, level=9)
    encoded = base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")
    return f"https://mermaid.ink/img/pako:{encoded}?type=png"


def _render_via_ink(code: str, *, timeout: float = 15.0) -> tuple[bytes, str]:
    url = build_mermaid_ink_url(code)
    req = urllib.request.Request(url, headers={"User-Agent": "md2keyx/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if not data.startswith(_PNG_MAGIC):
        raise RuntimeError("mermaid.ink response was not a valid PNG image.")
    return data, ".png"


def render_mermaid(
    code: str,
    *,
    mode: str = "auto",
    renderer: MermaidRenderer | None = None,
    warnings: list[str] | None = None,
) -> tuple[bytes, str] | None:
    """Render Mermaid diagram source into (image_bytes, extension) or None on fallback."""
    norm_mode = mode.strip().lower()
    if norm_mode not in MERMAID_MODES:
        raise ValueError(
            f"Unsupported mermaid mode {mode!r}; expected one of {sorted(MERMAID_MODES)}."
        )

    def _warn(msg: str) -> None:
        if warnings is not None:
            warnings.append(msg)

    if norm_mode == "off":
        _warn("Mermaid rendering disabled (mermaid=off); rendered diagram as code card.")
        return None

    if renderer is not None:
        try:
            data, ext = renderer(code)
            if not data:
                raise ValueError("Custom mermaid renderer returned empty bytes.")
            raw_ext = (ext or "").strip() or ".png"
            norm_ext = raw_ext if raw_ext.startswith(".") else f".{raw_ext}"
            return data, norm_ext
        except Exception as exc:
            _warn(f"Mermaid renderer failed ({exc}); rendered diagram as code card.")
            return None

    mmdc_error: str | None = None
    if norm_mode in ("auto", "mmdc"):
        mmdc_bin = _find_mmdc()
        if mmdc_bin:
            try:
                return _render_via_mmdc(code, mmdc_bin)
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                mmdc_error = str(exc)
        elif norm_mode == "mmdc":
            _warn("Mermaid CLI 'mmdc' not found on PATH; rendered diagram as code card.")
            return None

        if norm_mode == "mmdc":
            _warn(f"Mermaid rendering failed ({mmdc_error}); rendered diagram as code card.")
            return None

    if norm_mode in ("auto", "ink"):
        try:
            return _render_via_ink(code)
        except (OSError, urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            prefix = f"{mmdc_error}; " if mmdc_error else ""
            _warn(
                f"Mermaid rendering unavailable ({prefix}mermaid.ink: {exc}); rendered diagram as code card."
            )
            return None

    return None
