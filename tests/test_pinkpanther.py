"""Pink Panther episodes: the built-in list (from the user's Pink Panther Wiki guide), matching with confidence, reading long emails,
the concise slip, and the spoiler-free plot overview.  Nothing here touches the network: a fake Fandom/Wikipedia is plugged in."""
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import llm  # noqa: E402
from app import pinkpanther as pp  # noqa: E402
from app.config import normalise  # noqa: E402
from app.mailparse import Incoming  # noqa: E402
from app.pipeline import build_cards  # noqa: E402
from app.render import render_card  # noqa: E402

NOW = __import__("datetime").datetime(2026, 11, 4, 10, 0)
CFG = normalise({})
EPS = pp.load_episodes()
BY = {e["title"]: e for e in EPS}

PLOT = ("The Pink Panther is painting a house pink while a little man keeps painting it blue. They compete in a series of increasingly elaborate "
        "attempts to cover each other's work. The Panther climbs a ladder with a rope in the tussle. In the end the little man falls into a paint bucket "
        "and the Panther claims the house.")
FANDOM_PINK_PHINK = f"""<div class="mw-parser-output"><aside class="portable-infobox pi-theme-wikia"><h2 class="pi-item pi-title">The Pink Phink</h2>
<section class="pi-item pi-group"><div class="pi-item pi-data"><h3 class="pi-data-label pi-secondary-font">Release date</h3>
<div class="pi-data-value pi-font">December 18, 1964<br/>United States</div></div>
<div class="pi-item pi-data"><h3 class="pi-data-label">Directed by</h3><div class="pi-data-value">Friz Freleng</div></div></section></aside>
<p><b>The Pink Phink</b> is the first Pink Panther cartoon.<sup class="reference">[1]</sup> It won an Academy Award.</p>
<h2><span class="mw-headline" id="Plot">Plot</span></h2><p>{PLOT}</p>
<h2><span class="mw-headline" id="Trivia">Trivia</span></h2><p>The short was made in the sixties and has been shown many times since then on television.</p></div>"""
FANDOM_PINK_ICE = ("<div class='mw-parser-output'><p>Pink Ice is a short in which the Pink Panther sets out to steal a diamond from a jeweller. It was released on June 10, 1965 "
                   "and later shown on television.</p></div>")
WIKI_PINKNIC = """<div class="mw-parser-output"><table class="infobox vevent"><tbody><tr><th colspan="2">Pinknic</th></tr>
<tr><th scope="row">Release date</th><td><span style="display:none">(1966-03-01)</span>March 1, 1966</td></tr></tbody></table>
<p><b>Pinknic</b> is a Pink Panther short about a picnic that is interrupted by an ant.</p>
<h2><span class="mw-headline" id="Plot">Plot</span></h2><p>The Pink Panther heads out for a quiet picnic in the park and finds an ant already at his table. The two argue over the food.</p></div>"""


class FakeWeb:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, timeout=12):
        self.calls.append(url)
        if self.fail:
            raise OSError("no route to host")
        u = urllib.parse.urlparse(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if "fandom" in u.netloc and q.get("action") == "parse":
            html = {"The Pink Phink": FANDOM_PINK_PHINK, "Pink Ice": FANDOM_PINK_ICE}.get(q["page"])
            return {"parse": {"title": q["page"], "text": html}} if html else {"error": {"code": "missingtitle"}}
        if "wikipedia" in u.netloc and q.get("action") == "query":
            if "Pinknic" in q["srsearch"]:
                return {"query": {"search": [{"title": "Pinknic", "snippet": "a Pink Panther cartoon"}]}}
            return {"query": {"search": []}}
        if "wikipedia" in u.netloc and q.get("action") == "parse" and q["page"] == "Pinknic":
            return {"parse": {"title": "Pinknic", "text": WIKI_PINKNIC}}
        return {"error": {"code": "missingtitle"}}


@pytest.fixture()
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "CACHE_FILE", tmp_path / "pp.json")
    monkeypatch.setattr(pp, "LAST_PROBLEM", "")
    fake = FakeWeb()
    monkeypatch.setattr(pp, "FETCH", fake)
    return fake


