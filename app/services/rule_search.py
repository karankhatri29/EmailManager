"""The 'search' rule: a small query language for "mail that contains ...".

    invoice overdue          both words anywhere (implicit AND)
    invoice OR receipt       either word          (also: invoice | receipt)
    "payment due"            an exact phrase
    invoice -newsletter      contains invoice, does not contain newsletter (also: NOT newsletter)
    (invoice OR receipt) AND paid
    subject:urgent           only look in the subject   (also from:, body:)
    =pay                     whole word only: matches "pay" but not "payment"
    pay*ment                 wildcard inside a word

Plain terms are substring matches ("invoice" also finds "invoices" and "invoicing"), and everything is compared
case-, accent- and punctuation-insensitively ("Café-Menu" finds "cafe menu"). Operators must be written in capitals
so the ordinary words "or", "and" and "not" still work as search words.
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

MIN_TERM_LENGTH = 3
MAX_TERMS = 20
MAX_DEPTH = 6
FIELDS = ("subject", "from", "body")

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<lp>\()
      | (?P<rp>\))
      | (?P<or>\|)
      | (?P<and>&)
      | (?P<neg>-(?=[("\w=*]))
      | (?P<term>(?:(?:subject|from|body):)?(?:"[^"]*"|[^\s()"|&]+))
    )""",
    re.VERBOSE | re.IGNORECASE,
)


class SearchError(ValueError):
    """The search the user typed cannot be understood."""


def fold(text: str) -> str:
    """Lower-cases, drops accents and turns every run of punctuation/space into one space."""
    text = unicodedata.normalize("NFKD", (text or "").casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"\W+", " ", text).split())


@dataclass(frozen=True)
class _Term:
    field: str | None
    needle: str
    regex: re.Pattern | None  # set for whole-word and wildcard terms, else a plain substring test


@dataclass(frozen=True)
class _Not:
    node: object


@dataclass(frozen=True)
class _And:
    nodes: tuple


@dataclass(frozen=True)
class _Or:
    nodes: tuple


class Document:
    """One email folded once, so many rules can be tried against it cheaply."""

    def __init__(self, address: str, subject: str, body: str):
        self.subject = fold(subject)
        self.body = fold(body)
        self.parts = {
            None: f"{self.subject} {self.body}",
            "subject": self.subject,
            "body": self.body,
            "from": fold(address),
        }


def _tokens(query: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    while pos < len(query):
        m = _TOKEN.match(query, pos)
        if m is None or m.end() == pos:
            if query[pos:].strip() == "":
                break
            raise SearchError('Check your quotes and brackets: a " or ( was never closed.')
        pos = m.end()
        kind = m.lastgroup or "term"
        text = m.group(kind)
        if kind == "term":
            if text == "OR":
                kind = "or"
            elif text == "AND":
                kind = "and"
            elif text == "NOT":
                kind = "neg"
        out.append((kind, text))
    return out


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0
        self.terms = 0

    def peek(self):
        return self.tokens[self.i][0] if self.i < len(self.tokens) else None

    def take(self):
        token = self.tokens[self.i]
        self.i += 1
        return token

    def parse(self):
        if not self.tokens:
            raise SearchError("Type something to search for.")
        node = self.or_expr(0, False)
        if self.i != len(self.tokens):
            raise SearchError("There is a stray ) in your search.")
        return node

    def or_expr(self, depth, negated):
        nodes = [self.and_expr(depth, negated)]
        while self.peek() == "or":
            self.take()
            nodes.append(self.and_expr(depth, negated))
        return nodes[0] if len(nodes) == 1 else _Or(tuple(nodes))

    def and_expr(self, depth, negated):
        nodes = [self.unary(depth, negated)]
        while self.peek() in ("and", "neg", "term", "lp"):
            if self.peek() == "and":
                self.take()
            nodes.append(self.unary(depth, negated))
        return nodes[0] if len(nodes) == 1 else _And(tuple(nodes))

    def unary(self, depth, negated):
        kind = self.peek()
        if kind == "neg":
            self.take()
            return _Not(self.unary(depth, not negated))
        if kind == "lp":
            if depth >= MAX_DEPTH:
                raise SearchError("Too many nested brackets.")
            self.take()
            node = self.or_expr(depth + 1, negated)
            if self.peek() != "rp":
                raise SearchError("A ( was never closed.")
            self.take()
            return node
        if kind == "term":
            return self.term(negated)
        raise SearchError("An operator (OR, AND, -) is missing a word next to it.")

    def term(self, negated):
        self.terms += 1
        if self.terms > MAX_TERMS:
            raise SearchError(f"Use at most {MAX_TERMS} words or phrases per search.")
        raw = self.take()[1]
        field = None
        head, _, rest = raw.partition(":")
        if rest and head.lower() in FIELDS:
            field, raw = head.lower(), rest
        quoted = raw.startswith('"')
        whole = raw.startswith("=") and not quoted
        raw = raw.strip('"') if quoted else raw.lstrip("=")

        parts = [fold(p) for p in raw.split("*")] if not quoted else [fold(raw)]
        needle = " ".join(p for p in parts if p)
        minimum = 1 if negated else MIN_TERM_LENGTH
        if len(needle) < minimum:
            raise SearchError(f"“{raw}” is too short: use at least {MIN_TERM_LENGTH} characters.")

        regex = None
        if whole or (len(parts) > 1 and any(parts)):
            body = r"\w*".join(re.escape(p) for p in parts)
            regex = re.compile(rf"\b{body}\b" if whole else body)
        return _Term(field, parts[0] if len(parts) == 1 else needle, regex)


def _has_positive(node) -> bool:
    if isinstance(node, _Term):
        return True
    if isinstance(node, _Not):
        return False
    if isinstance(node, _And):
        return any(_has_positive(n) for n in node.nodes)
    return all(_has_positive(n) for n in node.nodes)


@lru_cache(maxsize=512)
def compile_query(query: str):
    """Parses a search into a tree, or raises SearchError explaining what is wrong."""
    node = _Parser(_tokens(query)).parse()
    if not _has_positive(node):
        raise SearchError(
            "Say what the mail must contain; a search of only -words matches nearly everything."
        )
    return node


def _eval(node, doc: Document) -> bool:
    if isinstance(node, _Term):
        text = doc.parts[node.field]
        return node.regex.search(text) is not None if node.regex else node.needle in text
    if isinstance(node, _Not):
        return not _eval(node.node, doc)
    if isinstance(node, _And):
        return all(_eval(n, doc) for n in node.nodes)
    return any(_eval(n, doc) for n in node.nodes)


def search_matches(query: str, doc: Document) -> bool:
    try:
        node = compile_query(query)
    except SearchError:
        return False
    return _eval(node, doc)
