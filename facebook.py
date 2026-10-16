"""Sube el compacto a la página de Facebook (Graph API), opcional.

Se sube con el mismo título que en YouTube; la descripción va sin capítulos y con los enlaces al sitio y a
Twitch (que YouTube no permite). Solo actúa con FB_ACTIVO=true, FB_PAGE_ID / FB_PAGE_TOKEN configurados y para
los sorteos de FB_SORTEOS.
Si FB_PUBLICAR no es "true", o el compacto tiene avisos (salvo el +18 de YouTube, que acá no aplica), el video
queda sin publicar en la biblioteca de videos de la página (Meta Business Suite > Contenido) para revisarlo y
publicarlo a mano.

Mayores de 18: la API no permite restringir por edad cada video; la restricción se configura una sola vez
para toda la página (Configuración > Restricción de edad > 18+), y así aplica a todo lo que se publica.

Requiere un token de página permanente con los permisos pages_manage_posts y pages_read_engagement.
Probar el token:  python facebook.py verificar
Estado de un video subido:  python facebook.py estado VIDEO_ID
"""
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


if __name__ == "__main__":
    if sys.argv[1:2] in (["verificar"], ["estado"]) and not configurado():
        sys.exit("Faltan FB_PAGE_ID y FB_PAGE_TOKEN")
    if sys.argv[1:] == ["verificar"]:
        verificar()
    elif sys.argv[1:2] == ["estado"] and len(sys.argv) == 3:
        estado(sys.argv[2])
    else:
        print(__doc__)
