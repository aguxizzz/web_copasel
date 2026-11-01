## Sistema de Pagos de Boletas

**Rol:** Backend (API de pagos, integración con Mercado Pago)  
**Stack:** Python/FastAPI, PostgreSQL
**Tiempo:** 2 meses

### El problema
Empresa con 1500 clientes necesitaba evitar la impresion de boletas
manualmente y ofrecer pagos remotos de las mismas.

### Lo que construí
- Descarga de boletas para imprimir
- Pago de boletas tanto en mercadopago como en macroclick
- Visualizacion de datos del cliente
- Sistema de importacion de boletas y base de datos desde su servidor

### Screenshots
Inicio de Sesion
<img width="1283" height="796" alt="login" src="https://github.com/user-attachments/assets/cd287dfb-b771-41ef-bdba-b0624d0597e8" />
Dashboard de cliente
<img width="1288" height="795" alt="dashboard" src="https://github.com/user-attachments/assets/8e718c42-fa35-409c-921a-19436cccfcb1" />
Dashboard de administrador
<img width="1238" height="732" alt="admindashboard" src="https://github.com/user-attachments/assets/29fa0fe7-57ec-4ba7-9cd0-8deac5a28e48" />


### Tests
Necesitan Docker: levantan un Postgres descartable y nunca tocan la base del `.env`.
Los PDFs de R2 se reemplazan por un bucket falso en memoria.

```bash
pip install -r requirements-dev.txt
pytest
```

---
Contacto: agustinciancio3@gmail.com!

