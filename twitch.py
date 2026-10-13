"""Lista y descarga VODs del canal de Twitch con yt-dlp (no requiere credenciales de Twitch)."""
import datetime as dt
import subprocess
import sys
from pathlib import Path

import yt_dlp

import config


class _Silencio:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _ydl(**opciones):
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "logger": _Silencio(),
                             "ffmpeg_location": str(Path(config.FFMPEG).parent), **opciones})


def en_vivo(canal=None):
    """True si el canal está transmitiendo ahora (el último VOD todavía está creciendo)."""
    try:
        with _ydl() as y:
            return bool(y.extract_info(f"https://www.twitch.tv/{canal or config.TWITCH_CANAL}", download=False).get("is_live"))
    except yt_dlp.utils.DownloadError:
        return False  # yt-dlp falla con "not currently live"


def ultimos_vods(canal=None, cantidad=config.VODS_A_REVISAR):
    """Devuelve [{id, titulo, inicio (datetime ARG), duracion}] de los últimos VODs, del más nuevo al más viejo."""
    url = f"https://www.twitch.tv/{canal or config.TWITCH_CANAL}/videos?filter=archives&sort=time"
    with _ydl(extract_flat=True, playlistend=cantidad) as y:
        lista = y.extract_info(url, download=False).get("entries") or []
    return [info_vod(str(e["id"]).lstrip("v")) for e in lista]


def info_vod(vod_id):
    with _ydl() as y:
        info = y.extract_info(f"https://www.twitch.tv/videos/{vod_id}", download=False)
    return {"id": vod_id, "titulo": info.get("title", ""), "duracion": info.get("duration") or 0,
            "inicio": dt.datetime.fromtimestamp(info["timestamp"], config.ARG)}


def descargar(vod_id, carpeta):
    """Descarga el VOD en 1080p y devuelve la ruta del mp4."""
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{vod_id}.mp4"
    if not destino.exists():
        with _ydl(format="1080p/best[height<=1080]/best", outtmpl=str(destino), concurrent_fragment_downloads=8) as y:
            y.download([f"https://www.twitch.tv/videos/{vod_id}"])
    return destino


def extraer_audio(video, destino):
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                    str(destino)], check=True)
    return destino


if __name__ == "__main__":
    canal = sys.argv[1] if len(sys.argv) > 1 else config.TWITCH_CANAL
    print("En vivo:", en_vivo(canal))
    for v in ultimos_vods(canal, 5):
        print(v["id"], v["inicio"].strftime("%d/%m %H:%M"), f"{v['duracion'] // 60} min", v["titulo"])
