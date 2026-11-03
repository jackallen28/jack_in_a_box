"""Forward summaries as bullet points, "include image", the training examples and the evaluator."""
import json
import re
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ai import EMAIL, Fake, ollama  # noqa: E402,F401  (the fixture is used by name)
from test_core import mk, summary_text  # noqa: E402
from app import evaluate, llm  # noqa: E402
from app.config import normalise  # noqa: E402
from app.focus import detect_image_request  # noqa: E402
from app.mailparse import parse  # noqa: E402
from app.pipeline import build_cards  # noqa: E402
from app.render import render_card  # noqa: E402
from app.summarise import bulletise, bullets  # noqa: E402

CFG = normalise({})
FWD = "---------- Forwarded message ---------\nFrom: Dave Smith <dave@x.com>\nDate: Mon\nSubject: Invoice\nTo: me\n\n"


def picture():
    return Image.effect_noise((320, 240), 90).convert("RGB")


def fwd(notes, images=(), body=EMAIL):
    return parse(mk("Fwd: Invoice chase", f"{notes}\n\n{FWD}{body}", images=images))


# ----------------------------------------------------------------------------- "include image"
@pytest.mark.parametrize("notes,yes,rest", [
    ("chase Dave include image", True, "chase Dave"),
    ("Please INCLUDE IMAGE and ring Dave", True, "ring Dave"),
    ("chase Dave, include image", True, "chase Dave"),
    ("include the image. reply by friday", True, "reply by friday"),
    ("[image] chase Dave", True, "chase Dave"),
    ("reply with attachment please", False, "reply with attachment please"),
    ("the image quality is bad", False, "the image quality is bad"),
    ("", False, ""),
])
def test_include_image_is_spotted_anywhere_in_your_note(notes, yes, rest):
    got, left = detect_image_request(notes)
    assert got is yes and left.strip() == rest


def test_forwarded_pictures_print_only_when_you_ask():
    (plain,), _ = build_cards(fwd("chase Dave", images=[picture()]), CFG)
    assert plain.images == []
    (asked,), _ = build_cards(fwd("chase Dave, include image", images=[picture()]), CFG)
    assert len(asked.images) == 1
    assert [s.items for s in asked.sections if s.key == "actions"] == [["Chase Dave"]]    # the phrase isn't left in the to-do


def test_asking_for_an_image_when_there_is_none_is_noted_not_fatal():
    (card,), _ = build_cards(fwd("include image"), CFG)
    assert card.images == [] and any("image" in n.lower() for n in card.notes)


# ----------------------------------------------------------------------------- bullets
@pytest.mark.parametrize("sentence,expect", [
    ("Hi Jack, I hope you are well.", None),
    ("Please let me know by Friday so Dave can close the quarter.", "[ ] Reply by Friday so Dave can close the quarter"),
    ("Invoice 4471 for $12,480 is 20 days overdue.", "Invoice 4471 for $12,480 is 20 days overdue."),
    ("Register on our website to secure your seat.", "[ ] Register on our website to secure your seat"),
    ("Check-in opens 24 hours before departure.", "Check-in opens 24 hours before departure."),
    ("If you want to cancel, call us on 1800 555 010 before 23 November.", "[ ] If you want to cancel, call us on 1800 555 010 before 23 November"),
])
def test_bulletise(sentence, expect):
    got = bulletise(sentence)
    if expect is None:
        assert not got.startswith("[ ]")
    else:
        assert got == expect


@pytest.mark.parametrize("sentence", ["No need to reply, just turn up.", "No action is needed from you.", "Nothing further is required."])
def test_never_prints_that_no_reply_is_needed(sentence):
    assert bulletise(sentence) == ""
    assert bullets([sentence, "The meeting is at 3pm on Thursday in Room 4."], 4) == ["The meeting is at 3pm on Thursday in Room 4."]


def test_bullets_put_information_before_actions():
    out = bullets(["Please send the signed form by Monday.", "The excursion costs $45 and leaves at 8am.", "The class is going to the zoo."], 4)
    assert out[-1].startswith("[ ] ") and not any(b.startswith("[ ] ") for b in out[:-1])


def test_the_summary_section_is_bullets_and_renders_with_round_bullets_and_tick_boxes():
    (card,), _ = build_cards(fwd(""), CFG)
    sec = next(s for s in card.sections if s.key == "summary")
    assert sec.bullets and len(sec.items) >= 2
    img = render_card(card, CFG["templates"]["email"], CFG["printer"], CFG["icons"])
    assert img.size[0] == 576 and img.size[1] > 300


