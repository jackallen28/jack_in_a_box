"""Emails written as "Label: value" lines (what the iPhone Shortcut sends): the Pi must pick out the title, date, type and a short plot, route Pink Panther
cartoons to the Pink Panther slip and everything else to the WATCH LIST slip, and drop director / cast / themes.  Also: the 30 mm minimum slip height."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import fields, llm  # noqa: E402
from app import pinkpanther as pp  # noqa: E402
from app.config import normalise  # noqa: E402
from app.mailparse import Incoming  # noqa: E402
from app.pipeline import build_cards  # noqa: E402
from app.render import render_card  # noqa: E402

NOW = datetime(2026, 11, 4, 10, 0)
CFG = normalise({})

PINK_8 = """Movie/Show: Pink 8-Ball
Relevant Information: Pink 8-Ball is an animated comedy short featuring the Pink Panther. In this short, the Pink Panther loses his basketball and replaces it with an 8-ball, leading to various comedic situations as he tries to play the game."""
FREEDOM = """Movie Name: Sound of Freedom
Description: Sound of Freedom follows Tim Ballard, a former U.S. government agent who dedicates himself to rescuing children from human trafficking. When he discovers that countless children are being exploited and held captive, he refuses to simply look away. What begins as one mission to find and rescue a child becomes a dangerous fight against the networks responsible for trafficking innocent lives. Risking everything, Ballard goes beyond his former role to pursue those responsible and bring children back to safety. Inspired by true events, it shines a light on the reality of human trafficking.
Additional Information:
Release Date: July 4, 2023
Director: Alejandro Monteverde
Lead Actor: Jim Caviezel (as Tim Ballard)
Based on: The real-life work of Tim Ballard and the organization Operation Underground Railroad (O.U.R.)"""
DEATH = """Movie: Death Becomes Her (1992)
Description: A dark fantasy comedy about two rivals, actress Madeline Ashton and author Helen Sharp, who both drink a magic potion that promises eternal youth. Caught in a bitter feud over a timid plastic surgeon named Ernest Menville, they soon discover the potion's grotesque curse: their bodies become immortal but can still be damaged, leaving them trapped in a state of living death.
Themes: Vanity, jealousy, obsession with youth, and the fear of aging."""


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "CACHE_FILE", tmp_path / "pp.json")
    monkeypatch.setattr(pp, "FETCH", lambda url, timeout=12: {"error": {"code": "missingtitle"}})


def cards_for(body, subject="", cfg=CFG):
    cards, _ = build_cards(Incoming(subject=subject, body=body, sender_addr="me@x.com", sender_name="Jack"), cfg)
    return cards


def text(card):
    return "\n".join((f"{s.label}: " if s.label else "") + s.text for s in card.sections)


# ----------------------------------------------------------------------------- the reader
def test_records_are_split_on_the_title_labels_and_only_the_wanted_fields_are_kept():
    recs = fields.parse(PINK_8 + "\n\n" + FREEDOM + "\n\n" + DEATH)
    assert [r.title for r in recs] == ["Pink 8-Ball", "Sound of Freedom", "Death Becomes Her"]
    f = recs[1]
    assert (f.date, f.year, f.type) == ("July 4, 2023", "2023", "movie") and f.desc.startswith("Sound of Freedom follows Tim Ballard") and f.desc.endswith("human trafficking.")
    assert "Monteverde" not in f.desc and "Caviezel" not in f.desc and "Underground" not in f.desc
    d = recs[2]
    assert (d.title, d.year, d.date, d.type) == ("Death Becomes Her", "1992", "", "movie") and "Vanity" not in d.desc
    assert (recs[0].type, recs[0].year, recs[0].date) == ("", "", "")                     # "Movie/Show": could be either
    assert recs[1].raw[0].startswith("Movie Name") and len(recs[1].raw) == 8


def test_a_description_can_run_over_several_lines_and_a_colon_inside_it_is_not_a_new_field():
    (r,) = fields.parse("Movie: Heat\nDescription: A crew of thieves and a detective.\nCaught between duty and family: both men are driven.\nDirector: Michael Mann")
    assert r.desc == "A crew of thieves and a detective. Caught between duty and family: both men are driven."


@pytest.mark.parametrize("value,expected", [
    ("Death Becomes Her (1992)", ("Death Becomes Her", "1992", "", "")), ("**Dune** (2021 film)", ("Dune", "2021", "movie", "")), ("Severance (TV Series)", ("Severance", "", "tv", "")),
    ("Severance - Season 2", ("Severance", "", "tv", "2")), ("\"Heat\"", ("Heat", "", "", "")), ("Dune (Part Two)", ("Dune (Part Two)", "", "", "")),
    ("The Bear (2022 TV series, S3)", ("The Bear", "2022", "tv", "3"))])
def test_the_title_is_cleaned(value, expected):
    assert fields.clean_title(value) == expected


def test_labels_are_forgiving_and_vague_values_are_ignored():
    (r,) = fields.parse("**Movie/Show:** Dune\n**Released:** 2021\nType: Film\nWhere to watch: Netflix\nGenre: Sci-fi\nRating: N/A")
    assert (r.title, r.year, r.date, r.type, r.on) == ("Dune", "2021", "", "movie", "Netflix")
    (r,) = fields.parse("Movie: Dune\nRelease Date: TBA\nSeason: 2")
    assert (r.date, r.year, r.season) == ("", "", "2")
    assert fields.parse("Hello,\nplease buy milk:\nthanks") == []


def test_a_bare_title_label_only_counts_when_the_message_is_about_watching():
    body = "Title: Quarterly report\nDescription: numbers for the board"
    assert fields.parse(body) == [] and [r.title for r in fields.parse(body, allow_weak=True)] == ["Quarterly report"]
    assert cards_for(body, "Work") == [] or {c.kind for c in cards_for(body, "Work")} == {"reminder"}
    assert [c.kind for c in cards_for(body, "Watch list")] == ["watchlist"]


# ----------------------------------------------------------------------------- the three emails as sent
def test_the_pink_panther_short_gets_the_pink_panther_slip_with_its_description():
    (c,) = cards_for(PINK_8)
    assert c.kind == "pinkpanther" and c.heading == "Pink 8-Ball" and c.stamp == ""
    assert text(c).startswith("PLOT: Pink 8-Ball is an animated comedy short featuring the Pink Panther. In this short, the Pink Panther loses his basketball")
    assert "SEASON" not in text(c) and "EPISODE" not in text(c)                                 # not in the episode guide: nothing invented


def test_a_movie_gets_the_watch_list_slip_with_type_date_and_a_short_plot_only():
    (c,) = cards_for(FREEDOM)
    assert c.kind == "watchlist" and c.heading == "Sound of Freedom" and c.icons == ["action"]
    t = text(c)
    assert t.splitlines()[:2] == ["TYPE: MOVIE", "DATE: July 4, 2023"] and "PLOT: Sound of Freedom follows Tim Ballard" in t
    assert len(pp.sentences(c.sections[-1].text)) == 2 and len(c.sections[-1].text) <= 330         # two sentences, not the whole paragraph
    for gone in ("Monteverde", "Caviezel", "Underground", "Director", "Lead Actor", "Based on", "Additional"):
        assert gone not in t
    (c,) = cards_for(DEATH, "Fun")
    assert c.heading == "Death Becomes Her" and text(c).splitlines()[:2] == ["TYPE: MOVIE", "YEAR: 1992"] and "Vanity" not in text(c) and "Themes" not in text(c)
    assert text(c).count("PLOT:") == 1 and text(c).endswith("eternal youth.")                          # the set-up only: the curse and its ending are left off


def test_all_three_in_one_email_each_go_to_the_right_slip():
    cards = cards_for(PINK_8 + "\n\n" + FREEDOM + "\n\n" + DEATH)
    assert [(c.kind, c.heading) for c in cards] == [("pinkpanther", "Pink 8-Ball"), ("watchlist", "Sound of Freedom"), ("watchlist", "Death Becomes Her")]


def test_the_subject_does_not_matter_and_a_pink_panther_subject_keeps_everything_pink():
    assert [c.kind for c in cards_for(FREEDOM, "")] == [c.kind for c in cards_for(FREEDOM, "Reminder")] == ["watchlist"]
    cards = cards_for(PINK_8 + "\n\n" + FREEDOM, "Pink Panther")
    assert [c.kind for c in cards] == ["pinkpanther", "pinkpanther"]


def test_an_episode_in_the_guide_gets_season_and_episode_and_uses_the_emails_date_and_plot_offline():
    body = "Movie/Show: The Pink Phink\nDescription: The Pink Panther paints a house pink while a little man keeps painting it blue. They compete to cover each other's work.\nRelease Date: December 18, 1964\nDirector: Friz Freleng"
    cfg = normalise({"pinkpanther": {"lookup": False}})
    (c,) = cards_for(body, "", cfg)
    t = text(c)
    assert c.heading == "The Pink Phink" and t.splitlines()[:3] == ["SEASON: 1 - The Pink Panther Show", "DATE: December 18, 1964", "EPISODE: 11"]
    assert "PLOT: The Pink Panther paints a house pink" in t and "Freleng" not in t


def test_a_pink_panther_film_or_a_movie_that_only_mentions_the_diamond():
    (c,) = cards_for("Movie: The Pink Panther (1963)\nDescription: Inspector Clouseau hunts a jewel thief.")
    assert c.kind == "pinkpanther" and c.heading == "The Pink Panther"
    (c,) = cards_for("Movie: Jewel Heist\nDescription: A thriller about a stolen diamond nicknamed the Pink Panther.")
    assert c.kind == "watchlist"


def test_symbols_you_tag_and_the_title_limit_apply_to_labelled_emails_too():
    cards = cards_for("Movie: A One\nMovie: B Two\nMovie: C Three", "[fun] Watch list", normalise({"watchlist": {"max_titles": 2}}))
    assert [(c.heading, c.icons) for c in cards] == [("A One", ["action", "fun"]), ("B Two", ["action", "fun"])]


def test_switching_features_off_leaves_the_email_as_a_normal_reminder():
    off = normalise({"watchlist": {"enabled": False}, "pinkpanther": {"enabled": False}})
    assert {c.kind for c in cards_for(FREEDOM, "", off)} == {"reminder"}


# ----------------------------------------------------------------------------- the AI condenses a long description (and cannot invent)
class FakeOllama:
    def __init__(self, answer):
        self.answer, self.sent = answer, []

    def __call__(self, cfg, path, payload, timeout):
        self.sent.append(payload)
        return {"message": {"content": self.answer}}


AI = normalise({"summary": {"backend": "ollama"}, "llm": {"url": "http://127.0.0.1:1"}})


def test_with_ai_a_long_description_becomes_a_few_spoiler_free_sentences(monkeypatch):
    good = "Sound of Freedom follows Tim Ballard, a former U.S. government agent. He sets out to rescue children from human trafficking. It is inspired by true events."
    fake = FakeOllama(good)
    monkeypatch.setattr(llm, "_post", fake)
    (c,) = cards_for(FREEDOM, "", AI)
    assert c.sections[-1].text == good and "movie or TV show" in fake.sent[0]["messages"][0]["content"] and "Tim Ballard" in fake.sent[0]["messages"][1]["content"]
    assert "Monteverde" not in fake.sent[0]["messages"][1]["content"]                              # the AI only ever sees the description
    monkeypatch.setattr(llm, "_post", FakeOllama("Inspector Clouseau arrives in Paris in 1999 and arrests Paul. He then flies to Berlin."))
    (c,) = cards_for(FREEDOM, "", AI)
    assert "Clouseau" not in c.sections[-1].text and c.sections[-1].text.startswith("Sound of Freedom follows Tim Ballard")      # rejected: the opening is used
    off = normalise({"summary": {"backend": "ollama"}, "watchlist": {"plot_ai": False}})
    fake = FakeOllama(good)
    monkeypatch.setattr(llm, "_post", fake)
    cards_for(FREEDOM, "", off)
    assert fake.sent == []
    (c,) = cards_for(FREEDOM, "", normalise({"watchlist": {"plot": False}}))
    assert [s.label for s in c.sections] == [""]                                                 # no PLOT line when it's switched off


# ----------------------------------------------------------------------------- the 30 mm minimum
def test_a_short_slip_is_30_mm_tall_not_80():
    (c,) = cards_for("Movie: Heat\nType: Film")
    tpl = CFG["templates"]["watchlist"]
    assert tpl["min_height_mm"] == 30 and all(t["min_height_mm"] == 30 for t in CFG["templates"].values())
    tiny = dict(tpl, title={**tpl["title"], "enabled": False}, icons={**tpl["icons"], "enabled": False}, graphic={**tpl["graphic"], "enabled": False}, heading={**tpl["heading"], "size_pt": 10})
    c.sections = []
    img = render_card(c, tiny, CFG["printer"], CFG["icons"])
    assert 235 <= img.size[1] <= 260                                                             # 30 mm at 203 dpi = 240 dots (not 640)


def test_old_80_mm_templates_move_to_30_once_and_your_own_choice_is_kept():
    old = normalise({"templates": {"reminder": {"min_height_mm": 80}, "message": {"min_height_mm": 55}}})
    assert old["templates"]["reminder"]["min_height_mm"] == 30 and old["templates"]["message"]["min_height_mm"] == 55 and old["migrations"]["min_height_30"] is True
    again = normalise({**old, "templates": {**old["templates"], "reminder": {**old["templates"]["reminder"], "min_height_mm": 80}}})
    assert again["templates"]["reminder"]["min_height_mm"] == 80                                  # you chose 80 after the move: it stays
