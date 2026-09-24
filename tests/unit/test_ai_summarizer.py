from unittest.mock import patch

from app.services import ai_summarizer


def test_clean_summary_text_builds_sections():
    raw = "### 📝 Core Ask\nSend the form.\n### ⚡ Tasks & Deadlines\n- Submit -> Friday"
    out = ai_summarizer.clean_summary_text(raw)
    assert "Core Ask" in out and "Action Tasks & Deadlines" in out
    assert "Send the form." in out and "Submit -&gt; Friday" in out


def test_clean_summary_text_escapes_html():
    out = ai_summarizer.clean_summary_text("<script>alert(1)</script>\n- <img src=x onerror=1>")
    assert "<script>" not in out and "<img" not in out
    assert "&lt;script&gt;" in out


def test_clean_summary_text_empty_and_preamble():
    assert ai_summarizer.clean_summary_text("") == ""
    assert "Here is" not in ai_summarizer.clean_summary_text("Here is your summary\nActual text")


def test_summarize_email_returns_none_on_failure():
    with patch.object(ai_summarizer, "_generate", side_effect=RuntimeError("api down")):
        assert ai_summarizer.summarize_email("body") is None


def test_summarize_email_success():
    with patch.object(ai_summarizer, "_generate", return_value="plain text") as gen:
        assert "plain text" in ai_summarizer.summarize_email("the body")
    assert "the body" in gen.call_args.args[0]
