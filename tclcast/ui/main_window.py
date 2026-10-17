"""GUI do tclcast em PySide6 (v4 — layout com ícones + fila de reprodução).

Recursos:
- Todos os botões com ícones SVG próprios (sem dependências externas)
- Controle remoto: direcionais, OK, Home/Back/Menu rápido/Source, volume,
  power (com confirmação) e envio de texto para a TV
- Player: play/pause, parar, ±10 s, faixa anterior/próxima e barra de seek
- Fila de reprodução local com avanço automático ao fim de cada faixa
- Captura de tela da TV (pasta screenshots/), apps da TV (popup)
- Espelhamento de tela do PC, busca de TVs, IP manual, reconexão automática

Mantém as correções da v3:
- Heartbeat (cmd 150) para evitar "pipe broken" (errno 32)
- Detecção TCL priorizada sobre DLNA (verifica TCP 6553)
- Busca incremental com limpeza de lista
- Captura de tela thread-safe (Signal/Slot)
- Persistência do último IP + reconexão automática
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time

from PySide6.QtCore import (QSize, QStandardPaths, QThread, QObject, QTimer,
                            QUrl, Qt, Signal)
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSlider, QSystemTrayIcon, QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import autostart
from ..core.device import CastDevice, DeviceKind
from ..core.discovery import DiscoveryManager, _is_tcl, is_host_alive
from ..core.media_server import MediaServer, get_local_ip
from ..core.screen_mirror import ScreenMirror
from ..protocol import tcl_const
from . import icons
from .theme import ACCENT, APP_QSS, DANGER_ICON, ON_ACCENT, TEXT_DIM

log = logging.getLogger(__name__)

CONFIG_FILE = os.path.expanduser("~/.config/tclcast/config.json")

#: Nome do socket local que garante uma instância por usuário
#: (TCLCAST_SOCKET permite usar um socket separado em testes)
SINGLE_INSTANCE_KEY = os.environ.get("TCLCAST_SOCKET", f"tclcast-{os.getuid()}")

#: Legendas dos estados do player (respostas "145>>{STATE}" da TV)
STATE_LABELS = {
    "PLAYING": "Tocando",
    "PAUSED_PLAYBACK": "Pausado",
    "STOPPED": "Parado",
    "COMPLETED": "Concluído",
    "TRANSITIONING": "Carregando…",
    "ERROR_OCCURRED": "Erro ao reproduzir",
    "NO_MEDIA": "Sem mídia",
    "PLAYER_EXIT": "Player encerrado",
}

#: Filtro do seletor de arquivos (multi-seleção = fila de reprodução)
MEDIA_FILTER = ("Mídia (*.mp4 *.mkv *.webm *.avi *.mov *.m4v *.ts "
                "*.mp3 *.m4a *.aac *.flac *.wav *.ogg *.jpg *.jpeg *.png)"
                ";;Vídeos (*.mp4 *.mkv *.webm *.avi *.mov *.m4v *.ts)"
                ";;Todos os arquivos (*)")

_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def media_type_for(path: str) -> str:
    """Tipo de mídia (cmd 133) conforme a extensão do arquivo."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _AUDIO_EXTS:
        return tcl_const.TYPE_AUDIO
    if ext in _IMAGE_EXTS:
        return tcl_const.TYPE_IMAGE
    return tcl_const.TYPE_VIDEO


# --------------------------------------------------------------------------- #
# Fábricas de botões com ícone
# --------------------------------------------------------------------------- #
def icon_button(name: str, tooltip: str = "", *,
                color: str = icons.DEFAULT_COLOR, size: int = 46,
                icon_px: int = 22, obj: str = "iconBtn") -> QPushButton:
    """Botão quadrado só com ícone."""
    b = QPushButton()
    b.setIcon(icons.make_icon(name, color, icon_px))
    b.setIconSize(QSize(icon_px, icon_px))
    b.setFixedSize(size, size)
    b.setToolTip(tooltip)
    b.setObjectName(obj)
    b.setCursor(Qt.PointingHandCursor)
    return b


def text_icon_button(name: str, text: str, tooltip: str = "", *,
                     color: str = icons.DEFAULT_COLOR, icon_px: int = 17,
                     obj: str = "") -> QPushButton:
    """Botão com ícone + texto."""
    b = QPushButton(text)
    b.setIcon(icons.make_icon(name, color, icon_px))
    b.setIconSize(QSize(icon_px, icon_px))
    if tooltip:
        b.setToolTip(tooltip)
    if obj:
        b.setObjectName(obj)
    b.setCursor(Qt.PointingHandCursor)
    return b


class CaptureSignals(QObject):
    """Signals thread-safe para captura de tela da TV."""
    done = Signal(str, str)   # status, caminho


class PlayerSignals(QObject):
    """Signals thread-safe para o polling do player da TV."""
    status = Signal(dict)


class AppSignals(QObject):
    """Signals thread-safe para o diálogo de apps da TV."""
    apps = Signal(list)
    storage = Signal(dict)


class ReconnectSignals(QObject):
    """Signals thread-safe para o resultado da reconexão automática."""
    done = Signal(object, bool)   # CastDevice | None, sucesso?
    probe = Signal(str, bool, bool)  # ip, é TCL?, host no ar? (probe ao abrir)


