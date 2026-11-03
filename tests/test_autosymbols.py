"""Automatic symbols for forwarded emails: only when clearly right, never from the AI's say-so alone."""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ai import fwd, ollama  # noqa: E402,F401
from test_aireview import rig  # noqa: E402,F401
from app import aifeedback, autosymbols as A, evaluate, icons, llm  # noqa: E402
from app.config import normalise  # noqa: E402
from app.pipeline import build_cards  # noqa: E402

CFG = normalise({})
INVOICE = "Invoice 4471 for $12,480 is 20 days overdue. Please arrange payment by Friday."


# ----------------------------------------------------------------------------- the library
def test_the_library_is_big_and_has_phrases_not_just_words():
    terms = [t for strong, weak in A.KEYWORDS.values() for t in (*strong, *weak)]
    assert len(terms) > 500 and sum(" " in t or "-" in t for t in terms) > 150
    assert set(A.KEYWORDS) <= set(icons.BUILTIN) and not set(A.KEYWORDS) & A.NEVER_AUTO
    for t in terms:
        re.compile(A._compile(t))                                         # every entry is a valid pattern


@pytest.mark.parametrize("term,text,hit", [
    ("blood test", "Please book a blood-test before Friday", True), ("blood test", "blood tests are due", True),
    ("performance review", "Your performance review is next week", True), ("celebrat*", "we are celebrating", True),
    ("driver's licence", "renew your drivers licence", True), ("stand-up", "daily standup at 9", True),
    ("g-code", "send me the g-code", True), ("re:turning \\d+", "she is turning 7", True),
    ("car", "scarf and carpet", False), ("pet", "petition and carpet", False), ("rent", "different", False),
])
def test_phrases_match_whole_words_and_variations(term, text, hit):
    assert bool(A._hits([term], text)) is hit


def test_describe_gives_the_ai_phrases_for_every_symbol_it_may_pick():
    d = A.describe(CFG)
    assert len(d) == 17 and any(x.startswith("health (doctor") or "dentist" in x for x in d)
    assert not any(x.startswith(("exclamation", "star", "idea", "email", "clock", "done")) for x in d)
    assert max(len(x) for x in d) < 140                                      # short enough for a small model's prompt


# ----------------------------------------------------------------------------- choosing: only when sure
def test_one_weak_hint_is_not_enough():
    assert A.choose("Can you give me a call about the thing?", "Hello", CFG) == []
    assert A.choose("The team will review the report", "Update", CFG) == []


def test_clear_evidence_without_the_ai_picks_symbols_but_a_single_keyword_does_not():
    assert A.choose(INVOICE, "Invoice 4471 overdue", CFG)[:1] == ["accountant"]
    assert A.choose("Your invoice is attached.", "Hello", CFG) == []        # one keyword, even a strong one: not certain


def test_the_ai_saying_none_is_final_even_with_evidence():
    assert A.choose(INVOICE, "Invoice 4471 overdue", CFG, ai=[]) == []


def test_an_ai_proposal_needs_backing_from_the_email():
    assert A.choose(INVOICE, "Invoice 4471 overdue", CFG, ai=["pet"]) == []                        # nothing about pets in the email
    assert A.choose(INVOICE, "Invoice 4471 overdue", CFG, ai=["accountant"]) == ["accountant"]
    assert A.choose(INVOICE, "Invoice 4471 overdue", CFG, ai=["accountant", "pet", "money"]) == ["accountant", "money"]


def test_at_most_three_and_weaker_ones_are_dropped():
    text = "School excursion: the flight and hotel are booked, the party is at the school, appointment with the dentist, invoice overdue"
    got = A.choose(text, "School excursion", CFG, ai=["school", "holiday", "fun", "health", "accountant", "calendar"])
    assert 1 <= len(got) <= 3
    weak = "There is a party at the school"
    assert A.choose(weak, "Hello", CFG, ai=["fun", "school", "holiday"]) == ["fun", "school"] or A.choose(weak, "Hello", CFG, ai=["fun", "school"]) != []