def msg(subject="", body="", **kw):
    return Incoming(subject=subject, body=body, sender_addr="me@x.com", sender_name="Jack", **kw)


# ----------------------------------------------------------------------------- the built-in list (from the user's guide)
def test_the_builtin_list_matches_the_guide():
    b = pp._bundled()
    assert [(s["season"], len(s["episodes"])) for s in b["seasons"]] == [("1", 32), ("2", 17), ("3–5", 17), ("6–7", 16), ("8–9", 16)]
    for s in b["seasons"]:
        assert [e["episode"] for e in s["episodes"]] == list(range(1, len(s["episodes"]) + 1)) and all(len(e["shorts"]) == 3 for e in s["episodes"])
    assert len(EPS) == 216 and b["seasons"][0]["show"] == "The Pink Panther Show" and b["seasons"][0]["years"] == "1969–1970"
    assert b["seasons"][4]["network"] == "ABC" and b["seasons"][4]["show"] == "The All New Pink Panther Show"


def test_each_short_knows_where_it_was_shown_first_showing_first():
    ap = lambda t: [(a["season"], a["episode"]) for a in BY[t]["appearances"]]
    assert ap("The Pink Phink") == [("1", 11), ("2", 17)]
    assert ap("Pink Sphinx") == [("1", 11), ("2", 3), ("2", 14), ("3–5", 6), ("6–7", 2)]
    assert ap("Pinknic") == [("1", 17)]
    assert ap("Dial \"P\" for Pink") == [("1", 10)] and ap("Bomb Voyage") == [("1", 1)] and ap("Pink Ice Cream") == [("8–9", 16)]
    assert "Come On In! The Water's Pink" in BY and all(t == t.strip() and "  " not in t for t in BY)


def test_the_tool_that_builds_the_list_merges_repeats():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import pinkpanther_from_pdf as tool
    data = {"seasons": [{"season": "1", "show": "S", "network": "NBC", "years": "1969–1970", "episodes": [{"episode": 1, "shorts": ["A", "B", "C"]}]},
                        {"season": "2", "show": "S2", "network": "NBC", "years": "1970–1971", "episodes": [{"episode": 4, "shorts": ["C", "B", "A"]}]}]}
    shorts = {e["title"]: e for e in tool.flatten(data)}
    assert [(a["season"], a["episode"]) for a in shorts["A"]["appearances"]] == [("1", 1), ("2", 4)] and shorts["B"]["slot"] == "middle"


# ----------------------------------------------------------------------------- matching with confidence
@pytest.mark.parametrize("query,title", [("The Pink Phink", "The Pink Phink"), ("pink phink", "The Pink Phink"), ("PINK PHINK", "The Pink Phink"), ("Pink Phnik", "The Pink Phink"),
                                         ("pinknic", "Pinknic"), ("Pink Ice", "Pink Ice"), ("tail fly", "The Pink Tail Fly"), ("pink pajamaz", "Pink Pajamas"),
                                         ("bomb voyage", "Bomb Voyage"), ("pink plunk plink", "Pink, Plunk, Plink"), ("sacre bleu cross", "Sacré Bleu Cross"),
                                         ("come on in the waters pink", "Come On In! The Water's Pink")])
def test_what_you_typed_is_matched_loosely_when_it_is_clear(query, title):
    ep, _, conf = pp.match(query, EPS)
    assert ep and ep["title"] == title and conf >= pp.ACCEPT


@pytest.mark.parametrize("query", ["pink", "panther", "completely different", "xyz", "the one with the boat"])
def test_a_doubtful_match_is_not_used(query):
    assert pp.match(query, EPS)[0] is None


