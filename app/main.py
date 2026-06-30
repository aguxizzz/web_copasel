# ----------------------------
# FastAPI Water Consumption Management API
# Manages: socios (users), conexiones (connections), consumos (consumption readings)
# ----------------------------

# ----------------------------
# IMPORTS
# ----------------------------
from contextlib import asynccontextmanager
from fastapi import (
    FastAPI, HTTPException, Depends, APIRouter, Header,
    UploadFile, File, Form, BackgroundTasks,
)
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, validator, EmailStr
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from dotenv import load_dotenv
from fastapi import Response
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from fastapi.staticfiles import StaticFiles
# # #

# Cargar .env desde la raíz del proyecto (solo si existe)
# En producción (Render), las variables están en el panel de Environment
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

from app.database import db_conn, pool
from app import boletas_processor

# ----------------------------
# CONFIGURATION & CONSTANTS
# ----------------------------
def _require_secret(name: str) -> str:
    """Lee un secreto del entorno; revienta si falta o sigue con el placeholder."""
    value = os.getenv(name)
    if not value or value == "change-me-in-production":
        raise RuntimeError(
            f"{name} no esta configurada. Defini un valor real (ej. un UUID) "
            "en las variables de entorno antes de arrancar la app."
        )
    return value


SECRET_KEY = _require_secret("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
SECRET_NCON = int(os.getenv("SECRET_NCON", "9997"))
PASSWORD = _require_secret("ADMIN_PASSWORD")
API_KEY = _require_secret("API_KEY")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
# Solo se sirve publicamente la carpeta de imagenes. Las boletas (pdfs/) y los
# montos (montos/payments.json) viven dentro de static/ pero NO se montan: las
# boletas solo se entregan via /download-pdf con token del socio.
IMAGES_DIR = os.path.join(STATIC_DIR, "images")
# ----------------------------
# APP INITIALIZATION & MIDDLEWARE
# ----------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Abre el pool al inicio y lo cierra al apagar la app."""
    yield
    pool.close()


app = FastAPI(lifespan=lifespan)

# CORS configuration driven from environment to avoid allowing everything by default.
# Set ALLOW_ORIGINS in the .env as a comma-separated list 
#(e.g. "https://app.example.com,http://localhost:3000").
allow_origins_env = os.getenv("ALLOW_ORIGINS", "")
allow_origin_regex_env = os.getenv("ALLOW_ORIGIN_REGEX", "")
if allow_origins_env:
    allow_origins = [o.strip() for o in allow_origins_env.split(",") if o.strip()]
else:
    allow_origins = []

if allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origins=allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
elif allow_origin_regex_env:
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origin_regex=allow_origin_regex_env,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # Safe default for development: only localhost origins allowed
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origins=["http://localhost", "http://localhost:8000", "http://127.0.0.1:8000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

os.makedirs(IMAGES_DIR, exist_ok=True)
app.mount("/static/images", StaticFiles(directory=IMAGES_DIR), name="static-images")

@app.get("/ping")
def ping():
    """Health check endpoint."""
    return {"ok": True}

# ----------------------------
# PYDANTIC MODELS (Request/Response Validation)
# ----------------------------


class LoginCreate(BaseModel):
    """Login request: DNI and connection number."""
    ncon: str = Field(..., min_length=1, max_length=4)

    @validator("ncon")
    def solo_digitos(cls, v: str):
        if not v.isdigit():
            raise ValueError("Debe contener solo digitos")
        return v


class AdminLogin(BaseModel):
    """ Admin login cookie creation"""
    password: str = Field(..., max_length=100)


class ConsumoInsert(BaseModel):
    """Consumption reading insert request."""
    ncon: int = Field(..., gt=0, description="Numero de conexion (ncon)")
    fecha: datetime = Field(
        None, description="Timestamp de la lectura (opcional). Si no se envia se usara el valor por defecto de la BD")
    estado: int = Field(..., gt=0, description="Estado del medidor")

    @validator("estado")
    def validar_estado(cls, v: float):
        if v <= 0:
            raise ValueError("estado debe ser mayor que 0")
        return v


class UpdateDniParams(BaseModel):
    """DNI update request for a socio."""
    ncon: int = Field(..., gt=0, description="Numero de conexion (ncon)")
    dni: int = Field(
        ..., ge=1_000_000, le=999_999_999,
        description="DNI nuevo (7 a 9 digitos)"
    )


class PeriodoPublish(BaseModel):
    """Publicar/despublicar un periodo de boletas."""
    periodo: str = Field(..., description="Periodo a publicar (YYYY-MM)")


class SocioCreate(BaseModel):
    """Socio creation request from base server."""
    ncon: int = Field(..., gt=0, description="Numero de conexion (ncon)")
    nombre: str = Field(..., min_length=1, description="Nombre del socio")
    dni: Optional[int] = Field(None, description="DNI (enviar 0 si es null)")
    cuit: Optional[int] = Field(None, description="CUIT (enviar 0 si es null)")
    medidor: Optional[str] = Field(None, description="Numero de medidor")
    domicilio: Optional[str] = Field(None, description="Domicilio")
    
# ----------------------------
# UTILITY FUNCTIONS
# ----------------------------


def check_admin(role: str):
    if role != "admin":
        raise HTTPException(status_code=401, detail="Usuario no autorizado")
    return True


def insert_data(conn, table: str, data: dict, returning: str):
    """
    Generic INSERT helper using existing connection.
    Args:
        conn: Database connection
        table: Table name
        data: Dictionary of column:value pairs
        returning: Column to return after insert
    Returns:
        The value of the RETURNING column
    """
    columns = []
    placeholders = []
    values = []

    for key, value in data.items():
        columns.append(key)
        placeholders.append("%s")
        values.append(value)

    query = f"""
        INSERT INTO {table} ({", ".join(columns)})
        VALUES ({", ".join(placeholders)})
        RETURNING {returning};
    """

    cursor = conn.cursor()
    try:
        cursor.execute(query, values)
        result = cursor.fetchone()[0]
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        cursor.close()


def fetch_consumos(ncon: int, limit: int | None = None):
    query = """
        SELECT estado, fecha
        FROM consumos
        WHERE ncon = %s
        ORDER BY fecha DESC
    """

    if limit:
        query += " LIMIT %s"
        params = (ncon, limit)
    else:
        params = (ncon,)

    with db_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()


def fetch_rangos():
    query = """
        SELECT id_rango, importe
        FROM rangos
    """

    with db_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchall()


# ----------------------------
# AUTHENTICATION ENDPOINTS
# ----------------------------


def decode_token(token):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid or expired token") from exc


# tokenUrl apunta al endpoint /token: hace que el boton "Authorize" de los docs
# tenga un formulario de login. En runtime solo lee "Authorization: Bearer <token>",
# igual que antes, asi que el frontend no cambia.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def verify_api_key(x_api_key: str = Header(..., alias="x-api-key")):
    """Verify API key from base server."""
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Extract and validate JWT token from Authorization header (Bearer token).
    Compatible with frontend storing token in localStorage and sending it as:
    Authorization: Bearer <token>
    Returns the user's ncon (connection number) if valid.
    """
    payload = decode_token(token)

    sub = payload.get("sub")
    role = payload.get("role")

    if not sub or not role:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        return {"ncon": int(sub), "role": role}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid user id") from exc


def create_token(ncon, is_admin=False):
    """
    Create JWT token with 2-hour expiry.
    Args:
        ncon: Connection number to embed as 'sub' claim
    Returns:
        Encoded JWT token string
    """
    sub = str(ncon)
    role = "user"
    exp = datetime.now(timezone.utc) + timedelta(hours=2)

    if is_admin:
        role = "admin"

    payload = {
        "sub": sub,
        "role": role,
        "exp": exp
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token


@app.post("/token", include_in_schema=False)
def token_login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Login para el boton 'Authorize' de los docs (flujo OAuth2 password).
    - password = contraseña de admin  -> token de admin
    - username = un ncon valido        -> token de ese socio
    El frontend NO usa este endpoint; sigue con /login y /admin-login.
    """
    # Admin: alcanza con la contraseña correcta.
    if form_data.password and form_data.password == PASSWORD:
        return {"access_token": create_token(SECRET_NCON, True), "token_type": "bearer"}

    # Socio: username = ncon existente.
    if form_data.username.isdigit():
        with db_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT ncon FROM socios WHERE ncon = %s",
                    (int(form_data.username),),
                )
                if cursor.fetchone():
                    return {
                        "access_token": create_token(int(form_data.username)),
                        "token_type": "bearer",
                    }

    raise HTTPException(status_code=401, detail="Credenciales invalidas")


# - - - - - - - - - - - - - - - -
# USER ENDPOINTS
# - - - - - - - - - - - - - - - -

@app.get("/download-pdf")
def download_user_pdf(current_user: dict = Depends(get_current_user)):
    ncon = current_user["ncon"]

    # Periodo PUBLICADO mas reciente con boleta para este socio.
    with db_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT b.periodo, b.sector, b.pagina FROM boletas b "
                "JOIN boleta_periodos p ON p.periodo = b.periodo "
                "WHERE b.ncon = %s AND p.publicado "
                "ORDER BY b.periodo DESC LIMIT 1",
                (ncon,),
            )
            row = cursor.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No hay boleta disponible para el socio {ncon}",
        )

    periodo, sector, pagina = row
    try:
        pdf_bytes = boletas_processor.extract_boleta_pdf(periodo, sector, pagina)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Error obteniendo la boleta: {e}",
        ) from e

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="boleta_{ncon}.pdf"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

