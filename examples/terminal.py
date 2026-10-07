"""Test "terminal" that drives an RC-S380 as an ISO-DEP reader to tap the
emulated card (the client, or an Android HCE app), exercising the whole relay:

    RC-S380 (this tool) --NFC--> client/HCE --TCP--> server --NFC--> real card

It polls NFC-A, activates ISO-DEP (RATS), then relays a few SELECTs through the
real `isodep.Pcd` state machine (so S(WTX) waits from the slow round trip are
handled) and prints each response. A real status word means the relay carried
the APDU back from the real card.

    python examples/terminal.py [device-index]
"""

import sys
import time

from s380_relay import isodep
from s380_relay.reader import open_port100

# Real applets of a Japanese My Number card: a successful SELECT returns an FCI
# template and 9000, proving the relay carries real transactions.
APDUS = [
    ("SELECT kenmen-hojo AP", "00a404000ad392100031000101040800"),
    ("SELECT JPKI AP", "00a404000ad392f00026010000000100"),
    ("SELECT juki AP", "00a404000ad392100031000101010000"),
]


def main():
    import nfc.clf

    index = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    clf = open_port100(index)
    print("RC-S380 terminal ready. Tap the emulated card (client/HCE) to this reader...")

    deadline = time.monotonic() + 20
    found = None
    while time.monotonic() < deadline:
        found = clf.sense(nfc.clf.RemoteTarget("106A"), iterations=1)
        if found is not None:
            break
        time.sleep(0.2)
    if found is None:
        print("no NFC-A target detected in 20s")
        clf.close()
        return
    print("NFC-A target: UID=%s" % bytes(found.sdd_res or b"").hex())

    # RATS (FSDI=8, CID=0) to enter ISO-DEP layer 4.
    try:
        ats = bytes(clf.exchange(bytearray(b"\xE0\x80"), 1.0))
    except nfc.clf.CommunicationError as e:
        print("RATS failed: %s" % e)
        clf.close()
        return
    print("ATS = %s" % ats.hex())
    pcd = isodep.Pcd(isodep.fsc_from_ats(ats))

    def send(block):
        # The round trip is slow; give each block a generous window. The Pcd
        # answers any S(WTX) raised while the client talks to the server.
        resp = clf.exchange(bytearray(block), 5.0)
        if not resp:
            raise isodep.IsoDepError("no response")
        return bytes(resp)

    for name, apdu_hex in APDUS:
        try:
            resp = pcd.transmit(send, bytes.fromhex(apdu_hex))
            print("%s -> %s" % (name, resp.hex()))
        except (isodep.IsoDepError, nfc.clf.CommunicationError) as e:
            print("%s -> ERROR: %s" % (name, e))
    clf.close()


if __name__ == "__main__":
    main()
