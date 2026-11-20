# Compactos de sorteos

Arma automáticamente un compacto de ~90 segundos de cada sorteo de la Tómbola transmitido por Twitch
y lo sube a YouTube (y, opcionalmente, a la página de Facebook).

**Compacto (90 s):** intro (logo + nombre, número y fecha del sorteo) → bienvenida del locutor →
los 50 s previos a "a la cabeza" y 15 s después → captura del tablero final (10 s). La cortina musical
baja sola cuando habla el locutor.

## Cómo funciona

1. **Detectar** (GitHub Actions, disparado por cron-job.org a los 22, 27, 32, 40, 55 y 80 min de cada sorteo; ver el workflow): lista los últimos VODs del canal con `yt-dlp`
   y se fija cuáles faltan en `estado/procesados.json`. Si no hay nada nuevo termina en ~30 s.
2. **Procesar** (solo si hay VODs nuevos), por cada VOD:
   - Lo cruza con el cronograma oficial por horario de inicio (`cronograma.csv`).
   - Lo descarga en 1080p y transcribe el audio con Whisper hasta encontrar la primera "cabeza".
   - Busca el tablero de resultados y lee con OCR el número de sorteo, la fecha y la cabeza. La cabeza se lee
     varias veces (primer tablero y tablero final, tres preprocesados) y se vota; si el locutor cantó una de las
     lecturas, manda esa.
   - Arma el video con FFmpeg y lo sube a YouTube con título, descripción y miniatura (el tablero).
   - Guarda el resultado en `estado/procesados.json`, que vive en el repo **privado** `<repo>-estado` (ver Puesta en marcha).

### Controles

El video **se sube como privado** (aunque `YT_PRIVACIDAD` diga otra cosa) y la corrida queda en rojo
(GitHub manda un mail; el detalle queda en el resumen de la corrida) si:

- la cabeza no se pudo confirmar con "ubicación uno" en los 30 s previos;
- la cabeza leída en el tablero no coincide con la que cantó el locutor;
- el locutor dice términos de riesgo en el tramo del compacto (secreto `TERMINOS_RIESGO`; sin definir, todos quedan privados);
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

### Compacto híbrido (sin tablero)

Si el locutor canta "a la cabeza" pero el tablero de resultados nunca aparece en la transmisión, se arma el compacto con el video del locutor como siempre y el cierre (y la miniatura) es la placa
con el extracto oficial de la API, la misma del respaldo. Los datos salen del cronograma y la cabeza del extracto,
controlada con la que cantó el locutor. Se sube **privado** con aviso y es solo para YouTube. Si el extracto
todavía no está publicado, el VOD espera a la próxima corrida sin gastar un intento.

### Transmisión sin audio

Si el VOD no tiene audio (nivel por debajo de `NIVEL_SIN_AUDIO_DB`, sin pista de audio, o Whisper no encuentra
ninguna voz), el compacto se arma igual pero **solo con la cortina musical**, y la cabeza se lee en el tablero
(vale si coinciden todas las lecturas del OCR, porque no hay locutor que la confirme). El Telegram lo indica con 🔇.

Sin "a la cabeza" que escuchar, el momento del corte se deduce del tablero: si la primera vez que aparece con la
casilla 1 llena es el único número nuevo respecto de la aparición anterior, la cabeza salió unos segundos antes.
Si no se puede deducir, no se adivina: el VOD se abandona sin reintentos
(el resultado no cambiaría) y el sorteo pasa al compacto de respaldo. Para armarlo con el video, correr
Actions → "Compactos de sorteos" → Run workflow con el VOD y el minuto en que sale la cabeza en el VOD
(`cabeza`, ej. `2:55`); local: `python procesar.py --vod 2925069480 --cabeza 2:55`. Si el respaldo ya se
había subido, borrarlo de YouTube a mano.

### Compacto de respaldo

