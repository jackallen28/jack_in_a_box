"""Forwarded emails in the shapes real mail clients produce: reply chains, Apple Mail's "> " quoting, Outlook
blocks, forwards with no recognisable marker.  The summary must describe the forwarded message and its history."""
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import normalise  # noqa: E402
from app.emailclean import clean_forwarded, thread_messages  # noqa: E402
from app.mailparse import parse  # noqa: E402
from app.pipeline import build_cards  # noqa: E402

CFG = normalise({})


def mail(subject, body):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Jack <jack@example.com>", "box@gmail.com", subject
    m["Message-ID"], m["Date"] = "<x@y>", "Tue, 3 Nov 2026 10:00:00 +1100"
    m.set_content(body)
    return m.as_bytes()


def card(subject, body):
    (c,), _ = build_cards(parse(mail(subject, body)), CFG)
    return c


def section(c, key):
    return next((s for s in c.sections if s.key == key), None)


GMAIL_CHAIN = """chase Dave

---------- Forwarded message ---------
From: Dave Smith <dave@supplier.com>
Date: Mon, 2 Nov 2026 at 4:10 PM
Subject: Re: Invoice 4471
To: Jack <jack@example.com>

Thanks Jack. I can confirm the remittance is not here yet. Please send it by Friday or we will have to charge a late fee of $150.

Regards,
Dave Smith
Accounts Manager
T: 02 9999 1234

On Fri, 30 Oct 2026 at 9:15 AM, Jack <jack@example.com> wrote:
> Hi Dave, we paid invoice 4471 on Tuesday through the bank. Can you check your end?
>
> On Thu, 29 Oct 2026 at 2:00 PM, Dave Smith <dave@supplier.com> wrote:
>> Hi Jack, invoice 4471 for $12,480 is 20 days overdue. Please confirm when payment will be made.
>>
>> Regards,
>> Dave Smith
>> Accounts Manager
"""
APPLE = """chase Dave about the invoice

Begin forwarded message:

> From: Dave Smith <dave@supplier.com>
> Subject: Invoice 4471 overdue
> Date: 2 November 2026 at 4:10:00 pm AEDT
> To: Jack <jack@example.com>
>
> Hi Jack, invoice 4471 for $12,480 is 20 days overdue. Please confirm when payment will be made.
> We are also changing our bank details from 1 December.
>
> Regards,
> Dave
"""
OUTLOOK_CHAIN = """ring Dave

From: Dave Smith <dave@supplier.com>
Sent: Monday, 2 November 2026 4:10 PM
To: Jack
Subject: RE: Invoice 4471

Can you confirm the remittance by Friday? Otherwise a $150 late fee applies.

Kind regards,
Dave Smith
Accounts Manager
T: 02 9999 1234

From: Jack <jack@example.com>
Sent: Friday, 30 October 2026 9:15 AM
To: Dave Smith
Subject: RE: Invoice 4471

We paid invoice 4471 on Tuesday through the bank.

From: Dave Smith <dave@supplier.com>
Sent: Thursday, 29 October 2026 2:00 PM
To: Jack
Subject: Invoice 4471

Invoice 4471 for $12,480 is 20 days overdue.
"""
NO_MARKER = "see below\n\nHi Jack, invoice 4471 for $12,480 is 20 days overdue. Please confirm when payment will be made by Friday. We are also changing our bank details from 1 December.\n\nRegards, Dave"


# ----------------------------------------------------------------------------- the reported problems
def test_reply_chain_summary_uses_the_whole_conversation():
    c = card("Fwd: Re: Invoice 4471", GMAIL_CHAIN)
    summary = section(c, "summary").text
    assert "remittance is not here yet" in summary                      # where it stands now
    assert "$12,480" in summary and "20 days overdue" in summary        # how it began (from the OLDEST, doubly-quoted message)
    assert "we paid invoice 4471" in summary                            # the middle of the conversation
    assert "Accounts Manager" not in summary and "02 9999" not in summary   # signatures stay out, in every message
    assert c.subline == "From Dave Smith · 3 messages"
    assert section(c, "actions").items == ["Chase Dave"]


def test_apple_mail_quoted_forward_is_summarised_not_lost():
    c = card("Fwd: Invoice 4471 overdue", APPLE)
    summary = section(c, "summary").text
    assert "$12,480" in summary and "20 days overdue" in summary and "bank details" in summary
    assert "no readable text" not in summary
    assert section(c, "actions").items == ["Chase Dave about the invoice"]      # your note stays an action
    assert c.subline == "From Dave Smith" and c.heading == "Invoice 4471 overdue"


