"""Describes every setting so the web page can build its forms automatically.

Field types: text, password, number, bool, select, lines, multicheck, time,
image, template (pick a template name).  ``path`` is dotted into the config;
template fields use the prefix ``t.`` which the page maps to the template being
edited.
"""
from __future__ import annotations

from .render import FONTS


def F(path, label, type="text", help="", **kw):
    return {"path": path, "label": label, "type": type, "help": help, **kw}


def opts(*pairs):
    return [list(p) if isinstance(p, tuple) else [p, p] for p in pairs]


FONT_OPTS = [[k, v[0]] for k, v in FONTS.items()]
ALIGN = opts(("left", "Left"), ("center", "Centre"), ("right", "Right"))
DITHER = opts(("floyd", "Photo (dithered greys)"), ("threshold", "Line art (pure black/white)"))

TEMPLATE_GROUPS = [
    {"title": "Size & spacing", "fields": [
        F("t.label", "Template name", help="Shown in menus."),
        F("t.width_mm", "Printout width (mm)", "number", "The TM-T82II can print at most 72 mm of its 80 mm paper.", min=30, max=72, step=1),
        F("t.min_height_mm", "Minimum height (mm)", "number", "80 gives the 80x80 mm square; it grows taller with content.", min=20, max=400, step=1),
        F("t.max_height_mm", "Maximum height (mm)", "number", min=80, max=2000, step=10),
        F("t.margin_top_mm", "White space above the printout (mm)", "number",
          "Blank paper before the border starts, e.g. 10. The printer's own paper-feed before the cut is set separately on the Printer tab.", min=0, max=60, step=1),
        F("t.margin_bottom_mm", "White space below the printout (mm)", "number", "Blank paper after the border ends.", min=0, max=60, step=1),
        F("t.padding_mm", "Padding inside border (mm)", "number", "Space between the border and the text.", min=0, max=12, step=0.5),
        F("t.gap_mm", "Space between sections (mm)", "number", min=0, max=12, step=0.5),
    ]},
    {"title": "Border", "fields": [
        F("t.border.enabled", "Draw a border", "bool"),
        F("t.border.width_pt", "Border thickness (pt)", "number", "1 pt = about 3 printer dots.", min=0.5, max=6, step=0.5),
        F("t.border.radius_mm", "Rounded corners (mm)", "number", min=0, max=10, step=1),
    ]},
    {"title": "Section order", "fields": [
        F("t.order", "Top to bottom", "order", "Use the arrows; turn sections on/off in their own groups below.",
          options=opts(("title", "Title"), ("icons", "Symbols"), ("graphic", "Graphic / logo"), ("heading", "Heading (subject)"),
                       ("body", "Text box"), ("images", "Email pictures"), ("divider", "Dotted divider"), ("footer", "Footer"))),
    ]},
    {"title": "Title (e.g. REMINDER)", "fields": [
        F("t.title.enabled", "Show title", "bool"),
        F("t.title.text", "Title text", help="Placeholders: {day} {date} {calendar}"),
        F("t.title.subtitle", "Small line under title", help="Placeholders: {day} {date}"),
        F("t.title.font", "Font", "select", options=FONT_OPTS),
        F("t.title.size_pt", "Size (pt)", "number", "Shrinks automatically if too wide.", min=10, max=80, step=1),
        F("t.title.style", "Style", "select", options=opts(("outline", "Outlined"), ("solid", "Solid"), ("shadow", "Shadow"), ("inverse", "White on black bar"))),
        F("t.title.align", "Alignment", "select", options=ALIGN),
    ]},
    {"title": "Symbols row ([school], [!] ...)", "fields": [
        F("t.icons.enabled", "Print symbols", "bool", "Symbols chosen with [tags] in the email print as a row above the heading."),
        F("t.icons.size_mm", "Symbol size (mm)", "number", "Shrinks automatically when there are several.", min=8, max=60, step=1),
        F("t.icons.max", "Most symbols per printout", "number", min=1, max=8),
        F("t.icons.gap_mm", "Space between (mm)", "number", min=0, max=15, step=0.5),
        F("t.icons.align", "Alignment", "select", options=ALIGN),
    ]},
    {"title": "Graphic / logo", "fields": [
        F("t.graphic.enabled", "Show graphic", "bool"),
        F("t.graphic.file", "Picture", "image", "Upload a logo or clip-art. Black and white works best."),
        F("t.graphic.max_height_mm", "Max height (mm)", "number", min=5, max=100, step=1),
        F("t.graphic.align", "Alignment", "select", options=ALIGN),
        F("t.graphic.dither", "Conversion", "select", options=DITHER),
    ]},
    {"title": "Heading (the email subject)", "fields": [
        F("t.heading.enabled", "Show heading", "bool", "Skipped automatically when the email has no subject."),
        F("t.heading.font", "Font", "select", options=FONT_OPTS),
        F("t.heading.bold", "Bold", "bool"),
        F("t.heading.size_pt", "Size (pt)", "number", min=7, max=40, step=0.5),
        F("t.heading.align", "Alignment", "select", options=ALIGN),
        F("t.heading.show_subline", "Show small line (e.g. original sender)", "bool"),
    ]},
    {"title": "Text box", "fields": [
        F("t.body.font", "Font", "select", options=FONT_OPTS),
        F("t.body.size_pt", "Size (pt)", "number", min=6, max=30, step=0.5),
        F("t.body.align", "Alignment", "select", options=ALIGN),
        F("t.body.box", "Outline", "select", options=opts(("single", "One box"), ("separate", "A box per section"), ("none", "No box"))),
        F("t.body.box_width_pt", "Outline thickness (pt)", "number", min=0.5, max=6, step=0.5),
        F("t.body.radius_mm", "Rounded corners (mm)", "number", min=0, max=10, step=1),
        F("t.body.show_labels", "Show section labels (DUE, WHEN...)", "bool"),
        F("t.body.hide_sections", "Hide these sections", "multicheck",
          options=opts(("text", "Task text"), ("deadline", "Deadline"), ("when", "When"), ("where", "Where"),
                       ("notes", "Notes"), ("summary", "Summary"), ("actions", "Actions"),
                       ("focus", "Your [focus] answers"), ("full", "Full email"))),
        F("t.body.checkbox_actions", "Tick-boxes on lists (off = dashes)", "bool"),
        F("t.body.priority_marker", "Show ! URGENT ! tag when flagged", "bool"),
        F("t.body.labels.deadline", "Label: deadline"),
        F("t.body.labels.when", "Label: when"),
        F("t.body.labels.where", "Label: where"),
        F("t.body.labels.notes", "Label: notes"),
        F("t.body.labels.summary", "Label: summary"),
        F("t.body.labels.actions", "Label: actions"),
        F("t.body.labels.full", "Label: full email"),
    ]},
    {"title": "Email pictures", "fields": [
        F("t.images.enabled", "Print attached pictures", "bool"),
        F("t.images.max_height_mm", "Max height each (mm)", "number", min=10, max=400, step=5),
        F("t.images.dither", "Conversion", "select", options=DITHER),
        F("t.images.contrast", "Contrast boost", "number", "1 = none. Photos usually look better around 1.2.", min=0.5, max=2.5, step=0.05),
        F("t.images.boxed", "Frame pictures", "bool"),
    ]},
    {"title": "Footer", "fields": [
        F("t.footer.enabled", "Show footer", "bool"),
        F("t.footer.text", "Text", help="e.g. your business name or 'Printed by the office printer'."),
        F("t.footer.show_timestamp", "Print date and time", "bool"),
        F("t.footer.size_pt", "Size (pt)", "number", min=5, max=20, step=0.5),
        F("t.footer.align", "Alignment", "select", options=ALIGN),
    ]},
]

