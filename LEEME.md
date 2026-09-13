# RISA Downloader

Descargador de vídeo y conversor de formatos. El nombre y el icono son de Risa, el perro de la casa.

Aplicación de escritorio para descargar vídeo/audio desde una URL y convertir
archivos multimedia que ya tengas en el disco. Construida sobre dos herramientas
libres muy conocidas: **yt-dlp** (descarga) y **FFmpeg** (conversión).

---

## Puesta en marcha en Windows (5 minutos)

### 1. Instalar Python

Descárgalo de <https://www.python.org/downloads/> y, durante la instalación,
**marca la casilla “Add python.exe to PATH”**. Sin esa casilla el programa no
arrancará.

> Si ya lo tienes, sáltate este paso. Puedes comprobarlo abriendo el símbolo del
> sistema y escribiendo `python --version`.

### 2. Descomprimir y arrancar

Descomprime la carpeta donde quieras (por ejemplo en `Documentos`) y haz doble
clic en **`Iniciar.bat`**.

### 3. Instalar los dos componentes

La primera vez la aplicación se abrirá en la pestaña **Opciones** avisándote de
lo que falta. Pulsa:

- **Instalar / Actualizar** en el recuadro de yt-dlp (tarda unos segundos).
- **Descargar automáticamente** en el recuadro de FFmpeg (unos 80 MB; se guarda
  en la subcarpeta `bin` de la propia aplicación, no toca nada del sistema).

Cuando ambos aparezcan en verde con su número de versión, ya está lista.

---

## Aspecto de la ventana

La interfaz imita el estilo clásico de Windows: pestañas, marcos de grupo,
botones con bisel y barra de progreso a bloques, todo dibujado por código (no
hacen falta imágenes). En la pestaña **Opciones**, apartado *Apariencia*,
puedes cambiar entre **Windows 95** (gris con barra azul marino) y
**Windows XP** (Luna). La elección se recuerda en `config.json`.

La carpeta `assets` guarda el icono (`risa.ico` y los PNG que usa la barra de título) y la tipografía MS Sans Serif del kit Windows 95 de
Themesberg (licencia MIT, incluida). Los textos de la ventana usan Tahoma
porque esa tipografía de píxeles no trae vocales acentuadas.

## Instalación

La forma cómoda: ejecutar **`publicar\Instalar RISA Downloader.exe`**. Es un
único archivo que lleva el programa dentro, no necesita Python ni permisos de
administrador y deja:

- el programa en `%LOCALAPPDATA%\Programs\RISA Downloader`
- acceso directo en el escritorio y en el menú Inicio, con el icono de Risa
- la entrada correspondiente en «Aplicaciones instaladas» de Windows, para
  poder desinstalarlo desde ahí como cualquier otro programa

Los ajustes y los componentes descargados (yt-dlp, FFmpeg) se guardan aparte,
en `%LOCALAPPDATA%\RISA Downloader`, así que **se conservan al desinstalar**
salvo que marques la casilla correspondiente.

Para volver a generar el instalador tras tocar el código:

```
pip install -U pyinstaller
python compilar.py
```

Deja el resultado en la carpeta `publicar`.

## Se mantiene solo

No tienes que hacer nada de mantenimiento:

- **yt-dlp se actualiza solo** en segundo plano cuando se queda viejo (más de
  dos semanas), como mucho una vez al día. Casi todos los fallos «raros» de
  descarga vienen de un yt-dlp caducado, y así se evitan sin que te enteres.
- **Cookies en «Automático»** por defecto: descarga sin cookies (rápido) y
  solo si un vídeo pide iniciar sesión prueba con tus navegadores.

Para la inmensa mayoría de vídeos (YouTube, TikToks públicos, etc.) no hace
falta tocar absolutamente nada: pega el enlace y dale a Descargar.

## Cookies (sesión del navegador)

En *Cookies de:* tienes tres formas de trabajar:

- **Ninguno** — lo normal. Ni se leen cookies ni se pierde tiempo.
- **Automático** — descarga sin cookies (rápido) y, **solo si el sitio contesta
  que hace falta iniciar sesión**, reintenta con los navegadores que tengas
  instalados, de uno en uno, hasta que uno funcione.
- **Un navegador concreto** o **Archivo cookies.txt…** — control manual.

