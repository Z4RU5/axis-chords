"""Turns the raw frames from capture.py into the README pictures:
docs/hero.gif and docs/panel_*.png (keycaps, titles, captions).

Run after capture.py:  python docs/visuals/compose.py
Needs Pillow and ffmpeg. Fonts: Segoe UI (Windows); swap FONT/BOLD elsewhere.
"""

import json
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "_render")
DOCS = os.path.dirname(HERE)
FONT = r"C:\Windows\Fonts\segoeui.ttf"
BOLD = r"C:\Windows\Fonts\seguisb.ttf" if os.path.exists(r"C:\Windows\Fonts\seguisb.ttf") else r"C:\Windows\Fonts\segoeuib.ttf"

BG = (30, 32, 36)
TEXT = (235, 235, 238)
MUTED = (160, 163, 170)
ORANGE = (255, 140, 20)
KEY = (232, 233, 237)
KEY_EDGE = (150, 152, 160)
KEY_TEXT = (28, 30, 34)

# Viewport crop (raw frames are 1600 x 941; the top strip is Blender's header).
CROP = (260, 70, 1460, 870)


def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def keycap(d, x, y, label, h=40, dim=False):
    """Draws a keyboard key at (x, y); returns its width."""
    f = font(int(h * 0.55), bold=True)
    w = max(h, int(d.textlength(label, font=f) + h * 0.7))
    fill = (120, 122, 128) if dim else KEY
    edge = (70, 72, 78) if dim else KEY_EDGE
    d.rounded_rectangle((x, y + 4, x + w, y + h + 4), radius=8, fill=edge)
    d.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=fill, outline=edge, width=2)
    tw = d.textlength(label, font=f)
    d.text((x + (w - tw) / 2, y + h * 0.13), label, font=f, fill=KEY_TEXT)
    return w


def keys(d, x, y, labels, h=40, dim=False, colour=TEXT):
    """Keys joined with '+'; returns the end x."""
    f = font(int(h * 0.6), bold=True)
    for i, k in enumerate(labels):
        if i:
            d.text((x + 6, y + h * 0.08), "+", font=f, fill=MUTED if dim else colour)
            x += int(d.textlength("+", font=f)) + 12
        x += keycap(d, x, y, k, h=h, dim=dim)
    return x


def tile(name, w, h):
    return Image.open(os.path.join(RAW, f"{name}.png")).convert("RGB").crop(CROP).resize((w, h), Image.LANCZOS)


def label_box(img, x, y, labels, caption, h=36):
    """Keys and a caption on a dark rounded box over a picture."""
    d = ImageDraw.Draw(img)
    f = font(int(h * 0.62))
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    end = keys(probe, 0, 0, labels, h=h) if labels else 0
    tw = d.textlength(caption, font=f)
    box_w = end + (18 if labels else 0) + tw + 32
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle((x, y, x + box_w, y + h + 28), radius=12, fill=(18, 19, 22, 225))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
    d = ImageDraw.Draw(img)
    cx = x + 16
    if labels:
        cx = keys(d, cx, y + 12, labels, h=h) + 18
    d.text((cx, y + 12 + h * 0.12), caption, font=f, fill=TEXT)


def panel(out_name, title, sub, tiles, cols):
    """Tiles: list of (raw name, keys, caption). Width 1200."""
    W = 1200
    gap = 12
    tw = (W - gap * (cols + 1)) // cols
    th = int(tw * 2 / 3)
    rows = (len(tiles) + cols - 1) // cols
    head = 128
    out = Image.new("RGB", (W, head + rows * (th + gap) + gap), BG)
    d = ImageDraw.Draw(out)
    d.text((32, 22), title, font=font(42, bold=True), fill=TEXT)
    d.text((32, 80), sub, font=font(24), fill=MUTED)
    for i, (name, labels, caption) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = gap + c * (tw + gap)
        y = head + gap + r * (th + gap)
        img = tile(name, tw, th)
        label_box(img, 12, 12, labels, caption, h=30 if cols > 2 else 34)
        out.paste(img, (x, y))
    out.save(os.path.join(DOCS, out_name), optimize=True)
    print("wrote", out_name, out.size)


def hero(timeline):
    """The animation: viewport plus a strip of the steps, the current one lit."""
    frames = os.path.join(RAW, "_hero")
    shutil.rmtree(frames, ignore_errors=True)
    os.makedirs(frames)
    steps = [(["G"], "Move"), (["X", "Y"], "along X+Y"), (["X", "Y"], "again: the other diagonal"),
             (["Enter"], "done")]

    def step_of(i, item):
        if not item["keys"]:
            return -1
        if item["keys"] == ["G"]:
            return 0
        if item["keys"] == ["Enter"]:
            return 3
        return 2 if item["caption"].startswith("again") else 1

    W, H = 1200, 800
    strip = 110
    for i, item in enumerate(timeline["hero"]):
        img = Image.open(os.path.join(RAW, item["file"])).convert("RGB").crop(CROP).resize((W, H), Image.LANCZOS)
        out = Image.new("RGB", (W, H + strip), BG)
        out.paste(img, (0, 0))
        d = ImageDraw.Draw(out)
        cur = step_of(i, item)
        x = 32
        y = H + 22
        for s, (labels, caption) in enumerate(steps):
            lit = s == cur
            done = s < cur
            x = keys(d, x, y, labels, h=42, dim=not (lit or done))
            f = font(26, bold=lit)
            d.text((x + 12, y + 4), caption, font=f, fill=ORANGE if lit else (TEXT if done else (110, 112, 118)))
            x += int(d.textlength(caption, font=f)) + 12
            if s < len(steps) - 1:
                d.text((x + 6, y + 2), "›", font=font(32, bold=True), fill=MUTED)
                x += 36
        out.save(os.path.join(frames, f"f_{i:03d}.png"))
    graph = ("fps=12,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
             "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "12", "-i",
                    os.path.join(frames, "f_%03d.png"), "-vf", graph, "-loop", "0",
                    os.path.join(DOCS, "hero.gif")], check=True)
    print("wrote hero.gif", os.path.getsize(os.path.join(DOCS, "hero.gif")) // 1024, "KB")


def main():
    with open(os.path.join(RAW, "timeline.json"), encoding="utf-8") as f:
        timeline = json.load(f)
    hero(timeline)
    panel("panel_chords.png", "Two axis keys together",
          "During Move (G). The line takes both axis colours.",
          [("chord_xy", ["X", "Y"], "between X and Y"), ("chord_xz", ["X", "Z"], "between X and Z"),
           ("chord_yz", ["Y", "Z"], "between Y and Z"), ("chord_xyz", ["X", "Y", "Z"], "the cube diagonal")],
          cols=2)
    panel("panel_again.png", "The same keys again",
          "The other diagonal, then free: like pressing X again in Blender.",
          [("cycle_1", ["X", "Y"], "the X+Y diagonal"), ("cycle_2", ["X", "Y"], "again: the other one"),
           ("cycle_3", ["X", "Y"], "a third time: free")], cols=3)
    panel("panel_more.png", "Shift and numbers",
          "Shift locks out the diagonal; typed numbers count along it.",
          [("plane_xy", ["Shift", "X", "Y"], "anywhere but X+Y"),
           ("typed_1", ["X", "Y", "2"], "2 m along the diagonal")], cols=2)


main()
