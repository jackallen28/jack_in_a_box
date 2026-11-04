"""Watch list: a message about a movie or TV show prints a generic WATCH LIST slip with the ACTION symbol.  Pink Panther keeps its own slip."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import icons, llm  # noqa: E402
from app import watchlist as wl  # noqa: E402
from app.config import normalise  # noqa: E402
from app.mailparse import Incoming  # noqa: E402
from app.pipeline import build_cards  # noqa: E402
from app.render import render_card  # noqa: E402

NOW = datetime(2026, 11, 4, 10, 0)
CFG = normalise({})


def msg(subject="", body="", name="Jack"):
    return Incoming(subject=subject, body=body, sender_addr="me@x.com", sender_name=name)


def one(subject, body, cfg=CFG, name="Jack"):
    cards, rest = wl.build(msg(subject, body, name), cfg, NOW)
    return cards, rest


def listed(cards):
    """Each slip as the title plus what the message said about it: "Dune (2021) - movie - Netflix"."""
    return [wl.describe(c.vars["item"]) for c in cards]


# ----------------------------------------------------------------------------- the slip
def test_the_slip_is_titled_watch_list_with_the_action_symbol_at_the_top():
    (c,), rest = one("Watch list", "Dune (2021)")
    assert rest is None and c.kind == c.template == "watchlist" and c.icons == ["action"] and c.stamp == "" and c.heading == "Dune"
    tpl = CFG["templates"]["watchlist"]
    assert tpl["title"]["text"] == "WATCH LIST" and tpl["order"][:2] == ["title", "icons"]
    img = render_card(c, tpl, CFG["printer"], CFG["icons"])
    assert img.size[0] == 576 and img.size[1] >= 400


def test_the_action_symbol_exists_draws_and_does_not_renumber_your_symbols():
    assert "action" in icons.BUILTIN and icons.draw_icon("action", 120).getbbox()
    assert icons.lookup(CFG)["action"] == icons.lookup(CFG)["clapperboard"] == "action"
    cfg = normalise({"icons": {"custom": [{"id": "mine", "name": "Mine", "tags": [], "file": "x.png"}]}})
    n = {x["id"]: x["n"] for x in icons.numbered(cfg)}
    assert n["tick"] == 24 and n["mine"] == 25 and n["action"] == 26


def test_your_own_action_symbol_wins_and_the_setting_picks_another():
    cfg = normalise({"icons": {"custom": [{"id": "myaction", "name": "Action", "tags": [], "file": "x.png"}]}})
    assert wl.symbol_id(cfg) == "myaction"
    assert wl.symbol_id(normalise({"watchlist": {"symbol": "star"}})) == "star"
    assert wl.symbol_id(normalise({"watchlist": {"symbol": "no such symbol"}})) == "action"


def test_one_title_one_slip_with_only_the_details_the_message_gave():
    (c,), _ = one("Movies", "watch the movie Dune (2021) on Netflix")
    assert c.heading == "Dune" and c.sections[0].text == "TYPE: MOVIE\nYEAR: 2021\nON: Netflix"
    (c,), _ = one("Evening", "Severance season 2")
    assert c.heading == "Severance" and c.sections[0].text == "TYPE: TV SHOW\nSEASON: 2"
    (c,), _ = one("Watch list", "Inception")
    assert c.heading == "Inception" and c.sections == []


def test_if_an_email_names_several_each_gets_its_own_slip():
    cards, rest = one("Watch list", "Dune (2021)\nSeverance season 2\nwatch The Bear on Disney+\nThe Godfather")
    assert [c.heading for c in cards] == ["Dune", "Severance", "The Bear", "The Godfather"] and rest is None
    assert all(c.kind == "watchlist" and c.icons == ["action"] for c in cards)
    assert cards[2].sections[0].text == "ON: Disney+"


def test_your_symbol_tags_come_along_after_the_action_symbol():
    cards, _ = one("[fun] Watch list", "Dune\nBarbie")
    assert [c.icons for c in cards] == [["action", "fun"]] * 2


# ----------------------------------------------------------------------------- reading the text
@pytest.mark.parametrize("subject,body,titles", [
    ("Watch list", "Dune\nOppenheimer\nBarbie", ["Dune", "Oppenheimer", "Barbie"]),
    ("Watch list", "Dune, Oppenheimer, Barbie", ["Dune", "Oppenheimer", "Barbie"]),
    ("watchlist", "- Dune\n- Barbie\n1. Heat\n[ ] Alien", ["Dune", "Barbie", "Heat", "Alien"]),
    ("Things to watch", "The Godfather\nI, Robot\nTo Kill a Mockingbird\nthe office - netflix", ["The Godfather", "I, Robot", "To Kill a Mockingbird", "the office - Netflix"]),
    ("Movies", "Dune\nbuy popcorn\nring Dave", ["Dune"]),
    ("TV", '"Severance" is great, we should start Succession this weekend, and I also want to see Oppenheimer.', ["Severance", "Succession", "Oppenheimer"]),
    ("Weekend", "We should watch The Bear on Disney+, Airplane! and Severance season 2 this weekend. Also Date Night (2010).",
     ["Date Night (2010)", "The Bear - Disney+", "Airplane", "Severance S2 - TV show"]),
    ("x", "watch Dune, Oppenheimer and Barbie tonight", ["Dune", "Oppenheimer and Barbie"]),
    ("Reminder", "Planet Earth III (2023)\nAlien (1979)", ["Planet Earth III (2023)", "Alien (1979)"]),
])
def test_titles_are_picked_out(subject, body, titles):
    cards, _ = one(subject, body, normalise({"watchlist": {"max_titles": 20}}))
    assert sorted(listed(cards)) == sorted(titles)


def test_each_titles_year_and_service_stay_with_that_title():
    cards, _ = one("Weekend", "watch The Bear on Disney+ and Dune (2021) on Netflix tonight")
    assert sorted(listed(cards)) == ["Dune (2021) - Netflix", "The Bear - Disney+"]


@pytest.mark.parametrize("subject,body", [
    ("Shopping", "milk\nbread"), ("Evening", "watch Dave play\nbuy popcorn"), ("Movie night", "buy popcorn\nring Dave"), ("", "call the dentist"),
    ("Reminder", "watch the footy tonight"), ("Hello", "Hi Jack, how are you? Thanks, Sam")])
def test_ordinary_messages_are_left_alone(subject, body):
    cards, rest = one(subject, body)
    assert cards == [] and rest.body == body


def test_a_clear_line_in_an_ordinary_message_is_taken_and_the_rest_stays_a_reminder():
    (c,), rest = one("Reminder", "watch the movie Dune (2021) on Netflix\nbuy milk")
    assert c.heading == "Dune" and rest.body == "buy milk"


def test_a_long_chatty_email_is_read_without_the_greeting_and_sign_off():
    body = "Hi Jack,\n\nHope you're well! We should watch The Bear on Disney+ and also Severance season 2 sometime.\n\nThanks,\nSam"
    cards, rest = one("Weekend", body, name="Sam")
    assert sorted(listed(cards)) == ["Severance S2 - TV show", "The Bear - Disney+"] and rest is None
    cards, _ = one("Watch list", "Dune\nBarbie\n\nThanks,\nJack", name="Jack")
    assert listed(cards) == ["Dune", "Barbie"]                                                      # "Jack" is the sign-off, not a film


def test_switching_it_off_and_the_title_limit():
    assert one("Watch list", "Dune", normalise({"watchlist": {"enabled": False}}))[0] == []
    cards, _ = one("Watch list", "A One\nB Two\nC Three", normalise({"watchlist": {"max_titles": 2}}))
    assert len(cards) == 2
    assert len(one("Watch list", "A One\nB Two\nC Three\nD Four\nE Five\nF Six")[0]) == 5          # the default


# ----------------------------------------------------------------------------- Pink Panther keeps its own slip
def test_pink_panther_messages_are_not_taken_by_the_watch_list(tmp_path, monkeypatch):
    from app import pinkpanther as pp
    monkeypatch.setattr(pp, "CACHE_FILE", tmp_path / "pp.json")
    monkeypatch.setattr(pp, "FETCH", lambda url, timeout=12: {"error": {"code": "missingtitle"}})
    cards, _ = build_cards(msg("Pink Panther", "The Pink Phink\nPink Ice"), CFG)
    assert {c.kind for c in cards} == {"pinkpanther"}
    assert one("Watch list", "Pink Panther")[0] == []


def test_the_pipeline_makes_watch_list_slips_and_leaves_other_mail_alone():
    cards, _ = build_cards(msg("Watch list", "Dune (2021)"), CFG)
    assert [c.kind for c in cards] == ["watchlist"]
    cards, _ = build_cards(msg("Chores", "wash dishes and walk the dog"), CFG)
    assert {c.kind for c in cards} == {"reminder"}
    cards, _ = build_cards(msg("Evening", "watch Severance season 2\nbuy milk"), CFG)
    assert [c.kind for c in cards] == ["watchlist", "reminder"]


def test_a_directive_still_wins(tmp_path):
    cards, _ = build_cards(msg("Watch list [plain]", "Dune"), CFG)
    assert {c.kind for c in cards} == {"reminder"} or cards[0].kind != "watchlist"


# ----------------------------------------------------------------------------- the AI helps, but cannot invent
class FakeOllama:
    def __init__(self, answer):
        self.answer, self.sent = answer, []

    def __call__(self, cfg, path, payload, timeout):
        self.sent.append(payload)
        if isinstance(self.answer, Exception):
            raise self.answer
        return {"message": {"content": self.answer}}


AI_CFG = normalise({"summary": {"backend": "ollama"}, "llm": {"url": "http://127.0.0.1:1"}})
CHATTY = "Hi Jack,\n\nThe kids have been on about the new Pixar one, and my brother says Heat holds up well. Also we finally saw oppenheimer last week.\n\nThanks,\nSam"


def test_with_ai_on_a_chatty_email_gets_its_titles_but_only_ones_that_are_in_the_text(monkeypatch):
    fake = FakeOllama("Heat | movie | \nOppenheimer | movie | 2023\nInception | movie | 2010\nNONE")
    monkeypatch.setattr(llm, "_post", fake)
    cards, _ = one("Movies", CHATTY, AI_CFG, "Sam")
    assert sorted(listed(cards)) == ["Heat - movie", "Oppenheimer - movie"]                          # Inception is not in the email; the year 2023 is not either
    assert "Title | movie or tv | year" in fake.sent[0]["messages"][0]["content"]


def test_ai_off_unavailable_or_disabled_means_the_built_in_rules_only(monkeypatch):
    fake = FakeOllama("Heat | movie | ")
    monkeypatch.setattr(llm, "_post", fake)
    cards, _ = one("Movies", CHATTY, CFG, "Sam")
    assert fake.sent == [] and "Heat - movie" not in listed(cards)
    one("Movies", CHATTY, normalise({"summary": {"backend": "ollama"}, "watchlist": {"ai": False}}), "Sam")
    assert fake.sent == []
    monkeypatch.setattr(llm, "_post", FakeOllama(OSError("refused")))
    one("Movies", CHATTY, AI_CFG, "Sam")                                                         # must not raise


def test_the_ai_is_not_asked_about_ordinary_reminders(monkeypatch):
    fake = FakeOllama("Heat | movie | ")
    monkeypatch.setattr(llm, "_post", fake)
    one("Chores", "wash dishes and walk the dog", AI_CFG)
    one("Evening", "buy milk", AI_CFG)
    assert fake.sent == []


# ----------------------------------------------------------------------------- the web page
def test_the_web_page_can_test_and_print(tmp_path):
    from test_web import H, login
    from app.calsync import CalendarSync
    from app.config import ConfigStore
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.service import Service
    from app.web import create_app
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc)).test_client()
    assert c.get("/api/watchlist").status_code == 401
    login(c)
    s = c.get("/api/watchlist").get_json()
    assert s["enabled"] is True and s["symbol"] == "action"
    t = c.post("/api/watchlist-test", json={"subject": "Watch list", "q": "Dune (2021)\nSeverance season 2"}, headers=H).get_json()
    assert [(x["heading"], x["text"]) for x in t["cards"]] == [("Dune", "YEAR: 2021"), ("Severance", "TYPE: TV SHOW\nSEASON: 2")] and not t["printed"] and svc.db.all("SELECT id FROM jobs") == []
    p = c.post("/api/watchlist-test", json={"subject": "Movies", "q": "Dune", "print": True}, headers=H).get_json()
    assert p["printed"] is True and svc.db.one("SELECT kind, title FROM jobs") == {"kind": "watchlist", "title": "Dune"}
    assert c.post("/api/watchlist-test", json={"subject": "Shopping", "q": "milk"}, headers=H).get_json()["cards"] == []
    schema = c.get("/api/schema").get_json()["sections"]["watchlist"]
    assert {f["path"] for f in schema} == {"watchlist.enabled", "watchlist.symbol", "watchlist.ai", "watchlist.plot", "watchlist.plot_ai", "watchlist.max_titles"}


def test_the_full_trip_through_the_mailbox(tmp_path):
    from test_core import mk
    from app.config import ConfigStore
    from app.mailbox import MailPoller
    from app.service import Service
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["email"]["allowed_senders"] = ["me@example.com"]
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    ids = MailPoller(cfg, svc).handle(mk("Watch list", "Dune (2021)\nSeverance season 2\n\nThanks,\nJack"))
    assert len(ids) == 2 and [(r["kind"], r["title"]) for r in svc.db.all("SELECT kind, title FROM jobs")] == [("watchlist", "Dune"), ("watchlist", "Severance")]
