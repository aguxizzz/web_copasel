"""
Aviso mensual de "el monto del mes siguiente se esta preparando".

El admin configura un dia del mes (ej. 27). Desde ese dia, el aviso queda activo
hasta que se publique un periodo: publicar es lo que indica que ya esta el monto
nuevo, sin depender de como se nombre el periodo.
"""
from datetime import datetime, timedelta, timezone

from app.database import db_conn

# Argentina no tiene horario de verano. Offset fijo para no depender de tzdata,
# que no siempre esta instalado en la imagen del deploy.
TZ_ARGENTINA = timezone(timedelta(hours=-3))

CLAVE_DIA = "dia_preparacion"


def inicio_ventana(ahora: datetime, dia: int) -> datetime:
    """Ultima vez que fue `dia` a las 00:00 (hora argentina), incluido hoy."""
    local = ahora.astimezone(TZ_ARGENTINA)
    inicio = local.replace(day=dia, hour=0, minute=0, second=0, microsecond=0)
    if local.day >= dia:
        return inicio
    # Todavia no llego el dia este mes: la ventana abrio el mes pasado.
    # `dia` va de 1 a 28, asi que existe en todos los meses.
    return (inicio.replace(day=1) - timedelta(days=1)).replace(day=dia)


def _mes_siguiente(fecha: datetime) -> str:
    primero_del_siguiente = (fecha.replace(day=1) + timedelta(days=32)).replace(day=1)
    return primero_del_siguiente.strftime("%Y-%m")


def calcular_estado(
    ahora: datetime, dia: int | None, ultima_publicacion: datetime | None
) -> dict:
    """Decide si el aviso esta activo. Sin base de datos, para poder testearlo."""
    if dia is None:
        return {"dia": None, "preparando": False, "mes": None}

    inicio = inicio_ventana(ahora, dia)
    if ultima_publicacion is not None and ultima_publicacion >= inicio:
        return {"dia": dia, "preparando": False, "mes": None}
    return {"dia": dia, "preparando": True, "mes": _mes_siguiente(inicio)}


def obtener_dia() -> int | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT valor FROM configuracion WHERE clave = %s", (CLAVE_DIA,))
            row = cur.fetchone()
    return int(row[0]) if row else None


def guardar_dia(dia: int | None) -> None:
    """Guarda el dia; con None desactiva el aviso."""
    with db_conn() as conn:
        with conn.cursor() as cur:
            if dia is None:
                cur.execute("DELETE FROM configuracion WHERE clave = %s", (CLAVE_DIA,))
            else:
                cur.execute(
                    "INSERT INTO configuracion (clave, valor) VALUES (%s, %s) "
                    "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor",
                    (CLAVE_DIA, str(dia)),
                )
        conn.commit()


def _ultima_publicacion() -> datetime | None:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(publicado_at) FROM boleta_periodos WHERE publicado")
            return cur.fetchone()[0]


def estado_actual() -> dict:
    return calcular_estado(
        datetime.now(timezone.utc), obtener_dia(), _ultima_publicacion()
    )
