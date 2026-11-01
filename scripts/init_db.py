#!/usr/bin/env python3
"""
Aplica app/schema.sql contra la base configurada en DATABASE_URL.
Idempotente. Uso:

    python -m scripts.init_db
"""
from pathlib import Path

from app.database import db_conn, pool

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "app" / "schema.sql"


def main() -> None:
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    # Sin esto, Python 3.14 tira un error al apagarse con el pool todavia abierto.
    pool.close()
    print("Esquema aplicado correctamente.")


if __name__ == "__main__":
    main()
