import html
import logging
import re

from google import genai

from ..core.config import get_settings

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """Extract email insights into this exact Markdown layout.
Omit any section lacking explicit data. Do NOT write any introduction or greetings.

### 📝 Core Ask
[2-3 sentences on what sender needs + deadlines if any]

### 👤 Sender
[Sender name/org + purpose]

### 📌 Important Facts
- [Key names, system/tracking IDs, or key figures]

### ⚡ Tasks & Deadlines
- [Actionable task] -> [Deadline or 'Immediate Action Required']

Email Text:
{text}"""


def _generate(prompt):
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key or None)
    response = client.models.generate_content(model=settings.gemini_model, contents=prompt)
    return response.text


def summarize_email(text):
    """Returns compressed, structured HTML for the email, or None if the AI call fails."""
    try:
        return clean_summary_text(_generate(SUMMARY_PROMPT.format(text=text)))
    except Exception:
        logger.exception("Email summarisation failed")
        return None


def clean_summary_text(raw_text):
    """Parses raw AI blocks, removes ### markers, and injects clean layout breaks.

    Model output is HTML-escaped before it is embedded in markup.
    """
    if not raw_text:
        return ""

    text = raw_text.strip()

    if text.lower().startswith("here"):
        text = "\n".join(text.split("\n")[1:]).strip()

    # Inject padding around raw markdown lines if compressed
    text = re.sub(r"([^\n])###", r"\1\n\n###", text)

    text = re.sub(r"### 📝\s*Core Ask\s*", "### 📝 Core Ask\n", text)
    text = re.sub(r"### 👤\s*Sender\s*", "### 👤 Sender\n", text)
    text = re.sub(r"### 📌\s*Important Facts\s*", "### 📌 Important Facts\n", text)
    text = re.sub(r"### ⚡\s*(Tasks & Deadlines|Tasks)\s*", "### ⚡ Tasks & Deadlines\n", text)

    processed_blocks = []

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("### 📝 Core Ask"):
            processed_blocks.append(
                "<div class='text-blue-400 font-bold border-b border-blue-500/20 pb-1 mb-2 text-xs uppercase tracking-wider mt-1'>📝 Core Ask</div>"
            )
        elif stripped.startswith("### 👤 Sender"):
            processed_blocks.append(
                "<div class='text-purple-400 font-bold border-b border-purple-500/20 pb-1 mb-2 text-xs uppercase tracking-wider mt-4'>👤 Sender & Context</div>"
            )
        elif stripped.startswith("### 📌 Important Facts"):
            processed_blocks.append(
                "<div class='text-emerald-400 font-bold border-b border-emerald-500/20 pb-1 mb-2 text-xs uppercase tracking-wider mt-4'>📌 Important Facts</div>"
            )
        elif stripped.startswith("### ⚡ Tasks & Deadlines"):
            processed_blocks.append(
                "<div class='text-red-400 font-bold border-b border-red-500/20 pb-1 mb-2 text-xs uppercase tracking-wider mt-4'>⚡ Action Tasks & Deadlines</div>"
            )
        elif stripped.startswith("-") or stripped.startswith("*"):
            clean_item = html.escape(stripped.lstrip("-* ").strip())
            processed_blocks.append(
                f"<div class='pl-2 text-slate-300 text-sm my-1 flex items-start gap-2'><span>•</span><span>{clean_item}</span></div>"
            )
        else:
            processed_blocks.append(
                f"<p class='text-slate-200 text-sm leading-relaxed mb-2'>{html.escape(stripped)}</p>"
            )

    return "".join(processed_blocks)
