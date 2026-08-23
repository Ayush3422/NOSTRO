"""The exception desk.

Scope, deliberately narrow: given an exception that deterministic rules already
classified, draft a proposed resolution for a human to approve. The desk cannot
post, cannot move money, and cannot change a classification. `requires_human` is
hard-coded True on every proposal — it is not the model's call to make.

Surface choice: this uses the Anthropic SDK's structured outputs
(`client.messages.parse(...)` with a pydantic `output_format`), not the Claude
Agent SDK. The Agent SDK is Claude Code packaged as a library — built-in
filesystem and bash tools, aimed at coding agents. Here we want a narrow,
propose-only surface over data we already hold: no filesystem, no shell, no
open-ended tool loop. Structured outputs rather than free text matter for the
same reason — a malformed answer is a validation error instead of something
that parses halfway and gets acted on.
"""
from __future__ import annotations

from pydantic import BaseModel

from nostro.audit.ledger import Ledger
from nostro.exceptions.taxonomy import (
    ExceptionClass, ExceptionItem, ProposedResolution, ResolutionKind,
)
from nostro.money import paise_to_rupees
from nostro.normalize.canonical import CanonicalSet

MODEL = "claude-opus-5"

_SYSTEM = """You are a finance-operations assistant on a payment reconciliation desk.

You are given one reconciliation exception that has ALREADY been classified by
deterministic rules. Your only job is to propose what a human analyst should do
about it, and explain why in one or two sentences.

Rules you must follow:
- Do not recompute, re-derive, or second-guess any amount. Arithmetic is not your job.
- Do not propose posting anything automatically. A human approves every action.
- If the evidence is insufficient, propose needs_human. That is a good answer.
"""

# Classes where a model adds nothing: the cause is already fully known.
_SKIP_MODEL = {ExceptionClass.QUARANTINED_ROW}


class _ResolutionDraft(BaseModel):
    """What we ask the model for. Note it cannot set requires_human."""

    kind: ResolutionKind
    rationale: str
    confidence: float


def _needs_human(item: ExceptionItem, why: str) -> ProposedResolution:
    return ProposedResolution(
        exception_id=item.exception_id, kind=ResolutionKind.NEEDS_HUMAN,
        rationale=why, confidence=0.0, requires_human=True,
    )


class ExceptionDesk:
    def __init__(
        self, client=None, ledger: Ledger | None = None, model: str = MODEL
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._model = model

    def _prompt(self, item: ExceptionItem, cset: CanonicalSet) -> str:
        rows = cset.by_id()
        lines = [
            f"Exception id: {item.exception_id}",
            f"Class: {item.exception_class.value}",
            f"Amount: Rs {paise_to_rupees(item.amount_paise)}",
            f"Evidence: {item.evidence}",
            "Rows involved:",
        ]
        for rid in item.row_ids:
            row = rows.get(rid)
            lines.append(
                f"  - {rid}: {row.source.value} {row.direction.value} "
                f"Rs {paise_to_rupees(row.amount_paise)} on {row.value_date}"
                if row else f"  - {rid}: (row not present in the canonical set)"
            )
        return "\n".join(lines)

    def propose(self, item: ExceptionItem, cset: CanonicalSet) -> ProposedResolution:
        if item.exception_class in _SKIP_MODEL:
            return _needs_human(item, "malformed source row; a human must repair the file")
        if self._client is None:
            return _needs_human(item, "no model client configured; running deterministic-only")

        try:
            response = self._client.messages.parse(
                model=self._model,
                max_tokens=2000,
                system=_SYSTEM,
                messages=[{"role": "user", "content": self._prompt(item, cset)}],
                output_format=_ResolutionDraft,
            )
            draft = response.parsed_output
        except Exception as exc:
            return _needs_human(item, f"model unavailable ({type(exc).__name__}); "
                                      f"routed to a human without a draft")

        resolution = ProposedResolution(
            exception_id=item.exception_id, kind=draft.kind,
            rationale=draft.rationale,
            confidence=max(0.0, min(1.0, draft.confidence)),
            requires_human=True,        # never the model's decision
        )
        if self._ledger is not None:
            self._ledger.append("resolution_proposed", resolution.model_dump(mode="json"))
        return resolution
