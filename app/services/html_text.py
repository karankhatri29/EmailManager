"""HTML to readable plain text (stdlib only), used on booking emails, which are almost always HTML-only."""

import re
from html import unescape
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "blockquote", "hr",
}  # fmt: skip
_CELL_TAGS = {"td", "th"}
_SKIP_TAGS = {"script", "style", "head", "title"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self._newline()
        elif tag in _CELL_TAGS:
            self.parts.append("  ")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self._newline()

    def _newline(self) -> None:
        if not self.parts or not self.parts[-1].endswith("\n"):  # one break per block edge
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


_TAG_HINT = re.compile(r"<\s*(?:html|body|div|p|br|table|td|span|a|b|strong)\b", re.IGNORECASE)


def looks_like_html(text: str) -> bool:
    return bool(_TAG_HINT.search(text or ""))


def html_to_text(text: str) -> str:
    """Plain text with the line structure kept (one line per block / table row). Non-HTML passes through."""
    if not text:
        return ""
    if looks_like_html(text):
        extractor = _Extractor()
        try:
            extractor.feed(text)
            extractor.close()
            text = "".join(extractor.parts)
        except Exception:  # malformed markup: fall back to dropping tags with a regex
            text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ").replace("​", "")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):  # collapse runs of blank lines
            out.append(line)
    return "\n".join(out).strip()