def test_the_year_or_season_in_the_message_settles_a_close_call():
    assert pp.match("pink pan", [BY["Pink Panic"], BY["Pink Pajamas"]])[0]["title"] == "Pink Panic"          # clearly the closer one
    amb = [{"title": "Pink Alart", "years": "1969–1970", "appearances": [{"season": "1"}]}, {"title": "Pink Alert", "years": "1976–1977", "appearances": [{"season": "6–7"}]}]
    assert pp.match("pink alurt", amb)[0] is None
    assert pp.match("pink alurt", amb, "season 6 please")[0]["title"] == "Pink Alert"
    assert pp.match("pink alurt", amb, "the 1969 one")[0]["title"] == "Pink Alart"


def test_context_never_creates_a_match_on_its_own():
    assert pp.match("completely unrelated words", EPS, "1970 season 2")[0] is None


@pytest.mark.parametrize("line,query", [
    ("watch The Pink Phink", "Pink Phink"), ("Pink Panther - Pink Phink", "Pink Phink"), ("1. Pink Ice", "Pink Ice"), ("- \"Pink Punch\"", "Pink Punch"),
    ("episode: Pinknic please", "Pinknic"), ("The Pink Panther cartoon Sink Pink", "Sink Pink"), ("Pink Panther", ""), ("pink panther episodes to watch", "")])
def test_the_title_is_picked_out_of_what_you_wrote(line, query):
    assert pp.clean_query(line) == query


# ----------------------------------------------------------------------------- reading short messages and long emails
def titles(res):
    return [ep["title"] for _, ep, _ in res["found"]]


def test_a_short_list_one_per_line_with_one_name_not_in_the_list():
    res = pp.find_episodes(msg("Pink Panther", "The Pink Phink\nPink Ice\n3. Pinknic\nPink Wrinkle"), CFG, EPS)
    assert titles(res) == ["The Pink Phink", "Pink Ice", "Pinknic"] and [g[1] for g in res["guesses"]] == ["Pink Wrinkle"]


LONG = """Hi Jack,

Hope you're having a good week! I was thinking about the weekend and the kids. They keep asking for the old cartoons again.
I'd really love to watch The Pink Phink and also Pinknic with them on Saturday morning, if we can find them anywhere.
Also there was one called "Pink Pajamaz" that my brother mentioned, and maybe something with a sphinx in it.
Anyway, let me know about dinner on Sunday as well, and whether the car needs to go in for its service before then.
Thanks so much,
Sam

Sam Smith
Operations Manager
Smith & Co Pty Ltd
T: 02 9999 1234 | M: 0412 345 678

This email and any attachments are confidential and may be privileged. If you are not the intended recipient please delete it.

On Mon, 2 Nov 2026 at 9:15 AM, Dave <dave@x.com> wrote:
> I could also watch Pink Panzer if you like
"""


def test_a_long_chatty_email_is_read_for_the_episodes_and_the_rest_is_ignored():
    res = pp.find_episodes(msg("Pink Panther", LONG), CFG, EPS)
    assert res["long"] and res["strong"]
    assert titles(res) == ["The Pink Phink", "Pinknic", "Pink Pajamas"]                    # exact titles in sentences, and a quoted one with a typo
    assert res["guesses"] == []                                                          # chatter, greetings, the signature: no junk slips
    assert "Pink Panzer" not in titles(res) and "Smith & Co" not in res["body"]          # the quoted reply and signature are gone


def test_a_long_email_with_no_episode_still_gets_one_honest_slip(web):
    body = "Hi Jack,\n\nJust a general note. " + "We talked about all sorts of things over dinner last week. " * 12 + "\n\nThanks,\nSam"
    cards, rest = pp.build(msg("Pink Panther", body), CFG, NOW)
    assert len(cards) == 1 and rest is None and cards[0].sections == [] and cards[0].kind == "pinkpanther"


