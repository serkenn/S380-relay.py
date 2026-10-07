"""RC-S380 (Port-100) Type B probe — the Python counterpart of the Rust
`probe100` / `probe100b` examples.

It polls NFC-B for up to 30s (place/reposition a Type-B card and watch whether
the reader senses it, i.e. returns SENSB_RES). On detection it optionally runs
the data phase — ATTRIB, then one I-block (SELECT PPSE) — and prints the card's
reply, so you can see whether the RC-S380's Type-B data phase answers at all and
whether the CID byte matters.

    python examples/probe_typeb.py [index]           # sense + data phase WITH CID
    python examples/probe_typeb.py [index] nocid     # data phase WITHOUT the CID byte
    python examples/probe_typeb.py [index] senseonly # only sense, no data phase

The RC-S380 carries the Type-B data phase fine (this was used to verify it);
this probe remains a handy bring-up diagnostic and a way to check whether a
given card expects the CID byte.
"""

import sys
import time

from s380_relay import isodep
from s380_relay.reader import open_port100


def main():
    import nfc.clf

    args = sys.argv[1:]
    index = 0
    mode = "cid"
    for a in args:
        if a.isdigit():
            index = int(a)
        elif a in ("nocid", "senseonly", "cid"):
            mode = a

    clf = open_port100(index)
    print("RC-S380 opened; polling NFC-B for 30s. Place a Type-B card on the antenna...")

    deadline = time.monotonic() + 30
    found = None
    while time.monotonic() < deadline:
        found = clf.sense(nfc.clf.RemoteTarget("106B"), iterations=1)
        if found is not None and getattr(found, "sensb_res", None):
            break
        found = None
        time.sleep(0.2)
    if found is None:
        print("no NFC-B card detected in 30s")
        clf.close()
        return

    sensb_res = bytes(found.sensb_res)
    print("SENSB_RES = %s" % sensb_res.hex())
    if len(sensb_res) >= 12:
        pupi = sensb_res[1:5]
        fsci = sensb_res[10] >> 4
        cid_supported = bool(sensb_res[11] & 0x02)
        print(
            "  PUPI=%s  FSC=%d (FSCI=%d)  CID-supported=%s"
            % (pupi.hex(), isodep.frame_size_from_code(fsci), fsci, cid_supported)
        )

    if mode == "senseonly":
        clf.close()
        return

    # ATTRIB: 0x1D + PUPI(4) + Param1..4. FSDI is Param2's low nibble, so
    # 0x08 -> FSDI=8 (256-byte frames); Param3=0x01 selects ISO14443-4; CID=0.
    attrib = b"\x1D" + sensb_res[1:5] + b"\x00\x08\x01\x00"
    try:
        print("ATTRIB -> %s" % bytes(clf.exchange(bytearray(attrib), 1.0)).hex())
    except nfc.clf.CommunicationError as e:
        print("ATTRIB failed: %s" % e)
        clf.close()
        return

    # One I-block: SELECT PPSE, WITH or WITHOUT the CID byte.
    ppse = bytes.fromhex("00a404000e325041592e5359532e4444463031")
    if mode == "nocid":
        block = bytes([isodep.i_block(0, False)]) + ppse  # 0x02 ...
    else:
        block = bytes([isodep.i_block(0, False) | 0x08, 0x00]) + ppse  # 0x0A 00 ...
    print("I-block (%s) -> " % ("no CID" if mode == "nocid" else "CID 0"), end="")
    try:
        resp = clf.exchange(bytearray(block), 2.0)
        print(bytes(resp).hex() if resp else "(no response)")
    except nfc.clf.CommunicationError as e:
        print("ERROR: %s" % e)
    clf.close()


if __name__ == "__main__":
    main()