Para los vídeos que piden sesión, en **Opciones** hay un botón **«Instalar Firefox»**: lo instala por ti (con el instalador de Windows). Luego inicias sesión en la web una vez, cierras Firefox y el modo «Automático» usa esa sesión solo.

Aviso práctico: Chrome y los navegadores basados en Chromium cifran sus
cookies desde la versión 127, así que yt-dlp no puede leerlas aunque cierres el
navegador. Con **Firefox** sí funciona; si usas Chrome, exporta las cookies a
un archivo con una extensión tipo «Get cookies.txt» y elige esa opción.

## Spotify

Pega un enlace de **canción, álbum o lista** de Spotify y se descarga la música.

Conviene saber cómo funciona: **de Spotify no sale ningún audio**. Su audio va
cifrado y el programa no lo toca. Lo que hace es leer los datos públicos del
enlace (título y artista, los mismos que ve cualquiera en el reproductor web),
buscar cada canción en YouTube y descargar el audio de allí, con el nombre
«Artista - Canción». Es la misma técnica que usan herramientas como spotDL.

Como consecuencia, la calidad y la versión concreta dependen de lo que haya en
YouTube: casi siempre es la buena, pero a veces cae una versión en directo o un
remix.

## Colas

Tanto las descargas como las conversiones van en cola y se procesan una tras
otra; cada elemento muestra su estado (En espera, Descargando, Hecho, Error).

- **Descargar:** escribe un enlace y pulsa «Añadir a la cola». Cada enlace
  guarda el formato y la calidad que estuvieran elegidos al añadirlo, así que
  puedes mezclar vídeos en MP4 con audios en MP3 en la misma tanda. El botón
  **Descargar** procesa lo que haya pendiente (si escribes un enlace y le das
  directamente, ese enlace se añade y empieza).
- **Convertir:** puedes **arrastrar archivos o carpetas sobre la ventana** y
  se añaden solos a la cola (se ignora lo que no sea vídeo ni audio).
  **Cancelar** detiene toda la cola, no solo el elemento en curso.

## Pestaña «Descargar»

1. Pega el enlace (botón **Pegar**).
2. Elige **Vídeo + audio** o **Solo audio**.
3. Elige formato y calidad:
   - Vídeo: MP4, MKV, WEBM, MOV, AVI · resoluciones desde 360p hasta 4K.
   - Audio: MP3, M4A, WAV, FLAC, OGG, OPUS · bitrate de 96 a 320 kbps, o
     profundidad/frecuencia (16 bit 44,1 kHz … 24 bit 96 kHz) para WAV y FLAC.
4. Opciones sueltas: lista de reproducción completa, subtítulos en español e
   inglés (se incrustan en MP4/MKV/WEBM), carátula y metadatos.
5. Elige la carpeta de destino y pulsa **Descargar**.

La barra inferior muestra el porcentaje y el registro detallado. **Cancelar**
detiene la tarea en cualquier momento.

### Otras redes, no solo YouTube

El motor reconoce más de mil sitios. Según escribes el enlace, la aplicación te
dice bajo el campo qué red ha detectado y qué necesita: TikTok, Reddit, X,
Instagram, Facebook, Twitch, Vimeo, Dailymotion, SoundCloud, Bandcamp,
Archive.org, RTVE, Rumble, Odysee, VK, Telegram y demás. Si el sitio es de solo
audio, cambia solo al modo audio. Y si no reconoce el dominio lo intenta
igualmente con el extractor genérico, que funciona en bastantes webs pequeñas.

Instagram y Facebook casi siempre necesitan que elijas tu navegador en
**«Cookies de:»**; la app te avisa cuando toca.

### Opciones avanzadas

Marca **Mostrar opciones avanzadas** para desplegarlas:

- **Solo un tramo** — descarga del minuto X al minuto Y en vez del vídeo entero
  (`00:10:00` → `00:20:00`). Útil en clases o conferencias largas. Necesita
  FFmpeg y no sirve para emisiones en directo.
- **Directos: parar a los N minutos** — corta la grabación de una emisión en
  vivo pasado ese tiempo. Con **Grabar desde el principio** intenta empezar por
  el inicio de la emisión, si el sitio lo permite.
- **Referer** — la página desde la que se incrusta el vídeo. Muchos
  reproductores externos solo sirven el flujo si se lo indicas; es lo primero
  que hay que probar cuando un enlace incrustado falla.
