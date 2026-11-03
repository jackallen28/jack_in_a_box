"""AI review: the history of summarised emails, your corrections, and feeding them back."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_ai import Fake, fwd, ollama  # noqa: E402,F401  (the fixture is used by name)
from app import aifeedback, evaluate, llm  # noqa: E402
from app.config import ConfigStore  # noqa: E402
from app.pipeline import build_cards  # noqa: E402
from app.service import Service  # noqa: E402

ANSWER = ("- Dave Smith says invoice 4471 for $12,480 is 20 days overdue.\n- The Year 9 science paper is due Monday 9 November.\n"
          "- ACTION: Reply by Friday so Dave can close the quarter.")


@pytest.fixture()
def rig(ollama, tmp_path, monkeypatch):
    cfg_dict, fake = ollama
    monkeypatch.setattr(llm, "USER_EXAMPLES_FILE", tmp_path / "ai_examples.json")
    store = ConfigStore(tmp_path / "c.json")
    d = store.data
    d["printer"]["backend"] = "dummy"
    d["summary"]["backend"] = "ollama"
    d["llm"].update(cfg_dict["llm"])
    store.replace(d)
    fake.reply = {"message": {"content": ANSWER}}
    return store, Service(store, tmp_path), fake


def summarise_and_queue(store, svc, notes=""):
    (card,), _ = build_cards(fwd(notes), store.data)
    svc.submit(card)
    return card


# ----------------------------------------------------------------------------- keeping the history
def test_each_forward_summary_is_kept_with_its_printout(rig):
    store, svc, fake = rig
    card = summarise_and_queue(store, svc)
    run = aifeedback.list_runs(svc.db)[0]
    job = svc.db.one("SELECT id FROM jobs")
    assert run["job_id"] == job["id"] and run["used_ai"] and run["status"] == "new" and run["subject"] == "Invoice chase"
    assert run["bullets"][-1] == "ACTION: Reply by Friday so Dave can close the quarter"
    assert run["printed"]["actions"] and "ACTION" not in " ".join(run["printed"]["summary"])           # as printed: overview and actions apart
    full = aifeedback.get_run(svc.db, run["id"])
    assert full["email"].startswith("From: Dave Smith\nSubject: Invoice chase") and "by Friday" in full["email"]       # exactly what the model saw
    assert "T: 02 9999" not in full["email"]                                                                         # signature never kept


def test_the_built_in_summary_is_kept_too_and_history_can_be_switched_off(rig):
    store, svc, fake = rig
    d = store.data
    d["summary"]["backend"] = "extractive"
    store.replace(d)
    summarise_and_queue(store, svc)
    assert [r["used_ai"] for r in aifeedback.list_runs(svc.db)] == [False]
    d = store.data
    d["llm"]["keep_history"] = False
    store.replace(d)
    summarise_and_queue(store, svc)
    assert len(aifeedback.list_runs(svc.db)) == 1


def test_full_and_plain_reminders_leave_no_history(rig):
    store, svc, fake = rig
    summarise_and_queue(store, svc, "[full]")
    assert aifeedback.list_runs(svc.db) == []


def test_old_unreviewed_history_is_pruned_but_reviewed_summaries_stay(rig):
    store, svc, fake = rig
    for _ in range(3):
        summarise_and_queue(store, svc)
    a, b, c = [r["id"] for r in aifeedback.list_runs(svc.db)]
    aifeedback.set_feedback(svc.db, b, "good")
    aifeedback.set_feedback(svc.db, c, "bad")
    old = (datetime.now() - timedelta(days=200)).isoformat(timespec="seconds")
    svc.db.execute("UPDATE ai_runs SET created=?", (old,))
    assert aifeedback.prune(svc.db, 90) == 2                                      # the new one and the bad one go
    assert [r["id"] for r in aifeedback.list_runs(svc.db)] == [b]


# ----------------------------------------------------------------------------- your feedback
def test_feedback_good_fix_bad_and_the_cases_in_between(rig):
    store, svc, fake = rig
    summarise_and_queue(store, svc)
    rid = aifeedback.list_runs(svc.db)[0]["id"]
    own = "\n".join(aifeedback.list_runs(svc.db)[0]["bullets"])
    assert aifeedback.set_feedback(svc.db, rid, "good", comment="nice")["status"] == "good"
    assert aifeedback.set_feedback(svc.db, rid, "fix", corrected=own)["status"] == "good"                 # "fixed" to the same text = it was fine
    fixed = aifeedback.set_feedback(svc.db, rid, "fix", corrected="- Dave says the $12,480 invoice is late.\nTo do: Pay invoice 4471 by Friday\nNo action needed.", comment="shorter")
    assert fixed["status"] == "fix" and fixed["corrected"] == ["Dave says the $12,480 invoice is late.", "ACTION: Pay invoice 4471 by Friday"]   # tidy, no "no action"
    assert fixed["comment"] == "shorter"
    assert aifeedback.set_feedback(svc.db, rid, "fix", corrected="  \n")["status"] == "bad"                # nothing written = no example
    assert aifeedback.set_feedback(svc.db, rid, "reset")["status"] == "new"
    assert aifeedback.set_feedback(svc.db, 999, "good") is None
    assert aifeedback.counts(svc.db)["total"] == 1


def test_rules_are_deduped_and_capped():
    rules = []
    for i in range(10):
        rules = aifeedback.add_rule(rules, f"Rule number {i}")
    assert len(rules) == 8 and rules[-1] == "Rule number 9"
    assert aifeedback.add_rule(rules, "rule NUMBER 9") == rules and aifeedback.add_rule(rules, "  ") == rules


# ----------------------------------------------------------------------------- feeding it back
def test_corrected_summaries_become_worked_examples_that_win_over_the_built_in_ones(rig):
    store, svc, fake = rig
    summarise_and_queue(store, svc)
    rid = aifeedback.list_runs(svc.db)[0]["id"]
    aifeedback.set_feedback(svc.db, rid, "fix", corrected="- Invoice 4471 is overdue.\nACTION: Pay invoice 4471 by Friday")
    assert aifeedback.write_examples(svc.db, llm.USER_EXAMPLES_FILE) == 1
    fake.seen.clear()
    cfg = store.data
    cfg["llm"]["examples"] = 1
    build_cards(fwd(""), cfg)                                                     # a similar email again
    msgs = fake.seen[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[2]["content"] == "- Invoice 4471 is overdue.\n- ACTION: Pay invoice 4471 by Friday"          # YOUR version, not a built-in one
    assert msgs[1]["content"].startswith("EMAIL:\nFrom: Dave Smith")
    cfg["llm"]["use_my_examples"] = False
    fake.seen.clear()
    build_cards(fwd(""), cfg)
    assert "Pay invoice 4471 by Friday" not in json.dumps(fake.seen[0]["messages"][:-1])


def test_good_summaries_are_used_as_they_are_and_bad_ones_never(rig):
    store, svc, fake = rig
    summarise_and_queue(store, svc)
    summarise_and_queue(store, svc)
    first, second = [r["id"] for r in aifeedback.list_runs(svc.db)]
    aifeedback.set_feedback(svc.db, first, "bad", comment="rubbish")
    aifeedback.set_feedback(svc.db, second, "good")
    rows = aifeedback.training_rows(svc.db)
    assert len(rows) == 1 and rows[0]["id"] == second
    assert rows[0]["bullets"][-1].startswith("ACTION: Reply by Friday")


def test_standing_rules_are_added_to_the_instructions(rig):
    store, svc, fake = rig
    d = store.data
    d["llm"]["rules"] = ["Always say who the email is from.", "Keep each bullet under 12 words"]
    store.replace(d)
    build_cards(fwd(""), store.data)
    system = fake.seen[0]["messages"][0]["content"]
    assert system.startswith(llm.SYSTEM) and "(1) Always say who the email is from." in system and "(2) Keep each bullet under 12 words." in system
    assert llm.system_prompt({}) == llm.SYSTEM


def test_training_data_download_is_chat_format_jsonl(rig):
    store, svc, fake = rig
    summarise_and_queue(store, svc)
    rid = aifeedback.list_runs(svc.db)[0]["id"]
    aifeedback.set_feedback(svc.db, rid, "fix", corrected="- Invoice 4471 is overdue.\nACTION: Pay it by Friday")
    text = aifeedback.export_jsonl(svc.db, llm.SYSTEM, llm.ask)
    rec = json.loads(text.strip())
    assert [m["role"] for m in rec["messages"]] == ["system", "user", "assistant"]
    assert rec["messages"][2]["content"] == "- Invoice 4471 is overdue.\n- ACTION: Pay it by Friday" and "BULLET POINTS:" in rec["messages"][1]["content"]
    assert aifeedback.export_jsonl(svc.db, "s", llm.ask).count("\n") == 1


# ----------------------------------------------------------------------------- the web page's API
def test_web_api_list_feedback_rule_export_and_delete(rig):
    from app.calsync import CalendarSync
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    from app.web import create_app
    store, svc, fake = rig
    summarise_and_queue(store, svc)
    c = create_app(store, svc, MailPoller(store, svc), CalendarSync(store, svc), ButtonController(store, svc)).test_client()
    h = {"X-Requested-With": "fetch"}
    c.post("/api/setup", json={"password": "hunter22"}, headers=h)
    r = c.get("/api/ai-runs").get_json()
    assert r["counts"]["new"] == 1 and r["runs"][0]["subject"] == "Invoice chase" and r["keep"] is True
    rid = r["runs"][0]["id"]
    assert "by Friday" in c.get(f"/api/ai-runs/{rid}").get_json()["email"] and c.get("/api/ai-runs/999").status_code == 404
    res = c.post(f"/api/ai-runs/{rid}/feedback", headers=h, json={"verdict": "fix", "corrected": "- Invoice 4471 is late.\nACTION: Pay by Friday",
                                                               "comment": "Always say who it is from", "rule": True}).get_json()
    assert res["run"]["status"] == "fix" and res["rules"] == ["Always say who it is from"] and store.data["llm"]["rules"] == ["Always say who it is from"]
    assert json.loads(llm.USER_EXAMPLES_FILE.read_text())["mine"][0]["bullets"] == ["Invoice 4471 is late.", "ACTION: Pay by Friday"]      # synced for the AI
    body = c.get("/api/ai-training.jsonl")
    assert body.mimetype == "application/x-ndjson" and json.loads(body.data.decode().strip())["messages"][2]["role"] == "assistant"
    assert c.get("/api/ai-runs?filter=reviewed").get_json()["runs"][0]["id"] == rid and c.get("/api/ai-runs?filter=new").get_json()["runs"] == []
    summarise_and_queue(store, svc)
    assert c.post("/api/ai-runs/clear", json={}, headers=h).get_json()["deleted"] == 1                # the unreviewed one goes, yours stays
    assert c.post(f"/api/ai-runs/{rid}/delete", headers=h).get_json() == {"ok": True}
    assert aifeedback.counts(svc.db)["total"] == 0 and json.loads(llm.USER_EXAMPLES_FILE.read_text())["mine"] == []


# ----------------------------------------------------------------------------- scoring the AI against your corrections
def test_evaluate_mine_hides_each_correction_from_the_model_while_testing_it(rig):
    store, svc, fake = rig
    summarise_and_queue(store, svc)
    rid = aifeedback.list_runs(svc.db)[0]["id"]
    aifeedback.set_feedback(svc.db, rid, "fix", corrected="- Dave Smith says invoice 4471 for $12,480 is 20 days overdue.\nACTION: Reply by Friday so Dave can close the quarter.")
    aifeedback.write_examples(svc.db, llm.USER_EXAMPLES_FILE)
    fake.seen.clear()
    res = evaluate.evaluate_mine(store.data, svc.db)
    assert len(res) == 1 and res[0]["accepted"] and res[0]["recall"] == 1.0 and res[0]["actions"]
    assert "Reply by Friday so Dave can close the quarter." not in json.dumps(fake.seen[0]["messages"][:-1])      # it never saw its own answer
    assert llm.USER_EXAMPLES_FILE.name == "ai_examples.json"                                                        # and the real file is restored
    assert "1 of your corrected emails" in evaluate.report_mine(res)
