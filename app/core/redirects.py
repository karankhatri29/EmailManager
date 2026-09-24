"""The address Google / Microsoft send the browser back to after sign-in (the OAuth "redirect URI").

It must match, character for character, an address registered with the provider. It comes from GOOGLE_REDIRECT_URI /
MICROSOFT_REDIRECT_URI when those are set properly. When they are missing, still the local-development default, or a
leftover placeholder such as "https://<name>.vercel.app/...", the address the visitor actually used is taken instead,
so a hosted copy works without guessing its own domain. GET /api/accounts/oauth-redirects shows what will be used.
"""

from contextvars import ContextVar

from fastapi import Request

GOOGLE_PATH = "/api/accounts/google/callback"
MICROSOFT_PATH = "/api/accounts/microsoft/callback"
LOCAL_PREFIXES = ("http://localhost", "http://127.0.0.1")

_origin: ContextVar[str | None] = ContextVar("oauth_origin", default=None)


def request_origin(request: Request) -> str:
    """https://host of the page the visitor is on (behind a proxy the forwarded headers say which)."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host.split(',')[0].strip()}"


def remember_origin(request: Request) -> None:
    """Called at the start of an OAuth request so the provider modules can build the redirect address."""
    _origin.set(request_origin(request))


def resolve(configured: str, path: str, origin: str | None = None) -> str:
    """The redirect URI to use: the configured one when it is usable, else this visitor's own origin + path."""
    origin = origin or _origin.get()
    placeholder = not configured or "<" in configured or ">" in configured
    local_default = configured.startswith(LOCAL_PREFIXES) and origin and not origin.startswith(LOCAL_PREFIXES)
    if (placeholder or local_default) and origin:
        return origin.rstrip("/") + path
    return configured
