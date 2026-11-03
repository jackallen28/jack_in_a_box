"""Draw a Card onto a 1-bit bitmap exactly as the printer will print it.

The TM-T82II prints at 203 dpi, 576 dots across (72 mm) on 80 mm paper.  The
web-page preview uses this same function, so what you see is what prints.
"""
from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from .config import APP_DIR, DATA_DIR
from .models import Card, Section

DPI = 203
FONT_DIR = APP_DIR / "fonts"
UPLOAD_DIR = DATA_DIR / "uploads"

# id -> (label, regular file, bold file)
FONTS = {
    "bungee": ("Bungee - chunky sign", "Bungee-Regular.ttf", None),
    "bungee-shade": ("Bungee Shade - 3D sign", "BungeeShade-Regular.ttf", None),
    "monoton": ("Monoton - neon lines", "Monoton-Regular.ttf", None),
    "press-start": ("Press Start 2P - arcade pixels", "PressStart2P-Regular.ttf", None),
    "shrikhand": ("Shrikhand - retro script", "Shrikhand-Regular.ttf", None),
    "courier-prime": ("Courier Prime - typewriter", "CourierPrime-Regular.ttf", "CourierPrime-Bold.ttf"),
    "dejavu-sans": ("DejaVu Sans - clean", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
}
_SYSTEM_DIRS = [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu"),
                Path("/usr/share/fonts/TTF")]


def mm(v: float) -> int:
    return int(round(float(v) * DPI / 25.4))


def pt(v: float) -> int:
    return max(4, int(round(float(v) * DPI / 72.0)))


def _find_font(fname: str) -> Path | None:
    for d in [FONT_DIR, *_SYSTEM_DIRS]:
        if (d / fname).exists():
            return d / fname
    return None


@lru_cache(maxsize=128)
def load_font(font_id: str, size: int, bold: bool = False) -> tuple[ImageFont.FreeTypeFont, bool]:
    """Return (font, needs_fake_bold)."""
    label, reg, bld = FONTS.get(font_id, FONTS["courier-prime"])
    path = _find_font(bld) if (bold and bld) else None
    fake = bold and path is None
    path = path or _find_font(reg) or _find_font(FONTS["courier-prime"][1])
    if path is None:
        return ImageFont.load_default(size), fake
    return ImageFont.truetype(str(path), size), fake


def line_height(font) -> int:
    asc, desc = font.getmetrics()
    return int((asc + desc) * 1.1)


def wrap(font, text: str, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for w in para.split(" "):
            trial = f"{cur} {w}" if cur else w
            if font.getlength(trial) <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            while font.getlength(w) > max_w and len(w) > 1:  # unbreakable long word
                k = len(w)
                while k > 1 and font.getlength(w[:k]) > max_w:
                    k -= 1
                lines.append(w[:k])
                w = w[k:]
            cur = w
        lines.append(cur)
    return lines


def _x_for(align: str, avail: int, w: float) -> int:
    if align == "center":
        return int((avail - w) / 2)
    if align == "right":
        return int(avail - w)
    return 0


def draw_text(d: ImageDraw.ImageDraw, xy, text, font, fake_bold=False, fill=0, **kw):
    x, y = xy
    d.text((x, y), text, font=font, fill=fill, anchor="la", **kw)
    if fake_bold:
        d.text((x + 1, y), text, font=font, fill=fill, anchor="la", **kw)


def text_block(text: str, font_id: str, size: int, width: int, *, bold=False, align="left",
               fill=0) -> Image.Image:
    font, fake = load_font(font_id, size, bold)
    lines = wrap(font, text, width - (1 if fake else 0))
    lh = line_height(font)
    img = Image.new("L", (width, max(lh, lh * len(lines))), 255)
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        w = font.getlength(l)
        draw_text(d, (_x_for(align, width, w), i * lh), l, font, fake, fill)
    return img


def fill_vars(text: str, card: Card) -> str:
    out = text or ""
    for k, v in {**card.vars, "kind": card.kind, "source": card.source}.items():
        out = out.replace("{" + k + "}", str(v))
    return out


# ------------------------------------------------------------------ images

def _dither(im: Image.Image, mode: str, contrast: float = 1.0) -> Image.Image:
    g = ImageOps.autocontrast(im.convert("L"), cutoff=1)
    if contrast != 1.0:
        g = ImageEnhance.Contrast(g).enhance(contrast)
    g = g.point(lambda p: int(255 * (p / 255) ** 0.85))   # thermal paper prints dark: lift mids
    if mode == "threshold":
        return g.point(lambda p: 255 if p > 128 else 0).convert("L")
    return g.convert("1").convert("L")                    # Floyd-Steinberg


def fit_image(im: Image.Image, max_w: int, max_h: int, mode: str, contrast: float = 1.0,
              align: str = "center") -> Image.Image:
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, "white")
        bg.alpha_composite(im)
        im = bg
    scale = min(max_w / im.width, max_h / im.height)
    new = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
    im = im.convert("L").resize(new, Image.LANCZOS)
    im = _dither(im, mode, contrast)
    canvas = Image.new("L", (max_w, im.height), 255)
    canvas.paste(im, (_x_for(align, max_w, im.width), 0))
    return canvas


def load_upload(name: str) -> Image.Image | None:
    if not name or "/" in name or "\\" in name:
        return None
    p = UPLOAD_DIR / name
    try:
        im = Image.open(p)
        im.load()
        return im
    except Exception:
        return None


# ------------------------------------------------------------------ blocks

def block_title(card: Card, tpl: dict, w: int) -> Image.Image | None:
    c = tpl["title"]
    text = fill_vars(card.vars.get("_title") or c.get("text", ""), card).strip()
    if not c.get("enabled", True) or not text:
        return None
    size = pt(c.get("size_pt", 30))
    style = c.get("style", "outline")
    pad = 6
    while True:
        font, _ = load_font(c.get("font", "bungee"), size)
        sw = max(2, size // 22) if style in ("outline", "shadow") else 0
        width = font.getlength(text) + 2 * sw + (6 if style == "shadow" else 0)
        if width <= w - (2 * pad if style == "inverse" else 0) or size <= 14:
            break
        size -= 2
    asc, desc = font.getmetrics()
    h = asc + desc + 2 * sw + (6 if style == "shadow" else 0) + (2 * pad if style == "inverse" else 0)
    sub = fill_vars(c.get("subtitle", ""), card).strip()
    sub_img = text_block(sub, "courier-prime", pt(11), w, bold=True, align=c.get("align", "center")) if sub else None
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    x = _x_for(c.get("align", "center"), w, width) + sw
    y = sw + (pad if style == "inverse" else 0)
    if style == "inverse":
        d.rectangle([0, 0, w - 1, h - 1], fill=0)
        d.text((x, y), text, font=font, fill=255, anchor="la")
    elif style == "outline":
        d.text((x, y), text, font=font, fill=255, anchor="la", stroke_width=sw, stroke_fill=0)
    elif style == "shadow":
        d.text((x + 6, y + 6), text, font=font, fill=0, anchor="la", stroke_width=sw, stroke_fill=0)
        d.text((x, y), text, font=font, fill=255, anchor="la", stroke_width=sw, stroke_fill=0)
    else:
        d.text((x, y), text, font=font, fill=0, anchor="la")
    if sub_img is not None:
        out = Image.new("L", (w, img.height + 4 + sub_img.height), 255)
        out.paste(img, (0, 0))
        out.paste(sub_img, (0, img.height + 4))
        return out
    return img


def block_graphic(card: Card, tpl: dict, w: int) -> Image.Image | None:
    c = tpl["graphic"]
    if not c.get("enabled") or not c.get("file"):
        return None
    im = load_upload(c["file"])
    if im is None:
        return None
    return fit_image(im, w, mm(c.get("max_height_mm", 25)), c.get("dither", "floyd"), 1.1, c.get("align", "center"))


def block_heading(card: Card, tpl: dict, w: int) -> Image.Image | None:
    c = tpl["heading"]
    if not c.get("enabled", True) or not card.heading.strip():
        return None
    parts = [text_block(card.heading.strip(), c.get("font", "courier-prime"), pt(c.get("size_pt", 15)), w,
                        bold=c.get("bold", True), align=c.get("align", "left"))]
    if c.get("show_subline", True) and card.subline:
        parts.append(text_block(card.subline, "courier-prime", pt(9), w, align=c.get("align", "left")))
    return stack(parts, w, 2)


def _section_image(sec: Section, tpl: dict, w: int) -> Image.Image | None:
    b = tpl["body"]
    if sec.key in b.get("hide_sections", []):
        return None
    if not sec.text.strip() and not sec.items:
        return None
    font_id, size = b.get("font", "courier-prime"), pt(b.get("size_pt", 11))
    parts: list[Image.Image] = []
    label = sec.label or (b.get("labels") or {}).get(sec.key, "")
    if b.get("show_labels", True) and label:
        parts.append(text_block(label.upper(), font_id, max(14, int(size * 0.72)), w, bold=True))
    if sec.text.strip():
        s = int(size * 1.3) if sec.strong else size
        parts.append(text_block(sec.text.strip(), font_id, s, w, bold=sec.strong, align=b.get("align", "left")))
    if sec.items:
        font, fake = load_font(font_id, size)
        lh = line_height(font)
        box = int(lh * 0.55)
        indent = box + int(size * 0.5)
        rows = []
        for it in sec.items:
            tb = text_block(it, font_id, size, w - indent)
            row = Image.new("L", (w, max(tb.height, lh)), 255)
            d = ImageDraw.Draw(row)
            top = int((lh - box) / 2)
            if b.get("checkbox_actions", True):
                d.rectangle([1, top, box, top + box - 1], outline=0, width=2)
            else:
                d.rectangle([2, top + box // 2 - 1, box - 2, top + box // 2 + 1], fill=0)
            row.paste(tb, (indent, 0))
            rows.append(row)
        parts.append(stack(rows, w, int(size * 0.3)))
    return stack(parts, w, 4)


def _dotted(w: int, gap: int = 8) -> Image.Image:
    img = Image.new("L", (w, 3), 255)
    d = ImageDraw.Draw(img)
    for x in range(0, w, gap):
        d.rectangle([x, 1, x + 2, 2], fill=0)
    return img


def block_body(card: Card, tpl: dict, w: int) -> Image.Image | None:
    b = tpl["body"]
    boxed = b.get("box", "single")
    line_w = max(1, pt(b.get("box_width_pt", 1)) // 3)
    inner_pad = mm(2.5) if boxed != "none" else 0
    inner_w = w - 2 * (inner_pad + (line_w if boxed != "none" else 0))
    secs = [i for i in (_section_image(s, tpl, inner_w) for s in card.sections) if i is not None]
    marker = None
    if card.priority and b.get("priority_marker", True):
        f, _ = load_font(b.get("font", "courier-prime"), pt(10), True)
        txt = "! URGENT !"
        marker = Image.new("L", (int(f.getlength(txt)) + 16, line_height(f) + 4), 0)
        ImageDraw.Draw(marker).text((8, 2), txt, font=f, fill=255, anchor="la")
    if not secs and marker is None:
        return None

    def wrap_box(content: Image.Image) -> Image.Image:
        if boxed == "none":
            return content
        W, H = w, content.height + 2 * inner_pad + 2 * line_w
        img = Image.new("L", (W, H), 255)
        d = ImageDraw.Draw(img)
        r = mm(b.get("radius_mm", 2))
        d.rounded_rectangle([0, 0, W - 1, H - 1], radius=r, outline=0, width=line_w) if r else \
            d.rectangle([0, 0, W - 1, H - 1], outline=0, width=line_w)
        img.paste(content, (inner_pad + line_w, inner_pad + line_w))
        return img

    gap = mm(2)
    if boxed == "separate":
        pieces = ([marker] if marker else []) + secs
        boxes = [wrap_box(p if p.width == inner_w else _pad_to(p, inner_w)) for p in pieces]
        return stack(boxes, w, gap)
    items: list[Image.Image] = []
    if marker:
        items.append(_pad_to(marker, inner_w, "left"))
    for i, s in enumerate(secs):
        if items:
            items.append(_dotted(inner_w) if boxed == "single" else Image.new("L", (inner_w, 2), 255))
        items.append(s)
    return wrap_box(stack(items, inner_w, gap))


def _pad_to(img: Image.Image, w: int, align: str = "left") -> Image.Image:
    if img.width >= w:
        return img
    out = Image.new("L", (w, img.height), 255)
    out.paste(img, (_x_for(align, w, img.width), 0))
    return out


def block_images(card: Card, tpl: dict, w: int) -> Image.Image | None:
    c = tpl["images"]
    if not c.get("enabled", True) or not card.images:
        return None
    pics = []
    for im in card.images:
        pic = fit_image(im, w - (8 if c.get("boxed") else 0), mm(c.get("max_height_mm", 100)),
                        c.get("dither", "floyd"), c.get("contrast", 1.15))
        if c.get("boxed"):
            framed = Image.new("L", (w, pic.height + 8), 255)
            framed.paste(pic, (4, 4))
            ImageDraw.Draw(framed).rectangle([0, 0, w - 1, framed.height - 1], outline=0, width=2)
            pic = framed
        pics.append(pic)
    return stack(pics, w, mm(2))


def block_footer(card: Card, tpl: dict, w: int) -> Image.Image | None:
    c = tpl["footer"]
    if not c.get("enabled"):
        return None
    lines = []
    if c.get("text"):
        lines.append(fill_vars(c["text"], card))
    if c.get("show_timestamp"):
        lines.append((card.created or datetime.now()).strftime("%a %d %b %Y  %I:%M %p").replace(" 0", " "))
    if not lines:
        return None
    return text_block("\n".join(lines), "courier-prime", pt(c.get("size_pt", 8)), w, align=c.get("align", "center"))


def block_icons(card: Card, tpl: dict, w: int) -> Image.Image | None:
    from . import icons as icon_lib
    c = tpl.get("icons", {})
    if not c.get("enabled", True) or not card.icons:
        return None
    ids = card.icons[: int(c.get("max", 6))]
    gap = mm(c.get("gap_mm", 3))
    size = min(mm(c.get("size_mm", 20)), (w - gap * (len(ids) - 1)) // len(ids))
    pics = [p for p in (icon_lib.icon_image(i, size, {"icons": card.icon_cfg}) for i in ids) if p is not None]
    if not pics:
        return None
    row = Image.new("L", (sum(p.width for p in pics) + gap * (len(pics) - 1), max(p.height for p in pics)), 255)
    x = 0
    for p in pics:
        row.paste(p, (x, (row.height - p.height) // 2))
        x += p.width + gap
    out = Image.new("L", (w, row.height), 255)
    out.paste(row, (_x_for(c.get("align", "center"), w, row.width), 0))
    return out


def block_divider(card: Card, tpl: dict, w: int) -> Image.Image | None:
    return _dotted(w)


BLOCKS = {"title": block_title, "icons": block_icons, "graphic": block_graphic, "heading": block_heading,
          "body": block_body, "images": block_images, "footer": block_footer, "divider": block_divider}


def stack(parts: list[Image.Image], w: int, gap: int) -> Image.Image:
    parts = [p for p in parts if p is not None]
    if not parts:
        return Image.new("L", (w, 1), 255)
    h = sum(p.height for p in parts) + gap * (len(parts) - 1)
    out = Image.new("L", (w, h), 255)
    y = 0
    for p in parts:
        out.paste(p, (_x_for("center", w, p.width) if p.width < w else 0, y))
        y += p.height + gap
    return out


# ------------------------------------------------------------------ card

def render_card(card: Card, tpl: dict, printer_cfg: dict | None = None, icons_cfg: dict | None = None) -> Image.Image:
    """Return a 1-bit image, ``width_dots`` wide, ready for the printer."""
    if icons_cfg is not None:
        card.icon_cfg = icons_cfg
    pc = printer_cfg or {}
    paper_w = int(pc.get("width_dots", 576))
    card_w = min(mm(tpl.get("width_mm", 72)), paper_w)
    bcfg = tpl.get("border", {})
    bw = max(1, int(round(float(bcfg.get("width_pt", 1)) * DPI / 72))) if bcfg.get("enabled", True) else 0
    pad = mm(tpl.get("padding_mm", 4))
    gap = mm(tpl.get("gap_mm", 3))
    inner_w = card_w - 2 * (bw + pad)

    order = list(tpl.get("order", []))
    if card.icons and "icons" not in order:   # templates saved before symbols existed
        order.insert(order.index("title") + 1 if "title" in order else 0, "icons")
    rendered = []
    for name in order:
        fn = BLOCKS.get(name)
        if fn is None:
            continue
        img = fn(card, tpl, inner_w)
        if img is not None:
            rendered.append(img)
    content = stack(rendered, inner_w, gap)

    min_h = math.ceil(float(tpl.get("min_height_mm", 80)) * DPI / 25.4)   # round UP so 80 mm is never 79.9
    max_h = mm(tpl.get("max_height_mm", 700))
    card_h = min(max(min_h, content.height + 2 * (bw + pad)), max_h)
    face = Image.new("L", (card_w, card_h), 255)
    face.paste(content.crop((0, 0, inner_w, min(content.height, card_h - 2 * (bw + pad)))), (bw + pad, bw + pad))
    if bw:
        d = ImageDraw.Draw(face)
        r = mm(bcfg.get("radius_mm", 0))
        box = [0, 0, card_w - 1, card_h - 1]
        d.rounded_rectangle(box, radius=r, outline=0, width=bw) if r else d.rectangle(box, outline=0, width=bw)

    # White space above/below the whole printout (outside the border). Per template; 0-60 mm.
    mt = mm(min(60, max(0, float(tpl.get("margin_top_mm", 0) or 0))))
    mb = mm(min(60, max(0, float(tpl.get("margin_bottom_mm", 0) or 0))))
    paper = Image.new("L", (paper_w, mt + card_h + mb), 255)
    paper.paste(face, ((paper_w - card_w) // 2, mt))
    thr = int(pc.get("threshold", 160))
    return paper.point(lambda p: 255 if p >= thr else 0).convert("1")