def test_an_exact_title_in_a_sentence_beats_a_longer_title_it_is_part_of():
    res = pp.find_episodes(msg("Pink Panther", "I want to see Pink Panic and Pink Panzer tonight please"), CFG, EPS)
    assert titles(res) == ["Pink Panic", "Pink Panzer"]


def test_a_passing_mention_is_not_an_episode_message():
    res = pp.find_episodes(msg("Toys", "buy a pink panther toy for Sam"), CFG, EPS)
    assert res["found"] == [] and not res["strong"]
    cards, rest = pp.build(msg("Toys", "buy a pink panther toy for Sam"), CFG, NOW)
    assert cards == [] and rest.body == "buy a pink panther toy for Sam"
    assert pp.build(msg("Shopping", "milk\nbread"), CFG, NOW)[0] == []


def test_a_weak_mention_that_does_match_an_episode_is_taken_and_the_rest_stays_a_reminder(web):
    cards, rest = pp.build(msg("Evening", "pink panther pink ice\nbuy milk\nring Dave"), CFG, NOW)
    assert [c.heading for c in cards] == ["Pink Ice"] and rest.body == "buy milk\nring Dave"


def test_there_is_no_sign_off_name_on_the_slip(web):
    cards, _ = build_cards(msg("Pink Panther [from Jack]", "Pink Ice\n\nThanks,\nJack"), CFG)
    assert [c.heading for c in cards] == ["Pink Ice"] and all(c.stamp == "" for c in cards)
    cards, _ = build_cards(msg("Watch list [from Jack]", "Dune"), CFG)
    assert [c.kind for c in cards] == ["watchlist"] and cards[0].stamp == ""
    cards, _ = build_cards(msg("Chores [from Jack]", "wash dishes"), CFG)
    assert cards[0].stamp == "From Jack"                                                         # ordinary reminders still can


def test_symbols_you_tag_come_along(web):
    (c,), _ = pp.build(msg("[fun] Pink Panther", "pink ice"), CFG, NOW)
    assert c.icons == ["fun"]


# ----------------------------------------------------------------------------- the slip: concise
def lines(card):
    return card.sections[0].text.split("\n")


def test_the_slip_has_the_title_season_date_episode_and_plot_only(web):
    (c,), rest = pp.build(msg("Pink Panther", "pink phink"), CFG, NOW)
    assert rest is None and c.kind == "pinkpanther" and c.template == "pinkpanther" and c.heading == "The Pink Phink" and c.stamp == ""
    assert lines(c) == ["SEASON: 1 - The Pink Panther Show", "DATE: December 18, 1964", "EPISODE: 11", "ALSO: S2 #17"]
    assert [(s.label, s.text) for s in c.sections[1:]] == [("PLOT", "The Pink Panther is painting a house pink while a little man keeps painting it blue. "
                                                                    "They compete in a series of increasingly elaborate attempts to cover each other's work. "
                                                                    "The Panther climbs a ladder with a rope in the tounge".replace("tounge", "tussle") + ".")]
    text = "\n".join(s.text for s in c.sections)
    for gone in ("DIRECTOR", "WRITER", "LENGTH", "NOTES", "RELEASED", "Friz", "Academy"):
        assert gone not in text


def test_the_plot_is_the_set_up_not_the_ending(web):
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), CFG, NOW)
    plot = c.sections[1].text
    assert "paint bucket" not in plot and "claims the house" not in plot and plot.count(".") == 3


def test_without_an_exact_date_the_tv_years_are_shown(web):
    (c,), _ = pp.build(msg("Pink Panther", "Pink Panzer"), CFG, NOW)
    assert lines(c) == ["SEASON: 1 - The Pink Panther Show", "DATE: 1969–1970 (TV)", "EPISODE: 31"]
    assert len(c.sections) == 1                                                                  # no page for it, so no plot line, nothing invented


def test_a_short_shown_many_times_lists_the_first_and_a_few_more(web):
    (c,), _ = pp.build(msg("Pink Panther", "Pink Sphinx"), CFG, NOW)
    assert lines(c)[2:] == ["EPISODE: 11", "ALSO: S2 #3, S2 #14, S3–5 #6 ..."]


