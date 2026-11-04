"""The print API as iOS Shortcuts uses it: JSON or a form, a shared screenshot, a message, a from name, symbols."""
import base64
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_web import H, client, login  # noqa: E402,F401

TOK = {"Authorization": "Bearer tok123"}


@pytest.fixture()
def api(client):
    c, cfg, svc = client
    d = cfg.data
    d["integrations"].update(api_enabled=True, api_token="tok123")
    cfg.replace(d)
    cards = []
    orig = svc.submit
    svc.submit = lambda card, copies=1, hold=False, due=None: (cards.append(card), orig(card, copies, hold, due))[1]
    return c, cfg, svc, cards


def picture(size=(600, 900), color=(30, 30, 30), fmt="PNG"):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, fmt)
    return b.getvalue()


def form(**fields):
    return {k: v for k, v in fields.items()}


def post(c, data=None, json_body=None, headers=None, files=None):
    h = {**TOK, **(headers or {})}
    if json_body is not None:
        return c.post("/api/print", json=json_body, headers=h)
    return c.post("/api/print", data={**(data or {}), **(files or {})}, headers=h, content_type="multipart/form-data")


# ----------------------------------------------------------------------------- the basics still work
def test_a_json_reminder_still_works_and_the_answer_is_readable_by_a_shortcut(api):
    c, cfg, svc, cards = api
    r = post(c, json_body={"heading": "Hi", "text": "wash dishes and walk the dog tomorrow"})
    assert r.status_code == 200 and r.json["ok"] is True and r.json["queued"] == 2 and r.json["message"] == "Sent 2 printouts to the printer"
    assert [x["source"] for x in svc.db.all("SELECT source FROM jobs")] == ["api", "api"]