def test_abstract_symbols_are_never_chosen_automatically():
    got = A.choose("URGENT important question star idea done email clock", "Urgent", CFG, ai=["exclamation", "question", "star", "idea", "email", "clock", "tick"])
    assert got == [] and A.resolve(["Exclamation", "star"], CFG) == []


def test_resolve_understands_names_ids_and_ignores_made_up_ones():
    assert A.resolve(["School", "3D printer", "3dprinter", "calendar", "Unicorn", "school"], CFG) == ["school", "3dprinter", "calendar"]


def test_your_own_symbols_and_aliases_count_as_keywords():
    cfg = normalise({"icons": {"custom": [{"id": "gym", "name": "Gym", "tags": ["workout", "fitness"], "file": "x.png", "mode": "line"}],
                               "aliases": {"school": ["scouts", "cubs"]}}})
    assert A.choose("Gym membership: your workout class", "Gym", cfg, ai=["gym"]) == ["gym"]
    assert A.choose("Scouts and cubs meet tonight", "Scouts", cfg, ai=["school"]) == ["school"]       # alias = strong keyword
    assert "gym" in A.names_for_prompt(cfg)


# ----------------------------------------------------------------------------- the AI's answer line
@pytest.mark.parametrize("raw,names", [
    ("- a bullet\nSYMBOLS: school, calendar", ["school", "calendar"]), ("- a bullet\n**Symbols:** none", []), ("- a bullet\nsymbols: None.", []),
    ("- a bullet\nSYMBOLS: school and holiday", ["school", "holiday"]), ("- a bullet", None), ("- a bullet\nSYMBOLS:", None if False else [])])
def test_the_symbols_line_is_split_off_the_bullets(raw, names):
    text, got = llm.split_symbols(raw)
    assert got == names and "SYMBOLS" not in text.upper() and text.startswith("- a bullet")


def test_prompt_lists_symbols_with_their_phrases_and_examples_show_the_answer_line(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "- Dave Smith says invoice 4471 is overdue.\nACTION: Reply by Friday\nSYMBOLS: none"}}
    build_cards(fwd(""), cfg)
    msgs = fake.seen[0]["messages"]
    assert "Symbols: " in msgs[0]["content"] and "accountant (" in msgs[0]["content"] and "SYMBOLS: none" in msgs[0]["content"]
    assert re.search(r"\nSYMBOLS: [a-z, ]+$", msgs[2]["content"]) or msgs[2]["content"].endswith("SYMBOLS: none")      # worked examples end the same way
    cfg["summary"]["auto_symbols"] = False
    fake.seen.clear()
    build_cards(fwd(""), cfg)
    assert "Symbols: " not in fake.seen[0]["messages"][0]["content"] and "SYMBOLS" not in json.dumps(fake.seen[0]["messages"][1:])


# ----------------------------------------------------------------------------- on the printout
def reply(fake, symbols):
    fake.reply = {"message": {"content": "- Dave Smith says invoice 4471 for $12,480 is 20 days overdue.\n- ACTION: Reply by Friday\n" + symbols}}


def test_the_ai_and_the_email_agree_so_the_symbols_are_printed(ollama):
    cfg, fake = ollama
    reply(fake, "SYMBOLS: accountant, money")
    (card,), _ = build_cards(fwd(""), cfg)
    assert set(card.icons) == {"accountant", "money"} and any(n.startswith("symbols chosen: ") for n in card.notes)


def test_unsure_means_no_symbols(ollama):
    cfg, fake = ollama
    reply(fake, "SYMBOLS: none")
    assert build_cards(fwd(""), cfg)[0][0].icons == []
    reply(fake, "SYMBOLS: pet, birthday")                                              # nothing in the email supports these
    (card,), _ = build_cards(fwd(""), cfg)
    assert card.icons == [] and any("symbols left out" in n for n in card.notes)


def test_a_missing_symbols_line_falls_back_to_strict_keyword_evidence(ollama):
    cfg, fake = ollama
    reply(fake, "")
    (card,), _ = build_cards(fwd(""), cfg)
    assert set(card.icons) <= {"accountant", "money"}                                  # never anything the evidence doesn't back


