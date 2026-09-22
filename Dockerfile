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
# en esta capa -- CUALQUIER dpkg --configure posterior falla con esto,
# sin importar que paquete estes instalando (tu log confirma que revienta
# en el PRIMER apt-get install, antes de siquiera llegar a ffmpeg). Se
# limpia el archivo antes de instalar nada.
RUN rm -f /var/lib/dpkg/statoverride

# Ademas, por las dudas: si 'ffmpeg' se instala en la MISMA linea que otros
# paquetes, su dependencia dbus puede generar el mismo tipo de conflicto.
# Lo dejamos separado en su propio paso como practica segura.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git python3.10 python3-pip libsndfile1 curl ca-certificates \
    && apt-get install -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip3 install -U "huggingface_hub[cli]" \
    && pip3 install git+https://github.com/resemble-ai/chatterbox.git

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# --- Modelo horneado en la imagen ---
# ResembleAI/chatterbox no esta gated hasta donde sabemos; si al construir
# esto te pide login, usa el mismo patron de --secret que para InfiniteTalk.
RUN hf download ResembleAI/chatterbox --local-dir /app/models/chatterbox_v3

COPY audio_core.py server_audio.py worker_audio.py entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

ENV CHATTERBOX_MODEL_DIR=/app/models/chatterbox_v3
ENV OUTPUT_DIR=/app/output

ENTRYPOINT ["/app/entrypoint.sh"]
