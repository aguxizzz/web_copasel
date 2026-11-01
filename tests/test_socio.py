def publicar(sql, periodo, publicado=True):
    sql(
        "INSERT INTO boleta_periodos (periodo, publicado, publicado_at) "
        "VALUES (%s, %s, CASE WHEN %s THEN now() END)",
        (periodo, publicado, publicado),
    )


def boleta(sql, periodo, ncon=345, sector="SECTOR1", pagina=1, importe=100):
    sql(
        "INSERT INTO boletas (ncon, periodo, sector, pagina, importe) "
        "VALUES (%s, %s, %s, %s, %s)",
        (ncon, periodo, sector, pagina, importe),
    )


def test_importe_es_el_del_ultimo_periodo_publicado(client, sql, socio_headers):
    publicar(sql, "2026-09")
    publicar(sql, "2026-10")
    publicar(sql, "2026-11", publicado=False)
    boleta(sql, "2026-09", importe=100)
    boleta(sql, "2026-10", importe=200.5)
    boleta(sql, "2026-11", importe=300)

    res = client.get("/importe", headers=socio_headers)

    assert res.json() == {"importe": 200.5}


def test_importe_no_se_mezcla_con_el_de_otro_socio(client, sql, socio_headers):
    publicar(sql, "2026-10")
    boleta(sql, "2026-10", ncon=1, importe=999)

    res = client.get("/importe", headers=socio_headers)

    assert res.json() == {"importe": 0, "message": "No hay importe para este socio"}


def test_importe_null_se_informa_como_cero(client, sql, socio_headers):
    publicar(sql, "2026-10")
    boleta(sql, "2026-10", importe=None)

    assert client.get("/importe", headers=socio_headers).json()["importe"] == 0


def test_descarga_la_pagina_del_socio_del_ultimo_publicado(
    client, sql, socio_headers, r2, hacer_pdf, texto_pdf
):
    r2.objetos["boletas/2026-10/SECTOR1.pdf"] = hacer_pdf("otro socio", "boleta de ana")
    publicar(sql, "2026-10")
    boleta(sql, "2026-10", pagina=2)

    res = client.get("/download-pdf", headers=socio_headers)

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert 'filename="boleta_345.pdf"' in res.headers["content-disposition"]
    assert "no-store" in res.headers["cache-control"]
    assert "boleta de ana" in texto_pdf(res.content)
    assert "otro socio" not in texto_pdf(res.content)


def test_descarga_ignora_periodos_sin_publicar(client, sql, socio_headers):
    publicar(sql, "2026-11", publicado=False)
    boleta(sql, "2026-11")

    assert client.get("/download-pdf", headers=socio_headers).status_code == 404


def test_error_de_r2_en_la_descarga_da_500(client, sql, socio_headers):
    publicar(sql, "2026-10")
    boleta(sql, "2026-10")  # el bucket de test esta vacio

    assert client.get("/download-pdf", headers=socio_headers).status_code == 500


def test_medidor_y_nombre(client, sql, socio_headers):
    sql("INSERT INTO socios (ncon, nombre, medidor) VALUES (345, 'Ana', 'M-1')")

    assert client.get("/medidor", headers=socio_headers).json() == {"medidor": "M-1"}
    assert client.get("/nombre", headers=socio_headers).json() == {"nombre": "Ana"}


def test_medidor_y_nombre_de_socio_borrado_dan_404(client, socio_headers):
    assert client.get("/medidor", headers=socio_headers).status_code == 404
    assert client.get("/nombre", headers=socio_headers).status_code == 404


def test_last_consumos_devuelve_diferencia_entre_lecturas(client, sql, socio_headers):
    sql(
        "INSERT INTO consumos (ncon, fecha, estado) VALUES "
        "(345, '2026-08-01', 100), (345, '2026-09-01', 130), (345, '2026-10-01', 145), "
        "(1, '2026-10-01', 9999)"
    )

    res = client.get("/last-consumos", headers=socio_headers)

    assert res.json() == {"2026-10-01": 15, "2026-09-01": 30}


def test_last_consumos_respeta_limit(client, sql, socio_headers):
    sql(
        "INSERT INTO consumos (ncon, fecha, estado) VALUES "
        "(345, '2026-08-01', 100), (345, '2026-09-01', 130), (345, '2026-10-01', 145)"
    )

    res = client.get("/last-consumos?limit=1", headers=socio_headers)

    assert res.json() == {"2026-10-01": 15}


def test_last_consumos_con_una_sola_lectura_queda_vacio(client, sql, socio_headers):
    sql("INSERT INTO consumos (ncon, fecha, estado) VALUES (345, '2026-10-01', 100)")

    assert client.get("/last-consumos", headers=socio_headers).json() == {}


def test_last_consumos_sin_lecturas_da_404(client, socio_headers):
    assert client.get("/last-consumos", headers=socio_headers).status_code == 404
