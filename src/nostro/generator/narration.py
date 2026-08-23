"""Bank narration rendering and corruption.

Real Indian bank statements carry the UTR inside a free-text narration whose
shape differs per bank and gets truncated by legacy core-banking systems. The
regex ladder in Task 6 is written against exactly these shapes; the corruption
here is what forces the LLM fallback path to exist at all.
"""
from __future__ import annotations

import random
from enum import Enum


class Bank(str, Enum):
    HDFC = "hdfc"
    ICICI = "icici"
    AXIS = "axis"
    SBI = "sbi"


_TEMPLATES: dict[Bank, str] = {
    Bank.HDFC: "NEFT CR-RAZORPAY SOFTWARE-{utr}-RZPY SETTLEMENT",
    Bank.ICICI: "MMT/IMPS/{utr}/RAZORPAY/SETTLEMENT/{rrn}",
    Bank.AXIS: "NEFT/{utr}/RAZORPAYSOFTWAREPVTLTD/SETTLE",
    Bank.SBI: "TRANSFER FROM RAZORPAY REF {utr} CR",
}


def render_narration(bank: Bank, utr: str, rrn: str | None, rng: random.Random) -> str:
    return _TEMPLATES[bank].format(utr=utr, rrn=rrn or f"RRN{rng.randrange(10**6):06d}")


def corrupt_narration(text: str, rng: random.Random) -> str:
    """Apply one realistic corruption. Never returns the input unchanged."""
    modes = ("truncate", "collapse_space", "case_flip", "typo")
    mode = rng.choice(modes)
    if mode == "truncate" and len(text) > 12:
        return text[: max(12, len(text) - rng.randrange(3, 10))]
    if mode == "collapse_space":
        collapsed = text.replace(" ", "")
        return collapsed if collapsed != text else text + "  "
    if mode == "case_flip":
        flipped = text.lower()
        return flipped if flipped != text else text.upper()
    idx = rng.randrange(len(text))
    replacement = "0" if text[idx] not in "0" else "O"
    return text[:idx] + replacement + text[idx + 1 :]
