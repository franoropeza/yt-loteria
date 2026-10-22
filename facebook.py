"""Sube el compacto a la página de Facebook (Graph API), opcional.

Se sube con el mismo título que en YouTube; la descripción va sin capítulos y con los enlaces al sitio y a
Twitch (que YouTube no permite). Solo actúa con FB_ACTIVO=true, FB_PAGE_ID / FB_PAGE_TOKEN configurados y para
los sorteos de FB_SORTEOS.
Si FB_PUBLICAR no es "true", o el compacto tiene avisos (salvo el +18 de YouTube, que acá no aplica), el video
queda sin publicar en la biblioteca de videos de la página (Meta Business Suite > Contenido) para revisarlo y
publicarlo a mano.

De los demás sorteos, y de los de FB_SORTEOS cuya cabeza no se pudo identificar bien (el título se corrige a mano en
YouTube), no se sube el video: se comparte en el muro el enlace de YouTube, recién cuando el video está
público y solo si eso pasa dentro de FB_LIMITE_MIN desde el sorteo (ver compartir.py).

Mayores de 18: la API no permite restringir por edad cada video; la restricción se configura una sola vez
para toda la página (Configuración > Restricción de edad > 18+), y así aplica a todo lo que se publica.

FB_ENDPOINT elige cómo se sube: "reels" (API de Reels, /video_reels; máximo 90 s) o "videos" (/videos, el original).

Requiere un token de página permanente con los permisos pages_manage_posts y pages_read_engagement.
Probar el token:  python facebook.py verificar
Estado de un video subido:  python facebook.py estado VIDEO_ID
Probar la subida por Reels con un clip de 5 s en borrador (no se publica y después se borra):  python facebook.py probar
"""
import datetime as dt
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config
from formulario import multipart


def configurado():
    return bool(config.FB_PAGE_ID and config.FB_PAGE_TOKEN)


def activo(sorteo_nombre=None):
    """True si hay que subir a Facebook (y, con `sorteo_nombre`, si ese sorteo está en FB_SORTEOS)."""
    if not (config.FB_ACTIVO and configurado()):
        return False
    return sorteo_nombre is None or not config.FB_SORTEOS or sorteo_nombre.upper() in config.FB_SORTEOS


def comparte_enlace(sorteo_nombre):
    """True si del sorteo se comparte en la página el enlace de YouTube: los que no están en FB_SORTEOS (esos se
    suben como video)."""
    return activo() and not activo(sorteo_nombre)


def limite(info):
    """Hasta cuándo se publica en Facebook: FB_LIMITE_MIN desde la hora del sorteo (después ya se publicó el extracto
    como imagen). None si no hay hora confiable (sorteo fuera del cronograma): no se arriesga."""
    try:
        hora = dt.datetime.strptime(f"{info['fecha']} {info['hora']}", "%d/%m/%Y %H:%M").replace(tzinfo=config.ARG)
    except (KeyError, ValueError):
        return None
    return hora + dt.timedelta(minutes=config.FB_LIMITE_MIN)


def mensaje(datos):
    """Texto de la publicación con el enlace: el mismo título y descripción que el video subido a Facebook."""
    import youtube
    titulo, descripcion, _ = youtube.metadatos(datos, facebook=True)
    return f"{titulo}\n\n{descripcion}"


def compartir(url_youtube, texto, publicar):
    """Publica el enlace de YouTube en el muro de la página. Devuelve {post_id, url, publicado}."""
    campos = {"message": texto, "link": url_youtube, "published": "true" if publicar else "false",
              "access_token": config.FB_PAGE_TOKEN}
    post_id = _llamar(f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/{config.FB_PAGE_ID}/feed",
                      urllib.parse.urlencode(campos).encode())["id"]
    # el enlace armado con el ID no siempre abre: se usa el que da Facebook
    url = _consultar(post_id, "permalink_url").get("permalink_url") or f"https://www.facebook.com/{post_id}"
    return {"post_id": post_id, "url": url, "publicado": publicar, "enlace": True}


