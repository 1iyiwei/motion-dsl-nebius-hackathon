"""Render an edit list into a demo video.

    uv run python demos/render.py demos/motionharness-v1.json demos/motionharness_demo_v1.mp4
    python3 demos/render.py demos/sketchdynamics-v1.json demos/sketchdynamics_demo_v1.mp4      # the original list format

An edit list is a JSON object (paths are relative to the edit list's folder):

    {
      "sources": {"W": "motion-harness-wobble.mp4", "C": "motion-harness-claude.mp4"},
      "size": [1920, 1080], "fps": 30, "background": "0xf4f4f1",
      "crops": {"ui": [8, 118, 1600, 900]},          # optional named crops: [x, y, w, h] in source pixels
      "segments": [
        {"src": "W", "in": 1.4, "out": 13.3, "crop": "ui", "note": "intro"},
        {"src": "W", "in": 62.8, "out": 82.8, "speed": 5, "crop": "ui"},       # sped up: silent, labelled "5× speed"
        {"src": "C", "in": 77.4, "out": 87.6, "crop": [828, 248, 880, 495], "caption": "Claude Code + MCP"},
        {"src": "C", "in": 11.8, "out": 16.0, "crop": "term", "masks": [[832, 280, 860, 80]]}   # hide a repo URL
      ]
    }

Each segment is cropped (optional), scaled and padded to `size` at `fps`, with a 40 ms audio fade at each cut so
splices don't click. Sped-up segments drop their audio and get a small label, so the edit stays honest about time.
`caption` puts a lower-third label on a segment. `masks` paints boxes over private details (source pixels,
`mask_color` defaults to a dark terminal background). Labels are drawn with Pillow (available in this workspace's venv).

The original format, a list of [source, start_s, end_s, note] rows with sources M = sketch_dynamics.mp4 and
S = sunrise.mp4, still renders as before. Needs an ffmpeg that understands -/filter_complex (7.1+).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

LEGACY_SOURCES = {"M": "sketch_dynamics.mp4", "S": "sunrise.mp4"}
FADE = 0.04


def load(path: Path) -> dict:
    data = json.loads(path.read_text())
    if isinstance(data, list):  # original format: [source, in, out, note] rows
        data = {"sources": LEGACY_SOURCES, "segments": [{"src": s, "in": a, "out": b, "note": n} for s, a, b, n in data]}
    data.setdefault("size", [1920, 1080])
    data.setdefault("fps", 30)
    data.setdefault("background", "0xf4f4f1")
    return data


def duration(path: Path) -> float | None:
    """A source's length in seconds (ffprobe), or None if it can't be read."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def check(cfg: dict, base: Path) -> None:
    """Catch edit-list mistakes before ffmpeg turns them into cryptic filter errors."""
    lengths = {k: duration(base / f) for k, f in cfg["sources"].items()}
    problems = []
    for i, seg in enumerate(cfg["segments"], 1):
        where = f"segment {i}" + (f" ({seg['note']})" if seg.get("note") else "")
        if seg["src"] not in lengths:
            problems.append(f"{where}: unknown source {seg['src']!r} (sources: {', '.join(lengths)})")
            continue
        a, b, n = float(seg["in"]), float(seg["out"]), lengths[seg["src"]]
        if b <= a:
            problems.append(f"{where}: out ({b:g}) must be after in ({a:g})")
        if n is not None and b > n + 0.05:
            problems.append(f"{where}: out ({b:g}) is past the end of {cfg['sources'][seg['src']]} ({n:.1f} s)")
    if problems:
        sys.exit("Edit list problems:\n  " + "\n  ".join(problems))


def label_png(text: str, out: Path, height: int, corner: bool) -> None:
    """A rounded dark label with white text (Pillow), sized for a 1080p frame."""
    from PIL import Image, ImageDraw, ImageFont

    size = round(height * (0.026 if corner else 0.032))
    font = None
    for name in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
                 "DejaVuSans-Bold.ttf", "Arial.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    pad = size // 2
    x0, y0, x1, y1 = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font)
    img = Image.new("RGBA", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=pad, fill=(15, 17, 21, 215))
    d.text((pad - x0, pad - y0), text, font=font, fill=(255, 255, 255, 255))
    img.save(out)


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    edl, out = Path(sys.argv[1]), Path(sys.argv[2])
    cfg = load(edl)
    base = edl.parent
    W, H = cfg["size"]
    fps, bg = cfg["fps"], cfg["background"]
    crops = cfg.get("crops", {})
    order = list(cfg["sources"])
    inputs = [x for k in order for x in ("-i", str(base / cfg["sources"][k]))]
    check(cfg, base)
    tmp = Path(tempfile.mkdtemp(prefix="render-"))
    parts, labels, total = [], [], 0.0

    for i, seg in enumerate(cfg["segments"]):
        k = order.index(seg["src"])
        a, b = float(seg["in"]), float(seg["out"])
        speed = float(seg.get("speed", 1))
        d = (b - a) / speed
        total += d
        crop = seg.get("crop")
        crop = crops.get(crop, crop) if isinstance(crop, str) else crop
        v = f"[{k}:v]trim={a}:{b}," + ("setpts=PTS-STARTPTS" if speed == 1 else f"setpts=(PTS-STARTPTS)/{speed:g}")
        for x, y, w, h in seg.get("masks", []):  # hide private details, in source pixels, before cropping
            v += f",drawbox=x={x}:y={y}:w={w}:h={h}:color={seg.get('mask_color', '0x1c222c')}:t=fill"
        if crop:
            x, y, w, h = crop
            v += f",crop={w}:{h}:{x}:{y}"
        v += (f",scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={bg},fps={fps},setsar=1")
        overlays = []
        if speed != 1:
            overlays.append((f"{speed:g}× speed", True))
        if seg.get("caption"):
            overlays.append((seg["caption"], False))
        if overlays:
            parts.append(v + f"[base{i}]")
            cur = f"base{i}"
            for j, (text, corner) in enumerate(overlays):
                png = tmp / f"label{i}_{j}.png"
                label_png(text, png, H, corner)
                inputs += ["-i", str(png)]
                idx = len(inputs) // 2 - 1
                pos = "x=W-w-28:y=28" if corner else "x=(W-w)/2:y=H-h-48"
                nxt = f"ov{i}_{j}"
                parts.append(f"[{cur}][{idx}:v]overlay={pos}:eof_action=repeat:format=auto[{nxt}]")
                cur = nxt
            parts.append(f"[{cur}]null[v{i}]")
        else:
            parts.append(v + f"[v{i}]")
        if speed != 1:  # sped-up stretches are silent
            parts.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={d:.3f}[a{i}]")
        else:
            parts.append(f"[{k}:a]atrim={a}:{b},asetpts=PTS-STARTPTS,aresample=48000,"
                         f"afade=t=in:d={FADE},afade=t=out:st={d - FADE:.3f}:d={FADE}[a{i}]")
        labels.append(f"[v{i}][a{i}]")

    graph = edl.with_suffix(".filtergraph")
    graph.write_text(";".join(parts) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=1:a=1[v][a]")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-/filter_complex", str(graph),
                        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
                       stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.exit(f"ffmpeg failed (exit {r.returncode}); filter graph: {graph}\n" + "\n".join(r.stderr.strip().splitlines()[-8:]))
    print(f"{out}: {total:.1f} s from {len(labels)} segments")


if __name__ == "__main__":
    main()
