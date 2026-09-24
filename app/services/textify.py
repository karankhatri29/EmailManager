"""Turns an email body into clean, readable plain text.

Many emails are only HTML. Showing that markup to a person, or feeding it to the classifier and the AI,
is useless, so bodies are converted when mail is synced (and again when shown, for mail stored earlier).
Plain text passes through unchanged, so running this twice is harmless.
"""

import re
from html.parser import HTMLParser

# Elements that start and end a line of text.
_BLOCK = frozenset(
    [
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "html",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "tfoot",
        "thead",
        "tr",
        "ul",
    ]
)
# Elements whose content is never part of the message.
_SKIP = frozenset(["script", "style", "head", "title", "noscript", "template", "svg", "object", "iframe"])
_CELLS = frozenset({"td", "th"})
# Elements set apart by a blank line; other block elements (div, tr, ...) only start a new line.
_PARAGRAPH = frozenset(["p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table", "ul", "ol"])

# Anything that is clearly HTML. Only real tag names count, so "<me@x.com>" or "1 < 2" stay plain text.
_HTML_HINT = re.compile(
    r"<\s*/?\s*(html|head|body|div|p|span|table|tbody|thead|tfoot|tr|td|th|br|hr|img|a|h[1-6]|ul|ol|li|dl|dt|dd|"
    r"style|script|meta|link|title|center|font|b|i|u|s|small|sub|sup|strong|em|blockquote|pre|code|section|article|"
    r"header|footer|nav|main|aside|form|label|button|input|figure|figcaption|iframe)\b[^>]*>|<!--",
    re.IGNORECASE,
)

# Line-break markers used while extracting; collapsed into real newlines at the end so that adjacent
# blocks (</td></tr><tr><td>, </div><div>) produce one line break, not a blank line.
_SOFT = chr(1)  # start a new line
_PARA = chr(2)  # leave a blank line
_BREAKS = re.compile(
    "(?:[ " + chr(9) + "]*[" + _SOFT + _PARA + chr(10) + "][ " + chr(9) + "]*)+"
)  # breaks with the spaces around them

# Invisible padding that marketing email pads its preview text with.
_INVISIBLE = re.compile(
    "[" + "".join(chr(c) for c in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x034F, 0x00AD)) + "]"
)


def looks_like_html(text: str) -> bool:
    return bool(_HTML_HINT.search(text))


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._pre = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif self._skip:
            return
        elif tag == "br":
            self.parts.append("\n")  # an explicit line break; two in a row make a blank line
        elif tag == "li":
            self.parts.append(_SOFT + "- ")
        elif tag in _BLOCK:
            self.parts.append(_PARA if tag in _PARAGRAPH else _SOFT)
            if tag == "pre":
                self._pre += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif self._skip:
            return
        elif tag in _CELLS:
            self.parts.append(" ")
        elif tag in _BLOCK:
            self.parts.append(_PARA if tag in _PARAGRAPH else _SOFT)
            if tag == "pre":
                self._pre = max(0, self._pre - 1)

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        data = data.replace(_SOFT, "").replace(_PARA, "")
        # HTML collapses whitespace; only real block/line-break elements make new lines (except inside <pre>).
        self.parts.append(data if self._pre else re.sub(r"\s+", " ", data))


def _collapse_breaks(match: re.Match) -> str:
    run = match.group()
    return "\n\n" if _PARA in run or run.count("\n") >= 2 else "\n"


def html_to_text(markup: str) -> str:
    parser = _Extractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # broken markup: fall back to dropping every tag
        return re.sub(r"<[^>]*>", " ", markup)
    return _BREAKS.sub(_collapse_breaks, "".join(parser.parts))


def _tidy(text: str) -> str:
    text = _INVISIBLE.sub("", text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " "))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):  # keep at most one blank line in a row
            out.append(line)
    return "\n".join(out).strip()


def normalize_body(text: str | None, limit: int | None = None) -> str:
    """The readable text of an email body (HTML converted, whitespace tidied), optionally capped at `limit` characters."""
    if not text:
        return ""
    if looks_like_html(text):
        text = html_to_text(text)
    text = _tidy(text)
    return text[:limit] if limit else text


# Emoji, pictographs and dingbats. The app's own labels and AI-written text never use them.
_SYMBOL_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x2300, 0x23FF),
    (0xFE0F, 0xFE0F),
    (0x20E3, 0x20E3),
)
_SYMBOLS = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _SYMBOL_RANGES) + "]")


def strip_symbols(text: str | None) -> str:
    """Removes emoji and pictographs (used on text the AI wrote, never on what people wrote to you)."""
    if not text:
        return ""
    return re.sub(r" {2,}", " ", _SYMBOLS.sub("", text)).strip()
