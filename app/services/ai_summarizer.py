import html
import json
import logging
import os
import re

from google import genai
from google.genai import types

from ..core.config import get_settings
from .textify import strip_symbols

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """Extract email insights into this exact Markdown layout.
Omit any section lacking explicit data. Do NOT write any introduction or greetings. Do not use emoji.

### Core Ask
[2-3 sentences on what sender needs + deadlines if any]

### Sender
[Sender name/org + purpose]

### Important Facts
- [Key names, system/tracking IDs, or key figures]

### Tasks & Deadlines
- [Actionable task] -> [Deadline or 'Immediate Action Required']

Email Text:
{text}"""

THREAD_PROMPT = """Summarise this email conversation for someone who was copied on it and has not read it.
Write exactly three bullet points, each one sentence, covering the core decisions reached and the main
arguments or open disagreements behind them, most important first. Name who decided or argued what when it
matters. Use plain "- " bullets, no headings and no introduction. Do not use emoji.

Messages, oldest first:
{text}"""

THREAD_PART_PROMPT = """These are the first messages of a long email conversation. Write short notes (at most 120
words) on the decisions made, the main arguments, who took which side, and anything left open. Keep names and
dates. Plain text, no emoji.

{text}"""


def _generate(prompt):
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key or None)
    response = client.models.generate_content(model=settings.gemini_model, contents=prompt)
    return response.text


def generate_json(prompt, schema):
    """Asks the model for JSON matching a pydantic schema; returns a dict. Raises if the call fails."""
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key or None)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema),
    )
    return json.loads(response.text or "{}")


def is_configured():
    """Whether an API key is available, so optional AI features can be skipped instead of failing slowly."""
    return bool(
        get_settings().gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    )


def generate_text(prompt):
    """Plain-text generation, without emoji. Raises if the call fails."""
    return strip_symbols(_generate(prompt) or "")


def condense_thread_part(text):
    """Short notes on one stretch of a very long conversation. Raises if the AI call fails."""
    return generate_text(THREAD_PART_PROMPT.format(text=text))


def three_bullets(raw_text):
    """Exactly three "- " bullets from whatever the model returned (bullets, numbered lines or a paragraph)."""
    text = strip_symbols(raw_text or "")
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", ln).strip() for ln in text.splitlines()]
    items = [ln for ln in lines if ln and not ln.startswith("#") and not ln.lower().startswith("here")]
    if len(items) == 1:  # one paragraph: split it into sentences
        items = [x.strip() for x in re.split(r"(?<=[.!?])\s+", " ".join(items)) if x.strip()]
    return "\n".join(f"- {item}" for item in items[:3])


def summarize_thread(text):
    """Three bullets (as HTML) on the core decisions and arguments of a conversation. Raises if the AI call fails."""
    return clean_summary_text(three_bullets(_generate(THREAD_PROMPT.format(text=text))))


def summarize_email(text):
    """Returns compressed, structured HTML for the email, or None if the AI call fails."""
    try:
        return clean_summary_text(_generate(SUMMARY_PROMPT.format(text=text)))
    except Exception:
        logger.exception("Email summarisation failed")
        return None


# Section title -> (label shown, colour). Anything else gets a neutral heading.
_SECTIONS = {
    "core ask": ("Core Ask", "blue"),
    "sender": ("Sender & Context", "purple"),
    "important facts": ("Important Facts", "emerald"),
    "tasks & deadlines": ("Action Tasks & Deadlines", "red"),
    "tasks": ("Action Tasks & Deadlines", "red"),
}
_HEADING = "text-{colour}-400 font-bold border-b border-{colour}-500/20 pb-1 mb-2 text-xs uppercase tracking-wider mt-4"


def clean_summary_text(raw_text):
    """Turns the model's Markdown into small HTML blocks: section headings, bullets and paragraphs.

    The model's output is stripped of emoji and HTML-escaped before it is embedded in markup.
    """
    text = strip_symbols(raw_text)
    if not text:
        return ""

    lines = text.split("\n")
    if lines and lines[0].strip().lower().startswith("here"):
        lines = lines[1:]  # a chatty preamble

    blocks = []
    for line in "\n".join(lines).replace("###", "\n###").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("###"):
            title = stripped.lstrip("#").strip()
            label, colour = _SECTIONS.get(title.lower(), (title, "slate"))
            blocks.append(f"<div class='{_HEADING.format(colour=colour)}'>{html.escape(label)}</div>")
        elif stripped.startswith(("-", "*")):
            item = html.escape(stripped.lstrip("-* ").strip())
            blocks.append(
                f"<div class='pl-2 text-slate-300 text-sm my-1 flex items-start gap-2'><span>&bull;</span><span>{item}</span></div>"
            )
        else:
            blocks.append(
                f"<p class='text-slate-200 text-sm leading-relaxed mb-2'>{html.escape(stripped)}</p>"
            )

    return "".join(blocks)
