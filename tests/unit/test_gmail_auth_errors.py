import json

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from app.providers.gmail import is_auth_failure


def error(status, reason="", api_status=""):
    body = {"error": {"status": api_status, "errors": [{"reason": reason}] if reason else []}}
    return HttpError(Response({"status": status}), json.dumps(body).encode())


@pytest.mark.parametrize(
    "exc,expected",
    [
        (error(401, "authError"), True),
        (error(403, "insufficientPermissions"), True),
        (error(403, "accessNotConfigured"), True),
        (error(403), True),  # unexplained 403: treat as a login problem
        (error(403, "rateLimitExceeded"), False),  # rate limits are not a login problem
        (error(403, "userRateLimitExceeded"), False),
        (error(403, "dailyLimitExceeded"), False),
        (error(403, "", "RESOURCE_EXHAUSTED quota"), False),
        (error(429, "rateLimitExceeded"), False),
        (error(500), False),
    ],
)
def test_only_real_login_failures_ask_for_a_reconnect(exc, expected):
    assert is_auth_failure(exc) is expected
