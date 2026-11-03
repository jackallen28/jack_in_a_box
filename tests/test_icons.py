import io
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_core import REF, mk  # noqa: E402
from app import icons  # noqa: E402
from app.config import ConfigStore, normalise  # noqa: E402
from app.mailparse import parse  # noqa: E402
from app.models import Card, Section  # noqa: E402
from app.pipeline import build_cards  # noqa: E402
from app.render import render_card  # noqa: E402
from app.textproc import build_items  # noqa: E402

CFG = normalise({})


def ids(text, cfg=CFG):
    return icons.extract_tags(text, cfg)


# ---------------------------------------------------------------- tag parsing
@pytest.mark.parametrize("tag,expected", [
    ("[school]", "school"), ("[SCHOOL]", "school"), ("[ School ]", "school"), ("[uni]", "school"),
    ("[!]", "exclamation"), ("[?]", "question"), ("[exclamation]", "exclamation"),
    ("[3D Printer]", "3dprinter"), ("[3d-printer]", "3dprinter"), ("[3dprinter]", "3dprinter"), ("[3d]", "3dprinter"),
    ("[work]", "work"), ("[holiday]", "holiday"), ("[calendar]", "calendar"), ("[accountant]", "accountant"),
    ("[bookwork]", "bookwork"), ("[fun]", "fun"),
])
def test_tag_names_full_and_short(tag, expected):
    text, found = ids(f"{tag} hello")
    assert found == [expected] and text == "hello"


def test_unknown_tags_stay_as_text_and_known_ones_are_removed():
    text, found = ids("fix the [draft] report [school] see [1]")
    assert found == ["school"] and text == "fix the [draft] report see [1]"
    assert ids("no tags here") == ("no tags here", [])


def test_multiple_tags_order_and_duplicates():
    assert ids("[work][!] [work] [?]")[1] == ["work", "exclamation", "question"]


def test_extra_names_disabled_and_custom_override():
    cfg = normalise({"icons": {"aliases": {"school": ["sch"]}, "disabled": ["fun"],
                               "custom": [{"id": "bike01", "name": "Bike", "tags": ["cycling", "work"], "file": "x.png"}]}})
    assert ids("[sch]", cfg)[1] == ["school"]
    assert ids("[fun]", cfg) == ("[fun]", [])                         # switched off -> plain text
    assert ids("[Bike] [cycling]", cfg)[1] == ["bike01"]
    assert ids("[work]", cfg)[1] == ["bike01"]                        # your symbol beats the built-in on a clash


# ---------------------------------------------------------------- where tags apply
def test_subject_tags_apply_to_every_reminder_and_are_removed_from_the_heading():
    cards, _ = build_cards(parse(mk("[school] Chores [!]", "wash dishes and walk the dog")), CFG)
    assert len(cards) == 2 and all(c.icons == ["school", "exclamation"] and c.heading == "Chores" for c in cards)


def test_tag_on_a_tasks_own_line_applies_only_to_it():
    cards, _ = build_cards(parse(mk("", "[fun] wash dishes\nwalk the dog [work]\ncall mum")), CFG)
    by = {c.sections[0].text: c.icons for c in cards}
    assert by == {"Wash dishes": ["fun"], "Walk the dog": ["work"], "Call mum": []}


def test_first_line_of_only_tags_applies_to_all_and_later_tag_lines_to_the_next():
    cards, _ = build_cards(parse(mk("", "[holiday]\nwash dishes\n[!]\nwalk the dog\ncall mum")), CFG)
    by = {c.sections[0].text: c.icons for c in cards}
    assert by == {"Wash dishes": ["holiday"], "Walk the dog": ["holiday", "exclamation"], "Call mum": ["holiday"]}


def test_a_tag_on_a_comma_list_goes_with_every_task_from_that_line():
    cards, _ = build_cards(parse(mk("", "wash dishes, walk the dog and reply to email [work]")), CFG)
    assert len(cards) == 3 and all(c.icons == ["work"] for c in cards)


