"""
Cliente de Cloudflare R2 (S3-compatible) para los PDFs de sector.

El bucket es PRIVADO: solo se accede con credenciales por la API S3. Los socios
nunca tocan R2 directamente; el backend baja el PDF de sector y les sirve su
página por /download-pdf.

Los PDFs de sector se cachean en disco local para no re-descargarlos de R2 en
cada request (son solo 4 por periodo).
"""
import os
from pathlib import Path

import boto3
from botocore.config import Config

R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_BUCKET = os.getenv("R2_BUCKET")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")

CACHE_DIR = Path(os.getenv("BOLETAS_CACHE_DIR", ".cache/boletas"))

_client = None


def _get_client():
    """Crea (lazy) el cliente boto3 apuntando a R2."""
    global _client
    if _client is None:
        missing = [
            name for name, val in (
                ("R2_ENDPOINT", R2_ENDPOINT),
                ("R2_BUCKET", R2_BUCKET),
                ("R2_ACCESS_KEY", R2_ACCESS_KEY),
                ("R2_SECRET_KEY", R2_SECRET_KEY),
            ) if not val
        ]
        if missing:
            raise RuntimeError(
                f"Faltan variables de entorno de R2: {', '.join(missing)}"
            )
        _client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
    return _client


def _sector_key(periodo: str, sector: str) -> str:
    return f"boletas/{periodo}/{sector}.pdf"


def upload_sector_pdf(periodo: str, sector: str, data: bytes) -> None:
    """Sube (o reemplaza) el PDF de un sector para un periodo."""
    _get_client().put_object(
        Bucket=R2_BUCKET,
        Key=_sector_key(periodo, sector),
        Body=data,
        ContentType="application/pdf",
    )


def upload_sector_pdf_file(periodo: str, sector: str, path) -> None:
    """
    Sube el PDF de un sector desde un archivo en disco, sin cargar los bytes en
    RAM (transferencia administrada de boto3, multipart y con bajo uso de memoria).
    """
    _get_client().upload_file(
        str(path),
        R2_BUCKET,
        _sector_key(periodo, sector),
        ExtraArgs={"ContentType": "application/pdf"},
    )


def get_sector_pdf_path(periodo: str, sector: str) -> Path:
    """
    Ruta local al PDF de sector, descargándolo de R2 a la cache si no está.
    """
    local = CACHE_DIR / periodo / f"{sector}.pdf"
    if not local.is_file():
        local.parent.mkdir(parents=True, exist_ok=True)
        _get_client().download_file(
            R2_BUCKET, _sector_key(periodo, sector), str(local)
        )
    return local
