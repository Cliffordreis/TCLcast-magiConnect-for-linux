"""Cliente TCP do protocolo TCL (porta 6553) com criptografia AES.

Fluxo (observado na TV real "Sala de estar", protocolo v14):
  1. Handshake (cmd 159) vai em TEXTO PLANO.
  2. TV responde com:
       a) pacote CIFRADO AES: "285>>1"  (TV_RESPONSE_CODE_NEW_VERSION=285, status=1 ACCEPTED)
       b) pacote TEXTO PLANO: "159>>{info da TV}>>..."
  3. Comandos seguintes (teclas, volume, mídia) vão CIFRADOS AES/CBC/PKCS5.
     Respostas da TV (se houver) também vêm cifradas.

Criptografia (extraída de SecurityUtil.AES + libjnitool.so):
  - Algoritmo: AES/CBC/PKCS5Padding
  - Chave (16 bytes): "tnscreentnscreen"
  - IV (16 bytes):  12 34 56 78 90 AB CD EF 12 34 56 78 90 AB CD EF
"""
from __future__ import annotations

import logging
import os
import queue
import socket
import struct
import threading
import time
import urllib.request
import uuid
from typing import Callable

from . import tcl_const as C

log = logging.getLogger(__name__)

MAX_PACKET = 1048576  # 1 MiB

# --- Criptografia AES (extraída do libjnitool.so) --------------------------
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad as _pad, unpad as _unpad

_AES_KEY = b"tnscreentnscreen"
_AES_IV = bytes.fromhex("1234567890abcdef1234567890abcdef")


def _aes_encrypt(plain: bytes) -> bytes:
    return AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV).encrypt(_pad(plain, 16))


def _aes_decrypt(cipher: bytes) -> bytes:
    if not cipher or len(cipher) % 16:
        return cipher
    try:
        return _unpad(AES.new(_AES_KEY, AES.MODE_CBC, _AES_IV).decrypt(cipher), 16)
    except Exception:
        return cipher


def _to_int(s: str) -> int:
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return 0


class TclProtocolError(Exception):
    pass


