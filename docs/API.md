# Contrato de la API — Copasel backend

Base URL: la del backend (local `http://127.0.0.1:8000`, prod en Render).

Autenticación: todos los endpoints protegidos esperan el header
`Authorization: Bearer <access_token>`. El token se obtiene en el login y dura 2 h.

---

## Autenticación

### `POST /login` — login de socio
Body JSON:
```json
{ "ncon": "345" }
```
Respuesta:
```json
{ "access_token": "<jwt>", "token_type": "bearer" }
```

### `POST /admin-login` — login de admin
Body JSON:
```json
{ "password": "<contraseña de admin>" }
```
Respuesta: igual que arriba, con rol admin.

> `POST /token` existe solo para el botón "Authorize" de `/docs`. El frontend no lo usa.

---

## Socio (token de socio)

### `GET /importe`
Importe de la boleta más reciente del socio.
```json
{ "importe": 12345.67 }
```
Si no hay boleta: `{ "importe": 0, "message": "No hay importe para este socio" }`.

### `GET /download-pdf`
Devuelve la boleta más reciente del socio como **PDF** (`application/pdf`,
1 página). 404 si el socio no tiene boleta.

### `GET /medidor` · `GET /nombre` · `GET /last-consumos?limit=5`
Datos del socio (número de medidor, nombre, últimos consumos).

---

## Boletas — carga mensual (token de admin)

Flujo: el trabajador sube los PDFs de sector → el backend procesa en background
→ el front consulta el estado hasta que termina → el admin **publica** el período.

> Importante: un período recién subido queda como **borrador** (`publicado: false`)
> y los socios NO lo ven todavía. Recién cuando el admin lo publica, `/download-pdf`
> e `/importe` empiezan a servir ese mes. Así una subida de prueba o a medias nunca
> tapa al período en vivo. Los socios siempre ven el último período **publicado**.

### `POST /boletas/upload`
`multipart/form-data`:
- `periodo`: string `YYYY-MM` (ej. `2026-06`)
- `files`: uno o varios PDFs de sector (el nombre del archivo, sin `.pdf`, se usa
  como nombre de sector: `SECTOR1.pdf` → `SECTOR1`)

Respuesta inmediata:
```json
{
  "job_id": "f1e2...",
  "estado": "procesando",
  "periodo": "2026-06",
  "sectores": ["SECTOR1", "SECTOR2", "SECTOR3", "SECTOR4"]
}
```

Ejemplo:
```js
const fd = new FormData();
fd.append("periodo", "2026-06");
for (const file of sectorFiles) fd.append("files", file);

const res = await fetch(`${API}/boletas/upload`, {
  method: "POST",
  headers: { Authorization: `Bearer ${adminToken}` },
  body: fd,
});
const { job_id } = await res.json();
```

### `GET /boletas/status/{job_id}`
Progreso del procesamiento. Pollear cada ~2-3 s hasta `estado != "procesando"`.
```json
{
  "job_id": "f1e2...",
  "periodo": "2026-06",
  "estado": "procesando",   // procesando | listo | error
  "total": 1289,            // páginas totales
  "procesadas": 540,        // páginas procesadas
  "error": null             // mensaje si estado == "error"
}
```

Ejemplo de polling:
```js
async function esperarFin(jobId, adminToken) {
  while (true) {
    const r = await fetch(`${API}/boletas/status/${jobId}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    const j = await r.json();
    if (j.estado === "listo") return j;
    if (j.estado === "error") throw new Error(j.error);
    await new Promise((s) => setTimeout(s, 2500));
  }
}
```

### `GET /boletas/periodos`
Lista los períodos con su estado, para que el admin vea cuál publicar.
```json
[
  { "periodo": "2026-06", "publicado": false, "publicado_at": null, "boletas": 1289 },
  { "periodo": "2026-05", "publicado": true,  "publicado_at": "2026-06-01 12:00:00+00", "boletas": 1289 }
]
```

### `POST /boletas/publish`
Publica un período: a partir de ahí los socios ven sus boletas de ese mes.
```json
{ "periodo": "2026-06" }
```
Respuesta: `{ "periodo": "2026-06", "publicado": true }`. 404 si el período no
existe (no se procesó ninguna subida para él).

### `POST /boletas/unpublish`
Despublica un período (rollback): deja de servirse a los socios.
```json
{ "periodo": "2026-06" }
```

> Flujo completo del mes: `upload` → pollear `status` hasta `listo` →
> (opcional) verificar → `publish`. Para corregir un error: `unpublish`,
> volver a `upload`, `publish`.

---

## Admin — sincronización de socios (server-to-server)

### `POST /sync-socio`
Crea/actualiza un socio. Se autentica con header `x-api-key: <API_KEY>`
(no con token JWT). Lo usa el servidor base, no el frontend.

### `POST /update-dni`
Actualiza el DNI de un socio (token de admin).
```json
{ "ncon": 345, "dni": 30111222 }
```
