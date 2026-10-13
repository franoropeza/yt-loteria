"""Transcribe el audio del sorteo con faster-whisper hasta encontrar "a la cabeza".

Es una sola pasada continua sobre todo el audio (como si se transcribiera completo), pero se corta
apenas aparece la primera "cabeza": faster-whisper entrega los segmentos de a uno y en orden.
No se transcribe por tramos separados: sin el contexto previo Whisper confunde la frase
("a la camisa") o inventa una "cabeza" donde no la hay.

Uso:
  python transcribir.py audio.wav [salida.json]
"""
import json
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

import config

# La pista "A la cabeza" hace falta: sin ella Whisper entiende "a la camisa". Como contrapartida, en tramos de
# música o silencio a veces repite la pista ("A la cabeza. A la cabeza. A la cabeza."): por eso una cabeza
# solo vale si antes se escuchó "ubicación uno" (ver buscar_cabeza).
PROMPT = "Sorteo de quiniela de la Lotería de Salta. A la cabeza."

CABEZA = re.compile(r"cabeza|camisa", re.IGNORECASE)  # "a la camisa": error típico de Whisper con este locutor
# El locutor siempre canta "Ubicación uno, número ..." unos segundos antes de "a la cabeza".
# Whisper escribe "Unicación", "Unificación", etc.; a veces "1" en vez de "uno".
UBICACION_1 = re.compile(r"icaci[oó]n\W*(?:n[uú]mero\W*)?(?:1|uno)\b", re.IGNORECASE)
VENTANA_UBICACION = 30  # segundos


def buscar_cabeza(segmentos):
    """Momento (s) de la primera "cabeza" con "ubicación uno" en los 30 s anteriores, o None.

    Se busca en el texto unido de la ventana porque Whisper a veces parte la frase en dos segmentos
    ("Ubicación 6, ubicación" / "1, número 9.706")."""
    for s in segmentos:
        for w in s["words"]:
            if CABEZA.search(w["w"]):
                previo = " ".join(x["text"] for x in segmentos if w["s"] - VENTANA_UBICACION <= x["start"] <= w["s"])
                if UBICACION_1.search(previo):
                    return w["s"]
    return None


_modelo = None


def modelo():
    global _modelo
    if _modelo is None:
        from faster_whisper import WhisperModel
        _modelo = WhisperModel(config.WHISPER_MODELO, device="cpu", compute_type="int8")
    return _modelo


def leer_wav(ruta):
    with wave.open(str(ruta)) as w:  # wav 16 kHz mono, generado con ffmpeg
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def transcribir(wav, hasta_cabeza=True):
    """Devuelve la lista de segmentos [{start, end, text, words:[{w, s, e}]}]."""
    segs, _ = modelo().transcribe(leer_wav(wav), language="es", word_timestamps=True, vad_filter=True,
                                  initial_prompt=PROMPT)
    segmentos, cabeza = [], None
    for s in segs:  # generador: cada segmento se transcribe recién al pedirlo
        segmentos.append({"start": s.start, "end": s.end, "text": s.text,
                          "words": [{"w": w.word, "s": w.start, "e": w.end} for w in s.words]})
        if hasta_cabeza and cabeza is None and CABEZA.search(s.text):
            cabeza = buscar_cabeza(segmentos)
        # se sigue un poco después de la cabeza para cubrir todo el tramo que entra en el compacto
        if cabeza is not None and s.start > cabeza + config.DESPUES_CABEZA + 5:
            break
    return segmentos


if __name__ == "__main__":
    wav = Path(sys.argv[1])
    destino = Path(sys.argv[2]) if len(sys.argv) > 2 else wav.with_suffix(".json")
    t0 = time.time()
    segs = transcribir(wav)
    destino.write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    print(f"{wav.name}: {len(segs)} segmentos en {time.time() - t0:.0f}s")
