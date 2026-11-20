"""Compacto de respaldo: cuando un sorteo no tiene compacto (no hubo VOD en Twitch o el VOD se abandonó
después de MAX_INTENTOS), se arma un video corto con el extracto oficial de la API de la Lotería:
intro (igual a la del compacto) -> placa con los 20 números, con la cortina de fondo.

Siempre se sube privado (lleva un aviso) para revisarlo antes de publicarlo.

Uso local (arma sin subir):
  python respaldo.py 14/10/2026 12:00 [--salida carpeta/]
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import config
import cronograma

TURNOS = {"12:00": "MATUTINO", "15:00": "VESPERTINO", "18:00": "EXTRA", "21:00": "NOCTURNO", "22:30": "NOCTURNOEXTRA"}
ESPERA_MIN = 70        # desde la hora del sorteo: antes de esto el compacto normal todavía puede salir
VENTANA_HORAS = 24     # sorteos más viejos no se respaldan (evita subir el historial)
PLACA_DUR = 20.0

_cache = {}


def clave(fila):
    """Clave en estado/procesados.json (los VODs usan su ID)."""
    return f"respaldo {fila['fecha']} {fila['hora']}"


def extracto(fila):
    """Los 20 números del sorteo según la API oficial, o None si todavía no está publicado."""
    fecha = fila["fecha"].replace("/", "-")
    if fecha not in _cache:
        if not config.API_EXTRACTO:
            raise RuntimeError("Falta LOTERIA_API_EXTRACTO (secreto con la URL de la API del extracto)")
        pedido = urllib.request.Request(config.API_EXTRACTO.format(fecha=fecha), headers={"User-Agent": "compactos"})
        with urllib.request.urlopen(pedido, timeout=20) as r:
            _cache[fecha] = json.load(r)
    turno = (_cache[fecha].get("resultados") or {}).get(TURNOS.get(fila["hora"], ""), {})
    salta = (turno.get("extractos") or {}).get("SALTA") or {}
    numeros = [str(n).zfill(4) for n in salta.get("extracto") or []]
    if salta.get("fechaSorteo") != fila["fecha"] or len(numeros) != 20 or not all(re.fullmatch(r"\d{4}", n) for n in numeros):
        return None
    return numeros


def faltantes(ahora, estado, vods):
    """Sorteos del cronograma (de las últimas VENTANA_HORAS) que necesitan respaldo: ya pasaron ESPERA_MIN,
    no tienen compacto subido y no tienen un VOD que todavía se pueda procesar."""
    if not cronograma.CSV.exists():
        return []
    tolerancia = dt.timedelta(minutes=config.TOLERANCIA_HORARIO_MIN)
    subidos = {(r.get("sorteo"), r.get("fecha")) for r in estado.values() if r.get("estado") == "ok"}
    faltan = []
    for fila in cronograma.cargar():
        hora = dt.datetime.strptime(f"{fila['fecha']} {fila['hora']}", "%d/%m/%Y %H:%M").replace(tzinfo=config.ARG)
        if not dt.timedelta(minutes=ESPERA_MIN) <= ahora - hora <= dt.timedelta(hours=VENTANA_HORAS):
            continue
        if fila["hora"] not in TURNOS or (f"{fila['nombre']} {fila['sorteo']}", fila["fecha"]) in subidos:
            continue
        if estado.get(clave(fila), {}).get("estado") in ("ok", "abandonado"):
            continue
        del_sorteo = [estado.get(v["id"]) for v in vods if abs(v["inicio"] - hora) <= tolerancia]
        if any(r is None or r["estado"] in ("ok", "omitido") or r["estado"] == "error" and r["intentos"] < config.MAX_INTENTOS
               for r in del_sorteo):
            continue  # ya tiene compacto o el VOD todavía se está procesando/reintentando
        faltan.append(fila)
    return faltan


def _fuente(tam):
    from PIL import ImageFont
    return ImageFont.truetype(str(config.FUENTE), tam)


def placa(datos, numeros, destino):
    """Placa de 1920x1080 sobre el fondo de la transmisión: cabeza destacada a la izquierda, extracto a la derecha."""
    from PIL import Image, ImageDraw

    img = Image.open(config.FONDO_INTRO).convert("RGBA").resize((1920, 1080))
    capa = Image.new("RGBA", img.size)
    d = ImageDraw.Draw(capa)
    blanco, oro, tenue = (255, 255, 255, 255), (255, 209, 0, 255), (255, 255, 255, 150)

    # encabezado
    titulo = f"{datos['sorteo_nombre']}  ·  N° {datos['sorteo_numero']}  ·  {datos['fecha']}"
    d.text((960, 175), titulo, font=_fuente(46), fill=blanco, anchor="mm")

    # panel izquierdo: logo + cabeza
    d.rounded_rectangle((250, 250, 790, 925), radius=36, fill=(20, 0, 40, 150), outline=(255, 255, 255, 40), width=2)
    logo = Image.open(config.LOGO).convert("RGBA")
    logo = logo.resize((440, round(440 * logo.height / logo.width)))
    capa.alpha_composite(logo, (300, 300))
    d.text((520, 540), "A LA CABEZA", font=_fuente(44), fill=tenue, anchor="mm")
    d.text((520, 660), numeros[0], font=_fuente(170), fill=oro, anchor="mm")
    d.text((520, 840), "Extracto oficial", font=_fuente(32), fill=tenue, anchor="mm")

    # panel derecho: 20 ubicaciones en dos columnas
    d.rounded_rectangle((850, 250, 1670, 925), radius=36, fill=(20, 0, 40, 150), outline=(255, 255, 255, 40), width=2)
    f_pos, f_num = _fuente(34), _fuente(52)
    for i, n in enumerate(numeros):
        col, fila = divmod(i, 10)
        x, y = 900 + col * 400, 290 + fila * 62
        if i == 0:
            d.rounded_rectangle((x - 20, y - 4, x + 340, y + 58), radius=14, fill=(255, 209, 0, 50))
        d.text((x + 50, y + 27), f"{i + 1}", font=f_pos, fill=tenue, anchor="rm")
        d.text((x + 100, y + 27), n, font=f_num, fill=oro if i == 0 else blanco, anchor="lm")

    img.alpha_composite(capa)
    img.convert("RGB").save(destino)


def armar(fila, numeros, salida):
    """Arma el video de respaldo. Devuelve un dict como el de armar_compacto.armar."""
    from PIL import Image

    from armar_compacto import filtro_intro

    salida.mkdir(parents=True, exist_ok=True)
    datos = {"sorteo_nombre": fila["nombre"], "sorteo_numero": fila["sorteo"], "fecha": fila["fecha"],
             "hora": fila["hora"], "changuita": fila["changuita"], "tombola_total": fila["tombola_total"],
             "cabeza": numeros[0], "extracto": numeros, "fuente": "api", "respaldo": True}
    tmp = Path(tempfile.mkdtemp())
    placa_png = tmp / "placa.png"
    placa(datos, numeros, placa_png)

    total = config.INTRO_DUR + PLACA_DUR - config.FUNDIDO
    f = [filtro_intro(datos),
         f"[2:v]scale=1920:1080,setsar=1,fps=30,trim=duration={PLACA_DUR},format=yuv420p[v1]",
         f"[v0][v1]xfade=transition=fade:duration={config.FUNDIDO}:offset={config.INTRO_DUR - config.FUNDIDO:.2f},"
         f"fade=t=out:st={total - 1:.2f}:d=1[vout]",
         # sin locutor: la cortina va al frente
         f"[3:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.2f},"
         f"afade=t=in:d=1,afade=t=out:st={total - 2:.2f}:d=2,loudnorm=I=-16:TP=-1.5:LRA=11[aout]"]

    nombre = re.sub(r"\W+", "_", f"{datos['fecha']}_{datos['sorteo_nombre']}_{datos['sorteo_numero']}").strip("_")
    destino = salida / f"respaldo_{nombre}.mp4"
    cmd = [config.FFMPEG, "-v", "error", "-y",
           "-loop", "1", "-framerate", "30", "-t", str(config.INTRO_DUR), "-i", str(config.FONDO_INTRO),
           "-i", str(config.LOGO),
           "-loop", "1", "-framerate", "30", "-t", str(PLACA_DUR), "-i", str(placa_png),
           "-stream_loop", "-1", "-i", str(config.CORTINA),
           "-filter_complex", ";".join(f), "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.2f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(destino)]
    subprocess.run(cmd, check=True)

    info = {**datos, "compacto": str(destino), "miniatura": str(destino.with_suffix(".jpg")),
            "duracion": round(total, 1), "capitulos": [],
            "avisos": ["Compacto de respaldo con el extracto oficial (sin imagen del sorteo): revisarlo antes de publicarlo"]}
    Image.open(placa_png).convert("RGB").save(info["miniatura"], quality=90)
    destino.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("fecha", help="DD/MM/AAAA")
    ap.add_argument("hora", help="HH:MM")
    ap.add_argument("--salida", type=Path, default=config.SALIDA)
    a = ap.parse_args()
    fila = cronograma.buscar(fecha=a.fecha, hora=a.hora)
    if not fila:
        raise SystemExit("El sorteo no está en el cronograma")
    numeros = extracto(fila)
    if not numeros:
        raise SystemExit("El extracto todavía no está publicado en la API")
    print(json.dumps(armar(fila, numeros, a.salida), ensure_ascii=False, indent=2))
