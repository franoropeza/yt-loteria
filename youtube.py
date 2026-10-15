"""Sube el compacto a YouTube (YouTube Data API v3).

Todos los videos tienen que quedar restringidos a mayores de 18. La API no documenta cómo marcarlo
(contentDetails.contentRating.ytRating figura como solo lectura), así que:
  1. se sube siempre como privado, intentando marcar la restricción;
  2. se lee el video y solo si YouTube confirma la restricción se aplica la privacidad configurada;
  3. si no, queda privado y se avisa para marcarlo a mano en YouTube Studio.

Costo de cuota por video: videos.insert 1600 + thumbnails.set 50 + videos.list 1 + videos.update 50
+ playlistItems.insert 50 (cuota diaria estándar: 10.000).

Autorizar por primera vez (genera token.json; su contenido va al secreto YT_TOKEN_JSON de GitHub):
  python youtube.py autorizar ruta/client_secret.json
"""
import datetime as dt
import json
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import config

SCOPES = ["https://www.googleapis.com/auth/youtube"]

LEYENDA = ("+18 Jugar compulsivamente es perjudicial para la salud. Prohibida su venta a menores de 18 años. "
           "Para más información comunicarse al 387-5420599 / 0800-55 5846 (JUEGO).")
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
HORARIOS = ("Sorteos de la Tómbola: lunes a viernes 12:00, 15:00, 18:00, 21:00 y 22:30 hs; "
            "sábados 12:00, 15:00, 21:00 y 22:30 hs; domingos 12:00 y 15:00 hs.")


def credenciales():
    if config.YT_TOKEN_JSON:
        info = json.loads(config.YT_TOKEN_JSON)
    elif config.YT_TOKEN_ARCHIVO.exists():
        info = json.loads(config.YT_TOKEN_ARCHIVO.read_text(encoding="utf-8"))
    else:
        raise RuntimeError("Falta el token de YouTube (variable YT_TOKEN_JSON o archivo token.json)")
    cred = Credentials.from_authorized_user_info(info, SCOPES)
    if not cred.valid:
        cred.refresh(Request())
    return cred


def _mmss(segundos):
    return f"{int(segundos) // 60}:{int(segundos) % 60:02d}"


def metadatos(d, facebook=False):
    """Título, descripción y etiquetas pensados para búsqueda ("tómbola salta matutina hoy", "quiniela de salta").

    facebook=True: sin capítulos (Facebook no los usa) y con "Seguí la página" en vez de "Suscribite"."""
    nombre = d["sorteo_nombre"].title()
    fecha = dt.datetime.strptime(d["fecha"], "%d/%m/%Y")
    dia = DIAS[fecha.weekday()]

    # el N° de Tómbola Total va solo en la descripción, no en el título
    titulo = f"Tómbola de Salta {nombre} {d['fecha']} | Sorteo {d['sorteo_numero']} | A la cabeza {d['cabeza']}"

    lineas = [
        f"Resultados de la Tómbola de Salta, sorteo {nombre} N° {d['sorteo_numero']} "
        f"del {dia} {d['fecha']} a las {d['hora']} hs.",
        f"A la cabeza: {d['cabeza']}",
        "",
    ]
    if d.get("extracto"):  # compacto de respaldo: los 20 números de la API oficial
        lineas += ["Extracto oficial:"] + [f"{i:>2}. {n}" for i, n in enumerate(d["extracto"], 1)] + [""]
    if d.get("changuita"):
        lineas.append(f"La Changuita: sorteo N° {d['changuita']}.")
    if d.get("tombola_total"):
        lineas.append(f"Tómbola Total: sorteo N° {d['tombola_total']}.")
    if d.get("capitulos") and not facebook:
        lineas += ["", "Capítulos:"] + [f"{_mmss(t)} {texto}" for t, texto in d["capitulos"]]
    # YouTube no deja poner enlaces en la descripción: el sitio y Twitch se mencionan sin URL (en Facebook, sí)
    sitio = "https://www.loteriadesalta.com" if facebook else "nuestro sitio web oficial."  # sin punto pegado a la URL
    lineas += [
        "",
        f"Extracto oficial publicado en {sitio}" if d.get("extracto")
        else f"Resultados provisorios. El extracto oficial se publica en {sitio}",
    ]
    if not d.get("extracto"):  # el respaldo se arma justamente cuando no hay sorteo en Twitch
        lineas.append(f"Sorteo completo en vivo: https://www.twitch.tv/{config.TWITCH_CANAL or 'loteriadesalta'}"
                      if facebook else "Para ver el sorteo completo, visitá nuestro canal oficial de Twitch.")
    lineas += [
        HORARIOS,
        "Seguí la página para ver el resumen de cada sorteo." if facebook
        else "Suscribite para ver el resumen de cada sorteo.",
        "",
        LEYENDA,
        "",
        f"#TómbolaDeSalta #QuinielaDeSalta #Tómbola{nombre.replace(' ', '')}",
    ]

    etiquetas = ["Tómbola de Salta", "Tómbola Salta", "Quiniela de Salta", "Quiniela Salta", "Lotería de Salta",
                 f"Tómbola {nombre}", f"Tómbola {nombre} hoy", f"Quiniela {nombre} Salta", f"Tómbola {d['fecha']}",
                 "resultados Tómbola", "sorteo Tómbola", "La Changuita", "extracto Tómbola"]
    if d.get("tombola_total"):
        etiquetas.append("Tómbola Total")
    return titulo[:100], "\n".join(lineas), etiquetas


