"""Comparte en la página de Facebook (Graph API) el enlace de YouTube del compacto, opcional.

No se sube el video: se publica en el muro de la página el enlace https://youtu.be/... con el mismo título y la
descripción de Facebook (sin capítulos y con los enlaces al sitio y a Twitch, que YouTube no permite). Solo actúa con
FB_ACTIVO=true y FB_PAGE_ID / FB_PAGE_TOKEN configurados, y va con todos los compactos que se suben a YouTube
(todos los sorteos, también el híbrido y el respaldo).

El enlace se comparte recién cuando el video está público en YouTube. Al subirlo a YouTube, procesar.py deja el
compartido pendiente en estado/procesados.json ("facebook_pendiente"); cada corrida del workflow mira la privacidad
de los pendientes en YouTube (`python facebook.py compartir`) y comparte los que ya están públicos; los demás
siguen pendientes hasta que se publiquen (si el video se borra de YouTube, se descarta).
Si FB_PUBLICAR no es "true", la publicación queda sin publicar en la página para revisarla y publicarla a mano.

Mayores de 18: la restricción se configura una sola vez para toda la página (Configuración > Restricción de edad > 18+).

Requiere un token de página permanente con los permisos pages_manage_posts y pages_read_engagement.
Probar el token:  python facebook.py verificar
Estado de una publicación compartida:  python facebook.py publicacion POST_ID (workflow "Estado de una publicación en Facebook")
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

import config


def configurado():
    return bool(config.FB_PAGE_ID and config.FB_PAGE_TOKEN)


def activo():
    """True si hay que compartir en Facebook."""
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


def pendiente(datos, url_youtube):
    """Lo que se guarda en el registro para compartir el enlace cuando el video esté público en YouTube."""
    import youtube  # mismos textos que en YouTube
    titulo, descripcion, _ = youtube.metadatos(datos, facebook=True)
    return {"url_youtube": url_youtube, "mensaje": f"{titulo}\n\n{descripcion}"}


def compartir(url_youtube, mensaje, publicar):
    """Publica el enlace en el muro de la página. Devuelve {post_id, url, publicado}."""
    campos = {"message": mensaje, "link": url_youtube, "published": "true" if publicar else "false",
              "access_token": config.FB_PAGE_TOKEN}
    post_id = _llamar(f"https://graph.facebook.com/{config.FB_GRAPH_VERSION}/{config.FB_PAGE_ID}/feed",
                      urllib.parse.urlencode(campos).encode())["id"]
    # el enlace armado con el ID no siempre abre: se usa el que da Facebook (con el ID nuevo de la página)
    url = _consultar(post_id, "permalink_url").get("permalink_url") or f"https://www.facebook.com/{post_id}"
    return {"post_id": post_id, "url": url, "publicado": publicar}


def compartir_pendientes():
    """Comparte los enlaces pendientes cuyo video ya está público en YouTube y descarta los borrados.
    Guarda el estado si hubo pendientes."""
    import telegram
    import youtube
    estado = json.loads(config.ESTADO.read_text(encoding="utf-8")) if config.ESTADO.exists() else {}
    pendientes = {k: r for k, r in estado.items() if r.get("facebook_pendiente")}
    if not pendientes or not activo():
        print("Sin enlaces pendientes para Facebook")
        return
    privacidad = youtube.privacidades([r["video_id"] for r in pendientes.values()])
    for clave, r in pendientes.items():
        p = r["facebook_pendiente"]
        nombre = r.get("titulo") or r.get("sorteo", clave)
        if privacidad.get(r["video_id"]) == "public":
            try:
                r["facebook"] = compartir(p["url_youtube"], p["mensaje"], config.FB_PUBLICAR)
            except Exception as e:  # se reintenta en la próxima corrida
                print(f"No se pudo compartir {nombre} en Facebook: {e}")
                continue
            del r["facebook_pendiente"]
            print(f"Compartido en Facebook: {nombre} {r['facebook']['url']}")
            telegram.avisar(f"📘 Enlace de YouTube compartido en Facebook "
                            f"({'publicado' if config.FB_PUBLICAR else 'sin publicar'}):\n{nombre}\n{r['facebook']['url']}")
        elif r["video_id"] not in privacidad:
            del r["facebook_pendiente"]
            r["facebook_omitido"] = "No se compartió en Facebook: el video ya no está en YouTube"
            print(f"{nombre}: {r['facebook_omitido']}")
        else:
            print(f"{nombre}: todavía {privacidad[r['video_id']]} en YouTube")
    config.ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


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


def publicacion(post_id):
    """Lo que la API dice de una publicación compartida y de las últimas de la página (para ver por qué no aparece).
    Los campos van de a uno: si la API no conoce alguno, rechaza toda la consulta."""
    campos = ("created_time", "is_published", "is_hidden", "is_expired", "permalink_url", "privacy", "status_type",
              "attachments", "feed_targeting", "timeline_visibility", "message")
    post = {c: r["error"] if "error" in (r := _consultar(post_id, c)) else r.get(c, "(sin dato)") for c in campos}
    print(json.dumps({"publicacion": post,
                      "pagina": _consultar(config.FB_PAGE_ID, "name,is_published,link"),
                      "ultimas_publicadas": _consultar(f"{config.FB_PAGE_ID}/published_posts",
                                                       "id,created_time,is_published,permalink_url,status_type"),
                      "ultimas_del_muro": _consultar(f"{config.FB_PAGE_ID}/feed", "id,created_time,permalink_url")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if sys.argv[1:2] == ["publicacion"] and len(sys.argv) == 3:
        publicacion(sys.argv[2])
    elif sys.argv[1:] == ["verificar"]:
        if not configurado():
            sys.exit("Faltan FB_PAGE_ID y FB_PAGE_TOKEN")
        verificar()
    elif sys.argv[1:] == ["compartir"]:
        compartir_pendientes()
    else:
        print(__doc__)
