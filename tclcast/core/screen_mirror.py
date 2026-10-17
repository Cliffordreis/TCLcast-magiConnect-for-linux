"""Espelhamento de tela do computador para a TV.

Gera um stream HLS (m3u8) servido pelo servidor HTTP local; a TV reproduz
pelo player normal do protocolo TCL (tipo "Video"). Duas rotas de captura:

- **X11**: ffmpeg + x11grab direto (simples e síncrono);
- **Wayland**: o helper ``portal_capture.py`` (python do sistema + GTK) fala
  com o xdg-desktop-portal (diálogo "Compartilhar tela" do GNOME), abre o
  PipeWire e entrega frames NV12@30fps no stdout; o ffmpeg lê esse pipe como
  rawvideo. O ``restore_token`` do portal é guardado para não pedir
  autorização nas próximas vezes.

No app original isso era MediaProjection/Miracast; no Linux, HLS sobre HTTP é a
abordagem compatível com o protocolo TCL.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _find_ffmpeg() -> str | None:
    """Localiza o ffmpeg: TCLCAST_FFMPEG → ao lado do executável
    (bundle/instalado) → PATH do sistema."""
    env = os.environ.get("TCLCAST_FFMPEG")
    if env and os.path.exists(env):
        return env
    bundled = os.path.join(os.path.dirname(sys.executable), "ffmpeg")
    if os.path.exists(bundled):
        return bundled
    return shutil.which("ffmpeg")


class ScreenMirror:
    """Espelha a tela do PC para um diretório HLS servido localmente."""

    def __init__(self, output_dir: str, local_ip: str, http_port: int = 7983):
        self.output_dir = Path(output_dir)
        self.local_ip = local_ip
        self.http_port = http_port
        self._proc: subprocess.Popen | None = None
        self._helper: subprocess.Popen | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self.master_playlist = "screen.m3u8"
        self._display = os.environ.get("DISPLAY", ":0")
        # estado do modo portal (Wayland)
        self.mode = "x11grab"
        self.needs_authorization = False
        self.error = ""
        self._helper_ready = threading.Event()
        self._helper_error: str | None = None
        self._err_lines: list[str] = []
        self._stop_requested = False
        self._portal_size: tuple[int, int] | None = None
        self._portal_target = (1920, 720)

    # ------------------------------------------------------------------ #
    @property
    def url(self) -> str:
        return f"http://{self.local_ip}:{self.http_port}/{self.master_playlist}"

    @property
    def running(self) -> bool:
        return self._running

    @property
    def ready(self) -> bool:
        """True quando já existem segmentos HLS *com conteúdo* (vídeo real).

        O ffmpeg cria um `segNNN.ts` de 0 bytes assim que abre a gravação;
        só a playlist com entradas `.ts` + segmento não-vazio garantem vídeo.
        """
        try:
            playlist = self.output_dir / self.master_playlist
            if not playlist.exists():
                return False
            if ".ts" not in playlist.read_text(errors="replace"):
                return False
            return any(f.stat().st_size > 0
                       for f in self.output_dir.glob("seg*.ts"))
        except OSError:
            return False

    @staticmethod
    def _is_wayland() -> bool:
        return (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
                or bool(os.environ.get("WAYLAND_DISPLAY")))

    # ------------------------------------------------------------------ #
    def start(self, fps: int = 15, width: int = 1920,
              height: int = 720) -> bool:
        """Inicia a captura e o stream HLS em background.

        No Wayland o método retorna assim que os processos são lançados — a
        autorização do portal pode demorar (diálogo do GNOME). Acompanhe o
        andamento pelas propriedades ``ready``, ``running`` e ``error``.
        """
        if self._running:
            return True
        self.error = ""
        self._stop_requested = False
        self._portal_target = (width, height)
        self._clean_output()

        if self._is_wayland():
            self.mode = "portal"
            self.needs_authorization = True
            return self._start_portal()

        self.mode = "x11grab"
        self.needs_authorization = False
        return self._start_x11grab(fps, width, height)

    def _clean_output(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for f in self.output_dir.glob("seg*"):  # inclui .ts.tmp de execuções passadas
            f.unlink()
        playlist = self.output_dir / self.master_playlist
        if playlist.exists():
            playlist.unlink()

    def _hls_args(self, fps: int) -> list[str]:
        return [
            # veryfast/crf 20: boa nitidez para texto de tela com CPU de sobra
            # (ultrafast + crf 23 no downscale deixava tudo borrado).
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
            "-crf", "20", "-maxrate", "12M", "-bufsize", "24M",
            # Keyframe no início de cada segmento (0,5 s): sem -g o x264 usa
            # GOP 250 frames e o ffmpeg 6.x só fecha o segmento no próximo
            # keyframe — a playlist ficaria ~17 s sem segmentos novos.
            "-g", str(max(2, fps // 2)), "-sc_threshold", "0",
            "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-muxdelay", "0",
            "-f", "hls",
            # Segmentos curtos + janela pequena reduzem o atraso na TV
            # (janela de 4 × 0,5 s = 2 s de playlist).
            "-hls_time", "0.5", "-hls_list_size", "4",
            "-hls_flags", "delete_segments+omit_endlist",
            "-hls_segment_filename", str(self.output_dir / "seg%03d.ts"),
            str(self.output_dir / self.master_playlist),
        ]

    # ---------------------------- X11 ---------------------------------- #
    def _detect_grabber(self) -> str | None:
        """Detecta o método de captura disponível."""
        ffmpeg = _find_ffmpeg()
        if ffmpeg is None:
            return None
        # Testa se x11grab funciona com o display atual
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error",
                 "-f", "x11grab", "-video_size", "320x240",
                 "-i", self._display,
                 "-frames:v", "1", "-f", "null", "-"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return "x11grab"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def _start_x11grab(self, fps: int, _width: int = 0,
                       _height: int = 0) -> bool:
        ffmpeg = _find_ffmpeg()
        grabber = self._detect_grabber()
        if ffmpeg is None or grabber is None:
            log.error("ffmpeg não disponível ou display %s inacessível",
                      self._display)
            return False

        # Captura a tela inteira: sem -video_size o x11grab descobre o
        # tamanho via XRandR (antes pegava só uma região fixa do canto).
        # O scale limita a 1920 de largura para monitores maiores.
        cmd = [
            ffmpeg, "-y",
            "-f", "x11grab",
            "-framerate", str(fps), "-i", self._display,
            "-vf", "scale=min(1920\\,iw):-2",
            *self._hls_args(fps),
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            log.error("ffmpeg falhou: %s", e)
            return False

        # Verifica se o ffmpeg realmente começou a produzir segmentos
        # (em vez de ficar pendurado num display headless)
        if not self._wait_for_segments(timeout=5):
            log.error("ffmpeg não produziu segmentos (display inacessível?)")
            self.stop()
            return False

        self._running = True
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        return True

    # --------------------------- Wayland ------------------------------- #
    def _start_portal(self) -> bool:
        """Lança o helper do portal + monitor em background (não bloqueia)."""
        helper = Path(__file__).with_name("portal_capture.py")
        if not helper.exists():
            self.error = "helper do portal (portal_capture.py) não encontrado"
            log.error(self.error)
            return False
        py = "/usr/bin/python3"
        if not Path(py).exists():
            py = shutil.which("python3") or "python3"
        token_file = os.path.expanduser("~/.config/tclcast/portal_token")
        cmd = [py, str(helper), "--token-file", token_file]

        self._helper_ready = threading.Event()
        self._helper_error = None
        self._err_lines = []
        try:
            self._helper = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            self.error = f"não consegui lançar o helper do portal: {e}"
            log.error(self.error)
            return False

        def _drain() -> None:
            try:
                for raw in self._helper.stderr:
                    line = raw.decode("utf-8", "replace").rstrip()
                    self._err_lines.append(line)
                    if line.startswith("SIZE "):
                        self._helper_ready.set()
                    elif line.startswith("ERROR"):
                        self._helper_error = line
                        self._helper_ready.set()
            except Exception:  # noqa: BLE001 — pipe fechado ao parar
                pass

        threading.Thread(target=_drain, daemon=True).start()
        self._running = True
        self._thread = threading.Thread(target=self._portal_worker, daemon=True)
        self._thread.start()
        return True

    def _portal_worker(self) -> None:
        """Espera a autorização, liga o ffmpeg no pipe do helper e monitora."""
        if not self._helper_ready.wait(timeout=180):
            if not self._stop_requested:
                self.error = "tempo esgotado aguardando a autorização de tela"
                log.error("portal: %s", self.error)
                self.stop()
            return
        if self._stop_requested:
            return
        helper = self._helper
        if helper is None:
            return
        if self._helper_error:
            self.error = self._helper_error
            log.error("portal: %s", self._helper_error)
            self.stop()
            return

        size = None
        for line in self._err_lines:
            if line.startswith("SIZE "):
                size = line.split()[1:3]
        try:
            w, h = int(size[0]), int(size[1])
        except (TypeError, ValueError, IndexError):
            self.error = "o portal não informou o tamanho da tela"
            log.error(self.error)
            self.stop()
            return
        self._portal_size = (w, h)
        largura_max = self._portal_target[0]

        ffmpeg_cmd = [
            _find_ffmpeg() or "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "nv12",
            "-s", f"{w}x{h}", "-r", "30",
            "-i", "pipe:0",
        ]
        # Só reduz se a captura for maior que a largura máxima. Mantém o
        # scaler padrão (bicúbico): o fast_bilinear antigo embaçava o texto.
        if w > largura_max:
            ffmpeg_cmd += ["-vf", f"scale={largura_max}:-2"]
        ffmpeg_cmd += self._hls_args(30)
        try:
            self._proc = subprocess.Popen(
                ffmpeg_cmd, stdin=helper.stdout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            helper.stdout.close()
        except Exception as e:
            self.error = f"ffmpeg falhou: {e}"
            log.error(self.error)
            self.stop()
            return
        self._watch()

    # --------------------------- monitoração --------------------------- #
    def _wait_for_segments(self, timeout: float = 5.0) -> bool:
        """Espera o ffmpeg criar o primeiro segmento (sinal de vida).

        O ffmpeg 6.x grava em `.tmp`/temp antes de fechar o segmento, então
        checamos qualquer arquivo `seg*` (não só `*.ts` fechados).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if list(self.output_dir.glob("seg*")):
                return True
            if self._proc and self._proc.poll() is not None:
                return False  # ffmpeg morreu
            time.sleep(0.3)
        return False

    def _watch(self) -> None:
        """Monitora os processos de captura; encerra tudo se algum morrer."""
        while not self._stop_requested:
            procs = [(n, p) for n, p in
                     (("ffmpeg", self._proc), ("helper", self._helper)) if p]
            if not procs:
                break
            if any(p.poll() is not None for _, p in procs):
                log.warning("espelhamento: processo de captura terminou")
                break
            time.sleep(0.5)
        self.stop()

    def stop(self) -> None:
        self._stop_requested = True
        # libera o worker do portal se estiver esperando a autorização
        self._helper_ready.set()
        for attr in ("_proc", "_helper"):
            p = getattr(self, attr)
            if p is None:
                continue
            if attr == "_helper" and p.stdout:
                try:
                    p.stdout.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
            try:
                p.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass
            setattr(self, attr, None)
        self._running = False
