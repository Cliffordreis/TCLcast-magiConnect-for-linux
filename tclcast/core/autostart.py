"""Início automático com o sistema e atalho no menu (padrão XDG).

Gera arquivos .desktop em ``~/.config/autostart`` e
``~/.local/share/applications`` apontando para o alvo correto em cada modo
de execução:

- **AppImage**: usa a variável ``APPIMAGE`` (caminho do próprio arquivo);
- **instalado via .deb** (Python portátil em /opt/tclcast): usa
  ``sys.executable -m tclcast``, que já é o Python embarcado;
- **código-fonte (dev)**: usa o Python do venv + ``-m tclcast`` com o
  diretório do projeto em ``Path=``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_CFG = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
_DATA = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()

#: Arquivo de início automático criado por enable()
AUTOSTART_FILE = _CFG / "autostart" / "tclcast.desktop"
#: Atalho de menu criado por install_menu()
MENU_FILE = _DATA / "applications" / "tclcast.desktop"
#: Atalho instalado pelo pacote .deb (quando existir, não criamos outro)
PACKAGE_MENU_FILE = Path("/usr/share/applications/tclcast.desktop")


def _source_root() -> Path | None:
    """Raiz do projeto quando rodando a partir do código-fonte (senão None)."""
    raiz = Path(__file__).resolve().parents[2]
    if (raiz / "README.md").exists():
        return raiz
    return None


def launch_command() -> str:
    """Comando que inicia o tclcast no modo atual (janela/bandeja)."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return f'"{appimage}"'
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m tclcast'


def _icon_value() -> str:
    """Valor de ``Icon=`` para os .desktop (caminho absoluto persistente)."""
    if PACKAGE_MENU_FILE.exists():
        return "tclcast"    # ícone instalado no hicolor pelo pacote .deb
    # AppImage/dev: gera um PNG persistente em ~/.config/tclcast/ (o
    # caminho do AppImage montado em /tmp/.mount_* deixa de existir ao sair)
    try:
        from PySide6.QtGui import QGuiApplication

        from ..ui import icons
        if QGuiApplication.instance() is not None:
            dest = _CFG / "tclcast" / "tclcast.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            icons.tv_window_icon().pixmap(256, 256).save(str(dest), "PNG")
            return str(dest)
    except Exception:   # noqa: BLE001 — ícone é opcional
        pass
    return "tclcast"


def _entry(*, autostart: bool) -> str:
    exec_line = (f"Exec={launch_command()} --tray" if autostart
                 else f"Exec={launch_command()}")
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=tclcast",
        "Comment=Controle remoto e espelhamento para TVs TCL",
        exec_line,
        f"Icon={_icon_value()}",
        "Terminal=false",
    ]
    raiz = _source_root()
    if raiz is not None:
        lines.append(f"Path={raiz}")   # cwd p/ `python -m tclcast` (dev)
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    else:
        lines.append("Categories=AudioVideo;Utility;")
        lines.append("StartupWMClass=tclcast")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Início automático
# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    """True quando o tclcast inicia junto com o sistema."""
    return AUTOSTART_FILE.exists()


def enable() -> None:
    """Cria o arquivo de autostart (tclcast inicia na bandeja)."""
    AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART_FILE.write_text(_entry(autostart=True), encoding="utf-8")


def disable() -> None:
    """Remove o arquivo de autostart."""
    AUTOSTART_FILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Atalho no menu de aplicativos
# --------------------------------------------------------------------------- #
def package_menu_entry() -> bool:
    """True quando o atalho de menu veio do pacote (.deb)."""
    return PACKAGE_MENU_FILE.exists()


def is_menu_installed() -> bool:
    return MENU_FILE.exists()


def install_menu() -> None:
    MENU_FILE.parent.mkdir(parents=True, exist_ok=True)
    MENU_FILE.write_text(_entry(autostart=False), encoding="utf-8")


def remove_menu() -> None:
    MENU_FILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
def refresh_shortcuts() -> None:
    """Reescreve atalhos já existentes com o caminho atual do app.

    Chamado ao abrir: se o usuário mover o AppImage de pasta, os atalhos
    continuam válidos sem precisar mexer nas opções.
    """
    if AUTOSTART_FILE.exists():
        AUTOSTART_FILE.write_text(_entry(autostart=True), encoding="utf-8")
    if MENU_FILE.exists():
        MENU_FILE.write_text(_entry(autostart=False), encoding="utf-8")
