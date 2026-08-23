from datetime import date

from nostro.exceptions.taxonomy import ExceptionClass, build_exceptions, classify_deterministic
from nostro.models import CanonicalRow, Direction, Match, MatchMethod, ParsedBy, Source
from nostro.normalize.canonical import CanonicalSet


def _row(rid, src, amount=1000, day=3, refs=None, parsed_by=ParsedBy.REGEX, narration=None):
    return CanonicalRow(source=src, row_id=rid, amount_paise=amount,
                        direction=Direction.CREDIT, value_date=date(2026, 6, day),
                        refs=refs or {}, parsed_by=parsed_by, narration_raw=narration)


def test_unparseable_narration_is_classified():
    row = _row("bk_1", Source.BANK, parsed_by=ParsedBy.NONE, narration="!!! junk !!!")
    item = classify_deterministic(row, CanonicalSet(bank=[row]), {})
    assert item.exception_class is ExceptionClass.UNPARSEABLE_NARRATION


def test_duplicate_utr_is_classified():
    a = _row("bk_1", Source.BANK, refs={"utr": "UTR1"}, narration="x")
    b = _row("bk_2", Source.BANK, refs={"utr": "UTR1"}, narration="x")
    item = classify_deterministic(a, CanonicalSet(bank=[a, b]), {})
    assert item.exception_class is ExceptionClass.DUPLICATE_UTR


def test_orphan_chargeback_is_classified():
    row = _row("bk_1", Source.BANK, refs={"kind": "chargeback"}, narration="CHARGEBACK DR")
    item = classify_deterministic(row, CanonicalSet(bank=[row]), {})
    assert item.exception_class is ExceptionClass.CHARGEBACK_WITHOUT_FORWARD


def test_a_lone_row_with_no_counterparty_is_classified():
    row = _row("pay_1", Source.RAZORPAY)
    item = classify_deterministic(row, CanonicalSet(razorpay=[row]), {})
    assert item.exception_class is ExceptionClass.MISSING_COUNTERPARTY
    assert item.amount_paise == 1000


def test_build_exceptions_covers_every_unmatched_row():
    rows = [_row("pay_1", Source.RAZORPAY), _row("pay_2", Source.RAZORPAY),
            _row("bk_1", Source.BANK)]
    cset = CanonicalSet(razorpay=rows[:2], bank=rows[2:])
    matches = [Match(match_id="m", razorpay_ids=("pay_1",), bank_ids=("bk_1",),
                     score=1.0, method=MatchMethod.EXACT)]
    items = build_exceptions(cset, matches, quarantined=[], consumed={"pay_1", "bk_1"})
    assert {i.row_ids[0] for i in items} == {"pay_2"}


def test_quarantined_rows_appear_as_exceptions_too():
    from nostro.ingest.loader import QuarantinedRow
    q = QuarantinedRow(source=Source.BANK, line_no=7, raw={"credit": "N/A"},
                       reason="not a rupee amount")
    items = build_exceptions(CanonicalSet(), [], quarantined=[q], consumed=set())
    assert len(items) == 1
    assert items[0].exception_class is ExceptionClass.QUARANTINED_ROW


def test_nothing_is_silently_dropped():
    rows = [_row(f"pay_{i}", Source.RAZORPAY) for i in range(10)]
    cset = CanonicalSet(razorpay=rows)
    items = build_exceptions(cset, [], quarantined=[], consumed=set())
    assert len(items) == 10
