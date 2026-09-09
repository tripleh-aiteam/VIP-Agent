# -*- coding: utf-8 -*-
"""ime_layout — undo "Korean keyboard was still on" typing.

Boss 2026-09-09 typed `ㅗㅑㅐ ㅊ무 ㅛㅐㅑㅕ ㅗ디ㅔ ㅡㄷ` and the desk answered
"이해하지 못했습니다". That string is not Korean at all — it is "hio can yoiu
help me" pressed through the Dubeolsik (2-set) IME. Every Korean user does this
several times a day, and the fix is deterministic: map each jamo back to the
QWERTY key that produced it.

Deliberately conservative — a false rewrite of real Korean would be far worse
than leaving a typo alone, so a message is only rewritten when it is BOTH
jamo-heavy (real Korean writes composed syllables, not bare ㅗㅑㅐ) AND the
result lands on recognisable English words.
"""
from __future__ import annotations

import re

# Dubeolsik: jamo → the QWERTY key that types it
_J2Q = {
    # consonants
    "ㅂ": "q", "ㅈ": "w", "ㄷ": "e", "ㄱ": "r", "ㅅ": "t",
    "ㅁ": "a", "ㄴ": "s", "ㅇ": "d", "ㄹ": "f", "ㅎ": "g",
    "ㅋ": "z", "ㅌ": "x", "ㅊ": "c", "ㅍ": "v",
    "ㅃ": "Q", "ㅉ": "W", "ㄸ": "E", "ㄲ": "R", "ㅆ": "T",
    # vowels
    "ㅛ": "y", "ㅕ": "u", "ㅑ": "i", "ㅐ": "o", "ㅔ": "p",
    "ㅗ": "h", "ㅓ": "j", "ㅏ": "k", "ㅣ": "l",
    "ㅠ": "b", "ㅜ": "n", "ㅡ": "m",
    "ㅒ": "O", "ㅖ": "P",
    # compound vowels / finals are two keystrokes
    "ㅘ": "hk", "ㅙ": "ho", "ㅚ": "hl", "ㅝ": "nj", "ㅞ": "np",
    "ㅟ": "nl", "ㅢ": "ml",
    "ㄳ": "rt", "ㄵ": "sw", "ㄶ": "sg", "ㄺ": "fr", "ㄻ": "fa",
    "ㄼ": "fq", "ㄽ": "ft", "ㄾ": "fx", "ㄿ": "fv", "ㅀ": "fg", "ㅄ": "qt",
}

_L = ["ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
      "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
_V = ["ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ", "ㅙ",
      "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ"]
_T = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ",
      "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ",
      "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]

# words that prove the conversion actually landed on English
_EN_HINTS = frozenset("""
hi hey hello can you your help me my we us please thanks thank ok okay
what which when where why who how is are do does did the a an and or of to
buy sell price stock stocks share shares order orders market close open
now today tomorrow yesterday news report show tell give find check
samsung hynix naver kakao money profit loss chart high low want need
""".split())

_JAMO_RE = re.compile(r"[ㄱ-ㅣ]")          # bare compatibility jamo
_SYL_RE = re.compile(r"[가-힣]")           # composed Hangul syllables
_EMOTICON_ONLY = re.compile(r"^[ㄱ-ㅣ\s.!?~ㅋㅎㅠㅜㅡ]*$")


def _syllable_to_jamo(ch: str) -> str:
    """Decompose one composed syllable into its L/V/T jamo."""
    i = ord(ch) - 0xAC00
    if not (0 <= i < 11172):
        return ch
    return _L[i // 588] + _V[(i % 588) // 28] + _T[i % 28]


def to_qwerty(text: str) -> str:
    """Map Hangul (composed or bare jamo) back to the keys that produced it."""
    out = []
    for ch in text or "":
        if _SYL_RE.match(ch):
            ch = _syllable_to_jamo(ch)
        for j in ch:
            out.append(_J2Q.get(j, j))
    return "".join(out)


def looks_mojibake(text: str) -> bool:
    """True when the message is almost certainly English typed with the Korean
    IME on. Requires bare jamo to dominate — real Korean composes syllables —
    and excludes pure emoticon strings (ㅋㅋㅋ, ㅠㅠ, ㅡㅡ)."""
    t = (text or "").strip()
    if len(t) < 4 or _EMOTICON_ONLY.match(t):
        return False
    body = re.sub(r"\s", "", t)
    if not body:
        return False
    jamo = len(_JAMO_RE.findall(body))
    if jamo / len(body) < 0.4:
        return False
    # …and the conversion has to actually mean something in English
    words = re.findall(r"[a-z]+", to_qwerty(t).lower())
    return sum(1 for w in words if w in _EN_HINTS) >= 2


def fix(text: str) -> str | None:
    """The rewritten English sentence, or None to leave the text untouched."""
    return to_qwerty(text) if looks_mojibake(text) else None
