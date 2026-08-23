from nostro.models import ParsedBy
from nostro.normalize.narration_parser import NarrationParser, ParsedNarration


def test_hdfc_settlement_narration_parsed_by_regex():
    p = NarrationParser()
    out = p.parse("NEFT CR-RAZORPAY SOFTWARE-UTR2608260001-RZPY SETTLEMENT")
    assert out.utr == "UTR2608260001"
    assert out.kind == "settlement"
    assert out.parsed_by is ParsedBy.REGEX


def test_icici_narration_yields_utr_and_rrn():
    p = NarrationParser()
    out = p.parse("MMT/IMPS/UTR1234567890/RAZORPAY/SETTLEMENT/RRN778812")
    assert out.utr == "UTR1234567890"
    assert out.rrn == "RRN778812"


def test_refund_and_chargeback_kinds():
    p = NarrationParser()
    assert p.parse("NEFT DR-RAZORPAY REFUND rfnd_0000123").kind == "refund"
    assert p.parse("CHARGEBACK DR RAZORPAY cb_0000456").kind == "chargeback"


def test_llm_fallback_is_only_called_when_regex_misses():
    calls: list[str] = []

    def fake_llm(text: str) -> ParsedNarration:
        calls.append(text)
        return ParsedNarration(utr="UTR999", rrn=None, kind="settlement",
                               parsed_by=ParsedBy.LLM, confidence=0.6)

    p = NarrationParser(llm_fallback=fake_llm)
    p.parse("NEFT CR-RAZORPAY SOFTWARE-UTR2608260001-RZPY SETTLEMENT")
    assert calls == []

    out = p.parse("!!! unreadable garbage !!!")
    assert calls == ["!!! unreadable garbage !!!"]
    assert out.parsed_by is ParsedBy.LLM


def test_without_a_fallback_a_miss_degrades_it_does_not_raise():
    p = NarrationParser()
    out = p.parse("!!! unreadable garbage !!!")
    assert out.utr is None
    assert out.parsed_by is ParsedBy.NONE
    assert out.kind == "unknown"


def test_failing_llm_fallback_degrades_gracefully():
    def broken_llm(text: str):
        raise TimeoutError("model unavailable")

    p = NarrationParser(llm_fallback=broken_llm)
    out = p.parse("!!! unreadable garbage !!!")
    assert out.parsed_by is ParsedBy.NONE
    assert out.utr is None


def test_squeezed_retry_recovers_utr_when_strict_match_fails():
    # A space-collapsed SBI narration: "REF" glues onto "UTR..." with no word
    # boundary between them, and "...0001" glues onto the trailing "CR", so
    # the strict \b-anchored pattern cannot match at all. The squeezed retry
    # (digits-only body, no boundary requirement) must still recover the UTR.
    p = NarrationParser()
    out = p.parse("TRANSFERFROMRAZORPAYREFUTR2608260001CR")
    assert out.utr == "UTR2608260001"
    assert out.parsed_by is ParsedBy.REGEX


def test_stats_track_ladder_versus_model():
    p = NarrationParser()
    p.parse("NEFT/UTR1234567890/RAZORPAYSOFTWAREPVTLTD/SETTLE")
    p.parse("garbage")
    assert p.stats["regex_hits"] == 1
    assert p.stats["misses"] == 1