Si un sorteo del cronograma se queda sin compacto (no hubo VOD en Twitch o el VOD se abandonó después de
3 intentos), desde los 70 min del sorteo se arma un video de ~24 s con el extracto oficial de la API de la
Lotería (secreto `LOTERIA_API_EXTRACTO`): la misma intro y una placa con la cabeza y
los 20 números, con la cortina de fondo. Si el extracto todavía no está publicado, se vuelve a mirar en la
próxima corrida (hasta 24 h después del sorteo). Siempre se sube **privado** para revisarlo; la descripción
incluye los 20 números. Es solo para YouTube: no se sube a Facebook (ahí el extracto se publica aparte, como
imagen). Para forzarlo: Run workflow con "respaldo" = `14/10/2026 15:00`, o localmente
`python respaldo.py 14/10/2026 15:00` (arma sin subir).

### Cronograma

`cronograma.csv` se genera a partir del PDF de <https://www.loteriadesalta.com/puntosdeventa/cronograma.pdf>.
No se descarga en cada corrida: solo si falta el mes en curso o, desde el día 25, una vez por día hasta que
aparezca el mes siguiente. Si una fecha no figura en el PDF es porque ese día no hay sorteo (por ejemplo 25/12 y 1/1):
no se espera VOD, no se avisa "sin VOD" y no se arma respaldo. Los nombres salen del horario: 12:00 Matutina, 15:00 Vespertina, 18:00 Extra,
21:00 Nocturna, 22:30 La Revancha.

## Puesta en marcha

### 1. Repositorio
Repositorio en una cuenta u organización de la Lotería (no personal). El código puede ser público: lo sensible va en
secretos y el estado (`estado/`) en un repo **privado** aparte con el mismo nombre + `-estado`:

```
gh repo create <org>/<repo>-estado --private
ssh-keygen -t ed25519 -N "" -f estado_key
gh repo deploy-key add estado_key.pub -R <org>/<repo>-estado --allow-write
gh secret set ESTADO_DEPLOY_KEY -R <org>/<repo> < estado_key     # después borrar estado_key y estado_key.pub
```

Para trabajar en local, clonarlo dentro de la carpeta del proyecto: `git clone <url del repo -estado> estado`.

### 2. Variables y secretos (Settings → Secrets and variables → Actions)

| Tipo | Nombre | Valor |
|---|---|---|
| Variable | `TWITCH_CANAL` | `loteriadesalta` |
| Variable | `YT_PRIVACIDAD` | `private` para arrancar; luego `unlisted` o `public` |
| Variable | `YT_PLAYLIST` | opcional: playlist de todos los compactos (por defecto `PLQw-MqYiPuUc`, "SORTEOS OFICIALES"); vacía = ninguna |
| Variable | `YT_PLAYLISTS` | opcional: `{"MATUTINA": "PL...", "VESPERTINA": "PL...", ...}` |
| Secreto | `YT_TOKEN_JSON` | contenido de `token.json` (ver abajo) |
| Variable | `FB_ACTIVO` | `true` para subir a Facebook; `false` (o sin definir) no sube nada aunque haya credenciales |
| Variable | `FB_PAGE_ID` | opcional: ID de la página de Facebook (ver abajo) |
| Variable | `FB_PUBLICAR` | `false` para arrancar (sube sin publicar); `true` para publicar directo |
| Variable | `FB_SORTEOS` | opcional: sorteos que van a Facebook (por defecto `VESPERTINA,NOCTURNA`; vacía = todos) |
| Variable | `FB_ENDPOINT` | opcional: `reels` (por defecto, API de Reels, máx. 90 s) o `videos` (endpoint `/videos`) |
| Secreto | `FB_PAGE_TOKEN` | opcional: token de página permanente de Facebook |
| Secreto | `TELEGRAM_TOKEN` | opcional: token del bot de avisos (ver abajo) |
| Secreto | `TELEGRAM_CHAT_ID` | opcional: chat donde llegan los avisos |
| Variable o secreto | `TELEGRAM_CANAL_CHAT_ID` | opcional: canal público donde se comparte el enlace de YouTube (`@usuario` o `-100...`) |
| Secreto | `TELEGRAM_CANAL_TOKEN` | opcional: token del bot que publica en el canal (vacío = el bot de los avisos) |
| Secreto | `ESTADO_DEPLOY_KEY` | clave privada SSH con escritura en el repo `<repo>-estado` (ver arriba) |
| Secreto | `TERMINOS_RIESGO` | términos que dejan el compacto privado, separados por coma, sin tildes y en minúscula |
| Secreto | `LOTERIA_API_EXTRACTO` | URL de la API del extracto oficial, con `{fecha}` (DD-MM-AAAA) |