@app.post("/login")
def log_in(data: LoginCreate):
    """
    Authenticate user with connection number.
    Returns JWT token in JSON for frontend to store in localStorage.
    Frontend must send: Authorization: Bearer <token> on subsequent requests.
    """
    cursor = None
    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            ncon_int = int(data.ncon)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="ncon debe ser un numero entero") from exc

        cursor.execute(
            "SELECT ncon FROM socios WHERE ncon = %s",
            (ncon_int,)
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(
                status_code=404, detail="Socio no encontrado/a")

        ncon = row[0]
        token = create_token(ncon)
        cursor.close()
        return {"access_token": token, "token_type": "bearer"}


@app.get("/importe")
def get_importe(current_user: dict = Depends(get_current_user)):
    """Importe de la boleta mas reciente del socio (tabla boletas)."""
    ncon = current_user["ncon"]
    with db_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT b.importe FROM boletas b "
                "JOIN boleta_periodos p ON p.periodo = b.periodo "
                "WHERE b.ncon = %s AND p.publicado "
                "ORDER BY b.periodo DESC LIMIT 1",
                (ncon,),
            )
            row = cursor.fetchone()

    if not row or row[0] is None:
        return {"importe": 0, "message": "No hay importe para este socio"}
    return {"importe": float(row[0])}


