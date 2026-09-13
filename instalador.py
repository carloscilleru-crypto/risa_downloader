# -*- coding: utf-8 -*-
"""
Instalador de RISA Downloader
=============================
Un único ejecutable que copia el programa, crea los accesos directos y se
registra en «Aplicaciones instaladas» de Windows para poder desinstalarlo.

Se instala solo para el usuario actual (en %LOCALAPPDATA%), así que no pide
permisos de administrador.

El mismo ejecutable hace de desinstalador cuando se le pasa --desinstalar.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import queue
import winreg
import zipfile

import tkinter as tk
from tkinter import filedialog, messagebox

# La capa visual (biseles, botones, marcos…) es la misma que la del programa.
import descargador as ui
from descargador import (S, T, THEME, THEMES, RetroButton, RetroCheck,
                         RetroGroup, RetroProgress, StatusBar, TitleBar,
                         bevel, enable_dpi_awareness, icon_image, retro_entry,
                         retro_label, ui_font)

APP_NAME = "RISA Downloader"
APP_VERSION = ui.APP_VERSION
PUBLISHER = "Carlos"
EXE_NAME = "RISA Downloader.exe"
UNINSTALLER_NAME = "Desinstalar RISA Downloader.exe"
REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\RISADownloader"

FROZEN = getattr(sys, "frozen", False)
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
PAYLOAD = os.path.join(RES_DIR, "app.zip")


def default_target():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", APP_NAME)


def data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def run_hidden(arguments):
    """Ejecuta un comando sin abrir ventana de consola."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(arguments, creationflags=flags,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def make_shortcut(link_path, target, icon, working_dir, description):
    """Crea un acceso directo mediante WScript.Shell (vía PowerShell)."""
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        "$s.TargetPath = '{target}';"
        "$s.IconLocation = '{icon}';"
        "$s.WorkingDirectory = '{cwd}';"
        "$s.Description = '{desc}';"
        "$s.Save()"
    ).format(link=link_path.replace("'", "''"),
             target=target.replace("'", "''"),
             icon=icon.replace("'", "''"),
             cwd=working_dir.replace("'", "''"),
             desc=description.replace("'", "''"))
    result = run_hidden(["powershell", "-NoProfile", "-NonInteractive",
                         "-ExecutionPolicy", "Bypass", "-Command", script])
    return result.returncode == 0 and os.path.isfile(link_path)


def desktop_dir():
    return os.path.join(os.path.expanduser("~"), "Desktop")


def start_menu_dir():
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")


def register_uninstall(target, size_kb):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
        uninstaller = os.path.join(target, UNINSTALLER_NAME)
        values = {
            "DisplayName": APP_NAME,
            "DisplayVersion": APP_VERSION,
            "Publisher": PUBLISHER,
            "DisplayIcon": os.path.join(target, EXE_NAME),
            "InstallLocation": target,
            "UninstallString": f'"{uninstaller}" --desinstalar',
            "QuietUninstallString": f'"{uninstaller}" --desinstalar --silencioso',
        }
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def unregister_uninstall():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY)
    except FileNotFoundError:
        pass


