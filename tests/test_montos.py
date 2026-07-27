import pytest

from extractor import _parsear_monto


def test_montos_numericos():
    assert _parsear_monto(12000) == 12000
    assert _parsear_monto(12000.4) == 12000


def test_montos_texto_chileno():
    assert _parsear_monto("12.000") == 12000
    assert _parsear_monto("$1.500") == 1500
    assert _parsear_monto("25k") == 25000
    assert _parsear_monto("12 lucas") == 12000
    assert _parsear_monto("media luca") == 500
    assert _parsear_monto("2 palos") == 2_000_000


def test_montos_invalidos():
    for malo in ("abc", 0, -100, True):
        with pytest.raises(ValueError):
            _parsear_monto(malo)
