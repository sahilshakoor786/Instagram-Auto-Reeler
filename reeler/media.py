"""ffmpeg helpers: probe, convert to a Reels-ready 9:16 MP4, grab frames, measure energy."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

FONT_CANDIDATES = [
    # Windows
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    # Linux
    "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


class MediaError(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            hint = {
                "Windows": "winget install Gyan.FFmpeg  (then reopen the terminal)",
                "Darwin": "brew install ffmpeg",
            }.get(platform.system(), "sudo apt install ffmpeg")
            raise MediaError(f"'{tool}' not found on PATH. Install it: {hint}")


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise MediaError(f"{cmd[0]} failed ({proc.returncode}):\n{tail}")
    return proc


@dataclass
class Probe:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    rotation: int = 0

    @property
    def display_size(self) -> tuple[int, int]:
        if abs(self.rotation) in (90, 270):
            return self.height, self.width
        return self.width, self.height


def probe(path: Path) -> Probe:
    out = _run([
        "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)
    ]).stdout
    data = json.loads(out)
    vstream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not vstream:
        raise MediaError(f"No video stream in {path}")
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    duration = float(data.get("format", {}).get("duration") or vstream.get("duration") or 0)
    num, _, den = (vstream.get("avg_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    rotation = 0
    for sd in vstream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if "rotate" in (vstream.get("tags") or {}):
        rotation = int(vstream["tags"]["rotate"])
    return Probe(duration, int(vstream["width"]), int(vstream["height"]), fps or 30.0, has_audio, rotation)


def find_font(configured: str = "") -> Path | None:
    for cand in ([configured] if configured else []) + FONT_CANDIDATES:
        if cand and Path(cand).is_file():
            return Path(cand)
    return None


def wrap_hook(text: str, width: int = 16, max_lines: int = 3) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    return textwrap.wrap(text.upper(), width=width)[:max_lines]


def _fit_filter(fit: str, W: int, H: int, src_w: int, src_h: int) -> str:
    """Filtergraph that turns [0:v] into an exact WxH frame labelled [base]."""
    if abs(src_w / src_h - W / H) < 0.01:
        return f"[0:v]scale={W}:{H}:flags=lanczos,setsar=1[base]"
    if fit == "crop":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={W}:{H},setsar=1[base]")
    if fit == "pad":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[base]")
    # blur: blurred, zoomed copy of the clip behind the full (uncropped) gameplay
    return (
        "[0:v]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
        f"gblur=sigma=12,eq=brightness=-0.10:saturation=1.2,scale={W}:{H}[bg];"
        f"[fgsrc]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
    )


def render_reel(
    src: Path,
    dst: Path,
    *,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    fit: str = "blur",
    max_duration: float = 90,
    crf: int = 20,
    audio_bitrate: str = "128k",
    hook_lines: list[str] | None = None,
    hook_seconds: float = 2.5,
    watermark: str = "",
    font: Path | None = None,
) -> Path:
    """Encode `src` into an Instagram-Reels-compliant H.264/AAC 9:16 MP4 at `dst`."""
    info = probe(src)
    sw, sh = info.display_size
    dst.parent.mkdir(parents=True, exist_ok=True)
    workdir = dst.parent

    chain = [_fit_filter(fit, width, height, sw, sh)]
    last = "base"
    overlays: list[str] = []

    # Text is passed through files + a font copied next to the output, so the filtergraph
    # never contains Windows drive letters / quotes that need ffmpeg escaping.
    font_arg = ""
    if font and (hook_lines or watermark):
        local_font = workdir / ("_font" + font.suffix.lower())
        if not local_font.exists():
            shutil.copyfile(font, local_font)
        font_arg = f"fontfile={local_font.name}:"
    if hook_lines and font_arg:
        size = 92 if max(len(line) for line in hook_lines) <= 12 else 76
        top = int(height * 0.16)
        for i, line in enumerate(hook_lines):
            tf = workdir / f"_{dst.stem}_hook{i}.txt"
            tf.write_text(line, encoding="utf-8")
            overlays.append(
                f"drawtext={font_arg}textfile={tf.name}:expansion=none:fontsize={size}:fontcolor=white:"
                f"borderw=7:bordercolor=black:x=(w-text_w)/2:y={top}+{i}*{int(size * 1.18)}:"
                f"enable='lt(t,{hook_seconds})'"
            )
    if watermark and font_arg:
        wf = workdir / f"_{dst.stem}_wm.txt"
        wf.write_text(watermark, encoding="utf-8")
        overlays.append(
            f"drawtext={font_arg}textfile={wf.name}:expansion=none:fontsize=38:fontcolor=white@0.55:"
            f"borderw=2:bordercolor=black@0.4:x=(w-text_w)/2:y=h*0.86"
        )
    overlays.append(f"fps={fps},format=yuv420p")
    chain.append(f"[{last}]" + ",".join(overlays) + "[v]")
    graph = ";".join(chain)

    duration = min(info.duration or max_duration, max_duration)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src.resolve())]
    if not info.has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += [
        "-filter_complex", graph,
        "-map", "[v]",
        "-map", "0:a:0" if info.has_audio else "1:a:0",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-level", "4.1",
        "-crf", str(crf), "-maxrate", "12M", "-bufsize", "24M",
        "-g", str(fps * 2), "-keyint_min", str(fps), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(dst.resolve()),
    ]
    _run(cmd, cwd=workdir)
    for tmp in workdir.glob(f"_{dst.stem}_*.txt"):
        tmp.unlink(missing_ok=True)
    return dst


def extract_frames(src: Path, out_dir: Path, count: int = 6, width: int = 768) -> list[tuple[float, Path]]:
    """Grab `count` evenly spaced JPEG frames (skipping the very first/last instants)."""
    info = probe(src)
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = max(info.duration, 0.5)
    frames = []
    for i in range(count):
        t = dur * (i + 0.5) / count
        fp = out_dir / f"frame_{i:02d}.jpg"
        _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src),
            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(fp),
        ])
        frames.append((round(t, 2), fp))
    return frames


def grab_cover(src: Path, dst: Path, t: float) -> Path:
    _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(t, 0):.2f}", "-i", str(src),
        "-frames:v", "1", "-q:v", "2", str(dst),
    ])
    return dst


def energy_stats(src: Path) -> dict:
    """Cheap heuristics used when no AI is available: cuts/sec and loudness."""
    info = probe(src)
    stats = {"duration": round(info.duration, 2), "scene_cuts_per_sec": 0.0, "mean_volume_db": None}
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(src), "-vf", "select='gt(scene,0.25)',showinfo",
             "-an", "-f", "null", os.devnull],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        cuts = proc.stderr.count("pts_time:")
        stats["scene_cuts_per_sec"] = round(cuts / max(info.duration, 1), 3)
    except OSError:
        pass
    if info.has_audio:
        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", str(src), "-af", "volumedetect", "-vn", "-f", "null", os.devnull],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", proc.stderr)
            if m:
                stats["mean_volume_db"] = float(m.group(1))
        except OSError:
            pass
    return stats
