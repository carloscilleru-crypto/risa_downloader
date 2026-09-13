# -*- coding: utf-8 -*-
"""
RISA Downloader — descargador de vídeo y conversor de formatos
==============================================================
Aplicación de escritorio (Windows / macOS / Linux) construida sobre
yt-dlp + FFmpeg. Lleva el nombre (y la cara) de un perro.

Uso responsable: descarga únicamente contenido propio, con licencia libre
o cuando cuentes con permiso del titular de los derechos.
"""

import datetime
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

APP_NAME = "RISA Downloader"
APP_VERSION = "1.0"

IS_WINDOWS = os.name == "nt"
FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # Compilado: el .exe puede estar en una carpeta de solo lectura y sus
    # recursos viven en la carpeta temporal que crea PyInstaller.
    APP_DIR = os.path.dirname(sys.executable)
    RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_NAME)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception:
        DATA_DIR = APP_DIR
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    RES_DIR = APP_DIR
    DATA_DIR = APP_DIR

BIN_DIR = os.path.join(DATA_DIR, "bin")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

YTDLP_EXE_URL = ("https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
                 "yt-dlp.exe")

FFMPEG_ZIP_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-win64-gpl.zip"
)

# --------------------------------------------------------------------------
# Catálogos de formatos y calidades
# --------------------------------------------------------------------------

VIDEO_FORMATS = ["MP4", "MKV", "WEBM", "MOV", "AVI"]
AUDIO_FORMATS = ["MP3", "M4A", "WAV", "FLAC", "OGG", "OPUS"]

VIDEO_RESOLUTIONS = [
    ("Mejor disponible", None),
    ("2160p (4K)", 2160),
    ("1440p (2K)", 1440),
    ("1080p (Full HD)", 1080),
    ("720p (HD)", 720),
    ("480p", 480),
    ("360p", 360),
]

LOSSY_BITRATES = ["320 kbps", "256 kbps", "192 kbps", "160 kbps", "128 kbps", "96 kbps"]
PCM_QUALITIES = [
    "16 bit / 44.1 kHz (CD)",
    "16 bit / 48 kHz",
    "24 bit / 48 kHz",
    "24 bit / 96 kHz",
    "Original",
]

PCM_MAP = {
    "16 bit / 44.1 kHz (CD)": ("pcm_s16le", "44100"),
    "16 bit / 48 kHz": ("pcm_s16le", "48000"),
    "24 bit / 48 kHz": ("pcm_s24le", "48000"),
    "24 bit / 96 kHz": ("pcm_s24le", "96000"),
    "Original": (None, None),
}

VIDEO_QUALITY_CRF = {
    "Máxima (archivo grande)": 18,
    "Alta": 20,
    "Media (recomendada)": 23,
    "Baja (archivo pequeño)": 28,
}
VIDEO_QUALITY_LIST = list(VIDEO_QUALITY_CRF.keys()) + ["Copiar sin recodificar (rápido)"]

CONVERT_TARGETS = VIDEO_FORMATS + ["GIF"] + AUDIO_FORMATS

# Navegadores de los que se pueden reutilizar las cookies de sesión
COOKIES_AUTO_OPTION = "Automático"
COOKIES_FILE_OPTION = "Archivo cookies.txt…"

BROWSERS = ["Ninguno", COOKIES_AUTO_OPTION, "Chrome", "Edge", "Firefox",
            "Brave", "Opera", "Vivaldi", "Chromium", "Safari",
            COOKIES_FILE_OPTION]

# Orden de preferencia al detectar navegadores. Firefox va primero porque es
# el único del que se pueden leer las cookies con fiabilidad: Chrome y los
# demás basados en Chromium las cifran desde la versión 127.
BROWSER_PROFILES = [
    ("Firefox", [os.path.join("{APPDATA}", "Mozilla", "Firefox", "profiles.ini")]),
    ("Edge", [os.path.join("{LOCALAPPDATA}", "Microsoft", "Edge", "User Data")]),
    ("Chrome", [os.path.join("{LOCALAPPDATA}", "Google", "Chrome", "User Data")]),
    ("Brave", [os.path.join("{LOCALAPPDATA}", "BraveSoftware",
                            "Brave-Browser", "User Data")]),
    ("Vivaldi", [os.path.join("{LOCALAPPDATA}", "Vivaldi", "User Data")]),
    ("Opera", [os.path.join("{APPDATA}", "Opera Software", "Opera Stable")]),
    ("Chromium", [os.path.join("{LOCALAPPDATA}", "Chromium", "User Data")]),
]


FIREFOX_URL = "https://www.mozilla.org/firefox/new/"


def firefox_instalado():
    """Ruta del ejecutable de Firefox, o None si no está."""
    candidatos = []
    if IS_WINDOWS:
        for base in (os.environ.get("PROGRAMFILES", r"C:\\Program Files"),
                     os.environ.get("PROGRAMFILES(X86)",
                                    r"C:\\Program Files (x86)")):
            candidatos.append(os.path.join(base, "Mozilla Firefox", "firefox.exe"))
    else:
        candidatos += ["/usr/bin/firefox", "/usr/local/bin/firefox",
                       "/Applications/Firefox.app/Contents/MacOS/firefox"]
    for ruta in candidatos:
        if ruta and os.path.isfile(ruta):
            return ruta
    return shutil.which("firefox")


def winget_disponible():
    return shutil.which("winget") is not None


def navegadores_disponibles():
    """Navegadores instalados de los que se podrían leer cookies."""
    if not IS_WINDOWS:
        return ["Firefox", "Chrome"]
    encontrados = []
    for nombre, rutas in BROWSER_PROFILES:
        for plantilla in rutas:
            ruta = plantilla.format(
                APPDATA=os.environ.get("APPDATA", ""),
                LOCALAPPDATA=os.environ.get("LOCALAPPDATA", ""))
            if os.path.exists(ruta):
                encontrados.append(nombre)
                break
    return encontrados

# Escalera de reintentos ante bloqueos (HTTP 403 y similares). YouTube sirve las
# pistas con firmas distintas según el cliente que las pida; si una falla, se
# vuelve a intentar pidiéndolas como otro tipo de cliente.
YOUTUBE_ATTEMPTS = [
    {"client": None, "label": "predeterminado"},
    {"client": "tv,web_safari", "label": "tv + web_safari"},
    {"client": "android,ios", "label": "android + ios"},
    {"client": "web_embedded,mweb", "label": "web_embedded + mweb",
     "force_ipv4": True},
]

# Para el resto de sitios no hay clientes alternativos que probar; lo único que
# a veces desatasca es forzar IPv4.
GENERIC_ATTEMPTS = [
    {"client": None, "label": "predeterminado"},
    {"client": None, "label": "IPv4 forzado", "force_ipv4": True},
]

# --------------------------------------------------------------------------
# Sitios reconocidos
#   cookies: "no" | "a veces" | "casi siempre"
#   audio:   el sitio solo sirve audio
# --------------------------------------------------------------------------

SITES = [
    {"name": "YouTube", "key": "youtube",
     "re": r"(?:^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)",
     "hint": "Vídeo y audio en pistas separadas: FFmpeg las une.",
     "cookies": "a veces"},
    {"name": "TikTok", "key": "tiktok",
     "re": r"(?:^|\.)(tiktok\.com)",
     "hint": "Se pide la pista sin marca de agua (se descarta «download_addr»).",
     "cookies": "a veces"},
    {"name": "Reddit", "key": "reddit",
     "re": r"(?:^|\.)(reddit\.com|redd\.it)",
     "hint": "El audio va aparte: sin FFmpeg el vídeo saldría mudo.",
     "cookies": "no"},
    {"name": "X / Twitter", "key": "twitter",
     "re": r"(?:^|\.)(twitter\.com|x\.com|t\.co)",
     "hint": "Los perfiles privados o el contenido sensible necesitan cookies.",
     "cookies": "a veces"},
    {"name": "Instagram", "key": "instagram",
     "re": r"(?:^|\.)(instagram\.com|instagr\.am)",
     "hint": "Casi siempre exige tu sesión: elige el navegador en «Cookies de:».",
     "cookies": "casi siempre"},
    {"name": "Facebook", "key": "facebook",
     "re": r"(?:^|\.)(facebook\.com|fb\.watch|fb\.com)",
     "hint": "Casi siempre exige tu sesión: elige el navegador en «Cookies de:».",
     "cookies": "casi siempre"},
    {"name": "Twitch", "key": "twitch",
     "re": r"(?:^|\.)(twitch\.tv)",
     "hint": "Funciona con VOD y clips. Los directos se graban desde el momento "
             "en que empiezas.",
     "cookies": "a veces"},
    {"name": "Vimeo", "key": "vimeo",
     "re": r"(?:^|\.)(vimeo\.com)",
     "hint": "Los vídeos con contraseña necesitan cookies.",
     "cookies": "a veces"},
    {"name": "Dailymotion", "key": "dailymotion",
     "re": r"(?:^|\.)(dailymotion\.com|dai\.ly)", "hint": "", "cookies": "no"},
    {"name": "SoundCloud", "key": "soundcloud",
     "re": r"(?:^|\.)(soundcloud\.com|snd\.sc)",
     "hint": "Sitio de audio: se descargará como pista de sonido.",
     "cookies": "no", "audio": True},
    {"name": "Bandcamp", "key": "bandcamp",
     "re": r"(?:^|\.)(bandcamp\.com)",
     "hint": "Sitio de audio: se descargará como pista de sonido.",
     "cookies": "no", "audio": True},
    {"name": "Mixcloud", "key": "mixcloud",
     "re": r"(?:^|\.)(mixcloud\.com)",
     "hint": "Sitio de audio: se descargará como pista de sonido.",
     "cookies": "no", "audio": True},
    {"name": "Internet Archive", "key": "archive",
     "re": r"(?:^|\.)(archive\.org)",
     "hint": "Archivo público: material de dominio público y libre.",
     "cookies": "no"},
    {"name": "RTVE", "key": "rtve", "re": r"(?:^|\.)(rtve\.es)",
     "hint": "Televisión pública en abierto.", "cookies": "no"},
    {"name": "Rumble", "key": "rumble", "re": r"(?:^|\.)(rumble\.com)",
     "hint": "", "cookies": "no"},
    {"name": "Odysee", "key": "odysee", "re": r"(?:^|\.)(odysee\.com|lbry\.tv)",
     "hint": "", "cookies": "no"},
    {"name": "Streamable", "key": "streamable",
     "re": r"(?:^|\.)(streamable\.com)", "hint": "", "cookies": "no"},
    {"name": "VK", "key": "vk", "re": r"(?:^|\.)(vk\.com|vkvideo\.ru)",
     "hint": "", "cookies": "a veces"},
    {"name": "Bilibili", "key": "bilibili", "re": r"(?:^|\.)(bilibili\.com)",
     "hint": "", "cookies": "a veces"},
    {"name": "Telegram", "key": "telegram", "re": r"(?:^|\.)(t\.me)",
     "hint": "Solo canales públicos.", "cookies": "no"},
    {"name": "Pinterest", "key": "pinterest", "re": r"(?:^|\.)(pinterest\.[a-z.]+)",
     "hint": "", "cookies": "no"},
    {"name": "Tumblr", "key": "tumblr", "re": r"(?:^|\.)(tumblr\.com)",
     "hint": "", "cookies": "a veces"},
    {"name": "Spotify", "key": "spotify",
     "re": r"(?:^|\.)(open\.spotify\.com|spotify\.link)",
     "hint": "Spotify no entrega sus audios: se lee el título y el artista y "
             "la canción se busca y se descarga de YouTube.",
     "cookies": "no", "audio": True},
    {"name": "Enlace directo", "key": "direct",
     "re": r"\.(mp4|webm|mkv|mov|mp3|m4a|ogg|wav|flac|m3u8|mpd)(?:\?|#|$)",
     "hint": "Archivo o emisión servidos directamente por el servidor.",
     "cookies": "no"},
]


SPOTIFY_ID_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist)/([A-Za-z0-9]+)",
    re.I)


def _pedir_web(url, timeout=25):
    peticion = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                      "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
        return respuesta.read().decode("utf-8", "replace")


def _limpiar_nombre(texto):
    """Deja un nombre de archivo válido en Windows."""
    texto = re.sub(r'[<>:"/\\|?*]', "-", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" .")
    return texto[:120] or "cancion"


def spotify_canciones(url):
    """Lista de (consulta, nombre) a partir de un enlace de Spotify.

    Solo usa datos públicos de la página (título, artista): los mismos que
    muestra el reproductor web a cualquiera. El audio nunca sale de Spotify;
    lo que se descarga después es el resultado equivalente de YouTube.
    """
    coincidencia = SPOTIFY_ID_RE.search(url)
    if not coincidencia:
        raise ValueError("No parece un enlace de canción, álbum o lista de "
                         "Spotify.")
    tipo, identificador = coincidencia.group(1).lower(), coincidencia.group(2)

    if tipo == "track":
        html = _pedir_web(f"https://open.spotify.com/track/{identificador}")
        titulo = re.search(r'property="og:title"[^>]+content="([^"]*)"', html)
        descripcion = re.search(r'property="og:description"[^>]+content="([^"]*)"',
                                html)
        titulo = titulo.group(1).strip() if titulo else ""
        artista = ""
        if descripcion:
            # formato: «Artista · Álbum · Song · Año»
            partes = [p.strip() for p in re.split(r"·|\u00b7", descripcion.group(1))]
            if partes:
                artista = partes[0]
        if not titulo:
            raise ValueError("Spotify no ha devuelto el título de la canción.")
        return [(f"{artista} {titulo}".strip(),
                 _limpiar_nombre(f"{artista} - {titulo}" if artista else titulo))]

    html = _pedir_web(f"https://open.spotify.com/embed/{tipo}/{identificador}")
    canciones = []
    for bloque in re.finditer(
            r'"uri":"spotify:track:[^"]+","uid":"[^"]*","title":"([^"]*)",'
            r'"subtitle":"([^"]*)"', html):
        # Vienen con escapes JSON (É…): decodificarlos como JSON es lo
        # único que respeta los acentos.
        titulo = json.loads('"%s"' % bloque.group(1))
        artista = json.loads('"%s"' % bloque.group(2))
        canciones.append((f"{artista} {titulo}".strip(),
                          _limpiar_nombre(f"{artista} - {titulo}")))
    if not canciones:
        raise ValueError("No se ha podido leer la lista de canciones. Comprueba "
                         "que el enlace sea público.")
    return canciones


def detect_site(url):
    """Reconoce la red a partir de la URL. Devuelve None si no hay coincidencia."""
    if not url or not url.strip():
        return None
    url = url.strip()
    match = re.match(r"^(?:https?://)?([^/?#]+)", url, re.I)
    host = (match.group(1) if match else "").lower()
    host = host.split("@")[-1].split(":")[0]
    for site in SITES:
        target = url if site["key"] == "direct" else host
        if re.search(site["re"], target, re.I):
            return site
    return None


def attempts_for(site):
    """Escalera de reintentos adecuada al sitio detectado."""
    if site and site["key"] == "youtube":
        return YOUTUBE_ATTEMPTS
    if site is None:
        return YOUTUBE_ATTEMPTS  # sitio desconocido: puede ser un espejo
    return GENERIC_ATTEMPTS

