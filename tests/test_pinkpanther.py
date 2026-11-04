"""Pink Panther episodes: reading Wikipedia's tables, matching what you wrote with confidence, the slips.
Nothing here touches the network: a fake Wikipedia (shaped like the real pages: hidden date spans, footnote marks, merged cells,
multi-director cells, summary rows) is plugged in."""
import json
import sys
import urllib.parse
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import pinkpanther as pp  # noqa: E402
from app.config import normalise  # noqa: E402
from app.mailparse import Incoming  # noqa: E402
from app.pipeline import build_cards  # noqa: E402
from app.render import render_card  # noqa: E402

NOW = __import__("datetime").datetime(2026, 11, 4, 10, 0)
CFG = normalise({})


def date(iso, text):
    return f'<span style="display:none">(<span class="bday dtstart published updated">{iso}</span>)</span>{text}'


def row(no, title, directors, writer, iso, text, ref=""):
    return (f'<tr><th scope="row">{no}</th><td>"<a href="/wiki/x" title="x">{title}</a>"</td><td>{directors}</td><td>{writer}</td>'
            f'<td>{date(iso, text)}{ref}</td></tr>')


LIST_HTML = f"""<div class="mw-parser-output"><p>The Pink Panther is a series of cartoons.</p>
<div class="mw-heading mw-heading2"><h2 id="Theatrical">Theatrical shorts</h2><span class="mw-editsection">[<a>edit</a>]</span></div>
<table class="wikitable plainrowheaders"><tbody>
<tr><th>No.</th><th>Title</th><th>Directed by</th><th>Written by</th><th>Release date</th></tr>
{row(1, "The Pink Phink", "Friz Freleng<br/>Hawley Pratt", "John W. Dunn", "1964-12-18", "December 18, 1964", '<sup class="reference">[1]</sup>')}
{row(2, "Pink Ice", "Friz Freleng", "John W. Dunn", "1965-06-10", "June 10, 1965")}
{row(3, "The Pink Tail Fly", "Hawley Pratt", "John W. Dunn", "1965-08-25", "August 25, 1965")}
{row(4, "Pink Punch", "Hawley Pratt", "John W. Dunn", "1966-02-21", "February 21, 1966")}
{row(5, "Pinknic", "Friz Freleng", "John W. Dunn", "1966-03-01", "March 1, 1966")}
{row(6, "Sink Pink", "Gerry Chiniquy", "John W. Dunn", "1965-10-12", "October 12, 1965")}
<tr class="expand-child"><td colspan="5">The Panther tries to sink an unwanted pink boat across the harbour, with predictable results for everyone.</td></tr>
</tbody></table>
<div class="mw-heading mw-heading2"><h2 id="TV">Television-only shorts</h2></div>
<table class="wikitable"><tbody>
<tr><th>Season</th><th>Title</th><th>Release date</th></tr>
<tr><td rowspan="3">Series 5</td><td>"Pink Alert"</td><td>{date("1976-09-11", "September 11, 1976")}</td></tr>
<tr><td>"Pink Alerts"</td><td>{date("1977-02-12", "February 12, 1977")}</td></tr>
<tr><td>"Pink Pull"</td><td>{date("1977-03-05", "March 5, 1977")}</td></tr>
</tbody></table>
<div class="mw-heading mw-heading2"><h2 id="Awards">Awards</h2></div>
<table class="wikitable"><tbody><tr><th>Year</th><th>Award</th></tr><tr><td>1965</td><td>Oscar</td></tr><tr><td>1966</td><td>Nomination</td></tr></tbody></table>
</div>"""

ARTICLE_HTML = """<div class="mw-parser-output"><table class="infobox vevent"><tbody>
<tr><th colspan="2" class="infobox-above">The Pink Phink</th></tr>
<tr><th scope="row">Directed by</th><td>Friz Freleng<br/>Hawley Pratt</td></tr>
<tr><th scope="row">Story by</th><td>John W. Dunn</td></tr>
<tr><th scope="row">Running time</th><td>6 minutes</td></tr>
<tr><th scope="row">Release date</th><td>December 18, 1964</td></tr></tbody></table>
<p class="mw-empty-elt"></p>
<p><b>The Pink Phink</b> is a 1964 animated short featuring the Pink Panther.<sup class="reference">[2]</sup> It won the Academy Award for Best Animated Short. It was the first of the series.</p></div>"""


