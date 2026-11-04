"""The iPhone Shortcut sends an EMAIL: symbol tags, [message], [from NAME], [plain], and a screenshot attached."""
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_core import env, mk  # noqa: E402,F401
from app.config import normalise  # noqa: E402
from app.mailparse import Incoming  # noqa: E402
from app.pipeline import apply_directives, build_cards, message_card  # noqa: E402

CFG = normalise({"general": {"timezone": "Australia/Sydney"}})


def cards_for(subject, body="", sender="Jack Allen", addr="jack@x.com"):
    return build_cards(Incoming(subject=subject, body=body, sender_name=sender, sender_addr=addr), CFG)[0]


# ----------------------------------------------------------------------------- reading the tags
@pytest.mark.parametrize("subject,body,mode,who,subject_left,body_left", [
    ("[message] Pay fees", "by Friday", "message", "", "Pay fees", "by Friday"),
    ("[MSG] Hi", "", "message", "", "Hi", ""),
    ("Dinner", "be home by six [plain]", "plain", "", "Dinner", "be home by six"),
    ("[exact] Note", "x", "plain", "", "Note", "x"),
    ("[from Mum] Milk", "buy milk", None, "Mum", "Milk", "buy milk"),
    ("Hello", "[from  Jack   Allen ] [message] text", "message", "Jack Allen", "Hello", "text"),
    ("[school] Pay fees", "by friday", None, "", "[school] Pay fees", "by friday"),          # symbol tags are left for the usual path
    ("[draft] note", "see [1]", None, "", "[draft] note", "see [1]")])
def test_directive_tags_are_read_and_removed(subject, body, mode, who, subject_left, body_left):
    inc, got_mode, got_who = apply_directives(Incoming(subject=subject, body=body))
    assert (got_mode, got_who, inc.subject, inc.body) == (mode, who, subject_left, body_left)


def test_message_wins_over_plain_when_both_are_there():
    assert apply_directives(Incoming(subject="[plain] [message] x"))[1] == "message"


# ----------------------------------------------------------------------------- what they print
def test_message_prints_a_message_slip_from_you_with_the_time_sent():
    (c,) = cards_for("[school] [message] Pay fees", "by Friday")
    assert c.kind == "message" and c.heading == "Pay fees" and c.icons == ["school"] and c.vars["name"] == "Jack Allen" and c.stamp.startswith("Sent ")
    assert c.sections[0].text == "by Friday"


def test_from_overrides_the_sender_name_and_adds_a_from_line_to_reminders():
    (m,) = cards_for("[message] [from Mum] Dinner", "be home by six")
    assert m.vars["name"] == "Mum"
    (r,) = cards_for("[from Mum] Milk", "buy milk")
    assert r.kind == "reminder" and r.stamp == "From Mum" and r.heading == "Milk"


def test_a_message_with_no_display_name_uses_the_address():
    (m,) = cards_for("[message] Hi", "x", sender="", addr="jack@x.com")
    assert m.vars["name"] == "jack"


def test_plain_is_one_printout_exactly_as_written():
    cards = cards_for("Note", "wash dishes and walk the dog tomorrow [plain]")
    assert len(cards) == 1 and "wash dishes and walk the dog tomorrow" in cards[0].sections[0].text
    assert len(cards_for("Note", "wash dishes and walk the dog tomorrow")) == 2                  # without it: split into tasks


def test_plain_reminders_without_tags_are_untouched():
    cards = cards_for("[school] Fees", "pay by Friday")
    assert len(cards) == 1 and cards[0].kind == "reminder" and cards[0].icons == ["school"] and cards[0].stamp == ""


def test_a_friends_message_never_prints_the_tag():
    m = message_card(Incoming(subject="Hi", body="see you [message] soon"), {"name": "Sarah", "email": "s@x.com"}, CFG)
    assert "[message]" not in m.sections[0].text and m.vars["name"] == "Sarah"


# ----------------------------------------------------------------------------- symbols from the library, through the real mailbox
@pytest.mark.parametrize("tags,expect", [("[school]", ["school"]), ("[money] [School]", ["money", "school"]), ("[3D printer]", ["3dprinter"]),
                                         ("[Holiday] [calendar] [pet]", ["holiday", "calendar", "pet"]), ("[!]", ["exclamation"])])
def test_stock_symbol_names_in_the_subject_become_symbols(tags, expect):
    (c,) = cards_for(f"{tags} Pay the bill", "by Friday")
    assert c.icons == expect and c.heading == "Pay the bill"


def test_a_name_that_is_not_in_the_library_is_left_as_text_not_lost():
    (c,) = cards_for("[unicorn] Pay the bill", "by Friday")
    assert c.icons == [] and "[unicorn]" in c.heading


def test_your_own_custom_symbols_work_as_tags_too():
    cfg = normalise({"icons": {"custom": [{"id": "gym", "name": "Gym", "tags": ["workout"], "file": "x.png", "mode": "line"}]}})
    (c,) = build_cards(Incoming(subject="[gym] Leg day", body="at 6pm", sender_addr="a@b.c"), cfg)[0]
    assert c.icons == ["gym"]


def test_the_full_trip_an_email_from_the_shortcut_with_a_screenshot(env):
    cfg, svc, poller = env
    shot = Image.new("RGB", (900, 1600), (240, 240, 240))
    raw = mk("[money] [message] Pay school fees", "Fees are due Friday 5pm. [from Jack]", images=[shot])
    ids = poller.handle(raw)
    assert len(ids) == 1 and poller.last_outcome == "printed"
    job = svc.db.one("SELECT kind, title, png FROM jobs")
    assert job["kind"] == "message" and job["title"] == "Pay school fees"
    assert Image.open(svc.hist_dir / job["png"]).height > 1000                                       # the screenshot is on the slip


def test_an_email_from_a_stranger_is_still_ignored(env):
    cfg, svc, poller = env
    assert poller.handle(mk("[message] Hi", "x", sender="evil@spam.com", mid="<evil@x>")) == [] and poller.last_outcome == "blocked"


def test_every_symbol_name_in_the_library_works_as_a_tag():
    """The Apple Intelligence prompt lists the library names in [brackets]; each one must really select its symbol."""
    from app import icons as icon_lib
    names = icon_lib.numbered(CFG)
    assert len(names) >= 24
    for item in names:
        (c,) = cards_for(f"[{item['name'].lower()}] Heading", "text here")
        assert c.icons == [item["id"]], item["name"]


def test_symbols_you_have_switched_off_are_not_offered_or_recognised():
    cfg = normalise({"icons": {"disabled": ["pet"]}})
    (c,) = build_cards(Incoming(subject="[pet] Feed", body="at six", sender_addr="a@b.c"), cfg)[0]
    assert c.icons == [] and "[pet]" in c.heading
