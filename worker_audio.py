"""
worker_audio.py — imagen "audio"
-----------------------------------
Conduce la produccion automatica: jala tareas de cola_tareas (subtipo
'Audio_Chatterbox'), llama audio_core.py de forma sincrona, revisa la hora
limite despues de CADA tarea, y avisa a n8n cuando toca cerrar el pod.
"""

import os
import json
import logging
import datetime as dt

import requests
import psycopg2
import psycopg2.extras

import audio_core

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("worker_audio")

DB_DSN            = os.environ["DATABASE_URL"]
FAMILIA_TAREA     = "GEN"
SUBTIPO_TAREA     = "Audio_Chatterbox"
STOP_AT_UTC       = os.environ["STOP_AT_UTC"]
POD_RUN_ID        = os.environ["POD_RUN_ID"]
N8N_WEBHOOK_DONE  = os.environ["N8N_WEBHOOK_DONE"]


def get_conn():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = False
    return conn


def fetch_next_task(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            UPDATE cola_tareas
            SET estado = 'En_Progreso', updated_at = now()
            WHERE id = (
                SELECT id FROM cola_tareas
                WHERE familia = %s AND subtipo_tarea = %s AND estado = 'Pendiente'
                ORDER BY prioridad DESC, created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, avatar_id, elemento_id, payload_entrada;
        """, (FAMILIA_TAREA, SUBTIPO_TAREA))
        row = cur.fetchone()
        conn.commit()
        return row


def fetch_pieza_id(conn, elemento_id):
    if not elemento_id:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT pieza_id FROM pieza_elementos WHERE id = %s;", (elemento_id,))
        row = cur.fetchone()
        return str(row[0]) if row else None


def fetch_voz_referencia(conn, avatar_id, estilo):
    """
    IMPORTANTE: avatar_voces_referencia.ruta_volumen ahora debe contener una
    URL publica de S3/R2, no una ruta de volumen local -- ya no hay volumen
    de red compartido entre pods. Si la columna sigue con datos de la epoca
    del volumen, hay que migrarlos a URLs antes de usar esta imagen.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT ruta_volumen FROM avatar_voces_referencia
            WHERE avatar_id = %s AND estilo = %s AND activo = true;
        """, (avatar_id, estilo))
        return cur.fetchone()


def mark_done(conn, task_id, elemento_id, audio_url):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE cola_tareas SET estado = 'Completada', payload_salida = %s, updated_at = now()
            WHERE id = %s;
        """, (json.dumps({"audio_url": audio_url}), task_id))
        if elemento_id:
            cur.execute("""
                UPDATE pieza_elementos SET url_asset_final = %s, updated_at = now()
                WHERE id = %s;
            """, (audio_url, elemento_id))
        conn.commit()


def mark_error(conn, task_id, mensaje):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE cola_tareas
            SET estado = CASE WHEN intentos + 1 >= max_intentos THEN 'Fallida' ELSE 'Pendiente' END,
                intentos = intentos + 1, mensaje_error = %s, updated_at = now()
            WHERE id = %s;
        """, (mensaje[:2000], task_id))
        conn.commit()


def past_stop_time() -> bool:
    return dt.datetime.utcnow().strftime("%H:%M") >= STOP_AT_UTC


def notify_n8n(reason: str, tasks_done: int):
    try:
        requests.post(N8N_WEBHOOK_DONE, json={
            "pod_run_id": POD_RUN_ID, "task_type": "audio",
            "reason": reason, "tasks_processed": tasks_done,
        }, timeout=15)
    except requests.RequestException as e:
        log.error("No se pudo notificar a n8n: %s", e)


def process_task(conn, task) -> str:
    payload = task["payload_entrada"] or {}
    texto  = payload.get("texto", "")
    idioma = payload.get("idioma", "es")
    estilo = payload.get("estilo_voz", "neutro")
    avatar_id = str(task["avatar_id"])

    if not texto:
        raise ValueError("payload_entrada.texto vacio")

    voz = fetch_voz_referencia(conn, task["avatar_id"], estilo)
    if not voz:
        raise ValueError(f"Avatar {avatar_id} sin voz de estilo '{estilo}' en avatar_voces_referencia")

    id_proyecto = fetch_pieza_id(conn, task["elemento_id"])
    filename = f"{task['id']}.wav"
    local_path = os.path.join(audio_core.OUTPUT_DIR, filename)

    ref_path = audio_core.obtener_referencia_cacheada(voz["ruta_volumen"])
    audio_core.generar_voz(texto, idioma, ref_path, local_path, payload.get("parametros_extra"))

    s3_key = audio_core.construir_s3_key(str(task["id"]), filename, id_proyecto, avatar_id)
    return audio_core.subir_y_destruir(local_path, s3_key)


def main():
    audio_core.cargar_modelo()
    conn = get_conn()
    tasks_done = 0
    log.info("Worker de audio iniciado. stop_at_utc=%s", STOP_AT_UTC)

    while True:
        task = fetch_next_task(conn)

        if task is None:
            log.info("No hay mas tareas pendientes de audio. Cerrando pod.")
            notify_n8n("no_pending_tasks", tasks_done)
            break

        log.info("Procesando tarea %s (avatar_id=%s)", task["id"], task["avatar_id"])
        try:
            audio_url = process_task(conn, task)
            mark_done(conn, task["id"], task["elemento_id"], audio_url)
            log.info("Tarea %s completada -> %s", task["id"], audio_url)
        except Exception as e:
            log.exception("Error procesando tarea %s", task["id"])
            mark_error(conn, task["id"], str(e))

        tasks_done += 1

        if past_stop_time():
            log.info("Hora limite alcanzada tras %s tareas. Cerrando pod.", tasks_done)
            notify_n8n("time_limit", tasks_done)
            break

    conn.close()


if __name__ == "__main__":
    main()
