"""
VIP AI Platform — PII Redactor (Sprint 6)
Masks personally identifiable information in meeting transcripts before
they're surfaced to anyone outside the worker + boss inner circle, and
before any storage that might be exported (DOCX, JSON, signed URLs).

Korean-aware: covers RRN (주민등록번호), Korean phone formats, common
account number patterns, plus standard email/credit-card/IP patterns.

The function is non-destructive — original `text` stays in the DB row,
but a `redacted_text` column can be added in a future migration if needed.
Right now we expose a function the dashboard + DOCX export call before
displaying.
"""

from __future__ import annotations

import re
from typing import Iterable


# ---------------------------------------------------------------------------
#  Patterns (compiled once)
# ---------------------------------------------------------------------------

# Email — RFC 5322 simplified
_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Korean phone: 010-1234-5678, 010 1234 5678, +82-10-1234-5678, 02-123-4567
_KR_PHONE = re.compile(
    r"(?:\+?82[-\s]?)?(?:0\d{1,2})[-\s]?\d{3,4}[-\s]?\d{4}"
)

# International phone with leading + and 8-15 digits
_INTL_PHONE = re.compile(r"\+\d{1,3}[-\s]?\d{2,4}[-\s]?\d{3,4}[-\s]?\d{3,4}")

# Korean RRN (주민등록번호): 6 digits - 7 digits = 900101-1234567
_KR_RRN = re.compile(r"\b\d{6}[-\s]?\d{7}\b")

# Credit card — 13 to 19 digits, optionally separated by spaces/dashes
_CREDIT_CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

# Bank account hints — "계좌 1234-1234-12345" or "account 12-345-67890"
_BANK_ACCOUNT_KR = re.compile(r"(?:계좌|계좌번호)[^\n\d]{0,8}(\d{2,4}[-\s]\d{2,6}[-\s]\d{2,8}(?:[-\s]\d{1,6})?)")

# IPv4 + IPv6
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b")

# US SSN-style 3-2-4
_US_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _is_likely_card(token: str) -> bool:
    """Cheap Luhn-ish check to avoid masking every 13-digit run."""
    digits = re.sub(r"\D", "", token)
    if len(digits) < 13 or len(digits) > 19:
        return False
    # Luhn
    s = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        s += n
    return s % 10 == 0


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def redact(text: str, mask: str = "[REDACTED:{kind}]") -> tuple[str, dict[str, int]]:
    """Return (redacted_text, counts) where counts is a per-kind dict
    of how many matches were masked. Pass an empty mask "" to strip
    matches entirely instead of substituting a token.
    """
    if not text:
        return text, {}
    counts: dict[str, int] = {}
    out = text

    def _apply(pattern: re.Pattern, kind: str, predicate=None) -> None:
        nonlocal out
        hits = 0

        def _sub(m: re.Match) -> str:
            nonlocal hits
            if predicate and not predicate(m.group(0)):
                return m.group(0)
            hits += 1
            return mask.format(kind=kind)

        out = pattern.sub(_sub, out)
        if hits:
            counts[kind] = counts.get(kind, 0) + hits

    # Order matters: redact the most specific first so a credit-card-like
    # phone doesn't get double-tagged.
    _apply(_KR_RRN, "kr_rrn")
    _apply(_US_SSN, "us_ssn")
    _apply(_CREDIT_CARD, "credit_card", predicate=_is_likely_card)
    _apply(_BANK_ACCOUNT_KR, "kr_bank_account")
    _apply(_INTL_PHONE, "phone")
    _apply(_KR_PHONE, "phone")
    _apply(_EMAIL, "email")
    _apply(_IPV4, "ip")
    _apply(_IPV6, "ip")
    return out, counts


def redact_utterances(rows: Iterable[dict]) -> list[dict]:
    """Walk a list of utterance dicts (as the GET endpoint returns) and
    return a copy with `text` and `text_korean` redacted. Original rows
    not mutated.
    """
    out = []
    for r in rows:
        copy = dict(r)
        if copy.get("text"):
            copy["text"], copy["_redaction_counts"] = redact(copy["text"])
        if copy.get("text_korean"):
            copy["text_korean"], _ = redact(copy["text_korean"])
        out.append(copy)
    return out
