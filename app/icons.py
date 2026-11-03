"""Hand-drawn symbols for printouts, plus the [tag] parser that picks them.

Put ``[school]`` or ``[!]`` (full name or short name, any capitalisation, spaces/dashes ignored:
``[3D Printer]`` = ``[3dprinter]``) in an email subject or text and that symbol is printed above the
heading.  Unknown tags such as ``[draft]`` are left in the text untouched.

The built-in symbols are not pictures: each one is a handful of strokes in a 100x100 box that a
"sketch pen" draws with a wobble, uneven pressure, overshooting ends and a loose second line, so they
look hand-drawn but come out identical on every print (the wobble is seeded by the symbol's id).
Your own symbols (uploaded or drawn on the Symbols page) are ordinary pictures kept in data/uploads.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw

# --------------------------------------------------------------------------- stroke helpers (100x100 box, y down)


def L(*pts):
    return [tuple(map(float, p)) for p in pts]


def arc(cx, cy, rx, ry=None, a0=0, a1=360, n=40):
    ry = rx if ry is None else ry
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def rrect(x, y, w, h, r=0):
    if not r:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    pts = []
    for cx, cy, a in ((x + w - r, y + r, 270), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
        pts += arc(cx, cy, r, r, a, a + 90, 6)
    return pts + [pts[0]]


def spline(*pts, closed=False, per=10):
    """Catmull-Rom curve through the points: the way a hand rounds a corner."""
    p = [tuple(map(float, q)) for q in pts]
    if closed:
        p = [p[-1]] + p + [p[0], p[1]]
    else:
        p = [p[0]] + p + [p[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for k in range(per):
            t = k / per
            out.append(tuple(0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t * t
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t ** 3) for j in (0, 1)))
    out.append(p[-2] if not closed else out[0])
    return out


def star(cx, cy, ro, ri, n=5, rot=-90):
    pts = []
    for i in range(n * 2):
        r = ro if i % 2 == 0 else ri
        a = math.radians(rot + i * 180 / n)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts + [pts[0]]


def S(pts, fill=None, w=1.0):
    """One stroke: its points, optional fill ('solid' | 'hatch') and pen width multiplier."""
    return {"pts": pts, "fill": fill, "w": w}


def dot(x, y, r=3.5):
    return S(arc(x, y, r, r, 0, 360, 14), "solid", 0.6)


def sparks(*segs):
    return [S(L(*s), w=0.8) for s in segs]


# --------------------------------------------------------------------------- the built-in symbols
# id -> (full name, [short names / aliases], strokes)
BUILTIN: dict[str, tuple[str, list[str], list[dict]]] = {
    "exclamation": ("Exclamation", ["!", "important", "urgent", "alert"], [
        S([(41, 10), (59, 9), (55, 62), (45, 63), (41, 10)], "solid", 0.8), dot(50, 82, 7.5),
        *sparks(((22, 24), (31, 31)), ((78, 24), (69, 31)), ((14, 46), (26, 46)), ((86, 46), (74, 46)))]),
    "question": ("Question", ["?", "query", "ask"], [
        S(spline((30, 32), (34, 16), (50, 9), (66, 16), (70, 31), (62, 45), (51, 54), (50, 65)), w=1.5), dot(50, 84, 7),
        *sparks(((20, 16), (28, 22)), ((80, 16), (72, 22)), ((84, 50), (76, 48)))]),
    "work": ("Work", ["job", "office", "business"], [
        S(rrect(12, 34, 76, 52, 7)), S(spline((36, 34), (36, 24), (42, 19), (58, 19), (64, 24), (64, 34))),
        S(L((12, 56), (88, 56))), S(rrect(43, 50, 14, 12, 2), "solid", 0.6)]),
    "school": ("School", ["study", "uni", "class", "homework"], [
        S(L((50, 20), (93, 40), (50, 60), (7, 40), (50, 20)), "hatch"),
        S(spline((25, 52), (26, 70), (50, 80), (74, 70), (75, 52))),
        S(L((86, 43), (86, 68))), dot(86, 73, 4.5)]),
    "fun": ("Fun", ["party", "play", "smile"], [
        S(arc(50, 60, 32, 31)), dot(38, 52, 4.5), dot(62, 52, 4.5),
        S(spline((31, 64), (40, 77), (50, 80), (60, 77), (69, 64)), w=1.2),
        S(L((37, 33), (50, 8), (63, 33)), "hatch"), dot(50, 8, 3.5),
        *sparks(((10, 22), (19, 28)), ((90, 20), (81, 27)), ((6, 50), (14, 50)), ((94, 46), (86, 48)))]),
    "holiday": ("Holiday", ["vacation", "beach", "trip", "travel"], [
        S(arc(80, 20, 9, 9), "hatch"), *sparks(((80, 4), (80, 7)), ((94, 20), (91, 20)), ((68, 8), (70, 10))),
        S(spline((6, 88), (30, 83), (62, 88), (94, 84))), S(spline((46, 85), (48, 66), (54, 48), (52, 34)), w=1.4),
        S(spline((52, 34), (38, 28), (24, 33), (15, 46))), S(spline((52, 34), (42, 20), (30, 15), (18, 18))),
        S(spline((52, 34), (60, 22), (72, 20), (84, 32))), S(spline((52, 34), (64, 32), (76, 40), (82, 54))),
        dot(48, 39, 3.5), dot(57, 38, 3.5)]),
    "calendar": ("Calendar", ["date", "event", "diary"], [
        S(rrect(12, 18, 76, 70, 6)), S(L((12, 38), (88, 38))), S(L((32, 8), (32, 26)), w=1.4), S(L((68, 8), (68, 26)), w=1.4),
        *[dot(x, y, 2.6) for x in (28, 44, 60, 74) for y in (50, 62, 74) if (x, y) != (60, 62)],
        S(arc(60, 62, 7.5, 7.5, 0, 355, 20), w=1.0)]),
    "3dprinter": ("3D printer", ["3dprint", "3dprinting", "3d", "maker"], [
        S(rrect(12, 8, 76, 10, 2), "hatch"), S(L((18, 18), (18, 84))), S(L((82, 18), (82, 84))),
        S(rrect(8, 82, 84, 9, 2), "solid", 0.8), S(L((18, 34), (82, 34)), w=1.2),
        S(L((44, 34), (56, 34), (52, 47), (48, 47), (44, 34)), "solid"),
        S(rrect(36, 62, 24, 20, 1)), S(L((36, 62), (45, 54), (69, 54), (60, 62))), S(L((60, 62), (69, 54), (69, 74), (60, 82))),
        S(L((36, 68), (60, 68)), w=0.6), S(L((36, 74), (60, 74)), w=0.6)]),
    "accountant": ("Accountant", ["accounts", "accounting", "calculator", "tax", "finance"], [
        S(rrect(22, 6, 56, 88, 7)), S(rrect(31, 15, 38, 20, 2), "hatch"),
        *[dot(x, y, 4.2) for x in (36, 50, 64) for y in (47, 61, 75)], S(L((55, 85), (70, 85)), w=1.2)]),
    "bookwork": ("Bookwork", ["books", "bookkeeping", "ledger", "book", "reading"], [
        S(L((50, 24), (30, 17), (9, 22), (9, 76), (30, 70), (50, 78), (50, 24))),
        S(L((50, 24), (70, 17), (91, 22), (91, 76), (70, 70), (50, 78))),
        *sparks(((17, 34), (41, 38)), ((17, 45), (41, 49)), ((17, 56), (41, 60)), ((59, 38), (83, 34)),
                ((59, 49), (83, 45)), ((59, 60), (83, 56))), S(L((74, 18), (74, 36), (78, 32), (82, 36), (82, 19)), "solid", 0.7)]),
    "home": ("Home", ["house", "chores"], [
        S(L((7, 50), (50, 13), (93, 50))), S(L((20, 42), (20, 88), (80, 88), (80, 42))),
        S(rrect(42, 58, 16, 30, 1), "hatch"), S(rrect(26, 54, 11, 11, 0)), S(rrect(63, 54, 11, 11, 0)),
        S(L((68, 28), (68, 14), (78, 14), (78, 38)))]),
    "shopping": ("Shopping", ["shop", "groceries", "cart", "store"], [
        S(L((5, 12), (18, 12), (34, 66), (82, 66))), S(L((24, 28), (92, 28), (83, 56), (31, 56), (24, 28)), "hatch"),
        S(arc(42, 80, 6.5, 6.5), "solid"), S(arc(74, 80, 6.5, 6.5), "solid")]),
    "money": ("Money", ["pay", "bill", "bills", "cash", "$"], [
        S(arc(50, 50, 40, 40)), S(arc(50, 50, 31, 31, 10, 330), w=0.7),
        S(spline((61, 35), (52, 28), (41, 32), (40, 42), (52, 50), (61, 58), (59, 68), (48, 72), (38, 66)), w=1.3),
        S(L((50, 20), (50, 80)), w=0.9), *sparks(((90, 12), (96, 6)), ((10, 90), (4, 96)))]),
    "phone": ("Phone", ["call", "mobile", "ring"], [
        S(rrect(30, 6, 40, 88, 8)), S(rrect(36, 18, 28, 54, 1), "hatch"), S(arc(50, 83, 4.5, 4.5), "solid"),
        S(L((44, 12), (56, 12)), w=0.8),
        S(spline((14, 30), (10, 42), (14, 54)), w=0.9), S(spline((86, 30), (90, 42), (86, 54)), w=0.9),
        S(spline((5, 24), (0, 42), (5, 60)), w=0.7), S(spline((95, 24), (100, 42), (95, 60)), w=0.7)]),
    "email": ("Email", ["mail", "message", "letter"], [
        S(rrect(8, 24, 84, 56, 5)), S(L((8, 28), (50, 58), (92, 28))), S(L((8, 78), (36, 50))), S(L((92, 78), (64, 50)))]),
    "birthday": ("Birthday", ["cake", "bday"], [
        S(L((6, 88), (94, 88))), S(rrect(14, 60, 72, 28, 3)), S(rrect(27, 40, 46, 20, 3)),
        S(spline((14, 66), (24, 73), (34, 66), (44, 73), (54, 66), (64, 73), (74, 66), (86, 70))),
        S(L((38, 40), (38, 27))), S(L((50, 40), (50, 24))), S(L((62, 40), (62, 27))),
        S(L((38, 25), (34, 18), (38, 9), (42, 18), (38, 25)), "solid", 0.6),
        S(L((50, 22), (46, 15), (50, 6), (54, 15), (50, 22)), "solid", 0.6),
        S(L((62, 25), (58, 18), (62, 9), (66, 18), (62, 25)), "solid", 0.6)]),
    "heart": ("Heart", ["love", "family"], [
        S(spline((50, 88), (20, 56), (12, 34), (25, 16), (41, 19), (50, 32), (60, 18), (76, 17), (89, 33), (80, 56), closed=True), "hatch", 1.1),
        *sparks(((70, 30), (76, 38)))]),
    "star": ("Star", ["favourite", "favorite", "vip"], [
        S(star(50, 54, 44, 19), "hatch"), *sparks(((10, 14), (18, 20)), ((92, 14), (84, 20)), ((50, 2), (50, 8)))]),
    "idea": ("Idea", ["lightbulb", "think"], [
        S(arc(50, 42, 27, 27, 125, 415, 34)), S(L((41, 63), (41, 75))), S(L((59, 63), (59, 75))),
        S(L((38, 75), (62, 75))), S(L((41, 83), (59, 83))), S(L((45, 91), (55, 91))),
        S(spline((42, 54), (46, 42), (50, 50), (54, 42), (58, 54)), w=0.8),
        *sparks(((13, 18), (22, 25)), ((87, 18), (78, 25)), ((5, 44), (16, 44)), ((95, 44), (84, 44)), ((50, 3), (50, 10)))]),
    "health": ("Health", ["doctor", "medical", "dentist", "medicine"], [
        S(L((37, 10), (63, 10), (63, 37), (90, 37), (90, 63), (63, 63), (63, 90), (37, 90), (37, 63), (10, 63), (10, 37), (37, 37), (37, 10)), "hatch")]),
    "car": ("Car", ["drive", "vehicle", "rego", "mechanic"], [
        S(L((6, 64), (10, 50), (28, 46), (38, 28), (64, 28), (76, 46), (92, 52), (94, 66))),
        S(L((6, 64), (16, 64))), S(L((40, 64), (60, 64))), S(L((84, 66), (94, 66))),
        S(L((41, 33), (63, 33), (71, 46), (35, 46), (41, 33)), "hatch"),
        S(arc(28, 66, 11, 11)), S(arc(72, 66, 11, 11)), dot(28, 66, 3.2), dot(72, 66, 3.2)]),
    "pet": ("Pet", ["dog", "cat", "paw", "vet"], [
        S(spline((33, 72), (35, 58), (50, 50), (65, 58), (67, 72), (50, 80), closed=True), "hatch", 1.1),
        S(arc(21, 46, 8, 11), "solid", 0.8), S(arc(40, 27, 8, 11), "solid", 0.8),
        S(arc(60, 27, 8, 11), "solid", 0.8), S(arc(79, 46, 8, 11), "solid", 0.8)]),
    "clock": ("Clock", ["time", "alarm", "deadline", "late"], [
        S(arc(50, 54, 36, 36)), S(arc(50, 54, 29, 29, 20, 330), w=0.6), S(L((50, 54), (50, 32))), S(L((50, 54), (66, 62))),
        S(L((50, 22), (50, 27)), w=0.8), S(L((82, 54), (77, 54)), w=0.8), S(L((50, 86), (50, 81)), w=0.8), S(L((18, 54), (23, 54)), w=0.8),
        dot(50, 54, 3.2), S(arc(22, 22, 10, 8, 180, 360, 10), "hatch"), S(arc(78, 22, 10, 8, 180, 360, 10), "hatch")]),
    "tick": ("Done", ["tick", "check", "complete"], [
        S(spline((14, 54), (36, 76), (60, 46), (88, 14)), w=2.1), S(arc(50, 52, 44, 44, 40, 300, 30), w=0.9)]),
}

DEFAULT_ORDER = list(BUILTIN)

# --------------------------------------------------------------------------- the sketch pen


def _resample(pts, step=1.1):
    out = [pts[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(d / step))
        out += [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(1, n + 1)]
    return out


class _Wobble:
    """Smooth, low-frequency distortion, like a hand that drifts a little."""

    def __init__(self, rnd: random.Random, amp: float):
        self.p = [rnd.uniform(0, 6.28) for _ in range(6)]
        self.f = [rnd.uniform(0.07, 0.13) for _ in range(4)]
        self.amp = amp

    def __call__(self, x, y):
        a, f, p = self.amp, self.f, self.p
        return (x + a * (math.sin(f[0] * y + p[0]) + 0.6 * math.sin(2.3 * f[1] * x + p[1])),
                y + a * (math.sin(f[2] * x + p[2]) + 0.6 * math.sin(2.1 * f[3] * y + p[3])))


def _stroke_points(pts, rnd, wob, closed, extend=True):
    pts = _resample([tuple(p) for p in pts])
    if len(pts) < 2:
        return pts
    if extend:
        if closed:     # a hand never closes a loop exactly: overshoot a little past the start
            pts = pts + pts[1:max(2, min(len(pts) // 12, 8))]
        else:          # lines poke out a hair past their ends
            (x0, y0), (x1, y1) = pts[0], pts[1]
            d = math.hypot(x1 - x0, y1 - y0) or 1
            e = rnd.uniform(0.0, 1.6)
            pts = [(x0 - (x1 - x0) / d * e, y0 - (y1 - y0) / d * e)] + pts
            (x0, y0), (x1, y1) = pts[-2], pts[-1]
            d = math.hypot(x1 - x0, y1 - y0) or 1
            e = rnd.uniform(0.0, 1.6)
            pts = pts + [(x1 + (x1 - x0) / d * e, y1 + (y1 - y0) / d * e)]
    return [wob(x, y) for x, y in pts]


def draw_icon(icon_id: str, size: int = 160, pen: float = 4.3) -> Image.Image:
    """Render a built-in symbol as a black-on-white greyscale image, size x size."""
    strokes = BUILTIN[icon_id][2]
    ss = 3                                         # supersample, then shrink for smooth edges
    S_ = size * ss
    k = S_ / 100.0
    seed = int(hashlib.sha1(icon_id.encode()).hexdigest()[:8], 16)
    rnd = random.Random(seed)
    img = Image.new("L", (S_, S_), 255)
    d = ImageDraw.Draw(img)

    def stamp_path(path, base_w, pressure_phase):
        for i, (x, y) in enumerate(path):
            r = base_w * k / 2 * (0.84 + 0.22 * math.sin(i * 0.11 + pressure_phase))
            d.ellipse([x * k - r, y * k - r, x * k + r, y * k + r], fill=0)

    for st in strokes:
        pts = st["pts"]
        closed = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 0.01 and len(pts) > 3
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        wob = _Wobble(rnd, 1.1)
        main = _stroke_points(pts, rnd, wob, closed)
        if st["fill"] and closed:
            poly = [(x * k, y * k) for x, y in main[: len(_resample([tuple(p) for p in pts]))]]
            if st["fill"] == "solid":
                d.polygon(poly, fill=0)
            else:
                mask = Image.new("L", (S_, S_), 0)
                ImageDraw.Draw(mask).polygon(poly, fill=255)
                hatch = Image.new("L", (S_, S_), 255)
                hd = ImageDraw.Draw(hatch)
                hw = _Wobble(rnd, 0.6)
                gap = 5.2
                c = -100.0
                while c < 200:
                    seg = _resample([(c + t * 1.0, t * 1.0) for t in range(-10, 130, 4)], 1.5)
                    pts2 = [hw(x, y) for x, y in seg]
                    hd.line([(x * k, y * k) for x, y in pts2], fill=0, width=max(1, int(pen * 0.5 * k)))
                    c += gap
                inside = Image.composite(hatch, Image.new("L", (S_, S_), 255), mask)
                img.paste(ImageChops.darker(img, inside))
                d = ImageDraw.Draw(img)
        w = pen * st["w"]
        stamp_path(main, w, rnd.uniform(0, 6.28))
        if length > 28 and st["fill"] != "solid":   # loose second pass = the sketchy feel
            second = _stroke_points(pts, rnd, _Wobble(rnd, 1.2), closed)
            stamp_path(second, w * 0.5, rnd.uniform(0, 6.28))
    return img.resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------- tags
_TAG = re.compile(r"\[([^\[\]\n]{1,30})\]")


def norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s.strip().lower())


def catalogue(cfg: dict) -> list[dict]:
    """All symbols (built-in + your own) with the tag names that select them."""
    ic = cfg.get("icons", {})
    extra = ic.get("aliases", {})
    out = []
    for c in ic.get("custom", []):
        out.append({"id": c["id"], "name": c.get("name") or c["id"], "builtin": False, "enabled": True,
                    "tags": [c.get("name", "")] + list(c.get("tags", [])), "file": c.get("file", ""), "mode": c.get("mode", "line")})
    for iid, (name, short, _) in BUILTIN.items():
        out.append({"id": iid, "name": name, "builtin": True, "enabled": iid not in ic.get("disabled", []),
                    "tags": [name] + short + list(extra.get(iid, [])), "short": short, "extra": list(extra.get(iid, []))})
    return out


def lookup(cfg: dict) -> dict[str, str]:
    table: dict[str, str] = {}
    for item in reversed(catalogue(cfg)):          # custom (listed first) wins over built-in on a clash
        if item["enabled"]:
            for t in item["tags"]:
                if t and norm(t):
                    table[norm(t)] = item["id"]
    return table


def extract_tags(text: str, cfg: dict) -> tuple[str, list[str]]:
    """Remove recognised [tags] from the text; return (clean text, symbol ids in order, no repeats)."""
    if "[" not in (text or ""):
        return text, []
    table = lookup(cfg)
    found: list[str] = []

    def take(m):
        iid = table.get(norm(m.group(1)))
        if iid is None:
            return m.group(0)                      # [draft], [1] ... stay as normal text
        if iid not in found:
            found.append(iid)
        return ""

    clean = _TAG.sub(take, text)
    if found:
        clean = re.sub(r"[ \t]{2,}", " ", clean)
        clean = re.sub(r"[ \t]+\n", "\n", clean)
        clean = re.sub(r"\n[ \t]+", "\n", clean).strip(" \t")
    return clean, found


def is_tags_only(line: str, cfg: dict) -> bool:
    clean, found = extract_tags(line, cfg)
    return bool(found) and not clean.strip()


# --------------------------------------------------------------------------- images for the printer
@lru_cache(maxsize=256)
def _builtin_image(icon_id: str, size: int) -> Image.Image:
    return draw_icon(icon_id, size)


def icon_image(icon_id: str, size: int, cfg: dict) -> Image.Image | None:
    """Greyscale image of one symbol (white background), ``size`` px square, or None if unknown."""
    from .render import fit_image, load_upload     # late import: render imports this module
    if icon_id in BUILTIN:
        return _builtin_image(icon_id, size).copy()
    for c in cfg.get("icons", {}).get("custom", []):
        if c["id"] == icon_id:
            im = load_upload(c.get("file", ""))
            if im is None:
                return None
            return fit_image(im, size, size, "floyd" if c.get("mode") == "photo" else "threshold", 1.0)
    return None


def render_sheet(cfg: dict, width: int = 576) -> Image.Image:
    """A printable cheat-sheet: every enabled symbol with the [tags] that print it."""
    from .render import load_font, text_block
    items = [c for c in catalogue(cfg) if c["enabled"]]
    cols, margin = 3, 14
    cell_w = (width - 2 * margin) // cols
    isize = cell_w - 36
    title_font, _ = load_font("bungee", 62)
    blocks = []
    for it in items:
        pic = icon_image(it["id"], isize, cfg)
        if pic is None:
            continue
        tags = [t for t in it["tags"] if t]
        cap = text_block(f"[{tags[0].lower()}]", "courier-prime", 22, cell_w - 6, bold=True, align="center")
        more = text_block(" ".join(f"[{t.lower()}]" for t in tags[1:4]), "courier-prime", 15, cell_w - 6, align="center") if len(tags) > 1 else None
        cell = Image.new("L", (cell_w, isize + cap.height + (more.height if more else 0) + 22), 255)
        cell.paste(pic, ((cell_w - isize) // 2, 4))
        cell.paste(cap, (3, isize + 8))
        if more:
            cell.paste(more, (3, isize + 8 + cap.height))
        blocks.append(cell)
    rows = [blocks[i:i + cols] for i in range(0, len(blocks), cols)]
    top = 110
    height = top + sum(max(c.height for c in r) + 8 for r in rows) + 60
    sheet = Image.new("L", (width, height), 255)
    d = ImageDraw.Draw(sheet)
    d.rectangle([0, 0, width - 1, height - 1], outline=0, width=3)
    d.text((width // 2, 16), "SYMBOLS", font=title_font, fill=255, anchor="ma", stroke_width=3, stroke_fill=0)
    sub = text_block("Put the name in [square brackets] in your email", "courier-prime", 19, width - 40, align="center")
    sheet.paste(sub, (20, 84))
    y = top + 14
    for r in rows:
        for i, c in enumerate(r):
            sheet.paste(c, (margin + i * cell_w, y))
        y += max(c.height for c in r) + 8
    return sheet.point(lambda p: 255 if p >= 160 else 0).convert("1")


# --------------------------------------------------------------------------- numbered list + printable/shareable chart
def numbered(cfg: dict) -> list[dict]:
    """Every symbol with a stable number: the built-ins are 1-24 (never renumbered), your own follow from 25."""
    ic = cfg.get("icons", {})
    out = [{"n": n, "id": iid, "name": name, "enabled": iid not in ic.get("disabled", []), "builtin": True}
           for n, (iid, (name, _short, _)) in enumerate(BUILTIN.items(), 1)]
    for n, c in enumerate(ic.get("custom", []), len(BUILTIN) + 1):
        out.append({"n": n, "id": c["id"], "name": c.get("name") or c["id"], "enabled": True, "builtin": False})
    return out


def symbol_from_value(value: str, cfg: dict) -> str | None:
    """A symbol picked in a form: "7", "7 - School", "School" or "[school]" -> its id (None if unknown)."""
    v = re.sub(r"^\[|\]$", "", (value or "").strip()).strip()
    if not v:
        return None
    m = re.match(r"^(\d{1,3})\b", v)                    # "3d printer" has no word boundary after the 3, so it isn't a number
    if m:
        item = next((x for x in numbered(cfg) if x["n"] == int(m.group(1)) and x["enabled"]), None)
        if item:
            return item["id"]
    _, ids = extract_tags(f"[{v}]", cfg)
    return ids[0] if ids else None


def render_chart(cfg: dict, cols: int = 6, cell_w: int = 150, icon_px: int = 96) -> Image.Image:
    """One picture of all symbols with their numbers and names (for the Google Form, a fridge, a README)."""
    from .render import load_font
    items = [x for x in numbered(cfg) if x["enabled"]]
    rows = -(-len(items) // cols)
    cell_h = icon_px + 58
    top = 58
    W, H = cols * cell_w + 20, top + rows * cell_h + 14
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    big, _ = load_font("bungee", 30)
    name_f, _ = load_font("courier-prime", 17, True)
    num_f, _ = load_font("courier-prime", 18, True)
    sub_f, _ = load_font("courier-prime", 13)
    d.text((12, 8), "SYMBOLS", font=big, fill=255, stroke_width=2, stroke_fill=0)
    d.text((W - 12, 40), "pick one by its number or its name", font=sub_f, fill=90, anchor="rs")
    for i, it in enumerate(items):
        x0, y0 = 10 + (i % cols) * cell_w, top + (i // cols) * cell_h
        d.rectangle([x0, y0, x0 + cell_w - 1, y0 + cell_h - 1], outline=215)
        pic = icon_image(it["id"], icon_px, cfg)
        if pic is not None:
            img.paste(pic, (x0 + (cell_w - icon_px) // 2, y0 + 8))
        cx, cy = x0 + 20, y0 + 20
        d.ellipse([cx - 15, cy - 15, cx + 15, cy + 15], fill=0)
        d.text((cx, cy + 1), str(it["n"]), font=num_f, fill=255, anchor="mm")
        d.text((x0 + cell_w // 2, y0 + icon_px + 30), it["name"][:16], font=name_f, fill=0, anchor="mm")
    return img.point(lambda p: 255 if p > 235 else p)
