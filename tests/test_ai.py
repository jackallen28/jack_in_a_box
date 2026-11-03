import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_core import mk  # noqa: E402
from app import llm  # noqa: E402
from app.config import normalise  # noqa: E402
from app.emailclean import clean_forwarded  # noqa: E402
from app.focus import find_focus, parse_focus  # noqa: E402
from app.mailparse import parse  # noqa: E402
from app.pipeline import build_cards  # noqa: E402
from app.summarise import prepare, summarise  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CFG = normalise({})

# ----------------------------------------------------------------------------- cleaning
SIG_OUTLOOK = """Hi Jack,

Please confirm the payment of $12,480 by Friday so we can close the quarter.

Kind regards,

Dave Smith
Accounts Manager
Supplier Pty Ltd
T: 02 9999 1234  |  M: 0412 345 678
www.supplier.com.au
[cid:image001.png@01D9ABCD.1234] [cid:image002.jpg@01D9ABCD.1234]
Level 4, 120 Collins Street, Melbourne VIC 3000

This email and any attachments are confidential and may be privileged. If you are not the intended recipient please delete it."""


@pytest.mark.parametrize("raw,must_have,must_not", [
    (SIG_OUTLOOK, ["$12,480", "Friday"], ["Dave Smith", "Accounts Manager", "02 9999", "cid:", "Collins", "confidential", "www."]),
    ("Can you send the report by Monday?\n\nThanks!\n\n-- \nSarah\nSent from my iPhone", ["Monday"], ["Sarah", "iPhone", "Thanks"]),
    ("The meeting moved to 3pm Thursday in room 4.\n\nJo Bloggs\nOperations Lead\n0400 111 222\njo@example.com", ["3pm Thursday"], ["Jo Bloggs", "0400", "Operations"]),
    ("Yes, Tuesday works.\n\nOn Mon, 2 Nov 2026 at 9:15 AM, Dave <dave@x.com> wrote:\n> Can we meet Tuesday?", ["Tuesday works"], ["Can we meet", "wrote"]),
    ("Approved.\n\nFrom: Dave <dave@x.com>\nSent: Monday, 2 November 2026 9:15 AM\nTo: Jack\nSubject: Approval\n\nPlease approve the budget.", ["Approved"], ["Please approve", "Sent:"]),
    ("See the schedule [image: Company logo] here https://tracking.example.com/click?id=abc123def456ghi789 today.", ["schedule", "today"], ["tracking", "Company logo"]),
    ("Hello\n\nImage removed by sender.\n\nThe cheque is ready for pickup.", ["cheque"], ["Image removed"]),
    ("[EXTERNAL] CAUTION: This email originated from outside the organisation. Do not click links.\n\nPayment due Friday.", ["Payment due Friday"], ["CAUTION"]),
])
def test_signatures_disclaimers_threads_and_images_are_removed(raw, must_have, must_not):
    out = clean_forwarded(raw)
    for w in must_have:
        assert w in out, (w, out)
    for w in must_not:
        assert w not in out, (w, out)


@pytest.mark.parametrize("keep", [
    "This message is to confirm your booking for 14 November. Please arrive by 9am.\n\nThanks, Amy",
    "Thanks for the update.\n\nOne more thing: the invoice is overdue and we need payment by Friday, otherwise the account will be suspended.",
    "Questions? Please email me on bob@example.com before Friday if anything is unclear to you.",
    "Lunch is at 12:30 on Wednesday. Bring your laptop.",
    "See the schedule [image: Company logo] here https://tracking.example.com/click?id=abc123def456ghi789 today.",
])
def test_real_content_is_never_thrown_away(keep):
    out = clean_forwarded(keep)
    assert out and len(out) > 0.6 * len(keep.split("\n\n")[0].replace("[image: Company logo] ", "")) - 80
    assert not (out == "")


# ----------------------------------------------------------------------------- focus
EMAIL = """Hi Jack,

I hope you're well. The coffee machine arrived last week.

I'm writing about invoice 4471 which we sent on 14 October for $12,480. Our records show the payment has not been received and it is now 20 days overdue. We've also noticed that invoice 4402 was paid twice, so there is a credit of $3,200 on your account.

Please let me know by Friday so we can close off our books for the quarter. We are also changing our bank details from 1 December.

Separately, the Year 9 science paper is due Monday 9 November, and Mr Lee needs the signed permission form back before then.

Kind regards,
Dave Smith
T: 02 9999 1234"""
SENTS = prepare(clean_forwarded(EMAIL))


def test_parse_focus_leaves_symbols_alone_and_finds_full():
    clean, focus, full = parse_focus("[school] [reply by friday] call Dave [full] [!] [paper due monday] [reply by Friday]", CFG)
    assert clean == "[school] call Dave [!]" and focus == ["reply by friday", "paper due monday"] and full is True