def test_tags_are_not_spell_corrected_or_read_as_dates():
    cards, _ = build_cards(parse(mk("", "[wrk] [fri] finish the [draft] report")), CFG)
    assert cards[0].icons == [] and [s.key for s in cards[0].sections] == ["text"]       # no tag, no DUE line
    assert "[wrk]" in cards[0].sections[0].text and "[draft]" in cards[0].sections[0].text
    items = build_items("[school] pack bag", REF, CFG)
    assert items[0].icons == ["school"] and items[0].text == "Pack bag" and items[0].deadline is None
    assert "[wrk]" in build_items("do [wrk] stuff", REF, CFG)[0].text          # unknown tag survives spell-check untouched


def test_photo_email_is_one_card_with_the_tags():
    pic = Image.new("RGB", (400, 300), "blue")
    cards, _ = build_cards(parse(mk("[3d printer] Broken part", "see photo [!]", images=[pic])), CFG)
    assert len(cards) == 1 and cards[0].icons == ["3dprinter", "exclamation"] and len(cards[0].images) == 1


def test_forwarded_email_takes_tags_from_subject_and_your_notes_but_not_the_forwarded_text():
    raw = mk("Fwd: [accountant] Invoice", "[!] reply to Dave by friday\n\n---------- Forwarded message ---------\n"
             "From: Dave <d@x.com>\nDate: Mon\nSubject: Invoice\nTo: me\n\nSee note [school] below. The invoice is overdue by 20 days.")
    (card,), _ = build_cards(parse(raw), CFG)
    assert card.kind == "email" and card.icons == ["accountant", "exclamation"] and card.heading == "Invoice"
    assert card.sections[1].items == ["Reply to Dave by Friday"]


def test_friend_message_and_calendar_event_tags(tmp_path):
    from app.calsync import card_for_event
    from app.pipeline import message_card
    friend = {"name": "Sarah", "email": "s@x.com"}
    card = message_card(parse(mk("[fun] Party!", "bring cake [birthday]", sender="s@x.com")), friend, CFG)
    assert card.icons == ["fun", "birthday"] and card.heading == "Party!" and card.sections[0].text == "bring cake"
    ev = {"summary": "[school] Parent night", "description": "Bring forms [!]", "location": "", "start": "2026-11-04T18:00:00+11:00",
          "end": "2026-11-04T19:00:00+11:00", "all_day": False}
    cal = card_for_event(ev, CFG, datetime.fromisoformat("2026-11-03T07:00:00+11:00"))
    assert cal.icons == ["school", "exclamation"] and cal.heading == "Parent night" and cal.sections[-1].text == "Bring forms"


# ---------------------------------------------------------------- drawing & printing
def ink(im):
    px = im.convert("L").point(lambda p: 1 if p < 128 else 0)
    return sum(px.getdata()) / (im.width * im.height)


def test_every_builtin_symbol_draws_something_sensible():
    for iid in icons.BUILTIN:
        im = icons.draw_icon(iid, 120)
        assert im.size == (120, 120) and 0.04 < ink(im) < 0.55, (iid, ink(im))


def test_hand_drawn_look_is_stable_but_not_a_perfect_vector():
    a, b = icons.draw_icon("star", 140), icons.draw_icon("star", 140)
    assert a.tobytes() == b.tobytes()                                   # same every print
    assert a.tobytes() != icons.draw_icon("heart", 140).tobytes()
    # wobble: the star isn't left/right symmetric like a ruler-drawn one would be
    flipped = a.transpose(Image.FLIP_LEFT_RIGHT)
    diff = sum(abs(x - y) > 60 for x, y in zip(a.getdata(), flipped.getdata())) / (140 * 140)
    assert diff > 0.01


def tpl(**over):
    t = {k: (dict(v) if isinstance(v, dict) else v) for k, v in CFG["templates"]["reminder"].items()}
    t.update(over)
    return t


def card(**kw):
    return Card(kind="reminder", heading="Hi", sections=[Section("text", text="Walk the dog")], **kw)


def test_symbols_print_above_the_heading_and_make_the_card_taller_or_different():
    base = render_card(card(), tpl(), CFG["printer"], CFG["icons"])
    with_icons = render_card(card(icons=["school", "exclamation"]), tpl(), CFG["printer"], CFG["icons"])
    assert with_icons.tobytes() != base.tobytes() and with_icons.size[0] == 576
    # icons sit between the title and the heading: the band right under the title has ink only when icons are present
    t = tpl(order=["icons", "heading"])
    only = render_card(card(icons=["school"]), t, CFG["printer"], CFG["icons"])
    assert ink(only) > ink(render_card(card(), t, CFG["printer"], CFG["icons"]))


