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
def test_the_library_is_big_and_every_symbol_has_a_reasoned_profile():
    assert set(A.SPECS) <= set(icons.BUILTIN) and not set(A.SPECS) & A.NEVER_AUTO and len(A.SPECS) == 17
    terms = 0
    for iid, sp in A.SPECS.items():
        assert sp["about"] and len(sp["subject"]) >= 10 and len(sp["sender"]) >= 2 and len(sp["strong"]) >= 12 and len(sp["weak"]) >= 3, iid
        for t in (*sp["subject"], *sp["sender"], *sp["strong"], *sp["weak"]):
            re.compile(A._compile(t))                                        # every entry is a valid pattern
            terms += 1
    assert terms > 1500
    phrases = [t for sp in A.SPECS.values() for t in (*sp["subject"], *sp["strong"]) if " " in t or "-" in t]
    assert len(phrases) > 400                                                # lots of phrases, not just single words


@pytest.mark.parametrize("term,text,hit", [
    ("blood test", "Please book a blood-test before Friday", True), ("blood test", "blood tests are due", True),
    ("performance review", "Your performance review is next week", True), ("celebrat*", "we are celebrating", True),
    ("driver's licence", "renew your drivers licence", True), ("stand-up", "daily standup at 9", True),
    ("g-code", "send me the g-code", True), ("re:turning \\d+", "she is turning 7", True),
    ("car", "scarf and carpet", False), ("pet", "petition and carpet", False), ("rent", "different", False),
])
def test_phrases_match_whole_words_and_variations(term, text, hit):
    assert bool(A._hits([term], text)) is hit


def test_describe_gives_the_ai_the_kind_of_email_and_typical_subjects_for_shortlisted_symbols_only():
    d = A.describe(CFG, ["money", "work"])
    assert len(d) == 2 and d[0].startswith("money = payments and banking") and "subjects like: receipt" in d[0] and d[1].startswith("work = business")
    assert max(len(x) for x in A.describe(CFG, list(A.SPECS))) < 330                   # short enough for a small model's prompt
    assert A.describe(CFG, ["nonsense", "exclamation"]) == [] or all(not x.startswith("nonsense") for x in A.describe(CFG, ["nonsense"]))


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


def test_the_ai_is_shown_only_the_symbols_the_email_has_evidence_for_each_described(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "- Dave Smith says invoice 4471 is overdue.\nACTION: Reply by Friday\nSYMBOLS: none"}}
    build_cards(fwd(""), cfg)
    msgs = fake.seen[0]["messages"]
    system = msgs[0]["content"]
    assert "Symbols: " in system and "accountant = accounting and tax emails" in system and "(subjects like: invoice" in system
    assert "pet =" not in system and "3d printer =" not in system and "birthday =" not in system                   # no evidence -> not offered
    assert re.search(r"\nSYMBOLS: [a-z0-9, ]+$", msgs[2]["content"])                                                # worked examples end the same way
    cfg["summary"]["auto_symbols"] = False
    fake.seen.clear()
    build_cards(fwd(""), cfg)
    assert "Symbols: " not in fake.seen[0]["messages"][0]["content"] and "SYMBOLS" not in json.dumps(fake.seen[0]["messages"][1:])


def test_no_evidence_means_the_ai_is_not_even_asked_about_symbols(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "- Sam asks if you are free sometime.\n- Nothing specific is planned."}}
    build_cards(plain_forward("hey, lol are you free sometime? cheers mate"), cfg)
    assert "Symbols: " not in fake.seen[0]["messages"][0]["content"]


# ----------------------------------------------------------------------------- on the printout
def plain_forward(body, subject="Hello"):
    from test_core import mk
    from app.mailparse import parse
    raw = mk("Fwd: " + subject, f"\n\n---------- Forwarded message ---------\nFrom: Sam <sam@x.com>\nDate: Mon\nSubject: {subject}\nTo: me\n\n{body}")
    return parse(raw)


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
    assert card.icons and set(card.icons) <= {"accountant", "money", "work"}
    (quiet,), _ = build_cards(plain_forward("Hi Jack, hope you are well. Can you give me a call when you get a chance? Thanks, Sam"), cfg)
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


