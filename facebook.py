"""Sube el compacto a la página de Facebook (Graph API), opcional.

Se sube con el mismo título y descripción que en YouTube (sin capítulos). Solo actúa con FB_ACTIVO=true
y FB_PAGE_ID / FB_PAGE_TOKEN configurados.
Si FB_PUBLICAR no es "true", o el compacto tiene avisos, el video queda sin publicar en la biblioteca de videos
de la página (Meta Business Suite > Contenido) para revisarlo y publicarlo a mano.

Mayores de 18: la API no permite restringir por edad cada video; la restricción se configura una sola vez
para toda la página (Configuración > Restricción de edad > 18+), y así aplica a todo lo que se publica.

Requiere un token de página permanente con los permisos pages_manage_posts y pages_read_engagement.
Probar el token:  python facebook.py verificar
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


def activo():
    return config.FB_ACTIVO and configurado()


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


if __name__ == "__main__":
    if sys.argv[1:] == ["verificar"]:
        if not configurado():
            sys.exit("Faltan FB_PAGE_ID y FB_PAGE_TOKEN")
        verificar()
    else:
        print(__doc__)
