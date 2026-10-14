"""Avisos por Telegram (opcional): un mensaje por cada compacto subido o fallido.

Configuración: crear un bot con @BotFather (da el token) y obtener el chat_id del chat o grupo
donde llegan los avisos (ver README). Sin TELEGRAM_TOKEN / TELEGRAM_CHAT_ID no hace nada.
"""
import json
import mimetypes
import urllib.request
import uuid

import config

LIMITE_EPIGRAFE = 1024  # Telegram corta los epígrafes de fotos en 1024 caracteres


def configurado():
    return bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)


def _llamar(metodo, campos, archivo=None):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/{metodo}"
    if archivo is None:
        datos, tipo = json.dumps(campos).encode(), "application/json"
    else:  # multipart/form-data a mano para no sumar dependencias
        nombre_campo, ruta = archivo
        borde = uuid.uuid4().hex
        partes = [f'--{borde}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                  for k, v in campos.items()]
        partes.append(f'--{borde}\r\nContent-Disposition: form-data; name="{nombre_campo}"; '
                      f'filename="{ruta.name}"\r\nContent-Type: {mimetypes.guess_type(ruta.name)[0] or "image/jpeg"}'
                      f'\r\n\r\n'.encode() + ruta.read_bytes() + b"\r\n")
        partes.append(f"--{borde}--\r\n".encode())
        datos, tipo = b"".join(partes), f"multipart/form-data; boundary={borde}"
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
    else:
        lineas.append("Armado sin subir (prueba)")
    if avisos := registro.get("avisos"):
        lineas += ["", "⚠️ Revisar:"] + [f"• {a}" for a in avisos]
    return "\n".join(lineas)


def texto_error(vod, registro):
    return (f"❌ No se pudo armar el compacto de {vod['titulo']} (VOD {vod['id']})\n"
            f"{registro.get('error', '')}\n"
            f"Intento {registro['intentos']} de {config.MAX_INTENTOS}"
            + (" · se reintenta en la próxima corrida" if registro["estado"] == "error" else " · abandonado"))
