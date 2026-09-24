"""Helpers shared by the mail providers."""

import re

_ANGLE = re.compile(r"<([^>]+)>")


def parse_list_unsubscribe(header: str | None, post_header: str | None = None) -> tuple[str | None, bool]:
    """Reads the List-Unsubscribe headers (RFC 2369 / RFC 8058).

    Returns (url, one_click). The https link is preferred over a mailto: link; one_click is True only
    when the sender advertises RFC 8058 one-click support for that https link.
    """
    if not header:
        return None, False
    targets = [t.strip() for t in _ANGLE.findall(header)]
    https = next((t for t in targets if t.lower().startswith("https://")), None)
    if https:
        one_click = bool(post_header and "list-unsubscribe=one-click" in post_header.lower().replace(" ", ""))
        return https, one_click
    mailto = next((t for t in targets if t.lower().startswith("mailto:")), None)
    return mailto, False