class TclClient:
    """Conecta a uma TV TCL e envia comandos do protocolo proprietário."""

    def __init__(self, host: str, port: int = C.TCP_PORT_COMMAND,
                 phone_name: str = "tclcast-linux", timeout: float = 8.0):
        self.host = host
        self.port = port
        self.phone_name = phone_name
        self.uuid = str(uuid.uuid4())
        self.timeout = timeout
        self._sock: socket.socket | None = None
        # Fila de transmissão: TODO envio é assíncrono (thread escritora).
        # A thread da GUI nunca faz I/O de rede — sem isso, um sendall preso
        # (TV/Wi-Fi parados) congela a interface por segundos a fio.
        self._txq: "queue.Queue" = queue.Queue()
        self._writer: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._running = False
        self._last_send = 0.0  # monotonic; controla ociosidade p/ heartbeat
        self.on_message: Callable[[str], None] | None = None
        self.on_disconnect: Callable[[], None] | None = None
        self.handshaken = False
        self.tv_info: str = ""
        self.protocol_version: int = 6
        # Controle de conexão viva (regra do app original — HeartbeatSender):
        # mandamos "150>>" com o writer ocioso e consideramos a conexão MORTA
        # se a TV não der NENHUM sinal depois do beat. Sem isso, um socket
        # zumbi (TV dormiu/trocou de rede) segue aceitando comandos que nunca
        # chegam — e só reconectar na mão resolvia.
        self._last_rx = 0.0     # monotonic; instante da última msg recebida
        self._beat_sent = 0.0   # monotonic; beat enviado e ainda sem resposta
        self._torndown = False  # queda notificada uma única vez
        # Pendências de resposta (consulta -> callback), demultiplexadas no read_loop
        self._pending_lock = threading.Lock()
        self._pending: list[list] = []  # [prefixo, callback, once]

    # ------------------------------------------------------------------ #
    # Demultiplexador de respostas (queries cmd -> resposta)
    # ------------------------------------------------------------------ #
    def add_pending(self, prefix: str, cb: Callable[[str], None],
                    once: bool = True) -> list:
        """Registra cb para mensagens da TV que comecem com `prefix`.

        Retorna um token para remover via remove_pending().
        """
        entry = [prefix, cb, once]
        with self._pending_lock:
            self._pending.append(entry)
        return entry

    def remove_pending(self, entry: list) -> None:
        with self._pending_lock:
            if entry in self._pending:
                self._pending.remove(entry)

    def _dispatch(self, text: str) -> None:
        with self._pending_lock:
            matched = [e for e in self._pending if text.startswith(e[0])]
            done = [e for e in matched if e[2]]
            for e in done:
                self._pending.remove(e)
        for _, cb, _once in matched:
            try:
                cb(text)
            except Exception:
                log.exception("erro no callback de resposta %r", text[:80])

    # ------------------------------------------------------------------ #
    # Conexão e framing
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._sock = sock
        self._running = True
        self._torndown = False
        self._last_send = time.monotonic()
        self._last_rx = time.monotonic()
        self._beat_sent = 0.0
        self._writer = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer.start()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._handshake()
        # Heartbeat proativo (como o app original): sem ele, a TV derruba a
        # conexão após ~1-2 min de inatividade (Errno 32 "pipe quebrado").
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()

    def _handshake(self) -> None:
        # InquiryDeviceInfoCommand.pack(): "159>>{phonename}>>1>>{uuid}>>1>>1"
        # Vai em TEXTO PLANO (a TV responde com 285>>1 cifrado + 159>>... plano).
        payload = C.GAP.join([
            str(C.GET_CLIENTTYPE), self.phone_name, "1", self.uuid, "1", "1"
        ])
        self._send_raw(payload, encrypt=False)
        # Marca handshaken; o reader confirma ao ver 285>>1.

    def close(self) -> None:
        # Encerramento explícito (fechar o app / desconectar): NÃO notifica
        # queda, para o watchdog da GUI não tentar reconectar sozinho.
        # Nunca bloqueia: shutdown() acorda o reader preso em recv() na hora
        # e o sentinela encerra a thread escritora.
        self._torndown = True
        self._running = False
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._txq.put(None)

    def _teardown(self) -> None:
        """Queda detectada (EOF/erro/timeout de heartbeat): limpa e avisa 1x."""
        if self._torndown:
            return
        self._torndown = True
        self._running = False
        sock, self._sock = self._sock, None
        self._beat_sent = 0.0
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self.on_disconnect:
            try:
                self.on_disconnect()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # ------------------------------------------------------------------ #
    # Escrita / leitura
    # ------------------------------------------------------------------ #
    def _send_raw(self, payload: str, encrypt: bool = True,
                  delay_after: float = 0.0) -> None:
        """Enfileira um envio — não bloqueia (quem escreve é a _writer_loop).

        `delay_after`: pausa entre este envio e o próximo na fila (usado pela
        155 dupla do send_text, que antes dormia 0.3s na thread da GUI).
        """
        if not self._sock or not self._running:
            raise TclProtocolError("não conectado")
        self._txq.put((payload, encrypt, delay_after))

    def _writer_loop(self) -> None:
        """Thread escritora: único ponto do cliente que faz sendall."""
        while True:
            item = self._txq.get()
            if item is None:
                break  # close(): encerra a thread
            payload, encrypt, delay = item
            sock = self._sock
            if not self._running or sock is None:
                continue  # conexão já caiu: descarta a fila pendente
            data = payload.encode("utf-8")
            if encrypt and self.handshaken:
                data = _aes_encrypt(data)
            envelope = struct.pack(">I", len(data)) + data
            try:
                sock.sendall(envelope)
            except OSError:
                # Escrita falhou: conexão morta. Derruba para o watchdog da
                # GUI reconectar (regra universal para todos os comandos).
                self._teardown()
                continue
            self._last_send = time.monotonic()
            if delay:
                time.sleep(delay)

    # Writer ocioso há quanto tempo para mandar HeartbeatRequest (SDK ≈5s).
    _BEAT_IDLE = 4.0
    # Sem NENHUM sinal da TV depois do beat => conexão morta (zumbi).
    # O SDK usa 20s; 10s deixa a recuperação automática mais rápida.
    _BEAT_TIMEOUT = 10.0

    def _heartbeat_loop(self) -> None:
        """Mantém a conexão viva E derruba conexão zumbi (regra do SDK).

        - Writer ocioso por ~4s: envia HeartbeatRequest "150>>" — a TV
          responde; isso impede a TV de fechar o socket por inatividade.
        - Beat sem NENHUMA resposta (nem tráfego de outro comando) dentro de
          `_BEAT_TIMEOUT`: a conexão está morta (TV dormiu, Wi-Fi caiu…).
          Derruba o socket em vez de mandar comandos para o vazio; o
          watchdog da GUI reconecta sozinho em seguida.
        """
        while self._running and self._sock:
            time.sleep(1)
            if not self._running or not self._sock:
                break
            if not self.handshaken:
                continue
            now = time.monotonic()
            pending = self._beat_sent > 0 and self._last_rx <= self._beat_sent
            if pending and now - self._beat_sent > self._BEAT_TIMEOUT:
                log.warning("heartbeat sem resposta há %.0fs — conexão morta",
                            now - self._beat_sent)
                self._teardown()
                break
            # Ocioso e sem beat pendente? Manda um novo beat.
            if not pending and now - self._last_send >= self._BEAT_IDLE:
                self._beat_sent = now
                try:
                    self._send_raw(f"{C.ISONLINE}>>", encrypt=True)
                except (OSError, TclProtocolError):
                    break  # _send_raw já derrubou o estado

    def _read_exact(self, n: int) -> bytes | None:
        sock = self._sock
        if sock is None:
            return None
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _read_loop(self) -> None:
        while self._running and self._sock:
            try:
                header = self._read_exact(4)
                if header is None:
                    break
                (length,) = struct.unpack(">I", header)
                if length == 0 or length > MAX_PACKET:
                    continue
                body = self._read_exact(length)
                if body is None:
                    break
                self._last_rx = time.monotonic()  # qualquer tráfego = TV viva
                # Tenta decriptografar; se falhar, assume texto plano.
                plain = _aes_decrypt(body)
                try:
                    text = plain.decode("utf-8")
                except UnicodeDecodeError:
                    text = body.decode("utf-8", errors="ignore")
                if text.strip() == "":
                    continue  # heartbeat vazio

                # Observação: "150>>..." vindo da TV é a RESPOSTA ao nosso
                # heartbeat — NUNCA responder de volta (no app original não
                # existe essa resposta), senão os dois lados entram num loop
                # infinito de beeps que degrada a TV. A chegada da mensagem
                # já conta como vida em `_last_rx` (acima).

                if text.startswith("285>>"):
                    # TV_RESPONSE_CODE_NEW_VERSION — handshake aceito
                    status = text.split(">>")[1] if ">>" in text else "0"
                    if status == "1":
                        self.handshaken = True
                elif text.startswith(str(C.GET_CLIENTTYPE)):
                    # Resposta do handshake com info da TV
                    self.handshaken = True
                    self.tv_info = text
                self._dispatch(text)
                if self.on_message:
                    self.on_message(text)
            except socket.timeout:
                continue
            except OSError:
                break
        # Conexão caiu (EOF/erro/TV desligou): limpar estado para que
        # `connected` reflita a realidade e a GUI possa reconectar.
        self._teardown()

    # ------------------------------------------------------------------ #
    # API de alto nível
    # ------------------------------------------------------------------ #
    def send_command(self, cmd: int, *args: str) -> None:
        """Envia um comando: 'cmd>>arg1>>arg2...' (cifrado após handshake)."""
        fields = [str(cmd), *[str(a) for a in args]]
        self._send_raw(C.GAP.join(fields))

    def send_key(self, key: str) -> None:
        """Envia uma tecla do remoto (nome amigável ou código numérico)."""
        # Limpa símbolos decorativos ("▲ UP" -> "UP") e espaços
        clean = key.strip().upper()
        for sym in ("▲", "▼", "◀", "▶", "⬆", "⬇", "⬅", "➡", "↑", "↓", "←", "→"):
            clean = clean.replace(sym, "")
        clean = clean.strip()
        code = C.KEYCODES.get(clean, None)
        if code is None:
            try:
                code = int(clean)
            except ValueError:
                raise ValueError(f"tecla desconhecida: {key!r}") from None
        self.send_command(C.KEY, str(code))

    def set_volume(self, level: int) -> None:
        self.send_command(C.MEDIA_SET_VOLUME, str(max(0, min(100, level))))

    def set_mute(self, mute: bool) -> None:
        # SetMuteCmd.isMute usa "true"/"false" (string), não "1"/"0"
        self.send_command(C.MEDIA_SET_MUTE, "true" if mute else "false")

    def prepare_play(self, media_type: str) -> None:
        self.send_command(C.PREPARE_PLAY, media_type)

    def play_single(self, info: str) -> None:
        self.send_command(C.PLAY_SINGLE, info)

    def play_media(self, url: str, media_type: str = C.TYPE_VIDEO,
                   title: str = "") -> None:
        """Prepara e envia uma mídia (arquivo local/URL) para a TV reproduzir.

        Fluxo do TCLVideoPlayerProxy.play():
          StopCmd (129) -> PreparePlayCmd "133>>{type}" -> PlaySingleCmd.
        O info do PlaySingleCmd usa VideoInfo.getInfo(), que separa os campos
        com "->" (NÃO com ">>") e são 8 campos, com defaults "null":
          url->title->player->album->coverPath->isPortrait->width->height
        """
        try:
            self.send_command(C.MEDIA_STOP, "")
        except TclProtocolError:
            pass
        self.send_command(C.PREPARE_PLAY, media_type)
        info = "->".join([url, title, "null", "null", "null",
                          "false", "null", "null"])
        self.send_command(C.PLAY_SINGLE, info)

    def play_url_tentplay(self, url: str, media_type: str = C.TYPE_VIDEO,
                          name: str = "") -> None:
        """Usa o comando 224 (MEDIA_TENTPLAY) com payload JSON para URLs."""
        import json
        j = json.dumps({"name": name, "url": url, "webUrl": ""})
        payload = C.GAP.join(["5", media_type, '""', '""', '""', '""', j])
        # envia como "224>>{payload}"
        self.send_command(C.MEDIA_TENTPLAY, payload)

    def pause(self) -> None:
        self.send_command(C.MEDIA_PAUSE)

    def resume(self) -> None:
        """Retoma a reprodução pausada (TCLVideoPlayerProxy.start():
        PlaySingleCmd com info vazio -> \"134>>\")."""
        self.send_command(C.PLAY_SINGLE, "")

    def stop(self) -> None:
        self.send_command(C.STOP)

    def seek(self, position_ms: int) -> None:
        """Pula para uma posição (milissegundos — SeekToCmd.position)."""
        self.send_command(C.MEDIA_SEEK, str(int(position_ms)))

    def play_next(self) -> None:
        self.send_command(C.MEDIA_PLAY_NEXT, "")

    def play_previous(self) -> None:
        self.send_command(C.MEDIA_PLAY_PREVIOUS, "")

    def send_text(self, text: str) -> None:
        """Digita texto na TV (RemoteControlInputCmd: \"155>>{texto}\").

        Semântica VERIFICADA AO VIVO (TV TCL-LA-TC8000-S1, 17/10/2026):
        - cada 155 SUBSTITUI o conteúdo do campo focado pelo texto enviado
          (não anexa) — por isso o app envia sempre o texto completo;
        - o canal de entrada da TV "ESFRIA": a PRIMEIRA 155 depois de um
          período ocioso (nova sessão TCP, ou ~1 min sem entrada) é ENGOLIDA
          pela TV. Por isso enviamos o MESMO texto DUAS vezes: a 1ª abre o
          canal (ou aplica, se já estava aberto), a 2ª garante a aplicação.
          Repetir é seguro: a semântica é de substituição — a 2ª cópia só
          republica o mesmo conteúdo.

        O fluxo é o mesmo do MagiConnect (TCLRemoteControlProxy.sendInputStr:
        RemoteControlInputCmd) — mensagens 155 com o texto acumulado completo.
        \"249\" (keyboardInput) NÃO é aceito por este firmware (responde
        1000>>249>>unknown) e não é necessário.
        """
        if not text:
            return
        # As duas cópias saem em ordem pela fila de transmissão; o intervalo
        # de 0.3s roda na thread escritora — a GUI não dorme.
        fields = C.GAP.join([str(C.INPUT_TEXT), text])
        self._send_raw(fields, delay_after=0.3)
        self._send_raw(fields)

    def _query(self, cmds: list[tuple[int, str]], prefixes: tuple[str, ...],
               timeout: float, cb: Callable[[dict], None]) -> None:
        """Envia comandos e coleta as respostas com os prefixos dados.

        cb({prefixo: mensagem}) é chamado ao fim do timeout (ou antes, se
        todos os prefixos chegarem). Pendências removidas garantidamente.
        """
        got: dict[str, str] = {}
        finished = threading.Event()
        entries: list = []

        def _mk(prefix: str):
            def _h(msg: str) -> None:
                got[prefix] = msg
                if all(p in got for p in prefixes):
                    finished.set()
            return _h

        for p in prefixes:
            entries.append(self.add_pending(p, _mk(p), once=False))

        def _finish() -> None:
            finished.wait(timeout)
            for e in entries:
                self.remove_pending(e)
            try:
                cb(got)
            except Exception:
                log.exception("erro no callback de query %s", cmds)

        sent_ok = True
        for cmd, payload in cmds:
            try:
                self.send_command(cmd, payload)
            except TclProtocolError:
                sent_ok = False
                break
        if not sent_ok:
            for e in entries:
                self.remove_pending(e)
            cb({})
            return
        threading.Thread(target=_finish, daemon=True).start()

    def query_player(self, cb: Callable[[dict], None]) -> None:
        """Pergunta estado/posição/duração/ações do player da TV.

        cb({'state','position_ms','duration_ms','actions'}) — campos ausentes
        quando a TV não responde (ex.: nenhum player ativo).
        """
        prefixes = ("145>>", "146>>", "147>>", "148>>")

        def _done(got: dict) -> None:
            out: dict = {}
            if "145>>" in got:
                m = got["145>>"]
                out["state"] = m.split(">>")[1] if ">>" in m else ""
            if "146>>" in got:
                out["duration_ms"] = _to_int(got["146>>"].split(">>")[-1])
            if "147>>" in got:
                out["position_ms"] = _to_int(got["147>>"].split(">>")[-1])
            if "148>>" in got:
                m = got["148>>"]
                out["actions"] = m.split(">>")[1] if ">>" in m else ""
            cb(out)

        self._query(
            [(C.MEDIA_GET_PLAY_STATE, ""),
             (C.MEDIA_GET_MEDIA_DURATION, ""),
             (C.MEDIA_GET_CUR_PLAY_POSITION, ""),
             (C.MEDIA_GET_CURRENT_TRANSPORT_ACTIONS, "")],
            prefixes, 1.8, _done)

    # --- Gerenciador de apps da TV (AppManagerProxy, cmd 223) ----------
    def get_apps(self, cb: Callable[[list[dict]], None]) -> None:
        """Lista os apps instalados na TV (\"223>>GET\").

        cb([{pkg, name, icon, version, code, size, type, ...}]) — [] em erro.
        Formato da resposta:
          "223>>GET>>{pkg}::{nome}::{icon}::{code}::{ver}::{..}::{..}::{..}::{..}>..."
        """
        called = threading.Event()

        def _h(msg: str) -> None:
            called.set()
            cb(self._parse_apps(msg))

        entry = self.add_pending("223>>GET>>", _h, once=True)

        def _timeout():
            time.sleep(8)
            if not called.is_set():
                self.remove_pending(entry)
                cb([])  # sem resposta: lista vazia (GUI mostra aviso)
        threading.Thread(target=_timeout, daemon=True).start()

        try:
            self.send_command(C.TV_APP, "GET")
        except TclProtocolError:
            self.remove_pending(entry)
            cb([])

    @staticmethod
    def _parse_apps(msg: str) -> list[dict]:
        apps: list[dict] = []
        try:
            payload = msg.split(">>", 2)[2]
        except IndexError:
            return apps
        for raw in payload.split(">"):
            campos = raw.split("::")
            if len(campos) >= 2 and campos[0].strip():
                apps.append({
                    "pkg": campos[0].strip(),
                    "name": campos[1].strip(),
                    "icon": campos[2] if len(campos) > 2 else "",
                    "code": campos[3] if len(campos) > 3 else "",
                    "version": campos[4] if len(campos) > 4 else "",
                    "size": campos[5] if len(campos) > 5 else "",
                    "type": campos[8] if len(campos) > 8 else "",
                })
        return apps

    def open_app(self, package: str) -> None:
        """Abre um app da TV (\"223>>OPEN>>{pkg}\")."""
        self.send_command(C.TV_APP, f"OPEN>>{package}")

    def uninstall_app(self, package: str, name: str) -> None:
        """Remove um app da TV (\"223>>UNINSTALL>>{pkg}>>{nome}\")."""
        self.send_command(C.TV_APP, f"UNINSTALL>>{package}>>{name}")

    def get_storage(self, cb: Callable[[dict], None]) -> None:
        """Armazenamento da TV (\"239>>\" -> \"239>>0>>{total}>>{livre}\" MB)."""
        def _done(got: dict) -> None:
            msg = got.get("239>>")
            if not msg:
                cb({})
                return
            parts = msg.split(">>")
            try:
                cb({"total_mb": int(parts[2]), "free_mb": int(parts[3])})
            except (IndexError, ValueError):
                cb({})
        self._query([(C.TV_STORAGE, "")], ("239>>",), 3.0, _done)

    # ------------------------------------------------------------------ #
    # Captura de tela da TV (ShotPic, cmd 225) — API não-bloqueante
    # ------------------------------------------------------------------ #
    def capture_tv_screen(self, on_result: Callable[[str, str], None]) -> None:
        """Envia cmd 225 e chama on_result(status, url) quando a TV responder.

        status: 'ok' (url válida), 'fail', 'unsupported', 'storage_full',
               'copyright', 'error'.
        """
        result: dict[str, str] = {}
        called = [False]

        def _handler(msg: str) -> None:
            if msg.startswith(f"{C.SNAP_SHOT}>>"):
                parts = msg.split(">>")
                code = parts[1] if len(parts) > 1 else "1"
                url = parts[2] if len(parts) > 2 else ""
                status = {
                    "0": "ok", "1": "fail", "2": "unsupported",
                    "3": "storage_full", "4": "copyright",
                }.get(code, "error")
                result["status"] = status
                result["url"] = url

        old_handler = self.on_message
        self.on_message = _handler
        self.send_command(C.SNAP_SHOT)

        # Timeout de segurança: se a TV não responder, chama com erro
        import threading
        import time
        def _timeout():
            time.sleep(12)  # timeout maior para GUI
            if self.on_message is _handler:
                self.on_message = old_handler
                on_result("error", "")
        threading.Thread(target=_timeout, daemon=True).start()

        # Polling para chamar on_result quando a resposta chegar
        def _poll():
            while self.on_message is _handler:
                if "status" in result:
                    status = result["status"]
                    url = result["url"]
                    self.on_message = old_handler
                    on_result(status, url)
                    return
                time.sleep(0.1)
        threading.Thread(target=_poll, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Captura de tela + download da imagem (fluxo completo)
    # ------------------------------------------------------------------ #
    #: nº de capturas repetidas se o servidor HTTP da TV fechar (efêmero)
    CAPTURE_RETRIES = 4

    def capture_and_save(self, dest_dir: str,
                         on_done: Callable[[str, str], None]) -> None:
        """Captura a tela da TV e salva a imagem em `dest_dir`.

        Fluxo (idêntico ao app original TCLShotPicProxy):
          1. cmd 225 → TV responde "225>>0>>{url}"
          2. download HTTP da url
        peculiaridades reais descobertas na TV:
          - o servidor HTTP dela é EFÊMERO: a 1ª captura costuma falhar
            (connection refused) — repetimos a captura automagicamente;
          - o arquivo vem como JPEG mesmo com sufixo ".PNG" — detectamos
            o formato pelo magic bytes.

        on_done(status, caminho): status 'ok' → caminho do arquivo salvo;
        demais status → mensagem de erro.
        """
        self._capture_attempt(dest_dir, on_done, 0)

    def _capture_attempt(self, dest_dir: str,
                         on_done: Callable[[str, str], None],
                         attempt: int) -> None:
        def _got(status: str, url: str) -> None:
            if status != "ok" or not url:
                if status in ("unsupported", "copyright", "storage_full"):
                    on_done(status, "")  # erro definitivo, não adianta repetir
                elif attempt + 1 < self.CAPTURE_RETRIES:
                    log.info("captura sem url (%s), repetindo (%d/%d)",
                             status, attempt + 2, self.CAPTURE_RETRIES)
                    time.sleep(1.0)
                    self._capture_attempt(dest_dir, on_done, attempt + 1)
                else:
                    on_done(status if status != "ok" else "error", "")
                return
            # URL chegou; download em thread separada (não bloqueia)
            threading.Thread(target=self._download_shot,
                             args=(url, dest_dir, on_done, attempt),
                             daemon=True).start()

        self.capture_tv_screen(_got)

    def _download_shot(self, url: str, dest_dir: str,
                       on_done: Callable[[str, str], None],
                       attempt: int) -> None:
        data: bytes | None = None
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read()
        except Exception as e:
            log.debug("download da captura falhou: %s", e)

        # Servidor fechou/curto demais → repete a captura (não o download:
        # a TV gera nova url/porta a cada cmd 225)
        if not data or len(data) < 1024:
            if attempt + 1 < self.CAPTURE_RETRIES:
                log.info("servidor da TV fechou a url, nova captura (%d/%d)",
                         attempt + 2, self.CAPTURE_RETRIES)
                time.sleep(1.0)
                self._capture_attempt(dest_dir, on_done, attempt + 1)
            else:
                on_done("error",
                        "TV capturou, mas o servidor dela não respondeu")
            return

        # Detecta formato real pelo magic bytes (TV diz .PNG mas é JPEG)
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            ext = ".png"
        elif data[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ext = ".webp"
        else:
            ext = ".bin"

        try:
            os.makedirs(dest_dir, exist_ok=True)
            path = os.path.join(
                dest_dir, f"tv_{time.strftime('%Y%m%d_%H%M%S')}{ext}")
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            on_done("error", f"não consegui salvar: {e}")
            return

        log.info("captura da TV salva em %s (%d bytes)", path, len(data))
        on_done("ok", path)