# ----------------------------------------------------------------------------- the clues: subject, sender, tone
def ev_for(subject="", sender="", body="", raw=""):
    return A.analyse(subject, sender, body, raw or body, CFG)


@pytest.mark.parametrize("subject,expect", [
    ("Payment received", "money"), ("Receipt for your purchase", "money"), ("Your receipt", "money"), ("Refund processed", "money"),
    ("Tax invoice 4471", "accountant"), ("Appointment reminder", "calendar"), ("Your order has shipped", "shopping"),
    ("Flight QF431 retimed", "holiday"), ("Prescription ready", "health"), ("Vehicle registration renewal", "car"),
    ("Vaccination due for Max", "pet"), ("Happy birthday Sam", "birthday"), ("Lease renewal", "home"), ("Excursion permission slip", "school"),
    ("You're invited: BBQ on Saturday", "fun"), ("Filament back in stock", "3dprinter"), ("Timesheet for October", "bookwork")])
def test_one_clear_subject_clue_is_enough_on_its_own(subject, expect):
    assert expect in A.decide(ev_for(subject=subject))


@pytest.mark.parametrize("subject", ["Update", "Hello", "Registration closing soon", "Quote for 50 units", "Reminder", "Re: your question", "Thank you", "Fwd: info"])
def test_generic_subjects_do_not_pick_anything(subject):
    assert A.decide(ev_for(subject=subject)) == []


def test_receipt_or_payment_in_the_subject_keeps_money_even_on_a_shops_order_email():
    got = A.decide(ev_for(subject="Receipt for your order #4421", sender="Bunnings", body="Thanks for shopping with us. Your order will be delivered on Friday."))
    assert "money" in got and "shopping" in got


@pytest.mark.parametrize("sender,body,expect", [
    ("Northside Primary School", "The Year 9 excursion is on Friday.", "school"), ("Dr Patel Medical Centre", "Please arrive 10 minutes early.", "health"),
    ("Qantas", "Your booking is confirmed.", "holiday"), ("Ray White Real Estate", "The inspection is on Tuesday.", "home"),
    ("Commonwealth Bank", "A transaction was made on your card.", "money"), ("Petbarn", "Your order is on its way.", "pet")])
def test_the_sender_counts(sender, body, expect):
    assert expect in A.decide(ev_for(subject="Hello", sender=sender, body=body))


FORMAL = ("Dear Mr Chen,\n\nPlease find attached the revised proposal. We would like to confirm the timeline and should you have any questions "
          "do not hesitate to contact me.\n\nKind regards,\nAnna Lee\nGeneral Manager\nNorthwind Pty Ltd")
CASUAL = "hey! lol are you free sometime? cheers mate\nsent from my phone"


def test_a_formal_business_letter_gets_the_briefcase_even_without_a_work_keyword():
    ev = ev_for(subject="Hello", body="Dear Mr Chen, thank you.", raw=FORMAL)
    assert A.formality(FORMAL) >= 3 and "work" in A.decide(ev) and "formal tone" in ev["work"]["why"]
    assert "work" not in A.decide(ev_for(subject="Hello", body="hey lol", raw=CASUAL)) and A.formality(CASUAL) < 0
    assert A.formality("Kind regards,\nSam") <= 1 and A.formality("Hi!\nRegards, Sam") <= 1                # a sign-off alone proves nothing


def test_formal_tone_alone_is_not_enough_when_it_is_only_mildly_formal():
    ev = ev_for(subject="Hello", body="Regarding the thing", raw="Regarding the thing, kindly reply.\nRegards, Sam")
    assert "work" not in A.decide(ev)


def test_overlapping_clues_are_not_counted_twice():
    assert A._hits(["order", "your order", "order confirmation"], "Your order confirmation") == ["your order", "order confirmation"]
    one = ev_for(subject="Your order")["shopping"]["score"]
    assert one == A.SUBJECT_STRONG                                          # not 4 + 4


def test_the_reason_is_shown_in_the_history_note(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "- Sam sent a receipt.\\n- Payment received for the order.\\nSYMBOLS: money"}}
    (card,), _ = build_cards(plain_forward("Payment received, thanks", subject="Payment received"), cfg)
    assert card.icons == ["money"] and any(n.startswith("symbols chosen: Money (subject 'payment") for n in card.notes)
