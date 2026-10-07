"""Server (card side): holds the real card on a local reader, activates it into
ISO-DEP, and relays command APDUs to it for a remote client.

An APDU exchange that fails is retried once after re-activating the card,
which recovers a card that dropped out of layer 4 while the field was idle.

Only one physical reader is involved, so connections are served one at a time;
a new client waits until the previous one disconnects. Clients keep their link
open between taps, so TCP keepalive is enabled to notice a peer that vanished
without closing (e.g. the phone dropped off Wi-Fi) instead of blocking every
later client forever.
"""

from __future__ import annotations

import logging
import socket
import sys
from dataclasses import dataclass
from typing import BinaryIO, Optional

from . import protocol
from .cardside import CardError, CardSide

log = logging.getLogger(__name__)

#: Caps a single request line.
MAX_LINE_BYTES = 64 * 1024

#: Idle time before the first keepalive probe, and the gap between probes.
KEEPALIVE_TIME_S = 15
KEEPALIVE_INTERVAL_S = 5


@dataclass
class ServerConfig:
    listen_addr: str = "127.0.0.1:7878"
    timeout_ms: int = 1000


class Session:
    """Per-connection state: whether the card has been activated yet."""

    def __init__(self) -> None:
        self.activated = False


def parse_addr(addr: str, default_host: str = "127.0.0.1"):
    host, sep, port = addr.rpartition(":")
    if not sep:
        host, port = default_host, addr
    host = host.strip("[]") or default_host
    return host, int(port)


def enable_keepalive(sock: socket.socket) -> None:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    if sys.platform == "win32" and hasattr(socket, "SIO_KEEPALIVE_VALS"):
        sock.ioctl(
            socket.SIO_KEEPALIVE_VALS,
            (1, KEEPALIVE_TIME_S * 1000, KEEPALIVE_INTERVAL_S * 1000),
        )
        return
    for name, value in (
        ("TCP_KEEPIDLE", KEEPALIVE_TIME_S),
        ("TCP_KEEPALIVE", KEEPALIVE_TIME_S),  # macOS spelling of KEEPIDLE
        ("TCP_KEEPINTVL", KEEPALIVE_INTERVAL_S),
    ):
        opt = getattr(socket, name, None)
        if opt is not None:
            sock.setsockopt(socket.IPPROTO_TCP, opt, value)


def run(card: CardSide, config: ServerConfig) -> None:
    log.info("card-side reader: %s", card.label())
    host, port = parse_addr(config.listen_addr)
    with socket.create_server((host, port)) as listener:
        print(
            "S380 relay server (card side, NFC-%s via %s) listening on %s:%d"
            % (card.tech, card.label(), host, port)
        )
        print("place the real card on this reader and tap the phone to the client")
        while True:
            conn, peer = listener.accept()
            log.info("client connected: %s", peer)
            try:
                enable_keepalive(conn)
            except OSError as e:
                log.warning("could not enable TCP keepalive: %s", e)
            try:
                with conn, conn.makefile("rb") as rfile, conn.makefile("wb") as wfile:
                    handle_client(rfile, wfile, card, config)
            except (OSError, ValueError) as e:
                log.warning("client session ended with error: %s", e)
            log.info("client disconnected: %s", peer)


def handle_client(rfile: BinaryIO, wfile: BinaryIO, card: CardSide, config: ServerConfig) -> None:
    session = Session()
    while True:
        line = rfile.readline(MAX_LINE_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_LINE_BYTES and not line.endswith(b"\n"):
            raise ValueError("request line exceeds %d bytes" % MAX_LINE_BYTES)
        if not line.strip():
            continue
        try:
            response = process_request(card, config, session, protocol.decode_request(line))
        except protocol.ProtocolError as e:
            response = protocol.Error("invalid request: %s" % e)
        wfile.write(protocol.encode(response))
        wfile.flush()


def process_request(
    card: CardSide, config: ServerConfig, session: Session, request: protocol.Request
) -> protocol.Response:
    if isinstance(request, protocol.GetCard):
        try:
            info = card.activate(config.timeout_ms)
        except CardError as e:
            session.activated = False
            return protocol.Error(str(e))
        session.activated = True
        return protocol.Card(card.tech, info)
    return relay_apdu(card, config, session, request.data, request.timeout_ms)


def relay_apdu(
    card: CardSide,
    config: ServerConfig,
    session: Session,
    apdu: bytes,
    timeout_ms: Optional[int],
) -> protocol.Response:
    timeout = timeout_ms if timeout_ms is not None else config.timeout_ms

    if not session.activated:
        try:
            card.activate(timeout)
        except CardError as e:
            return protocol.Error(str(e))
        session.activated = True

    # First attempt against the already-activated card.
    try:
        return _apdu_response(card.exchange(apdu, timeout))
    except CardError as e:
        first_err = e
    log.debug("APDU failed (%s); re-activating card and retrying once", first_err)

    # Retry once after re-activating, keeping the original error visible if the
    # card can no longer be activated.
    session.activated = False
    try:
        card.activate(timeout)
    except CardError as e:
        return protocol.Error(
            "APDU exchange failed: %s (re-activation also failed: %s)" % (first_err, e)
        )
    session.activated = True
    try:
        return _apdu_response(card.exchange(apdu, timeout))
    except CardError as e:
        return protocol.Error("APDU exchange failed: %s" % e)


def _apdu_response(response: bytes) -> protocol.ApduResponse:
    log.debug("card APDU response: %s", response.hex())
    return protocol.ApduResponse(response)