@app.get("/medidor")
def get_num_medidor(current_user: dict = Depends(get_current_user)):
    """Get meter number for authenticated user."""
    ncon = current_user["ncon"]
    cursor = None
    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT medidor FROM socios WHERE ncon = %s",
                (ncon,)
            )
            row = cursor.fetchone()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error en la base de datos: {str(e)}"
            ) from e
        finally:
            if cursor:
                cursor.close()

        if row:
            return {"medidor": row[0]}
        else:
            raise HTTPException(
                status_code=404, detail="Usuario no encontrado")

@app.get("/nombre")
def get_nombre(current_user: dict = Depends(get_current_user)):
    """Get user name for authenticated user."""
    ncon = current_user["ncon"]
    cursor = None
    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT nombre FROM socios WHERE ncon = %s",
                (ncon,)
            )
            row = cursor.fetchone()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error en la base de datos: {str(e)}"
            ) from e
        finally:
            if cursor:
                cursor.close()

        if row:
            return {"nombre": row[0]}
        else:
            raise HTTPException(
                status_code=404, detail="Usuario no encontrado")



@app.get("/last-consumos")
def get_last_consumos_date(current_user: dict = Depends(get_current_user), limit: int = 5):
    """Get last N consumption readings (ordered by date DESC)."""
    # Sanitize limit parameter
    ncon = current_user["ncon"]
    if limit is None or limit <= 0:
        limit = 5
    limit = min(limit, 100)

    cursor = None
    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT fecha::text AS fecha, estado FROM consumos WHERE ncon = %s ORDER BY fecha DESC LIMIT %s",
                (ncon, (limit + 1))
            )
            rows = cursor.fetchall()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error en la base de datos: {str(e)}"
            ) from e
        finally:
            if cursor:
                cursor.close()

        if rows:
            result = {}
            for i, r in enumerate(rows):
                if i == len(rows) - 1:
                    break
                else:
                    result.update({r[0]: (r[1] - rows[i+1][1])})
            return JSONResponse(content=result)
        else:
            raise HTTPException(
                status_code=404, detail="Usuario no encontrado")