def test_forward_with_no_recognisable_marker_is_still_split_into_note_and_email():
    ib = parse(mail("Fwd: Invoice", NO_MARKER))
    assert ib.guessed_forward and ib.body == "see below" and "invoice 4471" in ib.forwarded
    c = card("Fwd: Invoice", NO_MARKER)
    assert section(c, "actions").items == ["See below"]                         # NOT the whole email as a to-do list
    assert "$12,480" in section(c, "summary").text


def test_outlook_chain_is_read_newest_first_without_signatures():
    c = card("FW: Invoice 4471", OUTLOOK_CHAIN)
    summary = section(c, "summary").text
    assert "$12,480" in summary and "paid invoice 4471" in summary and "late fee" in summary
    assert "Accounts Manager" not in summary and "Sent:" not in summary
    assert c.subline.endswith("3 messages")


# ----------------------------------------------------------------------------- the parser
def test_thread_messages_order_senders_and_cleaning():
    parsed = parse(mail("Fwd: Re: Invoice 4471", GMAIL_CHAIN))
    msgs = thread_messages(parsed.forwarded)
    assert len(msgs) == 3
    assert "not here yet" in msgs[0]["text"] and "we paid" in msgs[1]["text"] and "20 days overdue" in msgs[2]["text"]   # newest first
    assert msgs[1]["sender"] == "Jack" and "30 Oct" in msgs[1]["date"]
    assert msgs[2]["sender"] == "Dave Smith"
    assert not any(">" in m["text"] for m in msgs)                                                # quote marks removed


def test_wrapped_attribution_line_and_a_reply_typed_below_the_quote():
    text = ("Newest note is here.\n\nOn Mon, 2 Nov 2026 at 9:15 AM, Dave Smith\n<dave@supplier.com> wrote:\n"
            "> The older message about the budget.\n\nMy answer is typed underneath the quote.")
    msgs = thread_messages(text)
    assert len(msgs) == 2
    assert "Newest note" in msgs[0]["text"] and "typed underneath" in msgs[0]["text"]    # bottom-posted reply = newest
    assert "older message about the budget" in msgs[1]["text"] and msgs[1]["sender"] == "Dave Smith"


def test_clean_forwarded_default_is_still_only_the_newest_message():
    assert clean_forwarded("Yes, Tuesday works.\n\nOn Mon, 2 Nov 2026 at 9:15 AM, Dave <d@x.com> wrote:\n> Can we meet Tuesday?") == "Yes, Tuesday works."
    both = clean_forwarded("Yes, Tuesday works.\n\nOn Mon, 2 Nov 2026 at 9:15 AM, Dave <d@x.com> wrote:\n> Can we meet Tuesday?", keep_thread=True)
    assert "Tuesday works" in both and "Can we meet" in both


def test_a_very_deep_or_odd_chain_cannot_hang_or_lose_everything():
    deep = "latest\n\n" + "".join(f"On Mon, 1 Jan 2026, P{i} <p@x.com> wrote:\n" + "> " * (i + 1) + f"message {i}\n" for i in range(40))
    msgs = thread_messages(deep)
    assert 1 <= len(msgs) <= 12 and "latest" in msgs[0]["text"]
    assert thread_messages("") == [] and thread_messages("just one plain message")[0]["text"] == "just one plain message"


def test_a_fully_quoted_message_keeps_its_words():
    assert "important words" in clean_forwarded("> some important words here\n> and more")


@pytest.mark.parametrize("marker", [
    "---------- Forwarded message ---------", "Begin forwarded message:", "----- Forwarded Message -----",
    "-------- Forwarded Message --------", "-----Original Message-----", "> Begin forwarded message:",
])
def test_common_forward_markers_all_work(marker):
    raw = f"my note\n\n{marker}\nFrom: Dave <d@x.com>\nSubject: Budget\nDate: Mon\nTo: me\n\nThe budget is $5,000 and is due Friday."
    ib = parse(mail("Fwd: Budget", raw))
    assert ib.body == "my note" and "budget is $5,000" in ib.forwarded and not ib.guessed_forward


# ----------------------------------------------------------------------------- [full] and [focus] now see the history too
def test_full_includes_earlier_messages_labelled_and_capped():
    c = card("Fwd: Re: Invoice 4471", "[full] ring Dave\n" + GMAIL_CHAIN.split("\n", 1)[1])
    full = section(c, "full").text
    assert "not here yet" in full and "EARLIER - Jack" in full and "20 days overdue" in full
    assert "Accounts Manager" not in full


def test_focus_can_find_answers_in_earlier_messages():
    c = card("Fwd: Re: Invoice 4471", "[how much was the invoice] [paid] chase\n" + GMAIL_CHAIN.split("\n", 1)[1])
    texts = {s.label: s.text for s in c.sections if s.key == "focus"}
    assert "$12,480" in texts["HOW MUCH WAS THE INVOICE"]
    assert "we paid invoice 4471" in texts["PAID"]
