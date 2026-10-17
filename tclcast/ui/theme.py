"""Tema visual do tclcast (folha de estilo QSS + cores da paleta).

Visual escuro e moderno: cartões arredondados, acento vermelho (identidade
TCL), ícones claros e espaçamento generoso.
"""
from __future__ import annotations

# --- Paleta ---------------------------------------------------------------- #
BG = "#131419"           # fundo da janela
CARD = "#1b1d24"         # fundo dos cartões (QGroupBox)
CARD_BORDER = "#262a36"  # borda dos cartões
ACCENT = "#e63d4f"       # vermelho de destaque (TCL)
ACCENT_HOVER = "#f0505f"
TEXT = "#e9e9f2"         # texto principal
TEXT_DIM = "#9aa0b4"     # texto secundário

#: Cor branca para ícones sobre botões de acento
ON_ACCENT = "#ffffff"

#: Cor de ícone "perigo" (power, parar)
DANGER_ICON = "#ff8a8a"

APP_QSS = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{
    color: {TEXT};
    font-size: 13px;
    font-family: "Inter", "Ubuntu", "Cantarell", "Noto Sans", "DejaVu Sans";
}}

/* ---------- cartões ---------- */
QGroupBox {{
    background: {CARD};
    border: 1px solid {CARD_BORDER};
    border-radius: 12px;
    margin-top: 10px;
    padding: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    top: 2px;
    padding: 0 6px;
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: 700;
}}

/* ---------- rótulos ---------- */
QLabel#title {{ font-size: 23px; font-weight: 800; color: #ffffff; }}
QLabel#subtitle {{ color: #8a90a6; font-size: 12px; }}
QLabel#pill {{
    background: #1d2029;
    border: 1px solid #2b2f3d;
    border-radius: 10px;
    padding: 5px 10px;
    color: #aab0c6;
}}
QLabel#stateLabel {{ font-size: 15px; font-weight: 700; color: #cfd3e2; }}
QLabel#dim {{ color: #8a90a6; font-size: 12px; }}
QLabel#timeLabel {{
    color: #aab0c6;
    font-family: "DejaVu Sans Mono", "Ubuntu Mono", monospace;
    font-size: 12px;
}}

/* ---------- botões ---------- */
QPushButton {{
    background: #232633;
    border: 1px solid #2e3242;
    border-radius: 10px;
    padding: 7px 12px;
    color: {TEXT};
}}
QPushButton:hover {{ background: #2b2f3f; border-color: #3a3f54; }}
QPushButton:pressed {{ background: #1e212b; }}
QPushButton:disabled {{ color: #6a7085; background: #1b1d24; border-color: #242834; }}

QPushButton#iconBtn {{
    background: #20232e;
    border: 1px solid #2b2f3d;
    border-radius: 12px;
    padding: 0px;
}}
QPushButton#iconBtn:hover {{ background: #2b2f3f; border-color: #3d4257; }}
QPushButton#iconBtn:pressed {{ background: #1b1e27; }}
QPushButton#iconBtn:disabled {{ background: #181a21; border-color: #23262f; }}

QPushButton#okBtn {{
    background: {ACCENT};
    border: none;
    border-radius: 27px;
    padding: 0px;
}}
QPushButton#okBtn:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#okBtn:pressed {{ background: #c93445; }}

QPushButton#playBtn {{
    background: {ACCENT};
    border: none;
    border-radius: 29px;
    padding: 0px;
}}
QPushButton#playBtn:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#playBtn:pressed {{ background: #c93445; }}

QPushButton#accentBtn {{
    background: {ACCENT};
    border: none;
    color: #ffffff;
    font-weight: 700;
    border-radius: 10px;
    padding: 9px 14px;
}}
QPushButton#accentBtn:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#accentBtn:pressed {{ background: #c93445; }}
QPushButton#accentBtn:disabled {{ background: #3a2830; color: #8a7680; }}

QPushButton#powerBtn {{
    background: #33202a;
    border: 1px solid #56303a;
}}
QPushButton#powerBtn:hover {{ background: #46262f; border-color: #6d3a45; }}

QPushButton#ghostBtn {{
    background: transparent;
    border: 1px solid #303444;
    color: #c7cbda;
}}
QPushButton#ghostBtn:hover {{ background: #232633; }}

/* ---------- campos ---------- */
QLineEdit {{
    background: #15171d;
    border: 1px solid #2b2f3d;
    border-radius: 10px;
    padding: 8px 11px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}

/* ---------- listas ---------- */
QListWidget {{
    background: #16181f;
    border: 1px solid #262a36;
    border-radius: 10px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{ padding: 8px 6px; border-radius: 7px; }}
QListWidget::item:hover {{ background: #1f2330; }}
QListWidget::item:selected {{ background: #2a2f42; color: #ffffff; }}

/* ---------- barra de progresso ---------- */
QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: #262a36; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -4px 0;
    border-radius: 7px; background: #ffffff;
}}
QSlider::handle:horizontal:hover {{ background: #ffd9dd; }}
QSlider:disabled::sub-page:horizontal {{ background: #3a3f52; }}

/* ---------- barras de rolagem ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px 2px 0; }}
QScrollBar::handle:vertical {{ background: #2e3242; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4054; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 2px 2px 2px; }}
QScrollBar::handle:horizontal {{ background: #2e3242; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ---------- dicas ---------- */
QToolTip {{
    background: #232633;
    color: {TEXT};
    border: 1px solid #3a4054;
    padding: 4px 8px;
    border-radius: 6px;
}}

/* ---------- controle rápido da bandeja ---------- */
QFrame#quickRemote {{
    background: {BG};
    border: 1px solid #2e3242;
    border-radius: 16px;
}}
QLabel#qrTitle {{ font-size: 13px; font-weight: 800; color: #ffffff; }}
QPushButton#rk {{
    background: #232633;
    border: 1px solid #2e3242;
    border-radius: 12px;
    padding: 0px;
}}
QPushButton#rk:hover {{ background: #2b2f3f; border-color: #3d4257; }}
QPushButton#rk:pressed {{ background: #1e212b; }}
QPushButton#rk:checked {{ background: #43242e; border-color: #6d3a45; }}
QPushButton#rkOk {{
    background: {ACCENT};
    border: none;
    border-radius: 26px;
    padding: 0px;
}}
QPushButton#rkOk:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#rkOk:pressed {{ background: #c93445; }}
QPushButton#rkDanger {{ background: #33202a; border: 1px solid #56303a; }}
QPushButton#rkDanger:hover {{ background: #46262f; border-color: #6d3a45; }}
QPushButton#rkText {{
    background: #20232e;
    border: 1px solid #2b2f3d;
    border-radius: 10px;
    padding: 4px 8px;
    color: {TEXT};
    font-size: 12px;
}}
QPushButton#rkText:hover {{ background: #2b2f3f; border-color: #3d4257; }}
QPushButton#rkText:pressed {{ background: #1b1e27; }}
QPushButton#rkText:checked {{ background: #43242e; border-color: #6d3a45; }}
QPushButton#rkTextDanger {{
    background: #2f1e26;
    border: 1px solid #56303a;
    border-radius: 10px;
    padding: 4px 8px;
    color: #ffc9c9;
    font-size: 12px;
}}
QPushButton#rkTextDanger:hover {{ background: #3a2430; border-color: #6d3a45; }}
QPushButton#rkTextDanger:pressed {{ background: #26181f; }}
"""
