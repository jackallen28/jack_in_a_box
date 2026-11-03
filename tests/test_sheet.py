"""Google Form -> Sheet -> printouts.  The Pi side runs against a fake copy of the Apps Script web address; the
script itself runs in Node against stand-ins for Google's services."""
import base64
import hashlib
import io
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import sheet as sheet_mod  # noqa: E402
from app.config import ConfigStore  # noqa: E402
from app.service import Service  # noqa: E402
from app.sheet import SheetError, SheetPoller, detect_columns, valid_url  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "tok-123"
HEADERS = ["Timestamp", "Email Address", "Message or reminder?", "Symbol 1", "Symbol 2", "Symbol 3", "Heading", "Body", "Image"]


def png_b64(size=(300, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 90, 200)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Fake(BaseHTTPRequestHandler):
    """Behaves like the deployed Apps Script."""
    headers_row: list = []
    rows: list = []
    files: dict = {}
    calls: list = []
    garbage = False

    @staticmethod
    def row_id(r):
        return hashlib.md5("|".join(r).encode()).hexdigest()[:16]

    def do_GET(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        Fake.calls.append(q)
        if Fake.garbage:
            self._send(b"<html>Sign in to Google</html>", "text/html")
            return
        if q.get("token") != TOKEN:
            return self._json({"ok": False, "error": "wrong secret code (token)"})
        a = q.get("action")
        if a == "rows":
            rows = [{"id": self.row_id(r), "values": dict(zip(Fake.headers_row, r))} for r in Fake.rows]
            return self._json({"ok": True, "headers": Fake.headers_row, "rows": rows})
        if a == "file":
            f = Fake.files.get(q.get("id"))
            return self._json({"ok": True, "name": "x.png", "mime": "image/png", "data": f} if f else {"ok": False, "error": "file not found"})
        if a == "delete":
            ids = set(q.get("ids", "").split(","))
            before = len(Fake.rows)
            Fake.rows = [r for r in Fake.rows if self.row_id(r) not in ids]
            return self._json({"ok": True, "deleted": before - len(Fake.rows)})
        self._json({"ok": True, "message": "working"})

    def _json(self, o):
        self._send(json.dumps(o).encode(), "application/json")

    def _send(self, b, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def row(email="me@x.com", kind="Reminder", s1="", s2="", s3="", heading="", body="", image="", ts="2026-11-03 10:00:00"):
    return [ts, email, kind, s1, s2, s3, heading, body, image]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    Fake.headers_row, Fake.rows, Fake.files, Fake.calls, Fake.garbage = list(HEADERS), [], {}, [], False
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(sheet_mod, "valid_url", lambda u: True)
    cfg = ConfigStore(tmp_path / "c.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["general"]["timezone"] = "Australia/Sydney"
    d["email"]["allowed_senders"] = ["me@x.com"]
    d["friends"] = [{"name": "Sarah", "email": "sarah@x.com", "bypass_queue": False, "enabled": True}]
    d["sheet"].update(enabled=True, url=f"http://127.0.0.1:{srv.server_port}/exec", token=TOKEN)
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    poller = SheetPoller(cfg, svc)
    seen = []
    orig = svc.submit
    monkeypatch.setattr(svc, "submit", lambda card, copies=1, hold=False: (seen.append((card, hold)), orig(card, copies, hold))[1])
    yield cfg, svc, poller, seen
    srv.shutdown()


def jobs(svc):
    return svc.db.all("SELECT id, kind, title, status FROM jobs ORDER BY id")


def deletes():
    return [c for c in Fake.calls if c.get("action") == "delete"]


# ----------------------------------------------------------------------------- the main flow
def test_new_rows_print_once_and_are_deleted_from_the_sheet_only_after_printing(env):
    cfg, svc, poller, seen = env
    Fake.rows = [row(s1="School", s2="fun", heading="Parent forms", body="sign the form by friday, pack lunch tomorrow")]
    assert poller.poll_once() == 1
    assert [j["status"] for j in jobs(svc)] == ["pending", "pending"]               # split into two reminders, like an email
    cards = [c for c, _ in seen]
    assert all(c.heading == "Parent forms" and c.icons[:2] == ["school", "fun"] and c.source == "form" for c in cards)
    assert any(s.key == "deadline" for c in cards for s in c.sections)                  # dates found, same as email
    assert len(Fake.rows) == 1 and deletes() == []                                    # NOT deleted: nothing has printed yet
    assert poller.poll_once() == 0 and len(jobs(svc)) == 2                            # polling again doesn't reprint it
    svc.process_pending()
    assert [j["status"] for j in jobs(svc)] == ["printed", "printed"]
    assert poller.reap() == 1
    assert Fake.rows == [] and len(deletes()) == 1                                    # gone from the sheet, once printed
    assert svc.db.one("SELECT status FROM sheet_rows")["status"] == "deleted"
    assert poller.poll_once() == 0 and len(jobs(svc)) == 2


def test_message_type_prints_as_a_MESSAGE_slip(env):
    cfg, svc, poller, seen = env
    Fake.rows = [row(kind="Message", heading="Hello", body="thinking of you, dont forget teh wine", s1="heart")]
    poller.poll_once()
    card, hold = seen[0]
    assert card.kind == "message" and card.template == "message" and card.icons == ["heart"] and not hold      # you: no queue
    assert card.sections[0].text == "thinking of you, dont forget teh wine"                                   # as written, not corrected
    assert card.vars["name"] == "me"


def test_a_friends_row_waits_for_the_button_and_stays_in_the_sheet_until_then(env):
    cfg, svc, poller, seen = env
    Fake.rows = [row(email="sarah@x.com", kind="Reminder", heading="Hi", body="call me")]
    poller.poll_once()
    card, hold = seen[0]
    assert hold and card.kind == "message" and card.vars["name"] == "Sarah"            # friends only ever send MESSAGEs
    assert [j["status"] for j in jobs(svc)] == ["held"]
    svc.process_pending()
    poller.reap()
    assert len(Fake.rows) == 1                                                         # still waiting for the button
    svc.release_held()
    svc.process_pending()
    poller.reap()
    assert Fake.rows == []


@pytest.mark.parametrize("policy,expect", [("hold", "held"), ("print", "pending"), ("ignore", None)])
def test_someone_not_on_your_lists_follows_your_policy(env, policy, expect):
    cfg, svc, poller, seen = env
    d = cfg.data
    d["sheet"]["unknown_policy"] = policy
    cfg.replace(d)
    Fake.rows = [row(email="stranger@z.com", heading="Hi", body="buy pills")]
    poller.poll_once()
    if expect is None:
        assert jobs(svc) == [] and len(Fake.rows) == 1 and svc.db.one("SELECT status FROM sheet_rows")["status"] == "ignored"
        assert poller.poll_once() == 0                                                  # not re-evaluated every minute
    else:
        assert [j["status"] for j in jobs(svc)] == [expect]


def test_a_form_that_does_not_collect_emails_uses_the_policy(env):
    cfg, svc, poller, seen = env
    Fake.headers_row = [h for h in HEADERS if h != "Email Address"]
    Fake.rows = [[v for i, v in enumerate(row(heading="Hi", body="buy milk")) if i != 1]]
    poller.poll_once()
    assert [j["status"] for j in jobs(svc)] == ["held"]


# ----------------------------------------------------------------------------- images
def test_uploaded_images_are_fetched_from_drive_and_printed(env):
    cfg, svc, poller, seen = env
    Fake.files = {"IMGONEIMGONE1": png_b64(), "IMGTWOIMGTWO2": png_b64((200, 300))}
    Fake.rows = [row(heading="Look", body="my bike", image="https://drive.google.com/open?id=IMGONEIMGONE1, https://drive.google.com/open?id=IMGTWOIMGTWO2")]
    poller.poll_once()
    card = seen[0][0]
    assert len(card.images) == 2 and len(seen) == 1                                    # photos => ONE reminder, like email
    assert [c["id"] for c in Fake.calls if c.get("action") == "file"] == ["IMGONEIMGONE1", "IMGTWOIMGTWO2"]


def test_image_problems_do_not_stop_the_text_printing(env):
    cfg, svc, poller, seen = env
    Fake.files = {"NOTANIMAGE0001": base64.b64encode(b"this is not a picture").decode()}
    Fake.rows = [row(heading="Look", body="my bike", image="https://drive.google.com/open?id=NOTANIMAGE0001 https://drive.google.com/open?id=MISSINGFILE001")]
    poller.poll_once()
    card = seen[0][0]
    assert card.images == [] and card.heading == "Look"
    assert sum("image skipped" in n for n in card.notes) == 2


def test_at_most_max_images_are_downloaded(env):
    cfg, svc, poller, seen = env
    Fake.files = {f"FILEFILEFILE{i}": png_b64() for i in range(6)}
    Fake.rows = [row(heading="x", body="y", image=" ".join(f"https://drive.google.com/open?id=FILEFILEFILE{i}" for i in range(6)))]
    poller.poll_once()
    assert len(seen[0][0].images) == 3 and len([c for c in Fake.calls if c.get("action") == "file"]) == 3


# ----------------------------------------------------------------------------- when things go wrong
def test_printer_off_keeps_the_row_and_never_duplicates_it(env):
    cfg, svc, poller, seen = env
    d = cfg.data
    d["printer"].update(backend="file", device="/nonexistent/lp0")
    cfg.replace(d)
    Fake.rows = [row(heading="Hi", body="buy milk")]
    poller.poll_once()
    for _ in range(3):
        svc.process_pending()
        poller.poll_once()
    assert len(jobs(svc)) == 1 and len(Fake.rows) == 1 and deletes() == []            # waiting, not duplicated, not deleted
    d["printer"]["backend"] = "dummy"
    cfg.replace(d)
    svc.db.execute("UPDATE jobs SET last_try=NULL")
    svc.process_pending()
    poller.reap()
    assert Fake.rows == []


def test_discarded_messages_are_cleared_from_the_sheet_but_real_failures_are_kept(env):
    cfg, svc, poller, seen = env
    Fake.rows = [row(email="sarah@x.com", heading="Hi", body="one", ts="t1"), row(email="sarah@x.com", heading="Hi", body="two", ts="t2")]
    poller.poll_once()
    first, second = [j["id"] for j in jobs(svc)]
    svc.discard_held(first)                                                            # you threw the first one away
    svc.db.execute("UPDATE jobs SET status='failed', error='printer on fire' WHERE id=?", (second,))
    assert poller.reap() == 1
    assert len(Fake.rows) == 1 and Fake.rows[0][-2] == "two"
    assert svc.db.one("SELECT status, note FROM sheet_rows WHERE status='error'")["note"].startswith("printing failed")


def test_a_row_with_nothing_to_print_is_cleared_and_a_bad_row_does_not_block_the_next(env, monkeypatch):
    cfg, svc, poller, seen = env
    real = sheet_mod.build_cards

    def flaky(inc, c, *a, **k):
        if inc.subject == "BOOM":
            raise RuntimeError("boom")
        return real(inc, c, *a, **k)

    monkeypatch.setattr(sheet_mod, "build_cards", flaky)
    Fake.rows = [row(heading="BOOM", body="x", ts="a"), row(heading="", body="", ts="b"), row(heading="Fine", body="buy milk", ts="c")]
    assert poller.poll_once() == 2
    assert len(jobs(svc)) == 1 and jobs(svc)[0]["title"] == "Fine"
    statuses = {r["status"] for r in svc.db.all("SELECT status FROM sheet_rows")}
    assert statuses == {"error", "deleted", "queued"}                                  # blank row removed; BOOM kept for you to see
    assert [r[0] for r in Fake.rows] == ["a", "c"]


def test_clear_messages_for_the_common_setup_mistakes(env):
    cfg, svc, poller, seen = env
    d = cfg.data
    d["sheet"]["token"] = "wrong"
    cfg.replace(d)
    with pytest.raises(SheetError, match="secret code"):
        poller.poll_once()
    d["sheet"]["token"] = TOKEN
    cfg.replace(d)
    Fake.garbage = True
    with pytest.raises(SheetError, match="web page instead of data"):
        poller.poll_once()
    Fake.garbage = False
    d["sheet"]["url"] = "http://127.0.0.1:1/exec"                                      # nothing there
    cfg.replace(d)
    with pytest.raises(SheetError, match="reach Google"):
        poller.poll_once()
    d["sheet"]["token"] = ""
    cfg.replace(d)
    with pytest.raises(SheetError, match="No secret code"):
        poller.poll_once()


def test_only_real_apps_script_urls_are_accepted():
    good = ["https://script.google.com/macros/s/AKfycbxABC123_-/exec", "https://script.google.com/a/macros/mycompany.com/s/AKfycbx123/exec"]
    bad = ["", "http://script.google.com/macros/s/AKfycbx/exec", "https://evil.example.com/macros/s/AKfycbx/exec",
           "https://script.google.com.evil.com/macros/s/AKfycbx/exec", "https://script.google.com/macros/s/AKfycbx/dev",
           "https://127.0.0.1/macros/s/x/exec", "file:///etc/passwd", "https://script.google.com/other"]
    import importlib
    real = importlib.reload(sheet_mod).valid_url
    assert all(real(u) for u in good) and not any(real(u) for u in bad)


# ----------------------------------------------------------------------------- reading the form's column titles
@pytest.mark.parametrize("headers,expect", [
    (HEADERS, {"email": "Email Address", "type": "Message or reminder?", "heading": "Heading", "body": "Body", "image": "Image",
               "symbols": ["Symbol 1", "Symbol 2", "Symbol 3"]}),
    (["Timestamp", "Type", "Icon 1", "Icon 2", "Title", "Text", "Photo"],
     {"email": None, "type": "Type", "heading": "Title", "body": "Text", "image": "Photo", "symbols": ["Icon 1", "Icon 2"]}),
    (["Timestamp", "Email address", "Subject", "Your note", "Upload a picture"],
     {"email": "Email address", "heading": "Subject", "image": "Upload a picture", "symbols": [], "type": None}),
    (["Timestamp", "Heading", "Something odd"], {"heading": "Heading", "body": "Something odd"}),
])
def test_columns_are_recognised_from_the_question_titles(headers, expect):
    cols = detect_columns(headers)
    for k, v in expect.items():
        assert cols[k] == v, (k, cols)


# ----------------------------------------------------------------------------- the pasted script, run for real in Node
@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_apps_script_itself_behaves():
    r = subprocess.run(["node", str(Path(__file__).parent / "apps_script_harness.js"), str(ROOT / "app/apps_script.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout)
    assert o["noToken"]["ok"] is False and o["wrongToken"]["ok"] is False and "token" in o["wrongToken"]["error"]
    assert o["ping"]["ok"] and o["ping"]["rows"] == 5
    rows = o["rows"]["rows"]
    assert o["rows"]["headers"] == ["Timestamp", "Email Address", "Body", "Image"]
    assert [r["values"]["Body"] for r in rows] == ["first", "second", "third", "fourth"]           # the blank row is skipped
    assert rows[0]["values"]["Timestamp"] == "2026-11-03T01:00:00.000Z" and len({r["id"] for r in rows}) == 4
    assert o["file"] == {"ok": True, "name": "pic.png", "mime": "image/png", "data": base64.b64encode(bytes([137, 80, 78, 71])).decode()}
    assert o["badFileId"]["ok"] is False and o["missingFile"]["ok"] is False
    assert o["hugeFile"]["ok"] is False and "too big" in o["hugeFile"]["error"]
    assert o["deleteMiddleAndLast"] == {"ok": True, "deleted": 2}
    assert o["afterDelete"] == ["first", "third"]                                                 # the RIGHT rows went (bottom-up deletion)
    assert o["deleteNothing"] == {"ok": True, "deleted": 0}


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_served_script_has_your_secret_in_it_and_is_valid_javascript(tmp_path):
    from app.web import create_app  # noqa
    cfg = ConfigStore(tmp_path / "c.json")
    svc = Service(cfg, tmp_path)
    from app.calsync import CalendarSync
    from app.gpio import ButtonController
    from app.mailbox import MailPoller
    c = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc)).test_client()
    H = {"X-Requested-With": "fetch"}
    assert c.post("/api/sheet-script", json={}, headers=H).status_code == 401
    c.post("/api/setup", json={"password": "hunter22"}, headers=H)
    script = c.post("/api/sheet-script", json={}, headers=H).json["script"]
    token = cfg.data["sheet"]["token"]
    assert len(token) >= 24 and f"const TOKEN = '{token}';" in script and "__TOKEN__" not in script
    assert c.post("/api/sheet-script", json={}, headers=H).json["script"] == script            # stable until regenerated
    f = tmp_path / "s.js"
    f.write_text(script)
    assert subprocess.run(["node", "--check", str(f)], capture_output=True).returncode == 0
    new = c.post("/api/sheet-script", json={"regenerate": True}, headers=H).json["script"]
    assert new != script and cfg.data["sheet"]["token"] in new
    pub = c.get("/api/config").json["sheet"]
    assert pub["token"] == "" and pub["token_set"] is True                                       # the secret never goes back to the browser
    conf = c.get("/api/config").json
    conf["sheet"]["poll_seconds"] = 120
    assert c.put("/api/config", json=conf, headers=H).status_code == 200
    assert cfg.data["sheet"]["token"] and cfg.data["sheet"]["poll_seconds"] == 120               # saving other settings keeps the secret