class FakeWiki:
    def __init__(self, list_html=LIST_HTML, fail=False):
        self.calls, self.list_html, self.fail = [], list_html, fail

    def __call__(self, url, timeout=12):
        self.calls.append(url)
        if self.fail:
            raise OSError("no route to host")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}
        if q.get("action") == "parse":
            if q["page"] == "List of The Pink Panther cartoons":
                return {"parse": {"title": q["page"], "text": self.list_html}}
            if q["page"] == "The Pink Phink":
                return {"parse": {"title": q["page"], "text": ARTICLE_HTML}}
            return {"error": {"code": "missingtitle"}}
        if q.get("action") == "query":
            term = q["srsearch"]
            if "Pink Phink" in term:
                return {"query": {"search": [{"title": "The Pink Phink", "snippet": "a 1964 Pink Panther cartoon"}]}}
            return {"query": {"search": [{"title": "Unrelated thing", "snippet": "nothing"}]}}
        return {}


@pytest.fixture()
def wiki(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "CACHE_FILE", tmp_path / "pp.json")
    monkeypatch.setattr(pp, "_last_failure", 0.0)
    monkeypatch.setattr(pp, "LAST_PROBLEM", "")
    fake = FakeWiki()
    monkeypatch.setattr(pp, "FETCH", fake)
    return fake, tmp_path / "pp.json"


# ----------------------------------------------------------------------------- reading the tables
def test_episode_tables_are_read_whatever_their_columns_and_junk_is_ignored():
    eps = {e.title: e for e in pp.episodes_from_html(LIST_HTML, "List")}
    assert list(eps)[:6] == ["The Pink Phink", "Pink Ice", "The Pink Tail Fly", "Pink Punch", "Pinknic", "Sink Pink"]
    e = eps["The Pink Phink"]
    assert (e.release, e.number, e.directed, e.written, e.section) == ("December 18, 1964", "1", "Friz Freleng, Hawley Pratt", "John W. Dunn", "Theatrical shorts")
    assert (e.position, e.of) == (1, 6) and "1964-12-18" not in e.release and "[1]" not in e.release          # hidden sort key and footnote gone
    assert eps["Sink Pink"].notes.startswith("The Panther tries to sink")                                    # the summary row attaches to its episode
    assert [x for x in eps if x.startswith("Award") or x == "Oscar"] == []                                    # the awards table has no title column


def test_merged_cells_and_the_series_column_are_understood():
    eps = {e.title: e for e in pp.episodes_from_html(LIST_HTML, "List")}
    assert [eps[t].season for t in ("Pink Alert", "Pink Alerts", "Pink Pull")] == ["Series 5"] * 3            # rowspan carried down
    assert eps["Pink Alert"].release == "September 11, 1976" and eps["Pink Alert"].section == "Television-only shorts"


def test_a_volume_column_is_picked_up_if_the_table_has_one():
    html = """<h2>Home video</h2><table class="wikitable"><tr><th>Title</th><th>Volume</th><th>Released</th></tr>
    <tr><td>Alpha</td><td>Volume 1</td><td>1 May 1965</td></tr><tr><td>Beta</td><td>Volume 2</td><td>2 May 1966</td></tr><tr><td>Gamma</td><td>Volume 2</td><td>3 May 1967</td></tr></table>"""
    eps = pp.episodes_from_html(html)
    assert [(e.title, e.volume, e.release) for e in eps] == [("Alpha", "Volume 1", "1 May 1965"), ("Beta", "Volume 2", "2 May 1966"), ("Gamma", "Volume 2", "3 May 1967")]


def test_a_page_that_is_not_a_list_gives_nothing():
    assert pp.episodes_from_html("<p>Just text</p><table class='wikitable'><tr><th>A</th></tr></table>") == []
    assert pp.episodes_from_html("") == []


