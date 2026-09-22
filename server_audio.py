"""
server_audio.py — imagen "audio"
-----------------------------------
Uso: llamadas MANUALES/puntuales via HTTP. La produccion automatica real
la conduce worker_audio.py (mismo contenedor, RUN_MODE=worker), que llama
las funciones de audio_core.py directamente para respetar la hora limite.
"""

import os
import logging

import requests
from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from pydantic import BaseModel

import audio_core

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("server_audio")

app = FastAPI()

TOKEN_ENTRADA_POD = os.environ.get("TOKEN_ENTRADA_POD", "GUDESTRIK_ENTRADA_2026")
TOKEN_SALIDA_N8N  = os.environ.get("TOKEN_SALIDA_N8N", "GUDESTRIK_SALIDA_2026")
N8N_WEBHOOK_URL   = os.environ["N8N_WEBHOOK_URL"]
WEBHOOK_ARRANQUE  = os.environ["WEBHOOK_ARRANQUE"]


@app.on_event("startup")
def avisar_arranque():
    audio_core.cargar_modelo()
    try:
        headers = {"Authorization": f"Bearer {TOKEN_SALIDA_N8N}"}
        requests.post(WEBHOOK_ARRANQUE, json={"pod": "audio", "estado": "listo"}, headers=headers, timeout=15)
        log.info("Aviso de arranque enviado a n8n.")
    except Exception as e:
        log.error("Error avisando arranque a n8n: %s", e)


def verificar_seguridad(authorization: str):
    if authorization != f"Bearer {TOKEN_ENTRADA_POD}":
        raise HTTPException(status_code=401, detail="Acceso denegado: Token invalido")


def notificar_n8n(payload: dict):
    headers = {"Authorization": f"Bearer {TOKEN_SALIDA_N8N}"}
    try:
        requests.post(N8N_WEBHOOK_URL, json=payload, headers=headers, timeout=15)
    except Exception as e:
        log.error("Error notificando a n8n: %s", e)


@app.get("/ping")
def ping():
    return {"estado": "ok", "mensaje": "El pod de audio esta listo para recibir tareas"}


class TareaVozSingle(BaseModel):
    id_tarea: str
    id_avatar: str
    id_proyecto: str | None = None
    texto: str
    idioma: str = "es"
    referencia_audio_url: str  # URL publica en S3/R2 (ya no hay volumen local compartido)
    parametros_extra: dict = {}


def _procesar_voz_single(tarea: TareaVozSingle):
    filename = f"{tarea.id_tarea}.wav"
    local_path = os.path.join(audio_core.OUTPUT_DIR, filename)
    try:
        ref_path = audio_core.obtener_referencia_cacheada(tarea.referencia_audio_url)
        audio_core.generar_voz(tarea.texto, tarea.idioma, ref_path, local_path, tarea.parametros_extra)

        s3_key = audio_core.construir_s3_key(tarea.id_tarea, filename, tarea.id_proyecto, tarea.id_avatar)
        url = audio_core.subir_y_destruir(local_path, s3_key)
        notificar_n8n({
            "pod": "audio", "endpoint": "voz/single", "id_tarea": tarea.id_tarea,
            "estado": "terminada", "url": url,
        })
    except Exception as e:
        log.exception("Error en voz/single para tarea %s", tarea.id_tarea)
        notificar_n8n({
            "pod": "audio", "endpoint": "voz/single", "id_tarea": tarea.id_tarea,
            "estado": "error", "mensaje_error": str(e),
        })


@app.post("/voz/single")
def generar_voz_single(tarea: TareaVozSingle, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    verificar_seguridad(authorization)
    background_tasks.add_task(_procesar_voz_single, tarea)
    return {"mensaje": f"Tarea {tarea.id_tarea} de voz recibida, procesando..."}


class LineaDialogo(BaseModel):
    personaje: str
    texto: str
    idioma: str = "es"
    referencia_audio_url: str


class TareaVozDialogo(BaseModel):
    id_tarea: str
    id_avatar: str
    id_proyecto: str | None = None
    dialogo: list[LineaDialogo]
    parametros_extra: dict = {}


def _procesar_voz_dialogo(tarea: TareaVozDialogo):
    urls = []
    try:
        for i, linea in enumerate(tarea.dialogo, start=1):
            filename = f"{i:02d}_{linea.personaje}.wav"
            local_path = os.path.join(audio_core.OUTPUT_DIR, filename)

            ref_path = audio_core.obtener_referencia_cacheada(linea.referencia_audio_url)
            audio_core.generar_voz(linea.texto, linea.idioma, ref_path, local_path, tarea.parametros_extra)

            s3_key = audio_core.construir_s3_key(tarea.id_tarea, filename, tarea.id_proyecto, tarea.id_avatar)
            urls.append(audio_core.subir_y_destruir(local_path, s3_key))

        notificar_n8n({
            "pod": "audio", "endpoint": "voz/dialogo", "id_tarea": tarea.id_tarea,
            "estado": "terminada", "urls": urls,
        })
    except Exception as e:
        log.exception("Error en voz/dialogo para tarea %s", tarea.id_tarea)
        notificar_n8n({
            "pod": "audio", "endpoint": "voz/dialogo", "id_tarea": tarea.id_tarea,
            "estado": "error", "mensaje_error": str(e), "urls_parciales": urls,
        })


@app.post("/voz/dialogo")
def generar_voz_dialogo(tarea: TareaVozDialogo, background_tasks: BackgroundTasks, authorization: str = Header(None)):
    verificar_seguridad(authorization)
    background_tasks.add_task(_procesar_voz_dialogo, tarea)
    return {"mensaje": f"Tarea {tarea.id_tarea} de dialogo recibida ({len(tarea.dialogo)} lineas), procesando..."}
