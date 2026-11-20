"""Arma el compacto de un sorteo a partir del VOD de Twitch.

Estructura del compacto:
  intro (fondo + logo Tómbola + título) -> bienvenida del locutor -> tramo hasta "a la cabeza"
  -> cierre con la captura del tablero final.
Si el tablero no aparece en la transmisión, se arma un compacto híbrido: el mismo video
del locutor, pero el cierre es la placa con el extracto oficial de la API (ver respaldo.py).
La cortina musical suena de fondo y baja sola cuando habla el locutor.
Si la transmisión salió sin audio, el compacto va solo con la cortina y la cabeza se ubica por el
tablero (ver cabeza_por_tablero), o con el momento que se indique a mano (procesar.py --vod ... --cabeza mm:ss).

Uso local:
  python armar_compacto.py VOD.mp4 TRANSCRIPCION.json [--salida carpeta/]
"""
import argparse
from collections import Counter
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import numpy as np
import pytesseract
from PIL import Image, ImageOps

import config
import cronograma
from transcribir import CABEZA, buscar_cabeza

pytesseract.pytesseract.tesseract_cmd = config.TESSERACT

# Coordenadas dentro del cuadro de 1916x1080 del VOD
TABLERO = (316, 182, 1298, 713)       # x, y, ancho, alto del tablero de resultados
EN_VIVO = "delogo=x=50:y=50:w=215:h=80"  # borra el cartel "EN VIVO"
CAMPOS_OCR = {                         # coordenadas relativas al tablero recortado
    "sorteo_nombre": (500, 36, 476, 30),
    "sorteo_numero": (389, 98, 173, 38),
    "fecha": (602, 98, 178, 38),
    "hora": (824, 98, 152, 38),
    "cabeza": (176, 190, 196, 80),
}


class CompactoError(Exception):
    pass


class SinTablero(CompactoError):
    pass


class ExtractoPendiente(CompactoError):
    """Falta el tablero y el extracto oficial todavía no está en la API: se reintenta sin gastar un intento."""


class SinAudio(CompactoError):
    """VOD sin audio y no se pudo ubicar la cabeza por el tablero: no tiene sentido reintentar (se pasa al respaldo)."""


def buscar_momentos(segmentos):
    """Devuelve (inicio de la bienvenida, momento de la cabeza, aviso o None)."""
    if not segmentos:
        raise CompactoError("La transcripción está vacía")
    inicio = next((s["start"] for s in segmentos if "bienvenid" in s["text"].lower()), segmentos[0]["start"])
    if (cabeza := buscar_cabeza(segmentos)) is not None:
        return min(inicio, cabeza), cabeza, None
    # sin "ubicación uno" antes: se toma la primera "cabeza" después de la bienvenida, pero hay que revisarlo
    for s in segmentos:
        for w in s["words"]:
            if w["s"] >= inicio and CABEZA.search(w["w"]):
                return inicio, w["s"], "La cabeza no se pudo confirmar con \"ubicación uno\": revisar el corte"
    raise CompactoError("No se encontró 'a la cabeza' en la transcripción")


DIGITOS = {"cero": "0", "uno": "1", "un": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
           "seis": "6", "siete": "7", "ocho": "8", "nueve": "9"}


def sin_tildes(texto):
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def numeros_cantados(texto):
    """Números que aparecen en el texto, uniendo cifras sueltas ("nueve, siete, cero, seis" -> "9706")."""
    t = re.sub(r"(?<=\d)[.,](?=\d)", "", sin_tildes(texto))  # "9.706" -> "9706"
    numeros, actual = set(), ""
    for tok in re.findall(r"\d+|[a-z]+", t):
        cifra = tok if tok.isdigit() else DIGITOS.get(tok)
        if cifra is None:
            if actual:
                numeros.add(actual)
            actual = ""
        else:
            actual += cifra
    return numeros | ({actual} if actual else set())


def revisar_locucion(segmentos, desde, hasta):
    """Controles sobre lo que dice el locutor. Devuelve una lista de avisos."""
    avisos = []
    if not config.TERMINOS_RIESGO:
        avisos.append("TERMINOS_RIESGO no está configurado: no se controlaron los términos de riesgo, revisar el audio")
    tramo = " ".join(s["text"] for s in segmentos if desde - 2 <= s["start"] <= hasta)
    if riesgo := sorted({t for t in config.TERMINOS_RIESGO if t in sin_tildes(tramo)}):
        avisos.append(f"El locutor menciona términos de riesgo en el compacto: {', '.join(riesgo)}")
    return avisos


