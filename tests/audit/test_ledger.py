import json
from pathlib import Path

from nostro.audit.ledger import GENESIS_HASH, Ledger


def _ledger(tmp_path: Path) -> Ledger:
    ticks = iter([f"2026-08-23T10:00:{i:02d}Z" for i in range(60)])
    return Ledger(tmp_path / "audit.jsonl", clock=lambda: next(ticks))


def test_first_entry_chains_from_genesis(tmp_path: Path):
    led = _ledger(tmp_path)
    entry = led.append("close_started", {"dataset": "full"})
    assert entry.seq == 0
    assert entry.prev_hash == GENESIS_HASH
    assert len(entry.entry_hash) == 64


def test_each_entry_chains_to_the_previous(tmp_path: Path):
    led = _ledger(tmp_path)
    first = led.append("a", {})
    second = led.append("b", {})
    assert second.prev_hash == first.entry_hash
    assert second.seq == 1


def test_verify_passes_on_an_untouched_ledger(tmp_path: Path):
    led = _ledger(tmp_path)
    for i in range(5):
        led.append("match_posted", {"i": i})
    ok, bad = led.verify()
    assert ok is True
    assert bad is None


def test_tampering_with_a_payload_is_detected(tmp_path: Path):
    led = _ledger(tmp_path)
    for i in range(5):
        led.append("match_posted", {"amount_paise": 100 * i})
    path = tmp_path / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    doctored = json.loads(lines[2])
    doctored["payload"]["amount_paise"] = 999999
    lines[2] = json.dumps(doctored)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, bad = Ledger(path).verify()
    assert ok is False
    assert bad == 2


def test_deleting_an_entry_is_detected(tmp_path: Path):
    led = _ledger(tmp_path)
    for i in range(4):
        led.append("x", {"i": i})
    path = tmp_path / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, _bad = Ledger(path).verify()
    assert ok is False


def test_result_hash_is_stable_for_identical_runs(tmp_path: Path):
    a, b = _ledger(tmp_path / "a"), _ledger(tmp_path / "b")
    for led in (a, b):
        led.append("close_started", {"dataset": "full"})
        led.append("match_posted", {"match_id": "m1"})
    assert a.result_hash() == b.result_hash()


def test_result_hash_changes_when_anything_changes(tmp_path: Path):
    a, b = _ledger(tmp_path / "a"), _ledger(tmp_path / "b")
    a.append("match_posted", {"match_id": "m1"})
    b.append("match_posted", {"match_id": "m2"})
    assert a.result_hash() != b.result_hash()


def test_entries_reload_from_disk(tmp_path: Path):
    led = _ledger(tmp_path)
    led.append("one", {"v": 1})
    led.append("two", {"v": 2})
    reloaded = Ledger(tmp_path / "audit.jsonl").entries()
    assert [e.kind for e in reloaded] == ["one", "two"]


def test_tail_truncation_is_NOT_detected_unsigned_chain_limitation(tmp_path: Path):
    # This pins a known limitation, not a bug. An unsigned hash chain cannot
    # tell "the last N entries were erased" apart from "the ledger was always
    # this short and honestly stopped here" -- removing the tail leaves every
    # surviving entry's seq equal to its file position and its prev_hash chain
    # intact, so verify() has nothing to flag. The same blind spot means a
    # forged append with a correctly computed hash is also accepted. Closing
    # this gap needs signing or an external checkpoint outside the file itself,
    # which is explicitly out of scope for this module (see verify()'s
    # docstring). The test exists so a future change that silently "fixes"
    # this without adding signing does not get treated as a regression, and so
    # the boundary is documented rather than discovered by an attacker.
    led = _ledger(tmp_path)
    for i in range(5):
        led.append("match_posted", {"i": i})
    path = tmp_path / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    truncated = lines[:2]  # drop the last three entries (seq 2, 3, 4)
    path.write_text("\n".join(truncated) + "\n", encoding="utf-8")

    ok, bad = Ledger(path).verify()
    assert ok is True
    assert bad is None
