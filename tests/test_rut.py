from auth import validar_rut


def test_rut_valido_normalizado():
    assert validar_rut("12.345.678-5") == "12345678-5"
    assert validar_rut("12345678-5") == "12345678-5"
    assert validar_rut("11111111-1") == "11111111-1"


def test_rut_digito_verificador_k():
    assert validar_rut("20.347.878-K") == "20347878-K"
    assert validar_rut("20347878-k") == "20347878-K"


def test_rut_invalido():
    assert validar_rut("12345678-0") is None
    assert validar_rut("11111111-2") is None
    assert validar_rut("") is None
    assert validar_rut("abc") is None
    assert validar_rut("1") is None