def subir(video, datos, privacidad, miniatura=None):
    """Sube el video. Devuelve {video_id, url, titulo, privacidad, mayores_18}."""
    yt = build("youtube", "v3", credentials=credenciales(), cache_discovery=False)
    titulo, descripcion, etiquetas = metadatos(datos)
    inicio = dt.datetime.strptime(f"{datos['fecha']} {datos['hora']}", "%d/%m/%Y %H:%M").replace(tzinfo=config.ARG)
    cuerpo = {
        "snippet": {"title": titulo, "description": descripcion, "categoryId": config.YT_CATEGORIA,
                    "tags": etiquetas, "defaultLanguage": "es-419", "defaultAudioLanguage": "es-419"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
        "recordingDetails": {"recordingDate": inicio.isoformat()},
        "contentDetails": {"contentRating": {"ytRating": "ytAgeRestricted"}},  # intento de marcar +18
    }

    def insertar(partes):
        media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        pedido = yt.videos().insert(part=",".join(partes), body={p: cuerpo[p] for p in partes}, media_body=media)
        respuesta = None
        while respuesta is None:
            _, respuesta = pedido.next_chunk()
        return respuesta

    partes = ["snippet", "status", "recordingDetails", "contentDetails"]
    try:
        respuesta = insertar(partes)
    except HttpError as e:
        if e.resp.status != 400:
            raise
        # la API rechazó contentDetails: se sube sin él (el error llega antes de mandar el video)
        respuesta = insertar(partes[:-1])
    video_id = respuesta["id"]

    if miniatura:
        try:  # requiere el canal verificado por teléfono; si falla, YouTube elige un cuadro
            yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(miniatura))).execute()
        except HttpError as e:
            print(f"No se pudo poner la miniatura: {e}")

    leido = yt.videos().list(part="contentDetails", id=video_id).execute()["items"][0]
    mayores_18 = leido["contentDetails"].get("contentRating", {}).get("ytRating") == "ytAgeRestricted"
    privacidad_final = "private"
    if mayores_18 and privacidad != "private":
        yt.videos().update(part="status", body={"id": video_id, "status": {
            "privacyStatus": privacidad, "selfDeclaredMadeForKids": False}}).execute()
        privacidad_final = privacidad

    playlists = [config.YT_PLAYLIST, json.loads(config.YT_PLAYLISTS or "{}").get(datos["sorteo_nombre"])]
    for pl in dict.fromkeys(p for p in playlists if p):  # sin repetir
        yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    return {"video_id": video_id, "url": f"https://youtu.be/{video_id}", "titulo": titulo,
            "privacidad": privacidad_final, "mayores_18": mayores_18}


def autorizar(client_secret):
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
    cred = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    config.YT_TOKEN_ARCHIVO.write_text(cred.to_json(), encoding="utf-8")
    print(f"Token guardado en {config.YT_TOKEN_ARCHIVO}. Copiá su contenido al secreto YT_TOKEN_JSON.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "autorizar":
        autorizar(sys.argv[2])
    elif len(sys.argv) == 2:  # vista previa de título y descripción a partir del .json de un compacto
        t, desc, tags = metadatos(json.loads(open(sys.argv[1], encoding="utf-8").read()))
        print(f"TÍTULO ({len(t)}): {t}\n\n{desc}\n\nETIQUETAS ({len(','.join(tags))} caracteres): {', '.join(tags)}")
    else:
        print(__doc__)
