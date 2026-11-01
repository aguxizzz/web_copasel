"""
Los tests corren contra un Postgres descartable en Docker, nunca contra la base
del .env. El entorno se fija aca arriba, antes de importar la app: database.py
abre el pool al importarse y load_dotenv no pisa variables ya definidas.
"""
import io
import os
from pathlib import Path

import psycopg
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from testcontainers.postgres import PostgresContainer

_postgres = PostgresContainer("postgres:18-alpine", driver=None)
_postgres.start()

os.environ.update(
    DATABASE_URL=_postgres.get_connection_url(),
    SECRET_KEY="clave-de-test",
    ADMIN_PASSWORD="admin-de-test",
    API_KEY="api-key-de-test",
    SECRET_NCON="9997",
    ALGORITHM="HS256",
    ALLOW_ORIGINS="http://localhost:3000",
    R2_ENDPOINT="http://r2.invalid",
    R2_BUCKET="bucket-de-test",
    R2_ACCESS_KEY="test",
    R2_SECRET_KEY="test",
)

assert "neon.tech" not in os.environ["DATABASE_URL"]

from fastapi.testclient import TestClient  # noqa: E402

from app import storage  # noqa: E402
from app.main import app, create_token  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as _conn:
    _conn.execute((ROOT / "tests" / "base_schema.sql").read_text(encoding="utf-8"))
    _conn.execute((ROOT / "app" / "schema.sql").read_text(encoding="utf-8"))


def pytest_sessionfinish():
    _postgres.stop()


@pytest.fixture(autouse=True)
def base_limpia():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            "TRUNCATE socios, consumos, boletas, boleta_jobs, boleta_periodos, "
            "configuracion RESTART IDENTITY"
        )


@pytest.fixture
def sql():
    """Ejecuta SQL directo para armar los datos de cada test."""
    def run(query, params=None):
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
            cur = conn.execute(query, params)
            return cur.fetchall() if cur.description else None
    return run


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def socio_headers():
    return {"Authorization": f"Bearer {create_token(345)}"}


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {create_token(9997, True)}"}


class FakeR2:
    """Reemplaza al bucket: guarda los PDFs en un dict en memoria."""

    def __init__(self):
        self.objetos = {}

    def upload_file(self, path, bucket, key, ExtraArgs=None, Config=None):
        self.objetos[key] = Path(path).read_bytes()

    def download_file(self, bucket, key, destino):
        Path(destino).write_bytes(self.objetos[key])


@pytest.fixture(autouse=True)
def r2(monkeypatch, tmp_path):
    fake = FakeR2()
    monkeypatch.setattr(storage, "_client", fake)
    monkeypatch.setattr(storage, "CACHE_DIR", tmp_path / "cache")
    return fake


def _pagina(writer, texto):
    pagina = writer.add_blank_page(width=300, height=200)
    fuente = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    pagina[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): fuente}),
    })
    contenido = DecodedStreamObject()
    contenido.set_data(f"BT /F1 10 Tf 20 100 Td ({texto}) Tj ET".encode("cp1252"))
    pagina[NameObject("/Contents")] = writer._add_object(contenido)


@pytest.fixture
def hacer_pdf():
    """Arma un PDF con una pagina por cada texto."""
    def hacer(*textos):
        writer = PdfWriter()
        for texto in textos:
            _pagina(writer, texto)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    return hacer


@pytest.fixture
def texto_pdf():
    def leer(data):
        return "".join(
            p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages
        )
    return leer
