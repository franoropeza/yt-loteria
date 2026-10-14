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
# playlists por sorteo, ej: {"MATUTINA": "PLxxxx", "VESPERTINA": "PLyyyy"} (JSON en variable YT_PLAYLISTS)
YT_PLAYLISTS = os.environ.get("YT_PLAYLISTS", "{}")

# avisos por Telegram (opcional, ver telegram.py)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

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

# Si el locutor dice alguno de estos términos en el tramo del compacto, el video queda privado para revisar.
# Misma idea que TERMINOS_RIESGO de otro script (se comparan sin tildes y en minúscula).
TERMINOS_RIESGO = []  # ver secreto TERMINOS_RIESGO

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
