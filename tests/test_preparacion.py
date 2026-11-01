from datetime import datetime, timezone

import pytest

from app import preparacion
from app.preparacion import TZ_ARGENTINA, calcular_estado, inicio_ventana


def ar(*args):
    return datetime(*args, tzinfo=TZ_ARGENTINA)


@pytest.mark.parametrize("ahora,dia,esperado", [
    (ar(2026, 10, 27, 0, 0), 27, ar(2026, 10, 27)),   # el mismo dia
    (ar(2026, 10, 30, 12, 0), 27, ar(2026, 10, 27)),  # despues del dia
    (ar(2026, 10, 26, 23, 59), 27, ar(2026, 9, 27)),  # un minuto antes
    (ar(2026, 1, 5, 9, 0), 27, ar(2025, 12, 27)),     # cruza el año
    (ar(2026, 3, 1, 9, 0), 28, ar(2026, 2, 28)),      # febrero
    (ar(2026, 10, 1, 0, 0), 1, ar(2026, 10, 1)),
])
def test_inicio_ventana(ahora, dia, esperado):
    assert inicio_ventana(ahora, dia) == esperado


def test_inicio_ventana_usa_la_hora_argentina():
    # 27/10 01:00 UTC todavia es 26/10 22:00 en Argentina.
    ahora = datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc)
    assert inicio_ventana(ahora, 27) == ar(2026, 9, 27)


def test_sin_dia_configurado_no_hay_aviso():
    estado = calcular_estado(ar(2026, 10, 27), None, None)
    assert estado == {"dia": None, "preparando": False, "mes": None}


def test_antes_del_dia_no_hay_aviso_si_ya_se_publico():
    estado = calcular_estado(ar(2026, 10, 15), 27, ar(2026, 9, 29, 14, 0))
    assert estado == {"dia": 27, "preparando": False, "mes": None}


def test_llegado_el_dia_se_activa_y_nombra_el_mes_siguiente():
    estado = calcular_estado(ar(2026, 10, 27, 8, 0), 27, ar(2026, 9, 29, 14, 0))
    assert estado == {"dia": 27, "preparando": True, "mes": "2026-11"}


def test_diciembre_pasa_a_enero():
    estado = calcular_estado(ar(2026, 12, 28), 27, ar(2026, 11, 29))
    assert estado["mes"] == "2027-01"


def test_publicar_apaga_el_aviso():
    estado = calcular_estado(ar(2026, 10, 30), 27, ar(2026, 10, 29, 18, 46))
    assert estado["preparando"] is False


def test_si_no_se_publico_sigue_activo_el_mes_siguiente():
    estado = calcular_estado(ar(2026, 11, 3), 27, ar(2026, 9, 29))
    assert estado == {"dia": 27, "preparando": True, "mes": "2026-11"}


def test_sin_ninguna_publicacion_queda_activo():
    assert calcular_estado(ar(2026, 10, 28), 27, None)["preparando"] is True


def congelar(monkeypatch, ahora):
    class Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            return ahora.astimezone(tz)
    monkeypatch.setattr(preparacion, "datetime", Reloj)


def test_por_defecto_esta_desactivado(client, socio_headers):
    res = client.get("/boletas/preparacion", headers=socio_headers)
    assert res.json() == {"dia": None, "preparando": False, "mes": None}


def test_exige_token(client):
    assert client.get("/boletas/preparacion").status_code == 401


def test_admin_configura_y_el_socio_ve_el_aviso(
    client, admin_headers, socio_headers, monkeypatch
):
    congelar(monkeypatch, ar(2026, 10, 27, 10, 0))

    res = client.post(
        "/boletas/preparacion", json={"dia": 27}, headers=admin_headers)

    assert res.json() == {"dia": 27, "preparando": True, "mes": "2026-11"}
    assert client.get("/boletas/preparacion", headers=socio_headers).json() == res.json()


def test_el_dia_se_puede_cambiar_y_desactivar(client, admin_headers, sql):
    client.post("/boletas/preparacion", json={"dia": 27}, headers=admin_headers)
    client.post("/boletas/preparacion", json={"dia": 25}, headers=admin_headers)
    assert sql("SELECT valor FROM configuracion") == [("25",)]

    res = client.post(
        "/boletas/preparacion", json={"dia": None}, headers=admin_headers)

    assert res.json() == {"dia": None, "preparando": False, "mes": None}
    assert sql("SELECT valor FROM configuracion") == []


@pytest.mark.parametrize("cuerpo", [{"dia": 0}, {"dia": 29}, {"dia": "x"}, {}])
def test_rechaza_dias_invalidos(client, admin_headers, cuerpo):
    res = client.post("/boletas/preparacion", json=cuerpo, headers=admin_headers)
    assert res.status_code == 422


def test_publicar_un_periodo_apaga_el_aviso_y_despublicar_lo_vuelve_a_prender(
    client, sql, admin_headers, socio_headers
):
    client.post("/boletas/preparacion", json={"dia": 1}, headers=admin_headers)
    sql("INSERT INTO boleta_periodos (periodo) VALUES ('2026-10')")
    ver = lambda: client.get("/boletas/preparacion", headers=socio_headers).json()
    assert ver()["preparando"] is True

    client.post("/boletas/publish", json={"periodo": "2026-10"}, headers=admin_headers)
    assert ver()["preparando"] is False

    client.post("/boletas/unpublish", json={"periodo": "2026-10"}, headers=admin_headers)
    assert ver()["preparando"] is True
