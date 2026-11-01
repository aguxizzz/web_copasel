-- Tablas que existen en la base pero no estan en app/schema.sql.
-- Mismas columnas y tipos que produccion.
CREATE TABLE socios (
    ncon      INTEGER PRIMARY KEY,
    nombre    TEXT,
    cuit      BIGINT,
    dni       INTEGER,
    medidor   TEXT,
    domicilio TEXT
);

CREATE TABLE consumos (
    consumo_id SERIAL PRIMARY KEY,
    ncon       INTEGER,
    fecha      DATE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    estado     INTEGER
);
