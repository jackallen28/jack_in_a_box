"""Example plugin - copy this file to e.g. plugins/my_rules.py and edit.

Files starting with ``example_`` are ignored unless you create an empty file next to them
called ``example_tagger.enabled`` (so the example can't surprise you).

Hooks (all optional):
    process(card, ctx)   change / split / drop a printout before it is drawn
    on_printed(info)     do something after a successful print (log to a sheet, ping Slack...)
    register(app)        add your own web routes to the Flask app
"""


def process(card, ctx):
    inc = ctx.get("incoming")
    # Tag everything that came from a work address
    if inc is not None and inc.sender_addr.endswith("@mycompany.com"):
        card.heading = ("[WORK] " + card.heading).strip()
    # Never print calendar events whose title starts with "Private"
    if ctx["source"] == "calendar" and card.heading.lower().startswith("private"):
        return None
    return card


def on_printed(info):
    # info = {"id", "kind", "title", "source", "printed_at"}
    pass
