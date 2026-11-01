import uuid

import pytest

from app import boletas_processor, storage


@pytest.mark.parametrize("texto,esperado", [
    ("N° Socio: 345", 345),
    ("N° Socio:   7 algo", 7),
    ("sin numero", None),
    ("", None),
    (None, None),
])
def test_extract_ncon(texto, esperado):
    assert boletas_processor._extract_ncon(texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("Total $1,234.56", 1234.56),
    ("$10.00 recargo $20.50", 20.50),  # vale el ultimo monto de la pagina
    ("$1,000,000.00", 1000000.0),
    ("sin monto", None),
    ("$12", None),
    (None, None),
])
def test_extract_importe(texto, esperado):
    assert boletas_processor._extract_importe(texto) == esperado


def subir(client, headers, archivos, periodo="2026-10"):
    return client.post(
        "/boletas/upload",
        headers=headers,
        data={"periodo": periodo},
        files=[("files", (nombre, data)) for nombre, data in archivos],
    )


def test_upload_procesa_y_deja_el_periodo_en_borrador(
    client, sql, admin_headers, r2, hacer_pdf
):
    pdf1 = hacer_pdf(
        "N\xb0 Socio: 1 Total $1,500.25",
        "pagina sin socio",
        "N\xb0 Socio: 2 Total $200.00",
    )
    pdf2 = hacer_pdf("N\xb0 Socio: 3 sin monto")

    res = subir(client, admin_headers, [("SECTOR1.pdf", pdf1), ("SECTOR2.PDF", pdf2)])

    assert res.status_code == 200
    cuerpo = res.json()
    assert cuerpo["estado"] == "procesando"
    assert cuerpo["sectores"] == ["SECTOR1", "SECTOR2"]

    # TestClient corre las background tasks antes de devolver la respuesta.
    status = client.get(
        f"/boletas/status/{cuerpo['job_id']}", headers=admin_headers).json()
    assert status == {
        "job_id": cuerpo["job_id"],
        "periodo": "2026-10",
        "estado": "listo",
        "total": 4,
        "procesadas": 4,
        "error": None,
    }

    filas = sql(
        "SELECT ncon, sector, pagina, importe::float FROM boletas ORDER BY ncon")
    assert filas == [
        (1, "SECTOR1", 1, 1500.25),
        (2, "SECTOR1", 3, 200.0),
        (3, "SECTOR2", 1, None),
    ]
    assert sorted(r2.objetos) == [
        "boletas/2026-10/SECTOR1.pdf", "boletas/2026-10/SECTOR2.pdf"]

    periodos = client.get("/boletas/periodos", headers=admin_headers).json()
    assert periodos == [{
        "periodo": "2026-10", "publicado": False, "publicado_at": None, "boletas": 3,
    }]


def test_upload_rechaza_periodo_mal_formado(client, admin_headers, hacer_pdf):
    res = subir(client, admin_headers, [("S.pdf", hacer_pdf("x"))], periodo="10-2026")
    assert res.status_code == 400


def test_upload_rechaza_archivos_que_no_son_pdf(client, admin_headers):
    assert subir(client, admin_headers, [("datos.txt", b"x")]).status_code == 400


def test_upload_solo_con_archivos_vacios_da_400(client, admin_headers):
    assert subir(client, admin_headers, [("S.pdf", b"")]).status_code == 400


def test_pdf_corrupto_deja_el_job_en_error(client, admin_headers):
    res = subir(client, admin_headers, [("S.pdf", b"esto no es un pdf")])

    status = client.get(
        f"/boletas/status/{res.json()['job_id']}", headers=admin_headers).json()
    assert status["estado"] == "error"
    assert status["error"]


def test_status_de_job_inexistente_da_404(client, admin_headers):
    res = client.get(f"/boletas/status/{uuid.uuid4()}", headers=admin_headers)
    assert res.status_code == 404


def test_publicar_y_despublicar_cambia_lo_que_ve_el_socio(
    client, sql, admin_headers, socio_headers, hacer_pdf
):
    subir(client, admin_headers, [("S.pdf", hacer_pdf("N\xb0 Socio: 345 $100.00"))])
    assert client.get("/importe", headers=socio_headers).json()["importe"] == 0

    res = client.post(
        "/boletas/publish", json={"periodo": "2026-10"}, headers=admin_headers)
    assert res.json() == {"periodo": "2026-10", "publicado": True}
    assert client.get("/importe", headers=socio_headers).json()["importe"] == 100
    assert sql("SELECT publicado_at IS NOT NULL FROM boleta_periodos") == [(True,)]

    res = client.post(
        "/boletas/unpublish", json={"periodo": "2026-10"}, headers=admin_headers)
    assert res.json() == {"periodo": "2026-10", "publicado": False}
    assert client.get("/importe", headers=socio_headers).json()["importe"] == 0
    assert sql("SELECT publicado_at FROM boleta_periodos") == [(None,)]


def test_reprocesar_no_despublica_el_periodo(client, sql, admin_headers, hacer_pdf):
    pdf = hacer_pdf("N\xb0 Socio: 1 $100.00")
    subir(client, admin_headers, [("S.pdf", pdf)])
    client.post("/boletas/publish", json={"periodo": "2026-10"}, headers=admin_headers)

    subir(client, admin_headers, [("S.pdf", pdf)])

    assert sql("SELECT publicado FROM boleta_periodos") == [(True,)]


@pytest.mark.parametrize("ruta", ["/boletas/publish", "/boletas/unpublish"])
def test_publish_valida_el_periodo(client, admin_headers, ruta):
    mal = client.post(ruta, json={"periodo": "2026"}, headers=admin_headers)
    inexistente = client.post(ruta, json={"periodo": "2020-01"}, headers=admin_headers)

    assert mal.status_code == 400
    assert inexistente.status_code == 404


def test_resubir_un_periodo_no_sirve_el_pdf_viejo_de_la_cache(
    client, admin_headers, socio_headers, hacer_pdf, texto_pdf
):
    """Si las paginas se mueven, servir la cache entrega la boleta de otro socio."""
    subir(client, admin_headers, [("S.pdf", hacer_pdf("N\xb0 Socio: 345 version vieja"))])
    client.post("/boletas/publish", json={"periodo": "2026-10"}, headers=admin_headers)
    assert storage.get_sector_pdf_path("2026-10", "S").is_file()  # queda en cache

    subir(
        client, admin_headers,
        [("S.pdf", hacer_pdf("N\xb0 Socio: 1 otro", "N\xb0 Socio: 345 version nueva"))],
    )

    res = client.get("/download-pdf", headers=socio_headers)
    assert "version nueva" in texto_pdf(res.content)