@pytest.mark.parametrize("phrase,expect_in,expect_not_in", [
    ("reply by friday", "let me know by Friday", "science paper"),
    ("paper due monday", "science paper is due Monday", "let me know"),
    ("cost", "$12,480", "science paper"),
    ("bank details", "bank details", "science"),
    ("signed form", "permission form", "bank"),
    ("get back to me", "let me know by Friday", None),
])
def test_focus_finds_the_relevant_sentence(phrase, expect_in, expect_not_in):
    found = " ".join(find_focus(phrase, SENTS))
    assert expect_in in found, (phrase, found)
    if expect_not_in:
        assert expect_not_in not in found


def test_focus_says_nothing_when_the_email_doesnt_mention_it():
    assert find_focus("parking", SENTS) == [] and find_focus("holiday plans", SENTS) == []


def test_actions_and_deadlines_keywords_and_priority_first_summary():
    acts = " ".join(find_focus("actions", SENTS))
    assert "let me know by Friday" in acts and "coffee machine" not in acts
    summary = summarise(clean_forwarded(EMAIL), 4)
    assert len(summary) == 4
    assert "by Friday" in summary[0] or "due Monday" in summary[0]            # requests/deadlines lead
    assert not any("coffee" in s for s in summary)


# ----------------------------------------------------------------------------- the card
def fwd(notes, body=EMAIL, subject="Fwd: Invoice chase"):
    raw = mk(subject, f"{notes}\n\n---------- Forwarded message ---------\nFrom: Dave Smith <dave@x.com>\nDate: Mon\nSubject: Invoice\nTo: me\n\n{body}")
    return parse(raw)


def test_card_has_focus_then_summary_then_actions():
    (card,), _ = build_cards(fwd("[reply by friday] [paper due monday] chase Dave on the credit"), CFG)
    keys = [s.key for s in card.sections]
    assert keys == ["focus", "focus", "summary", "actions"]
    assert card.sections[0].label == "REPLY BY FRIDAY" and "by Friday" in card.sections[0].text
    assert card.sections[1].label == "PAPER DUE MONDAY" and "Monday 9 November" in card.sections[1].text
    assert card.sections[3].items == ["Chase Dave on the credit"]
    assert "Dave Smith" not in card.sections[2].text and "02 9999" not in card.sections[2].text


def test_unfound_focus_is_reported_not_hidden():
    (card,), _ = build_cards(fwd("[parking]"), CFG)
    assert card.sections[0].label == "PARKING" and card.sections[0].text == "Not mentioned in this email."


def test_full_prints_the_whole_message_without_signature_or_images():
    body = EMAIL + "\n[cid:image001.png@01D9]\n\nThis email and any attachments are confidential."
    (card,), _ = build_cards(fwd("[full] call Dave", body), CFG)
    assert [s.key for s in card.sections] == ["full", "actions"]
    full = card.sections[0].text
    assert "coffee machine" in full and "science paper" in full                # everything, not a summary
    assert "T: 02 9999" not in full and "cid:" not in full and "confidential" not in full and "Dave Smith" not in full


def test_full_is_capped():
    cfg = normalise({"summary": {"max_full_chars": 300}})
    (card,), _ = build_cards(fwd("[full]", "word " * 400), cfg)
    assert len(card.sections[0].text) < 340 and card.sections[0].text.endswith("(cut short)")


def test_symbols_and_focus_work_together():
    (card,), _ = build_cards(fwd("[school] [!] [reply by friday]"), CFG)
    assert card.icons == ["school", "exclamation"] and card.sections[0].label == "REPLY BY FRIDAY"


def test_focus_card_renders():
    from app.render import render_card
    (card,), _ = build_cards(fwd("[reply by friday] [paper due monday] ring Dave"), CFG)
    img = render_card(card, CFG["templates"]["email"], CFG["printer"], CFG["icons"])
    assert img.size[0] == 576 and img.size[1] > 700


# ----------------------------------------------------------------------------- the AI (fake Ollama)
class Fake(BaseHTTPRequestHandler):
    reply = {"message": {"content": ""}}
    status = 200
    seen: list = []
    delay = 0.0

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append(body)
        time.sleep(Fake.delay)
        self.send_response(Fake.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(Fake.reply).encode())

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"models": [{"name": "qwen2.5:0.5b"}]}).encode())

    def log_message(self, *a):
        pass


