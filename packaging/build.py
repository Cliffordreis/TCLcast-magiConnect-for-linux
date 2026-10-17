#!/usr/bin/env python3
"""Empacota o tclcast em .AppImage e .deb usando um Python portátil.

Uso:
    .venv/bin/python packaging/build.py            # gera os dois pacotes
    .venv/bin/python packaging/build.py --appimage # só o AppImage
    .venv/bin/python packaging/build.py --deb      # só o .deb

Estratégia: em vez de PyInstaller (que amarra o binário à glibc da máquina
de build), baixamos um CPython portátil (python-build-standalone, compilado
contra glibc antiga) e instalamos PySide6-Essentials 6.9 (manylinux_2_28) +
pycryptodome dentro dele. O resultado roda em qualquer distro x86_64 com
glibc >= 2.28 (Ubuntu 20.04+, Debian 11+, Fedora 33+, etc.).

O ffmpeg estático (johnvansickle) é embutido para o espelhamento funcionar
mesmo sem ffmpeg instalado no sistema (use --no-ffmpeg para pular).

Saída: dist/tclcast-<versao>-x86_64.AppImage e dist/tclcast_<versao>_amd64.deb
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "packaging" / "cache"
BUILD = ROOT / "packaging" / "build"
DIST = ROOT / "dist"
APP_DIR = BUILD / "tclcast.AppDir"
USR = APP_DIR / "usr"

PYSIDE = "PySide6-Essentials==6.9.2"   # manylinux_2_28 → glibc 2.28+
PYCRYPTODOME = "pycryptodome>=3.20"

PYTHON_URL_FALLBACK = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20261013/cpython-3.12.15%2B20261013-x86_64-unknown-linux-gnu-"
    "install_only.tar.gz")
APPIMAGETOOL_URL = (
    "https://github.com/AppImage/appimagetool/releases/download/continuous/"
    "appimagetool-x86_64.AppImage")
FFMPEG_URL = ("https://johnvansickle.com/ffmpeg/releases/"
              "ffmpeg-release-amd64-static.tar.xz")

DEB_DEPENDS = [
    "libc6 (>= 2.28)", "libglib2.0-0", "libfontconfig1", "libfreetype6",
    "libdbus-1-3", "libgl1", "libegl1", "libx11-6", "libxi6",
    "libxkbcommon0", "libxkbcommon-x11-0", "libxcb1", "libxcb-cursor0",
    "libxcb-icccm4", "libxcb-image0", "libxcb-keysyms1", "libxcb-randr0",
    "libxcb-render0", "libxcb-render-util0", "libxcb-shape0", "libxcb-shm0",
    "libxcb-sync1", "libxcb-xfixes0", "libxcb-xinput0", "libxcb-xinerama0",
    "libxcb-xkb1",
]


def version() -> str:
    for line in (ROOT / "tclcast" / "__init__.py").read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("não achei __version__ em tclcast/__init__.py")


def run(cmd, **kw) -> None:
    print("    $", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def download(url: str, dest: Path) -> Path:
    if dest.exists():
        print(f"    = usando cache: {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"    ↓ {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as resp, open(tmp, "wb") as f:
        shutil.copyfileobj(resp, f)
    tmp.rename(dest)
    return dest


def find_python_url() -> str:
    """URL do CPython portátil (tenta a última release; senão usa a fixa)."""
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/astral-sh/python-build-standalone"
            "/releases/latest",
            headers={"User-Agent": "tclcast-build"})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        for a in d["assets"]:
            n = a["name"]
            if (n.startswith("cpython-3.12.")
                    and n.endswith("x86_64-unknown-linux-gnu-"
                                   "install_only.tar.gz")):
                print(f"    python-build-standalone {d['tag_name']}")
                return a["browser_download_url"]
    except Exception as e:   # noqa: BLE001
        print(f"    ! API do GitHub falhou ({e}); usando URL fixa")
    return PYTHON_URL_FALLBACK


def extract_python(url: str) -> None:
    name = urllib.parse.unquote(os.path.basename(url))
    tar = download(url, CACHE / name)
    tmp = BUILD / "pyextract"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    print("    → extraindo CPython portátil…")
    with tarfile.open(tar) as tf:
        tf.extractall(tmp, filter="data")
    src = tmp / "python"
    if not (src / "bin" / "python3").exists():
        raise SystemExit("tarball do CPython inesperado (sem bin/python3)")
    shutil.move(str(src), USR)
    shutil.rmtree(tmp, ignore_errors=True)


def install_deps() -> None:
    py = USR / "bin" / "python3"
    if subprocess.run([str(py), "-m", "pip", "--version"],
                      capture_output=True).returncode != 0:
        run([py, "-m", "ensurepip"])
    run([py, "-m", "pip", "install", "--no-warn-script-location",
         PYSIDE, PYCRYPTODOME])


def install_app() -> None:
    sp = next(iter((USR / "lib").glob("python3.*/site-packages")), None)
    if sp is None:
        raise SystemExit("site-packages não encontrado no CPython portátil")
    dest = sp / "tclcast"
    if dest.exists():
        shutil.rmtree(dest)
    print("    → copiando o pacote tclcast…")
    shutil.copytree(ROOT / "tclcast", dest,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    launcher = USR / "bin" / "tclcast"
    launcher.write_text(
        "#!/bin/sh\n"
        "# lançador relocatável do tclcast (AppImage/.deb)\n"
        "# resolve o próprio caminho mesmo chamado via symlink — no .deb,\n"
        "# /usr/bin/tclcast -> /opt/tclcast/bin/tclcast\n"
        'SELF="$(readlink -f -- "$0" 2>/dev/null || echo "$0")"\n'
        'DIR="$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)"\n'
        '# -I: modo isolado (não importa tclcast do diretório atual)\n'
        'exec "$DIR/python3" -I -m tclcast "$@"\n')
    launcher.chmod(0o755)


def install_ffmpeg() -> None:
    try:
        tar = download(FFMPEG_URL, CACHE / "ffmpeg-static.tar.xz")
        print("    → extraindo ffmpeg estático…")
        with tarfile.open(tar, "r:xz") as tf:
            member = next((m for m in tf.getmembers()
                           if m.name.endswith("/ffmpeg") and m.isfile()), None)
            if member is None:
                raise RuntimeError("binário ffmpeg não encontrado no tar")
            member.name = "ffmpeg"
            tf.extract(member, USR / "bin", filter="data")
        (USR / "bin" / "ffmpeg").chmod(0o755)
    except Exception as e:   # noqa: BLE001
        print(f"    ! ffmpeg não embutido ({e}) — o espelhamento exigirá "
              "o ffmpeg do sistema")


def make_icons() -> Path:
    """Gera tclcast.svg + PNGs (32…256) com o PySide6 do venv de dev."""
    out = BUILD / "icons"
    out.mkdir(parents=True, exist_ok=True)
    dev_py = ROOT / ".venv" / "bin" / "python"
    if not dev_py.exists():
        dev_py = Path(sys.executable)
    code = (
        "import os, pathlib\n"
        "from PySide6.QtCore import QByteArray, Qt\n"
        "from PySide6.QtGui import QGuiApplication, QPainter, QPixmap\n"
        "from PySide6.QtSvg import QSvgRenderer\n"
        "from tclcast.ui import icons\n"
        "out = pathlib.Path(os.environ['TCLCAST_ICON_OUT'])\n"
        "app = QGuiApplication([])\n"
        "svg = icons.app_icon_svg()\n"
        "(out / 'tclcast.svg').write_bytes(svg)\n"
        "r = QSvgRenderer(QByteArray(svg))\n"
        "for s in (32, 48, 64, 128, 256):\n"
        "    pm = QPixmap(s, s)\n"
        "    pm.fill(Qt.transparent)\n"
        "    p = QPainter(pm)\n"
        "    r.render(p)\n"
        "    p.end()\n"
        "    pm.save(str(out / f'tclcast-{s}.png'), 'PNG')\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               TCLCAST_ICON_OUT=str(out))
    run([dev_py, "-c", code], cwd=ROOT, env=env)
    return out


def desktop_entry(exec_: str) -> str:
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=tclcast\n"
            "Comment=Controle remoto e espelhamento para TVs TCL\n"
            f"Exec={exec_}\n"
            "Icon=tclcast\n"
            "Terminal=false\n"
            "Categories=AudioVideo;\n"
            "StartupWMClass=tclcast\n")


def build_appdir(icons_dir: Path) -> None:
    print("    → montando o AppDir…")
    shutil.copy2(icons_dir / "tclcast-256.png", APP_DIR / ".DirIcon")
    shutil.copy2(icons_dir / "tclcast-256.png", APP_DIR / "tclcast.png")
    shutil.copy2(icons_dir / "tclcast.svg", APP_DIR / "tclcast.svg")
    desktop = desktop_entry("tclcast")
    (APP_DIR / "tclcast.desktop").write_text(desktop)

    for s in (32, 48, 64, 128, 256):
        d = USR / "share/icons/hicolor" / f"{s}x{s}" / "apps"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icons_dir / f"tclcast-{s}.png", d / "tclcast.png")
    sd = USR / "share/icons/hicolor/scalable/apps"
    sd.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icons_dir / "tclcast.svg", sd / "tclcast.svg")
    ad = USR / "share/applications"
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "tclcast.desktop").write_text(desktop)

    apprun = APP_DIR / "AppRun"
    apprun.write_text(
        "#!/bin/sh\n"
        'HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
        'export PATH="$HERE/usr/bin:$PATH"\n'
        'exec "$HERE/usr/bin/tclcast" "$@"\n')
    apprun.chmod(0o755)


def build_appimage(v: str) -> Path:
    tool = download(APPIMAGETOOL_URL, CACHE / "appimagetool-x86_64.AppImage")
    tool.chmod(0o755)
    out = DIST / f"tclcast-{v}-x86_64.AppImage"
    print("    → criando o AppImage…")
    env = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1")
    run([tool, "--no-appstream", str(APP_DIR), str(out)], env=env)
    out.chmod(0o755)
    return out


def _dir_size_kb(root: Path) -> int:
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_symlink() or p.is_file():
                total += p.lstat().st_size
        except OSError:
            pass
    return max(1, total // 1024)


def build_deb(v: str) -> Path:
    print("    → montando o pacote .deb…")
    deb = BUILD / "deb"
    if deb.exists():
        shutil.rmtree(deb)
    (deb / "DEBIAN").mkdir(parents=True)
    shutil.copytree(USR, deb / "opt/tclcast", symlinks=True)

    usrbin = deb / "usr/bin"
    usrbin.mkdir(parents=True)
    (usrbin / "tclcast").symlink_to("/opt/tclcast/bin/tclcast")

    ad = deb / "usr/share/applications"
    ad.mkdir(parents=True)
    (ad / "tclcast.desktop").write_text(desktop_entry("tclcast"))
    for s in (32, 48, 64, 128, 256):
        d = deb / "usr/share/icons/hicolor" / f"{s}x{s}" / "apps"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BUILD / "icons" / f"tclcast-{s}.png", d / "tclcast.png")
    sd = deb / "usr/share/icons/hicolor/scalable/apps"
    sd.mkdir(parents=True)
    shutil.copy2(BUILD / "icons" / "tclcast.svg", sd / "tclcast.svg")

    size_kb = _dir_size_kb(deb)
    control = (
        f"Package: tclcast\n"
        f"Version: {v}\n"
        "Architecture: amd64\n"
        "Maintainer: tclcast\n"
        "Section: video\n"
        "Priority: optional\n"
        f"Installed-Size: {size_kb}\n"
        f"Depends: {', '.join(DEB_DEPENDS)}\n"
        "Description: controle remoto, casting e espelhamento para TVs TCL\n"
        " Cliente Linux do protocolo TCL: descoberta de TVs, controle\n"
        " remoto, captura de tela, apps e espelhamento da tela do PC,\n"
        " com ícone na bandeja e início automático opcional.\n")
    (deb / "DEBIAN" / "control").write_text(control)

    out = DIST / f"tclcast_{v}_amd64.deb"
    run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build",
         str(deb), str(out)])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Empacota o tclcast (.deb; AppImage opcional)")
    ap.add_argument("--appimage", action="store_true",
                    help="gera só o AppImage (opcional/legado)")
    ap.add_argument("--deb", action="store_true", help="gera só o .deb (padrão)")
    ap.add_argument("--both", action="store_true",
                    help="gera os dois formatos")
    ap.add_argument("--no-ffmpeg", action="store_true",
                    help="não embute o ffmpeg estático")
    args = ap.parse_args()
    # Padrão: só .deb (o formato recomendado/distribuído).
    do_appimage = args.appimage or args.both
    do_deb = args.deb or args.both or not (args.appimage or args.both)

    v = version()
    alvos = [n for n, on in (("AppImage", do_appimage), (".deb", do_deb)) if on]
    print(f"tclcast {v} — gerando: {', '.join(alvos)}")

    DIST.mkdir(exist_ok=True)
    if BUILD.exists():
        shutil.rmtree(BUILD)
    APP_DIR.mkdir(parents=True)

    print("• CPython portátil")
    extract_python(find_python_url())
    print("• dependências (PySide6-Essentials + pycryptodome)")
    install_deps()
    print("• pacote tclcast")
    install_app()
    if not args.no_ffmpeg:
        print("• ffmpeg estático")
        install_ffmpeg()
    print("• ícones")
    icons_dir = make_icons()
    build_appdir(icons_dir)

    feitos: list[Path] = []
    if do_appimage:
        print("• AppImage")
        feitos.append(build_appimage(v))
    if do_deb:
        print("• .deb")
        feitos.append(build_deb(v))

    print("\nPronto:")
    for p in feitos:
        print(f"  {p}  ({p.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
