from unittest.mock import patch

from app.services import ai_summarizer
from app.services.textify import strip_symbols


def test_clean_summary_text_builds_sections():
    raw = "### Core Ask\nSend the form.\n### Tasks & Deadlines\n- Submit -> Friday"
    out = ai_summarizer.clean_summary_text(raw)
    assert "Core Ask" in out and "Action Tasks &amp; Deadlines" in out
    assert "Send the form." in out and "Submit -&gt; Friday" in out


def test_thread_sections_get_neutral_headings():
    out = ai_summarizer.clean_summary_text("### Decisions\n- Ship on Monday\n### Open Questions\n- Who pays?")
    assert "Decisions" in out and "Open Questions" in out and "Ship on Monday" in out and "Who pays?" in out


def test_clean_summary_text_escapes_html():
    out = ai_summarizer.clean_summary_text("<script>alert(1)</script>\n- <img src=x onerror=1>")
    assert "<script>" not in out and "<img" not in out
    assert "&lt;script&gt;" in out


def test_clean_summary_text_empty_and_preamble():
    assert ai_summarizer.clean_summary_text("") == ""
    assert "Here is" not in ai_summarizer.clean_summary_text("Here is your summary\nActual text")


def test_emoji_the_model_adds_anyway_are_removed():
    raw = "### \U0001f4dd Core Ask\nPay the fee ⚡ today\n### ✅ Decisions\n- Done ✅"
    out = ai_summarizer.clean_summary_text(raw)
    assert strip_symbols(out) == out  # nothing left to strip
    assert "Core Ask" in out and "Pay the fee today" in out and "Decisions" in out


def test_the_prompts_ask_for_plain_text_and_contain_no_emoji():
    for prompt in (ai_summarizer.SUMMARY_PROMPT, ai_summarizer.THREAD_PROMPT):
        assert strip_symbols(prompt) == prompt
        assert "Do not use emoji" in prompt


def test_summarize_email_returns_none_on_failure():
    with patch.object(ai_summarizer, "_generate", side_effect=RuntimeError("api down")):
        assert ai_summarizer.summarize_email("body") is None


def test_summarize_email_success():
    with patch.object(ai_summarizer, "_generate", return_value="plain text") as gen:
        assert "plain text" in ai_summarizer.summarize_email("the body")
    assert "the body" in gen.call_args.args[0]


def test_generate_text_is_emoji_free():
    with patch.object(ai_summarizer, "_generate", return_value="Great deal \U0001f389 on laptops"):
        assert ai_summarizer.generate_text("prompt") == "Great deal on laptops"
