"""Ícones SVG do tclcast (desenhados localmente, sem dependências externas).

Cada ícone é um template SVG de 24×24 que é renderizado para QIcon via
QtSvg. Elementos traçados usam `{st}` (atributos de stroke com a cor) e
elementos preenchidos usam `{c}` diretamente.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

#: Cor padrão dos ícones (claro, sobre fundo escuro)
DEFAULT_COLOR = "#e9e9f2"

_STROKE_ATTR = ('fill="none" stroke="{c}" stroke-width="1.9" '
                'stroke-linecap="round" stroke-linejoin="round"')

# --------------------------------------------------------------------------- #
# Templates (viewBox 0 0 24 24)
# --------------------------------------------------------------------------- #
TEMPLATES: dict[str, str] = {
    # --- energia / navegação -------------------------------------------------
    "power": (
        '<path d="M12 3.2v8.2" {st}/>'
        '<path d="M7.05 6.35a7.3 7.3 0 1 0 9.9 0" {st}/>'
    ),
    "home": (
        '<path d="M3.6 10.6 12 3.6l8.4 7" {st}/>'
        '<path d="M5.7 9.4v11h12.6v-11" {st}/>'
        '<path d="M9.9 20.4v-5.2h4.2v5.2" {st}/>'
    ),
    "back": (
        '<path d="M19.2 12H5.4" {st}/>'
        '<path d="M11.2 5.8 5 12l6.2 6.2" {st}/>'
    ),
    "menu": (
        '<path d="M4 6.8h16" {st}/>'
        '<path d="M4 12h16" {st}/>'
        '<path d="M4 17.2h16" {st}/>'
    ),
    "source": (
        '<rect x="3.2" y="5.2" width="17.6" height="13.6" rx="2.4" {st}/>'
        '<path d="M8 12h7.4" {st}/>'
        '<path d="M12.9 9.5 15.4 12l-2.5 2.5" {st}/>'
    ),
    "remote": (
        '<rect x="7.2" y="2.4" width="9.6" height="19.2" rx="3" {st}/>'
        '<circle cx="12" cy="6.9" r="1.7" {st}/>'
        '<path d="M9.6 11.9h4.8" {st}/>'
        '<path d="M9.6 15h4.8" {st}/>'
        '<path d="M9.6 18.1h4.8" {st}/>'
    ),
    # --- direcionais ---------------------------------------------------------
    "up": '<path d="M6 15.2 12 9l6 6.2" {st}/>',
    "down": '<path d="M6 8.8 12 15l6-6.2" {st}/>',
    "ch_up": (
        '<path d="M7 11 12 6.5l5 4.5" {st}/>'
        '<path d="M7 16.5 12 12l5 4.5" {st}/>'
    ),
    "ch_down": (
        '<path d="M7 13 12 17.5l5-4.5" {st}/>'
        '<path d="M7 7.5 12 12l5-4.5" {st}/>'
    ),
    "left": '<path d="M15.2 6 9 12l6.2 6" {st}/>',
    "right": '<path d="M8.8 6 15 12l-6.2 6" {st}/>',
    "ok": (
        '<circle cx="12" cy="12" r="8.6" {st}/>'
        '<circle cx="12" cy="12" r="3.1" fill="{c}"/>'
    ),
    # --- volume --------------------------------------------------------------
    "vol_up": (
        '<path d="M4 9.6h3.1L11.6 6v12l-4.5-3.6H4z" {st}/>'
        '<path d="M18.2 9.6v4.8" {st}/>'
        '<path d="M15.8 12h4.8" {st}/>'
    ),
    "vol_down": (
        '<path d="M4 9.6h3.1L11.6 6v12l-4.5-3.6H4z" {st}/>'
        '<path d="M15.8 12h4.8" {st}/>'
    ),
    "mute": (
        '<path d="M4 9.6h3.1L11.6 6v12l-4.5-3.6H4z" {st}/>'
        '<path d="M15.6 9.8l5 4.4" {st}/>'
        '<path d="M20.6 9.8l-5 4.4" {st}/>'
    ),
    # --- transporte de mídia -------------------------------------------------
    "play": '<path d="M8.2 5.6v12.8L19 12z" fill="{c}"/>',
    "pause": (
        '<rect x="7" y="5.6" width="3.6" height="12.8" rx="1.3" fill="{c}"/>'
        '<rect x="13.4" y="5.6" width="3.6" height="12.8" rx="1.3" fill="{c}"/>'
    ),
    "stop": '<rect x="6.6" y="6.6" width="10.8" height="10.8" rx="2" fill="{c}"/>',
    "prev": (
        '<rect x="5" y="5.6" width="2.3" height="12.8" rx="1.1" fill="{c}"/>'
        '<path d="M19 6.2v11.6L9.6 12z" fill="{c}"/>'
    ),
    "next": (
        '<path d="M5 6.2v11.6L14.4 12z" fill="{c}"/>'
        '<rect x="16.7" y="5.6" width="2.3" height="12.8" rx="1.1" fill="{c}"/>'
    ),
    "seek_back": (
        '<path d="M11.2 6.2 5.6 12l5.6 5.8" {st}/>'
        '<path d="M18.4 6.2 12.8 12l5.6 5.8" {st}/>'
    ),
    "seek_fwd": (
        '<path d="M5.6 6.2 11.2 12l-5.6 5.8" {st}/>'
        '<path d="M12.8 6.2 18.4 12l-5.6 5.8" {st}/>'
    ),
    # --- ações ---------------------------------------------------------------
    "camera": (
        '<rect x="3" y="7.2" width="18" height="12.6" rx="2.6" {st}/>'
        '<path d="M8.6 7.2l1.3-2.6h4.2l1.3 2.6" {st}/>'
        '<circle cx="12" cy="13.4" r="3.9" {st}/>'
    ),
    "folder": (
        '<path d="M3.2 7.6a2 2 0 0 1 2-2h3.6l2 2.4h8a2 2 0 0 1 2 2v8.4a2 '
        '2 0 0 1-2 2H5.2a2 2 0 0 1-2-2z" {st}/>'
    ),
    "files": (
        '<path d="M3.2 7.6a2 2 0 0 1 2-2h3.6l2 2.4h8a2 2 0 0 1 2 2v8.4a2 '
        '2 0 0 1-2 2H5.2a2 2 0 0 1-2-2z" {st}/>'
        '<path d="M12 12.4v4.6" {st}/>'
        '<path d="M9.7 14.7h4.6" {st}/>'
    ),
    "keyboard": (
        '<rect x="2.6" y="6.6" width="18.8" height="10.8" rx="2.2" {st}/>'
        '<path d="M6 10.2h1.4" {st}/>'
        '<path d="M9.6 10.2H11" {st}/>'
        '<path d="M13.2 10.2h1.4" {st}/>'
        '<path d="M16.8 10.2h1.4" {st}/>'
        '<path d="M7.6 14.1h8.8" {st}/>'
    ),
    "apps": (
        '<rect x="3.6" y="3.6" width="7" height="7" rx="1.8" {st}/>'
        '<rect x="13.4" y="3.6" width="7" height="7" rx="1.8" {st}/>'
        '<rect x="3.6" y="13.4" width="7" height="7" rx="1.8" {st}/>'
        '<rect x="13.4" y="13.4" width="7" height="7" rx="1.8" {st}/>'
    ),
    "cast": (
        '<rect x="6.4" y="3.8" width="14.8" height="11" rx="2" {st}/>'
        '<circle cx="5.8" cy="17.6" r="1.2" fill="{c}"/>'
        '<path d="M5.8 13.9a3.7 3.7 0 0 1 3.7 3.7" {st}/>'
        '<path d="M5.8 10.6a7 7 0 0 1 7 7" {st}/>'
    ),
    "search": (
        '<circle cx="10.6" cy="10.6" r="6.2" {st}/>'
        '<path d="M15.4 15.4l4.4 4.4" {st}/>'
    ),
    "close": (
        '<path d="M6.2 6.2 17.8 17.8" {st}/>'
        '<path d="M17.8 6.2 6.2 17.8" {st}/>'
    ),
    "exit": (
        '<path d="M14.4 3.8h3.4a2 2 0 0 1 2 2v12.4a2 2 0 0 1-2 2h-3.4" {st}/>'
        '<path d="M9.8 16.4 5.4 12l4.4-4.4" {st}/>'
        '<path d="M5.6 12h9" {st}/>'
    ),
    "send": (
        '<path d="M3.4 11.4 20.6 3.9l-7.5 17.2-2.3-6.9z" {st}/>'
        '<path d="M10.8 14.2 20.6 3.9" {st}/>'
    ),
    "plus": (
        '<path d="M12 5.2v13.6" {st}/>'
        '<path d="M5.2 12h13.6" {st}/>'
    ),
    "refresh": (
        '<path d="M19.4 12a7.4 7.4 0 1 1-2.4-5.5" {st}/>'
        '<path d="M19.6 3.9v4.3h-4.3" {st}/>'
    ),
    "trash": (
        '<path d="M4.8 7.1h14.4" {st}/>'
        '<path d="M9.6 7.1V5.2h4.8v1.9" {st}/>'
        '<path d="M6.6 7.1l.9 13.7h9l.9-13.7" {st}/>'
        '<path d="M10.2 10.6v6.8" {st}/>'
        '<path d="M13.8 10.6v6.8" {st}/>'
    ),
    "open": (
        '<path d="M12.8 5H6.6A1.6 1.6 0 0 0 5 6.6v10.8A1.6 1.6 0 0 0 6.6 19h10.8a1.6 '
        '1.6 0 0 0 1.6-1.6v-6.2" {st}/>'
        '<path d="M14.6 4.4h5v5" {st}/>'
        '<path d="M19.6 4.4 11 13" {st}/>'
    ),
    "tv": (
        '<rect x="3.2" y="5.4" width="17.6" height="12.4" rx="2.2" {st}/>'
        '<path d="M8.4 21h7.2" {st}/>'
        '<path d="M12 17.8V21" {st}/>'
        '<path d="M8.4 5.4 12 2.4l3.6 3" {st}/>'
    ),
    "cast_arrow": (
        '<rect x="2.8" y="5" width="18.4" height="12.2" rx="2.2" {st}/>'
        '<path d="M12 13.8V8.4" {st}/>'
        '<path d="M9.6 10.8 12 8.4l2.4 2.4" {st}/>'
    ),
    "gear": (
        '<circle cx="12" cy="12" r="3.4" {st}/>'
        '<path d="M17.6 12h3.2" {st}/>'
        '<path d="M15.96 15.96l2.26 2.26" {st}/>'
        '<path d="M12 17.6v3.2" {st}/>'
        '<path d="M8.04 15.96l-2.26 2.26" {st}/>'
        '<path d="M6.4 12H3.2" {st}/>'
        '<path d="M8.04 8.04 5.78 5.78" {st}/>'
        '<path d="M12 6.4V3.2" {st}/>'
        '<path d="M15.96 8.04l2.26-2.26" {st}/>'
    ),
}


def _svg(name: str, color: str) -> bytes:
    body = TEMPLATES[name].format(
        c=color, st=_STROKE_ATTR.format(c=color))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'{body}</svg>')
    return svg.encode("utf-8")


def dot_icon(color: str, size: int = 18) -> QIcon:
    """Bolinha preenchida (teclas coloridas do remoto)."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f'<circle cx="12" cy="12" r="8" fill="{color}"/></svg>')
    return _render(svg.encode("utf-8"), size)


