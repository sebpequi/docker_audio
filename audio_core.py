"""
audio_core.py — imagen "audio" (Chatterbox Multilingual, MIT)
----------------------------------------------------------------
Version recortada: solo voz. La musica (YuE2 + Demucs + RVC) vive en su
propia imagen ("musica"), con su propio audio_core equivalente, para que
esta imagen no cargue dependencias pesadas que no usa.

El modelo esta HORNEADO en la imagen (ver Dockerfile), no depende de un
volumen de red -- por eso CHATTERBOX_MODEL_DIR apunta dentro de /app.
"""

import os
import logging

import torch
import torchaudio as ta
import boto3
from botocore.client import Config

log = logging.getLogger("audio_core")

CHATTERBOX_MODEL_DIR = os.environ.get("CHATTERBOX_MODEL_DIR", "/app/models/chatterbox_v3")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/app/output")

S3_ENDPOINT_URL    = os.environ["S3_ENDPOINT_URL"]
S3_BUCKET          = os.environ["S3_BUCKET"]
S3_ACCESS_KEY      = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY      = os.environ["S3_SECRET_KEY"]
S3_PUBLIC_BASE_URL = os.environ["S3_PUBLIC_BASE_URL"]

os.makedirs(OUTPUT_DIR, exist_ok=True)

_device = "cuda" if torch.cuda.is_available() else "cpu"
_modelo = None

_s3_client = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT_URL,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    config=Config(signature_version="s3v4"),
)


def cargar_modelo():
    """Carga el modelo en VRAM una sola vez. Llamalo en el startup del pod."""
    global _modelo
    if _modelo is None:
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        log.info("Cargando Chatterbox Multilingual desde %s ...", CHATTERBOX_MODEL_DIR)
        _modelo = ChatterboxMultilingualTTS.from_local(CHATTERBOX_MODEL_DIR, device=_device)
        log.info("Modelo cargado en %s.", _device)
    return _modelo


def generar_voz(
    texto: str,
    idioma: str,
    ruta_referencia_voz: str,
    salida_path: str,
    parametros_extra: dict | None = None,
):
    """
    Genera un WAV a partir de texto + audio de referencia (voice cloning).
    ruta_referencia_voz: en esta version sin volumen compartido, esta ruta
    debe ser una URL descargable (S3/R2 publica) -- ya no hay volumen local
    de donde leerla directo. Ver descargar_referencia() abajo.
    """
    modelo = cargar_modelo()
    kwargs = dict(parametros_extra or {})

    wav = modelo.generate(
        texto,
        language_id=idioma,
        audio_prompt_path=ruta_referencia_voz,
        **kwargs,
    )
    ta.save(salida_path, wav, modelo.sr)
    return salida_path


def descargar_referencia(url: str, destino_path: str) -> str:
    """
    Sin volumen de red compartido, las voces de referencia (avatar_voces_referencia)
    ahora deben vivir en S3/R2 tambien, no en un path local.
    """
    import requests
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(destino_path, "wb") as f:
        f.write(resp.content)
    return destino_path


REFERENCIAS_CACHE_DIR = os.path.join(OUTPUT_DIR, "_cache_referencias")
os.makedirs(REFERENCIAS_CACHE_DIR, exist_ok=True)
_referencias_cache = {}  # url -> ruta local, vive mientras el pod este arriba


def obtener_referencia_cacheada(url: str) -> str:
    """
    Descarga el audio de referencia SOLO si no se ha descargado ya en esta
    corrida del pod. El pod procesa muchas tareas del mismo avatar seguidas
    dentro de su ventana de 2 horas -- esto evita repetir la misma descarga
    task tras task. El cache vive y muere con el pod (no persiste entre
    corridas, pero no hace falta: dura lo que dura el trabajo del dia).
    """
    if url in _referencias_cache and os.path.isfile(_referencias_cache[url]):
        return _referencias_cache[url]

    nombre_archivo = f"ref_{abs(hash(url))}.wav"
    destino = os.path.join(REFERENCIAS_CACHE_DIR, nombre_archivo)
    descargar_referencia(url, destino)
    _referencias_cache[url] = destino
    return destino


def construir_s3_key(id_tarea: str, filename: str, id_proyecto: str = None, id_avatar: str = None) -> str:
    partes = [p for p in [id_proyecto, id_avatar, id_tarea, "elementos"] if p]
    return "/".join(partes) + f"/{filename}"


def subir_y_destruir(local_path: str, s3_key: str) -> str:
    """Sube a S3/R2 y borra el archivo local (el contenedor es efimero de todas formas,
    pero mantenemos la disciplina para no acumular disco durante una corrida larga)."""
    _s3_client.upload_file(local_path, S3_BUCKET, s3_key)
    os.remove(local_path)
    return f"{S3_PUBLIC_BASE_URL}/{s3_key}"