def _llamar(url, datos=None, tipo=None, timeout=60):
    pedido = urllib.request.Request(url, data=datos, headers={"Content-Type": tipo} if tipo else {})
    try:
        with urllib.request.urlopen(pedido, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:  # Facebook explica el error en el cuerpo de la respuesta
        try:
            detalle = json.load(e)["error"]["message"]
        except Exception:
            detalle = e.reason
        raise RuntimeError(f"Facebook respondió {e.code}: {detalle}") from None


def subir(video, datos, publicar, miniatura=None):
    """Sube el video a la página. Devuelve {video_id, url, publicado}."""
    import youtube  # mismos textos que en YouTube
    titulo, descripcion, _ = youtube.metadatos(datos, facebook=True)
    if config.FB_ENDPOINT == "reels":
        return _subir_reel(video, titulo, descripcion, publicar)
    campos = {"access_token": config.FB_PAGE_TOKEN, "title": titulo, "description": descripcion,
              "published": "true" if publicar else "false"}
    archivos = {"source": Path(video)}
    if miniatura and Path(miniatura).exists():
        archivos["thumb"] = Path(miniatura)
    cuerpo, tipo = multipart(campos, archivos)
    url = f"https://graph-video.facebook.com/{config.FB_GRAPH_VERSION}/{config.FB_PAGE_ID}/videos"
    video_id = _llamar(url, cuerpo, tipo, timeout=600)["id"]
    return {"video_id": video_id, "url": f"https://www.facebook.com/{config.FB_PAGE_ID}/videos/{video_id}",
            "publicado": publicar}


def _subir_reel(video, titulo, descripcion, publicar):
    """Sube por la API de Reels (/video_reels): iniciar, mandar el archivo a rupload y cerrar publicando.
    Facebook de todos modos convertía en reel lo subido por /videos. Acá no va la miniatura (la API no la recibe)."""
    base = f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/{config.FB_PAGE_ID}/video_reels"
    token = config.FB_PAGE_TOKEN
    inicio = _llamar(base, urllib.parse.urlencode({"upload_phase": "start", "access_token": token}).encode())
    video_id = inicio["video_id"]
    contenido = Path(video).read_bytes()
    pedido = urllib.request.Request(
        inicio.get("upload_url") or f"https://rupload.facebook.com/video-upload/{config.FB_GRAPH_VERSION}/{video_id}",
        data=contenido, headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(contenido))})
    try:
        with urllib.request.urlopen(pedido, timeout=600) as r:
            if not json.load(r).get("success"):
                raise RuntimeError("Facebook no confirmó la subida del reel")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Facebook respondió {e.code} al subir el reel: {e.read().decode(errors='replace')}") from None
    fin = {"upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED" if publicar else "DRAFT",
           "title": titulo, "description": descripcion, "access_token": token}
    if not _llamar(base, urllib.parse.urlencode(fin).encode()).get("success"):
        raise RuntimeError("Facebook no confirmó la publicación del reel")
    return {"video_id": video_id, "url": f"https://www.facebook.com/reel/{video_id}", "publicado": publicar,
            "endpoint": "reels"}


def verificar():
    """Muestra a qué página corresponde el token (si vence o no tiene permisos, Facebook lo dice)."""
    consulta = urllib.parse.urlencode({"fields": "id,name", "access_token": config.FB_PAGE_TOKEN})
    pagina = _llamar(f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/me?{consulta}")
    print(f"Token OK: página {pagina['name']} ({pagina['id']})")
    if pagina["id"] != config.FB_PAGE_ID:
        print(f"⚠️ FB_PAGE_ID es {config.FB_PAGE_ID}: no coincide con la página del token")


def _consultar(ruta, campos):
    consulta = urllib.parse.urlencode({"fields": campos, "access_token": config.FB_PAGE_TOKEN})
    try:
        return _llamar(f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/{ruta}?{consulta}")
    except RuntimeError as e:
        return {"error": str(e)}


def estado(video_id):
    """Lo que la API dice de un video subido: procesamiento, publicación y si aparece entre las publicaciones de la
    página. Para diagnosticar videos que se ven en la computadora pero no en el celular."""
    video = _consultar(video_id, "id,title,published,created_time,length,permalink_url,privacy,status")
    pagina = _consultar(config.FB_PAGE_ID, "id,name,is_published")
    posts = _consultar(f"{config.FB_PAGE_ID}/posts", "id,created_time,is_published,permalink_url")
    # campos que delatan bloqueos (copyright, segmentación, publicación oculta); de a uno, porque si la API no
    # conoce alguno rechaza toda la consulta
    def de_a_uno(ruta, campos):
        return {c: r["error"] if "error" in (r := _consultar(ruta, c)) else r.get(c, "(sin dato)") for c in campos}

    extra_video = de_a_uno(video_id, ("copyright_check_information", "embeddable", "is_crossposting_eligible",
                                      "content_category", "post_id", "is_instagram_eligible"))
    publicacion = next((p for p in posts.get("data", []) if video_id in p.get("permalink_url", "")), None)
    extra_post = de_a_uno(publicacion["id"], ("is_hidden", "is_expired", "is_eligible_for_promotion", "privacy",
                                              "status_type", "feed_targeting", "targeting")) if publicacion else {}
    extra_pagina = de_a_uno(config.FB_PAGE_ID, ("is_always_open", "is_permanently_closed", "is_unclaimed",
                                                "restrictions", "verification_status"))
    print(json.dumps({"video": video, "video_extra": extra_video, "publicacion": publicacion, "publicacion_extra": extra_post,
                      "pagina": pagina, "pagina_extra": extra_pagina,
                      "ultimas_publicaciones": posts["data"][:5] if "data" in posts else posts},
                     ensure_ascii=False, indent=2))


def probar():
    """Sube por /video_reels un clip de prueba como borrador, muestra su estado y lo borra."""
    import subprocess
    import tempfile
    clip = Path(tempfile.mkdtemp()) / "prueba.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=5",
                    "-f", "lavfi", "-i", "sine=d=5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(clip)], check=True)
    r = _subir_reel(clip, "Prueba de subida (borrador)", "Prueba", publicar=False)
    print(f"Subido en borrador: {r['video_id']}")
    import time
    time.sleep(20)  # que Facebook alcance a procesarlo
    estado(r["video_id"])
    borrado = _llamar(f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/{r['video_id']}?"
                      + urllib.parse.urlencode({"access_token": config.FB_PAGE_TOKEN, "method": "delete"}))
    print(f"Borrado: {borrado}")


if __name__ == "__main__":
    if sys.argv[1:2] in (["verificar"], ["estado"], ["probar"]) and not configurado():
        sys.exit("Faltan FB_PAGE_ID y FB_PAGE_TOKEN")
    if sys.argv[1:] == ["verificar"]:
        verificar()
    elif sys.argv[1:2] == ["estado"] and len(sys.argv) == 3:
        estado(sys.argv[2])
    elif sys.argv[1:] == ["probar"]:
        probar()
    else:
        print(__doc__)
