from psycopg_pool import ConnectionPool
import psycopg
from psycopg import OperationalError
from contextlib import contextmanager
import os
import time
from pathlib import Path
from dotenv import load_dotenv

# Cargar .env desde la raíz del proyecto (solo si existe)
# En producción (Render), las variables están en el panel de Environment
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

DATABASE_URL = os.getenv('DATABASE_URL')

# Retry config para Neon cold start (SSL handshake, conexión dormida)
MAX_RETRIES = 3
RETRY_DELAY_SEC = 1.5

pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=10,
    open=True,
    check=ConnectionPool.check_connection,
)


@contextmanager
def db_conn():
    """
    Context manager para conexiones desde el pool.
    Reintenta en OperationalError (cold start Neon).
    """
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with pool.connection() as conn:
                yield conn
            return
        except OperationalError as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY_SEC)
            else:
                raise last_err from e