# - - - - - - - - - - - - - - - -
# ADMIN ENDPOINTS
# - - - - - - - - - - - - - - - -


@app.post("/admin-login")
def log_in_admin(data: AdminLogin):
    """
    Authenticates admin by password. Returns JWT token for frontend to store in localStorage.
    Frontend must send: Authorization: Bearer <token> on subsequent requests.
    """

    if data.password != PASSWORD:
        raise HTTPException(status_code=400, detail="Contraseña incorrecta")
    token = create_token(SECRET_NCON, True)
    return {"access_token": token, "token_type": "bearer"}


#app.get("/listar-socios")
def list_socios(userdata: dict = Depends(get_current_user)):
    """List all socios ordered by connection number."""

    check_admin(userdata["role"])

    cursor = None
    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT * FROM socios ORDER BY ncon")
            return JSONResponse(cursor.fetchall())
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error en la base de datos: {str(e)}") from e
        finally:
            if cursor:
                cursor.close()


@app.post("/update-dni")
def update_dni(data: UpdateDniParams, userdata: dict = Depends(get_current_user)):
    """Update DNI field for a socio by connection number."""

    check_admin(userdata["role"])

    cursor = None
    with db_conn() as conn:
        conn.autocommit = False
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE socios SET dni = %s::bigint WHERE ncon = %s RETURNING medidor",
                (data.dni, data.ncon)
            )
            row = cursor.fetchone()
            if not row:
                conn.rollback()
                raise HTTPException(
                    status_code=404, detail="Socio no encontrado")

            conn.commit()
            return {"ncon": data.ncon, "dni": data.dni, "updated": True}
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(
                status_code=500, detail=f"Error en la base de datos: {str(e)}") from e
        finally:
            if cursor:
                cursor.close()


@app.post("/sync-socio")
def sync_socio(data: SocioCreate, _: bool = Depends(verify_api_key)):
    """
    Sync a socio from base server.
    Creates or updates a socio record. DNI and CUIT default to 0 if not provided.
    """
    cursor = None
    with db_conn() as conn:
        conn.autocommit = False
        cursor = conn.cursor()
        try:
            dni = data.dni if data.dni is not None else 0
            cuit = data.cuit if data.cuit is not None else 0

            cursor.execute("""
                INSERT INTO socios (ncon, nombre, dni, cuit, medidor, domicilio)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (ncon) DO UPDATE SET
                    nombre = EXCLUDED.nombre,
                    dni = EXCLUDED.dni,
                    cuit = EXCLUDED.cuit,
                    medidor = EXCLUDED.medidor,
                    domicilio = EXCLUDED.domicilio
                RETURNING ncon
            """, (data.ncon, data.nombre, dni, cuit, data.medidor, data.domicilio))

            row = cursor.fetchone()
            conn.commit()
            return {"ncon": row[0], "synced": True}
        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}") from e
        finally:
            if cursor:
                cursor.close()


