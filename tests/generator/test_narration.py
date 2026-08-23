import random
import pytest
from nostro.generator.config import GeneratorConfig
from nostro.generator.narration import Bank, corrupt_narration, render_narration


def test_every_bank_embeds_the_utr():
    rng = random.Random(0)
    for bank in Bank:
        text = render_narration(bank, "UTR2608260001", "RRN778812", rng)
        assert "UTR2608260001" in text


def test_banks_render_differently():
    rng = random.Random(0)
    shapes = {render_narration(b, "UTRX", None, random.Random(1)) for b in Bank}
    assert len(shapes) == len(Bank)


def test_corruption_changes_the_string_but_keeps_it_a_string():
    rng = random.Random(7)
    original = render_narration(Bank.HDFC, "UTR123456", "RRN99", rng)
    corrupted = corrupt_narration(original, random.Random(7))
    assert isinstance(corrupted, str)
    assert corrupted != original


def test_config_defaults_are_all_chaos_on():
    cfg = GeneratorConfig()
    assert cfg.split_settlement_rate > 0
    assert cfg.duplicate_utr_rate > 0
    assert cfg.narration_corruption_rate > 0
    assert cfg.seed == 20260823


def test_config_rejects_out_of_range_rate():
    with pytest.raises(Exception):
        GeneratorConfig(duplicate_utr_rate=1.5)
