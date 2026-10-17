"""Painel flutuante de controle rápido (aberto pelo ícone da bandeja).

Um "controle remoto" compacto e retangular: teclas de sistema em cima,
direcional em cruz no centro com volume/canais nas laterais e atalhos do app
embaixo. Abre com animação saindo do ícone da bandeja, é arrastável e fica
sempre por cima. O ✕ fecha SÓ o painel; o botão "Sair do tclcast" encerra o
programa por completo.
"""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QParallelAnimationGroup, QPoint,
                            QPropertyAnimation, QRect, QSize, Qt)
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout, QWidget)

from . import icons
from .theme import DANGER_ICON, ON_ACCENT


class QuickRemote(QWidget):
    """Painel de controle rápido com cara de controle remoto."""

    KEY_BTN = 48      # botões do direcional/volume/canal
    TOP_BTN = 34      # power/fechar do cabeçalho
    # largura fixa com folga exata: vol (48) + 10 + direcional (48+8+64+8+48)
    # + 10 + canais (48) + margens (2x14) = 324 (menos que isso o Qt comprime
    # as colunas do direcional de forma assimétrica e cola o ">" no OK)
    WIDTH = 324
    DUR_IN = 200      # animação de abertura (ms)
    DUR_OUT = 130     # animação de fechamento (ms)

    def __init__(self, win):
        super().__init__(win, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint)
        self._win = win
        self._drag: QPoint | None = None
        self._moved = False          # usuário já reposicionou o painel?
        self._anim: QParallelAnimationGroup | None = None
        self.setWindowTitle("tclcast — Controle rápido")
        self.setWindowIcon(icons.tv_window_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(self.WIDTH)

        card = QFrame(self)
        card.setObjectName("quickRemote")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)

        # ---------------------------------------------------------- #
        # Cabeçalho: título + power da TV + fechar painel
        # ---------------------------------------------------------- #
        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("Controle rápido")
        title.setObjectName("qrTitle")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self._btn("power", "Ligar/desligar a TV",
                                win._confirm_power, kind="danger",
                                size=self.TOP_BTN))
        top.addWidget(self._btn("close", "Fechar o controle rápido",
                                self.hide_panel, size=self.TOP_BTN))
        lay.addLayout(top)

        # ---------------------------------------------------------- #
        # Teclas de sistema da TV (4 botões largos)
        # ---------------------------------------------------------- #
        sys_row = QHBoxLayout()
        sys_row.setSpacing(8)
        for icon, tip, key in (
                ("home", "Início", "MENU"),
                ("menu", "Menu rápido da TV (☰)", "SUBMENU"),
                ("back", "Voltar", "BACK"),
                ("source", "Fonte de entrada (HDMI…)", "SOURCE")):
            b = self._btn(icon, tip, lambda k=key: win._key(k))
            b.setFixedHeight(44)
            sys_row.addWidget(b, 1)
        lay.addLayout(sys_row)

        # ---------------------------------------------------------- #
        # Corpo: volume | direcional em cruz | canais
        # ---------------------------------------------------------- #
        body = QHBoxLayout()
        body.setSpacing(10)

        vol = QVBoxLayout()
        vol.setSpacing(8)
        vol.addWidget(self._btn("vol_up", "Volume +",
                                lambda: win._key("VOL_UP")))
        vol.addWidget(self._btn("mute", "Mudo", lambda: win._key("MUTE")))
        vol.addWidget(self._btn("vol_down", "Volume −",
                                lambda: win._key("VOL_DOWN")))
        body.addLayout(vol)

        dpad = QGridLayout()
        dpad.setSpacing(8)
        dpad.setColumnStretch(0, 1)
        dpad.setColumnStretch(2, 1)
        dpad.addWidget(self._btn("up", "Cima", lambda: win._key("UP")), 0, 1)
        dpad.addWidget(self._btn("left", "Esquerda",
                                 lambda: win._key("LEFT")), 1, 0)
        ok = self._btn("ok", "OK (confirmar)", lambda: win._key("OK"),
                       kind="ok")
        ok.setFixedSize(64, 64)
        dpad.addWidget(ok, 1, 1)
        dpad.addWidget(self._btn("right", "Direita",
                                 lambda: win._key("RIGHT")), 1, 2)
        dpad.addWidget(self._btn("down", "Baixo",
                                 lambda: win._key("DOWN")), 2, 1)
        body.addLayout(dpad, 1)

        ch = QVBoxLayout()
        ch.setSpacing(8)
        ch.addWidget(self._btn("ch_up", "Canal +",
                               lambda: win._key("CH_UP")))
        ch.addStretch(1)
        ch.addWidget(self._btn("ch_down", "Canal −",
                               lambda: win._key("CH_DOWN")))
        body.addLayout(ch)
        lay.addLayout(body)

        # ---------------------------------------------------------- #
        # Atalhos do app
        # ---------------------------------------------------------- #
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(self._text_btn("camera", "Captura", "Captura de tela",
                                      win._capture_tv_screen))
        row1.addWidget(self._text_btn("apps", "Apps",
                                      "Aplicativos instalados na TV",
                                      win._open_apps_dialog))
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.mirror_btn = self._text_btn(
            "cast", "Espelhar", "Espelhar o PC na TV",
            win._toggle_mirror, checkable=True)
        row2.addWidget(self.mirror_btn)
        row2.addWidget(self._text_btn(
            "open", "App completo", "Abrir o aplicativo completo",
            win._show_window))
        lay.addLayout(row2)

        # Encerrar o PROGRAMA (o ✕ do cabeçalho só fecha este painel)
        quit_row = QHBoxLayout()
        quit_row.addWidget(self._text_btn(
            "exit", "Sair do tclcast", "Encerrar o programa por completo",
            win._quit_from_tray, danger=True))
        lay.addLayout(quit_row)

    # ------------------------------------------------------------------ #
    # Construção de botões
    # ------------------------------------------------------------------ #
    def _btn(self, icon: str, tip: str, handler, kind: str = "",
             size: int = KEY_BTN) -> QPushButton:
        b = QPushButton()
        color = DANGER_ICON if kind == "danger" else (
            ON_ACCENT if kind == "ok" else None)
        b.setIcon(icons.make_icon(icon, color) if color
                  else icons.make_icon(icon))
        b.setIconSize(QSize(22, 22))
        b.setFixedSize(size, size)
        b.setObjectName({"danger": "rkDanger", "ok": "rkOk"}.get(kind, "rk"))
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        # clicked(bool) NÃO pode vazar para o handler: no PySide 6.9 do pacote
        # o argumento sobrescreve o default do lambda (ex.: _key(False)).
        b.clicked.connect(lambda *_, h=handler: h())
        return b

    def _text_btn(self, icon: str, label: str, tip: str, handler,
                  checkable: bool = False,
                  danger: bool = False) -> QPushButton:
        b = QPushButton(label)
        b.setIcon(icons.make_icon(icon, DANGER_ICON, 18) if danger
                  else icons.make_icon(icon, size=18))
        b.setIconSize(QSize(18, 18))
        b.setObjectName("rkTextDanger" if danger else "rkText")
        b.setFixedHeight(40)
        b.setCheckable(checkable)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda *_, h=handler: h())
        return b

    # ------------------------------------------------------------------ #
    # Estado e exibição
    # ------------------------------------------------------------------ #
    def set_mirror(self, active: bool) -> None:
        """Sincroniza o check do botão "Espelhar" com o estado real."""
        self.mirror_btn.blockSignals(True)
        self.mirror_btn.setChecked(bool(active))
        self.mirror_btn.blockSignals(False)

    # ------------------------------------------------------------------ #
    # Abrir/fechar com animação saindo do ícone da bandeja
    # ------------------------------------------------------------------ #
    def _anchor_point(self) -> QPoint:
        """Ponto de origem: o ícone da bandeja.

        A geometria do ícone nem sempre está disponível (GNOME/AppIndicator);
        nesse caso usa o cursor — que está sobre o ícone (ou sobre o menu que
        acabou de abrir nele) no momento do clique.
        """
        tray = getattr(self._win, "_tray", None)
        if tray is not None:
            r = tray.geometry()
            if r.isValid() and not r.isEmpty():
                return r.center()
        return QCursor.pos()

    def _target_pos(self, anchor: QPoint) -> QPoint:
        """Posição final: encostado no ícone (barras de cima ou de baixo)."""
        scr = (QGuiApplication.screenAt(anchor) or self._win.screen()
               or QGuiApplication.primaryScreen())
        avail = scr.availableGeometry()
        w, h = self.width(), self.height()
        if anchor.x() > avail.left() + avail.width() * 3 // 4:
            x = avail.right() - w - 12      # bandeja no canto direito
        else:
            x = anchor.x() - w // 2         # centralizado sob o ícone
        x = max(avail.left() + 8, min(x, avail.right() - w - 8))
        below = anchor.y() < avail.top() + avail.height() // 2
        y = anchor.y() + 12 if below else anchor.y() - h - 12
        y = max(avail.top() + 8, min(y, avail.bottom() - h - 8))
        return QPoint(x, y)

    def _stop_anim(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def show_panel(self) -> None:
        """Mostra o painel com animação, deslizando do ícone da bandeja."""
        self._stop_anim()
        self.adjustSize()
        anchor = self._anchor_point()
        target = QRect(self.pos() if self._moved
                       else self._target_pos(anchor), self.size())
        below = anchor.y() < target.center().y()
        # deslize curto na direção do ícone + fade
        start = target.translated(0, -30 if below else 30)
        self.setGeometry(start)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        grp = QParallelAnimationGroup(self)
        ga = QPropertyAnimation(self, b"geometry", grp)
        ga.setDuration(self.DUR_IN)
        ga.setEasingCurve(QEasingCurve.Type.OutCubic)
        ga.setStartValue(start)
        ga.setEndValue(target)
        oa = QPropertyAnimation(self, b"windowOpacity", grp)
        oa.setDuration(self.DUR_IN - 70)
        oa.setEasingCurve(QEasingCurve.Type.OutCubic)
        oa.setStartValue(0.0)
        oa.setEndValue(1.0)
        grp.addAnimation(ga)
        grp.addAnimation(oa)
        self._anim = grp
        grp.start()

    def hide_panel(self) -> None:
        """Fecha o painel com saída rápida (mesma direção da abertura)."""
        if not self.isVisible():
            return
        self._stop_anim()
        anchor = self._anchor_point()
        current = self.geometry()
        below = anchor.y() < current.center().y()
        end = current.translated(0, -30 if below else 30)
        grp = QParallelAnimationGroup(self)
        ga = QPropertyAnimation(self, b"geometry", grp)
        ga.setDuration(self.DUR_OUT)
        ga.setEasingCurve(QEasingCurve.Type.InCubic)
        ga.setStartValue(current)
        ga.setEndValue(end)
        oa = QPropertyAnimation(self, b"windowOpacity", grp)
        oa.setDuration(self.DUR_OUT)
        oa.setStartValue(self.windowOpacity())
        oa.setEndValue(0.0)
        grp.addAnimation(ga)
        grp.addAnimation(oa)
        grp.finished.connect(self.hide)
        self._anim = grp
        grp.start()

    # ------------------------------------------------------------------ #
    # Janela sem moldura: arrastar e fechar com Esc
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag = (ev.globalPosition().toPoint()
                          - self.frameGeometry().topLeft())
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag)
            self._moved = True
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = None
        super().mouseReleaseEvent(ev)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self.hide_panel()
            return
        super().keyPressEvent(ev)