def test_old_templates_without_an_icons_block_still_show_symbols():
    t = tpl(order=["title", "heading", "body"])                         # saved before symbols existed
    a = render_card(card(icons=["school"]), t, CFG["printer"], CFG["icons"])
    b = render_card(card(), t, CFG["printer"], CFG["icons"])
    assert a.size[1] >= b.size[1] and a.tobytes() != b.tobytes()
    off = tpl(order=["title", "heading", "body"], icons={**CFG["templates"]["reminder"]["icons"], "enabled": False})
    assert render_card(card(icons=["school"]), off, CFG["printer"], CFG["icons"]).tobytes() == b.tobytes()


def test_many_symbols_shrink_to_fit_and_custom_symbol_prints(tmp_path, monkeypatch):
    import app.render as render
    monkeypatch.setattr(render, "UPLOAD_DIR", tmp_path)
    Image.new("RGBA", (200, 200), (0, 0, 0, 255)).save(tmp_path / "blob.png")
    cfg = normalise({"icons": {"custom": [{"id": "blob1", "name": "Blob", "tags": [], "file": "blob.png", "mode": "line"}]}})
    im = render_card(card(icons=["work", "school", "fun", "home", "heart", "blob1"]), tpl(), cfg["printer"], cfg["icons"])
    assert im.size[0] == 576
    solo = render_card(card(icons=["blob1"]), tpl(), cfg["printer"], cfg["icons"])
    assert ink(solo) > ink(render_card(card(), tpl(), cfg["printer"], cfg["icons"])) + 0.02   # the black square printed


def test_cheat_sheet_renders():
    sheet = icons.render_sheet(CFG, 576)
    assert sheet.mode == "1" and sheet.size[0] == 576 and sheet.size[1] > 900


# ---------------------------------------------------------------- web
@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.calsync import CalendarSync
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.service import Service
    from app.web import create_app
    import app.web as web
    monkeypatch.setattr(web, "UPLOAD_DIR", tmp_path / "up")
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc)).test_client()
    c.post("/api/setup", json={"password": "hunter22"}, headers={"X-Requested-With": "fetch"})
    return c, cfg, svc


H = {"X-Requested-With": "fetch"}


def test_icon_endpoints(client):
    c, cfg, svc = client
    r = c.get("/api/icons").json["icons"]
    assert len(r) == len(icons.BUILTIN) and {"id", "name", "tags", "short", "enabled"} <= set(r[0])
    png = c.get("/api/icon/school.png?size=100")
    assert png.status_code == 200 and png.data[:4] == b"\x89PNG" and Image.open(io.BytesIO(png.data)).size == (100, 100)
    assert c.get("/api/icon/nope.png").status_code == 404
    jid = c.post("/api/icons/print-sheet", headers=H).json["id"]
    assert svc.db.one("SELECT status, kind FROM jobs WHERE id=?", (jid,)) == {"status": "pending", "kind": "sheet"}
    assert c.post("/api/test-print", json={"template": "reminder"}, headers=H).status_code == 200


def test_saving_symbol_settings_is_sanitised(client):
    c, cfg, _ = client
    conf = c.get("/api/config").json
    conf["icons"] = {"disabled": ["fun", "bogus"], "aliases": {"school": [" sch ", ""], "nope": ["x"]},
                     "custom": [{"id": "a1", "name": "Bike", "tags": ["cycling"], "file": "ok.png", "mode": "photo"},
                                {"id": "evil", "name": "x", "tags": [], "file": "../../etc/passwd"},
                                {"id": "school", "name": "clash", "tags": [], "file": "b.png"}]}
    out = c.put("/api/config", json=conf, headers=H).json["icons"]
    assert out["disabled"] == ["fun"] and out["aliases"] == {"school": ["sch"]}
    assert [x["name"] for x in out["custom"]] == ["Bike", "clash"]                # traversal path dropped
    assert out["custom"][1]["id"] != "school"                                    # can't shadow a built-in id
    assert icons.extract_tags("[cycling] [sch]", cfg.data)[1] == ["a1", "school"]
