"""Servidor HTTP local que serve arquivos de mídia para a TV fazer download.

Equivale ao TCLFileServer/NanoHTTPD do app original (porta 7983).
A TV busca o arquivo por http://{ip_local}:7983/<caminho>.
"""
from __future__ import annotations

import mimetypes
import os
import socket
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

PORT = 7983


def get_local_ip(remote_ip: str = "8.8.8.8") -> str:
    """Descobre o IP local na rota até a TV."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((remote_ip, 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _Handler(BaseHTTPRequestHandler):
    # base_dir e current_file injetados via partial
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        rel = unquote(parsed.path).lstrip("/")
        base = self.server.base_dir
        target = os.path.join(base, rel) if rel else self.server.current_file
        if not target or not os.path.isfile(target):
            self.send_error(404, "não encontrado")
            return
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        size = os.path.getsize(target)

        # Suporte a "Range requests" (obrigatório para players Android/TV:
        # sem 206 eles abortam a conexão com BrokenPipe)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if rng and rng.startswith("bytes="):
            try:
                spec = rng[6:].split(",")[0].strip()
                s, _, e = spec.partition("-")
                start = int(s) if s else 0
                end = int(e) if e else size - 1
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
            except ValueError:
                start, end, status = 0, size - 1, 200

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(target, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # TV fechou (comum com players); não é erro

    def log_message(self, fmt, *args):  # silencia log
        pass


class MediaServer:
    """Servidor HTTP de mídia local."""

    def __init__(self, port: int = PORT, base_dir: str = os.path.expanduser("~")):
        self.port = port
        self.base_dir = base_dir
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        handler = partial(_Handler)
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), handler)
        self._httpd.base_dir = self.base_dir
        self._httpd.current_file = None
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True)
        self._thread.start()

    def serve_file(self, path: str) -> str:
        """Define o arquivo atual e retorna o URL local completo."""
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self.base_dir = os.path.dirname(os.path.abspath(path))
        if self._httpd:
            self._httpd.base_dir = self.base_dir
            self._httpd.current_file = os.path.abspath(path)
        ip = get_local_ip()
        name = os.path.basename(path)
        from urllib.parse import quote
        return f"http://{ip}:{self.port}/{quote(name)}"

    def set_base_dir(self, directory: str) -> None:
        """Serve arquivos de `directory` (ex.: diretório HLS do espelhamento)."""
        self.base_dir = directory
        if self._httpd:
            self._httpd.base_dir = directory
            self._httpd.current_file = None

    def stop(self) -> None:
        """Encerra o servidor sem bloquear a thread chamadora.

        `server_close()` faz join das threads dos handlers; a TV costuma
        manter conexões keep-alive abertas ao reproduzir mídia/espelhar e
        esse join travaria a interface no fechamento. Por isso o
        encerramento acontece em uma thread daemon (o processo sai logo
        depois e os sockets são fechados junto).
        """
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return

        def _close() -> None:
            try:
                httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_close, daemon=True).start()
