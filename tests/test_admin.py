import pytest

API_KEY = {"x-api-key": "api-key-de-test"}


@pytest.mark.parametrize("metodo,ruta,kwargs", [
    ("post", "/update-dni", {"json": {"ncon": 1, "dni": 30111222}}),
    ("get", "/boletas/periodos", {}),
    ("get", "/boletas/status/abc", {}),
    ("post", "/boletas/publish", {"json": {"periodo": "2026-10"}}),
    ("post", "/boletas/unpublish", {"json": {"periodo": "2026-10"}}),
    ("post", "/boletas/preparacion", {"json": {"dia": 27}}),
    ("post", "/boletas/upload", {
        "data": {"periodo": "2026-10"},
        "files": {"files": ("SECTOR1.pdf", b"x")},
    }),
])
def test_rutas_de_admin_rechazan_token_de_socio(
    client, socio_headers, metodo, ruta, kwargs
):
    res = getattr(client, metodo)(ruta, headers=socio_headers, **kwargs)
    assert res.status_code == 401


def test_update_dni(client, sql, admin_headers):
    sql("INSERT INTO socios (ncon, medidor) VALUES (1, 'M-1')")

    res = client.post(
        "/update-dni", json={"ncon": 1, "dni": 30111222}, headers=admin_headers)

    assert res.json() == {"ncon": 1, "dni": 30111222, "updated": True}
    assert sql("SELECT dni FROM socios WHERE ncon = 1") == [(30111222,)]


def test_update_dni_socio_inexistente_da_404(client, admin_headers):
    res = client.post(
        "/update-dni", json={"ncon": 1, "dni": 30111222}, headers=admin_headers)
    assert res.status_code == 404


def test_update_dni_valida_el_rango(client, admin_headers):
    res = client.post("/update-dni", json={"ncon": 1, "dni": 5}, headers=admin_headers)
    assert res.status_code == 422


def test_sync_socio_exige_api_key(client):
    cuerpo = {"ncon": 1, "nombre": "Ana"}

    assert client.post("/sync-socio", json=cuerpo).status_code == 422
    assert client.post(
        "/sync-socio", json=cuerpo, headers={"x-api-key": "mal"}).status_code == 401


def test_sync_socio_crea_y_despues_actualiza(client, sql):
    client.post(
        "/sync-socio", headers=API_KEY,
        json={"ncon": 1, "nombre": "Ana", "medidor": "M-1", "domicilio": "Calle 1"},
    )
    res = client.post(
        "/sync-socio", headers=API_KEY,
        json={"ncon": 1, "nombre": "Ana Paula", "dni": 30111222, "cuit": 20301112223},
    )

    assert res.json() == {"ncon": 1, "synced": True}
    assert sql("SELECT nombre, dni, cuit, medidor, domicilio FROM socios") == [
        ("Ana Paula", 30111222, 20301112223, None, None)
    ]


def test_sync_socio_sin_dni_ni_cuit_guarda_cero(client, sql):
    client.post("/sync-socio", headers=API_KEY, json={"ncon": 1, "nombre": "Ana"})

    assert sql("SELECT dni, cuit FROM socios") == [(0, 0)]
