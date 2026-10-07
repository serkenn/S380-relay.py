"""Client (phone side): drives a local RC-S380 as a Type A ISO-DEP (Type 4)
target through nfcpy's ``listen``. It presents a synthetic Type 4 card to the
phone/terminal, terminates ISO-DEP locally, and relays the command APDUs to the
server — whose real card may be Type A or Type B. Only the APDUs cross the
link, so the terminal is unaware of the real card's technology.

The RC-S380 can only emulate NFC-A (and NFC-F), so this side is always Type A.
nfcpy answers RATS itself during ``listen`` and hands back the first ISO-DEP
command block (``tt4_cmd``); everything after that is sequenced here.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from typing import Callable, Optional

from . import isodep, protocol

log = logging.getLogger(__name__)

# Synthetic Type A activation values presented to the phone. The ATS declares
# FSCI=8 (256-byte frames) and TA/TB/TC, matching a generic Type 4 card.
SENS_RES = bytes([0x04, 0x00])
SDD_RES = bytes([0x08, 0x01, 0x02, 0x03])  # random-UID tag 0x08 + 3 bytes
SEL_RES = bytes([0x20])  # ISO14443-4 supported
ATS = bytes([0x05, 0x78, 0x80, 0x70, 0x02])

#: Phone frame size (FSD) assumed when chaining a long response back.
PHONE_FSD = 256


@dataclass
class ClientConfig:
    server_addr: str = "127.0.0.1:7878"
    command_timeout_ms: int = 1000
    listen_window_s: float = 1.0
    device_index: int = 1
    use_wtx: bool = True
    wtxm: int = 10


class RelayLinkError(Exception):
    """The relay server could not be reached or reported an error."""


class ServerLink:
    """A line-based connection to the relay server.

    The link is opened (and the real card activated with ``get_card``) on first
    use, kept warm between taps, and re-established after a failure.
    """

    def __init__(self, addr: str, connect_timeout_s: float = 5.0) -> None:
        self.addr = addr
        self.connect_timeout_s = connect_timeout_s
        self.sock: Optional[socket.socket] = None
        self.rfile = None
        self.wfile = None

    def connect(self) -> protocol.Card:
        from .server import parse_addr

        self.close()
        host, port = parse_addr(self.addr)
        try:
            sock = socket.create_connection((host, port), timeout=self.connect_timeout_s)
        except OSError as e:
            raise RelayLinkError("cannot connect to %s: %s" % (self.addr, e)) from None
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = sock
        self.rfile = sock.makefile("rb")
        self.wfile = sock.makefile("wb")
        response = self.request(protocol.GetCard())
        if isinstance(response, protocol.Error):
            self.close()
            raise RelayLinkError("server could not activate the card: %s" % response.message)
        if not isinstance(response, protocol.Card):
            self.close()
            raise RelayLinkError("unexpected response to get_card: %r" % (response,))
        return response

    def close(self) -> None:
        for f in (self.rfile, self.wfile, self.sock):
            if f is not None:
                try:
                    f.close()
                except OSError:
                    pass
        self.sock = self.rfile = self.wfile = None

    @property
    def connected(self) -> bool:
        return self.sock is not None

    def request(self, message) -> protocol.Response:
        if self.sock is None:
            raise RelayLinkError("not connected")
        try:
            self.wfile.write(protocol.encode(message))
            self.wfile.flush()
            line = self.rfile.readline()
        except OSError as e:
            self.close()
            raise RelayLinkError("relay link failed: %s" % e) from None
        if not line:
            self.close()
            raise RelayLinkError("server closed the connection")
        try:
            return protocol.decode_response(line)
        except protocol.ProtocolError as e:
            self.close()
            raise RelayLinkError("bad response from server: %s" % e) from None

    def apdu(self, capdu: bytes, timeout_ms: int) -> bytes:
        """Relays one command APDU and returns the response APDU bytes."""
        if not self.connected:
            card = self.connect()
            log.info("relay link (re)established; real card NFC-%s (%s)", card.tech, card.info)
        response = self.request(protocol.ApduRequest(capdu, timeout_ms))
        if isinstance(response, protocol.ApduResponse):
            return response.data
        if isinstance(response, protocol.Error):
            raise RelayLinkError("server: %s" % response.message)
        raise RelayLinkError("unexpected relay response: %r" % (response,))


def build_local_target():
    """Builds the synthetic Type A Type-4 target presented to the phone."""
    import nfc.clf

    target = nfc.clf.LocalTarget("106A")
    target.sens_res = bytearray(SENS_RES)
    target.sdd_res = bytearray(SDD_RES)
    target.sel_res = bytearray(SEL_RES)
    target.rats_res = bytearray(ATS)
    return target


def run(clf, link: ServerLink, config: ClientConfig) -> None:
    """Top-level client loop: activate the card, then emulate taps forever."""
    from .reader import label

    log.info("reader: %s", label(clf))
    card = link.connect()
    print("connected to relay server at %s" % config.server_addr)
    print("real card activated: NFC-%s (%s)" % (card.tech, card.info))
    target = build_local_target()
    print("emulating a Type 4 (NFC-A) card; tap a phone to this reader")

    while True:
        try:
            emulate_once(clf, target, link, config)
        except RelayLinkError as e:
            log.warning("relay link error: %s", e)
        except Exception as e:  # keep listening through a dropped tap
            log.warning("relay session error: %s", e)


def emulate_once(clf, target, link: ServerLink, config: ClientConfig) -> None:
    """Waits for a tap, then runs the PICC-side ISO-DEP loop for one session."""
    import nfc.clf

    try:
        local = clf.listen(target, config.listen_window_s)
    except nfc.clf.CommunicationError:
        return
    if local is None:
        return  # no tap within the window
    first = getattr(local, "tt4_cmd", None)
    if not first:
        return  # not an ISO-DEP (Type 4) activation

    log.info("phone activated ISO-DEP; relaying APDUs")
    session = PiccSession(clf, link, config)
    session.run(bytes(first))


class PiccSession:
    """PICC-side (emulated card) ISO-DEP sequencing for one activation.

    Mirrors the Rust client: reassembles chained command APDUs from the phone,
    buys time with S(WTX) before the network round-trip, relays the APDU, then
    returns the response as one or more I-blocks (chaining long responses).
    """

    def __init__(self, clf, link: ServerLink, config: ClientConfig) -> None:
        self.clf = clf
        self.link = link
        self.config = config
        self.timeout_s = max(config.command_timeout_ms, 1) / 1000.0

    def _send(self, block: bytes) -> Optional[bytes]:
        """Sends one response block to the phone, returns its next command block."""
        import nfc.clf

        try:
            got = self.clf.exchange(bytearray(block), self.timeout_s)
        except nfc.clf.BrokenLinkError:
            return None
        except nfc.clf.CommunicationError:
            return None
        return bytes(got) if got else None

    def run(self, frame: bytes) -> None:
        while frame is not None:
            frame = self._process(frame)

    def _process(self, frame: bytes) -> Optional[bytes]:
        if not frame:
            return None
        pcb = frame[0]

        if isodep.is_s_deselect(pcb):
            log.debug("phone sent S(DESELECT); ending session")
            self._send(frame)  # echo the S(DESELECT)
            return None
        if isodep.is_s_wtx(pcb):
            wtxm = frame[1] if len(frame) > 1 else 1
            return self._send(isodep.s_wtx(wtxm))
        if not isodep.is_i_block(pcb):
            log.debug("phone sent unexpected PCB 0x%02X; ignoring", pcb)
            return None

        # Reassemble a (possibly chained) command APDU.
        bn = isodep.block_number(pcb)
        capdu = bytearray(isodep.inf(frame))
        chaining = isodep.has_chaining(pcb)
        while chaining:
            nxt = self._send(bytes([isodep.r_ack(bn)]))
            if not nxt or not isodep.is_i_block(nxt[0]):
                return None
            bn = isodep.block_number(nxt[0])
            capdu += isodep.inf(nxt)
            chaining = isodep.has_chaining(nxt[0])
        log.debug("phone -> card APDU: %s", bytes(capdu).hex())

        # Buy time over the network link before relaying, if enabled.
        if self.config.use_wtx:
            if self._send(isodep.s_wtx(self.config.wtxm)) is None:
                return None

        rapdu = self.link.apdu(bytes(capdu), self.config.command_timeout_ms)
        log.debug("card -> phone APDU: %s", rapdu.hex())
        return self._send_response_apdu(bn, rapdu)

    def _send_response_apdu(self, bn: int, rapdu: bytes) -> Optional[bytes]:
        """Sends a response APDU as one or more I-blocks, chaining it when it
        exceeds the phone's frame size; returns the phone's next block."""
        max_inf = max(PHONE_FSD - 2, 1)
        if len(rapdu) <= max_inf:
            return self._send(bytes([isodep.i_block(bn, False)]) + rapdu)

        parts = isodep.chunks(rapdu, max_inf)
        cur_bn = bn
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            got = self._send(bytes([isodep.i_block(cur_bn, not last)]) + part)
            if got is None:
                return None
            if last:
                return got
            # Mid-chain: the phone acknowledges with an R(ACK); advance.
            if not isodep.is_r_block(got[0]):
                return None
            cur_bn ^= 1
        return None
