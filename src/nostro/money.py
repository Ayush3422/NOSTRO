"""Money handling. Every rupee value in Nostro enters through this module.

Amounts are integer paise everywhere else. Float is never used for money:
paise-level rounding drift is one of the failure modes we are built to detect,
so we must not introduce our own.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

__all__ = ["rupees_to_paise", "paise_to_rupees", "MoneyParseError"]

_STRIP = re.compile(r"[₹,\s]|(?i:rs\.?)")


class MoneyParseError(ValueError):
    """Raised when a value cannot be read as an exact rupee amount."""


def rupees_to_paise(value: str | Decimal) -> int:
    """Convert a rupee amount to integer paise. Never rounds."""
    if isinstance(value, Decimal):
        dec = value
    else:
        cleaned = _STRIP.sub("", str(value))
        if not cleaned:
            raise MoneyParseError(f"empty amount: {value!r}")
        try:
            dec = Decimal(cleaned)
        except InvalidOperation as exc:
            raise MoneyParseError(f"not a rupee amount: {value!r}") from exc

    shifted = dec * 100
    if shifted != shifted.to_integral_value():
        raise MoneyParseError(f"sub-paise precision, refusing to round: {value!r}")
    return int(shifted)


def paise_to_rupees(paise: int) -> str:
    """Render integer paise as a plain rupee string with exactly two decimals."""
    if not isinstance(paise, int) or isinstance(paise, bool):
        raise MoneyParseError(f"paise must be int, got {type(paise).__name__}")
    sign = "-" if paise < 0 else ""
    whole, frac = divmod(abs(paise), 100)
    return f"{sign}{whole}.{frac:02d}"
