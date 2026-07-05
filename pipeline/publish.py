"""YT publishing assets. CLI: `shot publish`.

Two operations: thumbnail render (frame from the SOURCE at full resolution →
1280×720, place text in a heavy font with an outline, readability gradient,
subtle grading — Pillow) and description merge (specific part written by the
agent + channel boilerplate from a template in the repo root; specific part
FIRST — the first ~150 characters show up in YT search results).

The title and description body are written by the agent (in English) — this
module generates nothing, it does deterministic image-and-file work. The
thumbnail style ("trendy") is codified in the constants below; criteria for
frame, text and title choice: docs/decision-rules.md ("Publishing").
"""

import os
import subprocess
import tempfile
from pathlib import Path

from . import ffmpeg, paths

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from .probe import probe

PUBLISH_DIR = paths.PUBLISH
TEMPLATE_PATH = Path("publish-template.txt")  # env SHOT_PUBLISH_TEMPLATE wins
THUMB_W, THUMB_H = 1280, 720                  # YT standard
JPEG_QUALITY = 90
MAX_BYTES = 2_000_000                         # YT limit for a thumbnail

# Thumbnail style — values documented in decision-rules.md ("Publishing");
# style changes ONLY here + there.
CONTRAST, SATURATION = 1.08, 1.15             # ImageEnhance — subtle
GRADIENT_ALPHA = 140                          # gradient peak under the text
GRADIENT_FRAC = 0.45                          # gradient over 45% of the frame height
TEXT_MAX_W_FRAC = 0.86                        # text ≤ 86% of the frame width
TEXT_SIZE_START, TEXT_SIZE_FLOOR = 150, 48    # downward auto-fit of the size
SUBTITLE_SIZE = 44
SUBTITLE_GAP = 46                             # gap between the subtitle and the main text
MARGIN = 64
FONT_CHAIN = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",          # macOS
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",   # Debian/Ubuntu
    "/usr/share/fonts/noto/NotoSans-Black.ttf",               # Arch (heavy, Impact-like)
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",               # Arch ttf-dejavu
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",    # Arch
]
SUBTITLE_FONT_CHAIN = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
] + FONT_CHAIN
# Fontconfig queries (fc-match) — fallback when no fixed path exists;
# makes the thumbnail render portable to any Linux regardless of distro.
FONT_FC_QUERY = "sans:bold:weight=200"        # main text: the heaviest bold
SUBTITLE_FC_QUERY = "sans:bold"


# ----------------------------------------------------------------- thumbnail

def _fc_match(query: str) -> str | None:
    """Font file path from fontconfig (fc-match), or None when unavailable."""
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", query],
                             capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    path = out.stdout.strip()
    return path if path and Path(path).exists() else None


def _find_font(chain: list[str], fc_query: str | None = None) -> str:
    for path in chain:
        if Path(path).exists():
            return path
    if fc_query and (path := _fc_match(fc_query)):
        return path
    raise RuntimeError("no font for the thumbnail — looked for: " + ", ".join(chain))