def _render(svg: bytes, size: int, dpr: float = 2.0) -> QIcon:
    renderer = QSvgRenderer(QByteArray(svg))
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(dpr)
    return QIcon(pm)


def make_icon(name: str, color: str = DEFAULT_COLOR, size: int = 22) -> QIcon:
    """Renderiza um ícone nomeado na cor pedida."""
    return _render(_svg(name, color), size)


def app_icon_svg(bg: str = "#20202b", fg: str = "#e63d4f") -> bytes:
    """SVG completo do ícone do aplicativo (emblema + TV).

    Usado pela bandeja, pela janela e pelo empacotamento (AppImage/.deb).
    """
    body = TEMPLATES["tv"].format(c=fg, st=_STROKE_ATTR.format(c=fg))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<rect x="1.7" y="1.7" width="20.6" height="20.6" rx="4.8" '
        f'fill="{bg}"/>'
        f'<g transform="translate(3 3) scale(0.75)">{body}</g>'
        '</svg>')
    return svg.encode("utf-8")


def tv_window_icon(color: str = "#e63d4f") -> QIcon:
    """Ícone da janela/aplicativo/bandeja (emblema arredondado)."""
    svg = app_icon_svg(fg=color)
    ico = QIcon()
    for size in (24, 48, 96, 192):
        ico.addPixmap(_render(svg, size).pixmap(size, size))
    return ico
