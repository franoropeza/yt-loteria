"""Proceso completo: busca VODs nuevos en Twitch, arma los compactos y los sube a YouTube.

Pensado para correr cada 15-20 min (GitHub Actions). Lleva un registro en estado/procesados.json
para no repetir videos y reintentar los que fallaron.

Uso:
  python procesar.py                      # procesa los VODs nuevos
  python procesar.py --vod 2898838397     # fuerza un VOD puntual (aunque ya esté procesado)
  python procesar.py --sin-subir          # arma los compactos pero no sube nada (pruebas)
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import traceback

import config
import cronograma
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
        return registro
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)  # el VOD pesa ~1 GB


def resumen(filas):
    """Tabla en Markdown para el resumen de la corrida en GitHub Actions."""
    if not filas:
        return "Sin VODs nuevos.\n"
    md = ["| VOD | Sorteo | Cabeza | Estado | YouTube | Avisos |", "|---|---|---|---|---|---|"]
    for vod_id, r in filas:
        md.append(f"| {vod_id} | {r.get('sorteo', '')} {r.get('fecha', '')} | {r.get('cabeza', '')} | {r['estado']} | "
                  f"{r.get('url', '')} {r.get('privacidad', '')} | {'; '.join(r.get('avisos', [])) or r.get('error', '')} |")
    return "\n".join(md) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vod", help="ID de un VOD puntual a procesar")
    ap.add_argument("--sin-subir", action="store_true")
    ap.add_argument("--solo-detectar", action="store_true", help="solo informa si hay VODs pendientes")
    a = ap.parse_args()
    if not config.TWITCH_CANAL:
        sys.exit("Falta configurar TWITCH_CANAL")

    ahora = dt.datetime.now(config.ARG)
    avisos_generales = [] if a.solo_detectar else cronograma.asegurar(ahora.date())
    estado = cargar_estado()

    vods = twitch.ultimos_vods()
    if a.vod:
        vods = [v for v in vods if v["id"] == a.vod] or [twitch.info_vod(a.vod)]
    else:
        if vods and twitch.en_vivo():
            vods = vods[1:]  # el más nuevo todavía se está grabando
        limite = ahora - dt.timedelta(hours=MAX_ANTIGUEDAD_HORAS)
        vods = [v for v in vods if v["inicio"] >= limite and pendiente(estado.get(v["id"]))]

    if a.solo_detectar:
        print(f"VODs pendientes: {[v['id'] for v in vods]}")
        if destino := os.environ.get("GITHUB_OUTPUT"):
            with open(destino, "a") as f:
                f.write(f"pendientes={'true' if vods else 'false'}\n")
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
        if not a.sin_subir:  # las pruebas no marcan el VOD como procesado
            estado[vod["id"]] = registro
            guardar_estado(estado)  # después de cada VOD, por si la corrida se corta
        hechos.append((vod["id"], registro))
        print(json.dumps(registro, ensure_ascii=False), flush=True)

    texto = resumen(hechos) + "".join(f"\n> ⚠️ {x}\n" for x in avisos_generales)
    print(texto)
    if destino := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(destino, "a", encoding="utf-8") as f:
            f.write("## Compactos de sorteos\n\n" + texto)
    a_revisar = any(r.get("avisos") for _, r in hechos)
    if hubo_error or avisos_generales or a_revisar:
        sys.exit(1)  # la corrida queda en rojo y GitHub avisa por mail


if __name__ == "__main__":
    main()
