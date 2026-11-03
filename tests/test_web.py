import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.calsync import CalendarSync  # noqa: E402
from app.gpio import ButtonController  # noqa: E402
from app.config import ConfigStore  # noqa: E402
from app.mailbox import MailPoller  # noqa: E402
from app.service import Service  # noqa: E402
from app.web import create_app  # noqa: E402

H = {"X-Requested-With": "fetch"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.render as render
    import app.web as web
    monkeypatch.setattr(render, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(web, "UPLOAD_DIR", tmp_path / "uploads")
    cfg = ConfigStore(tmp_path / "config.json")
    d = cfg.data
    d["printer"]["backend"] = "dummy"
    d["email"]["password"] = "super-secret-app-password"
    cfg.replace(d)
    svc = Service(cfg, tmp_path)
    app = create_app(cfg, svc, MailPoller(cfg, svc), CalendarSync(cfg, svc), ButtonController(cfg, svc))
    return app.test_client(), cfg, svc


def login(c):
    assert c.post("/api/setup", json={"password": "hunter22"}, headers=H).status_code == 200


def test_api_requires_login_and_setup_runs_once(client):
    c, *_ = client
    assert c.get("/api/config").status_code == 401
    assert c.get("/api/state").json == {"logged_in": False, "setup_needed": True}
    assert c.post("/api/setup", json={"password": "abc"}, headers=H).status_code == 400   # too short
    login(c)
    assert c.get("/api/config").status_code == 200
    assert c.post("/api/setup", json={"password": "other123"}, headers=H).status_code == 400


def test_wrong_password_rejected_and_header_required(client):
    c, *_ = client
    login(c)
    c.post("/api/logout", headers=H)
    assert c.get("/api/config").status_code == 401
    assert c.post("/api/login", json={"password": "nope"}, headers=H).status_code == 401
    c.post("/api/login", json={"password": "hunter22"}, headers=H)   # may be throttled right after a failure
    # state-changing request without the custom header is refused (cross-site forms can't set it)
    with c.session_transaction() as s:
        s["ok"] = True
    assert c.put("/api/config", json={}).status_code == 400


def test_secrets_never_sent_to_browser(client):
    c, cfg, _ = client
    login(c)
    body = c.get("/api/config").get_data(as_text=True)
    assert "super-secret-app-password" not in body
    assert "password_hash" not in body and "secret_key" not in body
    assert c.get("/api/config").json["email"]["password_set"] is True


def test_saving_blank_password_keeps_existing(client):
    c, cfg, _ = client
    login(c)
    conf = c.get("/api/config").json
    conf["email"]["host"] = "imap.example.com"
    assert c.put("/api/config", json=conf, headers=H).status_code == 200
    assert cfg.data["email"]["password"] == "super-secret-app-password"
    assert cfg.data["email"]["host"] == "imap.example.com"
    assert cfg.data["web"]["password_hash"]       # not wiped by the save


def test_print_api_token(client):
    c, cfg, svc = client
    d = cfg.data
    d["integrations"].update(api_enabled=True, api_token="tok123")
    cfg.replace(d)
    assert c.post("/api/print", json={"text": "walk dog"}).status_code == 401
    assert c.post("/api/print", json={"text": "walk dog"}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.post("/api/print", json={"heading": "Hi", "text": "wash dishes and walk the dog tomorrow"},
               headers={"Authorization": "Bearer tok123"})
    assert r.status_code == 200 and r.json["queued"] == 2
    d["integrations"]["api_enabled"] = False
    cfg.replace(d)
    assert c.post("/api/print", json={"text": "x"}, headers={"Authorization": "Bearer tok123"}).status_code == 401


def test_preview_and_playground(client):
    c, *_ = client
    login(c)
    r = c.post("/api/preview", json={"sample": "email"}, headers=H)
    assert r.status_code == 200 and r.data[:4] == b"\x89PNG"
    r = c.post("/api/playground", json={"subject": "S", "body": "wash dishes, walk the dog"}, headers=H)
    assert len(r.json["cards"]) == 2
    r = c.post("/api/playground", headers=H, json={
        "subject": "S", "body": "do this", "forward": True,
        "fwd_body": "Hello there. This is the original email text about invoices and payments due soon."})
    assert r.json["cards"][0]["kind"] == "email"


def test_upload_rejects_non_images(client):
    c, *_ = client
    login(c)
    r = c.post("/api/upload", data={"file": (io.BytesIO(b"not an image"), "x.png")}, headers=H,
               content_type="multipart/form-data")
    assert r.status_code == 400