def test_the_exact_date_and_plot_can_come_from_wikipedia_when_fandom_has_no_page(web):
    (c,), _ = pp.build(msg("Pink Panther", "Pinknic"), CFG, NOW)
    assert "DATE: March 1, 1966" in lines(c) and c.sections[1].text.startswith("The Pink Panther heads out for a quiet picnic")
    (c,), _ = pp.build(msg("Pink Panther", "Pink Ice"), CFG, NOW)
    assert "DATE: June 10, 1965" in lines(c)                                                      # found in the page's own sentence


def test_a_name_not_in_the_list_is_printed_as_you_wrote_it(web):
    (c,), _ = pp.build(msg("Pink Panther", "Pink Wrinkle"), CFG, NOW)
    assert c.heading == "Pink Wrinkle" and c.sections == [] and c.vars["matched"] is False and c.stamp == ""
    (c,), _ = pp.build(msg("Pink Panther", "the one with the boat"), CFG, NOW)
    assert c.heading == "one with the boat" or c.heading == "the one with the boat"
    assert c.sections == []


def test_several_episodes_get_a_slip_each_and_repeats_are_collapsed(web):
    cards, rest = pp.build(msg("Pink Panther", "Pink Ice\nPinknic\n\n3. Sink Pink\nthe pink ice"), CFG, NOW)
    assert [c.heading for c in cards] == ["Pink Ice", "Pinknic", "Sink Pink"] and rest is None


def test_switching_it_off_or_the_internet_lookup_off(web):
    off = normalise({"pinkpanther": {"enabled": False}})
    assert pp.build(msg("Pink Panther", "pink phink"), off, NOW)[0] == []
    nolookup = normalise({"pinkpanther": {"lookup": False}})
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), nolookup, NOW)
    assert lines(c) == ["SEASON: 1 - The Pink Panther Show", "DATE: 1969–1970 (TV)", "EPISODE: 11", "ALSO: S2 #17"] and len(c.sections) == 1 and web.calls == []


def test_no_internet_still_gives_season_and_episode_and_the_failure_is_not_remembered(web):
    web.fail = True
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), CFG, NOW)
    assert lines(c)[0] == "SEASON: 1 - The Pink Panther Show" and len(c.sections) == 1 and "no route" in pp.LAST_PROBLEM
    web.fail = False
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), CFG, NOW)
    assert "DATE: December 18, 1964" in lines(c) and len(c.sections) == 2                              # the next try works: nothing was cached as "none"


def test_online_details_are_fetched_once_per_short(web):
    pp.build(msg("Pink Panther", "pink phink"), CFG, NOW)
    n = len(web.calls)
    pp.build(msg("Pink Panther", "pink phink"), CFG, NOW)
    assert len(web.calls) == n
    pp.build(msg("Pink Panther", "Pink Panzer"), CFG, NOW)
    m = len(web.calls)
    pp.build(msg("Pink Panther", "Pink Panzer"), CFG, NOW)
    assert len(web.calls) == m                                                                      # "nothing found" is remembered too


# ----------------------------------------------------------------------------- reading a page
def test_a_fandom_page_gives_the_release_date_and_the_plot_section():
    d = pp.details_from_html(FANDOM_PINK_PHINK)
    assert d["release"] == "December 18, 1964" and d["plot_source"] == PLOT                          # the Plot section, not the intro or the trivia


def test_a_wikipedia_infobox_and_a_page_with_no_plot_section():
    d = pp.details_from_html(WIKI_PINKNIC)
    assert d["release"] == "March 1, 1966" and d["plot_source"].startswith("The Pink Panther heads out")
    d = pp.details_from_html(FANDOM_PINK_ICE)
    assert d["release"] == "June 10, 1965" and d["plot_source"].startswith("Pink Ice is a short in which")
    assert pp.details_from_html("") == {} and pp.details_from_html("<p>short</p>") == {}


