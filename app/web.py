"""The local config page and its JSON API."""
from __future__ import annotations

import base64
import json
import subprocess
import copy
import hashlib
import hmac
import io
import logging
import re
import secrets
import time
from datetime import datetime

from flask import Flask, Response, jsonify, request, send_file, send_from_directory, session
from PIL import Image, ImageOps
from werkzeug.security import check_password_hash, generate_password_hash

from . import aifeedback, icons as icon_lib, llm, pinkpanther, plugins, schema, sheet as sheet_mod, watchlist
from .config import APP_DIR, DATA_DIR, ROOT, KIND_DEFAULTS, ConfigStore, default_template, merge
from .mailbox import MailPoller
from .mailparse import Incoming, _load_image, parse
from .pipeline import build_cards, classify_sender, local_now, local_tz, message_card, plain_card
from .render import FONTS, UPLOAD_DIR, render_card
from .samples import sample_card, sample_image
from .service import LOGBUF, Service
from .textproc import _dedupe

log = logging.getLogger("web")
PUBLIC_API = {"/api/state", "/api/login", "/api/setup", "/api/print"}


def create_app(cfg: ConfigStore, svc: Service, poller: MailPoller, cal, button, fan=None, power=None, sheet=None, airprint=None) -> Flask:
    app = Flask(__name__, static_folder=str(APP_DIR / "static"), static_url_path="/static")
    app.secret_key = cfg.data["web"]["secret_key"]
    app.config.update(SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_HTTPONLY=True,
                      MAX_CONTENT_LENGTH=12 * 1024 * 1024, PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30)
    fails = {"n": 0, "t": 0.0}

    # ------------------------------------------------------------ auth
    @app.before_request
    def guard():
        p = request.path
        if not p.startswith(("/api/", "/history/", "/uploads/")) or p in PUBLIC_API:
            return None
        if not session.get("ok"):
            return jsonify(error="login required"), 401
        if request.method != "GET" and request.headers.get("X-Requested-With") != "fetch":
            return jsonify(error="bad request"), 400
        if request.method != "GET":
            svc.touch()                      # a person is using the page: not idle
        return None

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        return resp

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/state")
    def state():
        return jsonify(setup_needed=not cfg.data["web"]["password_hash"], logged_in=bool(session.get("ok")))

    @app.post("/api/setup")
    def setup():
        if cfg.data["web"]["password_hash"]:
            return jsonify(error="already set up"), 400
        pw = (request.json or {}).get("password", "")
        if len(pw) < 6:
            return jsonify(error="Choose a password of at least 6 characters"), 400
        d = cfg.data
        d["web"]["password_hash"] = generate_password_hash(pw)
        cfg.replace(d)
        session.permanent = True
        session["ok"] = True
        return jsonify(ok=True)

    @app.post("/api/login")
    def login():
        wait = min(5.0, fails["n"] * 1.0)
        if fails["n"] and time.time() - fails["t"] < wait:
            return jsonify(error="Too many attempts - wait a few seconds"), 429
        pw = (request.json or {}).get("password", "")
        h = cfg.data["web"]["password_hash"]
        if h and check_password_hash(h, pw):
            fails["n"] = 0
            session.permanent = True
            session["ok"] = True
            return jsonify(ok=True)
        fails["n"] += 1
        fails["t"] = time.time()
        return jsonify(error="Wrong password"), 401

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.post("/api/password")
    def change_password():
        b = request.json or {}
        if not check_password_hash(cfg.data["web"]["password_hash"], b.get("old", "")):
            return jsonify(error="Current password is wrong"), 400
        if len(b.get("new", "")) < 6:
            return jsonify(error="New password must be at least 6 characters"), 400
        d = cfg.data
        d["web"]["password_hash"] = generate_password_hash(b["new"])
        cfg.replace(d)
        return jsonify(ok=True)

    # ------------------------------------------------------------ symbols
    def clean_icons(raw) -> dict:
        raw = raw if isinstance(raw, dict) else {}
        tags = lambda v: [str(t).strip()[:30] for t in (v if isinstance(v, list) else []) if str(t).strip()][:20]
        custom, ids = [], set(icon_lib.BUILTIN)
        for c in (raw.get("custom") or [])[:200]:
            if not isinstance(c, dict) or not c.get("file") or "/" in str(c.get("file")) or ".." in str(c.get("file")):
                continue
            cid = re.sub(r"[^a-z0-9]", "", str(c.get("id") or "").lower())[:16] or secrets.token_hex(4)
            if cid in ids:
                cid = secrets.token_hex(4)
            ids.add(cid)
            custom.append({"id": cid, "name": str(c.get("name") or "Symbol").strip()[:30] or "Symbol", "tags": tags(c.get("tags")),
                           "file": str(c["file"]), "mode": "photo" if c.get("mode") == "photo" else "line"})
        aliases = {k: tags(v) for k, v in (raw.get("aliases") or {}).items() if k in icon_lib.BUILTIN and tags(v)}
        disabled = [k for k in (raw.get("disabled") or []) if k in icon_lib.BUILTIN]
        return {"custom": custom, "aliases": aliases, "disabled": disabled}

    @app.get("/api/icons")
    def list_icons():
        nums = {x["id"]: x["n"] for x in icon_lib.numbered(cfg.data)}
        return jsonify(icons=[{**i, "n": nums.get(i["id"])} for i in icon_lib.catalogue(cfg.data)])

    @app.get("/api/symbol-chart.png")
    def symbol_chart():
        """All symbols with their numbers and names, as one picture (add it to your Google Form)."""
        buf = io.BytesIO()
        icon_lib.render_chart(cfg.data).save(buf, "PNG", optimize=True)
        buf.seek(0)
        return send_file(buf, mimetype="image/png", as_attachment=request.args.get("download") == "1",
                         download_name="jack-in-a-box-symbols.png")

    @app.get("/api/icon/<iid>.png")
    def icon_png(iid):
        size = max(40, min(400, int(request.args.get("size", 120))))
        im = icon_lib.icon_image(iid, size, cfg.data)
        if im is None:
            return "", 404
        return png_response(im)

    @app.post("/api/icons/print-sheet")
    def print_icon_sheet():
        jid = svc.submit_image(icon_lib.render_sheet(cfg.data, int(cfg.data["printer"].get("width_dots", 576))), "Symbol cheat sheet")
        return jsonify(id=jid)

    # ------------------------------------------------------------ config
    def public_config() -> dict:
        c = copy.deepcopy(cfg.data)
        c["email"]["password_set"] = bool(c["email"].get("password"))
        c["email"]["password"] = ""
        c["sheet"]["token_set"] = bool(c["sheet"].get("token"))
        c["sheet"]["token"] = ""
        c["web"] = {"port": c["web"]["port"]}
        return c

    @app.get("/api/config")
    def get_config():
        return jsonify(public_config())

    @app.put("/api/config")
    def put_config():
        new = request.json
        if not isinstance(new, dict):
            return jsonify(error="bad config"), 400
        old = cfg.data
        new["web"] = old["web"]                      # never editable from the browser
        new["setup_complete"] = old.get("setup_complete", False)
        new.get("email", {}).pop("password_set", None)
        new.setdefault("sheet", {}).pop("token_set", None)
        if not new["sheet"].get("token"):
            new["sheet"]["token"] = old["sheet"].get("token", "")
        if not new.get("email", {}).get("password"):
            new.setdefault("email", {})["password"] = old["email"].get("password", "")
        if not new["integrations"].get("api_token") and new["integrations"].get("api_enabled"):
            new["integrations"]["api_token"] = secrets.token_urlsafe(24)
        seen_emails, friends = set(), []
        for f in new.get("friends", []):
            em = str(f.get("email", "")).strip().lower()
            if em and "@" in em and em not in seen_emails:
                seen_emails.add(em)
                friends.append({"name": str(f.get("name", "")).strip()[:60] or em.split("@")[0],
                                "email": em, "bypass_queue": bool(f.get("bypass_queue")),
                                "enabled": bool(f.get("enabled", True))})
        new["friends"] = friends
        new["icons"] = clean_icons(new.get("icons"))
        cfg.replace(new)
        button.reconfigure()
        if airprint is not None:
            airprint.wake.set()                              # switch AirPrint on/off or rename it straight away
        if fan is not None:
            fan.reconfigure()
        poller.kick.set()
        cal.kick.set()
        if sheet is not None:
            sheet.kick.set()
        return jsonify(public_config())

    @app.get("/api/schema")
    def get_schema():
        return jsonify(sections=schema.SECTIONS, template_groups=schema.TEMPLATE_GROUPS,
                       kinds={k: v["label"] for k, v in KIND_DEFAULTS.items()},
                       plugins=plugins.names())

    @app.post("/api/generate-token")
    def gen_token():
        d = cfg.data
        d["integrations"]["api_token"] = secrets.token_urlsafe(24)
        cfg.replace(d)
        return jsonify(token=d["integrations"]["api_token"])

    # ------------------------------------------------------------ status / history
    @app.get("/api/status")
    def status():
        from .power import read_cpu_temp
        return jsonify(printer=svc.status(), mail=poller.status, calendar=cal.status, button=button.status,
                       cpu_temp=read_cpu_temp(), fan=(fan.status if fan is not None else None),
                       sheet=(sheet.status if sheet is not None else None),
                       airprint=(airprint.status if airprint is not None else None),
                       now=datetime.now().strftime("%a %d %b %H:%M"),
                       email_configured=bool(cfg.data["email"]["username"] and cfg.data["email"]["enabled"]))

    @app.get("/api/power-status")
    def power_status():
        from .power import read_cpu_temp, read_throttled
        fs = dict(fan.status) if fan is not None else {"state": "disabled", "message": "Fan control is not running."}
        ps = dict(power.status) if power is not None else {"enabled": False, "message": "Idle power-off is not running."}
        return jsonify(cpu_temp=read_cpu_temp(), fan=fs, power=ps, throttle=read_throttled(),
                       idle_minutes=int((__import__("time").time() - svc.last_activity) / 60))

    @app.get("/api/log")
    def get_log():
        return jsonify(list(LOGBUF)[-150:][::-1])

    @app.get("/api/history")
    def history():
        rows = svc.db.all("SELECT id, created, kind, source, title, status, attempts, error, notes, due FROM jobs "
                          "ORDER BY id DESC LIMIT ?", (min(200, int(request.args.get("limit", 40))),))
        tz = local_tz(cfg.data)
        for r in rows:                                   # scheduled printouts: when they are expected to print, in your timezone
            r["due_text"] = datetime.fromisoformat(r["due"]).astimezone(tz).strftime("%a %d %b %H:%M") if r["due"] and r["status"] == "scheduled" else ""
        return jsonify(rows)

    @app.get("/history/<int:jid>.png")
    def history_png(jid):
        row = svc.db.one("SELECT png FROM jobs WHERE id=?", (jid,))
        if not row or not row["png"]:
            return "", 404
        return send_file(svc.hist_dir / row["png"], mimetype="image/png")

    @app.post("/api/history/<int:jid>/reprint")
    def reprint(jid):
        new = svc.reprint(jid)
        return (jsonify(id=new), 200) if new else (jsonify(error="not found"), 404)

    @app.post("/api/history/<int:jid>/cancel")
    def cancel(jid):
        svc.db.execute("UPDATE jobs SET status='failed', error='Cancelled' WHERE id=? AND status IN ('pending','held','scheduled')", (jid,))
        return jsonify(ok=True)

    @app.post("/api/history/<int:jid>/release")
    def release_one(jid):
        return jsonify(ok=svc.release_one(jid))

    @app.post("/api/release")
    def release_all():
        """Same as pressing the physical button."""
        return jsonify(released=button.press())

    @app.get("/api/blocked-senders")
    def blocked_senders():
        """Who emailed the printer but isn't on the admin list or friends list (so nothing printed)."""
        rows = svc.db.all("SELECT info AS email, MAX(ts) AS last, COUNT(*) AS n FROM seen "
                          "WHERE status='blocked' AND info != '' GROUP BY info ORDER BY last DESC LIMIT 12")
        return jsonify([r for r in rows if classify_sender(r["email"], cfg.data)[0] is None][:8])

    @app.post("/api/allow-sender")
    def allow_sender():
        """One-click: add an address to YOUR addresses (admin) or to the friends list."""
        b = request.json or {}
        em = str(b.get("email", "")).strip().lower()
        if len(em) > 200 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", em):
            return jsonify(error="That doesn't look like an email address"), 400
        d = cfg.data
        if b.get("as") == "friend":
            if not any(f.get("email") == em for f in d["friends"]):
                d["friends"].append({"name": str(b.get("name") or em.split("@")[0]).strip()[:60], "email": em,
                                     "bypass_queue": False, "enabled": True})
        elif em not in [str(x).strip().lower() for x in d["email"]["allowed_senders"]]:
            d["email"]["allowed_senders"].append(em)
        cfg.replace(d)
        return jsonify(ok=True)

    @app.post("/api/discard-held")
    def discard_held():
        return jsonify(discarded=svc.discard_held())

    @app.post("/api/button-test")
    def button_test():
        ok = button.test_led()
        return jsonify(ok=ok, message="LED is flashing for 3 seconds." if ok else
                       (button.status.get("message") or "Button/LED isn't active - enable it and save first."))

    @app.post("/api/print-now")
    def print_now():
        """Custom message typed on the Print page (optionally with photos)."""
        f = request.form
        heading, text = f.get("heading", "").strip()[:200], f.get("text", "")[:5000]
        images = []
        for file in request.files.getlist("images")[:4]:
            im = _load_image(file.read(), 0)
            if im is None:
                return jsonify(error=f"'{file.filename}' isn't a picture I can read (use JPG, PNG, GIF or BMP)"), 400
            images.append(im)
        inc = Incoming(subject=heading, body=text, sender_addr="web", images=images)
        tpl = f.get("template") if f.get("template") in cfg.data["templates"] else ""
        now = local_now(cfg.data)
        if f.get("exact") == "1":
            card = plain_card(inc, cfg.data, now, template=tpl, kind=cfg.data["templates"][tpl]["kind"] if tpl else "reminder")
            cards, copies = ([card] if card else []), 1
        else:
            cards, copies = build_cards(inc, cfg.data, now, split=f.get("split", "1") == "1")
        if not cards:
            return jsonify(error="Nothing to print - add a heading, some text or a photo."), 400
        copies = max(copies, min(5, max(1, int(f.get("copies") or 1))))
        ids = []
        for c in cards:
            c.source = "web"
            if tpl:
                c.template = tpl
            if f.get("urgent") == "1":
                c.priority = True
            ids += svc.submit(c, copies)
        return jsonify(queued=len(ids), cards=len(cards))

    # ------------------------------------------------------------ preview / tests
    def template_from(body: dict) -> dict:
        t = body.get("tpl")
        if isinstance(t, dict):
            kind = t.get("kind", "reminder") if t.get("kind") in KIND_DEFAULTS else "reminder"
            return merge(default_template(kind), t)
        return cfg.template(body.get("template"))

    def png_response(img: Image.Image):
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")

    @app.post("/api/preview")
    def preview():
        b = request.json or {}
        tpl = template_from(b)
        card = sample_card(b.get("sample") or tpl.get("kind", "reminder"))
        return png_response(render_card(card, tpl, cfg.data["printer"], cfg.data["icons"]))

    @app.post("/api/test-print")
    def test_print():
        b = request.json or {}
        tpl_name = b.get("template") or "reminder"
        tpl = cfg.template(tpl_name)
        card = sample_card(b.get("sample") or tpl.get("kind", "reminder"))
        card.template = tpl_name if tpl_name in cfg.data["templates"] else ""
        ids = svc.submit(card)
        return jsonify(ids=ids)

    @app.post("/api/printer-check")
    def printer_check():
        from .printer import check
        ok, msg = check(cfg.data["printer"])
        return jsonify(ok=ok, message=msg)

    @app.get("/api/update-status")
    def update_status():
        def git(*a):
            try:
                return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, timeout=5).stdout.strip()
            except Exception:
                return ""
        st = None
        try:
            st = json.loads((DATA_DIR / "update-status.json").read_text())
        except Exception:
            pass
        log_line = git("log", "-1", "--format=%h|%cs|%s")
        h, d, subj = (log_line.split("|", 2) + ["", ""])[:3] if log_line else ("", "", "")
        return jsonify(version={"commit": h, "date": d, "subject": subj, "branch": git("rev-parse", "--abbrev-ref", "HEAD")},
                       status=st, git=bool(log_line))

    @app.post("/api/update-now")
    def update_now():
        """Ask the systemd updater to check right now (works even if auto-update is off)."""
        try:
            (DATA_DIR / "update-now").write_text("1")
            r = subprocess.run(["sudo", "-n", "systemctl", "start", "--no-block", "jack-in-a-box-update.service"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                (DATA_DIR / "update-now").unlink(missing_ok=True)
                return jsonify(ok=False, message="Updates aren't set up on this Pi yet - re-run deploy/install.sh once."), 200
        except Exception as e:
            return jsonify(ok=False, message=str(e)), 200
        return jsonify(ok=True, message="Checking for updates…")

    @app.get("/api/ai-status")
    def ai_status():
        return jsonify({**llm.status(cfg.data["llm"]), "backend": cfg.data["summary"]["backend"]})

    @app.post("/api/ai-test")
    def ai_test():
        """Run a sample email through the model in the background (a Pi can take a few minutes)."""
        started = llm.start_test(cfg.data["llm"], int(cfg.data["summary"].get("sentences", 4)))
        return jsonify(started=started)

    @app.get("/api/ai-test")
    def ai_test_result():
        return jsonify(llm.test_state())

    # ------------------------------------------------------------------ AI review: history, your corrections, training data
    def _sync_examples():
        aifeedback.write_examples(svc.db, svc.data_dir / "ai_examples.json", cfg.data)

    @app.get("/api/ai-runs")
    def ai_runs():
        return jsonify(counts=aifeedback.counts(svc.db), rules=cfg.data["llm"].get("rules", []),
                       keep=bool(cfg.data["llm"].get("keep_history", True)),
                       runs=aifeedback.list_runs(svc.db, request.args.get("filter", "all"), int(request.args.get("limit", 30))))

    @app.get("/api/ai-runs/<int:rid>")
    def ai_run(rid):
        r = aifeedback.get_run(svc.db, rid)
        return (jsonify(r), 200) if r else (jsonify(error="not found"), 404)

    @app.post("/api/ai-runs/<int:rid>/feedback")
    def ai_feedback(rid):
        b = request.json or {}
        syms = None
        if b.get("symbols") is not None:                           # names typed on the review page; blank = none
            from . import autosymbols
            syms = autosymbols.resolve([x.strip() for x in re.split(r"[,;]", str(b["symbols"])) if x.strip()], cfg.data)
        run = aifeedback.set_feedback(svc.db, rid, str(b.get("verdict", "")), str(b.get("corrected", "")), str(b.get("comment", "")), syms)
        if run is None:
            return jsonify(error="not found"), 404
        if b.get("rule") and run["comment"]:                       # "make this a standing rule"
            d = cfg.data
            d["llm"]["rules"] = aifeedback.add_rule(list(d["llm"].get("rules", [])), run["comment"])
            cfg.replace(d)
        _sync_examples()
        return jsonify(run=run, rules=cfg.data["llm"].get("rules", []))

    @app.post("/api/ai-runs/<int:rid>/delete")
    def ai_run_delete(rid):
        ok = aifeedback.delete(svc.db, rid)
        _sync_examples()
        return jsonify(ok=ok)

    @app.post("/api/ai-runs/clear")
    def ai_runs_clear():
        n = aifeedback.clear(svc.db, keep_reviewed=not (request.json or {}).get("everything"))
        _sync_examples()
        return jsonify(deleted=n)

    @app.get("/api/ai-training.jsonl")
    def ai_training():
        body = aifeedback.export_jsonl(svc.db, llm.system_prompt(cfg.data["llm"]), llm.ask, cfg.data)
        return Response(body, mimetype="application/x-ndjson", headers={"Content-Disposition": "attachment; filename=jack-in-a-box-ai-training.jsonl"})

    @app.get("/api/airprint")
    def airprint_status():
        if airprint is None:
            return jsonify(state="disabled", message="AirPrint isn't running in this session.")
        from . import airprint as ap
        s = dict(airprint.status)
        s.update(pdf=ap.pdf_available(), bonjour_library=ap.zeroconf_available(), formats=ap.supported_formats(),
                 recent=svc.db.all("SELECT id, created, title, status FROM jobs WHERE source='airprint' ORDER BY id DESC LIMIT 8"))
        return jsonify(s)

    @app.post("/api/airprint-test")
    def airprint_test():
        if airprint is not None:
            airprint.wake.set()                                  # pick up settings that were just saved
        return jsonify(airprint.self_test() if airprint is not None else {"ok": False, "message": "AirPrint isn't running."})

    @app.post("/api/sheet-script")
    def sheet_script():
        """The Apps Script to paste into the Google Sheet, with this app's secret code already inside."""
        d = cfg.data
        if (request.json or {}).get("regenerate") or not d["sheet"].get("token"):
            d["sheet"]["token"] = secrets.token_urlsafe(24)
            cfg.replace(d)
        script = (APP_DIR / "apps_script.js").read_text("utf-8").replace("__TOKEN__", cfg.data["sheet"]["token"])
        return jsonify(script=script)

    @app.post("/api/sheet-test")
    def sheet_test():
        try:
            data = sheet_mod.call(cfg.data, "rows", timeout=30)
        except sheet_mod.SheetError as e:
            return jsonify(ok=False, message=str(e))
        cols = sheet_mod.detect_columns(data.get("headers", []))
        n = len(data.get("rows", []))
        tzname = data.get("timezone", "")
        has_sched = bool(cols["sched_date"] or cols["sched_time"])
        schedule = [sheet_mod.describe(r.get("values", {}), cols, cfg.data, tzname) for r in data.get("rows", [])[:10]] if has_sched else []
        return jsonify(ok=True, message=f"Connected. {n} row{'s' if n != 1 else ''} waiting in the sheet.",
                       headers=data.get("headers", []), mapping=cols, schedule=schedule, sheet_timezone=tzname,
                       warning=sheet_mod.OUTDATED if has_sched and not tzname else "")

    @app.post("/api/sheet-check")
    def sheet_check():
        if sheet is not None:
            sheet._last_poll = 0
            sheet.kick.set()
        return jsonify(ok=True)

    @app.post("/api/test-email")
    def test_email():
        e = cfg.data["email"]
        if not (e["username"] and e["password"]):
            return jsonify(ok=False, message="Enter the email address and app password, save, then test."), 200
        try:
            imap = poller.connect()
            typ, _ = imap.select(e.get("folder") or "INBOX", readonly=True)
            typ, data = imap.uid("SEARCH", None, "UNSEEN")
            n = len(data[0].split()) if typ == "OK" and data and data[0] else 0
            imap.logout()
            return jsonify(ok=True, message=f"Connected. {n} unread message(s) waiting.")
        except Exception as ex:
            msg = str(ex)
            if "AUTHENTICATIONFAILED" in msg or "Invalid credentials" in msg:
                msg += " - for Gmail use an App Password, and make sure 2-Step Verification is on."
            return jsonify(ok=False, message=msg)

    @app.post("/api/check-mail")
    def check_mail():
        poller.kick.set()
        return jsonify(ok=True)

    @app.post("/api/test-calendar")
    def test_calendar():
        from .calsync import fetch_ics, parse_events
        from .pipeline import local_tz
        url = (request.json or {}).get("url", "")
        try:
            tz, now = local_tz(cfg.data), local_now(cfg.data)
            from datetime import timedelta
            evs = parse_events(fetch_ics(url), now, now + timedelta(days=30), tz)
            evs.sort(key=lambda e: e["start"])
            return jsonify(ok=True, message=f"Found {len(evs)} event(s) in the next 30 days.",
                           events=[{"title": e["summary"], "start": e["start"]} for e in evs[:8]])
        except Exception as ex:
            return jsonify(ok=False, message=str(ex))

    @app.post("/api/sync-calendar")
    def sync_calendar():
        cal.last_sync = 0
        cal.kick.set()
        return jsonify(ok=True)

    @app.get("/api/calendar/upcoming")
    def upcoming():
        return jsonify(cal.upcoming())

    @app.post("/api/playground")
    def playground():
        b = request.json or {}
        subject, body = (b.get("subject") or "")[:300], (b.get("body") or "")[:20000]
        if b.get("forward"):
            body = body.rstrip() + "\n\n---------- Forwarded message ---------\nFrom: Sam <sam@example.com>\n" \
                "Date: Mon, 3 Nov 2026\nSubject: " + (b.get("fwd_subject") or "Original email") + "\nTo: you\n\n" + (b.get("fwd_body") or "")
            subject = "Fwd: " + subject
        subject = " ".join(subject.split())
        inc = parse(f"Subject: {subject}\r\nFrom: playground@example.com\r\n\r\n{body}".encode(),
                    min_image_px=0)
        inc.sender_addr = "playground"
        if b.get("with_image") and inc.forwarded is None:
            inc.images = [sample_image()]
        cards, copies = build_cards(inc, cfg.data, use_ai=False)     # preview = instant; AI runs on real emails
        out = []
        for c in cards:
            tpl = cfg.template(c.template or c.kind)
            img = render_card(c, tpl, cfg.data["printer"], cfg.data["icons"])
            buf = io.BytesIO()
            img.save(buf, "PNG")
            out.append({"kind": c.kind, "heading": c.heading, "notes": c.notes,
                        "png": base64.b64encode(buf.getvalue()).decode()})
        return jsonify(cards=out, copies=copies)

    # ------------------------------------------------------------ uploads
    @app.post("/api/upload")
    def upload():
        f = request.files.get("file")
        if not f:
            return jsonify(error="no file"), 400
        try:
            im = Image.open(f.stream)
            im.load()
            im = ImageOps.exif_transpose(im)
            im.thumbnail((900, 900))
            im = im.convert("RGBA")
        except Exception:
            return jsonify(error="That doesn't look like a picture (use PNG, JPG, GIF or BMP)"), 400
        buf = io.BytesIO()
        im.save(buf, "PNG")
        name = hashlib.sha1(buf.getvalue()).hexdigest()[:16] + ".png"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (UPLOAD_DIR / name).write_bytes(buf.getvalue())
        return jsonify(file=name)

    @app.get("/uploads/<name>")
    def uploads(name):
        if "/" in name or ".." in name:
            return "", 404
        p = UPLOAD_DIR / name
        return send_file(p, mimetype="image/png") if p.exists() else ("", 404)

    # ------------------------------------------------------------ external print API (iOS Shortcuts, Zapier, Home Assistant ...)
    API_MAX_BYTES = 20 * 1024 * 1024

    def truthy(v) -> bool:
        return str(v).strip().lower() in ("1", "true", "yes", "on", "y")

    def api_authorised() -> bool:
        ic = cfg.data["integrations"]
        tok = (request.headers.get("Authorization", "").removeprefix("Bearer ").strip() or request.headers.get("X-Token", "").strip())
        return bool(ic.get("api_enabled") and ic.get("api_token") and hmac.compare_digest(tok, ic["api_token"]))

    @app.post("/api/print")
    def api_print():
        """Print from another app.  JSON or a form (what Shortcuts sends, with pictures):

        text       the reminder / message (the usual tidy-up applies: spelling, split into tasks, dates -> DUE)
        heading    optional bold heading            kind     reminder (default) | message | plain (exactly as written)
        from       optional "from" name             symbols  optional, names or numbers: "school, money"
        images     picture file(s) (form) or image_base64 / images_base64 (JSON); printed under the text
        urgent, copies, template   as on the Print page
        """
        if not api_authorised():
            return jsonify(error="unauthorised: wrong token, or the HTTP print API is switched off (Integrations / iPhone Shortcuts tab)"), 401
        if (request.content_length or 0) > API_MAX_BYTES:
            return jsonify(error="too big (20 MB at most)"), 413
        b = request.get_json(silent=True)
        if not isinstance(b, dict):
            b = request.form.to_dict()
        text = str(b.get("text", ""))[:5000]
        heading = str(b.get("heading", ""))[:200]
        kind = str(b.get("kind", "reminder")).strip().lower()
        if kind not in ("reminder", "message", "plain", "exact"):
            return jsonify(error="kind must be reminder, message or plain"), 400
        from_name = re.sub(r"\s+", " ", str(b.get("from") or b.get("name") or "")).strip()[:60]

        images: list = []
        blobs = [f.read() for f in (request.files.getlist("images") + request.files.getlist("image") + request.files.getlist("file"))[:4]]
        raw64 = b.get("images_base64") or ([b["image_base64"]] if b.get("image_base64") else [])
        for item in (raw64 if isinstance(raw64, list) else [raw64])[:4]:
            try:
                blobs.append(base64.b64decode(re.sub(r"^data:[^,]*,", "", str(item)), validate=False))
            except Exception:
                return jsonify(error="image_base64 isn't valid base64"), 400
        for blob in blobs[:4]:
            im = _load_image(blob, 0)
            if im is None:
                return jsonify(error="One of the images isn't a picture I can read (use JPG, PNG, GIF, BMP or HEIC)"), 400
            images.append(im)

        sym_ids, bad_syms = [], []
        for part in re.split(r"[,;]", str(b.get("symbols") or "")):
            if part.strip():
                iid = icon_lib.symbol_from_value(part.strip(), cfg.data)
                (sym_ids if iid else bad_syms).append(iid or part.strip())
        if svc.status()["queued"] >= 50:
            return jsonify(error="the printer queue is full - try again in a minute"), 429

        now = local_now(cfg.data)
        inc = Incoming(subject=heading, body=text, sender_addr="api", sender_name=from_name, images=images)
        tpl = b.get("template") if b.get("template") in cfg.data["templates"] else ""
        if kind == "message":
            card = message_card(inc, {"name": from_name or "iPhone", "email": ""}, cfg.data, now, when=now)
            cards, copies = ([card] if card else []), 1
        elif kind in ("plain", "exact"):
            card = plain_card(inc, cfg.data, now, template=tpl, kind=cfg.data["templates"][tpl]["kind"] if tpl else "reminder")
            cards, copies = ([card] if card else []), 1
        else:
            cards, copies = build_cards(inc, cfg.data, now)
        if not cards:
            return jsonify(error="nothing to print: send some text, a heading or a picture"), 400
        try:
            copies = max(copies, min(5, max(1, int(b.get("copies") or 1))))
        except (TypeError, ValueError):
            pass
        ids = []
        for c in cards:
            c.source = "api"
            c.icons = _dedupe(sym_ids + c.icons)[:6]
            if from_name and kind != "message":
                c.stamp = f"From {from_name}"
            if tpl and kind != "message":
                c.template = tpl
            if truthy(b.get("urgent")):
                c.priority = True
            ids += svc.submit(c, copies)
        out = {"ok": True, "queued": len(ids), "ids": ids, "cards": len(cards),
               "message": f"Sent {len(cards)} {'message' if kind == 'message' else 'printout'}{'s' if len(cards) != 1 else ''} to the printer"}
        if bad_syms:
            out["ignored_symbols"] = bad_syms
        return jsonify(out)

    # ------------------------------------------------------------------ Pink Panther episodes
    def _pp_state():
        data = pinkpanther._load(pinkpanther.CACHE_FILE)
        eps = pinkpanther.load_episodes()
        b = pinkpanther._bundled()
        return {"enabled": bool(cfg.data["pinkpanther"].get("enabled")), "lookup": bool(cfg.data["pinkpanther"].get("lookup")),
                "titles": len(eps), "seasons": len(b.get("seasons", [])), "tv_episodes": sum(len(x["episodes"]) for x in b.get("seasons", [])),
                "learned": len(data.get("details", {})), "last_problem": pinkpanther.LAST_PROBLEM}

    @app.get("/api/pinkpanther")
    def pp_status():
        return jsonify(_pp_state())

    @app.post("/api/pinkpanther-test")
    def pp_test():
        """Try a message exactly as it would be handled: shows what was found; ``print`` also queues the slips."""
        b = request.json or {}
        text = str(b.get("q", ""))[:2000]
        now = local_now(cfg.data)
        inc = Incoming(subject="Pink Panther", body=text, sender_addr="web")
        cards, _rest = pinkpanther.build(inc, cfg.data, now)
        out = [{"heading": c.heading, "text": "\n".join((f"{s.label}: " if s.label else "") + s.text for s in c.sections if s.text), "found": bool(c.vars.get("matched"))} for c in cards]
        if b.get("print") and cards:
            for c in cards:
                c.source = "web"
                svc.submit(c, 1)
        return jsonify(cards=out, printed=bool(b.get("print") and cards), **_pp_state())

    # ------------------------------------------------------------------ Watch list (movies / TV shows)
    @app.get("/api/watchlist")
    def wl_status():
        sym = watchlist.symbol_id(cfg.data)
        return jsonify(enabled=bool(cfg.data["watchlist"].get("enabled")), symbol=sym, ai=bool(cfg.data["watchlist"].get("ai")) and cfg.data["summary"].get("backend") == "ollama")

    @app.post("/api/watchlist-test")
    def wl_test():
        """Try a message exactly as it would be handled; ``print`` also queues the slip."""
        b = request.json or {}
        inc = Incoming(subject=str(b.get("subject", ""))[:200], body=str(b.get("q", ""))[:3000], sender_addr="web")
        cards, _rest = watchlist.build(inc, cfg.data, local_now(cfg.data))
        out = [{"heading": c.heading, "text": "\n".join(s.text for s in c.sections if s.text)} for c in cards]
        if b.get("print") and cards:
            for c in cards:
                c.source = "web"
                svc.submit(c, 1)
        return jsonify(cards=out, printed=bool(b.get("print") and cards))

    @app.get("/api/shortcuts")
    def shortcuts_info():
        """For the iPhone Shortcuts page: is the API on, is there a token, what address should the phone use."""
        ic = cfg.data["integrations"]
        e = cfg.data["email"]
        return jsonify(enabled=bool(ic.get("api_enabled")), token_set=bool(ic.get("api_token")), url=request.host_url.rstrip("/") + "/api/print",
                       symbols=[x["name"] for x in icon_lib.numbered(cfg.data) if x["enabled"]],
                       inbox=(e.get("username") or "").strip(), allowed_senders=[str(x) for x in e.get("allowed_senders", [])],
                       poll_seconds=int(e.get("poll_seconds", 30)))

    @app.post("/api/shortcuts-token")
    def shortcuts_token():
        """Switch the print API on and give the phone a token (keep the current one unless regenerate)."""
        d = cfg.data
        if (request.json or {}).get("regenerate") or not d["integrations"].get("api_token"):
            d["integrations"]["api_token"] = secrets.token_urlsafe(24)
        d["integrations"]["api_enabled"] = True
        cfg.replace(d)
        return jsonify(token=d["integrations"]["api_token"], url=request.host_url.rstrip("/") + "/api/print")

    plugins.register_routes(app)
    return app
