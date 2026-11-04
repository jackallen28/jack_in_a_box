"""Build app/pinkpanther_episodes.json from a saved copy of the Pink Panther Wiki episode guide (print the Fandom page "The Pink Panther
Show" to PDF).  Usage:   python tools/pinkpanther_from_pdf.py guide.pdf [output.json]

The guide is a set of season tables: "Ep #" then three shorts per TV episode (Pink Panther short 1, a middle short, Pink Panther short 2).
The columns are found from where the text sits on the page, so no layout is hard-coded; the same short can be in several seasons (reruns).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pypdfium2 as pdfium

SEASON_LEFT = re.compile(r"^Season\s+(\d+(?:\s*[–-]\s*\d+)?)\s*:\s*(.+?)\s*$")
SEASON_RIGHT = re.compile(r"^([A-Za-z]+),(\d{4}(?:[–-]\d{4})?)\)$")                    # "NBC,1969–1970)" once the stray spaces are gone
FORMAT = re.compile(r"^Format:\s*(.+)$")


def words_by_line(page) -> list[list[tuple[float, float, str]]]:
    """[(x_left, x_right, word)] for each visual line, top to bottom."""
    tp = page.get_textpage()
    text = tp.get_text_range()
    chars = []
    for i in range(min(tp.count_chars(), len(text))):
        ch = text[i]
        if ch.isspace():
            continue                                                       # spaces have odd boxes; word breaks come from the gaps instead
        l, b, r, t = tp.get_charbox(i)
        chars.append((ch, l, r, (b + t) / 2, t - b))
    chars.sort(key=lambda c: -c[3])                                       # top of the page first
    lines: list[list] = []
    for c in chars:
        if lines and abs(lines[-1][0] - c[3]) <= 6:                       # (a comma sits lower than the letters beside it)
            lines[-1][1].append(c)
        else:
            lines.append([c[3], [c]])
    out = []
    for _y, cs in lines:
        cs.sort(key=lambda c: c[1])
        words, cur, last_r = [], [], None
        for ch, l, r, _y2, h in cs:
            if cur and last_r is not None and l - last_r > max(1.4, 0.19 * h):      # a gap wider than ~a fifth of the letter height = a space
                words.append((cur[0][1], cur[-1][2], "".join(c[0] for c in cur)))
                cur = []
            cur.append((ch, l, r))
            last_r = r
        if cur:
            words.append((cur[0][1], cur[-1][2], "".join(c[0] for c in cur)))
        out.append(words)
    return out


def cells(words, gap: float = 9.0) -> list[tuple[float, str]]:
    """Group a line's words into table cells: a wide gap starts a new cell.  [(x_left, text)]."""
    out: list[list] = []
    for l, r, w in words:
        if out and l - out[-1][2] <= gap:
            out[-1][1].append(w)
            out[-1][2] = r
        else:
            out.append([l, [w], r])
    return [(c[0], " ".join(c[1])) for c in out]


def parse(pdf_path: str) -> dict:
    pdf = pdfium.PdfDocument(pdf_path)
    seasons: list[dict] = []
    lefts: list[float] | None = None
    current: dict | None = None
    for p in range(len(pdf)):
        for words in words_by_line(pdf[p]):
            line = " ".join(w for _, _, w in words)
            if line.startswith("Season") and "(" in line:
                left, _, right = line.rpartition("(")
                ml, mr = SEASON_LEFT.match(left), SEASON_RIGHT.match(re.sub(r"\s+", "", right))      # the parenthesis has stray spaces: "N BC , 1 969"
                if ml and mr:
                    current = {"season": re.sub(r"\s+", "", ml.group(1)).replace("-", "–"), "show": ml.group(2).strip(), "network": mr.group(1),
                               "years": mr.group(2).replace("-", "–"), "format": "", "episodes": []}
                    seasons.append(current)
                    lefts = None
                    continue
            if current is None:
                continue
            fm = FORMAT.match(line)
            if fm and not current["episodes"]:
                current["format"] = fm.group(1).strip()
                continue
            if not re.match(r"^\d{1,2}\b", line):
                continue                                                   # headers, advertisements, page furniture
            cs = cells(words)
            if lefts is None:
                if len(cs) != 4:
                    raise SystemExit(f"Can't find the 3 title columns in season {current['season']} (row: {line!r}, found {len(cs)} cells)")
                lefts = [c[0] for c in cs]
            # assign every word to the column whose left edge it is at or after
            cols: list[list[str]] = [[] for _ in lefts]
            for l, r, w in words:
                j = max((k for k, x in enumerate(lefts) if l >= x - 2.0), default=0)
                cols[j].append(w)
            texts = [" ".join(c).strip() for c in cols]
            texts[0] = texts[0].replace(" ", "")                              # "1 0" -> "10": digits can have a stray gap too
            texts[1:] = [re.sub(r"\s+([!?,.:;])", r"\1", t) for t in texts[1:]]      # "In ! The" -> "In! The"
            if not texts[0].isdigit() or not all(texts[1:]):
                raise SystemExit(f"Odd row in season {current['season']}: {texts!r}")
            current["episodes"].append({"episode": int(texts[0]), "shorts": texts[1:]})
    return {"source": "The Pink Panther Wiki (Fandom): The Pink Panther Show episode guide", "seasons": seasons}


def flatten(data: dict) -> list[dict]:
    """One entry per short title, with everywhere it was shown (first showing first)."""
    shorts: dict[str, dict] = {}
    for s in data["seasons"]:
        for ep in s["episodes"]:
            for slot, title in enumerate(ep["shorts"], 1):
                key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
                e = shorts.setdefault(key, {"title": title, "slot": "middle" if slot == 2 else "pink panther", "appearances": []})
                e["appearances"].append({"season": s["season"], "show": s["show"], "years": s["years"], "network": s["network"], "episode": ep["episode"]})
    return list(shorts.values())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    data = parse(sys.argv[1])
    data["shorts"] = flatten(data)
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "app" / "pinkpanther_episodes.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    eps = sum(len(s["episodes"]) for s in data["seasons"])
    print(f"{len(data['seasons'])} seasons, {eps} TV episodes, {len(data['shorts'])} different shorts -> {out}")