# ----------------------------------------------------------------------------- the AI plot overview
AI_CFG = normalise({"summary": {"backend": "ollama"}, "llm": {"url": "http://127.0.0.1:1"}, "pinkpanther": {"ai_plot": True}})


class FakeOllama:
    def __init__(self, answer):
        self.answer, self.sent = answer, []

    def __call__(self, cfg, path, payload, timeout):
        self.sent.append(payload)
        if isinstance(self.answer, Exception):
            raise self.answer
        return {"message": {"content": self.answer}}


GOOD = ("The Pink Panther is painting a house pink while a little man keeps painting it blue. The two compete to cover each other's work. "
        "Each tries a new trick with a ladder and a rope.")


def test_with_ai_on_the_overview_is_written_by_the_model_from_the_text_and_kept(web, monkeypatch):
    fake = FakeOllama("**Plot overview:**\n- " + GOOD.replace(". ", ".\n- "))
    monkeypatch.setattr(llm, "_post", fake)
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), AI_CFG, NOW)
    assert c.sections[1].label == "PLOT" and c.sections[1].text == GOOD
    system, user = fake.sent[0]["messages"][0]["content"], fake.sent[0]["messages"][1]["content"]
    assert "NEVER reveal how it ends" in system and "2 to 4" in system and "paint bucket" in user and "The Pink Phink" in user
    pp.build(msg("Pink Panther", "pink phink"), AI_CFG, NOW)
    assert len(fake.sent) == 1                                                                       # remembered: no second model run


def test_a_made_up_overview_is_rejected_and_the_opening_of_the_plot_is_used(web, monkeypatch):
    monkeypatch.setattr(llm, "_post", FakeOllama("Inspector Clouseau arrives in Paris in 1999 and arrests Paul. He then flies to Berlin."))
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), AI_CFG, NOW)
    assert "Clouseau" not in c.sections[1].text and c.sections[1].text.startswith("The Pink Panther is painting a house pink")


def test_ai_off_unavailable_or_disabled_uses_the_opening_of_the_plot_and_never_calls_the_model(web, monkeypatch):
    fake = FakeOllama(GOOD)
    monkeypatch.setattr(llm, "_post", fake)
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), CFG, NOW)                                  # AI mode off
    assert c.sections[1].text.startswith("The Pink Panther is painting") and fake.sent == []
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), normalise({"summary": {"backend": "ollama"}, "pinkpanther": {"ai_plot": False}}), NOW)
    assert fake.sent == []
    monkeypatch.setattr(llm, "_post", FakeOllama(OSError("connection refused")))
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), AI_CFG, NOW)
    assert c.sections[1].text.startswith("The Pink Panther is painting")


def test_only_a_few_overviews_per_message_use_the_model(web, monkeypatch):
    fake = FakeOllama(GOOD)
    monkeypatch.setattr(llm, "_post", fake)
    cfg = normalise({"summary": {"backend": "ollama"}, "pinkpanther": {"ai_plot": True, "max_ai_plots": 1}})
    pp.build(msg("Pink Panther", "pink phink\nPinknic"), cfg, NOW)
    assert len(fake.sent) == 1                                                                       # the second uses the plain opening


def test_a_one_word_or_empty_ai_answer_is_not_used(web, monkeypatch):
    monkeypatch.setattr(llm, "_post", FakeOllama("Fun."))
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), AI_CFG, NOW)
    assert c.sections[1].text.startswith("The Pink Panther is painting")


# ----------------------------------------------------------------------------- through the pipeline, the mailbox, the web page, the printer
def test_build_cards_makes_pink_panther_slips_from_an_email_and_leaves_other_mail_alone(web):
    cards, _ = build_cards(msg("Pink Panther", "The Pink Phink\nPink Ice"), CFG)
    assert [c.heading for c in cards] == ["The Pink Phink", "Pink Ice"] and {c.kind for c in cards} == {"pinkpanther"}
    cards, _ = build_cards(msg("Chores", "wash dishes and walk the dog"), CFG)
    assert {c.kind for c in cards} == {"reminder"}


