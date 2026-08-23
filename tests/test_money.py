from decimal import Decimal
import pytest
from nostro.money import rupees_to_paise, paise_to_rupees, MoneyParseError


def test_rupees_string_to_paise():
    assert rupees_to_paise("1234.56") == 123456


def test_rupees_with_commas_and_symbol():
    assert rupees_to_paise("Rs. 1,23,456.78") == 12345678


def test_third_decimal_is_rejected_not_rounded():
    with pytest.raises(MoneyParseError):
        rupees_to_paise("10.005")


def test_negative_amount_preserved():
    assert rupees_to_paise("-99.01") == -9901


def test_decimal_input():
    assert rupees_to_paise(Decimal("0.01")) == 1


def test_paise_back_to_rupees_is_exact():
    assert paise_to_rupees(12345678) == "123456.78"


def test_garbage_raises():
    with pytest.raises(MoneyParseError):
        rupees_to_paise("N/A")
