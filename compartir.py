"""Comparte el enlace de YouTube de los compactos cuando el video ya está público.

Destinos:
  - facebook: muro de la página, para los sorteos que no se suben como video (los que no están en FB_SORTEOS, y los de
    FB_SORTEOS cuya cabeza no se pudo identificar bien: se corrige a mano en YouTube), con la misma regla que el video: solo el compacto normal (no el híbrido ni el respaldo) y solo si queda público dentro de
    FB_LIMITE_MIN desde la hora del sorteo; pasado ese tiempo se descarta (ya se publicó el extracto como imagen);
  - telegram: canal público (TELEGRAM_CANAL_CHAT_ID), todos los compactos (normal, híbrido y respaldo), sin límite.

Al subir a YouTube, procesar.py deja el compartido pendiente en estado/procesados.json ("compartir_pendiente").
Cada corrida del workflow (job "compartir") mira la privacidad de los pendientes en YouTube y comparte los que ya
están públicos (por ejemplo, después de marcar el +18 a mano y publicarlo). La cabeza se toma del título actual en
YouTube, por si se corrigió a mano antes de publicarlo. Los demás siguen pendientes hasta que se
publiquen; si el video se borra de YouTube, se descartan. Si un destino falla, se reintenta en la próxima corrida.

Uso:  python compartir.py
"""
import datetime as dt
import json
import re

import config
import facebook
import telegram


def destinos(sorteo_nombre, extracto=False, cabeza_dudosa=False):
    """A dónde se comparte el enlace de un compacto de este sorteo (`extracto`: híbrido o respaldo; `cabeza_dudosa`:
    de los sorteos que se suben como video a Facebook, se comparte el enlace en su lugar)."""
    en_facebook = facebook.comparte_enlace(sorteo_nombre) or cabeza_dudosa and facebook.activo(sorteo_nombre)
    return ([d for d, activo in (("facebook", en_facebook and not extracto),
                                 ("telegram", telegram.canal_configurado())) if activo])


def pendiente(info, url_youtube, titulo):
    """Lo que se guarda en el registro para compartir el enlace más adelante (None si no va a ningún lado)."""
    lista = destinos(info["sorteo_nombre"], bool(info.get("extracto")), bool(info.get("cabeza_dudosa")))
    limite = facebook.limite(info)
    if "facebook" in lista and (not limite or dt.datetime.now(config.ARG) > limite):
        lista.remove("facebook")
    if not lista:
        return None
    p = {"url_youtube": url_youtube, "titulo": titulo, "destinos": lista}
    if "facebook" in lista:
        p.update(mensaje_facebook=facebook.mensaje(info), limite_facebook=limite.isoformat(timespec="seconds"))
    return p


def con_cabeza_del_titulo(texto, titulo):
    """El texto con la cabeza que figura ahora en el título de YouTube (si se corrigió a mano)."""
    if not (m := re.search(r"A la cabeza:? *(\d{4})", titulo)):
        return texto
    return re.sub(r"(A la cabeza:?) *\d*", lambda x: f"{x.group(1)} {m.group(1)}", texto)


def _compartir(destino, p, titulo):
    if destino == "facebook":
        return facebook.compartir(p["url_youtube"], con_cabeza_del_titulo(p["mensaje_facebook"], titulo),
                                  config.FB_PUBLICAR)
    return telegram.publicar_en_canal(titulo, p["url_youtube"])


def compartir_pendientes():
    """Comparte los enlaces pendientes cuyo video ya está público en YouTube. Guarda el estado si hubo cambios."""
    estado = json.loads(config.ESTADO.read_text(encoding="utf-8")) if config.ESTADO.exists() else {}
    pendientes = {k: r for k, r in estado.items() if r.get("compartir_pendiente")}
    if not pendientes:
        print("Sin enlaces pendientes")
        return
    import youtube
    videos = youtube.estados([r["video_id"] for r in pendientes.values()])
    for clave, r in pendientes.items():
        p = r["compartir_pendiente"]
        nombre = r.get("titulo") or r.get("sorteo", clave)
        if "facebook" in p["destinos"] and dt.datetime.now(config.ARG) > dt.datetime.fromisoformat(p["limite_facebook"]):
            p["destinos"].remove("facebook")
            r["facebook_omitido"] = (f"Pasaron más de {config.FB_LIMITE_MIN} min del sorteo sin el video público en "
                                     "YouTube: no se comparte en Facebook")
            print(f"{nombre}: {r['facebook_omitido']}")
            if not p["destinos"]:
                del r["compartir_pendiente"]
                continue
        if r["video_id"] not in videos:
            del r["compartir_pendiente"]
            r["compartir_omitido"] = "No se compartió el enlace: el video ya no está en YouTube"
            print(f"{nombre}: {r['compartir_omitido']}")
            continue
        video = videos[r["video_id"]]
        if video["privacidad"] != "public":
            print(f"{nombre}: todavía {video['privacidad']} en YouTube")
            continue
        for destino in list(p["destinos"]):
            # el destino se pudo apagar después de subir el video (FB_ACTIVO=false, canal sin configurar)
            if destino == "facebook" and not facebook.activo() or destino == "telegram" and not telegram.canal_configurado():
                print(f"{nombre}: {destino} desactivado, se descarta")
            else:
                try:
                    r["facebook" if destino == "facebook" else "telegram_canal"] = resultado = _compartir(destino, p, video["titulo"])
                except Exception as e:  # se reintenta en la próxima corrida
                    print(f"No se pudo compartir {nombre} en {destino}: {e}")
                    continue
                print(f"Compartido en {destino}: {nombre} {resultado['url']}")
                telegram.avisar(f"🔗 Enlace de YouTube compartido en {destino.title()}:\n{nombre}\n{resultado['url']}")
            p["destinos"].remove(destino)
        if not p["destinos"]:
            del r["compartir_pendiente"]
    config.ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    compartir_pendientes()