def test_article_details_come_from_the_infobox_and_the_first_sentence():
    d = pp.details_from_html(ARTICLE_HTML)
    assert d["directed"] == "Friz Freleng, Hawley Pratt" and d["written"] == "John W. Dunn" and d["length"] == "6 minutes" and d["release"] == "December 18, 1964"
    assert d["summary"] == "The Pink Phink is a 1964 animated short featuring the Pink Panther."


# ----------------------------------------------------------------------------- matching with confidence
EPS = [e.to_json() for e in pp.episodes_from_html(LIST_HTML, "List")]


@pytest.mark.parametrize("query,title", [("The Pink Phink", "The Pink Phink"), ("pink phink", "The Pink Phink"), ("PINK PHINK", "The Pink Phink"), ("Pink Phnik", "The Pink Phink"),
                                         ("pinknic", "Pinknic"), ("Pink Ice", "Pink Ice"), ("the pink tail fly", "The Pink Tail Fly"), ("tail fly", "The Pink Tail Fly")])
def test_what_you_typed_is_matched_loosely_when_it_is_clear(query, title):
    ep, sugg, conf = pp.match(query, EPS)
    assert ep and ep["title"] == title and conf >= pp.ACCEPT


@pytest.mark.parametrize("query", ["pink", "panther", "completely different", "xyz", ""])
def test_a_doubtful_match_is_never_stated_as_fact(query):
    ep, sugg, conf = pp.match(query, EPS)
    assert ep is None


def test_close_titles_are_told_apart_by_the_rest_of_the_message():
    eps = [{"title": "Pink Alert", "release": "September 11, 1976", "directed": "", "notes": ""}, {"title": "Pink Alerts", "release": "February 12, 1977", "directed": "", "notes": ""}]
    assert pp.match("pink alert", eps)[0]["release"].startswith("September")                       # exact wins
    ep, _, _ = pp.match("pink alerts", eps, "Pink Panther - pink alerts")
    assert ep["release"].startswith("February")
    amb = [{"title": "Pink Alert", "release": "1971", "directed": "", "notes": ""}, {"title": "Pink Allert", "release": "1977", "directed": "", "notes": ""}]
    assert pp.match("pink alart", amb)[0] is None                                                    # two equally likely: don't guess
    ep, _, _ = pp.match("pink alart", amb, "the 1977 one")
    assert ep and ep["title"] == "Pink Allert"                                                       # ...unless the message says which


def test_context_can_tip_a_close_call_but_never_creates_a_match():
    eps = [{"title": "Pink Ice", "release": "June 10, 1965", "directed": "Friz Freleng", "notes": ""}]
    assert pp.match("completely unrelated words", eps, "1965 Freleng")[0] is None
    assert pp.match("pink icee", eps, "pink icee 1965 freleng")[0]["title"] == "Pink Ice"


def test_an_episode_number_works_when_the_list_has_numbers():
    ep, _, conf = pp.match("episode 3", EPS)
    assert ep["title"] == "The Pink Tail Fly" and conf == 1.0
    assert pp.match("episode 999", EPS)[0] is None


@pytest.mark.parametrize("line,query", [
    ("watch The Pink Phink", "Pink Phink"), ("Pink Panther - Pink Phink", "Pink Phink"), ("1. Pink Ice", "Pink Ice"), ("- \"Pink Punch\"", "Pink Punch"),
    ("episode: Pinknic please", "Pinknic"), ("The Pink Panther cartoon Sink Pink", "Sink Pink"), ("Pink Panther", ""), ("pink panther episodes", "")])
def test_the_title_is_picked_out_of_what_you_wrote(line, query):
    assert pp.clean_query(line) == query


# ----------------------------------------------------------------------------- the lookup, the cache, offline
def test_the_list_is_downloaded_once_and_kept(wiki):
    fake, path = wiki
    idx = pp.get_index(CFG)
    assert len(idx["episodes"]) == 9 and path.exists() and len(fake.calls) == 1
    pp.get_index(CFG)
    assert len(fake.calls) == 1                                                                      # second time: from the cache


