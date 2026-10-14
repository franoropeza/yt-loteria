# Compactos de sorteos

Arma automáticamente un compacto de ~90 segundos de cada sorteo de la Tómbola transmitido por Twitch
y lo sube a YouTube.

**Compacto (90 s):** intro (logo + nombre, número y fecha del sorteo) → bienvenida del locutor →
los 50 s previos a "a la cabeza" y 15 s después → captura del tablero final (10 s). La cortina musical
baja sola cuando habla el locutor.

## Cómo funciona

1. **Detectar** (GitHub Actions, después de cada sorteo): lista los últimos VODs del canal con `yt-dlp`
   y se fija cuáles faltan en `estado/procesados.json`. Si no hay nada nuevo termina en ~30 s.
2. **Procesar** (solo si hay VODs nuevos), por cada VOD:
   - Lo cruza con el cronograma oficial por horario de inicio (`cronograma.csv`).
   - Lo descarga en 1080p y transcribe el audio con Whisper hasta encontrar la primera "cabeza".
   - Busca el tablero de resultados y lee con OCR el número de sorteo, la fecha y la cabeza (control).
   - Arma el video con FFmpeg y lo sube a YouTube con título, descripción y miniatura (el tablero).
   - Guarda el resultado en `estado/procesados.json` (se commitea al repo).

### Controles

El video **se sube como privado** (aunque `YT_PRIVACIDAD` diga otra cosa) y la corrida queda en rojo
(GitHub manda un mail; el detalle queda en el resumen de la corrida) si:

- la cabeza no se pudo confirmar con "ubicación uno" en los 30 s previos;
- la cabeza leída en el tablero no coincide con la que cantó el locutor;
- el locutor dice términos de riesgo en el tramo del compacto (ver secreto TERMINOS_RIESGO);
- el sorteo no está en el cronograma o el tablero no coincide con el cronograma;
- YouTube no confirma la restricción de edad (ver abajo).

### Mayores de 18

Todos los videos tienen que quedar restringidos a mayores de 18. La API de YouTube no documenta cómo marcarlo,
así que se sube privado intentando marcarlo y después se lee el video: solo si YouTube confirma la restricción
se aplica `YT_PRIVACIDAD`. Si no, queda privado y hay que marcarlo a mano en YouTube Studio
(Detalles → Público → Restricción de edad → "Sí, restringir a mayores de 18") y recién ahí publicarlo.

### Título y descripción

Pensados para búsqueda: "Tómbola de Salta Matutina 12/10/2026 | Sorteo 16234 | A la cabeza 9706", descripción con
la cabeza, La Changuita, Tómbola Total, capítulos, horarios, extracto oficial, leyenda +18 y hashtags.
Vista previa: `python youtube.py salida/compacto_....json`.

Si falla (no se encontró "a la cabeza", error de red, etc.) se reintenta en las próximas corridas,
hasta 3 veces.

### Cronograma

`cronograma.csv` se genera a partir del PDF de <https://www.loteriadesalta.com/puntosdeventa/cronograma.pdf>.
No se descarga en cada corrida: solo si falta el mes en curso o, desde el día 25, una vez por día hasta que
aparezca el mes siguiente. Los nombres salen del horario: 12:00 Matutina, 15:00 Vespertina, 18:00 Extra,
21:00 Nocturna, 22:30 La Revancha.

## Puesta en marcha

### 1. Repositorio
Repositorio **privado** en una cuenta u organización de la Lotería (no personal).

### 2. Variables y secretos (Settings → Secrets and variables → Actions)

| Tipo | Nombre | Valor |
|---|---|---|
| Variable | `TWITCH_CANAL` | `loteriadesalta` |
| Variable | `YT_PRIVACIDAD` | `private` para arrancar; luego `unlisted` o `public` |
| Variable | `YT_PLAYLISTS` | opcional: `{"MATUTINA": "PL...", "VESPERTINA": "PL...", ...}` |
| Secreto | `YT_TOKEN_JSON` | contenido de `token.json` (ver abajo) |
| Secreto | `TELEGRAM_TOKEN` | opcional: token del bot de avisos (ver abajo) |
| Secreto | `TELEGRAM_CHAT_ID` | opcional: chat donde llegan los avisos |

**Avisos por Telegram (opcional):** con estos dos secretos llega un mensaje por cada compacto subido (miniatura,
cabeza, avisos y link para revisarlo y publicarlo en YouTube Studio) y por cada falla. Con Telegram configurado,
los videos con avisos ya no ponen la corrida en rojo (el mail de GitHub queda solo para fallas).
1. En Telegram, hablarle a **@BotFather** → `/newbot` → copiar el token.
2. Mandarle cualquier mensaje al bot nuevo (o agregarlo a un grupo), abrir
   `https://api.telegram.org/bot<TOKEN>/getUpdates` y copiar el número de `"chat":{"id": ...}`.

### 3. Token de YouTube
En una PC con el `client_secret.json` del proyecto de Google Cloud:

```
pip install -r requirements.txt
python youtube.py autorizar ruta/client_secret.json
```

Iniciar sesión con la cuenta que administra el canal y elegir el canal de la Lotería. Copiar el contenido
del `token.json` generado al secreto `YT_TOKEN_JSON` y **borrar el archivo local**.

En Google Cloud, la pantalla de consentimiento OAuth tiene que estar en **Producción**: en modo "Testing"
el token vence a los 7 días.

### 4. Verificación de YouTube (obligatoria para publicar)
Los videos subidos por API desde un proyecto sin verificar quedan **bloqueados como privados**.
Pedir la verificación en el formulario "YouTube API Services - Audit and Quota Extension".
Cuota: cada compacto consume ~1.750 unidades (subida + miniatura + lectura + publicación + playlist); 5 sorteos por día = ~8.750
de las 10.000 diarias. Si el mismo proyecto se usa para otros scripts, pedir más cuota en el mismo formulario.

### 5. Probar
Actions → "Compactos de sorteos" → Run workflow, con un ID de VOD y "Armar sin subir" tildado.
El compacto queda descargable 7 días en los artefactos de la corrida.

## Uso local

```
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # requiere ffmpeg y tesseract
set TWITCH_CANAL=loteriadesalta
python procesar.py --solo-detectar          # qué VODs están pendientes
python procesar.py --vod 2898838397 --sin-subir
python armar_compacto.py VOD.mp4 transcripcion.json   # rearmar con una transcripción ya hecha
python cronograma.py                        # actualizar cronograma.csv a mano
```

## Mantenimiento

- **Cambia la gráfica del stream:** ajustar en `armar_compacto.py` las coordenadas `TABLERO`, `CAMPOS_OCR`
  y `EN_VIVO` (están en píxeles sobre el cuadro de 1916×1080 del VOD).
- **Cambian duraciones, volumen de la cortina, etc.:** `config.py`.
- **Cambia el formato del PDF del cronograma:** `cronograma.py` (`FILA`, `FECHA_SOLA`).
- **yt-dlp deja de funcionar con Twitch:** se instala siempre la última versión; si falla, revisar novedades de yt-dlp.

## Recursos (`assets/`)

- `cortina.m4a`: cortina musical generada con IA (sin derechos de terceros).
- `fondo.png`, `logo_tombola.png`: fondo y logo tomados de la transmisión.
- `Poppins-Bold.ttf`: tipografía con licencia SIL Open Font License (`OFL.txt`).