- **Extra** — argumentos sueltos de yt-dlp tal cual los escribirías en consola.
  Van los últimos, así que sobrescriben lo que haya elegido la interfaz: por
  ejemplo `-f 234` fuerza una pista concreta.
- **Analizar enlace y ver formatos** — lista las pistas que ofrece una
  dirección, con su ID, sin descargar nada. La forma rápida de saber si un
  enlace desconocido va a funcionar.

También acepta enlaces de flujo directos (`.m3u8`, `.mpd`) y archivos servidos
tal cual (`.mp4`, `.mp3`…). En los flujos activa un modo de grabación que deja
el archivo reproducible aunque cortes a mitad.

## Pestaña «Convertir archivos»

Para material que ya tienes en el disco. Añade uno o varios archivos, elige el
formato de salida y pulsa **Convertir**; se procesan en cola, uno tras otro.

- **A vídeo** (MP4, MKV, WEBM, MOV, AVI): calidad Máxima / Alta / Media / Baja,
  reescalado opcional de resolución, y una opción **Copiar sin recodificar** que
  cambia solo el contenedor en un par de segundos y sin pérdida de calidad
  (útil, por ejemplo, para pasar de MKV a MP4).
- **A audio** (MP3, M4A, WAV, FLAC, OGG, OPUS): con el mismo control de calidad
  que en la descarga.
- **A GIF**: con paleta optimizada, a 10, 12 o 15 fps.

Por defecto guarda el resultado junto al original; desmarca la casilla para
elegir otra carpeta. Nunca sobrescribe: si el nombre existe, añade ` (1)`.

---

## Preguntas frecuentes

**¿Los vídeos de TikTok salen con marca de agua?**
No: TikTok publica la misma toma varias veces y solo la pista
«download_addr» lleva la marca. El programa la descarta y pide cualquiera
de las otras. Si un vídeo concreto solo existiera en la versión marcada,
se descarga esa antes que fallar.

**¿Por qué hacen falta yt-dlp y FFmpeg?**
Son el motor real. Esta aplicación es la interfaz gráfica que los maneja por ti
y traduce tus elecciones a los parámetros correctos, que son bastante
enrevesados si se escriben a mano.

**El vídeo se descarga sin sonido o se queda a medias.**
Casi siempre es FFmpeg: las resoluciones altas vienen en dos pistas separadas
que hay que unir. Comprueba en **Opciones** que aparece instalado.

**Sale «HTTP Error 403: Forbidden» y la descarga se detiene.**
Es el bloqueo más habitual: el servidor rechaza la pista de vídeo aunque el
enlace sea correcto. La aplicación ya lo reintenta sola hasta cuatro veces,
pidiendo el vídeo como si fuera otro tipo de reproductor (tv, android, móvil).
Si aun así falla, en el registro aparece una lista de qué probar; el orden que
funciona casi siempre es:

1. **Opciones → Instalar / Actualizar** en yt-dlp. Estos servicios cambian sus
   defensas cada pocas semanas y la versión nueva suele traer el arreglo. Es la
   primera medida ante cualquier fallo de descarga.
2. Elegir tu navegador en **«Cookies de:»** (ciérralo antes de descargar). Esto
   usa tu sesión iniciada y resuelve los vídeos con restricción de edad,
   privados, de pago o el aviso de «confirma que no eres un robot».
3. Bajar la calidad a 720p: las pistas de 1080p y 4K son las que más se bloquean.
4. Esperar unos minutos. Muchas descargas seguidas desde la misma conexión
   provocan bloqueos temporales que se levantan solos.

**Una descarga falla y el registro habla de otro error del sitio.**
Mismo remedio del punto 1: pulsa **Instalar / Actualizar** en yt-dlp.

**¿Sale algo de mi equipo?**
No. Todo el proceso es local; lo único que viaja por la red es la propia
descarga del vídeo y, si los pides, los dos componentes del paso 3.

**macOS o Linux.**
El programa funciona igual (`python3 descargador.py`), pero FFmpeg hay que
instalarlo con el gestor de paquetes: `brew install ffmpeg`,
`sudo apt install ffmpeg` o `sudo dnf install ffmpeg`.

---

## Aviso legal

Descarga únicamente contenido propio, con licencia libre (Creative Commons,
dominio público) o para el que tengas permiso del titular de los derechos.
Descargar material protegido sin autorización infringe tanto las condiciones de
uso de las plataformas como la legislación de derechos de autor. El uso que
hagas de esta herramienta es responsabilidad tuya.
