-- ----------------------------------------------------------------------------
-- Esquema para el procesamiento de boletas (reemplaza al GitHub Action).
-- Idempotente: se puede correr varias veces sin romper nada.
-- ----------------------------------------------------------------------------

-- Una fila por (socio, periodo). Guarda en qué PDF de sector y página está la
-- boleta del socio + el importe extraído. Los PDFs de sector viven en R2.
CREATE TABLE IF NOT EXISTS boletas (
    ncon        INTEGER     NOT NULL,
    periodo     TEXT        NOT NULL,          -- 'YYYY-MM'
    sector      TEXT        NOT NULL,          -- stem del PDF subido, p.ej. 'SECTOR1'
    pagina      INTEGER     NOT NULL,          -- 1-indexed dentro del PDF de sector
    importe     NUMERIC(12, 2),               -- NULL si no se pudo extraer
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ncon, periodo)
);

CREATE INDEX IF NOT EXISTS idx_boletas_ncon_periodo
    ON boletas (ncon, periodo DESC);

-- Jobs de procesamiento (para que el front consulte el progreso por polling).
CREATE TABLE IF NOT EXISTS boleta_jobs (
    id          UUID        PRIMARY KEY,
    periodo     TEXT        NOT NULL,
    estado      TEXT        NOT NULL DEFAULT 'procesando',  -- procesando | listo | error
    total       INTEGER     NOT NULL DEFAULT 0,
    procesadas  INTEGER     NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Estado de publicación por periodo. /download-pdf y /importe solo sirven el
-- ultimo periodo con publicado = TRUE, asi una subida de prueba o a medias
-- nunca tapa al periodo en vivo. Publicar es una accion explicita del admin.
CREATE TABLE IF NOT EXISTS boleta_periodos (
    periodo      TEXT        PRIMARY KEY,
    publicado    BOOLEAN     NOT NULL DEFAULT FALSE,
    publicado_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Backfill idempotente: registrar los periodos que ya existen en boletas.
INSERT INTO boleta_periodos (periodo)
SELECT DISTINCT periodo FROM boletas
ON CONFLICT (periodo) DO NOTHING;

-- Bootstrap unico: si todavia no hay ningun periodo publicado, publicar el mas
-- reciente (los datos actuales se asumen consistentes en la migracion). Una vez
-- que exista al menos un publicado, esta sentencia no hace nada.
UPDATE boleta_periodos
SET publicado = TRUE, publicado_at = now()
WHERE periodo = (SELECT MAX(periodo) FROM boleta_periodos)
  AND NOT EXISTS (SELECT 1 FROM boleta_periodos WHERE publicado);