# Errores que merece la pena reintentar con otro cliente
BLOCK_RE = re.compile(
    r"(http error 403|forbidden|unable to download video data|"
    r"unable to download webpage|fragment.*(not found|failed)|"
    r"unable to extract|player response|precondition check failed|"
    r"requested format is not available|giving up after)", re.I)

# Errores que solo se arreglan con cookies de una sesión iniciada
BOT_RE = re.compile(r"(sign in to confirm|not a bot|confirm your age|"
                    r"age.?restricted|private video|members[- ]only|"
                    r"requiring login|requires login|login required|"
                    r"use --cookies)", re.I)

# Cookies de Chrome/Edge que no se pueden descifrar (cifrado app-bound)
COOKIE_DECRYPT_RE = re.compile(
    r"(failed to decrypt|could not decrypt|dpapi|app.?bound)", re.I)
# El navegador está abierto y bloquea su base de cookies
COOKIE_LOCKED_RE = re.compile(r"could not copy .*cookie", re.I)

# El sitio ha cambiado y este yt-dlp ya no lo entiende: toca actualizar
STALE_RE = re.compile(r"(page needs to be reloaded|unexpected response|"
                      r"please report this issue|confirm you are on the latest|"
                      r"unable to extract|nsig extraction failed)", re.I)

# Límite temporal del servidor: no es un fallo del programa
THROTTLE_RE = re.compile(r"(http error 429|too many requests|"
                         r"http error 403|forbidden)", re.I)

MEDIA_EXTS = (
    ".mp4 .mkv .webm .mov .avi .flv .wmv .m4v .mpg .mpeg .ts .3gp "
    ".mp3 .m4a .wav .flac .ogg .opus .aac .wma .aiff .alac"
).split()


# --------------------------------------------------------------------------
# Utilidades de sistema
# --------------------------------------------------------------------------

def creation_flags():
    """Evita que se abran ventanas negras de consola en Windows."""
    if IS_WINDOWS:
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return 0


def clean_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_capture(cmd, timeout=25):
    """Ejecuta un comando y devuelve (codigo, salida)."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags(),
            env=clean_env(),
            timeout=timeout,
        )
        out = proc.stdout.decode("utf-8", "replace").strip()
        return proc.returncode, out
    except FileNotFoundError:
        return 127, "No encontrado"
    except subprocess.TimeoutExpired:
        return 124, "Tiempo de espera agotado"
    except Exception as exc:  # pragma: no cover - defensivo
        return 1, str(exc)


def _exe(name):
    return name + ".exe" if IS_WINDOWS else name


def find_ffmpeg():
    local = os.path.join(BIN_DIR, _exe("ffmpeg"))
    if os.path.isfile(local):
        return local
    return shutil.which("ffmpeg")


def find_ffprobe():
    local = os.path.join(BIN_DIR, _exe("ffprobe"))
    if os.path.isfile(local):
        return local
    return shutil.which("ffprobe")


def find_ytdlp():
    """Devuelve la lista de argumentos base para invocar yt-dlp."""
    local = os.path.join(BIN_DIR, _exe("yt-dlp"))
    if os.path.isfile(local):
        return [local]
    if not FROZEN:
        try:
            import yt_dlp  # noqa: F401
            return [sys.executable, "-m", "yt_dlp"]
        except Exception:
            pass
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    return None


def edad_de_version(version):
    """Días transcurridos desde una versión de yt-dlp (formato AAAA.MM.DD)."""
    match = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", (version or "").strip())
    if not match:
        return None
    try:
        publicada = datetime.date(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    return max(0, (datetime.date.today() - publicada).days)


# Rutas de la carpeta «User Data» de cada navegador basado en Chromium.
CHROMIUM_USER_DATA = {
    "chrome": ["{LOCALAPPDATA}", "Google", "Chrome", "User Data"],
    "edge": ["{LOCALAPPDATA}", "Microsoft", "Edge", "User Data"],
    "brave": ["{LOCALAPPDATA}", "BraveSoftware", "Brave-Browser", "User Data"],
    "vivaldi": ["{LOCALAPPDATA}", "Vivaldi", "User Data"],
    "chromium": ["{LOCALAPPDATA}", "Chromium", "User Data"],
    "opera": ["{APPDATA}", "Opera Software", "Opera Stable"],
}


def _copiar_archivo_bloqueado(origen, destino):
    """Copia un archivo que otro proceso tiene abierto (p. ej. el navegador).

    Windows lo permite si se abre con el modo de compartición completo; la
    copia normal falla con «lo está usando otro proceso». Así el programa no
    obliga a cerrar el navegador para leer sus cookies.
    """
    import ctypes
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    FILE_SHARE_ALL = 0x07               # READ | WRITE | DELETE
    OPEN_EXISTING = 3
    INVALID = ctypes.c_void_p(-1).value

    kernel = ctypes.windll.kernel32
    crear = kernel.CreateFileW
    crear.restype = ctypes.c_void_p
    crear.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                      ctypes.c_void_p]
    leer = kernel.ReadFile
    leer.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

    handle = crear(origen, GENERIC_READ, FILE_SHARE_ALL, None, OPEN_EXISTING,
                   0, None)
    if handle == INVALID:
        raise ctypes.WinError()
    try:
        datos = bytearray()
        buffer = ctypes.create_string_buffer(1 << 20)
        leidos = wintypes.DWORD(0)
        while True:
            if not leer(handle, buffer, len(buffer), ctypes.byref(leidos), None):
                raise ctypes.WinError()
            if leidos.value == 0:
                break
            datos += buffer.raw[:leidos.value]
    finally:
        kernel.CloseHandle(handle)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "wb") as salida:
        salida.write(datos)


def preparar_cookies_navegador(navegador):
    """Devuelve (valor para --cookies-from-browser, carpeta temporal|None).

    Para los navegadores basados en Chromium copia su base de cookies (y el
    «Local State» con la clave) a una carpeta temporal, de modo que funcione
    aunque el navegador esté abierto. Firefox no lo necesita.
    """
    import tempfile

    clave = navegador.lower()
    if not IS_WINDOWS or clave not in CHROMIUM_USER_DATA:
        return navegador.lower(), None

    partes = [os.environ.get(p[1:-1], "") if p.startswith("{") else p
              for p in CHROMIUM_USER_DATA[clave]]
    user_data = os.path.join(*partes)
    origen_cookies = os.path.join(user_data, "Default", "Network", "Cookies")
    if not os.path.isfile(origen_cookies):
        origen_cookies = os.path.join(user_data, "Default", "Cookies")
    if not os.path.isfile(origen_cookies):
        return navegador.lower(), None

    temporal = tempfile.mkdtemp(prefix="risa_ck_")
    try:
        _copiar_archivo_bloqueado(
            origen_cookies, os.path.join(temporal, "Default", "Network", "Cookies"))
        local_state = os.path.join(user_data, "Local State")
        if os.path.isfile(local_state):
            shutil.copy2(local_state, os.path.join(temporal, "Local State"))
        return f"{clave}:{temporal}", temporal
    except Exception:
        shutil.rmtree(temporal, ignore_errors=True)
        return navegador.lower(), None


def default_download_dir():
    home = os.path.expanduser("~")
    for folder in ("Downloads", "Descargas", "Videos", "Vídeos"):
        candidate = os.path.join(home, folder)
        if os.path.isdir(candidate):
            return candidate
    return home


def unique_path(path):
    """Evita sobrescribir: archivo.mp4 -> archivo (1).mp4"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def human_time(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# --------------------------------------------------------------------------
# Construcción de comandos
# --------------------------------------------------------------------------

def build_ytdlp_args(url, opts):
    """Construye los argumentos de yt-dlp a partir de las opciones de la GUI."""
    args = [
        "--newline", "--no-warnings", "--ignore-config", "--progress",
        # Reintentos internos: la mayoría de los cortes son temporales
        "--retries", "10",
        "--fragment-retries", "10",
        "--extractor-retries", "5",
        "--file-access-retries", "5",
        "--retry-sleep", "http:exp=1:20",
    ]

    client = opts.get("client")
    if client:
        args += ["--extractor-args", f"youtube:player_client={client}"]

    cookies_file = opts.get("cookies_file")
    cookies_arg = opts.get("cookies_browser_arg")   # ya preparado por el worker
    cookies = opts.get("cookies")
    if cookies_file and os.path.isfile(cookies_file):
        args += ["--cookies", cookies_file]
    elif cookies_arg:
        args += ["--cookies-from-browser", cookies_arg]
    elif cookies and cookies not in ("Ninguno", COOKIES_FILE_OPTION,
                                     COOKIES_AUTO_OPTION):
        args += ["--cookies-from-browser", cookies.lower()]

    if opts.get("force_ipv4"):
        args += ["--force-ipv4"]

    ffmpeg = opts.get("ffmpeg")
    if ffmpeg:
        args += ["--ffmpeg-location", os.path.dirname(ffmpeg) or ffmpeg]

    outdir = opts["outdir"]
    if opts.get("playlist"):
        args += ["--yes-playlist"]
        tmpl = os.path.join(
            outdir, "%(playlist_title)s", "%(playlist_index)03d - %(title)s.%(ext)s"
        )
    elif opts.get("nombre_archivo"):
        args += ["--no-playlist"]
        tmpl = os.path.join(outdir, opts["nombre_archivo"] + ".%(ext)s")
    else:
        args += ["--no-playlist"]
        tmpl = os.path.join(outdir, "%(title)s.%(ext)s")
    args += ["-o", tmpl]

    if opts.get("mode") == "audio":
        fmt = opts["format"].lower()
        args += ["-f", "bestaudio/best", "-x", "--audio-format", fmt]
        quality = opts.get("quality", "")
        if fmt in ("wav", "flac"):
            codec, rate = PCM_MAP.get(quality, (None, None))
            pp = []
            if rate:
                pp += ["-ar", rate]
            if codec and fmt == "wav":
                pp += ["-c:a", codec]
            if pp:
                args += ["--postprocessor-args", "ExtractAudio:" + " ".join(pp)]
        else:
            kbps = re.sub(r"[^0-9]", "", quality) or "192"
            args += ["--audio-quality", f"{kbps}K"]
        if opts.get("extras"):
            args += ["--embed-metadata"]
            # WAV no admite carátula incrustada
            if fmt != "wav":
                args += ["--embed-thumbnail"]
    else:
        fmt = opts["format"].lower()
        height = opts.get("height")
        hfilter = f"[height<={height}]" if height else ""

        site = opts.get("site") or {}
        if site.get("key") == "tiktok":
            # TikTok ofrece la misma toma con y sin marca de agua: la pista
            # «download_addr» es la marcada. Se descarta por identificador y,
            # si el vídeo solo estuviera disponible así, se cae a lo que haya.
            sin_marca = "[format_id!^=download][format_id!*=watermark]"
            selector = (f"bv*{hfilter}{sin_marca}+ba/"
                        f"b{hfilter}{sin_marca}/"
                        f"bv*{hfilter}+ba/b{hfilter}/b")
            args += ["-f", selector, "--merge-output-format", fmt]
            if opts.get("extras"):
                args += ["--embed-metadata", "--embed-thumbnail"]
            args.append(url)
            return args

        if fmt == "mp4":
            selector = (
                f"bv*{hfilter}[ext=mp4]+ba[ext=m4a]/"
                f"bv*{hfilter}+ba/b{hfilter}/b"
            )
        elif fmt == "webm":
            selector = (
                f"bv*{hfilter}[ext=webm]+ba[ext=webm]/"
                f"bv*{hfilter}+ba/b{hfilter}/b"
            )
        else:
            selector = f"bv*{hfilter}+ba/b{hfilter}/b"
        args += ["-f", selector]

        if fmt in ("mp4", "mkv", "webm"):
            args += ["--merge-output-format", fmt]
        else:  # mov, avi -> requieren recodificar
            args += ["--merge-output-format", "mkv", "--recode-video", fmt]

        if opts.get("subs"):
            args += [
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                "es.*,en.*",
                "--convert-subs",
                "srt",
            ]
            if fmt in ("mp4", "mkv", "webm"):
                args += ["--embed-subs"]
        if opts.get("extras"):
            args += ["--embed-metadata", "--embed-thumbnail"]

    args.append(url)
    return args


def build_ffmpeg_args(src, dst, target, opts):
    """Construye los argumentos de FFmpeg para convertir un archivo local."""
    target = target.lower()
    args = ["-hide_banner", "-loglevel", "error", "-nostdin",
            "-progress", "pipe:1", "-y", "-i", src]

    if target in [f.lower() for f in AUDIO_FORMATS]:
        args += ["-vn"]
        quality = opts.get("audio_quality", "192 kbps")
        if target == "wav":
            codec, rate = PCM_MAP.get(quality, ("pcm_s16le", "44100"))
            args += ["-c:a", codec or "pcm_s16le"]
            if rate:
                args += ["-ar", rate]
        elif target == "flac":
            _, rate = PCM_MAP.get(quality, (None, None))
            args += ["-c:a", "flac"]
            if rate:
                args += ["-ar", rate]
        else:
            kbps = re.sub(r"[^0-9]", "", quality) or "192"
            codec = {
                "mp3": "libmp3lame",
                "m4a": "aac",
                "ogg": "libvorbis",
                "opus": "libopus",
            }[target]
            args += ["-c:a", codec, "-b:a", f"{kbps}k"]
        args.append(dst)
        return args

    # ---- vídeo ----
    height = opts.get("height")
    quality = opts.get("video_quality", "Media (recomendada)")
    vf = []
    if height:
        vf.append(f"scale=-2:trunc(min(ih\\,{height})/2)*2")

    if target == "gif":
        fps = opts.get("gif_fps", 12)
        width = opts.get("gif_width", 480)
        args += [
            "-filter_complex",
            f"fps={fps},scale={width}:-1:flags=lanczos,"
            f"split[a][b];[a]palettegen[p];[b][p]paletteuse",
            "-loop", "0",
        ]
        args.append(dst)
        return args

    if quality.startswith("Copiar"):
        args += ["-c", "copy"]
        if target == "mp4":
            args += ["-movflags", "+faststart"]
        args.append(dst)
        return args

    crf = VIDEO_QUALITY_CRF.get(quality, 23)
    if target == "webm":
        args += ["-c:v", "libvpx-vp9", "-crf", str(crf + 8), "-b:v", "0",
                 "-row-mt", "1", "-c:a", "libopus", "-b:a", "160k"]
    elif target == "avi":
        args += ["-c:v", "mpeg4", "-vtag", "xvid", "-qscale:v", "4",
                 "-c:a", "libmp3lame", "-b:a", "192k"]
    else:  # mp4, mkv, mov
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
        if target in ("mp4", "mov"):
            args += ["-movflags", "+faststart"]

    if vf:
        args += ["-vf", ",".join(vf)]
    args.append(dst)
    return args


def probe_duration(ffprobe, path):
    if not ffprobe:
        return None
    rc, out = run_capture(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path]
    )
    try:
        value = float(out.strip().splitlines()[-1])
        return value if value > 0 else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# Aspecto retro: paleta, tipografías y widgets clásicos dibujados a mano
#
# Todo se pinta sobre Canvas con los biseles de 2 px del estilo clásico de
# Windows (los mismos que DrawEdge: RAISED, SUNKEN y ETCHED), así que no hace
# falta ningún PNG y la aplicación sigue siendo un único archivo .py.
#
# Hay dos pieles: "95" (gris con barra azul marino) y "xp" (Luna).
# Las medidas se escriben en píxeles de diseño y pasan por S() para que la
# ventana se vea nítida en pantallas con escalado 125/150/200 %.
# --------------------------------------------------------------------------