def test_the_full_trip_through_the_mailbox_with_a_long_email(web, tmp_path):
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
    ids = MailPoller(cfg, svc).handle(mk("Pink Panther", "Hi Jack,\n\nI'd love to watch The Pink Phink and Pinknic this weekend, and ring Dave about dinner.\n\nThanks,\nJack"))
    assert len(ids) == 2                                                                             # the greeting and the "Thanks, Jack" sign-off make no slips
    assert {r["title"]: r["kind"] for r in svc.db.all("SELECT title, kind FROM jobs")} == {"The Pink Phink": "pinkpanther", "Pinknic": "pinkpanther"}


def ink(img, box):
    return sum(1 for p in img.crop(box).getdata() if p == 0)


def test_the_slip_is_titled_has_the_picture_and_prints_in_both_styles(web):
    (c,), _ = pp.build(msg("Pink Panther", "Pink Ice"), CFG, NOW)
    tpl = CFG["templates"]["pinkpanther"]
    img = render_card(c, tpl, CFG["printer"], CFG["icons"])
    assert img.size[0] == 576 and 500 < img.size[1] < 1100
    assert tpl["title"]["text"] == "PINK PANTHER" and tpl["graphic"]["enabled"] and tpl["graphic"]["file"] == "builtin:pink_panther"
    assert ink(img, (40, 140, 536, 340)) > 6000                                                       # the drawing is there under the title
    c.vars["graphic"] = "off"
    plain = render_card(c, tpl, CFG["printer"], CFG["icons"])
    whole = lambda i: ink(i, (0, 0, i.size[0], i.size[1]))
    assert whole(plain) < whole(img) - 4000
    c.vars.update(graphic="on", graphic_style="outline")
    outline = render_card(c, tpl, CFG["printer"], CFG["icons"])
    assert outline.size == img.size and ink(outline, (40, 140, 536, 340)) < ink(img, (40, 140, 536, 340))


def test_the_artwork_is_a_real_picture_prepared_for_thermal_paper():
    from app.render import builtin_graphic
    g = builtin_graphic("pink_panther", 504, 240, "shaded")
    assert g.size[0] == 504 and 150 < g.size[1] <= 240 and set(g.getdata()) <= {0, 255}
    assert builtin_graphic("does_not_exist", 504, 240) is None


def test_the_web_page_can_test_and_print(web, tmp_path):
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
    assert c.get("/api/pinkpanther").status_code == 401
    login(c)
    s = c.get("/api/pinkpanther").get_json()
    assert (s["enabled"], s["titles"], s["seasons"], s["tv_episodes"]) == (True, 216, 5, 98)
    t = c.post("/api/pinkpanther-test", json={"q": "pink phnik\nPink Wrinkle"}, headers=H).get_json()
    assert [x["heading"] for x in t["cards"]] == ["The Pink Phink", "Pink Wrinkle"] and [x["found"] for x in t["cards"]] == [True, False]
    assert "SEASON: 1 - The Pink Panther Show" in t["cards"][0]["text"] and "PLOT: " in t["cards"][0]["text"] and svc.db.all("SELECT id FROM jobs") == []
    p = c.post("/api/pinkpanther-test", json={"q": "pink ice", "print": True}, headers=H).get_json()
    assert p["printed"] is True and svc.db.one("SELECT kind, title FROM jobs") == {"kind": "pinkpanther", "title": "Pink Ice"}
    schema = c.get("/api/schema").get_json()["sections"]["pinkpanther"]
    assert {f["path"] for f in schema} >= {"pinkpanther.enabled", "pinkpanther.lookup", "pinkpanther.ai_plot"} and not any("pages" in f["path"] for f in schema)
