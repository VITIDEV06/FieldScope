# Fieldscope — Customer Installed Base Intelligence

Versión final consolidada para la demo con inferencia **QVAC on-device**.

## Qué conserva

- Captura conversacional.
- Extracción estructurada cliente/ubicación/equipamiento/cantidad/fabricante/modelo/edad.
- Customer 360.
- Geografía Región → País → Ciudad → Cliente.
- Panorama con agregaciones por unidades.
- Estados Confirmado / Reportado / Estimado / Desconocido.
- Eliminación individual de observaciones sin borrar equipamiento.
- QVAC local sin ruta de inferencia remota.

## Correcciones finales

- Barrera contra `manufacturer == model`.
- Barrera conservadora contra copiar una edad entre modalidades.
- Migraciones SQLite idempotentes para bases antiguas.
- Estado de validación también a nivel de Equipment.
- Detección de duplicados evita fusionar modelos conocidos distintos.
- Respuestas explícitas `no sé` se guardan como información incompleta sin insistir.
- `run.py` usa el puerto 8001 y no activa reload de Uvicorn.
- Errores API del frontend muestran correctamente detalles JSON.
- @qvac/sdk fijado en 0.18.1.

## Importante al instalar esta versión

No borres tu carpeta actual. Copia/reemplaza los archivos de código del ZIP sobre la carpeta `customer-installed-base`.

Conserva sin reemplazar:

- `backend\venv\`
- `backend\node_modules\`
- `backend\data\installed_base.db`
- `backend\data\qvac-cache\`
- cualquier `.gguf` QVAC ya descargado
- `backend\.env` si ya existe y funciona

## Arranque

Backend:

```powershell
cd "C:\Users\jorge\OneDrive\Documentos\customer-installed-base\backend"
venv\Scripts\activate
$env:QVAC_RPC_INIT_TIMEOUT_MS="120000"
python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

Frontend, en otra terminal:

```powershell
cd "C:\Users\jorge\OneDrive\Documentos\customer-installed-base\frontend"
python -m http.server 5500
```

Abrir `http://127.0.0.1:5500`.
