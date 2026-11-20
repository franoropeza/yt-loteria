"""Configuración central. Todo lo sensible o que cambia entre entornos viene de variables de entorno
(en GitHub: Settings > Secrets and variables > Actions)."""
import datetime as dt
import os
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
ASSETS = BASE / "assets"
TRABAJO = Path(os.environ.get("COMPACTOS_TRABAJO", BASE / "trabajo"))  # descargas y temporales
SALIDA = Path(os.environ.get("COMPACTOS_SALIDA", BASE / "salida"))
ESTADO = BASE / "estado" / "procesados.json"

ARG = dt.timezone(dt.timedelta(hours=-3))  # hora de Argentina

# --- Twitch ---
TWITCH_CANAL = os.environ.get("TWITCH_CANAL", "")
TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
VODS_A_REVISAR = 10           # últimos VODs del canal que se miran en cada corrida
MAX_INTENTOS = 3              # reintentos por VOD antes de abandonarlo
TOLERANCIA_HORARIO_MIN = 45   # diferencia máxima entre inicio del VOD y hora del cronograma

# --- YouTube ---
YT_TOKEN_JSON = os.environ.get("YT_TOKEN_JSON", "")   # contenido de token.json (OAuth con refresh_token)
YT_TOKEN_ARCHIVO = BASE / "token.json"                 # alternativa local
# private | unlisted | public. Arrancar en private/unlisted hasta confiar en el proceso.
YT_PRIVACIDAD = os.environ.get("YT_PRIVACIDAD", "private")
YT_CATEGORIA = "25"  # News & Politics
# playlist a la que va cada compacto ("SORTEOS OFICIALES"); vacía = ninguna
YT_PLAYLIST = os.environ.get("YT_PLAYLIST", "PLQw-MqYiPuUc")
# playlists adicionales por sorteo, ej: {"MATUTINA": "PLxxxx", "VESPERTINA": "PLyyyy"} (JSON en variable YT_PLAYLISTS)
YT_PLAYLISTS = os.environ.get("YT_PLAYLISTS", "{}")

# --- Facebook (opcional, ver facebook.py) ---
FB_ACTIVO = os.environ.get("FB_ACTIVO", "false").lower() == "true"  # interruptor general: false = no sube nada
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "")
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_TOKEN", "")   # token de página permanente
# false: se sube sin publicar (queda en la biblioteca de videos de la página para revisarlo). Arrancar así.
FB_PUBLICAR = os.environ.get("FB_PUBLICAR", "false").lower() == "true"
# sorteos que se suben a Facebook, separados por coma (vacío = todos)
FB_SORTEOS = [s.strip().upper() for s in os.environ.get("FB_SORTEOS", "VESPERTINA,NOCTURNA").split(",") if s.strip()]
# "reels" (API de Reels, /video_reels) o "videos" (/videos)
FB_ENDPOINT = os.environ.get("FB_ENDPOINT", "reels").lower()
FB_GRAPH_VERSION = os.environ.get("FB_GRAPH_VERSION", "v24.0")
# pasados estos minutos desde la hora del sorteo ya no se sube a Facebook (ahí ya se publicó el extracto como imagen)
FB_LIMITE_MIN = int(os.environ.get("FB_LIMITE_MIN", "60"))

# avisos por Telegram (opcional, ver telegram.py)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# canal público de Telegram donde se comparte el enlace de YouTube de cada compacto (ver compartir.py).
# El bot tiene que ser administrador del canal; sin TELEGRAM_CANAL_TOKEN se usa el bot de los avisos.
TELEGRAM_CANAL_TOKEN = os.environ.get("TELEGRAM_CANAL_TOKEN", "") or TELEGRAM_TOKEN
TELEGRAM_CANAL_CHAT_ID = os.environ.get("TELEGRAM_CANAL_CHAT_ID", "")  # @usuario del canal o -100...

# --- Armado del video ---
FUENTE = ASSETS / "Poppins-Bold.ttf"
FONDO_INTRO = ASSETS / "fondo.png"
LOGO = ASSETS / "logo_tombola.png"
CORTINA = ASSETS / "cortina.m4a"

INTRO_DUR = 4.5
BIENVENIDA_DUR = 12.0
ANTES_CABEZA, DESPUES_CABEZA = 50.0, 15.0
CIERRE_DUR = 10.0  # mínimo 10 s: YouTube exige capítulos de al menos 10 s
FUNDIDO = 0.5
MUSICA_DB = -20
# por debajo de este nivel (RMS de todo el VOD) se toma como transmisión sin audio: sin voz o sin pista.
# Referencia: con locutor ~-34 dBFS; sin voz ~-69 dBFS.
# Si hay ruido por encima del umbral pero Whisper no encuentra ninguna voz, también se toma como sin audio.
NIVEL_SIN_AUDIO_DB = -55

# Si el locutor dice alguno de estos términos en el tramo del compacto, el video queda privado para revisar.
# Separados por coma, sin tildes y en minúscula (secreto TERMINOS_RIESGO). Sin definir, todos los compactos
# quedan privados con aviso (ver armar_compacto.revisar_locucion).
TERMINOS_RIESGO = [t.strip() for t in os.environ.get("TERMINOS_RIESGO", "").lower().split(",") if t.strip()]

# API del extracto oficial, con {fecha} en formato DD-MM-AAAA (secreto LOTERIA_API_EXTRACTO). Ver respaldo.py.
API_EXTRACTO = os.environ.get("LOTERIA_API_EXTRACTO", "")

WHISPER_MODELO = os.environ.get("WHISPER_MODELO", "small")


def _binario(nombre, *alternativas):
    if ruta := shutil.which(nombre):
        return ruta
    for patron in alternativas:
        if encontrados := sorted(Path.home().glob(patron)):
            return str(encontrados[-1])
    return nombre  # si no está instalado, falla recién al usarlo (la etapa "detectar" no lo necesita)


FFMPEG = _binario("ffmpeg", "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe")
FFPROBE = _binario("ffprobe", "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffprobe.exe")
TESSERACT = _binario("tesseract", "AppData/Local/Programs/Tesseract-OCR/tesseract.exe")
