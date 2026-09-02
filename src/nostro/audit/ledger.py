"""Append-only, hash-chained audit ledger.

Each entry commits to its predecessor, so any edit to history invalidates every
hash after it. This is what lets the dashboard claim a number is drillable: the
number, the rows behind it, and the decision that produced it are all in a log
whose integrity can be checked in one pass.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

GENESIS_HASH = "0" * 64


class LedgerEntry(BaseModel):
    seq: int
    ts: str
    kind: str
    payload: dict
    prev_hash: str
    entry_hash: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(seq: int, ts: str, kind: str, payload: dict, prev_hash: str) -> str:
    # sort_keys so the digest does not depend on dict insertion order.
    body = json.dumps(
        {"seq": seq, "ts": ts, "kind": kind, "payload": payload, "prev_hash": prev_hash},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: Path, clock: Callable[[], str] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utc_now

    def entries(self) -> list[LedgerEntry]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(LedgerEntry(**json.loads(line)))
            except (json.JSONDecodeError, ValueError, TypeError):
                # A corrupted line (hand-edited garbage, a truncated write) must
                # not crash the caller -- `verify()` needs to see this as "the
                # chain broke here" and report it, the same as a bad hash. A
                # sentinel with a seq that can never match its position (-1)
                # guarantees verify()'s seq/position check trips on this entry.
                out.append(LedgerEntry(
                    seq=-1, ts="", kind="__corrupt__", payload={},
                    prev_hash="", entry_hash="",
                ))
        return out

    def append(self, kind: str, payload: dict) -> LedgerEntry:
        existing = self.entries()
        seq = len(existing)
        prev_hash = existing[-1].entry_hash if existing else GENESIS_HASH
        ts = self._clock()
        entry = LedgerEntry(
            seq=seq, ts=ts, kind=kind, payload=payload, prev_hash=prev_hash,
            entry_hash=_digest(seq, ts, kind, payload, prev_hash),
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")
        return entry

    def verify(self) -> tuple[bool, int | None]:
        """(ok, seq of the first entry that fails).

        Detects edits, deletions, and reordering *within the recorded range*:
        any change to a surviving entry's fields, or to the order/position of
        surviving entries, breaks either the recomputed hash or the seq/position
        alignment.

        Does NOT detect tail truncation (removing the most recent N entries) or
        a forged append: a chain with the tail cut off is internally consistent
        and indistinguishable from a shorter, honest ledger, because the chain
        is unsigned and nothing outside the file attests to how long it used to
        be. Closing that gap needs signing or an external checkpoint, which is
        out of scope for this module.
        """
        prev_hash = GENESIS_HASH
        for position, entry in enumerate(self.entries()):
            expected = _digest(entry.seq, entry.ts, entry.kind, entry.payload,
                               entry.prev_hash)
            if entry.seq != position or entry.prev_hash != prev_hash or entry.entry_hash != expected:
                return False, position
            prev_hash = entry.entry_hash
        return True, None

    def result_hash(self) -> str:
        """Hash of the whole chain — two identical closes produce the same value.

        Timestamps are excluded so a replay on a different day still matches.
        """
        h = hashlib.sha256()
        for entry in self.entries():
            h.update(json.dumps({"kind": entry.kind, "payload": entry.payload},
                                sort_keys=True, separators=(",", ":")).encode("utf-8"))
        return h.hexdigest()