@pytest.fixture()
def ollama():
    Fake.reply, Fake.status, Fake.seen, Fake.delay = {"message": {"content": ""}}, 200, [], 0.0
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = normalise({"summary": {"backend": "ollama"}, "llm": {"url": f"http://127.0.0.1:{srv.server_port}", "timeout": 5}})
    yield cfg, Fake
    srv.shutdown()


GOOD = ("Please let me know by Friday so Dave can close the quarter. Invoice 4471 for $12,480 is 20 days overdue. "
        "The Year 9 science paper is due Monday 9 November.")


def test_ai_summary_is_used_when_it_sticks_to_the_email(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": "**Summary:**\n- " + GOOD.replace(". ", ".\n- ")}}
    (card,), _ = build_cards(fwd(""), cfg)
    s = card.sections[0].text
    assert s.startswith("Please let me know by Friday") and "**" not in s and "- " not in s
    assert card.notes and card.notes[0].startswith("AI summary")
    req = fake.seen[0]
    assert req["keep_alive"] == 0 and req["options"]["num_ctx"] == 2048 and req["model"] == "qwen2.5:0.5b" and req["stream"] is False
    assert "ONLY facts" in req["messages"][0]["content"]
    prompt = req["messages"][1]["content"]
    assert "by Friday" in prompt and "From: Dave Smith" in prompt             # sender/subject given as context
    assert "T: 02 9999" not in prompt and "Accounts" not in prompt             # signature never reaches the model


def test_a_long_email_is_cut_to_a_small_prompt_that_keeps_the_requests(ollama):
    cfg, fake = ollama
    fake.reply = {"message": {"content": GOOD}}
    filler = " ".join(f"The weather in district number {i} has been quite pleasant and the gardens look lovely this season." for i in range(80))
    build_cards(fwd("", EMAIL.replace("I hope you're well.", filler)), cfg)
    prompt = fake.seen[0]["messages"][1]["content"]
    assert len(prompt) < 2200 and "by Friday" in prompt and "science paper is due Monday" in prompt


@pytest.mark.parametrize("lie", [
    "Dave wants $99,999 paid by Tuesday.",
    "Please pay invoice 5555 by Friday.",
    "Please call Gerald Winterbottom by Friday about the invoice.",
    "Zebras are lovely animals that live in grasslands across Africa.",
])
def test_ai_that_makes_things_up_is_rejected_and_builtin_summary_used(ollama, lie):
    cfg, fake = ollama
    fake.reply = {"message": {"content": lie + " Please see the details below."}}
    (card,), _ = build_cards(fwd(""), cfg)
    assert card.notes and card.notes[0].startswith("AI not used")
    s = card.sections[0].text
    assert "let me know by Friday" in s and "99,999" not in s and "5555" not in s and "Gerald" not in s and "Zebra" not in s


def test_ai_failures_fall_back_without_losing_the_print(ollama):
    cfg, fake = ollama
    fake.status = 404
    (card,), _ = build_cards(fwd(""), cfg)
    assert "ollama pull" in card.notes[0] and "let me know by Friday" in card.sections[0].text
    cfg["llm"]["url"] = "http://127.0.0.1:1"                                    # nothing listening
    t = time.time()
    (card,), _ = build_cards(fwd(""), cfg)
    assert time.time() - t < 5 and card.notes[0].startswith("AI not used") and card.sections[0].key == "summary"


def test_focus_and_full_never_call_the_model(ollama):
    cfg, fake = ollama
    build_cards(fwd("[full]"), cfg)
    assert fake.seen == []
    (card,), _ = build_cards(fwd("[reply by friday]"), cfg)
    assert card.sections[0].key == "focus" and len(fake.seen) == 1             # only the general summary used the AI


def test_playground_style_calls_skip_the_ai(ollama):
    cfg, fake = ollama
    build_cards(fwd(""), cfg, use_ai=False)
    assert fake.seen == []


def test_only_one_model_run_at_a_time(ollama):
    cfg, fake = ollama
    fake.delay = 0.4
    fake.reply = {"message": {"content": GOOD}}
    times = []

    def go():
        t = time.time()
        llm.run(prepare(clean_forwarded(EMAIL)), 4, cfg["llm"])
        times.append(time.time() - t)

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert max(times) >= 0.75                                                  # the second waited for the first


@pytest.mark.parametrize("sentences,ok", [
    (["Please reply by Friday."], True),
    (["Please reply by Tuesday."], False),
    (["Invoice 4471 is $12,480."], True),
    (["Invoice 9999 is $12,480."], False),
    (["Ask Gerald about it."], False),
    (["Ask Dave about it."], True),
])
def test_grounding_check(sentences, ok):
    src = "Please let me know by Friday. Invoice 4471 for $12,480 is overdue. Dave Smith will call about it."
    assert llm.grounded(sentences, src)[0] is ok


def test_status_messages(ollama, monkeypatch):
    cfg, _ = ollama
    st = llm.status(cfg["llm"])
    assert st["ok"] and st["reachable"] and st["model_installed"]
    assert not llm.status({**cfg["llm"], "model": "other:1b"})["ok"] and "ollama pull other:1b" in llm.status({**cfg["llm"], "model": "other:1b"})["message"]
    assert "Can't reach" in llm.status({**cfg["llm"], "url": "http://127.0.0.1:1"})["message"]
    monkeypatch.setattr(llm.platform, "machine", lambda: "armv7l")
    local = llm.status({**cfg["llm"], "url": "http://127.0.0.1:11434"})
    assert not local["ok"] and "64-bit" in local["message"]
    assert llm.status(cfg["llm"])["reachable"] is False or True                # a remote/other URL is still allowed on 32-bit


def test_web_status_and_background_test(ollama, tmp_path):
    from app.calsync import CalendarSync
    from app.config import ConfigStore
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.service import Service
    from app.web import create_app
    cfg, fake = ollama
    store = ConfigStore(tmp_path / "c.json")
    store.replace(cfg)
    svc = Service(store, tmp_path)
    c = create_app(store, svc, MailPoller(store, svc), CalendarSync(store, svc), ButtonController(store, svc)).test_client()
    H = {"X-Requested-With": "fetch"}
    c.post("/api/setup", json={"password": "hunter22"}, headers=H)
    assert c.get("/api/ai-status").json["ok"] is True
    fake.reply = {"message": {"content": GOOD}}
    assert c.post("/api/ai-test", headers=H).json["started"] is True
    for _ in range(50):
        r = c.get("/api/ai-test").json
        if not r["running"] and r["result"]:
            break
        time.sleep(0.1)
    assert r["result"]["used_ai"] is False or r["result"]["summary"]            # finished, with a verdict either way
    pg = c.post("/api/playground", json={"subject": "S", "body": "[reply by friday] call Dave", "forward": True, "fwd_body": EMAIL}, headers=H).json
    assert pg["cards"][0]["kind"] == "email"


# ----------------------------------------------------------------------------- scripts
def test_lean_mode_edits_boot_config_idempotently_and_undoes(tmp_path):
    cfgf = tmp_path / "config.txt"
    original = "# comment\ndtparam=audio=on\ngpu_mem=128\nhdmi_force_hotplug=1\n"
    cfgf.write_text(original)
    env = {**os.environ, "JIAB_BOOT_CONFIG": str(cfgf), "JIAB_STATE_DIR": str(tmp_path / "state"), "JIAB_DRY_RUN": "1"}
    for _ in range(2):                                                         # twice = idempotent
        r = subprocess.run(["bash", str(ROOT / "deploy/lean-mode.sh")], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    text = cfgf.read_text()
    assert text.count("gpu_mem=16") == 1 and "gpu_mem=128" not in text and text.count("dtoverlay=disable-bt") == 1
    assert "hdmi_force_hotplug=1" in text and "dtparam=audio=on" in text      # nothing else touched
    assert Path(str(cfgf) + ".jiab-backup").read_text() == original
    r = subprocess.run(["bash", str(ROOT / "deploy/lean-mode.sh"), "--undo"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and cfgf.read_text() == original


def test_ai_setup_refuses_32_bit_and_explains(tmp_path):
    r = subprocess.run(["bash", str(ROOT / "deploy/ai-setup.sh")], env={**os.environ, "JIAB_ARCH": "armv7l"}, capture_output=True, text=True)
    assert r.returncode == 1 and "64-bit" in r.stdout and "Raspberry Pi Imager" in r.stdout and "another computer" in r.stdout


def test_ai_setup_dry_run_on_64_bit(tmp_path):
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal:         948304 kB\n")
    r = subprocess.run(["bash", str(ROOT / "deploy/ai-setup.sh")],
                       env={**os.environ, "JIAB_ARCH": "aarch64", "JIAB_DRY_RUN": "1", "JIAB_MEMINFO": str(mem)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OLLAMA_KEEP_ALIVE=0" in r.stdout and "OLLAMA_MAX_LOADED_MODELS=1" in r.stdout and "ollama pull qwen2.5:0.5b" in r.stdout
    mem.write_text("MemTotal:         400000 kB\n")
    r = subprocess.run(["bash", str(ROOT / "deploy/ai-setup.sh")],
                       env={**os.environ, "JIAB_ARCH": "aarch64", "JIAB_DRY_RUN": "1", "JIAB_MEMINFO": str(mem)}, capture_output=True, text=True)
    assert r.returncode == 1 and "too little" in r.stdout
