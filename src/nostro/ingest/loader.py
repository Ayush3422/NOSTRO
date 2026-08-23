"""CSV loading with row-level quarantine.

File-level problems raise. Row-level problems quarantine. That split is
deliberate: a merchant whose bank sent one malformed line still deserves a
close over the other 4,999 rows, with the one exception reported honestly.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from nostro.ingest.contracts import BUILDERS, REQUIRED_HEADERS
from nostro.models import Source


class IngestError(RuntimeError):
    """File cannot be read at all, or is missing a required column."""


class QuarantinedRow(BaseModel):
    source: Source
    line_no: int
    raw: dict[str, str]
    reason: str


@dataclass
class LoadResult:
    """A dataclass, not a pydantic model, on purpose: pydantic v2 would validate
    `list[BaseModel]` by coercing each row to a bare BaseModel and silently
    dropping every subclass field. Rows arrive here already validated by their
    own contract, so no second validation pass is wanted."""

    rows: list[BaseModel]
    quarantined: list[QuarantinedRow]

    @property
    def quarantine_rate(self) -> float:
        total = len(self.rows) + len(self.quarantined)
        return len(self.quarantined) / total if total else 0.0


def load_csv(path: Path, source: Source) -> LoadResult:
    path = Path(path)
    if not path.exists():
        raise IngestError(f"missing source file: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise IngestError(f"unreadable source file {path}: {exc}") from exc

    reader = csv.DictReader(text.splitlines())
    header = set(reader.fieldnames or [])
    missing = REQUIRED_HEADERS[source.value] - header
    if missing:
        raise IngestError(
            f"{path.name} is missing required columns: {sorted(missing)}"
        )

    build = BUILDERS[source.value]
    rows: list[BaseModel] = []
    quarantined: list[QuarantinedRow] = []

    for line_no, raw in enumerate(reader, start=2):
        clean = {k: (v if v is not None else "") for k, v in raw.items() if k}
        try:
            rows.append(build(clean))
        except Exception as exc:
            quarantined.append(QuarantinedRow(
                source=source, line_no=line_no, raw=clean, reason=str(exc)[:300],
            ))

    return LoadResult(rows=rows, quarantined=quarantined)