def _fit_font(draw: ImageDraw.ImageDraw, text: str, font_path: str,
              max_w: int, start: int = TEXT_SIZE_START) -> tuple[ImageFont.FreeTypeFont, int]:
    """Largest size (≤ start) at which the text (with stroke) fits within max_w."""
    for size in range(start, TEXT_SIZE_FLOOR - 1, -6):
        font = ImageFont.truetype(font_path, size)
        sw = max(3, size // 16)
        left, _, right, _ = draw.textbbox((0, 0), text, font=font, stroke_width=sw)
        if right - left <= max_w:
            return font, sw
    return font, sw  # floor — the text is too long, it should be 1–3 words anyway


def _apply_gradient(im: Image.Image, pos: str) -> None:
    """Dark gradient under the text (readability on a bright/busy frame)."""
    gh = int(im.height * GRADIENT_FRAC)
    mask = Image.new("L", (1, gh))
    mask.putdata([int(GRADIENT_ALPHA * i / (gh - 1)) for i in range(gh)])
    if pos == "top":
        mask = mask.transpose(Image.FLIP_TOP_BOTTOM)
    mask = mask.resize((im.width, gh))
    black = Image.new("RGB", (im.width, gh), 0)
    im.paste(black, (0, 0 if pos == "top" else im.height - gh), mask)


def _extract_frame(source: Path, at_s: float) -> Image.Image:
    """Frame from the source at full resolution (downscale done by Pillow, not ffmpeg)."""
    info = probe(source)
    if not 0 <= at_s < info.duration:
        raise ValueError(f"frame time {at_s:g} s outside the video (0–{info.duration:.1f} s)")
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        ffmpeg.extract_frame(source, at_s, Path(tmp))
        with Image.open(tmp) as im:
            return im.convert("RGB")
    finally:
        os.unlink(tmp)


def render_thumbnail(source: Path, at_s: float, text: str, out: Path,
                     subtitle: str | None = None, pos: str = "bottom",
                     text_size: int | None = None) -> dict:
    """YT thumbnail: frame + grading + gradient + place text (UPPERCASE) with an outline."""
    im = _extract_frame(source, at_s)

    # center-crop to 16:9 when the source has different proportions
    target = THUMB_W / THUMB_H
    w, h = im.size
    if abs(w / h - target) > 0.01:
        if w / h > target:
            nw = int(h * target)
            im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
        else:
            nh = int(w / target)
            im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    im = im.resize((THUMB_W, THUMB_H), Image.LANCZOS)

    im = ImageEnhance.Contrast(im).enhance(CONTRAST)
    im = ImageEnhance.Color(im).enhance(SATURATION)
    _apply_gradient(im, pos)

    draw = ImageDraw.Draw(im)
    text = text.upper()
    font_path = _find_font(FONT_CHAIN, FONT_FC_QUERY)
    font, sw = _fit_font(draw, text, font_path, int(THUMB_W * TEXT_MAX_W_FRAC),
                         start=text_size or TEXT_SIZE_START)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=sw)
    main_h = bbox[3] - bbox[1]
    sub_font = ImageFont.truetype(_find_font(SUBTITLE_FONT_CHAIN, SUBTITLE_FC_QUERY), SUBTITLE_SIZE)

    if pos == "bottom":
        draw.text((MARGIN, THUMB_H - MARGIN), text, font=font, anchor="ld",
                  fill="white", stroke_width=sw, stroke_fill="black")
        if subtitle:
            draw.text((MARGIN + 4, THUMB_H - MARGIN - main_h - SUBTITLE_GAP),
                      subtitle, font=sub_font, anchor="ld", fill="white",
                      stroke_width=2, stroke_fill="black")
    else:
        draw.text((MARGIN, MARGIN), text, font=font, anchor="la",
                  fill="white", stroke_width=sw, stroke_fill="black")
        if subtitle:
            draw.text((MARGIN + 4, MARGIN + main_h + SUBTITLE_GAP), subtitle,
                      font=sub_font, anchor="la", fill="white",
                      stroke_width=2, stroke_fill="black")

    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=JPEG_QUALITY)
    if out.stat().st_size > MAX_BYTES:
        im.save(out, "JPEG", quality=85)
        if out.stat().st_size > MAX_BYTES:
            raise RuntimeError(f"thumbnail > {MAX_BYTES} B even at quality 85")
    return {"out": str(out), "source": str(source), "at_s": at_s,
            "text": text, "subtitle": subtitle, "pos": pos,
            "width": THUMB_W, "height": THUMB_H,
            "font": Path(font_path).stem, "bytes": out.stat().st_size}


# -------------------------------------------------------------- description

def template_path() -> Path:
    env = os.environ.get("SHOT_PUBLISH_TEMPLATE")
    return Path(env) if env else TEMPLATE_PATH


def merge_description(specific: str, template: Path) -> str:
    """The specific part (from the agent) FIRST, then the channel boilerplate."""
    if not template.exists():
        raise RuntimeError(
            f"description template {template} missing — copy publish-template.example.txt "
            f"to publish-template.txt and fill in your channel details")
    return specific.strip() + "\n\n" + template.read_text().strip() + "\n"
