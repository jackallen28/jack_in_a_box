"""[images attached]: scrape EVERY picture in a forwarded email - except ones known to be a signature at the bottom."""
import io
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import normalise  # noqa: E402
from app.focus import detect_image_mode, detect_image_request  # noqa: E402
from app.mailparse import _SIGNOFF, parse, signature_cids  # noqa: E402
from app.pipeline import build_cards  # noqa: E402

CFG = normalise({})


def png(size, col=(120, 120, 120)):
    b = io.BytesIO()
    Image.new("RGB", size, col).save(b, "PNG")
    return b.getvalue()


HTML = """<html><body><p>Hi Jack, here is the site photo:</p><img src="cid:photo@x"><p>And the plan:</p><img src="cid:plan@x">
<p>Thanks,</p><p>Dave Smith</p><img src="cid:logo@x"><img src="cid:badge@x">
<p>From: Old Sender</p><p>Earlier text</p><img src="cid:older@x"><p>Regards, Old</p><img src="cid:oldsig@x"></body></html>"""
PICS = (("photo", (400, 300), (200, 50, 50)), ("plan", (60, 40), (50, 50, 200)), ("logo", (120, 40), (10, 10, 10)), ("badge", (50, 50), (10, 200, 10)),
        ("older", (300, 200), (90, 90, 90)), ("oldsig", (100, 30), (1, 1, 1)), ("pixel", (1, 1), (0, 0, 0)))
NOTE = "\n\n---------- Forwarded message ---------\nFrom: Dave <dave@x.com>\nSubject: Site visit\n\nHi Jack, photos below.\nThanks,\nDave"


def html_forward(note="[images attached] chase Dave", pics=PICS, html=HTML):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Me <me@x.com>", "p@x.com", "Fwd: Site visit"
    m.set_content(note + NOTE)
    m.add_alternative(html, subtype="html")
    part = m.get_payload()[1]
    for cid, size, col in pics:
        part.add_related(png(size, col), maintype="image", subtype="png", cid=f"<{cid}@x>")
    return parse(m.as_bytes())


# ----------------------------------------------------------------------------- the tag
@pytest.mark.parametrize("text,mode,rest", [
    ("[images attached] chase Dave", "all", "chase Dave"), ("ring Dave [all images]", "all", "ring Dave"), ("[image attached]", "all", ""),
    ("[attached images] reply by friday", "all", "reply by friday"), ("include all the images please", "all", "please"), ("[any pictures]", "all", ""),
    ("include image", "some", ""), ("[image]", "some", ""), ("chase Dave include the photo", "some", "chase Dave"),
    ("reply with attachment please", None, "reply with attachment please"), ("the image quality is bad", None, "the image quality is bad"), ("", None, "")])
def test_image_tag_modes(text, mode, rest):
    got, left = detect_image_mode(text)
    assert got == mode and left.strip() == rest
    assert detect_image_request(text)[0] is (mode is not None)


# ----------------------------------------------------------------------------- knowing what is a signature
@pytest.mark.parametrize("line,yes", [("Kind regards,", True), ("Regards, Old", True), ("Thanks, Dave Smith", True), ("--", True), ("Sent from my iPhone", True),
                                     ("Best,", True), ("Thanks for the photos", False), ("Thanks for the photos, they look great", False), ("Here is the plan", False)])
def test_sign_off_lines(line, yes):
    assert bool(_SIGNOFF.match(line)) is yes


def test_only_pictures_after_a_sign_off_in_the_same_message_are_known_signatures():
    sig, other = signature_cids(HTML)
    assert sig == {"logo@x", "badge@x", "oldsig@x"} and other == {"photo@x", "plan@x", "older@x"}


