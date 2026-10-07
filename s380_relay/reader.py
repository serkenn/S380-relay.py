"""Finding and opening a *specific* RC-S380 through nfcpy.

nfcpy's ``ContactlessFrontend("usb:054c:06c1")`` opens the first matching
reader, so with two identical RC-S380 readers on one host it can only reach one
of them. This module enumerates every attached Port-100 reader in a stable
bus/address order and opens the one at a given index via its ``usb:BBB:DDD``
path, so a server and a client can each drive their own reader on one machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

SONY_VENDOR_ID = 0x054C
# Port-100 product ids: RC-S380/S, RC-S634/UA (RC-S380 OEM), RC-S380/P.
PORT100_PRODUCT_IDS = (0x06C1, 0x06C2, 0x06C3)


@dataclass(frozen=True)
class ReaderLocation:
    index: int
    bus: int
    address: int
    product_id: int

    @property
    def path(self) -> str:
        return "usb:%03d:%03d" % (self.bus, self.address)


def list_port100() -> List[ReaderLocation]:
    """Lists every attached Port-100 reader, in a stable bus/address order."""
    from nfc.clf.transport import USB

    found = set()
    for pid in PORT100_PRODUCT_IDS:
        for vid, pid_, bus, address in USB.find("usb:%04x:%04x" % (SONY_VENDOR_ID, pid)) or []:
            found.add((bus, address, pid_))
    return [
        ReaderLocation(index, bus, address, pid)
        for index, (bus, address, pid) in enumerate(sorted(found))
    ]


def open_port100(index: int):
    """Opens the Port-100 reader at ``index`` (see :func:`list_port100`) and
    returns an ``nfc.ContactlessFrontend``."""
    import nfc

    readers = list_port100()
    if index < 0 or index >= len(readers):
        raise IOError(
            "no RC-S380 (Port-100) reader at index %d (%d attached)" % (index, len(readers))
        )
    location = readers[index]
    try:
        clf = nfc.ContactlessFrontend(location.path)
    except IOError as e:
        raise IOError("could not open RC-S380 at %s: %s" % (location.path, e)) from e
    return clf


def label(clf) -> str:
    """Human-readable reader identification, for logging."""
    device = getattr(clf, "device", None)
    if device is None:
        return "Unknown"
    return "%s - %s (%s)" % (
        getattr(device, "vendor_name", None) or "Unknown",
        getattr(device, "product_name", None) or "Unknown",
        getattr(device, "path", None) or "?",
    )
