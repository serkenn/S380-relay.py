"""Wire protocol between the relay server (card side) and client (phone side).

Messages are newline-delimited JSON, identical to the Rust ``s380-relay`` and
the Android HCE client, so any combination of the implementations interoperates.
The relay works at the **APDU** layer: each side terminates ISO-DEP
(ISO14443-4) on its own reader, so only ISO 7816-4 APDUs cross the link.

::

    // client -> server
    {"type":"get_card"}
    {"type":"apdu","data":"00A4040007A0000002471001","timeout_ms":1000}
    // server -> client
    {"type":"card","tech":"B","info":"5090be4e5b..."}
    {"type":"apdu","data":"6F..9000"}
    {"type":"error","message":"..."}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Union

TECH_A = "A"
TECH_B = "B"
TECHS = (TECH_A, TECH_B)


class ProtocolError(ValueError):
    """A line on the link is not a valid relay message."""


# ---- requests (client -> server) -------------------------------------------


@dataclass(frozen=True)
class GetCard:
    """Activate the real card into ISO-DEP (layer 4) and report it."""


@dataclass(frozen=True)
class ApduRequest:
    """Relay one command APDU to the real card and return its response."""

    data: bytes
    timeout_ms: Optional[int] = None


Request = Union[GetCard, ApduRequest]


# ---- responses (server -> client) ------------------------------------------


@dataclass(frozen=True)
class Card:
    """The real card has been activated into ISO-DEP."""

    tech: str
    info: str  # ATS / ATQB / ATR (hex), for logging


@dataclass(frozen=True)
class ApduResponse:
    """The real card's response APDU."""

    data: bytes


@dataclass(frozen=True)
class Error:
    """An error occurred on the server side (card removed, protocol error, ...)."""

    message: str


Response = Union[Card, ApduResponse, Error]


# ---- encoding ---------------------------------------------------------------


def encode(message: Union[Request, Response]) -> bytes:
    """Serializes a message as one JSON line (with the trailing newline)."""
    if isinstance(message, GetCard):
        obj: dict = {"type": "get_card"}
    elif isinstance(message, ApduRequest):
        obj = {"type": "apdu", "data": message.data.hex()}
        if message.timeout_ms is not None:
            obj["timeout_ms"] = message.timeout_ms
    elif isinstance(message, Card):
        obj = {"type": "card", "tech": message.tech, "info": message.info}
    elif isinstance(message, ApduResponse):
        obj = {"type": "apdu", "data": message.data.hex()}
    elif isinstance(message, Error):
        obj = {"type": "error", "message": message.message}
    else:
        raise TypeError("not a relay message: %r" % (message,))
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


def _load(line: Union[str, bytes]) -> dict:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    try:
        obj = json.loads(line)
    except ValueError as e:
        raise ProtocolError("invalid JSON: %s" % e) from None
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        raise ProtocolError("missing field `type`")
    return obj


def _hex(obj: dict, field: str) -> bytes:
    value = obj.get(field)
    if not isinstance(value, str):
        raise ProtocolError("missing field `%s`" % field)
    try:
        return bytes.fromhex(value.strip())
    except ValueError as e:
        raise ProtocolError("bad hex in `%s`: %s" % (field, e)) from None


def decode_request(line: Union[str, bytes]) -> Request:
    obj = _load(line)
    kind = obj["type"]
    if kind == "get_card":
        return GetCard()
    if kind == "apdu":
        timeout = obj.get("timeout_ms")
        if timeout is not None and (
            not isinstance(timeout, int) or isinstance(timeout, bool) or not 0 <= timeout <= 0xFFFF
        ):
            raise ProtocolError("invalid `timeout_ms`: %r" % (timeout,))
        return ApduRequest(_hex(obj, "data"), timeout)
    raise ProtocolError("unknown request type `%s`" % kind)


def decode_response(line: Union[str, bytes]) -> Response:
    obj = _load(line)
    kind = obj["type"]
    if kind == "card":
        tech = obj.get("tech")
        if tech not in TECHS:
            raise ProtocolError("invalid `tech`: %r" % (tech,))
        return Card(tech, str(obj.get("info", "")))
    if kind == "apdu":
        return ApduResponse(_hex(obj, "data"))
    if kind == "error":
        return Error(str(obj.get("message", "")))
    raise ProtocolError("unknown response type `%s`" % kind)
