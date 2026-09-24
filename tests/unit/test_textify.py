import pytest

from app.services.textify import looks_like_html, normalize_body, strip_symbols

# A real placement-drive invitation: table layout, inline styles, a logo, a tracking pixel and an HTML comment.
PLACEMENT_EMAIL = """<!-- Email Subject Line: Congratulations! You're Eligible for LTM Placement Drive -->
<div style="max-width: 600px; margin: 40px auto; padding: 0 20px;"><!-- Top Border -->
<div style="background-color: #004d99; height: 4px;">&nbsp;</div>
<div style="background-color: #ffffff; padding: 40px; border: 1px solid #E5E5E5;">
<div style="margin-bottom: 30px;"><img style="width: 160px; height: auto;" src="https://storage.example.com/logo.jpg" alt="Logo"></div>
<h1 style="margin: 0 0 30px; font-size: 24px;">Placement Drive Invitation</h1>
<p style="margin: 0 0 20px;">Dear Karan Kaushik Khatri,</p>
<p style="margin: 0 0 20px;">Congratulations! Based on your profile, you are eligible to participate in the upcoming placement drive with LTM.</p>
<div style="margin: 30px 0; padding: 25px; background-color: #f5f5f5;">
<h2 style="margin: 0 0 20px;">Drive Details:</h2>
<table style="width: 100%; border-collapse: collapse;">
<tbody>
<tr><td style="padding: 8px 0; width: 140px;">Drive Name:</td><td style="padding: 8px 0;">LTM</td></tr>
<tr><td style="padding: 8px 0;">Drive Number:</td><td style="padding: 8px 0;">pat-PL-2026-1348</td></tr>
<tr><td style="padding: 8px 0;">Date:</td><td style="padding: 8px 0;"></td></tr>
<tr><td style="padding: 8px 0;">Company:</td><td style="padding: 8px 0;">LTM</td></tr>
</tbody>
</table>
</div>
<div style="margin: 30px 0; padding: 20px; background-color: #e6f3ff;"><p style="margin: 0;">Please log in to your placement portal to confirm your participation and view additional details about the drive.</p></div>
<div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #E5E5E5;">
<p style="margin: 0;">Best regards,<br><p>Dr. V. Samuel Rajkumar,</p>
<p>Director - Career Development Center, VIT</p></p>
<p style="margin: 20px 0 0; text-align: center;">Powered by <span style="color: #000000;">neo</span><span style="font-weight: bold;">PAT</span> &bull; A product of Iamneo</p>
</div></div><div style="background-color: #004d99; height: 4px;">&nbsp;</div></div><img src='https://mail.example.com/api/a/v?d=AIAADQSF' width='1' height='1' tabindex='-1' aria-hidden='true' alt='' />"""


def test_a_real_html_email_becomes_clean_readable_text():
    text = normalize_body(PLACEMENT_EMAIL)

    assert "<" not in text and ">" not in text and "style=" not in text and "&nbsp;" not in text
    assert "Email Subject Line" not in text  # HTML comments are not part of the message
    assert (
        "storage.example.com" not in text and "mail.example.com" not in text
    )  # no image URLs or tracking pixel
    lines = text.split("\n")
    assert lines[0] == "Placement Drive Invitation"
    assert "Dear Karan Kaushik Khatri," in lines
    assert "Drive Details:" in lines
    # table rows stay together as "label value" lines
    assert (
        "Drive Name: LTM" in lines and "Drive Number: pat-PL-2026-1348" in lines and "Company: LTM" in lines
    )
    assert "Date:" in lines
    assert (
        "Please log in to your placement portal to confirm your participation and view additional details about the drive."
        in lines
    )
    assert lines[-1] == "Powered by neoPAT • A product of Iamneo"
    assert "\n\n\n" not in text  # never more than one blank line in a row


def test_paragraphs_are_separated_and_table_rows_are_not():
    text = normalize_body(PLACEMENT_EMAIL)
    assert "Drive Name: LTM\nDrive Number: pat-PL-2026-1348\nDate:\nCompany: LTM" in text
    assert "Dear Karan Kaushik Khatri,\n\nCongratulations!" in text


def test_plain_text_passes_through_and_conversion_is_idempotent():
    plain = "Hi Sam,\n\nSee you at 3 < 4 pm, or 5 > 4 whatever.\n\nThanks"
    assert normalize_body(plain) == plain
    once = normalize_body(PLACEMENT_EMAIL)
    assert normalize_body(once) == once


def test_blank_and_missing_bodies():
    assert normalize_body(None) == "" and normalize_body("") == "" and normalize_body("   \n \n") == ""


@pytest.mark.parametrize(
    "markup,expected",
    [
        ("<p>Hello&nbsp;<b>world</b></p>", "Hello world"),
        ("one<br>two<br/>three", "one\ntwo\nthree"),
        ("<ul><li>a</li><li>b</li></ul>", "- a\n- b"),
        ("<p>Tom &amp; Jerry &lt;3 &copy; 2026</p>", "Tom & Jerry <3 © 2026"),
        ("<style>p{color:red}</style><script>alert(1)</script><p>visible</p>", "visible"),
        ("<head><title>Ignore me</title></head><body>Body text</body>", "Body text"),
        ("<a href='https://x.com/very/long/link'>Click here</a>", "Click here"),
        ("<div>a</div><div>b</div>", "a\nb"),
        ("<td>x</td><td>y</td>", "x y"),
        ("<pre>keep\n   this\n</pre>after", "keep\nthis\n\nafter"),
    ],
)
def test_html_elements(markup, expected):
    assert normalize_body(markup) == expected


def test_scripts_styles_and_hidden_head_content_never_leak():
    text = normalize_body(
        "<html><head><style>.a{}</style><title>T</title></head><body><script>var x = 1;</script>Hello</body></html>"
    )
    assert text == "Hello"


def test_invisible_padding_characters_are_removed():
    padded = "Preview text​‌‍⁠﻿͏­   then real content"
    assert normalize_body("<div>" + padded + "</div>") == "Preview text then real content"


def test_broken_markup_does_not_crash():
    for broken in (
        "<div><p>unclosed",
        "<<<>>>",
        "<div class='x",
        "text </b></i> stray closers",
        "<p>a</p></div></div>",
    ):
        assert isinstance(normalize_body(broken + "<br>"), str)
    assert normalize_body("<div><p>unclosed") == "unclosed"


def test_the_length_limit_applies_after_conversion():
    markup = (
        "<div style='" + "x" * 5000 + "'>real content</div>"
    )  # thousands of characters of markup, 12 of text
    assert normalize_body(markup, 4000) == "real content"
    assert len(normalize_body("word " * 2000, 100)) == 100


def test_looks_like_html():
    assert looks_like_html("<p>hi</p>") and looks_like_html("<!-- c --> x") and looks_like_html("a<br>b")
    assert not looks_like_html("just text, 1 < 2 and 3 > 2") and not looks_like_html("email me at <me@x.com>")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("\U0001f4dd Core Ask", "Core Ask"),
        ("Done ✅ ⚡ ❗️ now", "Done now"),
        ("time ⏱ and gear ⚙️", "time and gear"),
        ("plain text • with a bullet — and dash…", "plain text • with a bullet — and dash…"),
        ("", ""),
        (None, ""),
    ],
)
def test_strip_symbols_removes_emoji_but_keeps_ordinary_typography(text, expected):
    assert strip_symbols(text) == expected