def test_the_token_can_also_come_in_an_x_token_header_and_a_wrong_or_missing_one_is_refused(api):
    c, cfg, svc, cards = api
    assert c.post("/api/print", json={"text": "x"}, headers={"X-Token": "tok123"}).status_code == 200
    assert c.post("/api/print", json={"text": "x"}).status_code == 401
    r = c.post("/api/print", json={"text": "x"}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and "unauthorised" in r.json["error"]
    d = cfg.data
    d["integrations"]["api_enabled"] = False
    cfg.replace(d)
    assert post(c, json_body={"text": "x"}).status_code == 401


# ----------------------------------------------------------------------------- the form Shortcuts sends, with a shared screenshot
def test_a_shared_screenshot_goes_under_the_reminder(api):
    c, cfg, svc, cards = api
    r = post(c, {"heading": "Dentist", "text": "Dentist moved to Tuesday 3pm", "kind": "reminder"}, files={"images": (io.BytesIO(picture()), "Screenshot.png")})
    assert r.status_code == 200 and r.json["ok"]
    assert len(cards[0].images) == 1 and cards[0].images[0].size == (600, 900) and cards[0].heading == "Dentist"
    job = svc.db.one("SELECT png FROM jobs")
    assert Image.open(svc.hist_dir / job["png"]).height > 900                                       # the picture is on the printout


@pytest.mark.parametrize("field", ["images", "image", "file"])
def test_the_picture_field_can_be_called_images_image_or_file(api, field):
    c, cfg, svc, cards = api
    assert post(c, {"text": "see photo"}, files={field: (io.BytesIO(picture((400, 400))), "p.png")}).status_code == 200
    assert len(cards[0].images) == 1


def test_a_picture_alone_is_enough(api):
    c, cfg, svc, cards = api
    assert post(c, {}, files={"images": (io.BytesIO(picture()), "s.png")}).status_code == 200 and len(cards[0].images) == 1


def test_jpeg_and_base64_pictures_work_too(api):
    c, cfg, svc, cards = api
    assert post(c, {"text": "jpeg"}, files={"images": (io.BytesIO(picture(fmt="JPEG")), "a.jpg")}).status_code == 200
    b64 = base64.b64encode(picture((300, 300))).decode()
    assert post(c, json_body={"text": "b64", "image_base64": b64}).status_code == 200
    assert post(c, json_body={"text": "data url", "images_base64": ["data:image/png;base64," + b64, b64]}).status_code == 200
    assert [len(x.images) for x in cards] == [1, 1, 2]


def test_four_pictures_at_most(api):
    c, cfg, svc, cards = api
    b64 = base64.b64encode(picture((200, 200))).decode()
    assert post(c, json_body={"text": "many", "images_base64": [b64] * 7}).status_code == 200 and len(cards[0].images) == 4


def test_something_that_is_not_a_picture_is_a_clear_error(api):
    c, cfg, svc, cards = api
    r = post(c, {"text": "x"}, files={"images": (io.BytesIO(b"not an image"), "x.png")})
    assert r.status_code == 400 and "isn't a picture" in r.json["error"] and svc.db.all("SELECT id FROM jobs") == []
    assert post(c, json_body={"text": "x", "image_base64": "@@@"}).status_code == 400


# ----------------------------------------------------------------------------- kinds, from name, symbols
def test_a_message_slip_with_a_from_name_and_the_time_it_was_sent(api):
    c, cfg, svc, cards = api
    r = post(c, {"kind": "message", "text": "Back by six", "from": "Jack"})
    assert r.status_code == 200 and r.json["message"] == "Sent 1 message to the printer"
    card = cards[0]
    assert card.kind == "message" and card.vars["name"] == "Jack" and card.stamp.startswith("Sent ")
    assert svc.db.one("SELECT kind FROM jobs")["kind"] == "message"


def test_a_message_without_a_name_says_it_is_from_the_iphone(api):
    c, cfg, svc, cards = api
    post(c, json_body={"kind": "message", "text": "hello"})
    assert cards[0].vars["name"] == "iPhone"


def test_plain_prints_exactly_as_written_in_one_printout(api):
    c, cfg, svc, cards = api
    r = post(c, json_body={"kind": "plain", "text": "wash dishes and walk the dog tomorrow", "heading": "As is"})
    assert r.json["queued"] == 1 and "wash dishes and walk the dog tomorrow" in cards[0].sections[0].text


def test_a_reminder_with_a_from_name_gets_a_from_line(api):
    c, cfg, svc, cards = api
    post(c, json_body={"text": "buy milk", "from": "  Jack   Allen "})
    assert cards[0].stamp == "From Jack Allen"
    post(c, json_body={"text": "buy bread"})
    assert cards[1].stamp == ""


def test_symbols_by_name_or_number_and_unknown_ones_are_reported_not_fatal(api):
    c, cfg, svc, cards = api
    r = post(c, json_body={"text": "pay school fees", "symbols": "School, 13, unicorn"})
    assert r.status_code == 200 and r.json["ignored_symbols"] == ["unicorn"]
    assert "school" in cards[0].icons and len(cards[0].icons) == 2
    assert "ignored_symbols" not in post(c, json_body={"text": "ok", "symbols": ""}).json
    assert post(c, {"text": "form symbols", "symbols": "money"}).status_code == 200 and cards[-1].icons == ["money"]


def test_urgent_copies_and_template_still_work_and_are_limited(api):
    c, cfg, svc, cards = api
    post(c, json_body={"text": "pay rent", "urgent": "true", "copies": 3})
    assert cards[0].priority is True and len(svc.db.all("SELECT id FROM jobs")) == 3
    post(c, json_body={"text": "x", "copies": 99})
    assert len(svc.db.all("SELECT id FROM jobs")) == 3 + 5                                         # at most 5 copies
    post(c, {"text": "form urgent", "urgent": "1"})
    assert cards[-1].priority is True
    post(c, json_body={"text": "tpl", "template": "message"})
    assert cards[-1].template == "message"


# ----------------------------------------------------------------------------- mistakes get clear errors
def test_empty_bad_kind_full_queue_and_oversized_requests(api):
    c, cfg, svc, cards = api
    assert post(c, json_body={"text": "   "}).status_code == 400
    r = post(c, json_body={"text": "x", "kind": "poster"})
    assert r.status_code == 400 and "kind must be" in r.json["error"]
    for _ in range(50):
        svc.db.execute("INSERT INTO jobs(created, kind, source, title, notes, status) VALUES ('2026-11-04T10:00:00','x','api','t','[]','pending')")
    r = post(c, json_body={"text": "one more"})
    assert r.status_code == 429 and "queue is full" in r.json["error"]
    svc.db.execute("DELETE FROM jobs")
    assert post(c, {"text": "big"}, files={"images": (io.BytesIO(b"0" * (21 * 1024 * 1024)), "huge.png")}).status_code == 413


def test_a_long_text_is_cut_not_refused(api):
    c, cfg, svc, cards = api
    assert post(c, {"kind": "plain", "text": "word " * 5000}).status_code == 200


# ----------------------------------------------------------------------------- the setup helpers on the web page (need the login)
def test_the_shortcuts_page_helpers_need_a_login_and_set_up_the_token(client):
    c, cfg, svc = client
    assert c.get("/api/shortcuts").status_code == 401 and c.post("/api/shortcuts-token", json={}, headers=H).status_code == 401
    login(c)
    info = c.get("/api/shortcuts").get_json()
    assert info["inbox"] == "" and info["allowed_senders"] == [] and info["poll_seconds"] == 30                  # for the email-based recipes
    d = cfg.data
    d["email"].update(username="printer@gmail.com", allowed_senders=["me@icloud.com"], poll_seconds=20)
    cfg.replace(d)
    again_info = c.get("/api/shortcuts").get_json()
    assert again_info["inbox"] == "printer@gmail.com" and again_info["allowed_senders"] == ["me@icloud.com"] and again_info["poll_seconds"] == 20
    assert info["enabled"] is False and info["token_set"] is False and info["url"].endswith("/api/print") and "School" in info["symbols"]
    r = c.post("/api/shortcuts-token", json={}, headers=H).get_json()
    assert len(r["token"]) >= 20 and r["url"].endswith("/api/print")
    assert cfg.data["integrations"]["api_enabled"] is True and cfg.data["integrations"]["api_token"] == r["token"]
    assert c.get("/api/shortcuts").get_json()["enabled"] is True
    assert c.post("/api/print", json={"text": "from my phone"}, headers={"Authorization": "Bearer " + r["token"]}).status_code == 200      # the token it gave works
    again = c.post("/api/shortcuts-token", json={}, headers=H).get_json()
    assert again["token"] == r["token"]                                                           # asking again shows the same token
    new = c.post("/api/shortcuts-token", json={"regenerate": True}, headers=H).get_json()
    assert new["token"] != r["token"]
    assert c.post("/api/print", json={"text": "old"}, headers={"Authorization": "Bearer " + r["token"]}).status_code == 401              # the old one stops working
