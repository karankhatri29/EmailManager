"""One-click unsubscribe (RFC 8058) for bulk senders.

The link comes from an email header, i.e. from an untrusted sender, and this server would be the one
opening it. So it is only ever contacted if it is https on the default port, resolves to public
addresses only, and it is never followed through redirects.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import requests

TIMEOUT_SECONDS = 10
USER_AGENT = "EmailPrioritizer-Unsubscribe/1.0"


class UnsafeUrl(ValueError):
    """The unsubscribe link must not be contacted from this server."""


def assert_public_https(url: str) -> None:
    parts = urlparse(url)
    if parts.scheme != "https":
        raise UnsafeUrl("Only https links can be used")
    if not parts.hostname or parts.username or parts.password:
        raise UnsafeUrl("Malformed link")
    if parts.port not in (None, 443):
        raise UnsafeUrl("Unexpected port")

    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise UnsafeUrl("The link's host could not be found") from exc
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
        raise UnsafeUrl("Refusing to contact a private or internal address")


def one_click_unsubscribe(url: str) -> bool:
    """Sends the RFC 8058 one-click POST. True if the sender accepted it (HTTP 2xx).

    Raises UnsafeUrl if the link is not safe to contact; network errors propagate as requests exceptions.
    """
    assert_public_https(url)
    response = requests.post(
        url,
        data={"List-Unsubscribe": "One-Click"},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
        allow_redirects=False,
        stream=True,  # we never read the body
    )
    response.close()
    return 200 <= response.status_code < 300