#app.get("/listar-consumos")
def list_all_consumos(userdata: dict = Depends(get_current_user)):
    """List all consumption records with estado and fecha."""

    check_admin(userdata["role"])

    cursor = None
    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT consumo_id, ncon , estado , fecha::text AS fecha FROM consumos;")
            rows = cursor.fetchall()
            return JSONResponse(content=[list(row) for row in rows])
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error en la base de datos: {str(e)}") from e
        finally:
            if cursor:
                cursor.close()


#app.get("/keep-10-consumos")
def keep_10_consumos(userdata: dict = Depends(get_current_user)):
    check_admin(userdata["role"])
    with db_conn() as conn:
        with conn.cursor() as cursor:
            query = """DELETE FROM consumos WHERE consumo_id IN (SELECT consumo_id FROM (SELECT consumo_id, ROW_NUMBER() OVER (PARTITION BY ncon ORDER BY fecha DESC) AS rn FROM consumos) sub WHERE rn > 10);"""
            try:
                cursor.execute(query)
                deleted = cursor.rowcount
                conn.commit()
            except Exception as e:
                raise HTTPException(
                    status_code=500, detail=f"Error al eliminar filas: {e}") from e
            else:
                return "exito"


#app.post("/fill-consumos")
def post_consumo(data: ConsumoInsert, userdata: dict = Depends(get_current_user)):
    """Insert a new consumption reading."""
    check_admin(userdata["role"])
    cursor = None
    with db_conn() as conn:
        conn.autocommit = False
        cursor = conn.cursor()
        try:
            consumo_id = insert_data(conn, "consumos", data.dict(
                exclude_none=True), "consumo_id")

            conn.commit()
            return {"consumo_id": consumo_id}
        except HTTPException as he:
            conn.rollback()
            raise he
        except Exception as e:
            conn.rollback()
            raise HTTPException(
                status_code=500, detail=f"Error interno: {str(e)}") from e
        finally:
            if cursor:
                cursor.close()


#@app.get("/to_fill_consumos")
def get_unfilled_consumos(userdata: dict = Depends(get_current_user)):
    """Get all consumptions which are a month beyond current date"""

    check_admin(userdata["role"])
    current_date = datetime.now().replace(day=1)
    cursor = None
    query = """
    SELECT DISTINCT ON (ncon)
    ncon,
    fecha::text AS fecha,
    estado
    FROM consumos
    WHERE fecha < %s
    ORDER BY ncon, fecha DESC;
    """
    params = (current_date, )

    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error en la base de datos: {str(e)}"
            ) from e
        else:
            cursor.close()

        if rows:
            result = {r[0]: (r[1], r[2]) for r in rows}
            return JSONResponse(content=result)
        else:
            raise HTTPException(
                status_code=404, detail="Usuario no encontrado")


#@app.get("/emptydni")
def list_ncon_with_empty_dni(userdata: dict = Depends(get_current_user)):
    """List socios with NULL, empty, or placeholder '0' DNI values."""

    check_admin(userdata["role"])
    cursor = None
    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT ncon, nombre FROM socios WHERE dni IS NULL OR trim(CAST(dni AS TEXT)) IN ('', '0')"
            )
            rows = cursor.fetchall()
            return JSONResponse(content=[{"ncon": row[0], "nombre": row[1]} for row in rows])
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error en la base de datos: {str(e)}"
            ) from e
        finally:
            if cursor:
                cursor.close()


# - - - - - - - - - - - - - - - -
# BOLETAS (procesamiento mensual desde el frontend)
# - - - - - - - - - - - - - - - -

PERIODO_RE = re.compile(r"^\d{4}-\d{2}$")