# ----------------------------------------------------------------------------- the AI sees the examples
def test_worked_examples_are_sent_before_the_real_email(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "- Dave Smith says invoice 4471 is overdue.\n- ACTION: Reply by Friday."}}
    cfg["llm"]["examples"] = 2
    build_cards(fwd(""), cfg)
    msgs = fake.seen[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert msgs[2]["content"].startswith("- ") and "ACTION:" in " ".join(m["content"] for m in msgs[:5])
    assert "From: Dave Smith" in msgs[-1]["content"] and "BULLET POINTS" in msgs[-1]["content"]


def test_examples_can_be_switched_off(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "- Dave Smith says invoice 4471 is overdue.\n- ACTION: Reply by Friday."}}
    cfg["llm"]["examples"] = 0
    build_cards(fwd(""), cfg)
    assert [m["role"] for m in fake.seen[0]["messages"]] == ["system", "user"]


def test_ai_bullets_become_bullets_and_tick_boxes_and_never_say_no_action(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "- Dave Smith says invoice 4471 for $12,480 is overdue.\n- ACTION: Reply by Friday.\n- No action needed."}}
    (card,), _ = build_cards(fwd(""), cfg)
    sec = next(s for s in card.sections if s.key == "summary")
    assert sec.bullets and sec.items == ["Dave Smith says invoice 4471 for $12,480 is overdue.", "[ ] Reply by Friday"]
    assert card.notes[0].startswith("AI summary")


def test_the_whole_thread_reaches_the_model_oldest_part_included():
    text = llm.condense(["Now it is paid.", "It was 20 days overdue.", "Pay by Friday."], 3000, [0, 1, 1], ["LATEST MESSAGE - Dave, 4 Nov", "EARLIER - Jo, 2 Nov"])
    assert text.index("LATEST MESSAGE - Dave, 4 Nov:") < text.index("EARLIER - Jo, 2 Nov:") and "20 days overdue" in text


def test_a_possessive_name_is_not_flagged_as_invented():
    ok, bad = llm.grounded(["Jack's interview is on Monday."], "Tom: Jack is free Monday. Interview time?")
    assert ok, bad


# ----------------------------------------------------------------------------- the training examples
DATA = json.loads(llm.EXAMPLES_FILE.read_text("utf-8"))


def test_there_are_ten_worked_examples_and_a_separate_test_set():
    assert len(DATA["train"]) == 10 and len(DATA["eval"]) >= 8
    train_emails = {e["email"] for e in DATA["train"]}
    for case in DATA["eval"]:
        assert not any(m["text"] in e for e in train_emails for m in case["messages"]), case["name"]      # held out


@pytest.mark.parametrize("ex", DATA["train"], ids=[e["about"] for e in DATA["train"]])
def test_every_worked_example_is_well_formed_and_grounded(ex):
    assert 2 <= len(ex["bullets"]) <= 5 and ex["email"].startswith("From:")
    assert sum(b.startswith("ACTION:") for b in ex["bullets"]) <= 2
    assert not any(re.match(r"(?i)(?:ACTION:\s*)?no (?:action|need)", b) for b in ex["bullets"])
    ok, bad = llm.grounded([b.removeprefix("ACTION: ") for b in ex["bullets"]], ex["email"])
    assert ok, bad


def test_the_closest_examples_are_chosen():
    invoice = llm.pick_examples("From: Accounts\nSubject: Overdue invoice 88\nYour invoice is 30 days overdue, please pay by Friday.", 1)[0]
    assert "invoice" in invoice["about"]
    assert llm.pick_examples("anything", 0) == []


# ----------------------------------------------------------------------------- the evaluator
def test_builtin_evaluation_scores_every_held_out_email():
    res = evaluate.evaluate(CFG, use_ai=False)
    assert len(res) == len(DATA["eval"])
    assert sum(r["facts_ok"] for r in res) / len(res) >= 0.8 and sum(r["actions"] for r in res) / len(res) >= 0.8
    assert "emails:" in evaluate.report(res, False)


def test_ai_evaluation_runs_through_a_fake_ollama_and_reports(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "- Priya Nair wants a quote for 50 units of the standard model.\n- ACTION: Send the quote by Wednesday 11 November."}}
    res = evaluate.evaluate(cfg, use_ai=True, cases=2)
    assert len(res) == 2 and len(fake.seen) == 2
    assert res[0]["accepted"] and res[0]["facts_ok"] and res[0]["actions"]
    assert res[1]["accepted"] is False                       # the same answer is not grounded in the second email -> rejected
    text = evaluate.report(res, True, show=True)
    assert "AI accepted" in text and "AI not used" in text


def test_evaluate_command_line(ollama, capsys):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "- Priya Nair wants a quote for 50 units of the standard model.\n- ACTION: Send the quote by Wednesday 11 November."}}
    assert evaluate.main(["--url", cfg["llm"]["url"], "--cases", "1", "--examples", "0", "--config", "/nonexistent/none.json"]) == 0
    assert "1 emails:" in capsys.readouterr().out
    assert evaluate.main(["--url", "http://127.0.0.1:1", "--cases", "1"]) == 2
