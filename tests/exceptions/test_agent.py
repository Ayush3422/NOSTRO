from nostro.exceptions.agent import ExceptionDesk
from nostro.exceptions.taxonomy import ExceptionClass, ExceptionItem, ResolutionKind
from nostro.normalize.canonical import CanonicalSet


def _item(cls=ExceptionClass.MISSING_COUNTERPARTY) -> ExceptionItem:
    return ExceptionItem(exception_id="exc_1", row_ids=("pay_1",),
                         exception_class=cls, amount_paise=50000, evidence="none")


class _StubResponse:
    def __init__(self, parsed):
        self.parsed_output = parsed


class _StubClient:
    """Mimics the one SDK call the desk makes: client.messages.parse(...)."""

    def __init__(self, parsed, explode=False):
        self._parsed, self._explode = parsed, explode
        self.calls = 0

        class _Messages:
            def parse(inner, **kwargs):
                self.calls += 1
                if self._explode:
                    raise TimeoutError("model unavailable")
                return _StubResponse(self._parsed)

        self.messages = _Messages()


def test_desk_returns_the_models_proposal():
    from nostro.exceptions.agent import _ResolutionDraft
    stub = _StubClient(_ResolutionDraft(kind=ResolutionKind.CHASE_COUNTERPARTY,
                                        rationale="ask the bank for the missing credit",
                                        confidence=0.8))
    out = ExceptionDesk(client=stub).propose(_item(), CanonicalSet())
    assert out.kind is ResolutionKind.CHASE_COUNTERPARTY
    assert out.exception_id == "exc_1"
    assert stub.calls == 1


def test_every_proposal_requires_a_human_regardless_of_confidence():
    from nostro.exceptions.agent import _ResolutionDraft
    stub = _StubClient(_ResolutionDraft(kind=ResolutionKind.WRITE_OFF,
                                        rationale="just write it off", confidence=1.0))
    out = ExceptionDesk(client=stub).propose(_item(), CanonicalSet())
    assert out.requires_human is True


def test_a_model_outage_degrades_to_needs_human():
    stub = _StubClient(None, explode=True)
    out = ExceptionDesk(client=stub).propose(_item(), CanonicalSet())
    assert out.kind is ResolutionKind.NEEDS_HUMAN
    assert out.confidence == 0.0
    assert "unavailable" in out.rationale.lower()


def test_no_client_at_all_degrades_rather_than_raising():
    out = ExceptionDesk(client=None).propose(_item(), CanonicalSet())
    assert out.kind is ResolutionKind.NEEDS_HUMAN


def test_a_response_with_no_parsed_output_degrades_to_needs_human():
    """parsed_output is None (no exception) on truncation at max_tokens, a
    refusal stop_reason, or schema-invalid content per the anthropic SDK's
    ParsedMessage.parsed_output property. propose() must not touch attributes
    on a None draft outside its degradation path."""
    stub = _StubClient(None)
    out = ExceptionDesk(client=stub).propose(_item(), CanonicalSet())
    assert out.kind is ResolutionKind.NEEDS_HUMAN
    assert out.confidence == 0.0
    assert out.requires_human is True


def test_quarantined_rows_skip_the_model_entirely():
    stub = _StubClient(None)
    out = ExceptionDesk(client=stub).propose(
        _item(ExceptionClass.QUARANTINED_ROW), CanonicalSet())
    assert out.kind is ResolutionKind.NEEDS_HUMAN
    assert stub.calls == 0        # no point asking a model about a malformed CSV line
