"""The card side of the relay: activating the real card into ISO-DEP and
exchanging APDUs with it. Two reader backends implement :class:`CardSide`:

- :class:`Port100Side` drives an RC-S380 (Port-100) through nfcpy's raw
  ``sense``/``exchange`` and the hand-rolled ISO-DEP PCD layer in
  :mod:`s380_relay.isodep`, for both Type A and Type B ISO-DEP. With
  ``auto`` it polls both and relays whichever card answers.
- :class:`PcscSide` drives any PC/SC reader through pyscard — e.g. an
  RC-S300 / PaSoRi 4.0 with Sony's driver — whose own ISO-DEP stack handles
  WTX, chaining and IFS for both Type A and Type B. nfcpy has no RC-S300
  driver, so this replaces the Rust version's Port-400 backend.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, TypeVar

from . import isodep
from .protocol import TECH_A, TECH_B

log = logging.getLogger(__name__)

#: SAK bit 6 (0x20): the Type A card supports ISO14443-4 (ISO-DEP).
SAK_ISO_DEP = 0x20

#: Detect the real card's technology (A or B) at activation. Never sent on the
#: wire: the ``card`` response always carries the detected ``A`` or ``B``.
TECH_AUTO = "auto"

T = TypeVar("T")


class CardError(Exception):
    """The real card could not be activated or did not answer."""


class CardSide:
    """A reader holding the real card, able to activate it and exchange APDUs."""

    #: The real card's technology; with ``auto``, the one last detected.
    tech: str

    def label(self) -> str:
        raise NotImplementedError

    def activate(self, timeout_ms: int) -> str:
        """(Re)activates the card into ISO-DEP; returns identification (hex)."""
        raise NotImplementedError

    def exchange(self, apdu: bytes, timeout_ms: int) -> bytes:
        """Exchanges a single command APDU, returning the response APDU."""
        raise NotImplementedError

    def close(self) -> None:
        pass


def retry_until(timeout_ms: int, attempt: Callable[[], T], interval_s: float = 0.05) -> T:
    """Retries ``attempt`` until it succeeds or ``timeout_ms`` elapses.

    Relay activation is lazy — it fires on client connect with no warm-up
    polling — so a card still powering up when the field comes on can miss the
    first sense. The last error is raised if the deadline passes.
    """
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
    while True:
        try:
            return attempt()
        except CardError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(interval_s)


# ---- Port-100 (RC-S380) via nfcpy -------------------------------------------


class Port100Side(CardSide):
    def __init__(self, clf, tech: str) -> None:
        self.clf = clf
        self.auto = tech == TECH_AUTO
        self.tech = tech
        self.pcd: Optional[isodep.Pcd] = None

    @classmethod
    def open(cls, tech: str, device_index: int) -> "Port100Side":
        from .reader import open_port100

        return cls(open_port100(device_index), tech)

    def label(self) -> str:
        from .reader import label

        return label(self.clf)

    def close(self) -> None:
        self.clf.close()

    def _transceive(self, frame: bytes, timeout_ms: int) -> bytes:
        import nfc.clf

        try:
            resp = self.clf.exchange(bytearray(frame), max(timeout_ms, 1) / 1000.0)
        except nfc.clf.TimeoutError:
            raise CardError("card gave no response (timeout)") from None
        except nfc.clf.CommunicationError as e:
            raise CardError("RF communication error: %s" % (e or type(e).__name__)) from None
        if not resp:
            raise CardError("card gave no response")
        return bytes(resp)

    def _reset_field(self) -> None:
        # Drop the RF field so a card left in layer 4 resets and answers
        # REQA/REQB again, as on a fresh tap.
        self.pcd = None
        try:
            self.clf.device.mute()
        except Exception as e:  # pragma: no cover - hardware dependent
            log.debug("RF off failed: %s", e)
        time.sleep(0.01)

    def activate(self, timeout_ms: int) -> str:
        self._reset_field()
        if self.auto:
            return retry_until(timeout_ms, lambda: self._activate_any(timeout_ms))
        if self.tech == TECH_A:
            return retry_until(timeout_ms, lambda: self._activate_type_a(timeout_ms))
        return retry_until(timeout_ms, lambda: self._activate_type_b(timeout_ms))

    def _activate_any(self, timeout_ms: int) -> str:
        # Poll Type A then Type B, but try the last detected technology first
        # so re-activating the same card does not cost an extra sense.
        order = (TECH_B, TECH_A) if self.tech == TECH_B else (TECH_A, TECH_B)
        errors = []
        for tech in order:
            activate = self._activate_type_a if tech == TECH_A else self._activate_type_b
            try:
                info = activate(timeout_ms)
            except CardError as e:
                errors.append(str(e))
                continue
            self.tech = tech
            return info
        raise CardError("; ".join(errors))

    def _activate_type_a(self, timeout_ms: int) -> str:
        import nfc.clf

        found = self.clf.sense(nfc.clf.RemoteTarget("106A"), iterations=1)
        if found is None or not found.sel_res:
            raise CardError("no NFC-A card detected")
        if found.sel_res[0] & SAK_ISO_DEP == 0:
            raise CardError("NFC-A card is not ISO14443-4 (no ISO-DEP to relay)")
        # RATS: FSDI=8 (256-byte frames), CID=0.
        ats = self._transceive(b"\xE0\x80", timeout_ms)
        fsc = isodep.fsc_from_ats(ats)
        log.info(
            "NFC-A ISO-DEP card: UID=%s ATS=%s (FSC=%d)",
            bytes(found.sdd_res or b"").hex(), ats.hex(), fsc,
        )
        self.pcd = isodep.Pcd(fsc)
        return ats.hex()

    def _activate_type_b(self, timeout_ms: int) -> str:
        import nfc.clf

        found = self.clf.sense(nfc.clf.RemoteTarget("106B"), iterations=1)
        sensb_res = bytes(getattr(found, "sensb_res", None) or b"")
        if found is None or not sensb_res:
            raise CardError("no NFC-B card detected")
        if len(sensb_res) < 12:
            raise CardError("SENSB_RES too short: %s" % sensb_res.hex())
        # ATTRIB: 0x1D + PUPI(4) + Param1..4. FSDI is the *low* nibble of
        # Param2, so Param2=0x08 -> FSDI=8 (256-byte frames); Param3=0x01
        # selects ISO14443-4; CID=0. (A swapped nibble here, 0x80, makes the
        # card misread FSD and silently drop every data-phase I-block.)
        attrib = b"\x1D" + sensb_res[1:5] + b"\x00\x08\x01\x00"
        self._transceive(attrib, timeout_ms)
        fsc = isodep.frame_size_from_code(sensb_res[10] >> 4)
        # SENSB_RES protocol-info byte 11: b2 (0x02) = CID supported, b1 (0x01)
        # = NAD supported. Only include a CID byte when the card supports CID.
        cid = 0 if sensb_res[11] & 0x02 else None
        log.info("NFC-B ISO-DEP card: SENSB_RES=%s (FSC=%d, CID=%s)", sensb_res.hex(), fsc, cid)
        self.pcd = isodep.Pcd(fsc, cid)
        return sensb_res.hex()

    def exchange(self, apdu: bytes, timeout_ms: int) -> bytes:
        if self.pcd is None:
            raise CardError("no activated card")
        try:
            return self.pcd.transmit(lambda frame: self._transceive(frame, timeout_ms), apdu)
        except isodep.IsoDepError as e:
            raise CardError(str(e)) from None


# ---- PC/SC (e.g. RC-S300) via pyscard ---------------------------------------


class PcscSide(CardSide):
    def __init__(self, reader, tech: str) -> None:
        self.reader = reader
        self.auto = tech == TECH_AUTO
        self.tech = tech
        self.connection = None

    @classmethod
    def open(cls, tech: str, name: Optional[str] = None) -> "PcscSide":
        try:
            from smartcard.System import readers
        except ImportError:
            raise CardError(
                "the PC/SC backend needs pyscard: pip install pyscard"
            ) from None
        available = readers()
        if not available:
            raise CardError("no PC/SC readers found")
        if name:
            matches = [r for r in available if name.lower() in str(r).lower()]
        else:
            # Prefer a Sony reader (RC-S300 / PaSoRi) when several are attached.
            matches = [r for r in available if "sony" in str(r).lower()] or available
        if not matches:
            raise CardError(
                "no PC/SC reader matching %r (have: %s)" % (name, ", ".join(map(str, available)))
            )
        return cls(matches[0], tech)

    def label(self) -> str:
        return "PC/SC: %s" % self.reader

    def close(self) -> None:
        self._disconnect()

    def _disconnect(self) -> None:
        if self.connection is not None:
            try:
                self.connection.disconnect()
            except Exception:  # pragma: no cover - best effort
                pass
            self.connection = None

    def activate(self, timeout_ms: int) -> str:
        self._disconnect()

        def attempt() -> str:
            from smartcard.Exceptions import CardConnectionException, NoCardException

            connection = self.reader.createConnection()
            try:
                connection.connect()
            except (NoCardException, CardConnectionException) as e:
                raise CardError("no card on %s: %s" % (self.reader, e)) from None
            self.connection = connection
            return bytes(connection.getATR()).hex()

        atr = retry_until(timeout_ms, attempt)
        if self.auto:
            self.tech = tech_from_atr(bytes.fromhex(atr))
        log.info("ISO-DEP card via PC/SC (NFC-%s): ATR=%s", self.tech, atr)
        return atr

    def exchange(self, apdu: bytes, timeout_ms: int) -> bytes:
        if self.connection is None:
            raise CardError("no activated card")
        from smartcard.Exceptions import CardConnectionException

        try:
            data, sw1, sw2 = self.connection.transmit(list(apdu))
        except CardConnectionException as e:
            raise CardError("PC/SC transmit failed: %s" % e) from None
        return bytes(data) + bytes([sw1, sw2])


def tech_from_atr(atr: bytes) -> str:
    """Guesses A or B from a PC/SC contactless ATR (PC/SC Part 3).

    The reader builds a Type B card's historical bytes from the ATQB:
    application data (4) + protocol info (3) + MBLI/CID (1), i.e. exactly 8
    bytes; a Type A card's come from its ATS and rarely have that length. The
    reader runs ISO-DEP itself, so this is only a label for logging.
    """
    if len(atr) >= 4 and atr[0] == 0x3B and atr[2:4] == b"\x80\x01" and atr[1] == 0x88:
        return TECH_B
    return TECH_A


# ---- factory ----------------------------------------------------------------

READER_PORT100 = "port100"
READER_PCSC = "pcsc"


def open_card_side(reader: str, tech: str, device_index: int, pcsc_name: Optional[str]) -> CardSide:
    if tech not in (TECH_A, TECH_B, TECH_AUTO):
        raise ValueError("tech must be A, B or auto")
    if reader == READER_PORT100:
        return Port100Side.open(tech, device_index)
    if reader == READER_PCSC:
        return PcscSide.open(tech, pcsc_name)
    raise ValueError("unknown reader backend %r" % reader)