ASSETS_DIR = os.path.join(RES_DIR, "assets")
PIXEL_FONT = os.path.join(ASSETS_DIR, "ms-sans-serif.ttf")
PIXEL_FAMILY = "pix M 8pt"

ICON_ICO = os.path.join(ASSETS_DIR, "risa.ico")
ICON_PNGS = {size: os.path.join(ASSETS_DIR, f"risa-{size}.png")
             for size in (16, 24, 32, 48)}


def icon_image(widget, design_size=16):
    """Devuelve el icono al tamaño pedido usando solo Tk (sin Pillow).

    Se elige el PNG ya generado más cercano al tamaño real en pantalla, de
    modo que el dibujo no se deforme.
    """
    wanted = S(design_size)
    available = sorted(ICON_PNGS)
    best = min(available, key=lambda size: abs(size - wanted))
    path = ICON_PNGS[best]
    if not os.path.isfile(path):
        return None
    try:
        return tk.PhotoImage(master=widget, file=path)
    except Exception:
        return None

THEMES = {
    "95": {
        "name": "Windows 95",
        "face": "#c0c0c0",        # fondo de ventanas y botones
        "light": "#dfdfdf",       # bisel exterior claro
        "hilite": "#ffffff",      # bisel interior claro
        "shadow": "#808080",      # bisel interior oscuro
        "dark": "#000000",        # bisel exterior oscuro
        "text": "#000000",
        "gray_text": "#808080",
        "field": "#ffffff",
        "field_text": "#000000",
        "sel": "#000080",
        "sel_text": "#ffffff",
        "title_a": "#000080",     # degradado de la barra de título
        "title_b": "#1084d0",
        "title_text": "#ffffff",
        "desktop": "#008080",
        "bar": "#000080",         # relleno de la barra de progreso
    },
    "xp": {
        "name": "Windows XP",
        "face": "#ece9d8",
        "light": "#f5f4ea",
        "hilite": "#ffffff",
        "shadow": "#aca899",
        "dark": "#716f64",
        "text": "#000000",
        "gray_text": "#aca899",
        "field": "#ffffff",
        "field_text": "#000000",
        "sel": "#316ac5",
        "sel_text": "#ffffff",
        "title_a": "#0054e3",
        "title_b": "#3d95ff",
        "title_text": "#ffffff",
        "desktop": "#3a6ea5",
        "bar": "#25a021",
    },
}

THEME = dict(THEMES["95"])   # piel activa; la cambia App._apply_theme()
UI_SCALE = 1.0
_FAMILY = None
_PIXEL_OK = False


def T(key):
    """Color de la piel activa."""
    return THEME[key]