@app.post("/boletas/upload")
async def upload_boletas(
    background_tasks: BackgroundTasks,
    periodo: str = Form(..., description="Periodo de las boletas (YYYY-MM)"),
    files: list[UploadFile] = File(..., description="PDFs de sector"),
    userdata: dict = Depends(get_current_user),
):
    """
    Recibe los PDFs de sector, los procesa en background (sube a R2 + registra
    cada boleta en la BD) y devuelve un job_id para seguir el progreso.
    """
    check_admin(userdata["role"])

    if not PERIODO_RE.match(periodo):
        raise HTTPException(
            status_code=400, detail="periodo debe tener formato YYYY-MM")

    # Volcar cada PDF a disco en chunks (sin cargar el archivo entero en RAM)
    # y procesarlos despues desde ahi: en 512MB no entran todos los PDFs juntos.
    job_id = str(uuid.uuid4())
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"boletas_{job_id}_"))
    sector_paths: dict[str, str] = {}
    try:
        for f in files:
            if not (f.filename or "").lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400,
                    detail=f"'{f.filename}' no es un PDF")
            sector = Path(f.filename).stem
            dest = tmp_dir / f"{sector}.pdf"
            with open(dest, "wb") as out:
                shutil.copyfileobj(f.file, out, length=1024 * 1024)
            if dest.stat().st_size == 0:
                dest.unlink()
                continue
            sector_paths[sector] = str(dest)
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    if not sector_paths:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="No se recibieron PDFs")

    boletas_processor.create_job(job_id, periodo)
    background_tasks.add_task(
        boletas_processor.process_period, job_id, periodo, sector_paths, str(tmp_dir))

    return {
        "job_id": job_id,
        "estado": "procesando",
        "periodo": periodo,
        "sectores": list(sector_paths.keys()),
    }


@app.get("/boletas/status/{job_id}")
def boletas_status(job_id: str, userdata: dict = Depends(get_current_user)):
    """Progreso de un job de procesamiento de boletas."""
    check_admin(userdata["role"])

    row = boletas_processor.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job no encontrado")

    return {
        "job_id": str(row[0]),
        "periodo": row[1],
        "estado": row[2],
        "total": row[3],
        "procesadas": row[4],
        "error": row[5],
    }


@app.get("/boletas/periodos")
def boletas_periodos(userdata: dict = Depends(get_current_user)):
    """Lista de periodos con su estado de publicacion y cantidad de boletas."""
    check_admin(userdata["role"])
    rows = boletas_processor.listar_periodos()
    return [
        {
            "periodo": r[0],
            "publicado": r[1],
            "publicado_at": str(r[2]) if r[2] else None,
            "boletas": r[3],
        }
        for r in rows
    ]


@app.post("/boletas/publish")
def boletas_publish(data: PeriodoPublish, userdata: dict = Depends(get_current_user)):
    """Publica un periodo: a partir de ahi los socios ven sus boletas de ese mes."""
    check_admin(userdata["role"])
    if not PERIODO_RE.match(data.periodo):
        raise HTTPException(status_code=400, detail="periodo debe tener formato YYYY-MM")
    if not boletas_processor.publicar_periodo(data.periodo):
        raise HTTPException(
            status_code=404,
            detail="Periodo no encontrado (¿se proceso la subida?)")
    return {"periodo": data.periodo, "publicado": True}


@app.post("/boletas/unpublish")
def boletas_unpublish(data: PeriodoPublish, userdata: dict = Depends(get_current_user)):
    """Despublica un periodo (rollback): deja de servirse a los socios."""
    check_admin(userdata["role"])
    if not PERIODO_RE.match(data.periodo):
        raise HTTPException(status_code=400, detail="periodo debe tener formato YYYY-MM")
    if not boletas_processor.despublicar_periodo(data.periodo):
        raise HTTPException(status_code=404, detail="Periodo no encontrado")
    return {"periodo": data.periodo, "publicado": False}

