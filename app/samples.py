"""Sample cards for the live preview and test prints."""
from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw

from .models import Card, Section


def sample_image() -> Image.Image:
    w, h = 800, 520
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        v = int(235 - 150 * y / h)
        d.line([(0, y), (w, y)], fill=(v, v, min(255, v + 20)))
    d.polygon([(0, h), (260, 210), (470, h)], fill=(70, 70, 70))
    d.polygon([(280, h), (560, 140), (800, h)], fill=(40, 40, 40))
    d.ellipse([600, 50, 700, 150], fill=(250, 250, 250))
    return im


def sample_card(kind: str, now: datetime | None = None) -> Card:
    now = now or datetime.now()
    if kind == "calendar":
        return Card(kind="calendar", heading="Quarterly planning meeting", source="test", created=now,
                    vars={"day": "TOMORROW", "date": "Wed 4 Nov", "calendar": "Work"}, icons=["calendar"],
                    sections=[Section("when", text="Wed 4 Nov, 10:00 AM - 11:30 AM", strong=True),
                              Section("where", text="Level 4 boardroom, 120 Collins St"),
                              Section("notes", text="Bring the budget draft and last quarter's numbers.")])
    if kind == "email":
        return Card(kind="email", heading="Q4 supplier invoice dispute", subline="From Dave Smith", source="test",
                    created=now, vars={"date": "Mon 3 Nov"}, icons=["accountant"],
                    sections=[Section("summary", text="Dave says invoice 4471 for $12,480 is 20 days overdue. "
                                      "Invoice 4402 was paid twice, leaving a $3,200 credit on your account. "
                                      "He needs a reply by Friday to close the quarter."),
                              Section("actions", items=["Reply to Dave by Friday", "Check invoice 4471 against PO"])])
    secs = [Section("text", text="Wash the dishes"), Section("deadline", text="Today, 5:00 PM", strong=True)]
    card = Card(kind="reminder", heading="Home stuff", sections=secs, source="test", created=now,
                icons=["home", "exclamation"], vars={"name": "Sarah"})
    if kind == "reminder_image":
        card.sections = [Section("text", text="Ask the plumber to fix the leak under the sink before Friday."),
                         Section("deadline", text="Friday 6 Nov", strong=True)]
        card.images = [sample_image()]
        card.priority = True
        card.icons = ["work"]
    return card
