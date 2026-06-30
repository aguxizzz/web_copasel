"""
Procesamiento de boletas: reemplaza al GitHub Action.

Recibe los PDFs de sector subidos por un trabajador, los guarda en R2 y registra
en la tabla `boletas` (ncon, periodo, sector, pagina, importe) leyendo cada
página. El avance se refleja en `boleta_jobs` para que el front haga polling.

Pensado para correr en background (FastAPI BackgroundTasks): las funciones son
síncronas y Starlette las ejecuta en el threadpool, sin bloquear el event loop.
"""
import gc
import io
import os
import re
import shutil

from pypdf import PdfReader, PdfWriter

from app import storage
from app.database import db_conn

# Mismos patrones que el script original.
AMOUNT_RE = re.compile(r"\$([0-9,]+\.[0-9]{2})")
SOCIO_RE = re.compile(r"N° Socio:\s*(\d{1,4})")

# Campos que _update_job puede setear (whitelist; las claves NO vienen de input externo).
_JOB_FIELDS = {"estado", "total", "procesadas", "error"}


def _extract_ncon(text: str):
    m = SOCIO_RE.search(text or "")
    return int(m.group(1)) if m else None


def _extract_importe(text: str):
    """Último monto $ de la página (igual criterio que el script original)."""
    matches = AMOUNT_RE.findall(text or "")
    if not matches:
        return None
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


def extract_boleta_pdf(periodo: str, sector: str, pagina: int) -> bytes:
    """
    Devuelve los bytes de un PDF de una sola página: la boleta del socio,
    extraída del PDF de sector (descargado de R2 a la cache local).
    """
    path = storage.get_sector_pdf_path(periodo, sector)
    reader = PdfReader(str(path))
    writer = PdfWriter()
    writer.add_page(reader.pages[pagina - 1])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(job_id: str, periodo: str) -> None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO boleta_jobs (id, periodo, estado) VALUES (%s, %s, 'procesando')",
                (job_id, periodo),
            )
        conn.commit()


def _update_job(job_id: str, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in _JOB_FIELDS}
    if not fields:
        return
    sets = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [job_id]
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE boleta_jobs SET {sets}, updated_at = now() WHERE id = %s",
                params,
            )
        conn.commit()


def get_job(job_id: str):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, periodo, estado, total, procesadas, error "
                "FROM boleta_jobs WHERE id = %s",
                (job_id,),
            )
            return cur.fetchone()


# ---------------------------------------------------------------------------
# Persistencia de boletas
# ---------------------------------------------------------------------------

def _ensure_periodo(periodo: str) -> None:
    """Registra el periodo (como NO publicado) si no existe. Preserva el estado
    de publicacion si ya estaba, para no despublicar al re-procesar."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO boleta_periodos (periodo) VALUES (%s) "
                "ON CONFLICT (periodo) DO NOTHING",
                (periodo,),
            )
        conn.commit()


def publicar_periodo(periodo: str) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE boleta_periodos SET publicado = TRUE, publicado_at = now() "
                "WHERE periodo = %s",
                (periodo,),
            )
            updated = cur.rowcount
        conn.commit()
    return updated > 0


def despublicar_periodo(periodo: str) -> bool:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE boleta_periodos SET publicado = FALSE, publicado_at = NULL "
                "WHERE periodo = %s",
                (periodo,),
            )
            updated = cur.rowcount
        conn.commit()
    return updated > 0


def listar_periodos():
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.periodo, p.publicado, p.publicado_at, COUNT(b.ncon) AS boletas
                FROM boleta_periodos p
                LEFT JOIN boletas b ON b.periodo = p.periodo
                GROUP BY p.periodo, p.publicado, p.publicado_at
                ORDER BY p.periodo DESC
                """
            )
            return cur.fetchall()


def _upsert_boletas(rows) -> None:
    if not rows:
        return
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO boletas (ncon, periodo, sector, pagina, importe)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (ncon, periodo) DO UPDATE SET
                    sector  = EXCLUDED.sector,
                    pagina  = EXCLUDED.pagina,
                    importe = EXCLUDED.importe,
                    created_at = now()
                """,
                rows,
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Proceso principal (corre en background)
# ---------------------------------------------------------------------------

def process_period(
    job_id: str, periodo: str, sector_paths: dict, tmp_dir: str | None = None
) -> None:
    """
    sector_paths: dict[sector_name -> ruta del PDF en disco].

    Sube cada PDF de sector a R2 y registra la boleta de cada socio. Esta
    pensado para correr en 512MB de RAM: los PDFs nunca se cargan enteros en
    memoria. Procesa un sector a la vez leyendo de disco con pypdf (mucho mas
    liviano que pdfplumber/pdfminer), sube a R2 por streaming y borra cada
    temporal al terminar.
    """
    try:
        # Conteo total de paginas para el progreso.
        total = 0
        for path in sector_paths.values():
            with open(path, "rb") as fh:
                total += len(PdfReader(fh).pages)
        _update_job(job_id, total=total)

        procesadas = 0
        for sector in list(sector_paths.keys()):
            path = sector_paths.pop(sector)

            # Persistir el PDF de sector en R2 (privado), por streaming.
            storage.upload_sector_pdf_file(periodo, sector, path)

            rows = []
            reader = PdfReader(path)
            for i, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                ncon = _extract_ncon(text)
                if ncon is not None:
                    rows.append((ncon, periodo, sector, i, _extract_importe(text)))
                procesadas += 1
                if procesadas % 25 == 0:
                    _update_job(job_id, procesadas=procesadas)

            # Persistir por sector (no acumular todas las filas hasta el final).
            _upsert_boletas(rows)
            del reader, rows
            os.remove(path)  # liberar el disco del sector ya procesado
            gc.collect()  # devolver la memoria del sector antes del siguiente

        _ensure_periodo(periodo)  # queda como borrador hasta que el admin publique
        _update_job(job_id, procesadas=procesadas, estado="listo")
    except Exception as e:  # noqa: BLE001 - se registra en el job para el front
        _update_job(job_id, estado="error", error=str(e))
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