def test_a_stale_cache_or_changed_pages_refresh_the_list(wiki):
    fake, path = wiki
    pp.get_index(CFG)
    data = json.loads(path.read_text())
    data["fetched"] = "2020-01-01T00:00:00"
    path.write_text(json.dumps(data))
    pp.get_index(CFG)
    assert len(fake.calls) == 2
    cfg = normalise({"pinkpanther": {"pages": ["List of The Pink Panther cartoons", "Another list"]}})
    pp.get_index(cfg)
    assert len(fake.calls) >= 4                                                                      # the new page list was fetched (the second one doesn't exist: tolerated)


def test_offline_with_a_cache_still_works_and_without_one_says_so(wiki, monkeypatch):
    fake, path = wiki
    pp.get_index(CFG)
    fake.fail = True
    data = json.loads(path.read_text())
    data["fetched"] = "2020-01-01T00:00:00"
    path.write_text(json.dumps(data))
    idx = pp.get_index(CFG)
    assert idx and len(idx["episodes"]) == 9 and "no route" in pp.LAST_PROBLEM                       # old list, used anyway
    path.unlink()
    monkeypatch.setattr(pp, "_last_failure", 0.0)
    assert pp.get_index(CFG) is None


def test_failed_lookups_are_not_retried_every_message(wiki, monkeypatch):
    fake, path = wiki
    fake.fail = True
    pp.get_index(CFG)
    n = len(fake.calls)
    pp.get_index(CFG)
    pp.get_index(CFG)
    assert len(fake.calls) == n                                                                      # waits a few minutes before trying again


def test_forcing_a_refresh_reports_what_went_wrong(wiki):
    fake, path = wiki
    fake.fail = True
    with pytest.raises(Exception):
        pp.get_index(CFG, force=True)
    fake.fail, fake.list_html = False, "<p>no tables</p>"
    with pytest.raises(pp.LookupError_) as e:
        pp.get_index(CFG, force=True)
    assert "no episode table" in str(e.value)


def test_article_details_are_fetched_once_and_a_wrong_article_is_ignored(wiki):
    fake, path = wiki
    ep = next(e for e in EPS if e["title"] == "The Pink Phink")
    d = pp.fetch_details(ep, CFG)
    assert d["length"] == "6 minutes" and d["summary"].startswith("The Pink Phink is a 1964")
    n = len(fake.calls)
    assert pp.fetch_details(ep, CFG) == d and len(fake.calls) == n                                   # cached
    assert pp.fetch_details(next(e for e in EPS if e["title"] == "Pink Ice"), CFG) == {}             # no article for it: nothing invented


# ----------------------------------------------------------------------------- the slips
def msg(subject="", body="", **kw):
    return Incoming(subject=subject, body=body, sender_addr="me@x.com", sender_name="Jack", **kw)


def test_a_clear_episode_gets_a_slip_with_the_real_name_and_whatever_was_found(wiki):
    (c,), rest = pp.build(msg("Pink Panther", "pink phink"), CFG, NOW)
    assert rest is None and c.kind == "pinkpanther" and c.template == "pinkpanther" and c.heading == "The Pink Phink"
    text = c.sections[0].text
    assert "RELEASED: December 18, 1964" in text and "EPISODE: No. 1" in text and "DIRECTOR: Friz Freleng, Hawley Pratt" in text
    assert "WRITER: John W. Dunn" in text and "LENGTH: 6 minutes" in text and "NOTES: The Pink Phink is a 1964 animated short" in text
    assert c.stamp == ""                                                                              # no "source" line


def test_only_what_is_available_is_listed(wiki):
    (c,), _ = pp.build(msg("Pink Panther", "Pink Ice"), CFG, NOW)
    assert c.heading == "Pink Ice"
    lines = c.sections[0].text.split("\n")
    assert lines[0] == "RELEASED: June 10, 1965" and "EPISODE: No. 2" in lines and "DIRECTOR: Friz Freleng" in lines
    assert not any(ln.startswith(("VOLUME", "LENGTH", "NOTES")) for ln in lines)                      # no volume/length/notes anywhere: left off
    (c,), _ = pp.build(msg("Pink Panther", "Pink Alert"), CFG, NOW)
    assert "SERIES: Series 5" in c.sections[0].text and "EPISODE: #1 of 3 in release order" in c.sections[0].text


