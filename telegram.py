"""Avisos por Telegram (opcional): un mensaje por cada compacto subido o fallido.

Configuración: crear un bot con @BotFather (da el token) y obtener el chat_id del chat o grupo
donde llegan los avisos (ver README). Sin TELEGRAM_TOKEN / TELEGRAM_CHAT_ID no hace nada.
"""
import json
import urllib.request

import config
from formulario import multipart

LIMITE_EPIGRAFE = 1024  # Telegram corta los epígrafes de fotos en 1024 caracteres


def configurado():
    return bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)


def _llamar(metodo, campos, archivo=None):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/{metodo}"
    if archivo is None:
        datos, tipo = json.dumps(campos).encode(), "application/json"
    else:
        nombre_campo, ruta = archivo
        datos, tipo = multipart(campos, {nombre_campo: ruta})
    pedido = urllib.request.Request(url, data=datos, headers={"Content-Type": tipo})
    with urllib.request.urlopen(pedido, timeout=60) as r:
        return json.load(r)


def avisar(texto, foto=None):
    """Manda el aviso. Nunca corta el proceso: si Telegram falla, solo lo informa en el log."""
    if not configurado():
        return
    try:
        if foto and foto.exists() and len(texto) <= LIMITE_EPIGRAFE:
            _llamar("sendPhoto", {"chat_id": config.TELEGRAM_CHAT_ID, "caption": texto}, ("photo", foto))
        else:
            _llamar("sendMessage", {"chat_id": config.TELEGRAM_CHAT_ID, "text": texto,
                                    "disable_web_page_preview": True})
    except Exception as e:
        print(f"No se pudo avisar por Telegram: {e}")


def texto_compacto(registro):
    """Mensaje para un compacto subido (o armado sin subir)."""
    lineas = [f"🎬 {registro.get('titulo') or registro.get('sorteo', '') + ' ' + registro.get('fecha', '')}",
              f"A la cabeza: {registro.get('cabeza', '?')}"]
    if vid := registro.get("video_id"):
        lineas += [f"Subido como {registro['privacidad']}"
                   + (" · +18 confirmado" if registro.get("mayores_18") else " · falta marcar +18"),
                   f"Revisar y publicar: https://studio.youtube.com/video/{vid}/edit"]
        if fb := registro.get("facebook"):
            lineas.append(f"Facebook ({'publicado' if fb['publicado'] else 'sin publicar'}): {fb['url']}")
    else:
        lineas.append("Armado sin subir (prueba)")
    if avisos := registro.get("avisos"):
        lineas += ["", "⚠️ Revisar:"] + [f"• {a}" for a in avisos]
    return "\n".join(lineas)


def texto_error(vod, registro):
    return (f"❌ No se pudo armar el compacto de {vod['titulo']} (VOD {vod['id']})\n"
            f"{registro.get('error', '')}\n"
            f"Intento {registro['intentos']} de {config.MAX_INTENTOS}"
            + (" · se reintenta en la próxima corrida" if registro["estado"] == "error"
               else " · abandonado: se arma un compacto de respaldo con el extracto oficial"))


def texto_error_respaldo(fila, registro):
    return (f"❌ No se pudo armar el compacto de respaldo de {fila['nombre'].title()} N° {fila['sorteo']} "
            f"({fila['fecha']} {fila['hora']})\n{registro.get('error', '')}\n"
            f"Intento {registro['intentos']} de {config.MAX_INTENTOS}"
            + (" · se reintenta en la próxima corrida" if registro["estado"] == "error" else " · abandonado"))
