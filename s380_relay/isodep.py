"""Minimal ISO14443-4 (ISO-DEP) layer-4 logic shared by both relay sides.

The relay converges at the APDU layer: a Type A or Type B card and a Type A
emulated card all speak the same ISO-DEP block protocol above layers 2/3, so
each side terminates ISO-DEP independently and only the APDUs are relayed.

This module provides the block (PCB) helpers plus a PCD-side (reader) state
machine, :class:`Pcd`, that turns a single APDU exchange into the right sequence
of I-/R-/S-blocks over a caller-supplied ``send`` function. The PICC-side
(emulated card) sequencing lives in ``client.py`` and uses the same helpers.

Scope: no NAD (single card, no node addressing); CID only as needed for Type B.
Command and response chaining and S(WTX) are handled; these paths are
best-effort and most real APDUs use neither. CRC is added/checked by the reader.
"""

from __future__ import annotations

from typing import Callable, List, Optional


class IsoDepError(Exception):
    """The card broke the ISO-DEP block protocol."""


# Base PCB for an I-block (no CID, no NAD, block number 0, no chaining).
I_BLOCK = 0x02

#: Frame sizes for FSCI/FSDI codes 0..8 (codes above 8 are treated as 256).
_FRAME_SIZES = (16, 24, 32, 40, 48, 64, 96, 128, 256)


def is_i_block(pcb: int) -> bool:
    return pcb & 0xC0 == 0x00


def is_r_block(pcb: int) -> bool:
    return pcb & 0xE0 == 0xA0


def is_r_nak(pcb: int) -> bool:
    return is_r_block(pcb) and pcb & 0x10 != 0


def is_s_block(pcb: int) -> bool:
    return pcb & 0xC0 == 0xC0


def block_number(pcb: int) -> int:
    return pcb & 0x01


def has_chaining(pcb: int) -> bool:
    return is_i_block(pcb) and pcb & 0x10 != 0


def has_cid(pcb: int) -> bool:
    return pcb & 0x08 != 0


def has_nad(pcb: int) -> bool:
    return is_i_block(pcb) and pcb & 0x04 != 0


def is_s_wtx(pcb: int) -> bool:
    return is_s_block(pcb) and pcb & 0x30 == 0x30


def is_s_deselect(pcb: int) -> bool:
    return is_s_block(pcb) and pcb & 0x30 == 0x00


def i_block(block: int, chaining: bool) -> int:
    """Builds an I-block PCB with the given block number and chaining flag."""
    return I_BLOCK | (block & 1) | (0x10 if chaining else 0)


def r_ack(block: int) -> int:
    """Builds an R(ACK) PCB for the given block number."""
    return 0xA2 | (block & 1)


def s_wtx(wtxm: int) -> bytes:
    """Builds an S(WTX) block: PCB ``0xF2`` then the WTXM in the low 6 bits."""
    return bytes([0xF2, wtxm & 0x3F])


def inf(frame: bytes) -> bytes:
    """Returns the INF field (payload) of a block, skipping PCB and CID/NAD."""
    if not frame:
        return b""
    pcb = frame[0]
    offset = 1 + (1 if has_cid(pcb) else 0) + (1 if has_nad(pcb) else 0)
    return bytes(frame[offset:])


def frame_size_from_code(code: int) -> int:
    """Decodes a frame-size code (FSCI/FSDI) into a byte count."""
    return _FRAME_SIZES[code] if 0 <= code < len(_FRAME_SIZES) else 256


def fsc_from_ats(ats: bytes) -> int:
    """Derives FSC from an ATS: FSCI is the low nibble of T0 (byte after TL)."""
    if len(ats) < 2:
        return 32
    return frame_size_from_code(ats[1] & 0x0F)


def chunks(data: bytes, size: int) -> List[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)] or [b""]


Send = Callable[[bytes], bytes]


class Pcd:
    """PCD-side (reader) ISO-DEP state machine over a ``send`` function.

    ``fsc`` is the card's frame size (from ATS / ATQB). Block numbering starts
    at 0, as required right after RATS / ATTRIB, and persists across APDUs for
    the life of the activation. ``cid`` is included in every block when given:
    Type B cards that advertise CID support expect the CID byte even when 0.
    """

    def __init__(self, fsc: int, cid: Optional[int] = None) -> None:
        self.block = 0
        # Maximum INF bytes per block (FSC minus PCB, CID and the 2-byte CRC).
        overhead = 3 + (1 if cid is not None else 0)
        self.max_inf = max(fsc - overhead, 1)
        self.cid = None if cid is None else cid & 0x0F

    def _header(self, pcb: int) -> bytes:
        if self.cid is None:
            return bytes([pcb & ~0x08])
        return bytes([pcb | 0x08, self.cid])

    def transmit(self, send: Send, apdu: bytes) -> bytes:
        """Sends one command APDU and returns the response APDU, handling
        command chaining, response chaining and the card's S(WTX) requests.

        ``send`` transmits one block (PCB + INF, no CRC) and returns the card's
        reply block; it must raise on "no answer".
        """
        parts = chunks(bytes(apdu), self.max_inf)
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            frame = self._header(i_block(self.block, not last)) + part
            reply = self._drain_wtx(send, send(frame))
            if last:
                return self._receive(send, reply)
            # Mid-chain: the card acknowledges with an R(ACK); advance.
            if not reply or not is_r_block(reply[0]):
                raise IsoDepError("expected R(ACK) during command chaining")
            self.block ^= 1
        raise IsoDepError("empty APDU could not be sent")  # pragma: no cover

    def _receive(self, send: Send, first: bytes) -> bytes:
        """Collects a (possibly chained) response starting from ``first``."""
        data = bytearray()
        cur = first
        while True:
            cur = self._drain_wtx(send, cur)
            if not cur:
                raise IsoDepError("empty response block")
            pcb = cur[0]
            if not is_i_block(pcb):
                raise IsoDepError("unexpected PCB 0x%02X in response" % pcb)
            data += inf(cur)
            self.block ^= 1
            if not has_chaining(pcb):
                return bytes(data)
            # More to come: acknowledge with the next block number.
            cur = send(self._header(r_ack(self.block)))

    def _drain_wtx(self, send: Send, reply: bytes) -> bytes:
        """Answers any S(WTX) request from the card until a non-WTX block."""
        while reply and is_s_wtx(reply[0]):
            # WTXM is the last byte of the request (after an optional CID).
            wtxm = reply[-1] if len(reply) > 1 else 1
            reply = send(self._header(0xF2) + bytes([wtxm & 0x3F]))
        return reply