class ScanWorker(QThread):
    """Varredura em background; emite cada dispositivo assim que acha."""
    found = Signal(object)
    finished = Signal()

    def __init__(self, discovery: DiscoveryManager):
        super().__init__()
        self.discovery = discovery

    def run(self):
        self.discovery._scan_all()
        self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("tclcast — casting para TV TCL")
        self.setWindowIcon(icons.tv_window_icon())
        self.resize(1060, 700)
        self.setMinimumSize(960, 620)

        # aplica o tema na aplicação inteira (uma vez)
        app = QApplication.instance()
        if app is not None and not app.styleSheet():
            app.setStyleSheet(APP_QSS)

        self.discovery = DiscoveryManager()
        self.media_server = MediaServer()
        self.current: CastDevice | None = None
        self.scan_worker: ScanWorker | None = None
        self.mirror: ScreenMirror | None = None
        self._mirror_wait: QTimer | None = None
        self._mirror_wait_tries = 0
        self._known_ips: set[str] = set()
        self._capture_signals = CaptureSignals()
        self._capture_signals.done.connect(self._emit_capture_done)
        self._player_signals = PlayerSignals()
        self._player_signals.status.connect(self._on_player_status)
        self._recon_signals = ReconnectSignals()
        self._recon_signals.done.connect(self._on_reconnect_done)
        self._recon_signals.probe.connect(self._on_probe_done)
        self._reconnecting = False
        self._player_inflight = False
        self._is_playing = False

        # --- bandeja do sistema ---
        self._tray: QSystemTrayIcon | None = None
        self._tray_hint_shown = False
        self._force_quit = False
        self._tray_on_close = False   # preferência (config.json)
        self._quick_remote = None     # painel de controle rápido (bandeja)
        self._last_capture_folder: str | None = None

        # --- fila de reprodução (playlist local gerenciada pelo PC) ---
        self.playlist: list[str] = []
        self.playlist_idx = -1
        self._play_started_at = 0.0
        self._near_end = False
        self._end_seen_at = 0.0
        self._user_stopped = False

        self._build_ui()
        self._setup_tray()
        self.media_server.start()

        # Mantém os atalhos (autostart/menu) apontando para o executável atual
        try:
            autostart.refresh_shortcuts()
        except OSError:
            pass

        # Vigia a conexão a cada 2s; se cair, reconecta sozinho
        self._watchdog = QTimer(self)
        self._watchdog.timeout.connect(self._check_connection)
        self._watchdog.start(2000)

        # Estado do player (posição/duração) — só com TV TCL conectada
        self._player_timer = QTimer(self)
        self._player_timer.timeout.connect(self._poll_player)
        self._player_timer.start(2000)

        # Reconexão automática ao último IP usado
        self._load_config()
        if self._last_ip:
            self._try_reconnect(self._last_ip)

    # ------------------------------------------------------------------ #
    # Bandeja do sistema (tray)
    # ------------------------------------------------------------------ #
    def has_tray(self) -> bool:
        return self._tray is not None

    def _setup_tray(self) -> None:
        """Cria o ícone da bandeja.

        O menu é curto de propósito — os comandos ficam no painel de
        "Controle rápido" (formato de controle), aberto pelo clique no ícone
        ou pelo primeiro item do menu.
        """
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("bandeja do sistema indisponível — janela normal")
            return
        tray = QSystemTrayIcon(icons.tv_window_icon(), self)
        tray.setToolTip("tclcast — clique para abrir o controle rápido")
        menu = QMenu(self)
        act_quick = menu.addAction(icons.make_icon("remote", ACCENT),
                                   "Controle rápido")
        act_quick.triggered.connect(self._toggle_quick_remote)
        act_open = menu.addAction(icons.make_icon("tv"),
                                  "Abrir o app completo")
        act_open.triggered.connect(self._show_window)
        menu.addSeparator()
        act_cfg = menu.addAction(icons.make_icon("gear"), "Configurações…")
        act_cfg.triggered.connect(self._open_settings)
        act_quit = menu.addAction(icons.make_icon("power", DANGER_ICON),
                                  "Sair")
        act_quit.triggered.connect(self._quit_from_tray)

        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        tray.messageClicked.connect(self._on_tray_message_click)
        tray.show()
        self._tray = tray

    def _show_window(self) -> None:
        """Mostra e foca a janela principal (usado pela bandeja)."""
        if self._quick_remote is not None and self._quick_remote.isVisible():
            self._quick_remote.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _toggle_quick_remote(self) -> None:
        """Mostra/oculta o painel de controle rápido (formato de controle)."""
        if self._quick_remote is None:
            from .quick_remote import QuickRemote
            self._quick_remote = QuickRemote(self)
        qr = self._quick_remote
        if qr.isVisible():
            qr.hide_panel()
        else:
            qr.show_panel()

    def _tray_activated(self, reason) -> None:
        # Clique simples: painel de controle rápido. Duplo: janela completa.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_quick_remote()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _on_tray_message_click(self) -> None:
        """Clique na notificação: abre a pasta da última captura."""
        if self._last_capture_folder:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(self._last_capture_folder))

    def _sync_tray_mirror(self) -> None:
        """Mantém o botão "Espelhar" do painel rápido igual ao estado real."""
        active = bool(self.mirror and self.mirror.running)
        if self._quick_remote is not None:
            self._quick_remote.set_mirror(active)

    def _quit_from_tray(self) -> None:
        self._force_quit = True
        self.close()          # closeEvent faz a limpeza e fecha a janela
        # Fechar uma janela OCULTA não encerra o loop do Qt (lastWindowClosed
        # só conta janelas visíveis) — o quit() garante o fim do processo,
        # inclusive no modo --tray (autostart).
        QApplication.quit()

    def _notify(self, title: str, msg: str) -> None:
        """Notificação na bandeja (apenas quando a janela está oculta)."""
        if (self._tray is not None and not self._force_quit
                and not self.isVisible()):
            self._tray.showMessage(title, msg,
                                   QSystemTrayIcon.MessageIcon.Information,
                                   4000)

    def _open_settings(self) -> None:
        SettingsDialog(self).exec()

    # ------------------------------------------------------------------ #
    # Persistência
    # ------------------------------------------------------------------ #
    def _load_config(self) -> None:
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            self._last_ip = cfg.get("last_ip", "")
            self._last_name = cfg.get("last_name", "")
            self._tray_on_close = bool(cfg.get("tray_on_close", False))
            if self._last_ip:
                self.manual_ip.setText(self._last_ip)
        except (FileNotFoundError, json.JSONDecodeError):
            self._last_ip = ""
            self._last_name = ""
            self._tray_on_close = False

    def _save_config(self, ip: str, name: str = "") -> None:
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump({"last_ip": ip, "last_name": name,
                           "tray_on_close": self._tray_on_close}, f)
        except OSError:
            pass

    def _set_tray_on_close(self, enabled: bool) -> None:
        """Preferência: o X da janela esconde na bandeja em vez de sair."""
        self._tray_on_close = bool(enabled)
        self._save_config(self._last_ip, self._last_name)

    def _try_reconnect(self, ip: str) -> None:
        """Tenta reconectar ao último IP em background (ao abrir o app)."""
        def _do():
            try:
                is_tcl = _is_tcl(ip)
            except Exception:
                is_tcl = False
            # Sem TCL: distingue "TV desligada" de "host vivo não-TCL"
            alive = is_tcl or is_host_alive(ip)
            self._recon_signals.probe.emit(ip, is_tcl, alive)

        threading.Thread(target=_do, daemon=True).start()

    def _retry_if_idle(self, ip: str) -> None:
        """Nova tentativa de reconexão, só se ainda nada estiver conectado."""
        if self.current is not None and self.current.connected:
            return  # já voltou (ou o usuário conectou em outro dispositivo)
        if self._last_ip != ip:
            return  # o usuário mudou de dispositivo nesse meio-tempo
        self.status.setText(f"Tentando reconectar à TV {ip}…")
        self._try_reconnect(ip)

    def _on_probe_done(self, ip: str, is_tcl: bool, alive: bool) -> None:
        """Executado na thread principal via signal: cria device e conecta."""
        name = self._last_name or ip
        if is_tcl:
            dev = CastDevice(name, ip, DeviceKind.TCL)
            self._on_device_found(dev)
            self.status.setText(f"Reconectando à última TV: {name} ({ip})…")
        elif alive:
            # Host no ar mas sem protocolo TCL (ex.: DHCP mudou o IP)
            dev = CastDevice(name, ip, DeviceKind.DLNA)
            self._on_device_found(dev)
            return  # DLNA é stateless; nada a conectar
        else:
            # TV desligada/off: NÃO adiciona à lista (nada foi encontrado)
            # e tenta de novo em 20s — quando ligar, conecta sozinha.
            self.status.setText(
                f"Última TV {name} ({ip}) parece desligada — "
                f"nova tentativa em 20 s…")
            self.conn_label.setText("Nenhum dispositivo conectado")
            QTimer.singleShot(20000, lambda: self._retry_if_idle(ip))
            return
        # Conecta em background (evita travar a UI) — só para TCL
        self._reconnecting = True

        def _do():
            try:
                dev.connect()
                ok = dev.connected
            except Exception as e:
                log.info("reconexão inicial falhou: %s", e)
                ok = False
            self._recon_signals.done.emit(dev if ok else None, ok)
        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Watchdog: detecta queda e reconecta sozinho
    # ------------------------------------------------------------------ #
    def _check_connection(self) -> None:
        """Chamado a cada 2s. Se a conexão TCL caiu, inicia reconexão."""
        self._sync_tray_mirror()
        if self._reconnecting or self.current is None:
            return
        if self.current.kind != DeviceKind.TCL:
            return
        tcl = self.current._tcl
        if self.current.connected and tcl is not None and tcl._running:
            return  # tudo bem
        # Caiu! Reconectar em background.
        self._reconnecting = True
        ip = self.current.ip
        self.conn_label.setText(f"Conexão perdida — reconectando à {ip}…")
        self.status.setText("Conexão perdida, tentando reconectar…")
        threading.Thread(target=self._reconnect_worker, args=(ip,),
                         daemon=True).start()

    def _reconnect_worker(self, ip: str, attempts: int = 30) -> None:
        """Tenta reconectar ao IP; a cada 5s; até `attempts` vezes."""
        name = self.current.name if self.current else ip
        dev_ok: CastDevice | None = None
        for attempt in range(1, attempts + 1):
            try:
                dev = CastDevice(name, ip, DeviceKind.TCL)
                dev.connect()
                if dev.connected and dev._tcl and dev._tcl._running:
                    dev_ok = dev
                    log.info("reconectado a %s (tentativa %d)", ip, attempt)
                    break
            except Exception as e:
                log.debug("tentativa %d falhou: %s", attempt, e)
            time.sleep(5)
        self._recon_signals.done.emit(dev_ok, dev_ok is not None)

    def _on_reconnect_done(self, dev: CastDevice | None, ok: bool) -> None:
        self._reconnecting = False
        old = self.current
        if ok and dev is not None:
            self.current = dev
            if old is not None and old is not dev:
                try:
                    old.disconnect()
                except Exception:
                    pass
            self.conn_label.setText(f"Conectado: {dev.name} ({dev.ip})")
            self.status.setText(f"Reconectado a {dev.name}.")
            self._select_in_list(dev)
        else:
            self.conn_label.setText(
                "Conexão perdida — clique no dispositivo para tentar de novo")
            self.status.setText(
                f"Não consegui reconectar a {old.ip if old else '?'}. TV ligada?")
            # Reconexão inicial falhou (TV ligando? DHCP mudou?): tenta de novo
            ip = old.ip if old is not None else self._last_ip
            if ip and (self.current is None or not self.current.connected):
                self.conn_label.setText(
                    f"Sem conexão — nova tentativa à {ip} em 20 s…")
                QTimer.singleShot(20000, lambda: self._retry_if_idle(ip))

    def _select_in_list(self, dev: CastDevice) -> None:
        """Marca o device como selecionado na lista, se estiver nela."""
        for i in range(self.device_list.count()):
            item = self.device_list.item(i)
            if item.data(Qt.UserRole) is dev:
                self.device_list.setCurrentItem(item)
                return

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ============================ ESQUERDA ============================ #
        left = QVBoxLayout()
        left.setSpacing(12)

        # --- cabeçalho ---
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icons.make_icon("tv", ACCENT, 30).pixmap(30, 30))
        tl = QVBoxLayout()
        tl.setSpacing(0)
        title = QLabel("tclcast")
        title.setObjectName("title")
        sub = QLabel("Casting para TVs TCL — Linux")
        sub.setObjectName("subtitle")
        tl.addWidget(title)
        tl.addWidget(sub)
        header.addWidget(logo)
        header.addLayout(tl)
        header.addStretch(1)
        left.addLayout(header)

        self.conn_label = QLabel("Nenhum dispositivo conectado")
        self.conn_label.setObjectName("pill")
        self.conn_label.setWordWrap(True)
        left.addWidget(self.conn_label)

        # --- TVs encontradas ---
        dev_box = QGroupBox("TVS ENCONTRADAS")
        dv = QVBoxLayout(dev_box)
        dv.setSpacing(8)
        self.device_list = QListWidget()
        self.device_list.setMinimumHeight(120)
        self.device_list.itemClicked.connect(self._select_device)
        dv.addWidget(self.device_list, 1)
        self.scan_btn = text_icon_button(
            "search", "Procurar TVs", "Varredura por SSDP + TCP 6553")
        self.scan_btn.clicked.connect(self._scan)
        dv.addWidget(self.scan_btn)
        ip_row = QHBoxLayout()
        ip_row.setSpacing(6)
        self.manual_ip = QLineEdit()
        self.manual_ip.setPlaceholderText("IP manual (ex.: 192.168.0.10)")
        self.manual_ip.returnPressed.connect(self._add_manual)
        self.add_btn = icon_button("plus", "Adicionar este IP à lista")
        self.add_btn.clicked.connect(self._add_manual)
        ip_row.addWidget(self.manual_ip, 1)
        ip_row.addWidget(self.add_btn)
        dv.addLayout(ip_row)
        left.addWidget(dev_box)

        # --- ferramentas ---
        tools = QGroupBox("FERRAMENTAS")
        tlv = QVBoxLayout(tools)
        tlv.setSpacing(8)
        self.capture_btn = text_icon_button(
            "camera", "Capturar tela da TV",
            "Salva um JPG na pasta screenshots/")
        self.capture_btn.clicked.connect(self._capture_tv_screen)
        self.open_folder_btn = text_icon_button(
            "folder", "Abrir pasta de capturas",
            "Abre a pasta screenshots/ no gerenciador de arquivos")
        self.open_folder_btn.clicked.connect(self._open_screenshots_folder)
        self.apps_btn = text_icon_button(
            "apps", "Apps e armazenamento…",
            "Lista, abre e desinstala apps da TV (popup)")
        self.apps_btn.clicked.connect(self._open_apps_dialog)
        self.mirror_btn = text_icon_button(
            "cast", "Espelhar tela do PC",
            "Transmite a tela deste PC para a TV (ffmpeg/HLS)")
        self.mirror_btn.clicked.connect(self._toggle_mirror)
        self.settings_btn = text_icon_button(
            "gear", "Configurações…",
            "Bandeja, início automático e atalho no menu de aplicativos")
        self.settings_btn.clicked.connect(self._open_settings)
        tlv.addWidget(self.capture_btn)
        tlv.addWidget(self.open_folder_btn)
        tlv.addWidget(self.apps_btn)
        tlv.addWidget(self.mirror_btn)
        tlv.addWidget(self.settings_btn)
        left.addWidget(tools)

        self.status = QLabel("Clique em “Procurar TVs” ou digite um IP.")
        self.status.setObjectName("dim")
        self.status.setWordWrap(True)
        left.addWidget(self.status)
        left.addStretch(1)

        left_wrap = QWidget()
        left_wrap.setFixedWidth(330)
        left_wrap.setLayout(left)
        root.addWidget(left_wrap)

        # ============================ DIREITA ============================= #
        right = QVBoxLayout()
        right.setSpacing(14)

        # --- controle remoto ---
        remote_box = QGroupBox("CONTROLE REMOTO")
        rl = QHBoxLayout(remote_box)
        rl.setSpacing(20)

        # d-pad 3×3
        dpad = QGridLayout()
        dpad.setSpacing(6)
        up = icon_button("up", "Cima (↑)")
        down = icon_button("down", "Baixo (↓)")
        leftk = icon_button("left", "Esquerda (←)")
        rightk = icon_button("right", "Direita (→)")
        self.ok_btn = icon_button("ok", "OK (confirmar)", size=54,
                                  icon_px=26, obj="okBtn")
        self.ok_btn.setIcon(icons.make_icon("ok", ON_ACCENT, 26))
        up.clicked.connect(lambda: self._key("UP"))
        down.clicked.connect(lambda: self._key("DOWN"))
        leftk.clicked.connect(lambda: self._key("LEFT"))
        rightk.clicked.connect(lambda: self._key("RIGHT"))
        self.ok_btn.clicked.connect(lambda: self._key("OK"))
        dpad.addWidget(up, 0, 1)
        dpad.addWidget(leftk, 1, 0)
        dpad.addWidget(self.ok_btn, 1, 1)
        dpad.addWidget(rightk, 1, 2)
        dpad.addWidget(down, 2, 1)
        rl.addLayout(dpad)

        # coluna de teclas
        col = QVBoxLayout()
        col.setSpacing(8)
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        home = icon_button("home", "Home (início da TV)")
        back = icon_button("back", "Voltar")
        menu = icon_button(
            "menu", "Menu rápido da TV\n(brilho, imagem, som, conexão…)")
        source = icon_button("source", "Fonte de entrada (HDMI…)")
        power = icon_button("power", "Desligar/ligar a TV (pede confirmação)",
                            color=DANGER_ICON, obj="powerBtn")
        home.clicked.connect(lambda: self._key("MENU"))
        back.clicked.connect(lambda: self._key("BACK"))
        menu.clicked.connect(lambda: self._key("SUBMENU"))
        source.clicked.connect(lambda: self._key("SOURCE"))
        power.clicked.connect(self._confirm_power)
        nav_row.addWidget(home)
        nav_row.addWidget(back)
        nav_row.addWidget(menu)
        nav_row.addWidget(source)
        nav_row.addSpacing(8)
        nav_row.addWidget(power)
        nav_row.addStretch(1)
        col.addLayout(nav_row)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(6)
        v_down = icon_button("vol_down", "Diminuir volume")
        v_up = icon_button("vol_up", "Aumentar volume")
        mute = icon_button("mute", "Mudo (liga/desliga)")
        ch_down = icon_button("ch_down", "Canal anterior")
        ch_up = icon_button("ch_up", "Próximo canal")
        v_down.clicked.connect(lambda: self._key("VOL_DOWN"))
        v_up.clicked.connect(lambda: self._key("VOL_UP"))
        mute.clicked.connect(lambda: self._key("MUTE"))
        ch_down.clicked.connect(lambda: self._key("CH_DOWN"))
        ch_up.clicked.connect(lambda: self._key("CH_UP"))
        vol_row.addWidget(v_down)
        vol_row.addWidget(v_up)
        vol_row.addWidget(mute)
        vol_row.addSpacing(8)
        vol_row.addWidget(ch_down)
        vol_row.addWidget(ch_up)
        vol_row.addStretch(1)
        col.addLayout(vol_row)

        # texto PC -> TV
        txt_row = QHBoxLayout()
        txt_row.setSpacing(6)
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText(
            "Digitar na TV… (busca, login, texto livre)")
        self.text_input.returnPressed.connect(self._send_text)
        send_btn = icon_button(
            "send", "Enviar o texto digitado para a TV (cmd 155)")
        send_btn.clicked.connect(self._send_text)
        txt_row.addWidget(self.text_input, 1)
        txt_row.addWidget(send_btn)
        col.addLayout(txt_row)
        col.addStretch(1)
        rl.addLayout(col, 1)
        right.addWidget(remote_box)

        # --- reprodução ---
        player_box = QGroupBox("REPRODUÇÃO NA TV")
        pl = QVBoxLayout(player_box)
        pl.setSpacing(10)
        self.state_label = QLabel("— Sem reprodução ativa")
        self.state_label.setObjectName("stateLabel")
        self.state_label.setAlignment(Qt.AlignCenter)
        pl.addWidget(self.state_label)

        prow = QHBoxLayout()
        prow.setSpacing(8)
        self.pos_label = QLabel("--:--")
        self.pos_label.setObjectName("timeLabel")
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, 1000)
        self.progress.setEnabled(False)
        self.progress.sliderReleased.connect(self._seek_to_slider)
        self.dur_label = QLabel("--:--")
        self.dur_label.setObjectName("timeLabel")
        prow.addWidget(self.pos_label)
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.dur_label)
        pl.addLayout(prow)

        trow = QHBoxLayout()
        trow.setSpacing(8)
        self.prev_btn = icon_button("prev", "Faixa anterior da fila")
        self.back10_btn = icon_button("seek_back", "Voltar 10 segundos")
        self.play_pause_btn = icon_button("play", "Reproduzir / pausar",
                                          size=58, icon_px=28, obj="playBtn")
        self.play_pause_btn.setIcon(icons.make_icon("play", ON_ACCENT, 28))
        self.fwd10_btn = icon_button("seek_fwd", "Avançar 10 segundos")
        self.next_btn = icon_button("next", "Próxima faixa da fila")
        self.stop_btn = icon_button("stop", "Parar reprodução",
                                    color=DANGER_ICON)
        self.prev_btn.clicked.connect(self._media_prev)
        self.back10_btn.clicked.connect(lambda: self._nudge(-10))
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        self.fwd10_btn.clicked.connect(lambda: self._nudge(10))
        self.next_btn.clicked.connect(self._media_next)
        self.stop_btn.clicked.connect(self._stop_media)
        trow.addStretch(1)
        trow.addWidget(self.prev_btn)
        trow.addWidget(self.back10_btn)
        trow.addWidget(self.play_pause_btn)
        trow.addWidget(self.fwd10_btn)
        trow.addWidget(self.next_btn)
        trow.addSpacing(14)
        trow.addWidget(self.stop_btn)
        trow.addStretch(1)
        pl.addLayout(trow)
        right.addWidget(player_box)

        # --- mídia local (fila) ---
        media_box = QGroupBox("MÍDIA LOCAL — FILA DE REPRODUÇÃO")
        ml = QVBoxLayout(media_box)
        ml.setSpacing(8)
        send_row = QHBoxLayout()
        send_row.setSpacing(8)
        self.send_btn = text_icon_button(
            "files", "Enviar arquivos…",
            "Escolha um ou vários arquivos: eles tocam em sequência na TV",
            color=ON_ACCENT, icon_px=18, obj="accentBtn")
        self.send_btn.clicked.connect(self._send_files)
        self.clear_queue_btn = text_icon_button(
            "trash", "Limpar", "Esvazia a fila de reprodução",
            obj="ghostBtn")
        self.clear_queue_btn.clicked.connect(self._clear_queue)
        send_row.addWidget(self.send_btn, 1)
        send_row.addWidget(self.clear_queue_btn)
        ml.addLayout(send_row)
        self.queue_label = QLabel("Fila vazia — escolha um ou mais arquivos.")
        self.queue_label.setObjectName("dim")
        self.queue_label.setWordWrap(True)
        ml.addWidget(self.queue_label)
        right.addWidget(media_box)

        right.addStretch(1)
        root.addLayout(right, 1)

        self._update_queue_ui()

    # ------------------------------------------------------------------ #
    # Busca de dispositivos
    # ------------------------------------------------------------------ #
    def _scan(self):
        """Nova busca: limpa a lista e os conhecidos."""
        self.device_list.clear()
        self._known_ips.clear()
        self.status.setText("Procurando…")
        self.scan_btn.setEnabled(False)

        self.discovery.on_device = lambda d: self._on_device_found(d)
        self.scan_worker = ScanWorker(self.discovery)
        self.scan_worker.found.connect(self._on_device_found)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.start()

    def _on_device_found(self, dev: CastDevice):
        if dev.ip in self._known_ips:
            return
        self._known_ips.add(dev.ip)
        item = QListWidgetItem(f"{dev.name}   ·   {dev.ip}")
        cor = ACCENT if dev.kind == DeviceKind.TCL else TEXT_DIM
        item.setIcon(icons.make_icon("tv", cor, 20))
        item.setData(Qt.UserRole, dev)
        item.setToolTip(f"{dev.name}\n{dev.ip}\nProtocolo: {dev.kind.value}")
        self.device_list.addItem(item)
        self.status.setText(f"Encontrado: {dev.name} ({dev.ip})")

    def _on_scan_finished(self):
        self.scan_btn.setEnabled(True)
        n = self.device_list.count()
        self.status.setText(f"{n} dispositivo(s) encontrado(s).")
        self.scan_worker = None

    def _add_manual(self):
        ip = self.manual_ip.text().strip()
        if not ip:
            return
        if ip in self._known_ips:
            self.status.setText(f"{ip} já está na lista.")
            return
        # Detecta se é TCL ou DLNA
        if _is_tcl(ip):
            dev = self.discovery.add_manual(ip, DeviceKind.TCL, "Sala de estar")
        else:
            dev = self.discovery.add_manual(ip, DeviceKind.DLNA)
        self._on_device_found(dev)

    def _select_device(self, item: QListWidgetItem):
        dev: CastDevice = item.data(Qt.UserRole)
        if self.current and self.current is not dev:
            try:
                self.current.disconnect()
            except Exception:
                pass
        try:
            dev.connect()
            self.current = dev
            self._save_config(dev.ip, dev.name)
            self.conn_label.setText(f"Conectado: {dev.name} ({dev.ip})")
            self.status.setText(f"Conectado a {dev.name}.")
        except Exception as e:
            QMessageBox.warning(self, "Erro de conexão",
                                f"Não foi possível conectar a {dev.ip}:\n{e}")
            self.status.setText(f"Falha ao conectar: {e}")

    # ------------------------------------------------------------------ #
    # Teclas do remoto
    # ------------------------------------------------------------------ #
    def _key(self, key: str):
        if not self._require():
            return
        try:
            self.current.send_key(key)
        except Exception as e:
            QMessageBox.warning(self, "Erro", str(e))

    def _confirm_power(self):
        """Power só com confirmação (evita desligar a TV sem querer)."""
        if not self._require():
            return
        resp = QMessageBox.question(
            self, "Energia da TV",
            "Desligar (ou ligar) a TV agora?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp == QMessageBox.Yes:
            self._key("POWER")

    def _send_text(self):
        """Envia o texto digitado no campo para a TV (cmd 155)."""
        text = self.text_input.text().strip()
        if not text or not self._require():
            return
        try:
            self.current.send_text(text)
            self.status.setText(f"Texto enviado à TV: {text!r}")
            self.text_input.clear()
        except Exception as e:
            QMessageBox.warning(self, "Erro", str(e))

    # ------------------------------------------------------------------ #
    # Player da TV: polling de estado + controles (cmds 139/141/142/...)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _fmt_ms(ms: int) -> str:
        s = max(0, int(ms)) // 1000
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    def _poll_player(self) -> None:
        """A cada 2s pergunta o estado/posição do player (thread-safe)."""
        if (self._player_inflight or self._reconnecting or self.current is None
                or self.current.kind != DeviceKind.TCL):
            return
        tcl = self.current._tcl
        if not (self.current.connected and tcl and tcl._running):
            return
        self._player_inflight = True

        def _cb(status: dict) -> None:
            # Chamado de thread não-QT: só emite o signal
            self._player_signals.status.emit(status)

        self.current.query_player(_cb)

    def _on_player_status(self, st: dict) -> None:
        """Executado na thread principal via signal."""
        self._player_inflight = False
        if not st:
            return  # timeout/sem resposta: mantém o último estado
        state = st.get("state", "")
        pos = st.get("position_ms", None)
        dur = st.get("duration_ms", None)
        if state:
            self.state_label.setText(STATE_LABELS.get(state, state))
        if dur and dur > 0:
            self.progress.setRange(0, dur)
            self.dur_label.setText(self._fmt_ms(dur))
            if pos is not None:
                self.pos_label.setText(self._fmt_ms(pos))
                self.progress.blockSignals(True)
                self.progress.setValue(pos)
                self.progress.blockSignals(False)
            self.progress.setEnabled(True)
        else:
            # sem duração conhecida: barra indeterminada, sem seek
            self.progress.setRange(0, 0)
            self.dur_label.setText("--:--")
            self.pos_label.setText("--:--")
            self.progress.setEnabled(False)
            # TV ociosa também responde TRANSITIONING (sem player ativo):
            # nesse caso não mostramos "Carregando…" eternamente.
            if state == "TRANSITIONING":
                self.state_label.setText("— Sem reprodução ativa")

        # ícone do play/pause reflete o estado real
        self._is_playing = state == "PLAYING"
        self.play_pause_btn.setIcon(icons.make_icon(
            "pause" if self._is_playing else "play", ON_ACCENT, 28))

        # ---- fim de faixa → avança a fila automaticamente ---- #
        near_end = bool(dur and dur > 0 and pos is not None
                        and pos >= dur - 4000)
        if state == "COMPLETED":
            self._end_seen_at = time.time()
        elif (self._near_end and not dur and not pos
              and state in ("PLAYING", "TRANSITIONING", "STOPPED",
                            "PLAYER_EXIT")):
            # a TV zera duração/posição ao terminar (não manda COMPLETED)
            self._end_seen_at = time.time()
        self._near_end = near_end
        # decisão madura: só avança se não foi parada manual e já passou
        # tempo suficiente desde o play (evita o TRANSITIONING de boot)
        if (self._end_seen_at and not self._user_stopped
                and time.time() - self._play_started_at > 6
                and self.playlist_idx + 1 < len(self.playlist)):
            self._end_seen_at = 0.0
            self.playlist_idx += 1
            log.info("fim de faixa — tocando próxima da fila (%d/%d)",
                     self.playlist_idx + 1, len(self.playlist))
            self._play_current()
            self.status.setText("Faixa concluída — tocando a próxima…")

    def _seek_to_slider(self) -> None:
        if not self._require() or not self.progress.isEnabled():
            return
        try:
            self.current.seek(self.progress.value())
            self.status.setText(
                f"Seek: {self._fmt_ms(self.progress.value())}")
        except Exception as e:
            QMessageBox.warning(self, "Erro", str(e))

    def _nudge(self, delta_s: int) -> None:
        if not self._require():
            return
        # Se soubermos a posição atual, pula relativo a ela
        target = self.progress.value() + delta_s * 1000
        if self.progress.isEnabled():
            target = max(0, min(self.progress.maximum(), target))
        else:
            target = max(0, delta_s * 1000)
        try:
            self.current.seek(target)
            self.status.setText(
                f"{'+' if delta_s > 0 else ''}{delta_s}s → "
                f"{self._fmt_ms(target)}")
        except Exception as e:
            QMessageBox.warning(self, "Erro", str(e))

    def _toggle_play_pause(self) -> None:
        if not self._require():
            return
        try:
            if self._is_playing:
                self.current.pause()
                self._is_playing = False
                self.state_label.setText(STATE_LABELS["PAUSED_PLAYBACK"])
                self.play_pause_btn.setIcon(icons.make_icon("play", ON_ACCENT, 28))
            else:
                self.current.resume()
                self._is_playing = True
                self.state_label.setText(STATE_LABELS["PLAYING"])
                self.play_pause_btn.setIcon(icons.make_icon("pause", ON_ACCENT, 28))
        except Exception as e:
            QMessageBox.warning(self, "Erro", str(e))

    def _stop_media(self):
        if not self._require():
            return
        self._user_stopped = True   # parada manual: não avança a fila
        self._near_end = False
        self._end_seen_at = 0.0
        try:
            self.current.stop()
            self.state_label.setText(STATE_LABELS["STOPPED"])
        except Exception as e:
            QMessageBox.warning(self, "Erro", str(e))

    # ------------------------------------------------------------------ #
    # Fila de reprodução local (playlist gerenciada pelo PC)
    # ------------------------------------------------------------------ #
    def _send_files(self):
        """Escolhe 1+ arquivos e inicia a fila de reprodução."""
        if not self._require():
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Escolher mídia (vários arquivos tocam em sequência)",
            "", MEDIA_FILTER)
        if not files:
            return
        self.playlist = list(files)
        self.playlist_idx = 0
        self._play_current()

    def _play_current(self):
        """Serve e manda a faixa atual da fila para a TV."""
        if not (0 <= self.playlist_idx < len(self.playlist)):
            return
        if not self._require():
            return
        path = self.playlist[self.playlist_idx]
        try:
            url = self.media_server.serve_file(path)
            self.current.play_media(url, media_type_for(path),
                                    os.path.basename(path))
            self._play_started_at = time.time()
            self._near_end = False
            self._end_seen_at = 0.0
            self._user_stopped = False
            n = len(self.playlist)
            self.queue_label.setText(
                f"Reproduzindo {self.playlist_idx + 1} de {n}: "
                f"{os.path.basename(path)}")
            self.status.setText(f"Enviando: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.warning(self, "Erro", str(e))
        self._update_queue_ui()

    def _media_prev(self):
        if self.playlist_idx > 0:
            self.playlist_idx -= 1
            self._play_current()

    def _media_next(self):
        if self.playlist_idx < len(self.playlist) - 1:
            self.playlist_idx += 1
            self._play_current()

    def _clear_queue(self):
        self.playlist = []
        self.playlist_idx = -1
        self._update_queue_ui()
        self.status.setText("Fila de reprodução esvaziada.")

    def _update_queue_ui(self):
        n = len(self.playlist)
        self.clear_queue_btn.setEnabled(n > 0)
        self.prev_btn.setEnabled(n > 0 and self.playlist_idx > 0)
        self.next_btn.setEnabled(n > 0 and self.playlist_idx < n - 1)
        if n == 0:
            self.queue_label.setText(
                "Fila vazia — escolha um ou mais arquivos.")

    # ------------------------------------------------------------------ #
    # Capturas de tela (pasta screenshots/)
    # ------------------------------------------------------------------ #
    @property
    def _screenshots_dir(self) -> str:
        """Pasta das capturas.

        Rodando do código-fonte (dev), usa ``screenshots/`` do projeto;
        instalado (AppImage/.deb), usa a pasta de imagens do usuário
        (``~/Imagens/tclcast`` ou equivalente do sistema).
        """
        # __file__ = .../tclcast/tclcast/ui/main_window.py → sobe 3 níveis
        projeto = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        if os.path.isfile(os.path.join(projeto, "README.md")):
            return os.path.join(projeto, "screenshots")
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation)
        if not base:
            base = os.path.expanduser("~")
        return os.path.join(base, "tclcast")

    def _capture_tv_screen(self):
        if not self._require():
            return
        if self.current.kind != DeviceKind.TCL:
            QMessageBox.information(self, "Captura",
                                    "Captura de tela disponível apenas para TVs TCL.")
            return
        if not self.current._tcl:
            QMessageBox.warning(self, "Erro", "Conexão TCL não ativa.")
            return
        self.capture_btn.setEnabled(False)
        self.status.setText("Capturando tela da TV…")

        # Pasta de capturas (projeto em dev; ~/Imagens quando instalado)
        dest = self._screenshots_dir
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError:
            pass

        def _done(status: str, path: str):
            # Chamado de thread não-QT: só emite o signal (thread-safe)
            self._capture_signals.done.emit(status, path)

        self.current._tcl.capture_and_save(dest, _done)

    def _emit_capture_done(self, status: str, path: str):
        """Executado na thread principal via signal: mostra o resultado."""
        self.capture_btn.setEnabled(True)
        if status == "ok" and path:
            self.status.setText(f"Captura salva em: {path}")
            self._last_capture_folder = os.path.dirname(path)
            self._notify("Captura salva", os.path.basename(path))
            return
        msgs = {
            "unsupported": "TV não suporta captura de tela.",
            "copyright": "Conteúdo protegido por direitos autorais.",
            "storage_full": "Armazenamento da TV cheio.",
        }
        self.status.setText(msgs.get(status,
                                     f"Falha na captura: {path or status}"))
        self._notify("Captura falhou", self.status.text())

    def _open_screenshots_folder(self):
        dest = self._screenshots_dir
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(dest))
        self.status.setText(f"Pasta de capturas: {dest}")

    # ------------------------------------------------------------------ #
    # Apps da TV (popup): listar / abrir / desinstalar (cmd 223)
    # ------------------------------------------------------------------ #
    def _open_apps_dialog(self):
        if not self._require():
            return
        if self.current.kind != DeviceKind.TCL:
            QMessageBox.information(self, "Apps",
                                    "Disponível apenas para TVs TCL.")
            return
        dlg = AppsDialog(self.current, self)
        dlg.exec()

    # ------------------------------------------------------------------ #
    # Espelhamento de tela do PC (ffmpeg -> HLS -> URL m3u8)
    # ------------------------------------------------------------------ #
    def _toggle_mirror(self):
        if self.mirror and self.mirror.running:
            self._stop_mirror()
        else:
            self._start_mirror()

    def _start_mirror(self):
        if not self._require():
            return
        # Sessão Wayland: x11grab só vê janelas X11 (fundo preto); a captura
        # usa o portal do GNOME (PipeWire) — pede autorização só na 1ª vez.
        wayland = (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
                   or bool(os.environ.get("WAYLAND_DISPLAY")))
        if wayland:
            resp = QMessageBox.question(
                self, "Espelhamento em sessão Wayland",
                "No Wayland o tclcast captura a tela pelo portal do GNOME "
                "(PipeWire). Na primeira vez o GNOME mostra um diálogo — "
                "escolha a tela e clique em “Compartilhar”.\n\n"
                "Depois disso não pede autorização de novo.\n\nContinuar?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if resp != QMessageBox.Yes:
                self.status.setText("Espelhamento cancelado.")
                return
        ip = get_local_ip()
        outdir = os.path.join(tempfile.gettempdir(), "tclcast_mirror")
        self.mirror = ScreenMirror(outdir, ip, self.media_server.port)
        self.status.setText("Iniciando captura de tela…")
        ok = self.mirror.start()
        if not ok:
            QMessageBox.warning(
                self, "Erro",
                "Não foi possível iniciar a captura de tela.\n"
                + (self.mirror.error or
                   "Verifique se o ffmpeg está instalado e se há um display "
                   "ativo (X11 ou Wayland com portal do GNOME)."))
            self.mirror = None
            return
        if self.mirror.needs_authorization:
            # Wayland: o diálogo do GNOME pode demorar; acompanha sem travar
            # a interface (polling a cada 1s).
            self.mirror_btn.setText("Parar espelhamento")
            self.status.setText(
                "Aguardando autorização do GNOME: escolha a tela e clique em "
                "“Compartilhar” na janela que apareceu.")
            self._mirror_wait_tries = 0
            self._mirror_wait = QTimer(self)
            self._mirror_wait.setInterval(1000)
            self._mirror_wait.timeout.connect(self._mirror_wait_ready)
            self._mirror_wait.start()
            return
        self._mirror_go_live()

    def _mirror_wait_ready(self):
        """Polling do modo portal: vai ao ar quando houver segmentos HLS."""
        m = self.mirror
        if m is None:
            if self._mirror_wait:
                self._mirror_wait.stop()
                self._mirror_wait = None
            return
        self._mirror_wait_tries += 1
        if m.ready:
            self._mirror_wait.stop()
            self._mirror_wait = None
            self.status.setText("Captura autorizada — enviando para a TV…")
            self._mirror_go_live()
            return
        if not m.running:
            self._mirror_wait.stop()
            self._mirror_wait = None
            err = m.error or "o portal não autorizou o compartilhamento"
            self._stop_mirror()
            QMessageBox.warning(self, "Espelhamento",
                                "Não foi possível capturar a tela:\n" + err)
            return
        if self._mirror_wait_tries > 125:
            self._mirror_wait.stop()
            self._mirror_wait = None
            self._stop_mirror()
            QMessageBox.warning(
                self, "Espelhamento",
                "Tempo esgotado aguardando a autorização de tela do GNOME.")

    def _mirror_go_live(self):
        """Dirige a TV para o m3u8 do espelhamento (segmentos já prontos)."""
        if not self.mirror:
            return
        # Serve o diretório HLS (m3u8 + segmentos .ts) pelo servidor local
        self.media_server.set_base_dir(str(self.mirror.output_dir))
        try:
            # Tipo "Video" (TCLVideoPlayerProxy.play): player normal, SEM o
            # pré-roll de anúncio do tipo "m3u8" (que abre o player de vídeo
            # online da TCL, com anúncios).
            self.current.play_media(self.mirror.url, tcl_const.TYPE_VIDEO,
                                    "Espelhamento do PC")
            self.mirror_btn.setText("Parar espelhamento")
            self.status.setText("Espelhando tela do PC para a TV…")
            self._sync_tray_mirror()
            self._notify("tclcast", "Espelhando a tela do PC na TV.")
        except Exception as e:
            QMessageBox.warning(self, "Erro", str(e))
            self._stop_mirror()

    def _stop_mirror(self):
        was_running = bool(self.mirror and self.mirror.running)
        if self._mirror_wait:
            self._mirror_wait.stop()
            self._mirror_wait = None
        if self.mirror:
            self.mirror.stop()
            self.mirror = None
        self.media_server.set_base_dir(os.path.expanduser("~"))
        if self.current and self.current.kind == DeviceKind.TCL:
            self._user_stopped = True
            self._near_end = False
            self._end_seen_at = 0.0
            try:
                self.current.stop()
            except Exception:
                pass
        self.mirror_btn.setText("Espelhar tela do PC")
        self.status.setText("Espelhamento parado.")
        self._sync_tray_mirror()
        if was_running:
            self._notify("tclcast", "Espelhamento parado.")

    # ------------------------------------------------------------------ #
    def _require(self) -> bool:
        if not self.current:
            self.status.setText("Conecte a um dispositivo primeiro.")
            self._notify("tclcast", "Nenhum dispositivo conectado — "
                         "abra o app para escolher uma TV.")
            return False
        # TCL: checa o socket de verdade (não só a flag)
        if self.current.kind == DeviceKind.TCL:
            tcl = self.current._tcl
            if not (self.current.connected and tcl and tcl._running):
                # Regra universal: comando com a conexão caída dispara a
                # reconexão automática na hora (o watchdog faria em até 2s).
                if not self._reconnecting:
                    self._check_connection()
                self.status.setText(
                    "Conexão perdida — reconectando à TV automaticamente…")
                self._notify("tclcast", "Sem conexão com a TV — "
                             "reconectando automaticamente…")
                return False
        elif not self.current.connected:
            self.status.setText("Conecte um dispositivo primeiro.")
            self._notify("tclcast", "Conecte um dispositivo primeiro.")
            return False
        return True

    def closeEvent(self, event):
        # Padrão: o X da janela encerra o app. "Fechar para a bandeja" é
        # opcional (Configurações) — aí o X esconde e o app continua.
        if (self._tray_on_close and not self._force_quit
                and self._tray is not None and self._tray.isVisible()):
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self._tray.showMessage(
                    "tclcast",
                    "O tclcast continua rodando na bandeja. Para encerrar, "
                    "use “Sair” no menu do ícone.",
                    QSystemTrayIcon.MessageIcon.Information, 5000)
            return
        self._shutdown()
        super().closeEvent(event)
        # Redundância segura: garante o fim do loop mesmo quando a janela
        # estava oculta ao fechar (lastWindowClosed não dispara nesse caso).
        QApplication.quit()

    def _shutdown(self) -> None:
        """Limpeza do fechamento — passos isolados e sem travar a interface."""
        for label, fn in (
            ("parar o espelhamento", self._stop_mirror),
            ("parar o servidor de mídia", self.media_server.stop),
            ("desconectar da TV", self._disconnect_device),
        ):
            try:
                fn()
            except Exception:
                log.exception("falha ao %s no fechamento", label)

    def _disconnect_device(self) -> None:
        if self.current:
            self.current.disconnect()


class AppsDialog(QDialog):
    """Janela popup com os apps instalados na TV (cmd 223 do AppManagerProxy).

    Lista, abre (223>>OPEN>>{pkg}) e remove (223>>UNINSTALL>>{pkg}>>{nome}).
    """

    def __init__(self, device: CastDevice, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Apps da TV")
        self.setWindowIcon(icons.tv_window_icon())
        self.resize(560, 520)
        self.device = device
        self._sig = AppSignals()
        self._sig.apps.connect(self._fill)
        self._sig.storage.connect(self._fill_storage)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.storage_label = QLabel("Armazenamento: consultando…")
        self.storage_label.setObjectName("dim")
        refresh = text_icon_button("refresh", "Atualizar",
                                   "Recarrega apps e armazenamento")
        refresh.clicked.connect(self._reload)
        top.addWidget(self.storage_label, 1)
        top.addWidget(refresh)
        lay.addLayout(top)

        self.listw = QListWidget()
        self.listw.itemDoubleClicked.connect(lambda *_: self._open())
        lay.addWidget(self.listw, 1)

        self.info = QLabel("Consultando apps da TV…")
        self.info.setObjectName("dim")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        open_btn = text_icon_button("open", "Abrir", "Abrir o app na TV")
        open_btn.clicked.connect(self._open)
        del_btn = text_icon_button("trash", "Desinstalar…",
                                   "Remover o app da TV (pede confirmação)")
        del_btn.clicked.connect(self._uninstall)
        close_btn = text_icon_button("close", "Fechar", obj="ghostBtn")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(open_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        lay.addLayout(btns)

        self._reload()

    # ------------------------------------------------------------------ #
    def _reload(self) -> None:
        self.info.setText("Consultando apps da TV…")
        try:
            self.device.get_apps(lambda a: self._sig.apps.emit(a))
            self.device.get_storage(lambda d: self._sig.storage.emit(d))
        except Exception as e:
            self.info.setText(f"Erro: {e}")

    def _fill(self, apps: list) -> None:
        self.listw.clear()
        for a in apps:
            label = f"{a['name']}  —  {a['pkg']}"
            if a.get("version"):
                label += f"  ({a['version']})"
            item = QListWidgetItem(label)
            item.setIcon(icons.make_icon("apps", TEXT_DIM, 18))
            item.setData(Qt.UserRole, a)
            self.listw.addItem(item)
        if not apps:
            self.info.setText(
                "Nenhum app recebido da TV (sem resposta?). "
                "Clique em Atualizar.")
        else:
            self.info.setText(
                f"{len(apps)} apps instalados. "
                "Duplo clique = abrir. Desinstalar pede confirmação.")

    def _fill_storage(self, st: dict) -> None:
        if st:
            free = st.get("free_mb", 0)
            total = st.get("total_mb", 0)
            self.storage_label.setText(
                f"Armazenamento da TV: {free} MB livres de {total} MB")
        else:
            self.storage_label.setText("Armazenamento: sem resposta")

    def _selected(self) -> dict | None:
        item = self.listw.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _open(self) -> None:
        app = self._selected()
        if not app:
            self.info.setText("Selecione um app primeiro.")
            return
        try:
            self.device.open_app(app["pkg"])
            self.info.setText(f"Abrindo {app['name']} na TV…")
        except Exception as e:
            self.info.setText(f"Erro ao abrir: {e}")

    def _uninstall(self) -> None:
        app = self._selected()
        if not app:
            self.info.setText("Selecione um app primeiro.")
            return
        resp = QMessageBox.question(
            self, "Desinstalar app",
            f"Remover \"{app['name']}\" ({app['pkg']}) da TV?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return
        try:
            self.device.uninstall_app(app["pkg"], app["name"])
            self.info.setText(
                f"Remoção de {app['name']} enviada — confirme na TV se "
                "pedir, depois clique Atualizar.")
        except Exception as e:
            self.info.setText(f"Erro ao remover: {e}")


class SettingsDialog(QDialog):
    """Configurações do app: início automático e atalho no menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurações do tclcast")
        self.setWindowIcon(icons.tv_window_icon())
        self.resize(580, 400)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        box = QGroupBox("SISTEMA")
        bl = QVBoxLayout(box)
        bl.setSpacing(8)

        self.autostart_cb = QCheckBox(
            "Iniciar junto com o computador (na bandeja)")
        self.autostart_cb.setChecked(autostart.is_enabled())
        self.autostart_cb.toggled.connect(self._toggle_autostart)
        bl.addWidget(self.autostart_cb)
        hint = QLabel(f"Arquivo: {autostart.AUTOSTART_FILE}")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        bl.addWidget(hint)

        self.menu_cb = QCheckBox("Atalho no menu de aplicativos")
        if autostart.package_menu_entry():
            self.menu_cb.setChecked(True)
            self.menu_cb.setEnabled(False)
            self.menu_cb.setText(
                "Atalho no menu de aplicativos (instalado pelo pacote)")
        else:
            self.menu_cb.setChecked(autostart.is_menu_installed())
            self.menu_cb.toggled.connect(self._toggle_menu)
        bl.addWidget(self.menu_cb)

        self.tray_close_cb = QCheckBox(
            "Continuar na bandeja ao fechar a janela (não encerrar)")
        self.tray_close_cb.setChecked(
            bool(getattr(parent, "_tray_on_close", False)))
        self.tray_close_cb.toggled.connect(self._toggle_tray_close)
        bl.addWidget(self.tray_close_cb)
        tray_hint = QLabel(
            "Desmarcado (padrão): o X da janela encerra o tclcast. "
            "Marcado: o X esconde a janela e o app segue na bandeja — "
            "para encerrar de vez, use “Sair” no menu do ícone.")
        tray_hint.setObjectName("dim")
        tray_hint.setWordWrap(True)
        bl.addWidget(tray_hint)
        lay.addWidget(box)

        target = QLabel("Comando usado nos atalhos:\n"
                        f"{autostart.launch_command()} --tray")
        target.setObjectName("dim")
        target.setWordWrap(True)
        target.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(target)
        lay.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        close_btn = text_icon_button("close", "Fechar", obj="ghostBtn")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        lay.addLayout(btns)

    def _toggle_autostart(self, enabled: bool) -> None:
        try:
            autostart.enable() if enabled else autostart.disable()
        except OSError as e:
            QMessageBox.warning(
                self, "Configurações",
                f"Não foi possível alterar o início automático:\n{e}")
            self.autostart_cb.blockSignals(True)
            self.autostart_cb.setChecked(autostart.is_enabled())
            self.autostart_cb.blockSignals(False)

    def _toggle_menu(self, enabled: bool) -> None:
        try:
            autostart.install_menu() if enabled else autostart.remove_menu()
        except OSError as e:
            QMessageBox.warning(
                self, "Configurações",
                f"Não foi possível alterar o atalho no menu:\n{e}")
            self.menu_cb.blockSignals(True)
            self.menu_cb.setChecked(autostart.is_menu_installed())
            self.menu_cb.blockSignals(False)

    def _toggle_tray_close(self, enabled: bool) -> None:
        win = self.parent()
        if win is not None and hasattr(win, "_set_tray_on_close"):
            win._set_tray_on_close(enabled)


def run():
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="tclcast",
        description="Controle remoto, casting e espelhamento para TVs TCL.")
    parser.add_argument(
        "--tray", "--hidden", dest="start_hidden", action="store_true",
        help="inicia só na bandeja, sem abrir a janela")
    parser.add_argument("--version", action="version",
                        version=f"tclcast {__version__}")
    args, _ = parser.parse_known_args()

    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    app.setApplicationName("tclcast")
    app.setDesktopFileName("tclcast")
    app.setStyleSheet(APP_QSS)
    app.setWindowIcon(icons.tv_window_icon())

    # Instância única: se já existe uma rodando, avisa e sai (o app que já
    # está aberto mostra a janela — ou nada, quando veio do autostart).
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_INSTANCE_KEY)
    if sock.waitForConnected(300):
        sock.write(b"hide" if args.start_hidden else b"show")
        sock.flush()
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        sys.exit(0)

    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)   # socket órfão (crash)
    server = QLocalServer()
    server.listen(SINGLE_INSTANCE_KEY)

    win = MainWindow()

    def _on_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.waitForReadyRead(300)
        if bytes(conn.readAll()).strip() == b"show":
            win._show_window()
        conn.disconnectFromServer()

    server.newConnection.connect(_on_new_connection)

    if args.start_hidden and win.has_tray():
        log.info("iniciado na bandeja (--tray)")
    else:
        win.show()
    sys.exit(app.exec())