def test_several_episodes_in_one_message_get_a_slip_each(wiki):
    cards, rest = pp.build(msg("Pink Panther", "Pink Ice\nPinknic\n\n3. Sink Pink"), CFG, NOW)
    assert [c.heading for c in cards] == ["Pink Ice", "Pinknic", "Sink Pink"] and rest is None
    assert "NOTES: The Panther tries to sink" in cards[2].sections[0].text
    assert [c.heading for c in pp.build(msg("Pink Panther", "pink ice\nthe pink ice"), CFG, NOW)[0]] == ["Pink Ice"]            # same episode twice: once


def test_the_year_or_director_in_the_message_helps_choose(wiki):
    (c,), _ = pp.build(msg("Pink Panther", "Pink Alerts 1977"), CFG, NOW)
    assert c.heading == "Pink Alerts"


def test_an_unsure_match_prints_what_you_wrote_and_the_closest_not_a_guess(wiki):
    (c,), _ = pp.build(msg("Pink Panther", "pink"), CFG, NOW)
    text = c.sections[0].text
    assert c.heading == "pink" and "RELEASED" not in text and "I couldn't match this to an episode." in text and "Did you mean:" in text
    (c,), _ = pp.build(msg("Pink Panther", "the one with the boat"), CFG, NOW)
    assert "RELEASED" not in c.sections[0].text and c.heading == "the one with the boat"


def test_just_the_name_prints_the_picture_and_your_words(wiki):
    (c,), rest = pp.build(msg("Pink Panther", "watch sometime this week"), CFG, NOW)
    assert rest is None and c.kind == "pinkpanther" and "RELEASED" not in c.sections[0].text


def test_a_passing_mention_is_left_for_the_normal_reminder(wiki):
    cards, rest = pp.build(msg("Toys", "buy a pink panther toy for Sam"), CFG, NOW)
    assert cards == [] and rest.body == "buy a pink panther toy for Sam"
    cards, rest = pp.build(msg("Shopping", "milk\nbread"), CFG, NOW)
    assert cards == [] and rest.body == "milk\nbread"


def test_a_weak_mention_that_does_match_an_episode_is_taken_and_the_rest_stays_a_reminder(wiki):
    cards, rest = pp.build(msg("Evening", "pink panther pink ice\nbuy milk\nring Dave"), CFG, NOW)
    assert [c.heading for c in cards] == ["Pink Ice"] and rest.body == "buy milk\nring Dave"


def test_switching_it_off_or_lookup_off(wiki):
    fake, _ = wiki
    off = normalise({"pinkpanther": {"enabled": False}})
    assert pp.build(msg("Pink Panther", "pink phink"), off, NOW)[0] == []
    nolookup = normalise({"pinkpanther": {"lookup": False}})
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), nolookup, NOW)
    assert c.heading == "pink phink" and "switched off" in c.sections[0].text and fake.calls == []                  # prints what you wrote, never goes online


def test_no_internet_and_no_cache_still_prints_something_honest(wiki):
    fake, _ = wiki
    fake.fail = True
    (c,), _ = pp.build(msg("Pink Panther", "pink phink"), CFG, NOW)
    assert c.heading == "pink phink" and "couldn't look it up" in c.sections[0].text and "RELEASED" not in c.sections[0].text


def test_symbols_you_tag_come_along(wiki):
    (c,), _ = pp.build(msg("[fun] Pink Panther", "pink ice"), CFG, NOW)
    assert c.icons == ["fun"]


# ----------------------------------------------------------------------------- through the real pipeline and the printer
def test_build_cards_makes_pink_panther_slips_from_an_email_and_leaves_other_mail_alone(wiki):
    cards, _ = build_cards(msg("Pink Panther", "The Pink Phink\nPink Ice"), CFG)
    assert [c.heading for c in cards] == ["The Pink Phink", "Pink Ice"] and {c.kind for c in cards} == {"pinkpanther"}
    cards, _ = build_cards(msg("Chores", "wash dishes and walk the dog"), CFG)
    assert {c.kind for c in cards} == {"reminder"}


