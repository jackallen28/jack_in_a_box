from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from PIL import Image


@dataclass
class Section:
    """One labelled chunk inside the body box (text, a deadline, a checklist...)."""
    key: str                       # text | deadline | when | where | notes | summary | actions
    text: str = ""
    items: list[str] = field(default_factory=list)
    strong: bool = False           # drawn larger / bold (e.g. the deadline)
    label: str = ""                # overrides the template label (e.g. your [focus] phrase)
    bullets: bool = False          # items are bullet points; an item starting "[ ] " is an action (tick-box)


@dataclass
class Card:
    """Everything needed to draw one printout, independent of its look."""
    kind: str = "reminder"         # reminder | calendar | email
    heading: str = ""              # bold line at the top (email subject)
    subline: str = ""              # small grey-ish line under the heading
    sections: list[Section] = field(default_factory=list)
    images: list[Image.Image] = field(default_factory=list)
    priority: bool = False
    icons: list[str] = field(default_factory=list)   # symbol ids printed above the heading
    icon_cfg: dict = field(default_factory=dict, repr=False)   # the "icons" config (for custom symbols)
    template: str = ""             # template name; blank = the one for ``kind``
    vars: dict = field(default_factory=dict)   # {day} {date} ... for title text
    source: str = ""               # email | calendar | api | test
    created: datetime | None = None
    notes: list[str] = field(default_factory=list)   # corrections made, for the log
    trace: dict | None = field(default=None, repr=False)   # forward summaries: what the AI saw/answered (kept for review, see aifeedback.py)

    def summary(self) -> str:
        for s in self.sections:
            if s.text:
                return s.text.replace("\n", " ")[:90]
            if s.items:
                return "; ".join(s.items)[:90]
        return self.heading[:90]
