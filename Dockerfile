# =============================================================================
# Imagen: audio (Chatterbox Multilingual, MIT)
# El modelo va HORNEADO dentro de la imagen -- no depende de ningun volumen
# de red, se puede correr en CUALQUIER datacenter/GPU disponible.
# =============================================================================
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Bug conocido de empaquetado en imagenes base de CUDA/Ubuntu: el archivo
# statoverride hereda una referencia al grupo 'messagebus' que no existe
# en esta capa -- CUALQUIER dpkg --configure posterior falla con esto.
RUN rm -f /var/lib/dpkg/statoverride

# IMPORTANTE: ya NO instalamos 'python3-pip' de apt. Ese paquete instala
# pip atado al python3 que apt considere "default" en este momento del
# build, que puede NO ser python3.10 (fue la causa real de "No module
# named uvicorn": pip3 instalo los paquetes en OTRO interprete). Bootstrapeamos
# pip manualmente, amarrado EXPLICITAMENTE a python3.10, mas abajo.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git python3.10 curl libsndfile1 ca-certificates \
    && apt-get install -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Bootstrap de pip directo desde PyPA, amarrado EXPLICITAMENTE a python3.10.
# Esto es lo que garantiza que 'python3.10 -m pip' instale paquetes en el
# MISMO interprete que usamos despues para correr la app.
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.10

WORKDIR /app

# A partir de aqui, SIEMPRE 'python3.10 -m pip install' -- nunca 'pip3' o
# 'pip' sueltos, para no volver a caer en la misma ambiguedad.
# se mexclaron los procesos de instalacion para ver si pip maneja bien las versiones
# de las dependencias y así evitar conflictos
RUN python3.10 -m pip install --no-cache-dir --ignore-installed \
        "huggingface_hub[cli]>=1.3.0,<2.0" \
        git+https://github.com/resemble-ai/chatterbox.git

COPY requirements.txt /app/requirements.txt
# --ignore-installed: fuerza a pip a instalar su PROPIA copia de cada
# paquete en /usr/local/lib/python3.10/dist-packages (la unica ruta que
# SI persiste en runtime), en vez de confiar en paquetes que el sistema
# operativo ya cree tener instalados en otra ruta (/usr/lib/python3/dist-packages,
# especifica de Debian/Ubuntu, que no sobrevive al contenedor final). Esto
# fue exactamente la causa real de "No module named six": pip vio que 'six'
# ya estaba "satisfecho" por una copia del sistema y nunca instalo la suya.
RUN python3.10 -m pip install --no-cache-dir --ignore-installed -r /app/requirements.txt

# Prueba de humo: si falta cualquier dependencia de Chatterbox, el build
# falla AQUI (pod CPU barato), antes de bajar el modelo pesado.
RUN python3.10 -c "from chatterbox.mtl_tts import ChatterboxMultilingualTTS; print('chatterbox import ok')"

# --- Modelo horneado en la imagen ---
RUN hf download ResembleAI/chatterbox --local-dir /app/models/chatterbox_v3

COPY audio_core.py server_audio.py worker_audio.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

ENV CHATTERBOX_MODEL_DIR=/app/models/chatterbox_v3
ENV OUTPUT_DIR=/app/output

ENTRYPOINT ["/app/entrypoint.sh"]
