def _registro(email="ana@test.cl", rut="11.111.111-1", **extra):
    base = {
        "email": email,
        "password": "clave12345",
        "nombre_completo": "Ana Prueba",
        "apodo": "Ana",
        "rut": rut,
        "fecha_nacimiento": "1995-05-10",
    }
    base.update(extra)
    return base


def test_registro_ok_abre_sesion(client):
    r = client.post("/auth/register", json=_registro())
    assert r.status_code == 200
    user = r.get_json()["user"]
    assert user["nombre"] == "Ana"
    assert user["rut"] == "11111111-1"
    assert user["metodo_login"] == "password"

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.get_json()["user"]["email"] == "ana@test.cl"


def test_registro_rut_invalido(client):
    r = client.post("/auth/register", json=_registro(email="otra@test.cl", rut="12345678-0"))
    assert r.status_code == 400


def test_registro_password_corta(client):
    r = client.post("/auth/register", json=_registro(email="corta@test.cl", password="123"))
    assert r.status_code == 400


def test_registro_email_duplicado(client):
    client.post("/auth/register", json=_registro(email="dup@test.cl", rut="12.345.678-5"))
    r = client.post("/auth/register", json=_registro(email="dup@test.cl", rut="20.347.878-K"))
    assert r.status_code == 409


def test_login_correcto_e_incorrecto(client):
    client.post("/auth/register", json=_registro(email="beto@test.cl", rut="9.007.920-4"))

    mal = client.post("/auth/login", json={"email": "beto@test.cl", "password": "otra-clave"})
    assert mal.status_code == 401

    ok = client.post("/auth/login", json={"email": "BETO@test.cl", "password": "clave12345"})
    assert ok.status_code == 200
    assert ok.get_json()["user"]["email"] == "beto@test.cl"


def test_api_requiere_sesion(client):
    assert client.get("/api/dashboard").status_code == 401