**Avisos por Telegram (opcional):** con estos dos secretos llega un mensaje por cada compacto subido (miniatura,
cabeza, avisos y link para revisarlo y publicarlo en YouTube Studio) y por cada falla. Con Telegram configurado,
los videos con avisos ya no ponen la corrida en rojo (el mail de GitHub queda solo para fallas).
1. En Telegram, hablarle a **@BotFather** → `/newbot` → copiar el token.
2. Mandarle cualquier mensaje al bot nuevo (o agregarlo a un grupo), abrir
   `https://api.telegram.org/bot<TOKEN>/getUpdates` y copiar el número de `"chat":{"id": ...}`.

**Facebook (opcional):** con `FB_ACTIVO=true`, `FB_PAGE_ID` y `FB_PAGE_TOKEN` cada compacto subido a YouTube de los sorteos
de `FB_SORTEOS` se sube también a la página, con el mismo título; la descripción es más corta (sin La Changuita, Tómbola Total, horarios ni teléfonos) y lleva
los enlaces a loteriadesalta.com, al canal de Twitch y al canal de YouTube (YouTube no permite enlaces). Con `FB_PUBLICAR=false`, o si el compacto tiene avisos (salvo el de +18
de YouTube: en Facebook la restricción de edad es de toda la página), queda sin publicar
en la biblioteca de videos de la página (Meta Business Suite → Contenido) para revisarlo. Si falla Facebook, el
compacto no se reintenta (ya está en YouTube): llega como aviso.
- Solo va a Facebook el compacto normal (no el híbrido ni el respaldo), y solo si queda listo dentro de
  `FB_LIMITE_MIN` (por defecto 60) desde la hora del sorteo: después ya se publicó el extracto como imagen.
- Si la cabeza no se pudo identificar bien (el tablero no coincide con lo que cantó el locutor, no se leyó, o no se
  confirmó con "ubicación uno"), no se sube el video: se corrige el título a mano en YouTube y, cuando queda
  **público**, se comparte el enlace de YouTube como con los demás sorteos (ver abajo), con la cabeza que figure en
  ese momento en el título.
- El token tiene que ser **de página** y permanente, con `pages_manage_posts` y `pages_read_engagement`.
  Probarlo: `set FB_PAGE_ID=...` / `set FB_PAGE_TOKEN=...` y `python facebook.py verificar`.
- **+18:** la API no restringe por edad cada video. Configurarlo una vez para toda la página
  (Configuración → Restricción de edad → 18+).

**Enlaces de YouTube (Facebook y canal de Telegram):** de los sorteos que no están en `FB_SORTEOS` (Matutina, Extra,
La Revancha) no se sube el video: se comparte en el muro de la página el **enlace de YouTube**, con el mismo texto
que llevaría el video (`FB_PUBLICAR=false` lo deja sin publicar). Misma regla que el video: solo el compacto normal
(no el híbrido ni el respaldo) y solo si el video queda **público** en YouTube dentro de `FB_LIMITE_MIN` (60 min)
desde la hora del sorteo; si no, no se publica nada en Facebook. Con `TELEGRAM_CANAL_CHAT_ID` se publica además en el
canal de Telegram el título y el enlace de **todos** los compactos (normal, híbrido y respaldo), solo si quedan
públicos dentro de `TELEGRAM_LIMITE_MIN` (variable, por defecto 180 min) desde la hora del sorteo.
El enlace se comparte recién cuando el video está **público** en YouTube: al subirlo queda pendiente en
`estado/procesados.json` y en cada corrida el job `compartir` del workflow (`python compartir.py`) revisa la
privacidad en YouTube y comparte los que ya se publicaron (por ejemplo, después de marcar el +18 a mano). Si un
destino falla se reintenta en la corrida siguiente; si el video se borra de YouTube, se descarta.
- El bot del canal tiene que ser **administrador** del canal con permiso para publicar. Puede ser el bot del
  autopublicador de extractos (su token en `TELEGRAM_CANAL_TOKEN`) o el de los avisos agregado como administrador.

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
set TERMINOS_RIESGO=...                     # y LOTERIA_API_EXTRACTO para el respaldo
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
