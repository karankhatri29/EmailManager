import socket
from unittest.mock import MagicMock, patch

import pytest

from app.services import unsubscribe
from app.services.unsubscribe import UnsafeUrl, assert_public_https, one_click_unsubscribe


def _resolves_to(*addresses):
    return patch.object(
        unsubscribe.socket,
        "getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 443)) for a in addresses],
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.com/unsub",  # not https
        "ftp://shop.com/unsub",
        "https://shop.com:8443/unsub",  # unusual port
        "https://user:pw@shop.com/unsub",  # credentials in the URL
        "https:///nohost",
        "not a url",
    ],
)
def test_unsafe_link_shapes_are_rejected(url):
    with _resolves_to("93.184.216.34"), pytest.raises(UnsafeUrl):
        assert_public_https(url)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.9", "169.254.169.254", "::1", "0.0.0.0"]
)
def test_private_and_internal_addresses_are_refused(address):
    with _resolves_to(address), pytest.raises(UnsafeUrl, match="private"):
        assert_public_https("https://evil.example/unsub")


def test_a_host_with_any_private_address_is_refused():
    with _resolves_to("93.184.216.34", "10.0.0.1"), pytest.raises(UnsafeUrl):
        assert_public_https("https://mixed.example/unsub")


def test_unresolvable_host_is_refused():
    with (
        patch.object(unsubscribe.socket, "getaddrinfo", side_effect=socket.gaierror),
        pytest.raises(UnsafeUrl, match="could not be found"),
    ):
        assert_public_https("https://nope.invalid/unsub")


def test_public_https_link_is_accepted():
    with _resolves_to("93.184.216.34"):
        assert_public_https("https://shop.com/unsub?id=1")
        assert_public_https("https://shop.com:443/unsub")


def test_one_click_posts_the_rfc8058_body_without_following_redirects():
    response = MagicMock(status_code=200)
    with _resolves_to("93.184.216.34"), patch.object(unsubscribe.requests, "post", return_value=response) as post:
        assert one_click_unsubscribe("https://shop.com/unsub") is True

    assert post.call_args.kwargs["data"] == {"List-Unsubscribe": "One-Click"}
    assert post.call_args.kwargs["allow_redirects"] is False and post.call_args.kwargs["timeout"] <= 10
    response.close.assert_called_once()


@pytest.mark.parametrize("status,expected", [(200, True), (204, True), (302, False), (404, False), (500, False)])
def test_only_2xx_counts_as_success(status, expected):
    with _resolves_to("93.184.216.34"), patch.object(unsubscribe.requests, "post", return_value=MagicMock(status_code=status)):
        assert one_click_unsubscribe("https://shop.com/unsub") is expected


def test_nothing_is_sent_to_an_unsafe_link():
    with _resolves_to("127.0.0.1"), patch.object(unsubscribe.requests, "post") as post, pytest.raises(UnsafeUrl):
        one_click_unsubscribe("https://shop.com/unsub")
    post.assert_not_called()
