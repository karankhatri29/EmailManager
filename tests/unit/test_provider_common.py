import pytest

from app.providers.common import parse_list_unsubscribe


@pytest.mark.parametrize(
    "header,post,expected",
    [
        (None, None, (None, False)),
        ("", None, (None, False)),
        ("<https://x.com/unsub?u=1>", None, ("https://x.com/unsub?u=1", False)),
        ("<https://x.com/unsub>", "List-Unsubscribe=One-Click", ("https://x.com/unsub", True)),
        ("<https://x.com/unsub>", "list-unsubscribe = one-click", ("https://x.com/unsub", True)),
        (
            "<mailto:unsub@x.com?subject=stop>",
            "List-Unsubscribe=One-Click",
            ("mailto:unsub@x.com?subject=stop", False),
        ),
        ("<mailto:u@x.com>, <https://x.com/u>", None, ("https://x.com/u", False)),  # https preferred
        ("<http://insecure.com/u>", None, (None, False)),  # plain http is never used
        ("no angle brackets", None, (None, False)),
    ],
)
def test_parse_list_unsubscribe(header, post, expected):
    assert parse_list_unsubscribe(header, post) == expected
