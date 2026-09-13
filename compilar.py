# -*- coding: utf-8 -*-
"""
Compila RISA Downloader y su instalador
=======================================
Uso:   python compilar.py

Deja en la carpeta «publicar»:
  · RISA Downloader/            el programa suelto (por si prefieres el zip)
  · Instalar RISA Downloader.exe   instalador de un solo archivo

Requiere PyInstaller:   pip install -U pyinstaller
"""

import os
import shutil
import subprocess
import sys
import zipfile

RAIZ = os.path.dirname(os.path.abspath(__file__))
CONSTRUCCION = os.path.join(RAIZ, "construccion")
PUBLICAR = os.path.join(RAIZ, "publicar")
ASSETS = os.path.join(RAIZ, "assets")
ICONO = os.path.join(ASSETS, "risa.ico")
SEPARADOR = ";" if os.name == "nt" else ":"

APP_NOMBRE = "RISA Downloader"
INSTALADOR_NOMBRE = "Instalar RISA Downloader"


def paso(texto):
    print("\n=== " + texto + " " + "=" * max(0, 60 - len(texto)))


def ejecutar(argumentos):
    print(">", " ".join(argumentos))
    resultado = subprocess.run(argumentos, cwd=RAIZ)
    if resultado.returncode != 0:
        sys.exit(f"Ha fallado: {' '.join(argumentos)}")


def pyinstaller(*argumentos):
    ejecutar([sys.executable, "-m", "PyInstaller", "--noconfirm",
              "--workpath", os.path.join(CONSTRUCCION, "temp"),
              "--specpath", CONSTRUCCION, *argumentos])


def main():
    if not os.path.isfile(ICONO):
        sys.exit("Falta assets/risa.ico")

    for carpeta in (CONSTRUCCION, PUBLICAR):
        shutil.rmtree(carpeta, ignore_errors=True)
        os.makedirs(carpeta, exist_ok=True)

    # ---- 1. el programa (carpeta, arranca más rápido que en un solo archivo)
    paso("Compilando el programa")
    salida_app = os.path.join(CONSTRUCCION, "app")
    pyinstaller("--windowed", "--onedir", "--name", APP_NOMBRE,
                "--icon", ICONO,
                "--add-data", f"{ASSETS}{SEPARADOR}assets",
                "--distpath", salida_app,
                os.path.join(RAIZ, "descargador.py"))

    carpeta_app = os.path.join(salida_app, APP_NOMBRE)
    if not os.path.isfile(os.path.join(carpeta_app, APP_NOMBRE + ".exe")):
        sys.exit("No se ha generado el ejecutable del programa")

    # ---- 2. empaquetarlo para que el instalador lo lleve dentro
    paso("Empaquetando")
    paquete = os.path.join(CONSTRUCCION, "app.zip")
    with zipfile.ZipFile(paquete, "w", zipfile.ZIP_DEFLATED) as zf:
        for raiz, _dirs, archivos in os.walk(carpeta_app):
            for nombre in archivos:
                completo = os.path.join(raiz, nombre)
                zf.write(completo, os.path.relpath(completo, carpeta_app))
    print(f"  app.zip: {os.path.getsize(paquete) / 1e6:.1f} MB")

    # ---- 3. el instalador (un solo archivo, con el programa dentro)
    paso("Compilando el instalador")
    salida_inst = os.path.join(CONSTRUCCION, "instalador")
    pyinstaller("--windowed", "--onefile", "--name", INSTALADOR_NOMBRE,
                "--icon", ICONO,
                "--add-data", f"{ASSETS}{SEPARADOR}assets",
                "--add-data", f"{paquete}{SEPARADOR}.",
                "--distpath", salida_inst,
                os.path.join(RAIZ, "instalador.py"))

    # ---- 4. recoger resultados
    paso("Recogiendo")
    shutil.copytree(carpeta_app, os.path.join(PUBLICAR, APP_NOMBRE))
    shutil.copy2(os.path.join(salida_inst, INSTALADOR_NOMBRE + ".exe"), PUBLICAR)

    instalador = os.path.join(PUBLICAR, INSTALADOR_NOMBRE + ".exe")
    print(f"\nListo:\n  {instalador}  "
          f"({os.path.getsize(instalador) / 1e6:.1f} MB)")
    print(f"  {os.path.join(PUBLICAR, APP_NOMBRE)}  (programa suelto)")


if __name__ == "__main__":
    main()
