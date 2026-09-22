#!/usr/bin/env bash
# =============================================================================
# entrypoint.sh — imagen audio
# RUN_MODE=server (default, pruebas manuales) -> uvicorn puerto 8000
# RUN_MODE=worker (produccion real con n8n) -> loop automatico
# Las variables las inyecta RunPod directo al pod (Environment Variables en
# la UI/API), YA NO hace falta un archivo .env dentro del contenedor.
# =============================================================================
set -e

RUN_MODE="${RUN_MODE:-server}"
cd /app

if [ "$RUN_MODE" = "worker" ]; then
    echo "[entrypoint] RUN_MODE=worker: produccion automatica..."
    exec python3 worker_audio.py
else
    echo "[entrypoint] RUN_MODE=server: servidor de pruebas en puerto 8000..."
    exec uvicorn server_audio:app --host 0.0.0.0 --port 8000
fi
