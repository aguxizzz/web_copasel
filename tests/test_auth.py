from datetime import datetime, timedelta, timezone

from jose import jwt

from app.main import ALGORITHM, SECRET_KEY


def test_ping(client):
    assert client.get("/ping").json() == {"ok": True}


def test_login_de_socio_existente_devuelve_token(client, sql):
    sql("INSERT INTO socios (ncon, nombre) VALUES (345, 'Ana')")

    res = client.post("/login", json={"ncon": "345"})

    assert res.status_code == 200
    payload = jwt.decode(res.json()["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["sub"] == "345"
    assert payload["role"] == "user"


def test_login_de_socio_inexistente_da_404(client):
    assert client.post("/login", json={"ncon": "999"}).status_code == 404


def test_login_rechaza_ncon_no_numerico_o_largo(client):
    assert client.post("/login", json={"ncon": "12a"}).status_code == 422
    assert client.post("/login", json={"ncon": "12345"}).status_code == 422


def test_admin_login(client):
    ok = client.post("/admin-login", json={"password": "admin-de-test"})
    mal = client.post("/admin-login", json={"password": "otra"})

    assert ok.status_code == 200
    token = ok.json()["access_token"]
    assert jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["role"] == "admin"
    assert mal.status_code == 400


def test_token_docs_acepta_password_de_admin_o_ncon_valido(client, sql):
    sql("INSERT INTO socios (ncon) VALUES (345)")

    admin = client.post("/token", data={"username": "x", "password": "admin-de-test"})
    socio = client.post("/token", data={"username": "345", "password": "x"})
    nadie = client.post("/token", data={"username": "999", "password": "x"})

    assert admin.status_code == 200
    assert socio.status_code == 200
    assert nadie.status_code == 401


def test_endpoint_protegido_sin_token_da_401(client):
    assert client.get("/importe").status_code == 401


def test_token_invalido_da_401(client):
    res = client.get("/importe", headers={"Authorization": "Bearer basura"})
    assert res.status_code == 401


def test_token_vencido_da_401(client):
    token = jwt.encode(
        {
            "sub": "345",
            "role": "user",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    res = client.get("/importe", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_token_firmado_con_otra_clave_da_401(client):
    token = jwt.encode({"sub": "345", "role": "admin"}, "otra-clave", algorithm=ALGORITHM)
    res = client.get("/importe", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