def installed_location():
    """Dónde está instalado ahora mismo, si lo está."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
            return winreg.QueryValueEx(key, "InstallLocation")[0]
    except OSError:
        return None


# --------------------------------------------------------------------------
# Instalación y desinstalación (sin interfaz)
# --------------------------------------------------------------------------

def esta_en_uso(target):
    """¿Hay una copia del programa abierta bloqueando sus archivos?

    Windows no deja renombrar un ejecutable en marcha: esa es la prueba más
    fiable, y no requiere permisos ni listar procesos.
    """
    exe = os.path.join(target, EXE_NAME)
    if not os.path.isfile(exe):
        return False
    provisional = exe + ".enuso"
    try:
        os.rename(exe, provisional)
        os.rename(provisional, exe)
        return False
    except OSError:
        return True


def cerrar_programa(espera=6.0):
    """Cierra las copias abiertas y espera a que suelten los archivos."""
    run_hidden(["taskkill", "/IM", EXE_NAME, "/F"])
    limite = time.time() + espera
    while time.time() < limite:
        time.sleep(0.4)
        resultado = run_hidden(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}"])
        if EXE_NAME.encode("mbcs", "replace") not in resultado.stdout:
            break
    time.sleep(0.5)

def install(target, desktop=True, start_menu=True, report=lambda text, pct: None,
            cerrar_si_abierto=True):
    report("Preparando…", 5)
    if esta_en_uso(target):
        if not cerrar_si_abierto:
            raise RuntimeError("RISA Downloader está abierto. Ciérralo y vuelve "
                               "a intentarlo.")
        report("Cerrando la copia que está abierta…", 8)
        cerrar_programa()
        if esta_en_uso(target):
            raise RuntimeError("No se ha podido cerrar RISA Downloader. "
                               "Ciérralo a mano y vuelve a intentarlo.")
    os.makedirs(target, exist_ok=True)

    report("Copiando archivos…", 15)
    with zipfile.ZipFile(PAYLOAD) as zf:
        members = zf.infolist()
        for index, member in enumerate(members, start=1):
            # Un archivo puede seguir bloqueado un instante (antivirus, o la
            # limpieza de una desinstalación anterior): se reintenta.
            for intento in range(6):
                try:
                    zf.extract(member, target)
                    break
                except PermissionError:
                    if intento == 5:
                        raise RuntimeError(
                            "No se puede escribir en:\n" + member.filename +
                            "\n\nCierra RISA Downloader si lo tienes abierto "
                            "y vuelve a intentarlo.")
                    time.sleep(0.5)
            if index % 12 == 0 or index == len(members):
                report("Copiando archivos…", 15 + 60 * index / len(members))

    exe_path = os.path.join(target, EXE_NAME)
    if not os.path.isfile(exe_path):
        raise RuntimeError("El paquete no contiene " + EXE_NAME)

    report("Copiando el desinstalador…", 80)
    if FROZEN:
        shutil.copy2(sys.executable, os.path.join(target, UNINSTALLER_NAME))

    icon = os.path.join(target, "assets", "risa.ico")
    if not os.path.isfile(icon):
        icon = exe_path

    if desktop:
        report("Creando el acceso directo del escritorio…", 88)
        make_shortcut(os.path.join(desktop_dir(), APP_NAME + ".lnk"),
                      exe_path, icon, target,
                      "Descargador de vídeo y conversor de formatos")
    if start_menu:
        report("Creando la entrada del menú Inicio…", 93)
        make_shortcut(os.path.join(start_menu_dir(), APP_NAME + ".lnk"),
                      exe_path, icon, target,
                      "Descargador de vídeo y conversor de formatos")

    report("Registrando la aplicación…", 97)
    size_kb = 0
    for root, _dirs, files in os.walk(target):
        for name in files:
            try:
                size_kb += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    register_uninstall(target, max(1, size_kb // 1024))

    report("Instalación terminada.", 100)
    return exe_path


def uninstall(target=None, remove_data=False, report=lambda text, pct: None):
    target = target or installed_location() or default_target()

    if esta_en_uso(target):
        report("Cerrando el programa…", 5)
        cerrar_programa()

    report("Quitando los accesos directos…", 10)
    for folder in (desktop_dir(), start_menu_dir()):
        link = os.path.join(folder, APP_NAME + ".lnk")
        if os.path.isfile(link):
            try:
                os.remove(link)
            except OSError:
                pass

    report("Quitando el registro…", 25)
    unregister_uninstall()

    report("Borrando los archivos…", 45)
    pendientes = []
    if os.path.isdir(target):
        for root, dirs, files in os.walk(target, topdown=False):
            for name in files:
                path = os.path.join(root, name)
                if FROZEN and os.path.normcase(path) == os.path.normcase(sys.executable):
                    pendientes.append(path)   # es el propio desinstalador
                    continue
                try:
                    os.remove(path)
                except OSError:
                    pendientes.append(path)
            for name in dirs:
                try:
                    os.rmdir(os.path.join(root, name))
                except OSError:
                    pass

    if remove_data and os.path.isdir(data_dir()):
        report("Borrando los ajustes…", 75)
        shutil.rmtree(data_dir(), ignore_errors=True)

    if pendientes:
        # El desinstalador no puede borrarse a sí mismo mientras se ejecuta:
        # se encarga un pequeño .bat que espera a que el proceso termine.
        report("Últimos detalles…", 90)
        limpiador = os.path.join(tempfile.gettempdir(), "risa_limpiar.bat")
        with open(limpiador, "w", encoding="mbcs", errors="replace") as fh:
            # Espera a que Windows suelte el ejecutable, lo borra y solo
            # entonces quita la carpeta; con reintentos, sin bucle infinito.
            # Ojo: aquí NO se puede usar «rmdir /s». Si el usuario vuelve a
            # instalar el programa justo después de desinstalarlo, un borrado
            # recursivo se llevaría por delante la instalación nueva. Con «rd»
            # a secas la carpeta solo desaparece si ha quedado vacía.
            lineas = ["@echo off", "setlocal", "set /a intentos=0", ":esperar",
                      "set /a intentos+=1", "ping -n 2 127.0.0.1 >nul"]
            lineas += [f'del /f /q "{path}" 2>nul' for path in pendientes]
            lineas += [f'if exist "{pendientes[0]}" (',
                       "  if %intentos% lss 15 goto esperar",
                       ")",
                       f'rd /q "{target}" 2>nul',
                       'del /f /q "%~f0" 2>nul']
            fh.write("\r\n".join(lineas) + "\r\n")

        subprocess.Popen(["cmd", "/c", limpiador],
                         creationflags=(subprocess.CREATE_NO_WINDOW
                                        | subprocess.DETACHED_PROCESS))
    else:
        try:
            os.rmdir(target)
        except OSError:
            pass

    report("Desinstalación terminada.", 100)


# --------------------------------------------------------------------------
# Ventana
# --------------------------------------------------------------------------

class Installer(tk.Tk):
    def __init__(self, modo="instalar"):
        enable_dpi_awareness()
        super().__init__()

        ui.UI_SCALE = max(1.0, round(self.winfo_fpixels("1i") / 96.0, 2))
        self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
        ui.load_pixel_font()
        THEME.clear()
        THEME.update(THEMES["95"])

        self.modo = modo
        self.mensajes = queue.Queue()
        self.trabajando = False

        titulo = ("Instalación de " if modo == "instalar" else "Desinstalar ") + APP_NAME
        self.title(titulo)
        self.configure(bg=T("face"))
        self.geometry(f"{S(560)}x{S(430)}")
        self.resizable(False, False)
        self._set_icon()
        self._build(titulo)
        self.after(100, self._drain)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _set_icon(self):
        icono = os.path.join(RES_DIR, "assets", "risa.ico")
        if os.path.isfile(icono):
            try:
                self.iconbitmap(default=icono)
            except Exception:
                pass

    def _build(self, titulo):
        TitleBar(self, titulo).pack(fill="x", padx=S(3), pady=(S(3), 0))

        marco = tk.Frame(self, bg=T("face"))
        marco.pack(fill="both", expand=True, padx=S(3))

        cabecera = tk.Frame(marco, bg=T("face"))
        cabecera.pack(fill="x", pady=(S(12), S(4)))
        self._icono = icon_image(self, 48)
        if self._icono is not None:
            tk.Label(cabecera, image=self._icono, bg=T("face")).pack(side="left",
                                                                    padx=(S(4), S(12)))
        if self.modo == "instalar":
            texto = (f"{APP_NAME} {APP_VERSION}\n\n"
                     "Descargador de vídeo y conversor de formatos.\n"
                     "Se instalará solo para tu usuario, sin pedir permisos de "
                     "administrador.")
        else:
            texto = (f"Se va a desinstalar {APP_NAME}.\n\n"
                     "Se quitarán el programa y sus accesos directos.")
        retro_label(cabecera, texto, wraplength=S(420)).pack(side="left", anchor="w")

        if self.modo == "instalar":
            grupo = RetroGroup(marco, "Carpeta de destino")
            grupo.pack(fill="x", pady=(S(10), 0))
            cuerpo = grupo.body
            cuerpo.columnconfigure(0, weight=1)
            self.var_destino = tk.StringVar(value=default_target())
            caja, _ = retro_entry(cuerpo, self.var_destino)
            caja.grid(row=0, column=0, sticky="ew", ipady=S(4))
            RetroButton(cuerpo, "Examinar...", command=self._elegir,
                        width=88).grid(row=0, column=1, padx=(S(6), 0))

            opciones = RetroGroup(marco, "Accesos directos")
            opciones.pack(fill="x", pady=(S(10), 0))
            self.var_escritorio = tk.BooleanVar(value=True)
            self.var_inicio = tk.BooleanVar(value=True)
            self.var_abrir = tk.BooleanVar(value=True)
            RetroCheck(opciones.body, "Crear acceso directo en el escritorio",
                       self.var_escritorio).pack(anchor="w")
            RetroCheck(opciones.body, "Añadir al menú Inicio",
                       self.var_inicio).pack(anchor="w", pady=(S(4), 0))
            RetroCheck(opciones.body, "Abrir el programa al terminar",
                       self.var_abrir).pack(anchor="w", pady=(S(4), 0))
        else:
            grupo = RetroGroup(marco, "Opciones")
            grupo.pack(fill="x", pady=(S(10), 0))
            self.var_datos = tk.BooleanVar(value=False)
            RetroCheck(grupo.body,
                       "Borrar también los ajustes y los componentes "
                       "descargados", self.var_datos).pack(anchor="w")

        self.barra = RetroProgress(marco)
        self.barra.pack(fill="x", pady=(S(16), S(4)))

        botones = tk.Frame(marco, bg=T("face"))
        botones.pack(fill="x", pady=(S(4), S(8)))
        etiqueta = "Instalar" if self.modo == "instalar" else "Desinstalar"
        self.btn_accion = RetroButton(botones, etiqueta, command=self._accion,
                                      width=120, height=28, default=True)
        self.btn_accion.pack(side="right")
        self.btn_salir = RetroButton(botones, "Salir", command=self._close,
                                     width=92, height=28)
        self.btn_salir.pack(side="right", padx=(0, S(6)))

        self.estado = StatusBar(self, weights=(1,))
        self.estado.pack(side="bottom", fill="x", padx=S(3), pady=(0, S(3)))
        self.estado.set(0, "Listo.")

    # ---- acciones ----

    def _elegir(self):
        carpeta = filedialog.askdirectory(initialdir=os.path.dirname(
            self.var_destino.get()))
        if carpeta:
            self.var_destino.set(os.path.join(carpeta, APP_NAME))

    def _informe(self, texto, pct):
        self.mensajes.put((texto, pct))

    def _accion(self):
        if self.trabajando:
            return
        if self.modo == "instalar":
            destino = self.var_destino.get().strip()
            if not destino:
                messagebox.showwarning("Falta la carpeta",
                                       "Indica dónde quieres instalarlo.")
                return
            objetivo = self._instalar
            argumentos = (destino, self.var_escritorio.get(), self.var_inicio.get())
        else:
            if not messagebox.askyesno("Desinstalar",
                                       f"¿Seguro que quieres quitar {APP_NAME}?"):
                return
            objetivo = self._desinstalar
            argumentos = (self.var_datos.get(),)

        self.trabajando = True
        self.btn_accion.configure(state="disabled")
        self.btn_salir.configure(state="disabled")
        threading.Thread(target=objetivo, args=argumentos, daemon=True).start()

    def _instalar(self, destino, escritorio, inicio):
        try:
            exe = install(destino, escritorio, inicio, self._informe)
            self.mensajes.put(("__fin__", exe))
        except Exception as exc:
            self.mensajes.put(("__error__", str(exc)))

    def _desinstalar(self, borrar_datos):
        try:
            uninstall(remove_data=borrar_datos, report=self._informe)
            self.mensajes.put(("__fin__", None))
        except Exception as exc:
            self.mensajes.put(("__error__", str(exc)))

    def _drain(self):
        try:
            while True:
                texto, dato = self.mensajes.get_nowait()
                if texto == "__fin__":
                    self._terminar(dato)
                elif texto == "__error__":
                    self.trabajando = False
                    self.btn_accion.configure(state="normal")
                    self.btn_salir.configure(state="normal")
                    self.estado.set(0, "Ha fallado.")
                    messagebox.showerror("Error", dato)
                else:
                    self.estado.set(0, texto)
                    self.barra.set_value(dato)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _terminar(self, exe):
        self.trabajando = False
        self.barra.set_value(100)
        self.btn_salir.configure(state="normal")
        if self.modo == "instalar":
            self.estado.set(0, "Instalado correctamente.")
            messagebox.showinfo(
                "Listo",
                f"{APP_NAME} se ha instalado.\n\n"
                "Tienes su acceso directo en el escritorio y en el menú Inicio.")
            if exe and self.var_abrir.get():
                try:
                    subprocess.Popen([exe], cwd=os.path.dirname(exe))
                except Exception:
                    pass
        else:
            self.estado.set(0, "Desinstalado.")
            messagebox.showinfo("Listo", f"{APP_NAME} se ha desinstalado.")
        self._close()

    def _close(self):
        if self.trabajando:
            return
        self.destroy()


def main():
    argumentos = [a.lower() for a in sys.argv[1:]]
    modo = "desinstalar" if "--desinstalar" in argumentos else "instalar"

    if "--silencioso" in argumentos:
        try:
            if modo == "instalar":
                destino = default_target()
                for i, valor in enumerate(sys.argv[1:]):
                    if valor.lower() == "--destino" and i + 2 <= len(sys.argv[1:]):
                        destino = sys.argv[i + 2]
                install(destino)
                print("instalado en", destino)
            else:
                uninstall(remove_data="--datos" in argumentos)
                print("desinstalado")
        except Exception as exc:
            print("ERROR:", exc)
            sys.exit(1)
        return

    Installer(modo).mainloop()


if __name__ == "__main__":
    main()
