"""User-defined classification rules: 'mail from this sender / domain / containing this word gets this category'."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .nlp_engine import CATEGORIES
from .senders import sender_domain

KINDS = ("sender", "domain", "keyword")
MIN_KEYWORD_LENGTH = 3


@dataclass(frozen=True)
class RuleSpec:
    """A rule detached from the database, cheap to pass around while processing mail."""

    kind: str
    pattern: str
    category: str


class RuleError(ValueError):
    """The rule the user asked for is not valid."""


def normalize(kind: str, pattern: str, category: str) -> RuleSpec:
    """Validates and canonicalises a rule (lower-case, no stray '@' or spaces)."""
    if kind not in KINDS:
        raise RuleError(f"Rule type must be one of: {', '.join(KINDS)}")
    if category not in CATEGORIES:
        raise RuleError(f"Category must be one of: {', '.join(CATEGORIES)}")

    pattern = " ".join((pattern or "").lower().split())
    if kind == "sender":
        if pattern.count("@") != 1 or pattern.startswith("@") or pattern.endswith("@") or " " in pattern:
            raise RuleError("Enter a full sender address such as news@shop.com")
    elif kind == "domain":
        pattern = pattern.removeprefix("@")
        if "." not in pattern or "@" in pattern or " " in pattern:
            raise RuleError("Enter a domain such as shop.com")
    elif len(pattern) < MIN_KEYWORD_LENGTH:
        raise RuleError(f"Keywords need at least {MIN_KEYWORD_LENGTH} characters")
    return RuleSpec(kind, pattern, category)


def to_specs(rules: Iterable) -> list[RuleSpec]:
    return [RuleSpec(r.kind, r.pattern, r.category) for r in rules]


def _domain_matches(address_domain: str, pattern: str) -> bool:
    """shop.com matches shop.com and mail.shop.com, but not notshop.com."""
    return address_domain == pattern or address_domain.endswith("." + pattern)


def matches(spec: RuleSpec, address: str, subject: str, body: str) -> bool:
    if spec.kind == "sender":
        return address == spec.pattern
    if spec.kind == "domain":
        return _domain_matches(sender_domain(address), spec.pattern)
    text = f"{subject} {body}".lower()
    return re.search(rf"\b{re.escape(spec.pattern)}\b", text) is not None


def match(specs: Iterable[RuleSpec], address: str, subject: str, body: str) -> RuleSpec | None:
    """The most specific matching rule: a sender beats a domain, which beats a keyword."""
    specs = list(specs)
    for kind in KINDS:
        for spec in specs:
            if spec.kind == kind and matches(spec, address, subject, body):
                return spec
    return None


def describe(spec: RuleSpec) -> str:
    """Plain-English reason shown next to an email a rule decided."""
    if spec.kind == "sender":
        target = f"mail from {spec.pattern}"
    elif spec.kind == "domain":
        target = f"mail from {spec.pattern}"
    else:
        target = f"mail mentioning “{spec.pattern}”"
    return f"Your rule: {target} is always {spec.category}."