SECTIONS = {
    "email": [
        F("email.enabled", "Check this mailbox for reminders", "bool"),
        F("email.host", "IMAP server", help="Gmail: imap.gmail.com"),
        F("email.port", "Port", "number", min=1, max=65535),
        F("email.username", "Email address / username"),
        F("email.password", "Password", "password",
          "For Gmail this must be an App Password (Google Account > Security > 2-Step Verification > App passwords), not your normal password."),
        F("email.folder", "Folder to watch"),
        F("email.poll_seconds", "Check every (seconds)", "number", min=10, max=3600),
        F("email.after", "After printing an email", "select", options=opts(
            ("delete", "Delete it (Gmail: moved to Trash, which empties itself after 30 days)"),
            ("mark_read", "Mark as read and keep it"), ("move", "Move to a folder"))),
        F("email.trash_folder", "Trash folder (for 'delete')", help="Gmail: [Gmail]/Trash. Leave BLANK for other providers to delete permanently. Only emails that were actually printed or queued are deleted."),
        F("email.move_folder", "Folder to move to", help="Created if it doesn't exist."),
        F("email.allowed_senders", "Your addresses (admin)", "lines",
          "One per line: a full address (me@gmail.com) or a whole domain (@mycompany.com). Mail from these prints "
          "immediately as reminders and skips the friends' queue. Friends are added on the Messages tab. "
          "Mail from anyone else is ignored."),
        F("email.require_auth_pass", "Reject mail that fails SPF/DKIM checks", "bool", "Stops forged 'From' addresses."),
        F("email.max_cards_per_email", "Max printouts from one email", "number", min=1, max=50),
        F("email.max_images", "Max pictures printed per email", "number", min=0, max=10),
        F("email.min_image_px", "Ignore pictures smaller than (px)", "number", "Skips signature logos and tracking pixels.", min=0, max=2000),
        F("email.subject_only_as_task", "If the body is empty, treat the subject as the reminder", "bool"),
    ],
    "printer": [
        F("printer.backend", "Connection", "select", options=opts(
            ("auto", "Automatic (USB if plugged in, else network IP)"), ("file", "USB device file (/dev/usb/lp0)"), ("usb", "Direct USB (pyusb)"),
            ("network", "Network (port 9100)"), ("dummy", "Test mode - save images, print nothing"))),
        F("printer.device", "Device file", help="Usually /dev/usb/lp0"),
        F("printer.usb_vendor", "USB vendor ID", help="Epson = 0x04b8"),
        F("printer.usb_product", "USB product ID", help="Leave blank to use the first Epson found."),
        F("printer.host", "Network printer IP", help="Direct Ethernet cable to the Pi: usually 192.168.192.168 (check the printer's self-test printout)."),
        F("printer.port", "Network port", "number"),
        F("printer.width_dots", "Paper width (dots)", "number", "576 for the TM-T82II on 80 mm paper.", min=192, max=832, step=8),
        F("printer.threshold", "Print darkness", "number", "Higher = bolder text (100-220).", min=80, max=230, step=5),
        F("printer.cut", "After each printout", "select", options=opts(
            ("partial", "Partial cut"), ("full", "Full cut"), ("line", "Dashed tear-off line, no cut"), ("none", "Just feed paper"))),
        F("printer.feed_lines", "Extra paper feed before cut (lines)", "number", min=0, max=20),
        F("printer.max_prints_per_hour", "Safety limit: printouts per hour", "number", "Protects your paper roll from loops/spam.", min=1, max=1000),
        F("printer.retry_seconds", "Retry when printer is off/out of paper (seconds)", "number", min=5, max=3600),
        F("printer.max_attempts", "Give up after this many tries", "number", min=1, max=1000),
    ],
    "text": [
        F("text.split", "Split one email into separate reminders", "bool"),
        F("text.sort", "Print order", "select", options=opts(
            ("smart", "Smart: urgent first, then soonest deadline, then as written"),
            ("deadline", "Soonest deadline first"), ("original", "As written"))),
        F("text.fix_spelling", "Correct spelling", "bool", "Fixes obvious typos. It can't know every name - add yours below."),
        F("text.fix_grammar", "Correct grammar & punctuation", "bool"),
        F("text.trailing_period", "End short reminders with a full stop", "bool"),
        F("text.deadline_words", "Find dates/times and print them as a DUE line", "bool"),
        F("text.custom_words", "Words never to 'correct'", "lines", "Names, brands, slang - one per line."),
        F("text.priority_words", "Words that mark a reminder urgent", "lines"),
        F("general.date_order", "Dates like 4/11 mean", "select", options=opts(("dmy", "4 November (day first)"), ("mdy", "April 11 (month first)"))),
        F("general.time_format", "Clock", "select", options=opts(("12h", "12-hour (5:00 PM)"), ("24h", "24-hour (17:00)"))),
        F("general.timezone", "Timezone", help="Blank = the Pi's timezone. e.g. Australia/Sydney"),
        F("summary.sentences", "Forwarded-email summary length (bullet points)", "number", "How many bullet points the summary has (information first, then what to do).", min=2, max=8),
        F("summary.auto_symbols", "Pick 1-3 symbols for forwarded emails", "bool", "Only when it is clearly relevant: needs real evidence in the email (and, with AI mode on, the AI agreeing). If unsure it adds none. Symbols you type yourself always win."),
        F("summary.max_actions", "Max actions listed under a summary", "number", min=1, max=30),
        F("summary.show_original_sender", "Show who the forwarded email was from", "bool"),
        F("summary.backend", "Summary engine", "select", options=opts(
            ("extractive", "Built-in (instant; quotes the key sentences)"),
            ("ollama", "Local AI (Ollama; slow on a Pi, falls back to built-in if anything goes wrong)"))),
        F("summary.max_full_chars", "Longest [full] email printed (characters)", "number", min=300, max=20000, step=100),
        F("llm.url", "AI: Ollama address", help="On the Pi: http://127.0.0.1:11434. Or another computer, e.g. http://192.168.1.50:11434"),
        F("llm.model", "AI: model", help="qwen2.5:0.5b is the largest that fits a Pi 3B+ (1 GB)."),
        F("llm.timeout", "AI: give up after (seconds)", "number", "Each email waits at most this long, then uses the built-in summary.", min=10, max=900),
        F("llm.examples", "AI: worked examples shown first", "number", "Real-looking emails with a good summary, shown to the model before yours (from app/summary_examples.json). More = more accurate but slower; 0 = none. 1-2 suits a Pi, 3-4 a faster computer.", min=0, max=6),
        F("llm.max_input_chars", "AI: how much of the email it reads (characters)", "number", "The whole conversation if it fits. Longer emails lose their least useful sentences first.", min=500, max=12000, step=100),
        F("llm.num_predict", "AI: longest answer (tokens)", "number", "Room for 3-5 bullet points. Higher is slower.", min=60, max=800, step=10),
        F("llm.rules", "AI: your standing rules (one per line)", "lines", "Added to the AI's instructions, e.g. 'Always say who the email is from', 'Keep each bullet under 12 words'. Up to 8. You can also make one from a comment on the AI review tab."),
        F("llm.use_my_examples", "AI: learn from my reviewed summaries", "bool", "Summaries you marked good or corrected on the AI review tab are shown to the model as worked examples (closest first)."),
        F("llm.keep_history", "AI: keep a history of summarised emails for review", "bool", "Stored only on the Pi. Turn off if you'd rather not keep email text."),
        F("llm.history_days", "AI: keep that history for (days)", "number", "Reviewed ones you corrected are kept as long as you keep using them; the rest are deleted after this long.", min=1, max=730),
        F("llm.keep_alive", "AI: keep the model in memory for (seconds)", "number", "0 = free the RAM straight after each summary (best on a Pi).", min=0, max=3600),
    ],
    "calendar": [
        F("calendar.enabled", "Print calendar reminders", "bool"),
        F("calendar.print_time", "Print at", "time", "Events print at this time the day before, or the morning of if added after that."),
        F("calendar.late_policy", "Event added after the morning-of cutoff", "select", options=opts(
            ("print_now", "Print straight away (if it hasn't started)"), ("skip", "Don't print"))),
        F("calendar.also_day_of", "Also print a second copy on the morning of", "bool"),
        F("calendar.sync_minutes", "Check calendars every (minutes)", "number", min=1, max=1440),
        F("calendar.lookahead_days", "Look this many days ahead", "number", min=2, max=90),
        F("calendar.skip_all_day", "Ignore all-day events", "bool"),
        F("calendar.notes_chars", "Max characters of event description (0 = none)", "number", min=0, max=2000),
    ],
    "integrations": [
        F("integrations.api_enabled", "Allow other tools to print (HTTP API)", "bool",
          "Lets Zapier, Home Assistant, iOS Shortcuts, scripts, etc. POST a reminder to this Pi."),
        F("integrations.api_token", "API token", help="Send as header  Authorization: Bearer <token>"),
        F("integrations.webhook_url", "Webhook after each print", help="We POST JSON {id, kind, title, printed_at} here."),
        F("integrations.plugins_enabled", "Load Python plugins from the plugins/ folder", "bool"),
    ],
    "messages": [
        F("messages.hold_for_button", "Hold friends' messages until the button is pressed", "bool",
          "Off = friends' messages print straight away like yours."),
        F("messages.template", "Template for messages", "template", "Edit its look on the Templates tab (it's called “Message from a friend”)."),
        F("messages.tidy_text", "Fix spelling/grammar in friends' messages", "bool", "Off = printed exactly as they wrote it."),
        F("messages.max_held", "Most messages that can wait", "number", "Extra messages are dropped so a flood can't fill your queue.", min=1, max=500),
    ],
    "button": [
        F("button.enabled", "Use a physical button + LED", "bool"),
        F("button.button_pin", "Button pin (BCM/GPIO number)", "number", "Button between this pin and a GND pin. Default 17 (physical pin 11).", min=2, max=27),
        F("button.led_pin", "LED pin (BCM/GPIO number)", "number", "Through a resistor. Default 27 (physical pin 13). 0 = no LED.", min=0, max=27),
        F("button.led_active_high", "LED lights when the pin is HIGH", "bool", "Turn off if your LED is wired to 3.3 V instead of to GND."),
        F("button.blink_seconds", "Flash speed (seconds on/off)", "number", min=0.1, max=3, step=0.1),
    ],
    "fan": [
        F("fan.enabled", "Control a cooling fan from a GPIO pin", "bool"),
        F("fan.pin", "Fan pin (BCM/GPIO number)", "number", "To the transistor/MOSFET that switches the fan. Default 22 (physical pin 15).", min=2, max=27),
        F("fan.mode", "Mode", "select", options=opts(("auto", "Automatic (when hot, and during AI summaries)"), ("always", "Always on"), ("off", "Always off"))),
        F("fan.on_temp", "Turn on at (°C)", "number", "A Pi 3B+ starts easing its CPU speed down from about 60°C and slows hard near 80°C. A heatsink and open air are often enough without a fan.", min=40, max=80),
        F("fan.off_temp", "Turn off below (°C)", "number", "Lower than the on temperature so the fan doesn't flick on and off.", min=30, max=75),
        F("fan.during_ai", "Also run the fan during every AI summary", "bool", "The CPU works flat out and heats quickly; this cools it before it throttles."),
        F("fan.min_on_seconds", "Run for at least (seconds)", "number", min=0, max=3600),
        F("fan.active_high", "Fan switches on when the pin is HIGH", "bool", "Untick for an active-low relay/driver board."),
    ],
    "power": [
        F("power.idle_shutdown_hours", "Power off after being idle for (hours, 0 = never)", "number",
          "A Pi 3B+ can't sleep or wake itself, so this fully powers it OFF. While off nothing prints (email waits safely in Gmail). Wake it with the GPIO3 button or by re-plugging power.", min=0, max=168, step=0.5),
        F("power.min_uptime_minutes", "Never power off in the first (minutes after boot)", "number", min=5, max=600),
        F("power.keep_awake_before_calendar_hours", "Stay on if a calendar reminder prints within (hours)", "number", min=0, max=72),
    ],
    "sheet": [
        F("sheet.enabled", "Print new rows from the Google Sheet", "bool"),
        F("sheet.url", "Web app URL (from the Apps Script deployment)", help="Starts with https://script.google.com/macros/s/ and ends with /exec"),
        F("sheet.poll_seconds", "Check the sheet every (seconds)", "number", min=15, max=3600),
        F("sheet.timezone", "Timezone of the Schedule Send answers", help="Blank = the timezone Google reports for the spreadsheet (File -> Settings in the sheet). Set e.g. Australia/Sydney if scheduled times come out wrong."),
        F("sheet.treat_as", "Who are form submissions treated as?", "select", options=opts(
            ("admin", "Me (admin): print straight away, never wait for the button"),
            ("by_sender", "Decide by the sender's verified email (friends wait for the button)")),
          help="Pick 'by sender' only if you share the form with other people. It needs 'Collect email addresses: Verified' in the form."),
        F("sheet.unknown_policy", "…and rows from someone not on your lists (by-sender mode only)", "select", options=opts(
            ("hold", "Hold them until I press the button"), ("print", "Print them straight away"), ("ignore", "Ignore them (leave in the sheet)"))),
    ],
    "updates": [
        F("updates.enabled", "Install updates automatically", "bool",
          "Checks GitHub hourly. After an update the app restarts and the page is tested; if it doesn't come up it rolls back by itself."),
    ],
    "general": [
        F("general.history_keep_days", "Keep history for (days)", "number", min=1, max=3650),
    ],
}