def test_the_full_trip_through_the_mailbox(wiki, tmp_path):
    from test_core import env, mk  # noqa: F401
    from app.config import ConfigStore
    from app.mailbox import MailPoller
    from app.service import Service
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["email"]["allowed_senders"] = ["me@example.com"]
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    ids = MailPoller(cfg, svc).handle(mk("Pink Panther", "Pink Ice\nPinknic"))
    assert len(ids) == 2
    assert [r["kind"] for r in svc.db.all("SELECT kind FROM jobs")] == ["pinkpanther", "pinkpanther"]
    assert [r["title"] for r in svc.db.all("SELECT title FROM jobs")] == ["Pink Ice", "Pinknic"]


def ink(img, box):
    return sum(1 for p in img.crop(box).getdata() if p == 0)


def test_the_slip_is_titled_has_the_picture_and_prints_in_both_styles(wiki):
    (c,), _ = pp.build(msg("Pink Panther", "Pink Ice"), CFG, NOW)
    tpl = CFG["templates"]["pinkpanther"]
    img = render_card(c, tpl, CFG["printer"], CFG["icons"])
    assert img.size[0] == 576 and 600 < img.size[1] < 1100
    assert tpl["title"]["text"] == "PINK PANTHER" and tpl["graphic"]["enabled"] and tpl["graphic"]["file"] == "builtin:pink_panther"
    assert ink(img, (40, 140, 536, 340)) > 6000                                                       # the drawing is there under the title
    c.vars["graphic"] = "off"
    plain = render_card(c, tpl, CFG["printer"], CFG["icons"])
    whole = lambda i: ink(i, (0, 0, i.size[0], i.size[1]))
    assert whole(plain) < whole(img) - 4000                                                           # switched off: no picture, much less ink
    c.vars.update(graphic="on", graphic_style="outline")
    outline = render_card(c, tpl, CFG["printer"], CFG["icons"])
    assert outline.size == img.size and ink(outline, (40, 140, 536, 340)) < ink(img, (40, 140, 536, 340))   # clean line art uses less ink than the stipple


def test_the_artwork_is_a_real_picture_prepared_for_thermal_paper():
    from app.render import builtin_graphic
    g = builtin_graphic("pink_panther", 504, 240, "shaded")
    assert g.size[0] == 504 and 150 < g.size[1] <= 240 and set(g.getdata()) <= {0, 255}
    assert builtin_graphic("does_not_exist", 504, 240) is None


# ----------------------------------------------------------------------------- the web page's endpoints
def test_the_web_page_can_test_refresh_and_print(wiki):
    from test_web import H, client, login  # noqa: F401
    import test_web
    from app.calsync import CalendarSync
    from app.config import ConfigStore
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.service import Service
    from app.web import create_app
    fake, path = wiki
    tmp = path.parent
    cfg = ConfigStore(tmp / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    cfg.replace(d)
    svc = Service(cfg, tmp)
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc)).test_client()
    assert c.get("/api/pinkpanther").status_code == 401
    login(c)
    s = c.get("/api/pinkpanther").get_json()
    assert s["enabled"] is True and s["cached"] == 0
    r = c.post("/api/pinkpanther-refresh", headers=H).get_json()
    assert r["ok"] and r["cached"] == 9 and "Downloaded 9" in r["message"]
    t = c.post("/api/pinkpanther-test", json={"q": "pink phink\nthe one with the boat"}, headers=H).get_json()
    assert [x["heading"] for x in t["cards"]] == ["The Pink Phink", "the one with the boat"] and [x["found"] for x in t["cards"]] == [True, False]
    assert "RELEASED: December 18, 1964" in t["cards"][0]["text"] and t["printed"] is False and svc.db.all("SELECT id FROM jobs") == []
    p = c.post("/api/pinkpanther-test", json={"q": "pink ice", "print": True}, headers=H).get_json()
    assert p["printed"] is True and svc.db.one("SELECT kind, title FROM jobs") == {"kind": "pinkpanther", "title": "Pink Ice"}
    fake.fail = True
    bad = c.post("/api/pinkpanther-refresh", headers=H).get_json()
    assert bad["ok"] is False and "no route" in bad["message"]
    assert any(f["path"] == "pinkpanther.lookup" for f in c.get("/api/schema").get_json()["sections"]["pinkpanther"])