def cantados_en_cabeza(segmentos, t_cab):
    """Números que dijo el locutor alrededor de "a la cabeza"."""
    return numeros_cantados(" ".join(s["text"] for s in segmentos if t_cab - 30 <= s["start"] <= t_cab + 20))


def _cuadros_tablero(vod, desde):
    """Un cuadro cada 2 s desde `desde`: (hay tablero, tiene el cartel de Tómbola Total, paso en s)."""
    w, h, paso = 192, 108, 2
    raw = subprocess.run([config.FFMPEG, "-v", "error", "-ss", str(desde), "-i", str(vod),
                          "-vf", f"fps=1/{paso},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    cuadros = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(int)
    # zona de las celdas de numeros (escala 1/10 aprox.)
    celdas = cuadros[:, 37:83, 41:153]
    r, g, b = celdas[..., 0], celdas[..., 1], celdas[..., 2]
    tablero = ((r > 200) & (g > 200) & (b > 200)).mean(axis=(1, 2)) > 0.30
    cartel = ((g > 140) & (r < 140) & (b < 120)).mean(axis=(1, 2)) > 0.01
    return tablero, cartel, paso


def buscar_tablero(vod, desde):
    """Ubica el tablero de resultados entre la cabeza y el final del VOD.

    Devuelve (t_datos, t_final, tombola_total, datos_con_cartel):
      t_datos: primer tablero sin cartel encima, para leer nombre/número del sorteo y la cabeza.
      t_final: último tablero, para el cierre.
    En Vespertina y Nocturna el tablero final lleva encima el cartel verde del Tómbola Total
    (y el encabezado cambia a "TÓMBOLA TOTAL"), por eso los datos se leen antes. A veces el tablero
    aparece directamente con el cartel: ahí se lee ese, la cabeza (celda 1) se ve
    igual pero el encabezado trae el número del Tómbola Total."""
    tablero, cartel, paso = _cuadros_tablero(vod, desde)
    limpios = np.where(tablero & ~cartel)[0]
    todos = np.where(tablero)[0]
    if not len(todos):
        raise SinTablero("No se encontró el tablero de resultados después de la cabeza")
    elegidos = limpios if len(limpios) else todos
    # +1 cuadro: da tiempo a que termine la animación de entrada del tablero
    t_datos = desde + min(elegidos[0] + 1, elegidos[-1]) * paso
    return t_datos, desde + todos[-1] * paso, bool(cartel[todos[-1]]), not len(limpios)


# Esquina de la zona del número de cada casilla (1 a 20, por columnas de a 5) dentro del tablero recortado;
# cada una mide 196x80, como CAMPOS_OCR["cabeza"], que es la casilla 1
CASILLAS = [(176 + 283 * (i // 5), 190 + 96 * (i % 5)) for i in range(20)]
DEMORA_TABLERO = 4  # s entre "a la cabeza" y la aparición del tablero, cuando el operador lo muestra enseguida
REARMAR = "Para armarlo con el video, correr el workflow con este VOD y el minuto en que sale la cabeza (cabeza = mm:ss)"


def casillas_llenas(png):
    """Lista de 20 booleanos: qué casillas del tablero recortado ya tienen número."""
    rgb = np.asarray(Image.open(png).convert("RGB")).astype(int)
    return [bool((rgb[y + 10:y + 70, x + 10:x + 186].min(axis=2) < 120).mean() > 0.05) for x, y in CASILLAS]


def mmss(t):
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def cabeza_por_tablero(vod):
    """Momento de la cabeza en un VOD sin audio, deducido de las apariciones del tablero.

    El operador muestra el tablero cada tantas extracciones. Si la primera vez que aparece la casilla 1 llena es la
    única novedad respecto de la aparición anterior, la cabeza fue la última extracción: salió unos segundos antes.
    Si aparecieron varios números juntos no se sabe cuál de esas extracciones fue la cabeza, y no se adivina."""
    tablero, _, paso = _cuadros_tablero(vod, 0)
    idx = np.where(tablero)[0]
    tramos = np.split(idx, np.where(np.diff(idx) > 1)[0] + 1) if len(idx) else []
    previas = 0
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "tablero.png"
        for tramo in tramos:
            ini, fin = tramo[0] * paso, tramo[-1] * paso
            capturar(vod, min(ini + paso, fin), png, recorte=TABLERO)  # +paso: que termine la animación de entrada
            llenas = casillas_llenas(png)
            if llenas[0]:
                if sum(llenas) - previas == 1:
                    return max(0.0, ini - DEMORA_TABLERO)
                raise SinAudio(f"El VOD no tiene audio y no se pudo ubicar la cabeza: el tablero apareció a los "
                               f"{mmss(ini)} con {sum(llenas) - previas} números nuevos. {REARMAR}")
            previas = sum(llenas)
    raise SinAudio(f"El VOD no tiene audio y el tablero nunca mostró la cabeza. {REARMAR}")


def capturar(vod, t, destino, recorte=None):
    filtro = f"crop={recorte[2]}:{recorte[3]}:{recorte[0]}:{recorte[1]}" if recorte else f"scale=1920:1080,{EN_VIVO}"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-ss", str(t), "-i", str(vod), "-frames:v", "1",
                    "-vf", filtro, str(destino)], check=True)


def leer_tablero(png):
    tablero = Image.open(png)
    datos = {}
    for campo, (x, y, w, h) in CAMPOS_OCR.items():
        img = ImageOps.grayscale(tablero.crop((x, y, x + w, y + h)).resize((w * 3, h * 3)))
        if campo != "sorteo_nombre":
            img = ImageOps.invert(img) if np.asarray(img).mean() < 128 else img
        cfg = "--psm 7" + ("" if campo == "sorteo_nombre" else " -c tessedit_char_whitelist=0123456789/:")
        datos[campo] = pytesseract.image_to_string(img, config=cfg).strip()
    datos["sorteo_nombre"] = re.sub(r"[^A-ZÁÉÍÓÚÑ ]", "", datos["sorteo_nombre"].upper()).strip()
    datos["sorteo_numero"] = re.sub(r"\D", "", datos["sorteo_numero"])
    datos["fecha"] = re.sub(r"[^\d/]", "", datos["fecha"])
    datos["hora"] = re.sub(r"[^\d:]", "", datos["hora"])[:5]
    datos["cabeza"] = re.sub(r"\D", "", datos["cabeza"])
    return datos


# (umbral de rojo, escala, psm): una sola lectura de Tesseract no es fiable con algunas cifras; se vota entre
# estas tres combinaciones
LECTURAS_CABEZA = [(30, 3, 7), (60, 1, 7), (60, 2, 8)]


def leer_cabeza(tablero):
    """Lecturas de 4 cifras de la cabeza (en rojo) en un tablero recortado; puede devolver ninguna."""
    x, y, w, h = CAMPOS_OCR["cabeza"]
    rgb = np.asarray(Image.open(tablero).convert("RGB").crop((x, y, x + w, y + h))).astype(int)
    rojo = rgb[..., 0] - np.maximum(rgb[..., 1], rgb[..., 2])
    lecturas = []
    for umbral, escala, psm in LECTURAS_CABEZA:
        img = Image.fromarray(np.where(rojo > umbral, 0, 255).astype(np.uint8)).resize((w * escala, h * escala))
        texto = pytesseract.image_to_string(ImageOps.expand(img, 20, fill=255),
                                            config=f"--psm {psm} -c tessedit_char_whitelist=0123456789")
        if re.fullmatch(r"\d{4}", cifras := re.sub(r"\D", "", texto)):
            lecturas.append(cifras)
    return lecturas


def elegir_cabeza(lecturas, cantados, sin_voz=False):
    """La lectura más votada entre las que cantó el locutor; si no cantó ninguna, la más votada.
    Sin audio no hay locutor que la confirme: vale si coinciden todas las lecturas (al menos 4).
    Devuelve (cabeza, confirmada)."""
    votos = Counter(lecturas).most_common()
    if sin_voz:
        return (votos[0][0] if votos else ""), len(votos) == 1 and len(lecturas) >= 4
    for cabeza, _ in votos:
        if any(cabeza.lstrip("0") in n for n in cantados):
            return cabeza, True
    return (votos[0][0] if votos else ""), False


def combinar_datos(ocr, fila, con_cartel=False):
    """El cronograma oficial manda; el OCR sirve para encontrar el sorteo si no hay fila, y como control.
    Con el cartel del Tómbola Total el encabezado trae su número, así que solo se controla la fecha.

    Devuelve (datos, avisos). Un aviso significa que conviene revisar el video antes de publicarlo."""
    avisos = []
    fila = fila or cronograma.buscar(sorteo=ocr["sorteo_numero"], fecha=ocr["fecha"], hora=ocr["hora"])
    if fila:
        datos = {**ocr, "sorteo_nombre": fila["nombre"], "sorteo_numero": fila["sorteo"], "fecha": fila["fecha"],
                 "hora": fila["hora"], "changuita": fila["changuita"], "tombola_total": fila["tombola_total"],
                 "fuente": "cronograma"}
        # el OCR a veces agrega un caracter suelto ("116238", "/16234"): se compara por contenido
        if (fila["sorteo"] not in ocr["sorteo_numero"] and not con_cartel) or ocr["fecha"] != fila["fecha"]:
            avisos.append(f"El tablero dice sorteo {ocr['sorteo_numero']} del {ocr['fecha']}, "
                          f"el cronograma {fila['sorteo']} del {fila['fecha']}")
    else:
        datos = {**ocr, "tombola_total": "", "fuente": "ocr"}
        avisos.append("El sorteo no está en el cronograma: los datos salen solo del OCR")
    if not re.fullmatch(r"\d{4}", datos["cabeza"]):
        avisos.append(f"No se pudo leer la cabeza en el tablero (OCR: '{datos['cabeza']}')")
    return datos, avisos


def datos_hibrido(segmentos, t_cab, fila, placa_png):
    """Sin tablero en la transmisión: los datos salen del cronograma y del extracto oficial, y la placa del
    extracto (la del respaldo) se guarda en `placa_png` para el cierre. Devuelve (datos, avisos)."""
    import respaldo
    if not fila:
        raise CompactoError("No se encontró el tablero y el sorteo no está en el cronograma (sin extracto oficial)")
    numeros = respaldo.extracto(fila)
    if not numeros:
        raise ExtractoPendiente("No apareció el tablero en la transmisión y el extracto oficial todavía no está publicado")
    datos = {"sorteo_nombre": fila["nombre"], "sorteo_numero": fila["sorteo"], "fecha": fila["fecha"],
             "hora": fila["hora"], "changuita": fila["changuita"], "tombola_total": fila["tombola_total"],
             "cabeza": numeros[0], "extracto": numeros, "fuente": "cronograma", "hibrido": True}
    respaldo.placa(datos, numeros, placa_png)
    avisos = ["No apareció el tablero en la transmisión: el cierre muestra el extracto oficial"]
    cantados = cantados_en_cabeza(segmentos, t_cab)
    if not elegir_cabeza([numeros[0]], cantados)[1]:
        avisos.append(f"La cabeza del extracto oficial ({numeros[0]}) no coincide con la que cantó el locutor "
                      f"({', '.join(sorted(cantados)) or 'no se entendió'})")
    return datos, avisos


def texto_ff(t):
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")


def ruta_ff(p):
    return str(p).replace("\\", "/").replace(":", "\\:")


def filtro_intro(datos):
    """Filtro de FFmpeg de la intro (entradas 0 = fondo, 1 = logo): fondo fijo + logo + título
    (y Tómbola Total si corresponde). Sale como [v0]."""
    fuente = ruta_ff(config.FUENTE)
    titulo = f"{datos['sorteo_nombre']}  ·  N° {datos['sorteo_numero']}  ·  {datos['fecha']}"
    intro = (f"[0:v]scale=1920:1080,setsar=1,fps=30[fondo];"
             f"[1:v]scale=760:-1[logo];[fondo][logo]overlay=x=(W-w)/2:y=300,"
             f"drawtext=fontfile='{fuente}':text='{texto_ff(titulo)}':fontsize=60:fontcolor=white:"
             f"x=(w-tw)/2:y=h*0.62:alpha='min(1,max(0,(t-0.6)*2))':shadowcolor=black@0.6:shadowx=3:shadowy=3")
    if datos.get("tombola_total"):
        intro += (f",drawtext=fontfile='{fuente}':text='{texto_ff('+ TÓMBOLA TOTAL ' + datos['tombola_total'])}':"
                  f"fontsize=52:fontcolor=0xFFD100:x=(w-tw)/2:y=h*0.62+90:alpha='min(1,max(0,(t-1.0)*2))':"
                  f"shadowcolor=black@0.6:shadowx=3:shadowy=3")
    return intro + f",trim=duration={config.INTRO_DUR},setpts=PTS-STARTPTS,format=yuv420p[v0]"


def armar(vod, segmentos, salida, fila_cronograma=None, cabeza=None, sin_voz=False):
    """Arma el compacto. Devuelve un dict con los datos del sorteo, la ruta del video y los avisos.

    cabeza: momento (s) de "a la cabeza" indicado a mano, en vez de buscarlo en la transcripción.
    sin_voz: la transmisión salió sin audio; el compacto va solo con la cortina."""
    salida.mkdir(parents=True, exist_ok=True)
    if cabeza is not None or sin_voz:
        t_cab = float(cabeza) if cabeza is not None else cabeza_por_tablero(vod)
        aviso_cabeza = None
        bienvenida = next((s["start"] for s in segmentos if "bienvenid" in s["text"].lower()), None)
        t_ini = min(bienvenida, t_cab) if bienvenida is not None else max(0.0, t_cab - config.ANTES_CABEZA)
    else:
        t_ini, t_cab, aviso_cabeza = buscar_momentos(segmentos)
    tmp = Path(tempfile.mkdtemp())
    cierre_png = tmp / "cierre.png"
    try:
        t_datos, t_tab, cartel_tt, datos_con_cartel = buscar_tablero(vod, t_cab)
    except SinTablero:
        datos, avisos = datos_hibrido(segmentos, t_cab, fila_cronograma, cierre_png)
        t_datos = t_tab = None
        cabeza_dudosa = False  # sale del extracto oficial
    else:
        capturar(vod, t_datos, datos_png := tmp / "datos.png", recorte=TABLERO)
        capturar(vod, t_tab, cierre_png)
        capturar(vod, t_tab, cierre_tab_png := tmp / "cierre_tablero.png", recorte=TABLERO)
        ocr = leer_tablero(datos_png)
        # la cabeza va en el título: se vota entre varias lecturas de los dos tableros y manda la que cantó el locutor
        lecturas = leer_cabeza(datos_png) + leer_cabeza(cierre_tab_png)
        ocr["cabeza"], confirmada = elegir_cabeza(lecturas, cantados_en_cabeza(segmentos, t_cab), sin_voz)
        datos, avisos = combinar_datos(ocr, fila_cronograma, datos_con_cartel)
        cabeza_dudosa = not confirmada or not re.fullmatch(r"\d{4}", datos["cabeza"])
        if ocr["cabeza"] and not confirmada and sin_voz:
            avisos.append(f"Sin audio: las lecturas de la cabeza en el tablero no coinciden ({', '.join(lecturas)})")
        elif ocr["cabeza"] and not confirmada:
            avisos.append(f"La cabeza del tablero ({ocr['cabeza']}) no coincide con la que cantó el locutor "
                          f"(lecturas: {', '.join(lecturas)})")
        if cartel_tt and not datos["tombola_total"]:
            avisos.append("Se vio el cartel de Tómbola Total pero el cronograma no lo indica")
    if aviso_cabeza:
        avisos.append(aviso_cabeza)
        cabeza_dudosa = True

    b_ini = max(t_ini, t_cab - config.ANTES_CABEZA - config.BIENVENIDA_DUR)  # sin solapar con el tramo de la cabeza
    b_dur = min(config.BIENVENIDA_DUR, t_cab - config.ANTES_CABEZA - b_ini)
    c_ini = max(t_ini, t_cab - config.ANTES_CABEZA)
    c_dur = (t_cab - c_ini) + config.DESPUES_CABEZA
    con_bienvenida = b_dur >= 3

    piezas = [config.INTRO_DUR] + ([b_dur] if con_bienvenida else []) + [c_dur, config.CIERRE_DUR]
    n = len(piezas)
    total = sum(piezas) - config.FUNDIDO * (n - 1)
    avisos += revisar_locucion(segmentos, b_ini if con_bienvenida else c_ini, c_ini + c_dur)

    # capítulos de YouTube (en segundos dentro del compacto); cada uno tiene que durar 10 s o más
    ini_cabeza = total - config.CIERRE_DUR - c_dur + config.FUNDIDO
    capitulos = [(0, "Presentación"), (ini_cabeza, "Extracción"),
                 (ini_cabeza + (t_cab - c_ini) - 3, f"A la cabeza: {datos['cabeza']}"),
                 (total - config.CIERRE_DUR, "Resultados")]
    validos = [capitulos[0]]
    for c in capitulos[1:]:
        if c[0] - validos[-1][0] >= 10 and total - c[0] >= 10:
            validos.append(c)
    capitulos = validos

    entradas = ["-loop", "1", "-framerate", "30", "-t", str(config.INTRO_DUR), "-i", str(config.FONDO_INTRO), "-i", str(config.LOGO)]
    if con_bienvenida:
        entradas += ["-ss", f"{b_ini:.2f}", "-t", f"{b_dur:.2f}", "-i", str(vod)]
    entradas += ["-ss", f"{c_ini:.2f}", "-t", f"{c_dur:.2f}", "-i", str(vod),
                 "-loop", "1", "-framerate", "30", "-t", str(config.CIERRE_DUR), "-i", str(cierre_png),
                 "-stream_loop", "-1", "-i", str(config.CORTINA)]
    i_mus = n + 1  # la intro usa 2 entradas (fondo + logo)

    norm = f"scale=1920:1080,setsar=1,fps=30,{EN_VIVO},format=yuv420p"
    f = [filtro_intro(datos)]
    f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{config.INTRO_DUR}[a0]")
    k, entrada = 1, 2
    for _ in range(2 if con_bienvenida else 1):  # tramos del VOD
        f.append(f"[{entrada}:v]{norm}[v{k}]")
        if sin_voz:  # sin locutor: silencio (si quedó ruido de fondo en la transmisión, no se escucha)
            f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{piezas[k]:.2f}[a{k}]")
        else:
            f.append(f"[{entrada}:a]aresample=48000,aformat=channel_layouts=stereo[a{k}]")
        k, entrada = k + 1, entrada + 1
    f.append(f"[{entrada}:v]scale=1920:1080,setsar=1,fps=30,trim=duration={config.CIERRE_DUR},format=yuv420p[v{k}]")  # cierre: tablero o placa
    f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{config.CIERRE_DUR}[a{k}]")

    # encadenar con fundidos
    v_prev, a_prev, offset = "v0", "a0", 0.0
    for j in range(1, n):
        offset += piezas[j - 1] - config.FUNDIDO
        f.append(f"[{v_prev}][v{j}]xfade=transition=fade:duration={config.FUNDIDO}:offset={offset:.2f}[vx{j}]")
        f.append(f"[{a_prev}][a{j}]acrossfade=d={config.FUNDIDO}[ax{j}]")
        v_prev, a_prev = f"vx{j}", f"ax{j}"

    # voz normalizada + cortina con ducking (baja cuando habla el locutor)
    f.append(f"[{a_prev}]highpass=f=80,dynaudnorm=f=200:g=15,asplit=2[voz][llave]")
    f.append(f"[{i_mus}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.2f},"
             f"volume={config.MUSICA_DB}dB,afade=t=in:d=1,afade=t=out:st={total - 2:.2f}:d=2[mus]")
    f.append("[mus][llave]sidechaincompress=threshold=0.02:ratio=8:attack=50:release=600[musduck]")
    f.append("[voz][musduck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    nombre = re.sub(r"\W+", "_", f"{datos['fecha']}_{datos['sorteo_nombre']}_{datos['sorteo_numero']}").strip("_")
    destino = salida / f"compacto_{nombre}.mp4"
    cmd = [config.FFMPEG, "-v", "error", "-y", *entradas, "-filter_complex", ";".join(f),
           "-map", f"[{v_prev}]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           # formatos estandar: sin esto sale yuv444p (por xfade) y audio a 96 kHz (por loudnorm),
           # que la mayoria de los reproductores no abre
           "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", str(destino)]
    subprocess.run(cmd, check=True)

    info = {**datos, "compacto": str(destino), "miniatura": str(destino.with_suffix(".jpg")),
            "duracion": round(total, 1), "inicio_bienvenida": round(t_ini, 1), "momento_cabeza": round(t_cab, 1),
            "momento_datos": t_datos and round(t_datos, 1), "momento_tablero": t_tab and round(t_tab, 1), "avisos": avisos,
            "cabeza_dudosa": cabeza_dudosa, "sin_audio": sin_voz,  # el título puede llevar mal la cabeza: se corrige a mano en YouTube
            "capitulos": [[round(t), nombre] for t, nombre in capitulos]}
    Image.open(cierre_png).convert("RGB").save(info["miniatura"], quality=90)  # el tablero (o la placa) sirve de miniatura
    destino.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(tmp, ignore_errors=True)
    return info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("vod", type=Path)
    ap.add_argument("transcripcion", type=Path)
    ap.add_argument("--salida", type=Path, default=config.SALIDA)
    a = ap.parse_args()
    segs = json.loads(a.transcripcion.read_text(encoding="utf-8"))
    print(json.dumps(armar(a.vod, segs, a.salida), ensure_ascii=False, indent=2))
