"""Proceso completo: busca VODs nuevos en Twitch, arma los compactos y los sube a YouTube.

Pensado para correr cada 15-20 min (GitHub Actions). Lleva un registro en estado/procesados.json
para no repetir videos y reintentar los que fallaron.

Uso:
  python procesar.py                      # procesa los VODs nuevos
  python procesar.py --vod 2898838397     # fuerza un VOD puntual (aunque ya esté procesado)
  python procesar.py --sin-subir          # arma los compactos pero no sube nada (pruebas)
  python procesar.py --respaldo "14/10/2026 12:00"   # fuerza el compacto de respaldo de un sorteo

Si un sorteo se queda sin compacto (no hubo VOD o el VOD se abandonó), se arma uno de respaldo con el
extracto oficial (ver respaldo.py).
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

import config
import cronograma
import facebook
import respaldo
import telegram
import twitch

MAX_ANTIGUEDAD_HORAS = 36  # no procesar VODs viejos (evita subir el historial la primera vez)


def cargar_estado():
    if config.ESTADO.exists():
        return json.loads(config.ESTADO.read_text(encoding="utf-8"))
    return {}


def guardar_estado(estado):
    config.ESTADO.parent.mkdir(parents=True, exist_ok=True)
    config.ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def pendiente(registro):
    return not registro or (registro["estado"] == "error" and registro["intentos"] < config.MAX_INTENTOS)


def procesar_vod(vod, subir):
    """Procesa un VOD. Devuelve el registro para estado/procesados.json."""
    fila = cronograma.por_horario(vod["inicio"], config.TOLERANCIA_HORARIO_MIN)
    if not fila and "SORTEO" not in vod["titulo"].upper():
        return {"estado": "omitido", "motivo": "No es un sorteo del cronograma"}

    from armar_compacto import armar  # imports pesados: solo en la etapa que procesa
    from transcribir import transcribir

    carpeta = config.TRABAJO / vod["id"]
    try:
        video = twitch.descargar(vod["id"], carpeta)
        wav = twitch.extraer_audio(video, carpeta / "audio.wav")
        segmentos = transcribir(wav)
        (carpeta / "transcripcion.json").write_text(json.dumps(segmentos, ensure_ascii=False), encoding="utf-8")
        info = armar(video, segmentos, config.SALIDA, fila)
        if not fila:
            info["avisos"].append("El horario del VOD no coincide con ningún sorteo del cronograma")

        return publicar(info, subir)
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)  # el VOD pesa ~1 GB


def procesar_respaldo(fila, subir):
    """Arma y sube el compacto de respaldo. Devuelve el registro, o None si el extracto todavía no está en la API."""
    numeros = respaldo.extracto(fila)
    if not numeros:
        return None
    return publicar(respaldo.armar(fila, numeros, config.SALIDA), subir)


def publicar(info, subir):
    """Sube el compacto a YouTube (y Facebook) y avisa por Telegram. Devuelve el registro para estado/procesados.json."""
    registro = {"estado": "armado", "sorteo": f"{info['sorteo_nombre']} {info['sorteo_numero']}",
                "fecha": info["fecha"], "cabeza": info["cabeza"], "avisos": info["avisos"],
                "compacto": os.path.basename(info["compacto"])}
    if subir:
        import youtube
        # con avisos se sube privado para revisarlo a mano, sin importar la configuración
        privacidad = "private" if info["avisos"] else config.YT_PRIVACIDAD
        registro.update(youtube.subir(info["compacto"], info, privacidad, info["miniatura"]), estado="ok")
        if not registro["mayores_18"]:
            registro["avisos"].append("No se pudo marcar +18 por la API: marcarlo en YouTube Studio "
                                      "(Detalles > Público > Restricción de edad) y recién ahí publicarlo")
        if facebook.activo():
            try:  # con avisos (incluido el +18 de YouTube) queda sin publicar, igual que en YouTube
                fb_publicar = config.FB_PUBLICAR and not registro["avisos"]
                registro["facebook"] = facebook.subir(info["compacto"], info, fb_publicar, info["miniatura"])
            except Exception as e:  # no se reintenta: volvería a subirlo a YouTube
                traceback.print_exc()
                registro["avisos"].append(f"No se pudo subir a Facebook: {e}")
    telegram.avisar(telegram.texto_compacto(registro), Path(info["miniatura"]))
    return registro


def extracto_publicado(fila):
    try:
        return bool(respaldo.extracto(fila))
    except Exception as e:  # API caída: se vuelve a mirar en la próxima corrida
        print(f"No se pudo consultar el extracto: {e}")
        return False


def sorteos_sin_vod(ahora, vods):
    """Sorteos del cronograma de hoy que ya deberían tener VOD y no lo tienen.

    Solo mira los sorteos cuya hora fue hace 70-95 min: así el aviso sale una sola vez, en la 3.ª revisión
    (a los 80 min del sorteo), sin tener que guardar estado."""
    faltan = []
    for fila in cronograma.cargar() if cronograma.CSV.exists() else []:
        if fila["fecha"] != ahora.strftime("%d/%m/%Y"):
            continue
        hora = dt.datetime.strptime(f"{fila['fecha']} {fila['hora']}", "%d/%m/%Y %H:%M").replace(tzinfo=config.ARG)
        if not 70 <= (ahora - hora).total_seconds() / 60 < 95:
            continue
        tolerancia = dt.timedelta(minutes=config.TOLERANCIA_HORARIO_MIN)
        if not any(abs(v["inicio"] - hora) <= tolerancia for v in vods):
            faltan.append(fila)
    return faltan


def resumen(filas):
    """Tabla en Markdown para el resumen de la corrida en GitHub Actions."""
    if not filas:
        return "Sin VODs nuevos.\n"
    md = ["| VOD | Sorteo | Cabeza | Estado | YouTube | Facebook | Avisos |", "|---|---|---|---|---|---|---|"]
    for vod_id, r in filas:
        fb = r.get("facebook") or {}
        md.append(f"| {vod_id} | {r.get('sorteo', '')} {r.get('fecha', '')} | {r.get('cabeza', '')} | {r['estado']} | "
                  f"{r.get('url', '')} {r.get('privacidad', '')} | "
                  f"{fb.get('url', '')} {'' if not fb else 'publicado' if fb['publicado'] else 'sin publicar'} | "
                  f"{'; '.join(r.get('avisos', [])) or r.get('error', '')} |")
    return "\n".join(md) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vod", help="ID de un VOD puntual a procesar")
    ap.add_argument("--sin-subir", action="store_true")
    ap.add_argument("--solo-detectar", action="store_true", help="solo informa si hay VODs pendientes")
    ap.add_argument("--respaldo", metavar="'DD/MM/AAAA HH:MM'", help="arma el compacto de respaldo de un sorteo")
    a = ap.parse_args()
    if not config.TWITCH_CANAL:
        sys.exit("Falta configurar TWITCH_CANAL")

    ahora = dt.datetime.now(config.ARG)
    avisos_generales = [] if a.solo_detectar else cronograma.asegurar(ahora.date())
    estado = cargar_estado()

    vods = todos = [] if a.respaldo else twitch.ultimos_vods()
    if a.solo_detectar and not a.vod:
        for fila in sorteos_sin_vod(ahora, vods):
            print(f"Sin VOD: {fila['nombre']} {fila['sorteo']} de las {fila['hora']}")
            telegram.avisar(f"📵 No se encontró en Twitch la transmisión del sorteo {fila['nombre'].title()} "
                            f"N° {fila['sorteo']} de hoy a las {fila['hora']} hs.\n"
                            "Revisar si salió en vivo. Se va a armar un compacto de respaldo con el extracto "
                            "oficial cuando esté publicado.")
    if a.respaldo:
        fecha, _, hora = a.respaldo.strip().partition(" ")
        if not (fila := cronograma.buscar(fecha=fecha, hora=hora)):
            sys.exit(f"El sorteo del {a.respaldo} no está en el cronograma")
        respaldos = [fila]
    elif a.vod:
        respaldos = []
        vods = [v for v in vods if v["id"] == a.vod] or [twitch.info_vod(a.vod)]
    else:
        vivo = bool(vods) and twitch.en_vivo()
        if a.solo_detectar:  # para diagnosticar corridas que no ven un VOD ya terminado
            print(f"En vivo: {vivo}")
            for v in vods[:3]:
                print(f"VOD {v['id']} {v['inicio']:%d/%m %H:%M} {v['duracion'] // 60} min "
                      f"{(estado.get(v['id']) or {}).get('estado', 'nuevo')} {v['titulo']}")
        if vivo:
            vods = vods[1:]  # el más nuevo todavía se está grabando
        limite = ahora - dt.timedelta(hours=MAX_ANTIGUEDAD_HORAS)
        vods = [v for v in vods if v["inicio"] >= limite and pendiente(estado.get(v["id"]))]
        respaldos = None  # se calculan después de procesar los VODs (alguno puede quedar abandonado)

    if a.solo_detectar:
        listos = [f for f in respaldo.faltantes(ahora, estado, todos) if extracto_publicado(f)]
        print(f"VODs pendientes: {[v['id'] for v in vods]}")
        print(f"Respaldos pendientes: {[respaldo.clave(f) for f in listos]}")
        if destino := os.environ.get("GITHUB_OUTPUT"):
            with open(destino, "a") as f:
                f.write(f"pendientes={'true' if vods or listos else 'false'}\n")
        return

    hechos, hubo_error = [], False
    for vod in sorted(vods, key=lambda v: v["inicio"]):
        previo = estado.get(vod["id"], {})
        print(f"Procesando VOD {vod['id']} ({vod['inicio']:%d/%m %H:%M}) {vod['titulo']}", flush=True)
        try:
            registro = procesar_vod(vod, subir=not a.sin_subir)
        except Exception as e:  # CompactoError, red, YouTube, etc.: se reintenta en la próxima corrida
            hubo_error = True
            traceback.print_exc()
            intentos = previo.get("intentos", 0) + 1
            registro = {"estado": "error" if intentos < config.MAX_INTENTOS else "abandonado",
                        "error": f"{type(e).__name__}: {e}"}
        registro.update(intentos=previo.get("intentos", 0) + 1, titulo_twitch=vod["titulo"],
                        actualizado=dt.datetime.now(config.ARG).isoformat(timespec="seconds"))
        if registro["estado"] in ("error", "abandonado"):
            telegram.avisar(telegram.texto_error(vod, registro))
        if not a.sin_subir:  # las pruebas no marcan el VOD como procesado
            estado[vod["id"]] = registro
            guardar_estado(estado)  # después de cada VOD, por si la corrida se corta
        hechos.append((vod["id"], registro))
        print(json.dumps(registro, ensure_ascii=False), flush=True)

    if respaldos is None:
        respaldos = respaldo.faltantes(dt.datetime.now(config.ARG), estado, todos)
    for fila in respaldos:
        clave = respaldo.clave(fila)
        previo = estado.get(clave, {})
        print(f"Respaldo de {fila['nombre']} {fila['sorteo']} ({fila['fecha']} {fila['hora']})", flush=True)
        try:
            registro = procesar_respaldo(fila, subir=not a.sin_subir)
        except Exception as e:  # API, FFmpeg, YouTube, etc.: se reintenta en la próxima corrida
            hubo_error = True
            traceback.print_exc()
            intentos = previo.get("intentos", 0) + 1
            registro = {"estado": "error" if intentos < config.MAX_INTENTOS else "abandonado",
                        "error": f"{type(e).__name__}: {e}"}
        if registro is None:
            print("El extracto todavía no está publicado en la API: se reintenta en la próxima corrida")
            continue
        registro.update(intentos=previo.get("intentos", 0) + 1,
                        actualizado=dt.datetime.now(config.ARG).isoformat(timespec="seconds"))
        if registro["estado"] in ("error", "abandonado"):
            telegram.avisar(telegram.texto_error_respaldo(fila, registro))
        if not a.sin_subir:
            estado[clave] = registro
            guardar_estado(estado)
        hechos.append(("respaldo", registro))
        print(json.dumps(registro, ensure_ascii=False), flush=True)

    texto = resumen(hechos) + "".join(f"\n> ⚠️ {x}\n" for x in avisos_generales)
    print(texto)
    if destino := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Compactos de sorteos\n\n" + texto)
    for aviso in avisos_generales:
        telegram.avisar(f"⚠️ {aviso}")
    # con Telegram los videos a revisar ya llegan por ahí; sin Telegram, la corrida en rojo manda el mail de GitHub
    a_revisar = any(r.get("avisos") for _, r in hechos) and not telegram.configurado()
    if hubo_error or avisos_generales or a_revisar:
        sys.exit(3)  # en rojo (GitHub avisa por mail); el 3 indica que el detalle ya salió por Telegram


if __name__ == "__main__":
    main()
