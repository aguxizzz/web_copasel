# ----------------------------
# FastAPI Water Consumption Management API
# Manages: socios (users), conexiones (connections), consumos (consumption readings)
# ----------------------------

# ----------------------------
# IMPORTS
# ----------------------------
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, APIRouter
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, validator, EmailStr
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from fastapi.responses import FileResponse
import json
from fastapi.staticfiles import StaticFiles
# # #

# Cargar .env desde la raíz del proyecto (solo si existe)
# En producción (Render), las variables están en el panel de Environment
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

from app.database import db_conn, pool

# ----------------------------
# CONFIGURATION & CONSTANTS
# ----------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
SECRET_NCON = int(os.getenv("SECRET_NCON", "9997"))
PASSWORD = os.getenv("ADMIN_PASSWORD", "change-me-in-production")
PDF_DIR = Path(os.getenv("PDF_DIR", "app/static/pdfs"))
IMPORTES_FILE = Path(os.getenv("IMPORTES_FILE", "app/static/montos/payments.json"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
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
    print(allow_origins)
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

os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
    dni: int = Field(..., min_length=7, max_length=9, description="DNI nuevo")

    @validator("dni")
    def solo_digitos(cls, v: str):
        if not v.isdigit():
            raise ValueError("Debe contener solo digitos")
        return v
    
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


security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Extract and validate JWT token from Authorization header (Bearer token).
    Compatible with frontend storing token in localStorage and sending it as:
    Authorization: Bearer <token>
    Returns the user's ncon (connection number) if valid.
    """
    token = decode_token(credentials.credentials)

    sub = token.get("sub")
    role = token.get("role")

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


# - - - - - - - - - - - - - - - -
# USER ENDPOINTS
# - - - - - - - - - - - - - - - -

@app.get("/download-pdf")
def download_user_pdf(current_user: dict = Depends(get_current_user)):
    if current_user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    ncon = current_user["ncon"]
    filename = f"ncon_{ncon}.pdf"
    file_path = PDF_DIR / filename
    
    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"PDF not found for user {current_user['ncon']} (expected: {filename})"
        )
    
    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=f"boleta_{ncon}.pdf",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
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
    ncon = str(current_user["ncon"])
    try:
        with open(IMPORTES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Get the payments for this specific user
        user_payments = data['payments'].get(ncon, [])
        
        # If no payments found, return 0 or appropriate message
        if not user_payments:
            return {"importe": 0, "message": "No payments found for this user"}
        
        # Get the latest payment amount
        latest_payment = user_payments[-1]  # Most recent payment
        amount = latest_payment.get('amount_numeric', 0)
        
        return {"importe": float(amount)}
        
    except FileNotFoundError:
        return {"importe": 0, "message": "Payments file not found"}
    except KeyError as e:
        return {"importe": 0, "message": f"Invalid data structure: {e}"}
    except Exception as e:
        return {"error": str(e)}

    # rows = fetch_rangos()

    # rangos = {}
    # for rango, importe in rows:
    #     rangos[rango] = importe

    # print(rangos)

    # rows = fetch_consumos(ncon, limit=2)

    # if not rows:
    #     raise HTTPException(404, "Usuario no encontrado")

    # consumo_actual = rows[0][0]
    # consumo_pasado = rows[1][0]
    # consumo = consumo_actual - consumo_pasado

    # importe = calcular_importe(consumo, rangos)
    # return importe


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


def init_test():
    """Helper function: create consumos table if it doesn't exist."""

    cursor = None
    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
            SHOW search_path;
            """)
        except Exception as e:
            return f"Error : {e}"
        finally:
            if cursor:
                cursor.close()
            conn.commit()


@app.get("/debug-conexion")
def debug_conexion():
    """Debug endpoint: return database info, search path, and table statistics."""
    cursor = None
    with db_conn() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT current_database(), current_user, version()")
            db_info = cursor.fetchone()

            cursor.execute("SHOW search_path")
            search_path = cursor.fetchone()[0]

            cursor.execute("""
                SELECT
                    table_catalog,
                    table_schema,
                    table_name,
                    table_type
                FROM information_schema.tables
                WHERE table_name LIKE '%socio%'
                ORDER BY table_schema, table_name
            """)
            tablas_socio = cursor.fetchall()

            todas_tablas = []
            for _, schema, tabla, _ in tablas_socio:
                cursor.execute(f"""
                    SELECT COUNT(*) as count,
                           COALESCE(MAX(id_socio), 0) as max_id
                    FROM {schema}.{tabla}
                """)
                stats = cursor.fetchone()
                todas_tablas.append({
                    "schema": schema,
                    "tabla": tabla,
                    "registros": stats[0],
                    "max_id": stats[1]
                })

            return {
                "base_datos_actual": db_info[0],
                "usuario": db_info[1],
                "search_path": search_path,
                "tablas_socio_encontradas": tablas_socio,
                "estadisticas_tablas": todas_tablas
            }
        finally:
            if cursor:
                cursor.close()


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