# ----------------------------------------------------------------------------- scraping
def test_all_mode_takes_every_picture_but_known_signatures_and_tracking_pixels():
    inc = html_forward()
    assert sorted(im.size for im in inc.fwd_all_images) == [(60, 40), (300, 200), (400, 300)]       # body pictures, even the small plan
    assert inc.fwd_signature_skipped == 3                                                          # logo, badge, old signature
    assert all(im.size != (1, 1) for im in inc.fwd_all_images)
    assert sorted(im.size for im in inc.fwd_images) == [(300, 200), (400, 300)]                    # "include image" is unchanged (small ones ignored)


def test_the_printout_gets_them_all_and_says_what_it_left_out():
    (card,), _ = build_cards(html_forward(), CFG)
    assert sorted(im.size for im in card.images) == [(60, 40), (300, 200), (400, 300)]
    assert any("included 3 image(s) (all pictures in the email)" in n for n in card.notes)
    assert any("left out 3 picture(s) it knew were in a signature" in n for n in card.notes)
    actions = next(s for s in card.sections if s.key == "actions").items
    assert actions[0] == "Chase Dave" and not any("image" in a.lower() for a in actions)             # the tag isn't left in your to-do


def test_include_image_still_prints_only_the_real_pictures_and_no_tag_means_no_pictures():
    (some,), _ = build_cards(html_forward("include image chase Dave"), CFG)
    assert sorted(im.size for im in some.images) == [(300, 200), (400, 300)]
    (none,), _ = build_cards(html_forward("chase Dave"), CFG)
    assert none.images == []


def test_a_picture_that_might_be_a_signature_but_is_not_known_to_be_one_is_included():
    html = "<html><body><p>Hello Jack, our new logo:</p><img src='cid:logo@x'><p>Call me.</p></body></html>"      # no sign-off: position says nothing
    inc = html_forward(pics=[("logo", (90, 30), (5, 5, 5))], html=html)
    assert [im.size for im in inc.fwd_all_images] == [(90, 30)] and inc.fwd_signature_skipped == 0


def test_pictures_attached_to_the_email_are_always_kept_even_if_their_cid_is_in_a_signature():
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Me <me@x.com>", "p@x.com", "Fwd: Site visit"
    m.set_content("[images attached] see below" + NOTE)
    m.add_attachment(png((500, 400), (30, 60, 90)), maintype="image", subtype="png", filename="site.png")
    m.add_attachment(png((500, 400), (30, 60, 90)), maintype="image", subtype="png", filename="same-again.png")      # an identical copy
    m.add_attachment(png((200, 150), (90, 60, 30)), maintype="image", subtype="jpeg", filename="other.jpg")
    inc = parse(m.as_bytes())
    assert sorted(im.size for im in inc.fwd_all_images) == [(200, 150), (500, 400)]                  # duplicates collapsed
    (card,), _ = build_cards(inc, CFG)
    assert len(card.images) == 2


def test_the_number_of_pictures_is_capped_by_the_setting():
    pics = [(f"p{i}", (200 + i, 150), (i * 20, 0, 0)) for i in range(7)]
    html = "<html><body>" + "".join(f"<img src='cid:p{i}@x'>" for i in range(7)) + "</body></html>"
    cfg = normalise({"email": {"max_all_images": 4}})
    (card,), _ = build_cards(html_forward(pics=pics, html=html), cfg)
    assert len(card.images) == 4
    assert len(build_cards(html_forward(pics=pics, html=html), CFG)[0][0].images) == 7


def test_asking_when_there_are_no_pictures_is_noted_not_fatal():
    (card,), _ = build_cards(html_forward(pics=[], html="<html><body><p>No pictures here</p></body></html>"), CFG)
    assert card.images == [] and any("pictures requested but the email has none" in n for n in card.notes)


def test_a_mixed_bag_of_pictures_renders_on_the_printout():
    from app.render import render_card
    (card,), _ = build_cards(html_forward(), CFG)
    img = render_card(card, CFG["templates"]["email"], CFG["printer"], CFG["icons"])
    assert img.size[0] == 576 and img.size[1] > 900                      # three pictures stacked under the text