def enable_dpi_awareness():
    """Evita que Windows estire la ventana (que se vería borrosa)."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # Ojo: estas funciones no lanzan excepción cuando fallan, devuelven 0
        # (o un HRESULT distinto de 0), así que hay que mirar el resultado.
        try:
            user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
            user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except (AttributeError, OSError):
            pass
        try:
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
                return
        except (AttributeError, OSError):
            pass
        user32.SetProcessDPIAware()
    except Exception:
        pass


def S(value):
    """Convierte píxeles de diseño en píxeles reales de pantalla."""
    return int(round(value * UI_SCALE))


def load_pixel_font():
    """Registra la fuente del kit solo para este proceso (no la instala)."""
    global _PIXEL_OK
    if not IS_WINDOWS or not os.path.isfile(PIXEL_FONT):
        return
    try:
        import ctypes
        FR_PRIVATE = 0x10
        added = ctypes.windll.gdi32.AddFontResourceExW(PIXEL_FONT, FR_PRIVATE, 0)
        _PIXEL_OK = bool(added)
    except Exception:
        _PIXEL_OK = False


def _family():
    """Familia tipográfica de la interfaz.

    La fuente del kit es de píxeles: solo se ve bien a tamaños enteros, así
    que se usa cuando el escalado es 100 % o 200 %. En 125/150 % se recurre a
    la tipografía del sistema, que es la que usaban de verdad estas ventanas.
    """
    global _FAMILY
    if _FAMILY is None:
        families = set(tkfont.families())
        for candidate in ("Tahoma", "Microsoft Sans Serif", "MS Sans Serif",
                          "DejaVu Sans"):
            if candidate in families:
                _FAMILY = candidate
                break
        else:
            _FAMILY = tkfont.nametofont("TkDefaultFont").cget("family")
    return _FAMILY


def ui_font(size=8, weight="normal"):
    return (_family(), size, weight)


def title_font():
    """Tipografía de la barra de título.

    La del kit es de píxeles: solo se usa si está disponible y el escalado es
    entero (si no, se deformaría). Además no trae acentos españoles, por eso
    no se usa para el resto de la interfaz.
    """
    integer_scale = abs(UI_SCALE - round(UI_SCALE)) < 0.01
    if _PIXEL_OK and integer_scale and PIXEL_FAMILY in set(tkfont.families()):
        return (PIXEL_FAMILY, -(11 * max(1, int(round(UI_SCALE)))))
    return ui_font(8, "bold")


def mono_font(size=8):
    families = set(tkfont.families())
    for candidate in ("Consolas", "Courier New", "DejaVu Sans Mono"):
        if candidate in families:
            return (candidate, size)
    return (tkfont.nametofont("TkFixedFont").cget("family"), size)


# ---- biseles ----------------------------------------------------------------

def _line(canvas, x1, y1, x2, y2, colour, w):
    canvas.create_line(x1, y1, x2, y2, fill=colour, width=w)


def bevel(canvas, x1, y1, x2, y2, style="raised"):
    """Dibuja el borde 3D de 2 px característico del estilo clásico."""
    w = max(1, S(1))
    if style == "raised":        # botón en reposo
        rings = ((T("light"), T("dark")), (T("hilite"), T("shadow")))
    elif style == "pressed":     # botón pulsado
        rings = ((T("shadow"), T("hilite")), (T("dark"), T("light")))
    elif style == "sunken":      # cajas de texto y listas
        rings = ((T("shadow"), T("hilite")), (T("dark"), T("light")))
    elif style == "etched":      # marcos de grupo y separadores
        rings = ((T("shadow"), T("hilite")), (T("hilite"), T("shadow")))
    elif style == "thin":        # paneles de la barra de estado
        rings = ((T("shadow"), T("hilite")),)
    else:
        return

    for index, (top_left, bottom_right) in enumerate(rings):
        off = index * w
        left, top = x1 + off, y1 + off
        right, bottom = x2 - off, y2 - off
        half = w / 2
        _line(canvas, left, top + half, left, bottom, top_left, w)          # izq
        _line(canvas, left, top + half, right, top + half, top_left, w)     # arriba
        _line(canvas, right - half, top, right - half, bottom, bottom_right, w)
        _line(canvas, left, bottom - half, right, bottom - half, bottom_right, w)


def gradient(canvas, x1, y1, x2, y2, colour_a, colour_b, steps=64):
    """Degradado horizontal como el de las barras de título."""
    r1, g1, b1 = canvas.winfo_rgb(colour_a)
    r2, g2, b2 = canvas.winfo_rgb(colour_b)
    width = max(1, x2 - x1)
    step = max(1, width / steps)
    x = x1
    while x < x2:
        t = (x - x1) / width
        colour = "#%02x%02x%02x" % (
            int((r1 + (r2 - r1) * t) / 256),
            int((g1 + (g2 - g1) * t) / 256),
            int((b1 + (b2 - b1) * t) / 256),
        )
        canvas.create_rectangle(x, y1, min(x + step + 1, x2), y2,
                                fill=colour, outline=colour)
        x += step


def _widget_bg(widget):
    try:
        return widget.cget("bg")
    except tk.TclError:
        return T("face")


# ---- widgets ----------------------------------------------------------------

class RetroButton(tk.Canvas):
    """Botón clásico: bisel saliente, hundido al pulsar y texto grabado si está
    deshabilitado."""

    def __init__(self, master, text, command=None, width=100, height=25,
                 bg=None, font=None, default=False):
        bg = bg or _widget_bg(master)
        super().__init__(master, width=S(width), height=S(height), bg=bg,
                         highlightthickness=0, bd=0, takefocus=1)
        self._text = text
        self._command = command
        self._font = font or ui_font(8)
        self._default = default
        self._btn_state = "normal"
        self._pressed = False
        self._focus = False
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Leave>", lambda _e: (setattr(self, "_pressed", False),
                                         self._redraw()))
        self.bind("<FocusIn>", lambda _e: (setattr(self, "_focus", True),
                                           self._redraw()))
        self.bind("<FocusOut>", lambda _e: (setattr(self, "_focus", False),
                                            self._redraw()))
        self.bind("<Return>", lambda _e: self._fire())
        self.bind("<space>", lambda _e: self._fire())
        self.bind("<Configure>", lambda _e: self._redraw())
        self._redraw()

    # API compatible con los widgets a los que sustituye
    def configure(self, **kwargs):
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if "state" in kwargs:
            self._btn_state = kwargs.pop("state")
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if kwargs:
            super().configure(**kwargs)
        self._redraw()

    config = configure

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width() or int(self["width"])
        h = self.winfo_height() or int(self["height"])
        inset = S(1) if self._default else 0

        if self._default:   # el botón predeterminado lleva un marco negro
            self.create_rectangle(0, 0, w - 1, h - 1, outline=T("dark"),
                                  width=max(1, S(1)))
        self.create_rectangle(inset, inset, w - 1 - inset, h - 1 - inset,
                              fill=T("face"), outline=T("face"))
        bevel(self, inset, inset, w - inset, h - inset,
              "pressed" if self._pressed else "raised")

        offset = S(1) if self._pressed else 0
        cx, cy = w / 2 + offset, h / 2 + offset
        if self._btn_state == "disabled":
            self.create_text(cx + S(1), cy + S(1), text=self._text,
                             fill=T("hilite"), font=self._font)
            self.create_text(cx, cy, text=self._text, fill=T("shadow"),
                             font=self._font)
        else:
            self.create_text(cx, cy, text=self._text, fill=T("text"),
                             font=self._font)
            if self._focus:
                self.create_rectangle(S(4) + offset, S(4) + offset,
                                      w - S(4) + offset, h - S(4) + offset,
                                      outline=T("text"), dash=(1, 1))
        # ojo: configure() está sobrescrito, hay que ir al de Canvas
        tk.Canvas.configure(self, cursor="" if self._btn_state == "disabled"
                            else "arrow")

    def _fire(self):
        if self._btn_state != "disabled" and self._command:
            self._command()

    def _on_press(self, _e):
        if self._btn_state == "disabled":
            return
        self.focus_set()
        self._pressed = True
        self._redraw()

    def _on_release(self, _e):
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was:
            self._fire()


class RetroCheck(tk.Canvas):
    """Casilla de verificación clásica: cajita hundida blanca con la marca en
    negro."""

    BOX = 13

    def __init__(self, master, text, variable, bg=None, command=None,
                 radio=False, value=None):
        bg = bg or _widget_bg(master)
        self._font = ui_font(8)
        measure = tkfont.Font(font=self._font).measure(text)
        super().__init__(master, width=measure + S(self.BOX + 10),
                         height=S(self.BOX + 5), bg=bg,
                         highlightthickness=0, bd=0)
        self._var = variable
        self._text = text
        self._bg = bg
        self._command = command
        self._radio = radio
        self._value = value
        self._enabled = True
        self.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *_: self._redraw())
        self._redraw()

    def set_enabled(self, enabled):
        self._enabled = enabled
        self._redraw()

    def _is_on(self):
        if self._radio:
            return self._var.get() == self._value
        return bool(self._var.get())

    def _toggle(self, _e=None):
        if not self._enabled:
            return
        if self._radio:
            if self._var.get() == self._value:
                return
            self._var.set(self._value)
        else:
            self._var.set(not self._var.get())
        if self._command:
            self._command()

    def _redraw(self):
        self.delete("all")
        box = S(self.BOX)
        top = S(2)
        on = self._is_on()
        text_colour = T("text") if self._enabled else T("gray_text")
        field = T("field") if self._enabled else T("face")

        if self._radio:
            self.create_oval(0, top, box, top + box, fill=field,
                             outline=T("shadow"), width=max(1, S(1)))
            self.create_arc(0, top, box, top + box, start=45, extent=180,
                            style="arc", outline=T("dark"), width=max(1, S(1)))
            if on:
                pad = S(4)
                self.create_oval(pad, top + pad, box - pad, top + box - pad,
                                 fill=text_colour, outline=text_colour)
        else:
            self.create_rectangle(0, top, box, top + box, fill=field,
                                  outline=field)
            bevel(self, 0, top, box, top + box, "sunken")
            if on:
                w = max(1, S(2))
                self.create_line(S(3), top + S(6), S(5), top + S(9),
                                 S(10), top + S(3), fill=text_colour, width=w)

        self.create_text(box + S(7), top + box / 2, text=self._text, anchor="w",
                         fill=text_colour, font=self._font)


class RetroFrame(tk.Frame):
    """Contenedor con bisel (saliente, hundido o grabado)."""

    def __init__(self, master, style="raised", bg=None, pad=0):
        bg = bg or T("face")
        super().__init__(master, bg=bg)
        self._style = style
        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        border = S(2) + S(pad)
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill="both", expand=True, padx=border, pady=border)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        self._canvas.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        bevel(self._canvas, 0, 0, w, h, self._style)


class RetroGroup(tk.Frame):
    """Marco de grupo con etiqueta, como los cuadros de diálogo clásicos."""

    def __init__(self, master, title, bg=None, pad=10):
        bg = bg or T("face")
        super().__init__(master, bg=bg)
        self._title = title
        self._font = ui_font(8)
        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill="both", expand=True, padx=S(pad),
                       pady=(S(pad + 8), S(pad)))
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        self._canvas.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        top = S(7)
        bevel(self._canvas, 0, top, w, h, "etched")
        text_w = tkfont.Font(font=self._font).measure(self._title)
        self._canvas.create_rectangle(S(8), top - S(4), S(14) + text_w,
                                      top + S(5), fill=T("face"),
                                      outline=T("face"))
        self._canvas.create_text(S(11), top, text=self._title, anchor="w",
                                 fill=T("text"), font=self._font)


def retro_entry(parent, textvariable, bg=None, state="normal", width=None):
    """Caja de texto hundida; devuelve (contenedor, entry)."""
    bg = bg or T("face")
    frame = tk.Frame(parent, bg=bg)
    canvas = tk.Canvas(frame, bg=bg, highlightthickness=0, bd=0)
    canvas.place(x=0, y=0, relwidth=1, relheight=1)
    entry = tk.Entry(frame, textvariable=textvariable, bg=T("field"),
                     fg=T("field_text"), relief="flat", highlightthickness=0,
                     bd=0, insertbackground=T("field_text"), font=ui_font(8),
                     disabledbackground=T("face"), disabledforeground=T("gray_text"),
                     selectbackground=T("sel"), selectforeground=T("sel_text"),
                     state=state)
    if width:
        entry.configure(width=width)
    entry.pack(fill="both", expand=True, padx=S(3), pady=S(3))
    frame.bind("<Configure>", lambda _e: (
        canvas.delete("all"),
        canvas.create_rectangle(0, 0, frame.winfo_width(), frame.winfo_height(),
                                fill=T("field"), outline=T("field")),
        bevel(canvas, 0, 0, frame.winfo_width(), frame.winfo_height(), "sunken")))
    return frame, entry


class RetroCombo(tk.Frame):
    """Lista desplegable clásica.

    Expone la misma API que el ttk.Combobox al que sustituye (get, set,
    ["values"], state([...]) y el evento <<ComboboxSelected>>), de modo que la
    lógica de la aplicación no cambia.
    """

    def __init__(self, master, values=(), width=14, bg=None):
        bg = bg or T("face")
        self._font = ui_font(8)
        char = tkfont.Font(font=self._font).measure("0")
        self._w = char * width + S(28)
        super().__init__(master, bg=bg, width=self._w, height=S(21))
        self.pack_propagate(False)
        self.grid_propagate(False)

        self._values = list(values)
        self._value = tk.StringVar(value=self._values[0] if self._values else "")
        self._enabled = True
        self._popup = None
        self._callback = None

        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Configure>", lambda _e: self._redraw())
        self._value.trace_add("write", lambda *_: self._redraw())
        self._redraw()

    # -- API --
    def get(self):
        return self._value.get()

    def set(self, value):
        self._value.set(value)

    def set_values(self, values):
        self._values = list(values)
        if self._value.get() not in self._values and self._values:
            self._value.set(self._values[0])
        self._redraw()

    def __setitem__(self, key, value):
        if key == "values":
            self.set_values(value)
        else:
            super().__setitem__(key, value)

    def __getitem__(self, key):
        if key == "values":
            return list(self._values)
        return super().__getitem__(key)

    def state(self, flags=()):
        for flag in flags:
            if flag == "disabled":
                self._enabled = False
            elif flag == "!disabled":
                self._enabled = True
        self._close()
        self._redraw()

    def bind_select(self, callback):
        self._callback = callback

    def bind(self, sequence=None, func=None, add=None):
        if sequence == "<<ComboboxSelected>>":
            self._callback = func
            return None
        return super().bind(sequence, func, add)

    # -- dibujo --
    def _redraw(self):
        self._canvas.delete("all")
        w = self.winfo_width() or self._w
        h = self.winfo_height() or S(21)
        btn = S(17)

        field = T("field") if self._enabled else T("face")
        self._canvas.create_rectangle(0, 0, w, h, fill=field, outline=field)
        bevel(self._canvas, 0, 0, w - btn, h, "sunken")
        self._canvas.create_text(S(5), h / 2, text=self._value.get(), anchor="w",
                                 fill=T("text") if self._enabled else T("gray_text"),
                                 font=self._font)

        self._canvas.create_rectangle(w - btn, 0, w, h, fill=T("face"),
                                      outline=T("face"))
        bevel(self._canvas, w - btn, 0, w, h,
              "raised" if self._enabled else "raised")
        cx, cy = w - btn / 2, h / 2
        arrow = T("text") if self._enabled else T("gray_text")
        self._canvas.create_polygon(cx - S(4), cy - S(2), cx + S(4), cy - S(2),
                                    cx, cy + S(3), fill=arrow, outline=arrow)

    # -- desplegable --
    def _on_click(self, _event):
        if not self._enabled:
            return
        if self._popup:
            self._close()
        else:
            self._open()

    def _open(self):
        if not self._values:
            return
        self._popup = tk.Toplevel(self)
        self._popup.overrideredirect(True)
        self._popup.configure(bg=T("dark"))
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        rows = min(len(self._values), 10)
        listbox = tk.Listbox(self._popup, bg=T("field"), fg=T("field_text"),
                             font=self._font, highlightthickness=0, bd=0,
                             selectbackground=T("sel"),
                             selectforeground=T("sel_text"),
                             activestyle="none", exportselection=False,
                             height=rows)
        for item in self._values:
            listbox.insert("end", item)
        if self._value.get() in self._values:
            index = self._values.index(self._value.get())
            listbox.selection_set(index)
            listbox.see(index)
        listbox.pack(fill="both", expand=True, padx=S(1), pady=S(1))
        listbox.bind("<ButtonRelease-1>", self._choose)
        listbox.bind("<Return>", self._choose)
        listbox.bind("<Escape>", lambda _e: self._close())
        listbox.focus_set()
        self._popup.geometry(f"{self.winfo_width()}x"
                             f"{listbox.winfo_reqheight() + S(2)}+{x}+{y}")
        self._popup.bind("<FocusOut>", lambda _e: self._close())
        self._listbox = listbox

    def _choose(self, _event=None):
        selection = self._listbox.curselection()
        if selection:
            self._value.set(self._values[selection[0]])
        self._close()
        if self._callback:
            self._callback(None)

    def _close(self):
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None


class RetroProgress(tk.Canvas):
    """Barra de progreso a bloques, como la de los diálogos de copiar archivos."""

    def __init__(self, master, bg=None, height=20, width=300):
        bg = bg or _widget_bg(master)
        super().__init__(master, height=S(height), width=S(width), bg=bg,
                         highlightthickness=0, bd=0)
        self._value = 0.0
        self._indeterminate = False
        self._offset = 0
        self._anim = None
        self.bind("<Configure>", lambda _e: self._redraw())

    def set_value(self, pct):
        self.stop()
        self._value = max(0.0, min(100.0, float(pct)))
        self._redraw()

    def start_indeterminate(self):
        if self._indeterminate:
            return
        self._indeterminate = True
        self._offset = 0
        self._tick()

    def stop(self):
        self._indeterminate = False
        if self._anim is not None:
            self.after_cancel(self._anim)
            self._anim = None

    def _tick(self):
        if not self._indeterminate:
            return
        self._offset += 1
        self._redraw()
        self._anim = self.after(60, self._tick)

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width() or int(self["width"])
        h = self.winfo_height() or int(self["height"])
        self.create_rectangle(0, 0, w, h, fill=T("face"), outline=T("face"))
        bevel(self, 0, 0, w, h, "sunken")

        pad = S(3)
        block = S(8)
        gap = S(2)
        inner = w - pad * 2
        total = max(1, int(inner // (block + gap)))

        if self._indeterminate:
            lit = {(self._offset + i) % total for i in range(3)}
        else:
            filled = int(total * self._value / 100)
            lit = set(range(filled))

        for i in sorted(lit):
            x = pad + i * (block + gap)
            if x + block > w - pad:
                continue
            self.create_rectangle(x, pad, x + block, h - pad,
                                  fill=T("bar"), outline=T("bar"))


class RetroTabs(tk.Canvas):
    """Fila de pestañas clásica: la activa sobresale y se funde con el panel."""

    def __init__(self, master, tabs, command, bg=None, height=24):
        bg = bg or T("face")
        super().__init__(master, height=S(height), bg=bg, highlightthickness=0,
                         bd=0)
        self._tabs = tabs            # [(clave, etiqueta), ...]
        self._command = command
        self._font = ui_font(8)
        self._active = tabs[0][0]
        self._rects = []
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda _e: self._redraw())
        self._redraw()

    def select(self, key):
        self._active = key
        self._redraw()

    def _click(self, event):
        for key, x1, x2 in self._rects:
            if x1 <= event.x <= x2:
                if key != self._active:
                    self._active = key
                    self._redraw()
                    self._command(key)
                return

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width() or 1
        h = self.winfo_height() or S(24)
        font_obj = tkfont.Font(font=self._font)
        self._rects = []

        x = S(2)
        for key, label in self._tabs:
            width = font_obj.measure(label) + S(24)
            active = key == self._active
            top = 0 if active else S(3)
            self.create_rectangle(x, top, x + width, h + S(2), fill=T("face"),
                                  outline=T("face"))
            # bordes: claro arriba/izquierda, oscuro a la derecha
            self.create_line(x, top + S(2), x, h, fill=T("hilite"),
                             width=max(1, S(1)))
            self.create_line(x, top + S(1), x + width - S(2), top + S(1),
                             fill=T("hilite"), width=max(1, S(1)))
            self.create_line(x + width - S(1), top + S(2), x + width - S(1), h,
                             fill=T("dark"), width=max(1, S(1)))
            self.create_line(x + width - S(2), top + S(2), x + width - S(2), h,
                             fill=T("shadow"), width=max(1, S(1)))
            self.create_text(x + width / 2, top + (h - top) / 2 + S(1),
                             text=label, fill=T("text"), font=self._font)
            self._rects.append((key, x, x + width))
            x += width - S(1)

        # línea inferior del marco, interrumpida bajo la pestaña activa
        y = h - S(1)
        self.create_line(0, y, w, y, fill=T("hilite"), width=max(1, S(1)))
        for key, x1, x2 in self._rects:
            if key == self._active:
                self.create_line(x1, y, x2 - S(1), y, fill=T("face"),
                                 width=max(1, S(1)))


class TitleBar(tk.Canvas):
    """Barra de título con degradado, como la de las ventanas de la época."""

    def __init__(self, master, text, height=26, bg=None):
        bg = bg or T("face")
        super().__init__(master, height=S(height), bg=bg, highlightthickness=0,
                         bd=0)
        self._text = text
        self._font = title_font()
        self._icon = icon_image(self, 16)
        self.bind("<Configure>", lambda _e: self._redraw())

    def set_text(self, text):
        self._text = text
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width() or 1
        h = self.winfo_height() or S(26)
        gradient(self, 0, 0, w, h, T("title_a"), T("title_b"))
        x = S(6)
        if self._icon is not None:
            self.create_image(x, h / 2, image=self._icon, anchor="w")
            x += self._icon.width() + S(6)
        self.create_text(x, h / 2, text=self._text, anchor="w",
                         fill=T("title_text"), font=self._font)


class StatusBar(tk.Frame):
    """Barra de estado con paneles hundidos."""

    def __init__(self, master, weights=(1, 0), bg=None):
        bg = bg or T("face")
        super().__init__(master, bg=bg)
        self._panels = []
        for index, weight in enumerate(weights):
            canvas = tk.Canvas(self, height=S(20), bg=bg, highlightthickness=0,
                               bd=0)
            canvas.grid(row=0, column=index, sticky="ew",
                        padx=(0, S(2) if index < len(weights) - 1 else 0))
            self.columnconfigure(index, weight=weight)
            variable = tk.StringVar(value="")
            canvas.bind("<Configure>",
                        lambda _e, c=canvas, v=variable: self._draw(c, v))
            variable.trace_add("write",
                               lambda *_, c=canvas, v=variable: self._draw(c, v))
            self._panels.append((canvas, variable))

    def variable(self, index):
        return self._panels[index][1]

    def set(self, index, text):
        self._panels[index][1].set(text)

    def _draw(self, canvas, variable):
        canvas.delete("all")
        w = canvas.winfo_width() or 1
        h = canvas.winfo_height() or S(20)
        canvas.create_rectangle(0, 0, w, h, fill=T("face"), outline=T("face"))
        bevel(canvas, 0, 0, w, h, "thin")
        canvas.create_text(S(5), h / 2, text=variable.get(), anchor="w",
                           fill=T("text"), font=ui_font(8))


def retro_label(parent, text, bg=None, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=bg or T("face"), fg=T("text"),
                    font=ui_font(8, "bold" if bold else "normal"),
                    anchor="w", justify="left", **kwargs)


# --------------------------------------------------------------------------
# Aplicación
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        # Debe ir antes de crear la ventana, no solo desde main()
        enable_dpi_awareness()
        super().__init__()

        # Escalado: en pantallas a 125/150/200 % todo se dibuja en píxeles
        # reales, de modo que la ventana se ve nítida en vez de estirada.
        global UI_SCALE
        UI_SCALE = max(1.0, round(self.winfo_fpixels("1i") / 96.0, 2))
        self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
        load_pixel_font()

        self.msg_queue = queue.Queue()
        self.download_queue = []      # [{url, opts, estado}, ...]
        self.convert_items = []       # [{path, estado}, ...]
        self.worker = None
        self.proc = None
        self.cancelled = False

        self.ffmpeg = find_ffmpeg()
        self.ffprobe = find_ffprobe()
        self.ytdlp = find_ytdlp()
        self.ytdlp_edad = None        # días desde la versión instalada
        self.cookies_file = None      # ruta del cookies.txt, si se usa

        self.config_data = self._load_config()
        self.cookies_file = self.config_data.get("cookies_file") or None
        self.theme_key = self.config_data.get("theme", "95")
        THEME.clear()
        THEME.update(THEMES.get(self.theme_key, THEMES["95"]))

        self.title(f"{APP_NAME} {APP_VERSION}")
        self._set_window_icon()
        self.geometry(f"{S(940)}x{S(800)}")
        self.minsize(S(820), S(700))
        self.configure(bg=T("face"))

        self._build_ui()
        self._enable_drag_and_drop()
        self._refresh_tools(initial=True)
        self.after(100, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- arrastrar y soltar ----------------

    def _enable_drag_and_drop(self):
        """Permite soltar archivos sobre la ventana.

        Tk no trae arrastrar-y-soltar, así que se usa la API de Windows: se
        marca la ventana como receptora y se intercepta el mensaje
        WM_DROPFILES encadenando el procedimiento de ventana original.
        """
        if not IS_WINDOWS:
            return
        try:
            import ctypes

            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if not hwnd:
                return

            user32 = ctypes.windll.user32
            self._shell32 = ctypes.windll.shell32
            self._shell32.DragAcceptFiles(ctypes.c_void_p(hwnd), True)

            WM_DROPFILES = 0x0233
            GWLP_WNDPROC = -4
            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, ctypes.c_uint,
                                         ctypes.c_size_t, ctypes.c_ssize_t)

            user32.CallWindowProcW.restype = LRESULT
            user32.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                               ctypes.c_uint, ctypes.c_size_t,
                                               ctypes.c_ssize_t]

            def procedure(handle, message, wparam, lparam):
                if message == WM_DROPFILES:
                    try:
                        paths = self._read_dropped_files(wparam)
                        # fuera del procedimiento de ventana, para no bloquear
                        # a la aplicación que soltó los archivos
                        self.after(1, lambda: self._on_files_dropped(paths))
                    except Exception:
                        pass
                    return 0
                return user32.CallWindowProcW(self._old_wndproc, handle,
                                              message, wparam, lparam)

            # La referencia debe sobrevivir: si la recoge el recolector de
            # basura, Windows llamaría a memoria liberada.
            self._drop_procedure = WNDPROC(procedure)

            setter = getattr(user32, "SetWindowLongPtrW", None) or \
                user32.SetWindowLongW
            setter.restype = ctypes.c_void_p
            setter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            self._old_wndproc = setter(
                ctypes.c_void_p(hwnd), GWLP_WNDPROC,
                ctypes.cast(self._drop_procedure, ctypes.c_void_p))
        except Exception:
            pass

    def _read_dropped_files(self, hdrop):
        """Lee la lista de archivos que trae el mensaje y libera su memoria."""
        import ctypes

        query = self._shell32.DragQueryFileW
        query.restype = ctypes.c_uint
        query.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                          ctypes.c_wchar_p, ctypes.c_uint]

        handle = ctypes.c_void_p(hdrop)
        count = query(handle, 0xFFFFFFFF, None, 0)
        paths = []
        buffer = ctypes.create_unicode_buffer(1024)
        for index in range(count):
            if query(handle, index, buffer, 1024):
                paths.append(buffer.value)
        self._shell32.DragFinish(handle)
        return paths

    def _on_files_dropped(self, paths):
        """Los archivos soltados van a la cola de conversión."""
        candidates = []
        for path in paths:
            if os.path.isdir(path):
                for name in sorted(os.listdir(path)):
                    full = os.path.join(path, name)
                    if os.path.isfile(full):
                        candidates.append(full)
            else:
                candidates.append(path)

        media = [p for p in candidates
                 if os.path.splitext(p)[1].lower() in MEDIA_EXTS]
        if not media:
            self.status("Los archivos soltados no son de vídeo ni de audio.")
            return

        added = self._add_files(media)
        self._show_page("convert")
        if added:
            self.status(f"Añadidos {added} archivo(s) a la cola de conversión.")
            self.log(f"▶ Añadidos {added} archivo(s) por arrastre.", "info")
        else:
            self.status("Esos archivos ya estaban en la cola.")

    def _set_window_icon(self):
        """Icono de la ventana y de la barra de tareas."""
        if IS_WINDOWS and os.path.isfile(ICON_ICO):
            try:
                self.iconbitmap(default=ICON_ICO)
                return
            except Exception:
                pass
        image = icon_image(self, 48)
        if image is not None:
            self._icon_big = image        # hay que conservar la referencia
            try:
                self.iconphoto(True, image)
            except Exception:
                pass

    # ---------------- configuración persistente ----------------

    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_config(self):
        data = {
            "outdir": self.var_outdir.get(),
            "conv_outdir": self.var_conv_outdir.get(),
            "mode": self.var_mode.get(),
            "video_format": self.var_vformat.get(),
            "audio_format": self.var_aformat.get(),
            "cookies": self.cmb_cookies.get(),
            "cookies_file": self.cookies_file or "",
            "theme": self.theme_key,
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ---------------- interfaz ----------------

    def _scrollbar(self, master, command):
        """Barra de desplazamiento clásica (la de Tk, sin tema, ya lo es)."""
        return tk.Scrollbar(master, orient="vertical", command=command,
                            bg=T("face"), troughcolor=T("light"),
                            activebackground=T("face"), relief="raised",
                            borderwidth=max(1, S(1)), width=S(16),
                            highlightthickness=0, elementborderwidth=max(1, S(1)))

    def _build_ui(self):
        self.configure(bg=T("face"))

        self.titlebar = TitleBar(self, f"{APP_NAME}  {APP_VERSION}")
        self.titlebar.pack(fill="x", padx=S(3), pady=(S(3), 0))

        self.tabs = RetroTabs(self, [("download", "Descargar"),
                                     ("convert", "Convertir"),
                                     ("settings", "Opciones")],
                              command=self._show_page)
        self.tabs.pack(fill="x", padx=S(3), pady=(S(6), 0))

        # La barra inferior se coloca antes del panel elástico para que no
        # quede fuera de la ventana.
        self._build_bottom()

        panel = RetroFrame(self, style="raised", pad=4)
        panel.pack(fill="both", expand=True, padx=S(3), pady=0)
        holder = panel.body
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self.pages = {}
        for key in ("download", "convert", "settings"):
            page = tk.Frame(holder, bg=T("face"))
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = page

        self._build_download_page()
        self._build_convert_page()
        self._build_settings_page()
        self._show_page("download")

    def _sunken_list(self, parent, height=8):
        """Lista con marco hundido y barra de desplazamiento clásica."""
        wrap = tk.Frame(parent, bg=T("face"))
        canvas = tk.Canvas(wrap, bg=T("face"), highlightthickness=0, bd=0)
        canvas.place(x=0, y=0, relwidth=1, relheight=1)
        wrap.bind("<Configure>", lambda _e: (
            canvas.delete("all"),
            bevel(canvas, 0, 0, wrap.winfo_width(), wrap.winfo_height(),
                  "sunken")))

        inner = tk.Frame(wrap, bg=T("field"))
        inner.pack(fill="both", expand=True, padx=S(3), pady=S(3))
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)
        listbox = tk.Listbox(inner, selectmode="extended", activestyle="none",
                             height=height, bd=0, highlightthickness=0,
                             bg=T("field"), fg=T("field_text"), font=ui_font(8),
                             selectbackground=T("sel"),
                             selectforeground=T("sel_text"))
        listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = self._scrollbar(inner, listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        listbox.configure(yscrollcommand=scrollbar.set)
        return wrap, listbox

    def _show_page(self, key):
        self.pages[key].tkraise()
        self.tabs.select(key)

    # ---- página Descargar ----

    def _build_download_page(self):
        page = self.pages["download"]
        page.columnconfigure(0, weight=1)

        # --- dirección ---
        link = RetroGroup(page, "Dirección")
        link.grid(row=0, column=0, sticky="ew", pady=(S(4), 0))
        body = link.body
        body.columnconfigure(0, weight=1)

        self.var_url = tk.StringVar()
        wrap, entry = retro_entry(body, self.var_url)
        wrap.grid(row=0, column=0, sticky="ew", ipady=S(4))
        entry.bind("<Return>", lambda _e: self.start_download())
        RetroButton(body, "Pegar", command=self._paste,
                    width=76).grid(row=0, column=1, padx=(S(6), 0))

        self.var_site = tk.StringVar(
            value="Funciona con YouTube, TikTok, Reddit, X, Instagram, Twitch, "
                  "Vimeo, SoundCloud y muchos más.")
        self.lbl_site = retro_label(body, "", wraplength=S(700))
        self.lbl_site.configure(textvariable=self.var_site)
        self.lbl_site.grid(row=1, column=0, columnspan=2, sticky="w",
                           pady=(S(6), 0))
        self.var_url.trace_add("write", lambda *_: self._on_url_change())

        # --- qué descargar ---
        what = RetroGroup(page, "Qué quieres obtener")
        what.grid(row=1, column=0, sticky="ew", pady=(S(10), 0))
        body = what.body
        body.columnconfigure(3, weight=1)

        self.var_mode = tk.StringVar(value=self.config_data.get("mode", "video"))
        RetroCheck(body, "Vídeo + audio", self.var_mode, radio=True,
                   value="video", command=self._on_mode_change).grid(
            row=0, column=0, sticky="w")
        RetroCheck(body, "Solo audio", self.var_mode, radio=True, value="audio",
                   command=self._on_mode_change).grid(row=0, column=1,
                                                      sticky="w", padx=(S(20), 0))

        self.var_vformat = tk.StringVar(
            value=self.config_data.get("video_format", "MP4"))
        self.var_aformat = tk.StringVar(
            value=self.config_data.get("audio_format", "MP3"))

        retro_label(body, "Formato:").grid(row=1, column=0, sticky="w",
                                           pady=(S(12), 0))
        self.cmb_format = RetroCombo(body, width=10)
        self.cmb_format.grid(row=1, column=1, sticky="w", padx=(S(6), S(20)),
                             pady=(S(12), 0))
        self.cmb_format.bind("<<ComboboxSelected>>", self._on_format_change)

        retro_label(body, "Calidad:").grid(row=1, column=2, sticky="w",
                                           pady=(S(12), 0))
        self.cmb_quality = RetroCombo(body, width=20)
        self.cmb_quality.grid(row=1, column=3, sticky="w", padx=(S(6), 0),
                              pady=(S(12), 0))

        self.var_playlist = tk.BooleanVar(value=False)
        self.var_subs = tk.BooleanVar(value=False)
        self.var_extras = tk.BooleanVar(value=True)
        RetroCheck(body, "Descargar la lista completa", self.var_playlist).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(S(12), 0))
        self.chk_subs = RetroCheck(body, "Subtítulos (es/en)", self.var_subs)
        self.chk_subs.grid(row=2, column=2, columnspan=2, sticky="w",
                           pady=(S(12), 0))
        RetroCheck(body, "Incluir carátula y metadatos", self.var_extras).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(S(4), 0))

        # --- cookies ---
        cookies = RetroGroup(page, "Sesión del navegador")
        cookies.grid(row=2, column=0, sticky="ew", pady=(S(10), 0))
        body = cookies.body
        body.columnconfigure(2, weight=1)
        retro_label(body, "Cookies de:").grid(row=0, column=0, sticky="w")
        self.cmb_cookies = RetroCombo(body, values=BROWSERS, width=16)
        self.cmb_cookies.set(self.config_data.get("cookies", COOKIES_AUTO_OPTION))
        self.cmb_cookies.grid(row=0, column=1, sticky="w", padx=(S(6), S(14)))
        self.cmb_cookies.bind("<<ComboboxSelected>>", self._on_cookies_change)
        self.var_cookies_hint = tk.StringVar()
        pista = retro_label(body, "", wraplength=S(500))
        pista.configure(textvariable=self.var_cookies_hint)
        pista.grid(row=0, column=2, sticky="w")
        self._update_cookies_hint()

        # --- destino ---
        dest = RetroGroup(page, "Guardar en")
        dest.grid(row=3, column=0, sticky="ew", pady=(S(10), 0))
        body = dest.body
        body.columnconfigure(0, weight=1)
        self.var_outdir = tk.StringVar(
            value=self.config_data.get("outdir") or default_download_dir())
        wrap, _ = retro_entry(body, self.var_outdir)
        wrap.grid(row=0, column=0, sticky="ew", ipady=S(4))
        RetroButton(body, "Examinar...", command=self._pick_outdir,
                    width=88).grid(row=0, column=1, padx=(S(6), 0))
        RetroButton(body, "Abrir", command=self._open_outdir,
                    width=64).grid(row=0, column=2, padx=(S(4), 0))

        # --- cola ---
        queue_group = RetroGroup(page, "Cola de descargas")
        queue_group.grid(row=4, column=0, sticky="nsew", pady=(S(10), 0))
        page.rowconfigure(4, weight=1)
        body = queue_group.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        list_wrap, self.lst_queue = self._sunken_list(body, height=4)
        list_wrap.grid(row=0, column=0, columnspan=2, sticky="nsew")

        buttons = tk.Frame(body, bg=T("face"))
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(S(6), 0))
        RetroButton(buttons, "Añadir a la cola", command=self._queue_add,
                    width=124).pack(side="left", padx=(0, S(5)))
        RetroButton(buttons, "Quitar", command=self._queue_remove,
                    width=92).pack(side="left", padx=(0, S(5)))
        RetroButton(buttons, "Vaciar", command=self._queue_clear,
                    width=92).pack(side="left")

        # --- acción ---
        action = tk.Frame(page, bg=T("face"))
        action.grid(row=5, column=0, sticky="ew", pady=(S(14), S(4)))
        action.columnconfigure(0, weight=1)
        retro_label(action, wraplength=S(560),
                    text=("Descarga únicamente contenido propio, con licencia "
                          "libre o para el que tengas permiso del titular de "
                          "los derechos.")).grid(row=0, column=0, sticky="w")
        self.btn_download = RetroButton(action, "Descargar",
                                        command=self.start_download,
                                        width=120, height=28, default=True)
        self.btn_download.grid(row=0, column=1, sticky="e")

        self._on_mode_change()

    def _on_cookies_change(self, _event=None):
        """Si se elige el archivo de cookies, se pide en ese momento."""
        if self.cmb_cookies.get() == COOKIES_FILE_OPTION:
            ruta = filedialog.askopenfilename(
                title="Elige el archivo de cookies exportado",
                filetypes=[("Archivos de cookies", "*.txt"),
                           ("Todos los archivos", "*.*")],
                initialfile=self.cookies_file or "cookies.txt")
            if ruta:
                self.cookies_file = ruta
            elif not self.cookies_file:
                self.cmb_cookies.set("Ninguno")
        self._update_cookies_hint()

    def _update_cookies_hint(self):
        eleccion = self.cmb_cookies.get()
        if eleccion == COOKIES_FILE_OPTION and self.cookies_file:
            self.var_cookies_hint.set("Usando: " +
                                      os.path.basename(self.cookies_file))
        elif eleccion == COOKIES_FILE_OPTION:
            self.var_cookies_hint.set(
                "Exporta tus cookies con una extensión tipo «Get cookies.txt» "
                "y elige el archivo.")
        elif eleccion == COOKIES_AUTO_OPTION:
            detectados = navegadores_disponibles()
            self.var_cookies_hint.set(
                "Se descarga sin cookies (más rápido) y solo si el sitio pide "
                "sesión se prueban: " + (", ".join(detectados) or "ninguno "
                "detectado"))
        elif eleccion == "Ninguno":
            self.var_cookies_hint.set(
                "Solo si un vídeo falla por edad, acceso privado o «confirma "
                "que no eres un robot».")
        else:
            self.var_cookies_hint.set(
                f"Cierra {eleccion} antes de descargar. Si da error al leer las "
                f"cookies, usa «{COOKIES_FILE_OPTION}».")

    def _on_mode_change(self):
        if self.var_mode.get() == "audio":
            self.cmb_format["values"] = AUDIO_FORMATS
            self.cmb_format.set(self.var_aformat.get() if
                                self.var_aformat.get() in AUDIO_FORMATS else "MP3")
            self.chk_subs.set_enabled(False)
        else:
            self.cmb_format["values"] = VIDEO_FORMATS
            self.cmb_format.set(self.var_vformat.get() if
                                self.var_vformat.get() in VIDEO_FORMATS else "MP4")
            self.chk_subs.set_enabled(True)
        self._on_format_change()

    def _on_format_change(self, _event=None):
        fmt = self.cmb_format.get()
        if self.var_mode.get() == "audio":
            self.var_aformat.set(fmt)
            if fmt in ("WAV", "FLAC"):
                self.cmb_quality["values"] = PCM_QUALITIES
                self.cmb_quality.set("16 bit / 44.1 kHz (CD)")
            else:
                self.cmb_quality["values"] = LOSSY_BITRATES
                self.cmb_quality.set("320 kbps")
        else:
            self.var_vformat.set(fmt)
            self.cmb_quality["values"] = [label for label, _ in VIDEO_RESOLUTIONS]
            self.cmb_quality.set("1080p (Full HD)")

    def _open_outdir(self):
        self._open_folder(self.var_outdir.get())

    def _open_folder(self, path):
        if not os.path.isdir(path):
            return
        try:
            if IS_WINDOWS:
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    # ---- página Convertir ----

    def _build_convert_page(self):
        page = self.pages["convert"]
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        files_group = RetroGroup(page, "Archivos a convertir")
        files_group.grid(row=0, column=0, sticky="nsew", pady=(S(4), 0))
        body = files_group.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        retro_label(body, "Arrastra archivos aquí, o púlsa «Añadir…». Se "
                          "convierten uno tras otro."
                    ).grid(row=0, column=0, columnspan=2, sticky="w",
                           pady=(0, S(6)))

        list_wrap, self.lst_files = self._sunken_list(body, height=8)
        list_wrap.grid(row=1, column=0, sticky="nsew")

        btns = tk.Frame(body, bg=T("face"))
        btns.grid(row=1, column=1, sticky="n", padx=(S(8), 0))
        RetroButton(btns, "Añadir...", command=self._add_files,
                    width=92).pack(pady=(0, S(5)))
        RetroButton(btns, "Quitar", command=self._remove_files,
                    width=92).pack(pady=(0, S(5)))
        RetroButton(btns, "Vaciar", command=self._clear_files,
                    width=92).pack()

        out = RetroGroup(page, "Salida")
        out.grid(row=1, column=0, sticky="ew", pady=(S(10), 0))
        body = out.body
        body.columnconfigure(3, weight=1)

        retro_label(body, "Convertir a:").grid(row=0, column=0, sticky="w")
        self.cmb_target = RetroCombo(body, values=CONVERT_TARGETS, width=10)
        self.cmb_target.set("MP3")
        self.cmb_target.grid(row=0, column=1, sticky="w", padx=(S(6), S(20)))
        self.cmb_target.bind("<<ComboboxSelected>>", self._on_target_change)

        retro_label(body, "Calidad:").grid(row=0, column=2, sticky="w")
        self.cmb_cquality = RetroCombo(body, width=22)
        self.cmb_cquality.grid(row=0, column=3, sticky="w", padx=(S(6), 0))

        self.lbl_res = retro_label(body, "Resolución:")
        self.lbl_res.grid(row=1, column=0, sticky="w", pady=(S(8), 0))
        self.cmb_cres = RetroCombo(
            body, values=[label for label, _ in VIDEO_RESOLUTIONS], width=18)
        self.cmb_cres.set("Mejor disponible")
        self.cmb_cres.grid(row=1, column=1, columnspan=3, sticky="w",
                           padx=(S(6), 0), pady=(S(8), 0))

        self.var_same_dir = tk.BooleanVar(value=True)
        RetroCheck(body, "Guardar junto al archivo original", self.var_same_dir,
                   command=self._toggle_conv_dir).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(S(10), 0))

        dest = tk.Frame(body, bg=T("face"))
        dest.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(S(6), 0))
        dest.columnconfigure(0, weight=1)
        self.var_conv_outdir = tk.StringVar(
            value=self.config_data.get("conv_outdir") or default_download_dir())
        self.frm_conv_dir, self.ent_conv_dir = retro_entry(
            dest, self.var_conv_outdir, state="disabled")
        self.frm_conv_dir.grid(row=0, column=0, sticky="ew", ipady=S(4))
        self.btn_conv_dir = RetroButton(dest, "Examinar...",
                                        command=self._pick_conv_outdir,
                                        width=88)
        self.btn_conv_dir.grid(row=0, column=1, padx=(S(6), 0))
        self.btn_conv_dir.configure(state="disabled")

        action = tk.Frame(page, bg=T("face"))
        action.grid(row=2, column=0, sticky="e", pady=(S(14), S(4)))
        self.btn_convert = RetroButton(action, "Convertir",
                                       command=self.start_convert,
                                       width=120, height=28, default=True)
        self.btn_convert.pack(side="right")

        self._on_target_change()

    def _on_target_change(self, _event=None):
        target = self.cmb_target.get()
        if target in AUDIO_FORMATS:
            if target in ("WAV", "FLAC"):
                self.cmb_cquality["values"] = PCM_QUALITIES
                self.cmb_cquality.set("16 bit / 44.1 kHz (CD)")
            else:
                self.cmb_cquality["values"] = LOSSY_BITRATES
                self.cmb_cquality.set("320 kbps")
            self.cmb_cres.state(["disabled"])
        elif target == "GIF":
            self.cmb_cquality["values"] = ["12 fps / 480 px", "15 fps / 640 px",
                                           "10 fps / 320 px"]
            self.cmb_cquality.set("12 fps / 480 px")
            self.cmb_cres.state(["disabled"])
        else:
            self.cmb_cquality["values"] = VIDEO_QUALITY_LIST
            self.cmb_cquality.set("Media (recomendada)")
            self.cmb_cres.state(["!disabled"])

    def _toggle_conv_dir(self):
        enabled = not self.var_same_dir.get()
        self.ent_conv_dir.configure(state="normal" if enabled else "disabled")
        self.btn_conv_dir.configure(state="normal" if enabled else "disabled")

    # ---- cola de conversión ----

    def _add_files(self, paths=None):
        if paths is None:
            paths = filedialog.askopenfilenames(
                title="Selecciona archivos de vídeo o audio",
                filetypes=[
                    ("Archivos multimedia", " ".join("*" + e for e in MEDIA_EXTS)),
                    ("Todos los archivos", "*.*"),
                ],
            )
        existing = {item["path"] for item in self.convert_items}
        added = 0
        for path in paths:
            path = os.path.normpath(path)
            if path in existing or not os.path.isfile(path):
                continue
            self.convert_items.append({"path": path, "estado": "En espera"})
            existing.add(path)
            added += 1
        if added:
            self._refresh_convert_list()
        return added

    def _remove_files(self):
        for index in reversed(self.lst_files.curselection()):
            del self.convert_items[index]
        self._refresh_convert_list()

    def _clear_files(self):
        self.convert_items = []
        self._refresh_convert_list()

    def _refresh_convert_list(self):
        selection = self.lst_files.curselection()
        self.lst_files.delete(0, "end")
        for item in self.convert_items:
            self.lst_files.insert("end", f'[{item["estado"]}]  {item["path"]}')
        for index in selection:
            if index < len(self.convert_items):
                self.lst_files.selection_set(index)

    # ---- cola de descargas ----

    def _queue_add(self, url=None):
        """Guarda el enlace con las opciones tal y como están ahora."""
        url = (url or self.var_url.get()).strip()
        if not url:
            messagebox.showwarning(
                "Falta el enlace",
                "Pega la dirección del vídeo que quieres añadir a la cola.")
            return False
        opts = self._current_download_opts(url)
        if opts is None:
            return False
        self.download_queue.append({"url": url, "opts": opts,
                                    "estado": "En espera"})
        self.var_url.set("")
        self._refresh_queue_list()
        return True

    def _queue_remove(self):
        for index in reversed(self.lst_queue.curselection()):
            del self.download_queue[index]
        self._refresh_queue_list()

    def _queue_clear(self):
        self.download_queue = []
        self._refresh_queue_list()

    def _refresh_queue_list(self):
        self.lst_queue.delete(0, "end")
        for item in self.download_queue:
            etiqueta = item["opts"]["format"]
            self.lst_queue.insert(
                "end", f'[{item["estado"]}]  ({etiqueta})  {item["url"]}')

    def _current_download_opts(self, url):
        """Opciones de descarga actuales; None si la carpeta no sirve."""
        outdir = self.var_outdir.get().strip()
        if not os.path.isdir(outdir):
            try:
                os.makedirs(outdir, exist_ok=True)
            except Exception:
                messagebox.showerror("Carpeta no válida",
                                     "No se puede escribir en la carpeta elegida.")
                return None
        quality = self.cmb_quality.get()
        height = None
        for label, value in VIDEO_RESOLUTIONS:
            if label == quality:
                height = value
        return {
            "mode": self.var_mode.get(),
            "format": self.cmb_format.get(),
            "quality": quality,
            "height": height,
            "outdir": outdir,
            "playlist": self.var_playlist.get(),
            "subs": self.var_subs.get(),
            "extras": self.var_extras.get(),
            "ffmpeg": self.ffmpeg,
            "cookies": self.cmb_cookies.get(),
            "cookies_file": (self.cookies_file
                             if self.cmb_cookies.get() == COOKIES_FILE_OPTION
                             else None),
            "site": detect_site(url),
        }

    def _pick_conv_outdir(self):
        folder = filedialog.askdirectory(initialdir=self.var_conv_outdir.get())
        if folder:
            self.var_conv_outdir.set(folder)

    # ---- página Opciones ----

    def _build_settings_page(self):
        page = self.pages["settings"]
        page.columnconfigure(0, weight=1)

        tools = RetroGroup(page, "Componentes necesarios")
        tools.grid(row=0, column=0, sticky="ew", pady=(S(4), 0))
        body = tools.body
        body.columnconfigure(0, weight=1)

        retro_label(body, wraplength=S(700),
                    text=("yt-dlp se encarga de la descarga y FFmpeg de unir "
                          "pistas y convertir formatos. Ambos son gratuitos y "
                          "de código abierto.")).grid(row=0, column=0,
                                                      columnspan=2, sticky="w",
                                                      pady=(0, S(10)))

        self.lbl_ytdlp = self._tool_row(body, 1, "yt-dlp",
                                        "Instalar / Actualizar",
                                        self.install_ytdlp)
        self.lbl_ffmpeg, self.btn_ffmpeg = self._tool_row(
            body, 2, "FFmpeg", "Descargar automáticamente", self.install_ffmpeg,
            return_button=True)

        RetroButton(body, "Comprobar de nuevo", command=self._refresh_tools,
                    width=136).grid(row=3, column=0, sticky="w", pady=(S(10), 0))

        cookies = RetroGroup(page, "Sesión (para vídeos que piden iniciar sesión)")
        cookies.grid(row=1, column=0, sticky="ew", pady=(S(10), 0))
        body = cookies.body
        body.columnconfigure(0, weight=1)
        retro_label(body, wraplength=S(700),
                    text=("Algunos vídeos (por ejemplo ciertos TikToks o "
                          "contenido privado) solo se descargan con la sesión "
                          "de un navegador. Firefox es el único del que se "
                          "pueden leer las cookies de forma fiable; Chrome y "
                          "Edge las cifran.")).grid(row=0, column=0, columnspan=2,
                                                    sticky="w", pady=(0, S(8)))
        self.lbl_firefox, self.btn_firefox = self._tool_row(
            body, 1, "Firefox", "Instalar Firefox", self.install_firefox,
            return_button=True)

        look = RetroGroup(page, "Apariencia")
        look.grid(row=2, column=0, sticky="ew", pady=(S(10), 0))
        body = look.body
        self.var_theme = tk.StringVar(value=self.theme_key)
        for index, key in enumerate(("95", "xp")):
            RetroCheck(body, THEMES[key]["name"], self.var_theme, radio=True,
                       value=key,
                       command=lambda k=key: self._apply_theme(k)).grid(
                row=0, column=index, sticky="w", padx=(0, S(20)))
        retro_label(body, wraplength=S(700),
                    text="Tipografía MS Sans Serif del kit Windows 95 de "
                         "Themesberg (licencia MIT), incluida en la carpeta "
                         "«assets»."
                    ).grid(row=1, column=0, columnspan=2, sticky="w",
                           pady=(S(8), 0))

        manual = RetroGroup(page, "Instalación manual")
        manual.grid(row=3, column=0, sticky="ew", pady=(S(10), 0))
        retro_label(manual.body, wraplength=S(700),
                    text=("yt-dlp:  abre una consola y ejecuta  "
                          "pip install -U yt-dlp\n"
                          "FFmpeg:  descárgalo de ffmpeg.org y añade su carpeta "
                          "«bin» al PATH, o pulsa el botón de arriba y se "
                          "guardará en la carpeta «bin» de esta aplicación.\n\n"
                          "Los archivos descargados y convertidos nunca salen "
                          "de tu equipo.")).pack(anchor="w")

    def _tool_row(self, parent, row, name, button_text, command,
                  return_button=False):
        frame = tk.Frame(parent, bg=T("face"))
        frame.grid(row=row, column=0, sticky="ew", pady=(0, S(6)))
        frame.columnconfigure(1, weight=1)

        retro_label(frame, name + ":", bold=True, width=8).grid(row=0, column=0,
                                                                sticky="w")
        status = retro_label(frame, "Comprobando...")
        status.grid(row=0, column=1, sticky="w", padx=(S(6), S(10)))
        button = RetroButton(frame, button_text, command=command, width=160)
        button.grid(row=0, column=2, sticky="e")
        if return_button:
            return status, button
        return status

    def _refresh_tools(self, initial=False):
        self.ffmpeg = find_ffmpeg()
        self.ffprobe = find_ffprobe()
        self.ytdlp = find_ytdlp()

        if self.ytdlp:
            rc, out = run_capture(self.ytdlp + ["--version"])
            version = out.splitlines()[-1] if rc == 0 and out else "?"
            self.ytdlp_edad = edad_de_version(version)
            texto = f"Instalado  ·  versión {version}"
            if self.ytdlp_edad is not None and self.ytdlp_edad > 45:
                texto += f"  ·  {self.ytdlp_edad} días: conviene actualizar"
            self.lbl_ytdlp.configure(text=texto)
        else:
            self.ytdlp_edad = None
            self.lbl_ytdlp.configure(text="No encontrado — pulsa «Instalar»")

        if self.ffmpeg:
            rc, out = run_capture([self.ffmpeg, "-version"])
            first = out.splitlines()[0] if out else ""
            match = re.search(r"ffmpeg version (\S+)", first)
            version = match.group(1) if match else "?"
            self.lbl_ffmpeg.configure(text=f"Instalado  ·  {version}")
            self.btn_ffmpeg.configure(text="Reinstalar")
        else:
            self.lbl_ffmpeg.configure(text="No encontrado — necesario para "
                                           "convertir")
            self.btn_ffmpeg.configure(text="Descargar automáticamente")

        if hasattr(self, "lbl_firefox"):
            ruta_fx = firefox_instalado()
            if ruta_fx:
                self.lbl_firefox.configure(text="Instalado")
                self.btn_firefox.configure(text="Ya instalado",
                                           state="disabled")
            else:
                self.lbl_firefox.configure(text="No instalado (opcional)")
                self.btn_firefox.configure(text="Instalar Firefox",
                                           state="normal")

        estado = ["yt-dlp: " + ("sí" if self.ytdlp else "no"),
                  "FFmpeg: " + ("sí" if self.ffmpeg else "no")]
        self.status_bar.set(1, "   ".join(estado))

        if initial and (not self.ytdlp or not self.ffmpeg):
            faltan = []
            if not self.ytdlp:
                faltan.append("yt-dlp")
            if not self.ffmpeg:
                faltan.append("FFmpeg")
            self.after(400, lambda: (
                self._show_page("settings"),
                messagebox.showinfo(
                    "Falta " + " y ".join(faltan),
                    "Para que la aplicación funcione hace falta instalar "
                    + " y ".join(faltan) + ".\n\nPuedes hacerlo desde la pestaña "
                    "«Opciones» con los botones de la derecha.")))

    # ---- cambio de piel ----

    def _apply_theme(self, key):
        """Cambia entre Windows 95 y XP reconstruyendo la ventana."""
        if key == self.theme_key:
            return
        snapshot = {
            "url": self.var_url.get(),
            "outdir": self.var_outdir.get(),
            "conv_outdir": self.var_conv_outdir.get(),
            "mode": self.var_mode.get(),
            "cookies": self.cmb_cookies.get(),
            "files": None,
            "log": self.txt_log.get("1.0", "end-1c"),
            "status": self.var_status.get(),
            "page": "settings",
        }
        self.theme_key = key
        self.config_data["theme"] = key
        THEME.clear()
        THEME.update(THEMES[key])
        self._save_config()

        if hasattr(self, "progress"):
            self.progress.stop()
        for child in list(self.winfo_children()):
            child.destroy()

        self._build_ui()
        self.var_url.set(snapshot["url"])
        self.var_outdir.set(snapshot["outdir"])
        self.var_conv_outdir.set(snapshot["conv_outdir"])
        self.var_mode.set(snapshot["mode"])
        self.cmb_cookies.set(snapshot["cookies"])
        self._update_cookies_hint()
        self._refresh_convert_list()
        self._refresh_queue_list()
        if snapshot["log"]:
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", snapshot["log"])
            self.txt_log.configure(state="disabled")
        self.var_status.set(snapshot["status"])
        self._on_mode_change()
        self._refresh_tools()
        self._show_page(snapshot["page"])

    # ---- zona inferior compartida ----

    def _build_bottom(self):
        bottom = tk.Frame(self, bg=T("face"))
        bottom.pack(side="bottom", fill="x", padx=S(3), pady=(0, S(3)))
        bottom.columnconfigure(0, weight=1)

        log_group = RetroGroup(bottom, "Registro")
        log_group.grid(row=0, column=0, sticky="ew", pady=(S(6), S(4)))
        body = log_group.body
        body.columnconfigure(0, weight=1)

        log_wrap = tk.Frame(body, bg=T("face"))
        log_wrap.grid(row=0, column=0, sticky="ew")
        log_canvas = tk.Canvas(log_wrap, bg=T("face"), highlightthickness=0,
                               bd=0)
        log_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        log_wrap.bind("<Configure>", lambda _e: (
            log_canvas.delete("all"),
            bevel(log_canvas, 0, 0, log_wrap.winfo_width(),
                  log_wrap.winfo_height(), "sunken")))

        inner = tk.Frame(log_wrap, bg=T("field"))
        inner.pack(fill="both", expand=True, padx=S(3), pady=S(3))
        inner.columnconfigure(0, weight=1)
        self.txt_log = tk.Text(inner, height=7, wrap="none", bd=0,
                               highlightthickness=0, font=mono_font(8),
                               background=T("field"), foreground=T("field_text"),
                               insertbackground=T("field_text"),
                               selectbackground=T("sel"),
                               selectforeground=T("sel_text"), state="disabled")
        self.txt_log.grid(row=0, column=0, sticky="ew")
        sb = self._scrollbar(inner, self.txt_log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.tag_configure("err", foreground="#a00000")
        self.txt_log.tag_configure("ok", foreground="#006000")
        self.txt_log.tag_configure("info", foreground="#000080")

        buttons = tk.Frame(body, bg=T("face"))
        buttons.grid(row=1, column=0, sticky="e", pady=(S(6), 0))
        RetroButton(buttons, "Limpiar", command=self._clear_log,
                    width=76).pack(side="right")

        bar = tk.Frame(bottom, bg=T("face"))
        bar.grid(row=1, column=0, sticky="ew", pady=(0, S(4)))
        bar.columnconfigure(0, weight=1)
        self.progress = RetroProgress(bar)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.btn_cancel = RetroButton(bar, "Cancelar", command=self.cancel_job,
                                      width=92)
        self.btn_cancel.grid(row=0, column=1, padx=(S(6), 0))
        self.btn_cancel.configure(state="disabled")

        self.status_bar = StatusBar(bottom, weights=(1, 0))
        self.status_bar.grid(row=2, column=0, sticky="ew")
        self.var_status = self.status_bar.variable(0)
        self.var_status.set("Listo.")

    def _clear_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    # ---------------- comunicación con hilos ----------------

    def log(self, text, tag=None):
        self.msg_queue.put(("log", (text, tag)))

    def status(self, text):
        self.msg_queue.put(("status", text))

    def set_progress(self, value):
        self.msg_queue.put(("progress", value))

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    text, tag = payload
                    self.txt_log.configure(state="normal")
                    self.txt_log.insert("end", text + "\n", tag or ())
                    self.txt_log.see("end")
                    self.txt_log.configure(state="disabled")
                elif kind == "status":
                    self.var_status.set(payload)
                elif kind == "progress":
                    if payload is None:
                        self.progress.start_indeterminate()
                    else:
                        self.progress.set_value(payload)
                elif kind == "listas":
                    self._refresh_queue_list()
                    self._refresh_convert_list()
                elif kind == "done":
                    self._refresh_queue_list()
                    self._refresh_convert_list()
                    self._job_finished()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _job_finished(self):
        self.worker = None
        self.proc = None
        self.btn_cancel.configure(state="disabled")
        self.btn_download.configure(state="normal")
        self.btn_convert.configure(state="normal")

    def _start_job(self, target, args=()):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Espera",
                                   "Ya hay una tarea en marcha. Espera a que "
                                   "termine o púlsala en «Cancelar».")
            return False
        self.cancelled = False
        self.btn_cancel.configure(state="normal")
        self.btn_download.configure(state="disabled")
        self.btn_convert.configure(state="disabled")
        self.set_progress(0)
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()
        return True

    def cancel_job(self):
        self.cancelled = True
        self.status("Cancelando…")
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                if IS_WINDOWS:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                                   creationflags=creation_flags(),
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                else:
                    proc.terminate()
            except Exception:
                pass

    def _stream(self, cmd, on_line):
        """Lanza un proceso y entrega cada línea de salida a on_line."""
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags(),
            env=clean_env(),
            bufsize=1,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in self.proc.stdout:
            if self.cancelled:
                break
            on_line(line.rstrip("\r\n"))
        self.proc.stdout.close()
        return self.proc.wait()

    # ---------------- acciones ----------------

    def _paste(self):
        try:
            self.var_url.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def _on_url_change(self):
        """Reconoce el sitio mientras se escribe y avisa de lo que necesita."""
        url = self.var_url.get().strip()
        if not url:
            self.var_site.set(
                "Funciona con YouTube, TikTok, Reddit, X, Instagram, Twitch, "
                "Vimeo, SoundCloud y muchos más.")
            return

        site = detect_site(url)
        if not site:
            self.var_site.set(
                "Sitio no reconocido: se intentará de todos modos. Muchas webs "
                "usan un reproductor estándar que sí funciona.")
            return

        parts = ["Detectado: " + site["name"]]
        if site.get("hint"):
            parts.append(site["hint"])
        if site.get("cookies") == "casi siempre" and \
                self.cmb_cookies.get() == "Ninguno":
            parts.append("Elige tu navegador en «Cookies de:».")
        self.var_site.set("  ·  ".join(parts))

        if site.get("audio") and self.var_mode.get() != "audio":
            self.var_mode.set("audio")
            self._on_mode_change()

    def _pick_outdir(self):
        folder = filedialog.askdirectory(initialdir=self.var_outdir.get())
        if folder:
            self.var_outdir.set(folder)

    # ---- descarga ----

    def start_download(self):
        """Descarga lo que haya en la cola; el enlace escrito se añade primero."""
        if self.var_url.get().strip() and not self._queue_add():
            return
        pendientes = [item for item in self.download_queue
                      if item["estado"] in ("En espera", "Error", "Cancelado")]
        if not pendientes:
            messagebox.showwarning(
                "Cola vacía",
                "Pega un enlace o añade alguno a la cola antes de descargar.")
            return
        if not self.ytdlp:
            self._show_page("settings")
            messagebox.showerror("Falta yt-dlp",
                                 "Instala yt-dlp desde la pestaña «Opciones».")
            return
        for item in pendientes:
            item["estado"] = "En espera"
            item["opts"]["ffmpeg"] = self.ffmpeg
        self._refresh_queue_list()
        self._save_config()
        self._start_job(self._download_queue_worker, (pendientes,))

    def _download_queue_worker(self, items):
        total = len(items)
        hechos = 0
        for index, item in enumerate(items, start=1):
            if self.cancelled:
                break
            prefijo = f"[{index}/{total}] " if total > 1 else ""
            self._set_item_state(item, "Descargando")
            if (item["opts"].get("site") or {}).get("key") == "spotify":
                rc = self._download_spotify(item, prefijo)
            else:
                rc = self._download_one(item["url"], item["opts"], prefijo)
            if self.cancelled:
                self._set_item_state(item, "Cancelado")
                break
            if rc == 0:
                hechos += 1
                self._set_item_state(item, "Hecho")
            else:
                self._set_item_state(item, "Error")

        self.set_progress(100 if hechos == total and not self.cancelled else 0)
        if self.cancelled:
            self.status("Cancelado.")
            self.log("■ Cancelado por el usuario.", "err")
        elif total > 1:
            self.status(f"Cola terminada: {hechos} de {total} descargas.")
            self.log(f"✔ Cola terminada: {hechos} de {total} descargas.",
                     "ok" if hechos == total else "err")
        self.msg_queue.put(("done", None))

    def _download_spotify(self, item, prefijo=""):
        """Descarga las canciones de un enlace de Spotify.

        Spotify entrega su audio cifrado y no se toca: de su página solo se
        leen el título y el artista (lo que ve cualquiera en el reproductor
        web) y cada canción se busca y se descarga de YouTube.
        """
        self.status(f"{prefijo}Leyendo el enlace de Spotify…")
        self.log(f"▶ {prefijo}Spotify: " + item["url"], "info")
        try:
            canciones = spotify_canciones(item["url"])
        except Exception as exc:
            self.log(f"✖ {exc}", "err")
            return 1

        self.log(f"   {len(canciones)} canción(es). Spotify no entrega el "
                 "audio, así que se busca cada una en YouTube y se descarga "
                 "de allí.", "info")

        base = dict(item["opts"])
        base["mode"] = "audio"
        base["playlist"] = False
        base["site"] = None
        if base.get("format") not in AUDIO_FORMATS:
            base["format"] = "MP3"
            base["quality"] = "320 kbps"

        fallos = 0
        for numero, (consulta, nombre) in enumerate(canciones, start=1):
            if self.cancelled:
                break
            opciones = dict(base)
            opciones["nombre_archivo"] = nombre
            etiqueta = f"{prefijo}[{numero}/{len(canciones)}] "
            self.log(f"   ♪ {nombre}", "info")
            rc = self._download_one(f"ytsearch1:{consulta}", opciones, etiqueta)
            if rc != 0:
                fallos += 1

        if self.cancelled:
            return 1
        if fallos:
            self.log(f"✖ {fallos} de {len(canciones)} canciones no se han "
                     "podido descargar.", "err")
            return 1
        self.log(f"✔ {len(canciones)} canción(es) descargadas.", "ok")
        return 0

    def _set_item_state(self, item, estado):
        item["estado"] = estado
        self.msg_queue.put(("listas", None))

    def _download_one(self, url, opts, prefijo=""):
        site = opts.get("site")
        self.log(f"▶ {prefijo}Descargando: " + url, "info")

        # Si se pidió un navegador concreto, se prepara aquí (copiando su base
        # de cookies) para que funcione aunque esté abierto.
        cookie_tmp = None
        eleccion = opts.get("cookies", "Ninguno")
        if (eleccion not in ("Ninguno", COOKIES_AUTO_OPTION, COOKIES_FILE_OPTION)
                and not opts.get("cookies_file")
                and not opts.get("cookies_browser_arg")):
            arg, cookie_tmp = preparar_cookies_navegador(eleccion)
            opts = dict(opts)
            opts["cookies_browser_arg"] = arg
        try:
            return self._download_one_inner(url, opts, prefijo)
        finally:
            if cookie_tmp:
                shutil.rmtree(cookie_tmp, ignore_errors=True)

    def _download_one_inner(self, url, opts, prefijo=""):
        site = opts.get("site")
        if site:
            self.log(f"   Sitio: {site['name']}", "info")
            if site.get("cookies") == "casi siempre" and \
                    opts.get("cookies", "Ninguno") == "Ninguno":
                self.log("   Aviso: este sitio casi siempre pide una sesión "
                         "iniciada. Si falla, elige tu navegador en «Cookies de:».",
                         "err")
        else:
            self.log("   Sitio no reconocido: se intentará con el extractor "
                     "genérico.", "info")
        if not self.ffmpeg:
            self.log("Aviso: FFmpeg no está disponible; puede que no se pueda unir "
                     "vídeo y audio ni convertir el formato.", "err")
        if self.ytdlp_edad is not None and self.ytdlp_edad > 45:
            self.log(f"   Aviso: tu yt-dlp tiene {self.ytdlp_edad} días. Los "
                     "sitios cambian cada pocas semanas; si falla, actualízalo "
                     "en Opciones.", "err")
        self.status(f"{prefijo}Preparando la descarga…")

        pct_re = re.compile(r"\[download\]\s+([\d.]+)%")
        dest_re = re.compile(r"\[download\] Destination: (.+)")
        state = {"blocked": False, "bot": False, "stale": False,
                 "throttled": False, "cookie_cifrada": False,
                 "cookie_bloqueada": False}

        def on_line(line):
            if not line.strip():
                return
            match = pct_re.search(line)
            if match:
                pct = float(match.group(1))
                self.set_progress(pct)
                self.status(line.strip())
                return
            if BOT_RE.search(line):
                state["bot"] = True
            if STALE_RE.search(line):
                state["stale"] = True
            if THROTTLE_RE.search(line):
                state["throttled"] = True
            if COOKIE_DECRYPT_RE.search(line):
                state["cookie_cifrada"] = True
            if COOKIE_LOCKED_RE.search(line):
                state["cookie_bloqueada"] = True
            if BLOCK_RE.search(line):
                state["blocked"] = True
            if dest_re.search(line):
                self.log(line, "info")
            elif line.startswith("[Merger]") or line.startswith("[ExtractAudio]") \
                    or line.startswith("[VideoConvertor]"):
                self.status("Procesando con FFmpeg…")
                self.set_progress(None)
                self.log(line, "info")
            elif "ERROR" in line or "error" in line.lower():
                self.log(line, "err")
            else:
                self.log(line)

        try:
            antes = set(os.listdir(opts["outdir"]))
        except OSError:
            antes = None

        rc = 1
        attempts = attempts_for(site)
        total = len(attempts)
        for index, attempt in enumerate(attempts):
            if self.cancelled:
                break
            state["blocked"] = False
            attempt_opts = dict(opts)
            attempt_opts["client"] = attempt["client"]
            attempt_opts["force_ipv4"] = attempt.get("force_ipv4", False)

            if index:
                self.log(f"↻ Reintento {index} de {total - 1}: pidiendo el vídeo "
                         f"como cliente «{attempt['label']}»…", "info")
                self.status(f"Reintentando ({index + 1} de {total})…")
                self.set_progress(0)

            cmd = self.ytdlp + build_ytdlp_args(url, attempt_opts)
            try:
                rc = self._stream(cmd, on_line)
            except Exception as exc:
                self.log(f"Error al ejecutar yt-dlp: {exc}", "err")
                rc = 1

            if rc == 0 or self.cancelled or not state["blocked"]:
                break

        # Si el sitio ha pedido sesión y las cookies están en automático, se
        # prueba con cada navegador instalado. Se hace solo aquí, al final,
        # para no ralentizar las descargas que no lo necesitan.
        if (rc != 0 and not self.cancelled and state["bot"]
                and opts.get("cookies") == COOKIES_AUTO_OPTION):
            for navegador in navegadores_disponibles():
                if self.cancelled:
                    break
                self.log(f"↻ {prefijo}El sitio pide sesión: probando con las "
                         f"cookies de {navegador}…", "info")
                self.status(f"{prefijo}Probando la sesión de {navegador}…")
                self.set_progress(0)
                arg, tmp = preparar_cookies_navegador(navegador)
                con_cookies = dict(opts)
                con_cookies["cookies"] = navegador
                con_cookies["cookies_browser_arg"] = arg
                con_cookies["client"] = None
                con_cookies["force_ipv4"] = False
                state["cookie_cifrada"] = False
                try:
                    rc = self._stream(self.ytdlp +
                                      build_ytdlp_args(url, con_cookies), on_line)
                except Exception as exc:
                    self.log(f"Error al ejecutar yt-dlp: {exc}", "err")
                    rc = 1
                finally:
                    if tmp:
                        shutil.rmtree(tmp, ignore_errors=True)
                if rc == 0:
                    self.log(f"   Funcionó con {navegador}.", "ok")
                    break
                if state["cookie_cifrada"]:
                    self.log(f"   {navegador} cifra sus cookies y no se pueden "
                             "leer (normal en Chrome y Edge modernos).", "err")

        self.set_progress(100 if rc == 0 else 0)
        if rc != 0 and antes is not None:
            self._limpiar_restos(opts["outdir"], antes)
        if self.cancelled:
            pass
        elif rc == 0:
            self.status(f"{prefijo}Descarga completada.")
            self.log("✔ Listo. Archivos en: " + opts["outdir"], "ok")
        else:
            self.status(f"{prefijo}La descarga ha fallado.")
            self.log(f"✖ yt-dlp terminó con el código {rc}.", "err")
            self._explain_failure(state, opts)
        return rc

    RESTOS_TEMPORALES = (".part", ".ytdl", ".temp", ".partial")
    RESTOS_IMAGEN = (".webp", ".jpg", ".jpeg", ".png")

    def _limpiar_restos(self, outdir, antes):
        """Borra lo que deja a medias una descarga fallida.

        Una miniatura solo se borra si no existe el vídeo o el audio al que
        acompañaba: así nunca se toca un archivo bueno de descargas anteriores.
        """
        try:
            ahora = set(os.listdir(outdir))
        except OSError:
            return
        borrados = 0
        for nombre in ahora - antes:
            ruta = os.path.join(outdir, nombre)
            if not os.path.isfile(ruta):
                continue
            base, extension = os.path.splitext(nombre)
            extension = extension.lower()
            if extension in self.RESTOS_TEMPORALES:
                objetivo = True
            elif extension in self.RESTOS_IMAGEN:
                objetivo = not any(
                    os.path.isfile(os.path.join(outdir, base + media))
                    for media in MEDIA_EXTS)
            else:
                objetivo = False
            if objetivo:
                try:
                    os.remove(ruta)
                    borrados += 1
                except OSError:
                    pass
        if borrados:
            self.log(f"   (limpiados {borrados} archivo(s) sueltos que dejó el "
                     f"intento fallido)", "info")

    def _explain_failure(self, state, opts):
        """Explica el fallo concreto, no una lista genérica."""
        self.log("", None)
        sitio = (opts.get("site") or {}).get("name", "este sitio")

        if state.get("cookie_cifrada"):
            self.log("→ Chrome y Edge cifran sus cookies (cifrado «app-bound») "
                     "y ni yt-dlp ni este programa pueden leerlas.", "err")
            self.log("   Lo que SÍ funciona:", "info")
            self.log("   • Firefox: inicia sesión ahí, ciérralo y elige "
                     "«Firefox» en «Cookies de:».", "info")
            self.log(f"   • O exporta un archivo de cookies con una extensión "
                     f"tipo «Get cookies.txt» y elige «{COOKIES_FILE_OPTION}».",
                     "info")
            return

        if state.get("cookie_bloqueada"):
            self.log(f"→ No se han podido leer las cookies de tu navegador. "
                     "Ciérralo del todo y reintenta.", "err")
            return

        if state.get("bot"):
            self.log(f"→ {sitio} exige una sesión iniciada para este vídeo.",
                     "err")
            self.log("   • Con Firefox: inicia sesión, ciérralo y elígelo en "
                     "«Cookies de:».", "info")
            self.log("   • Con Chrome/Edge no se puede (cifran las cookies): "
                     f"usa «{COOKIES_FILE_OPTION}» con un archivo exportado.",
                     "info")
            return

        if state.get("throttled"):
            self.log("→ El servidor ha cortado la descarga por exceso de "
                     "peticiones (403/429). No es un fallo del programa.", "err")
            self.log("   Espera unos minutos y reintenta; si descargas en cola, "
                     "hazlo en tandas más cortas.", "info")
            self.log("   Bajar la calidad a 720p también ayuda: las pistas de "
                     "1080p y 4K son las más vigiladas.", "info")
            return

        if state.get("stale"):
            self.log("→ El sitio ha cambiado y esta versión de yt-dlp ya no lo "
                     "entiende.", "err")
            self.log("   Ve a Opciones → «Instalar / Actualizar» en yt-dlp. Es "
                     "la solución en la mayoría de los casos.", "info")
            if self.ytdlp_edad is not None:
                self.log(f"   Tu yt-dlp tiene {self.ytdlp_edad} días.", "info")
            return

        self.log("Qué probar, en este orden:", "info")
        self.log("  1. Opciones → «Instalar / Actualizar» en yt-dlp.", "info")
        if opts.get("cookies", "Ninguno") == "Ninguno":
            self.log("  2. Elige tu navegador en «Cookies de:» (ciérralo antes).",
                     "info")
        else:
            self.log("  2. Prueba con otro navegador en «Cookies de:», o vuelve "
                     "a poner «Ninguno».", "info")
        self.log("  3. Baja la calidad a 720p.", "info")
        self.log("  4. Espera unos minutos y reintenta.", "info")

    # ---- conversión ----

    def start_convert(self):
        files = [item["path"] for item in self.convert_items]
        if not files:
            messagebox.showwarning("Sin archivos",
                                   "Añade al menos un archivo para convertir.")
            return
        if not self.ffmpeg:
            self._show_page("settings")
            messagebox.showerror("Falta FFmpeg",
                                 "Instala FFmpeg desde la pestaña «Opciones».")
            return

        target = self.cmb_target.get()
        quality = self.cmb_cquality.get()
        height = None
        for label, value in VIDEO_RESOLUTIONS:
            if label == self.cmb_cres.get():
                height = value

        opts = {"audio_quality": quality, "video_quality": quality, "height": height}
        if target == "GIF":
            fps, _, width = quality.replace(" px", "").partition(" fps / ")
            opts["gif_fps"] = int(fps.strip() or 12)
            opts["gif_width"] = int(width.strip() or 480)

        outdir = None if self.var_same_dir.get() else self.var_conv_outdir.get()
        if outdir and not os.path.isdir(outdir):
            messagebox.showerror("Carpeta no válida",
                                 "Elige una carpeta de salida existente.")
            return
        for item in self.convert_items:
            item["estado"] = "En espera"
        self._refresh_convert_list()
        self._save_config()
        self._start_job(self._convert_worker,
                        (list(self.convert_items), target, opts, outdir))

    def _convert_worker(self, items, target, opts, outdir):
        total = len(items)
        ok_count = 0
        ext = target.lower()

        for index, item in enumerate(items, start=1):
            src = item["path"]
            if self.cancelled:
                break
            if not os.path.isfile(src):
                self._set_item_state(item, "No existe")
                self.log(f"✖ No existe: {src}", "err")
                continue
            self._set_item_state(item, "Convirtiendo")

            folder = outdir or os.path.dirname(src)
            name = os.path.splitext(os.path.basename(src))[0]
            dst = unique_path(os.path.join(folder, f"{name}.{ext}"))
            if os.path.abspath(dst) == os.path.abspath(src):
                dst = unique_path(os.path.join(folder, f"{name}_convertido.{ext}"))

            duration = probe_duration(self.ffprobe, src)
            self.log(f"▶ [{index}/{total}] {os.path.basename(src)}  →  "
                     f"{os.path.basename(dst)}", "info")
            self.status(f"Convirtiendo {index} de {total}…")
            self.set_progress(0 if duration else None)

            cmd = [self.ffmpeg] + build_ffmpeg_args(src, dst, target, opts)
            errors = []

            def on_line(line):
                if line.startswith("out_time_us=") and duration:
                    try:
                        secs = int(line.split("=", 1)[1]) / 1_000_000
                        self.set_progress(secs / duration * 100)
                        self.status(f"Convirtiendo {index} de {total}  ·  "
                                    f"{human_time(secs)} / {human_time(duration)}")
                    except Exception:
                        pass
                elif "=" not in line and line.strip():
                    errors.append(line)
                    self.log(line, "err")

            try:
                rc = self._stream(cmd, on_line)
            except Exception as exc:
                self.log(f"Error al ejecutar FFmpeg: {exc}", "err")
                rc = 1

            if self.cancelled:
                if os.path.isfile(dst):
                    try:
                        os.remove(dst)
                    except Exception:
                        pass
                self._set_item_state(item, "Cancelado")
                break
            if rc == 0 and os.path.isfile(dst):
                ok_count += 1
                self.set_progress(100)
                self._set_item_state(item, "Hecho")
                self.log(f"✔ Guardado en {dst}", "ok")
            else:
                self._set_item_state(item, "Error")
                self.log(f"✖ Falló la conversión de {os.path.basename(src)} "
                         f"(código {rc}).", "err")

        if self.cancelled:
            self.status("Cancelado.")
            self.log("■ Cancelado por el usuario.", "err")
        else:
            self.status(f"Conversión terminada: {ok_count} de {total} archivos.")
            self.log(f"✔ {ok_count} de {total} archivos convertidos.", "ok")
        self.msg_queue.put(("done", None))

    # ---- instalación de componentes ----

    def install_ytdlp(self):
        self._start_job(self._install_ytdlp_worker)

    def _install_ytdlp_worker(self):
        self.status("Instalando yt-dlp…")
        self.set_progress(None)
        if FROZEN:
            rc = self._download_ytdlp_exe()
        else:
            self.log("▶ pip install -U yt-dlp", "info")
            cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
            try:
                rc = self._stream(cmd, lambda line: self.log(line))
            except Exception as exc:
                self.log(str(exc), "err")
                rc = 1
        if rc != 0:
            self.log("✖ No se pudo instalar. Prueba a ejecutar en una consola:  "
                     "pip install -U yt-dlp", "err")
            self.status("La instalación ha fallado.")
        else:
            self.log("✔ yt-dlp instalado o actualizado.", "ok")
            self.status("yt-dlp listo.")
        self.set_progress(0)
        self.msg_queue.put(("done", None))
        self.after(200, self._refresh_tools)

    def _download_ytdlp_exe(self):
        """En la versión compilada no hay pip: se baja el yt-dlp.exe oficial."""
        os.makedirs(BIN_DIR, exist_ok=True)
        destination = os.path.join(BIN_DIR, "yt-dlp.exe")
        temporary = destination + ".parcial"
        self.log("▶ " + YTDLP_EXE_URL, "info")

        def hook(blocks, block_size, total):
            if self.cancelled:
                raise RuntimeError("cancelado")
            if total > 0:
                self.set_progress(blocks * block_size / total * 100)

        try:
            urllib.request.urlretrieve(YTDLP_EXE_URL, temporary, hook)
            os.replace(temporary, destination)
            self.log("✔ yt-dlp guardado en " + BIN_DIR, "ok")
            return 0
        except Exception as exc:
            self.log(f"✖ No se pudo descargar yt-dlp: {exc}", "err")
            if os.path.isfile(temporary):
                try:
                    os.remove(temporary)
                except Exception:
                    pass
            return 1

    def install_firefox(self):
        if firefox_instalado():
            messagebox.showinfo("Firefox", "Firefox ya está instalado.")
            self._refresh_tools()
            return
        if not IS_WINDOWS:
            self._abrir_web(FIREFOX_URL)
            return
        if not winget_disponible():
            if messagebox.askyesno(
                    "Instalar Firefox",
                    "Este equipo no tiene el instalador automático (winget) de "
                    "Windows.\n\n¿Abro la página de descarga de Firefox para "
                    "instalarlo a mano?"):
                self._abrir_web(FIREFOX_URL)
            return
        if not messagebox.askyesno(
                "Instalar Firefox",
                "Se instalará Firefox (unos 60 MB) usando el instalador de "
                "Windows.\n\nSirve para descargar vídeos que piden iniciar "
                "sesión. ¿Continuar?"):
            return
        self._start_job(self._install_firefox_worker)

    def _install_firefox_worker(self):
        self.status("Instalando Firefox…")
        self.set_progress(None)
        self.log("▶ winget install Mozilla.Firefox", "info")
        cmd = ["winget", "install", "--id", "Mozilla.Firefox", "--silent",
               "--accept-source-agreements", "--accept-package-agreements"]
        try:
            rc = self._stream(cmd, lambda line: self.log(line)
                              if line.strip() else None)
        except Exception as exc:
            self.log(str(exc), "err")
            rc = 1
        if firefox_instalado():
            self.log("✔ Firefox instalado. Inicia sesión en la web que quieras, "
                     "ciérralo, y deja «Cookies de:» en «Automático».", "ok")
            self.status("Firefox instalado.")
        else:
            self.log("✖ No se pudo instalar Firefox automáticamente. Puedes "
                     "descargarlo de mozilla.org.", "err")
            self.status("La instalación de Firefox ha fallado.")
            self._abrir_web(FIREFOX_URL)
        self.set_progress(0)
        self.msg_queue.put(("done", None))
        self.after(200, self._refresh_tools)

    def _abrir_web(self, url):
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    def install_ffmpeg(self):
        if not IS_WINDOWS:
            messagebox.showinfo(
                "Instalación de FFmpeg",
                "En este sistema instala FFmpeg con tu gestor de paquetes:\n\n"
                "  macOS:  brew install ffmpeg\n"
                "  Debian/Ubuntu:  sudo apt install ffmpeg\n"
                "  Fedora:  sudo dnf install ffmpeg\n"
                "  Arch:  sudo pacman -S ffmpeg")
            return
        if not messagebox.askyesno(
                "Descargar FFmpeg",
                "Se descargarán unos 80 MB desde el repositorio oficial de "
                "compilaciones de FFmpeg (BtbN en GitHub) y se guardarán en la "
                "carpeta «bin» de esta aplicación.\n\n¿Continuar?"):
            return
        self._start_job(self._install_ffmpeg_worker)

    def _install_ffmpeg_worker(self):
        os.makedirs(BIN_DIR, exist_ok=True)
        zip_path = os.path.join(BIN_DIR, "_ffmpeg.zip")
        self.status("Descargando FFmpeg…")
        self.log("▶ " + FFMPEG_ZIP_URL, "info")

        def hook(blocks, block_size, total):
            if self.cancelled:
                raise RuntimeError("cancelado")
            if total > 0:
                self.set_progress(blocks * block_size / total * 100)

        try:
            urllib.request.urlretrieve(FFMPEG_ZIP_URL, zip_path, hook)
            self.status("Extrayendo…")
            self.set_progress(None)
            wanted = ("ffmpeg.exe", "ffprobe.exe")
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.namelist():
                    base = os.path.basename(member)
                    if base in wanted:
                        with zf.open(member) as source, \
                                open(os.path.join(BIN_DIR, base), "wb") as target:
                            shutil.copyfileobj(source, target)
                        self.log("  extraído " + base)
            os.remove(zip_path)
            self.log("✔ FFmpeg instalado en " + BIN_DIR, "ok")
            self.status("FFmpeg listo.")
        except Exception as exc:
            self.log(f"✖ No se pudo instalar FFmpeg: {exc}", "err")
            self.log("Alternativa: descárgalo de https://ffmpeg.org/download.html y "
                     "copia ffmpeg.exe y ffprobe.exe en la carpeta 'bin'.", "err")
            self.status("La instalación ha fallado.")
            if os.path.isfile(zip_path):
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
        self.set_progress(0)
        self.msg_queue.put(("done", None))
        self.after(200, self._refresh_tools)

    # ---------------- cierre ----------------

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Salir",
                                       "Hay una tarea en marcha. ¿Cerrar de todos "
                                       "modos?"):
                return
            self.cancel_job()
            time.sleep(0.3)
        self._save_config()
        self.destroy()


def main():
    enable_dpi_awareness()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
