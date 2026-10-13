"""Sube el compacto a YouTube (YouTube Data API v3).

Costo de cuota: videos.insert 1600 + thumbnails.set 50 + playlistItems.insert 50 por video (cuota diaria estándar: 10.000).

Autorizar por primera vez (genera token.json; su contenido va al secreto YT_TOKEN_JSON de GitHub):
  python youtube.py autorizar ruta/client_secret.json
"""
import json
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import config

SCOPES = ["https://www.googleapis.com/auth/youtube"]

LEYENDA = ("+18 Jugar compulsivamente es perjudicial para la salud. Prohibida su venta a menores de 18 años. "
           "Para más información comunicarse al 387-5420599 / 0800-55 5846 (JUEGO).")


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


def titulo_y_descripcion(d):
    nombre = d["sorteo_nombre"].title()
    titulo = f"Tómbola {nombre} N° {d['sorteo_numero']} - {d['fecha']} | A la cabeza {d['cabeza']}"
    lineas = [
        f"Sorteo de la Tómbola {nombre} N° {d['sorteo_numero']} del {d['fecha']} a las {d['hora']} hs.",
        f"A la cabeza: {d['cabeza']}",
    ]
    if d.get("tombola_total"):
        lineas.append(f"Incluye el sorteo de Tómbola Total N° {d['tombola_total']}.")
    lineas += ["", "Resultados provisorios. Los resultados oficiales se publican en www.loteriadesalta.com", "",
               LEYENDA]
    return titulo[:100], "\n".join(lineas)


def subir(video, datos, privacidad, miniatura=None):
    yt = build("youtube", "v3", credentials=credenciales(), cache_discovery=False)
    titulo, descripcion = titulo_y_descripcion(datos)
    cuerpo = {
        "snippet": {"title": titulo, "description": descripcion, "categoryId": config.YT_CATEGORIA,
                    "tags": ["Tómbola", "Lotería de Salta", "sorteo", datos["sorteo_nombre"].title(), "resultados"],
                    "defaultLanguage": "es", "defaultAudioLanguage": "es"},
        "status": {"privacyStatus": privacidad, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    pedido = yt.videos().insert(part="snippet,status", body=cuerpo, media_body=media)
    respuesta = None
    while respuesta is None:
        _, respuesta = pedido.next_chunk()
    video_id = respuesta["id"]

    if miniatura:
        try:  # requiere el canal verificado por teléfono; si falla, YouTube elige un cuadro
            yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(miniatura))).execute()
        except Exception as e:
            print(f"No se pudo poner la miniatura: {e}")

    playlists = json.loads(config.YT_PLAYLISTS or "{}")
    if pl := playlists.get(datos["sorteo_nombre"]):
        yt.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": pl, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    return {"video_id": video_id, "url": f"https://youtu.be/{video_id}", "titulo": titulo,
            "privacidad": respuesta["status"]["privacyStatus"]}


def autorizar(client_secret):
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
    cred = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    config.YT_TOKEN_ARCHIVO.write_text(cred.to_json(), encoding="utf-8")
    print(f"Token guardado en {config.YT_TOKEN_ARCHIVO}. Copiá su contenido al secreto YT_TOKEN_JSON.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "autorizar":
        autorizar(sys.argv[2])
    else:
        print(__doc__)
