"""Captura de tela no Wayland via xdg-desktop-portal (helper).

Este script é executado pelo **python do sistema** (`/usr/bin/python3`), não
pelo venv, porque precisa de `python3-gi` (GTK/Gio). O `screen_mirror.py` o
lança como subprocesso e lê o vídeo bruto do seu stdout.

Fluxo:
  1. cria uma janela X11 oculta (via GDK_BACKEND=x11) apenas para ter um
     `parent_window` válido — o xdg-desktop-portal-gnome 46.2 sofre SEGV com
     parent vazio (flameshot #4663);
  2. fala com `org.freedesktop.portal.ScreenCast` (CreateSession →
     SelectSources → Start) e guarda o `restore_token` para não pedir
     autorização nas próximas vezes;
  3. roda o `gst-launch-1.0` com `pipewiresrc target-object=<serial do nó>`
     (conexão normal — o WirePlumber cria o link; mirar pela conexão fd do
     portal falha com "target not found" neste gstreamer1.0-pipewire 1.0.5)
     entregando frames **NV12 @30fps** no stdout; o ffmpeg do tclcast usa
     isso como entrada rawvideo e gera o HLS que a TV reproduz.
     O gst roda como processo filho com retry (até 3 tentativas) e este
     processo permanece vivo para manter a sessão do portal aberta.

Somente stderr é usado para log/status (linhas "SIZE {w} {h}", "ERROR …");
stdout é exclusivamente vídeo.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJ_PATH = "/org/freedesktop/portal/desktop"
SC_IFACE = "org.freedesktop.portal.ScreenCast"
REQ_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _load_token(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _save_token(path: str, token: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(token)
    except OSError:
        pass


def _hidden_x11_window():
    """Janela X11 oculta para usar como parent_window. Retorna (xid_str, win)."""
    if not os.environ.get("DISPLAY"):
        return "", None
    try:
        os.environ.setdefault("GDK_BACKEND", "x11")
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("GdkX11", "3.0")
        from gi.repository import Gtk
        from gi.repository import GdkX11

        win = Gtk.Window()
        win.set_default_size(1, 1)
        win.move(-10000, -10000)
        win.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration()
        gdk_win = win.get_window()
        if gdk_win is None:
            return "", win
        xid = GdkX11.X11Window.get_xid(gdk_win)
        return f"x11:0x{xid:x}", win
    except Exception as e:  # noqa: BLE001
        log(f"AVISO: sem janela X11 para parent_window ({e})")
        return "", None


def _variant_get(v, default=None):
    """Desembrulha GLib.Variant (se for) de forma defensiva."""
    if v is None:
        return default
    if hasattr(v, "unpack"):
        return v.unpack()
    return v


def _pw_dump_props(node_id: int) -> dict:
    """Propriedades do nó do PipeWire (object.serial, node.name) via pw-dump."""
    try:
        out = subprocess.run(["pw-dump"], capture_output=True,
                             timeout=20).stdout
        for obj in json.loads(out):
            if (obj.get("type", "").endswith(":Node")
                    and obj.get("id") == node_id):
                return (obj.get("info", {}) or {}).get("props", {}) or {}
    except Exception:  # noqa: BLE001
        pass
    return {}


class Portal:
    def __init__(self):
        from gi.repository import Gio, GLib
        self.Gio = Gio
        self.GLib = GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # nome único do cliente (ex.: ":1.1432" -> "1_1432"): usado para
        # prever o caminho do request na convenção handle_token
        self._unique = self.bus.get_unique_name().lstrip(":").replace(".", "_")

    def _call(self, method: str, params, reply_type):
        return self.bus.call_sync(BUS_NAME, OBJ_PATH, SC_IFACE, method, params,
                                  self.GLib.VariantType(reply_type),
                                  self.Gio.DBusCallFlags.NONE, -1, None)

    def _subscribe_response(self, handle_token: str, state: dict):
        """Inscreve no caminho *previsto* do request (ANTES da chamada!).

        A convenção do portal é que o request viva em
        /org/freedesktop/portal/desktop/request/{nome-único}/{handle_token};
        inscrever antes evita perder o sinal Response de requests instantâneos
        (ex.: CreateSession no xdg-desktop-portal 1.18.x).
        """
        predicted = (f"/org/freedesktop/portal/desktop/request/"
                     f"{self._unique}/{handle_token}")

        def _on_response(_conn, _sender, _path, _iface, _sig, params):
            code, results = params.unpack()
            state["code"] = code
            state["results"] = results

        sub = self.bus.signal_subscribe(
            None, REQ_IFACE, "Response", predicted, None,
            self.Gio.DBusSignalFlags.NONE, _on_response)
        return sub

    def _wait_state(self, state: dict, timeout: float):
        """Roda o loop principal até o sinal Response chegar."""
        GLib = self.GLib
        ctx = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while "code" not in state:
            if time.monotonic() > deadline:
                raise TimeoutError("o portal não respondeu (timeout)")
            ctx.iteration(False)
            if "code" not in state:
                time.sleep(0.02)
        if state["code"] != 0:
            raise RuntimeError(
                f"autorização cancelada/negada (code={state['code']})")
        return state.get("results") or {}

    def create_session(self) -> str:
        GLib = self.GLib
        opts = {
            "handle_token": GLib.Variant("s", "tclcast_cs"),
            "session_handle_token": GLib.Variant("s", "tclcast"),
        }
        state: dict = {}
        sub = self._subscribe_response("tclcast_cs", state)
        try:
            res = self._call("CreateSession", GLib.Variant("(a{sv})", (opts,)), "(o)")
            handle = res.unpack()[0]
            if "/session/" in handle:
                # versões novas devolvem o session handle direto
                return handle
            # 1.18.x devolve um request handle; o session handle vem no
            # sinal Response deste request
            results = self._wait_state(state, timeout=30)
        finally:
            self.bus.signal_unsubscribe(sub)
        session = _variant_get(results.get("session_handle"))
        if not session:
            raise RuntimeError("portal não devolveu o session handle")
        return session

    def select_sources(self, session: str, restore_token: str = "") -> None:
        GLib = self.GLib
        opts = {
            "handle_token": GLib.Variant("s", "tclcast_ss"),
            "types": GLib.Variant("u", 1),            # 1 = MONITOR
            "multiple": GLib.Variant("b", False),
            "cursor_mode": GLib.Variant("u", 2),      # 2 = embutido no vídeo
            # sem persist_mode o portal não devolve restore_token
            # (0 = não persistir, 1 = até reiniciar, 2 = para sempre)
            "persist_mode": GLib.Variant("u", 2),
        }
        if restore_token:
            opts["restore_token"] = GLib.Variant("s", restore_token)
        state: dict = {}
        sub = self._subscribe_response("tclcast_ss", state)
        try:
            self._call("SelectSources",
                       GLib.Variant("(oa{sv})", (session, opts)), "(o)")
            self._wait_state(state, timeout=30)
        finally:
            self.bus.signal_unsubscribe(sub)

    def start(self, session: str, parent_window: str) -> dict:
        GLib = self.GLib
        opts = {"handle_token": GLib.Variant("s", "tclcast_st")}
        state: dict = {}
        sub = self._subscribe_response("tclcast_st", state)
        try:
            self._call(
                "Start",
                GLib.Variant("(osa{sv})", (session, parent_window, opts)),
                "(o)")
            return self._wait_state(state, timeout=600)
        finally:
            self.bus.signal_unsubscribe(sub)

    def open_pipewire(self, session: str) -> int:
        GLib = self.GLib
        res, fd_list = self.bus.call_with_unix_fd_list_sync(
            BUS_NAME, OBJ_PATH, SC_IFACE, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (session, {})), None,
            self.Gio.DBusCallFlags.NONE, -1, None)
        index = res.unpack()[0]
        return fd_list.get(index)

    def close_session(self, session: str) -> None:
        try:
            self.bus.call_sync(
                BUS_NAME, session, SESSION_IFACE, "Close",
                None, None, self.Gio.DBusCallFlags.NONE, -1, None)
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="captura via portal (Wayland)")
    ap.add_argument("--token-file", default=os.path.expanduser(
        "~/.config/tclcast/portal_token"))
    ap.add_argument("--dry-run", action="store_true",
                    help="valida o portal sem abrir o diálogo")
    args = ap.parse_args()

    try:
        portal = Portal()
    except Exception as e:  # noqa: BLE001
        log(f"ERROR portal indisponível: {e}")
        return 1

    try:
        session = portal.create_session()
        log(f"SESSION {session}")
        token = _load_token(args.token_file)
        portal.select_sources(session, token)

        if args.dry_run:
            portal.close_session(session)
            log("DRYRUN_OK")
            return 0

        parent, win = _hidden_x11_window()
        if parent:
            log(f"PARENT {parent}")
        results = portal.start(session, parent)

        # A janela oculta já cumpriu o papel (posicionar o diálogo)
        if win is not None:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass

        streams = _variant_get(results.get("streams"), [])
        if not streams:
            log("ERROR o portal não retornou streams")
            return 1
        node_id, props = streams[0][0], _variant_get(streams[0][1], {})
        size = _variant_get(props.get("size"))
        if not size or len(size) != 2:
            log("ERROR stream sem tamanho definido")
            return 1
        w, h = int(size[0]), int(size[1])

        new_token = _variant_get(results.get("restore_token"))
        if new_token:
            _save_token(args.token_file, new_token)

        # ---- identificar o nó do screencast ------------------------------
        # O pipewiresrc do gstreamer1.0-pipewire 1.0.5 NÃO mira o nó pela
        # conexão do portal (fd + target por id/serial => "target not found";
        # fd sem target => o WirePlumber não cria o link). O que funciona é
        # uma conexão NORMAL mirando o nó pelo *serial* (preferido; ids podem
        # ser reutilizados) ou pelo nome — o WirePlumber faz o link.
        alvo = _variant_get(props.get("pipewire-serial"))
        origem = "serial do portal"
        props_no: dict = {}
        if not alvo:
            props_no = _pw_dump_props(node_id)
            alvo = props_no.get("object.serial")
            origem = "serial via pw-dump"
        if not alvo:
            alvo = props_no.get("node.name")
            origem = "nome via pw-dump"
        if not alvo:
            log("ERROR não consegui identificar o nó do screencast")
            return 1
        log(f"ALVO {alvo} ({origem})")

        # ---- gst-launch gerenciado (com retry) ---------------------------
        caps = (f"video/x-raw,format=NV12,width={w},height={h},"
                f"framerate=30/1")
        filho: subprocess.Popen | None = None

        def _encerrar(_sinal, _frame):
            if filho is not None and filho.poll() is None:
                filho.terminate()
                try:
                    filho.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    filho.kill()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _encerrar)
        signal.signal(signal.SIGINT, _encerrar)

        log(f"SIZE {w} {h}")
        log(f"NODE {node_id}")

        for tentativa in range(1, 4):
            args_gst = [
                "gst-launch-1.0", "-q",
                "pipewiresrc", f"target-object={alvo}",
                "client-name=tclcast",
                "do-timestamp=true", "keepalive-time=100",
                "!", "queue", "max-size-buffers=8", "leaky=downstream",
                "!", "videoconvert", "!", "videorate", "!", caps,
                "!", "fdsink", "fd=1",
            ]
            log(f"GST tentativa {tentativa} (alvo={alvo})")
            filho = subprocess.Popen(args_gst)
            # morreu rápido? (link ainda não pronto etc.) tenta de novo
            time.sleep(1.5)
            rc = filho.poll()
            if rc is None:
                break
            log(f"AVISO gst-launch saiu cedo (rc={rc})")
        else:
            log("ERROR pipewiresrc não conseguiu conectar ao stream da tela")
            return 1

        rc = filho.wait()
        if rc < 0:
            # sinal (SIGTERM/SIGPIPE) = desligamento normal (stop()/ffmpeg fim)
            log(f"AVISO gst-launch encerrado por sinal ({-rc})")
            return 0
        if rc != 0:
            log(f"ERROR gst-launch terminou (rc={rc})")
            return 1
        log("AVISO a captura terminou")
        return 0
    except Exception as e:  # noqa: BLE001
        log(f"ERROR {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
