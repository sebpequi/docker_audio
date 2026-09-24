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
    exec python3.10 worker_audio.py
else
    echo "[entrypoint] RUN_MODE=server: servidor de pruebas en puerto 8000..."
    # Se invoca como modulo (-m) para IGNORAR el shebang del script uvicorn
    # instalado por pip, que puede quedar apuntando a un interprete que no
    # existe en la imagen final si el sistema resuelve python3 de forma
    # ambigua durante el build (ver nota en el Dockerfile).
    exec python3.10 -m uvicorn server_audio:app --host 0.0.0.0 --port 8000
fi