def test_symbols_you_type_win_and_the_feature_can_be_switched_off(ollama):
    cfg, fake = ollama
    reply(fake, "SYMBOLS: accountant, money")
    assert build_cards(fwd("[school]"), cfg)[0][0].icons == ["school"]
    cfg["summary"]["auto_symbols"] = False
    assert build_cards(fwd(""), cfg)[0][0].icons == []


def test_the_built_in_summary_also_picks_symbols_but_only_with_strong_evidence():
    cfg = normalise({})
    (card,), _ = build_cards(fwd(""), cfg)
    assert card.icons and set(card.icons) <= {"accountant", "money"}
    (quiet,), _ = build_cards(fwd("", body="Hi Jack, hope you are well. Can you give me a call when you get a chance? Thanks, Dave"), cfg)
    assert quiet.icons == []


def test_full_prints_get_no_automatic_symbols():
    assert build_cards(fwd("[full]"), CFG)[0][0].icons == []


# ----------------------------------------------------------------------------- review + training data
def test_chosen_symbols_are_kept_for_review_and_your_correction_becomes_the_example(rig):
    store, svc, fake = rig
    reply(fake, "SYMBOLS: accountant, money")
    (card,), _ = build_cards(fwd(""), store.data)
    svc.submit(card)
    run = aifeedback.list_runs(svc.db)[0]
    assert sorted(run["symbols"]) == ["accountant", "money"]
    fixed = aifeedback.set_feedback(svc.db, run["id"], "good", symbols=["accountant"])
    assert fixed["symbols"] == ["accountant"]
    rows = aifeedback.training_rows(svc.db, cfg=store.data)
    assert rows[0]["symbols"] == ["accountant"]
    assert json.loads(aifeedback.export_jsonl(svc.db, "s", llm.ask, store.data).strip())["messages"][2]["content"].endswith("SYMBOLS: accountant")
    aifeedback.write_examples(svc.db, llm.USER_EXAMPLES_FILE, store.data)
    fake.seen.clear()
    build_cards(fwd(""), store.data)
    assert fake.seen[0]["messages"][2]["content"].endswith("SYMBOLS: accountant")     # your correction is what it is shown next time


def test_web_review_accepts_symbol_corrections(rig):
    from app.calsync import CalendarSync
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.web import create_app
    store, svc, fake = rig
    reply(fake, "SYMBOLS: accountant, money")
    svc.submit(build_cards(fwd(""), store.data)[0][0])
    c = create_app(store, svc, MailPoller(store, svc), CalendarSync(store, svc), ButtonController(store, svc)).test_client()
    h = {"X-Requested-With": "fetch"}
    c.post("/api/setup", json={"password": "hunter22"}, headers=h)
    rid = c.get("/api/ai-runs").get_json()["runs"][0]["id"]
    r = c.post(f"/api/ai-runs/{rid}/feedback", headers=h, json={"verdict": "good", "symbols": "Money, unicorn"}).get_json()
    assert r["run"]["symbols"] == ["money"]
    r = c.post(f"/api/ai-runs/{rid}/feedback", headers=h, json={"verdict": "good", "symbols": ""}).get_json()
    assert r["run"]["symbols"] == []


# ----------------------------------------------------------------------------- the examples and the scorer
def test_examples_have_valid_symbols_and_some_say_none():
    d = llm.load_examples()
    names = set(A.names_for_prompt(CFG))
    assert all(set(e["symbols"]) <= names for e in d["train"]) and any(e["symbols"] == [] for e in d["train"])
    ids = {i for i in icons.BUILTIN}
    assert all(set(e["symbols_ok"]) <= ids for e in d["eval"])
    for e in d["train"]:                                                             # a worked example must not break the very rules it teaches
        text = e["email"]
        subject = text.split("Subject:")[1].split("\n")[0]
        ai = A.resolve(e["symbols"], CFG)
        assert set(A.choose(text, subject, CFG, ai)) == set(ai), e["about"]


def test_evaluation_reports_whether_the_symbols_were_safe():
    res = evaluate.evaluate(CFG, use_ai=False)
    assert all(r["symbols_safe"] for r in res) and any(r["symbols"] for r in res)
    assert "symbols safe 100%" in evaluate.report(res, False)
