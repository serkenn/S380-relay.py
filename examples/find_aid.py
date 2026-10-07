"""Probe the real card (via the running server) with a list of candidate AIDs
to identify it. Prints the status word for each SELECT; anything that is not
"not found" is a hit worth noting.

    python examples/find_aid.py [host:port]
"""

import sys

from s380_relay.client import ServerLink, RelayLinkError

CANDIDATES = [
    ("My Number: kenmen-hojo", "D3921000310001010408"),
    ("My Number: kenmen-kakunin", "D3921000310001010402"),
    ("My Number: JPKI", "D392f0002601000000"),
    ("My Number: juki", "D3921000310001010100"),
    ("Driver license DF1", "A0000002310100000000"),
    ("Driver license common", "A0000002310101"),
    ("Residence card", "D392f000d401000000"),
    ("eMRTD LDS1", "A0000002471001"),
    ("eMRTD LDS2 travel", "A0000002472001"),
    ("EMV PPSE (by name)", "325041592e5359532e4444463031"),
    ("NDEF Type 4", "D2760000850101"),
]

NOT_FOUND = {"6a82", "6a86", "6a80", "6d00", "6e00"}


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:7878"
    link = ServerLink(addr)
    try:
        card = link.connect()
    except RelayLinkError as e:
        print("connect failed: %s" % e)
        return
    print("get_card -> NFC-%s (%s)\n" % (card.tech, card.info))

    for name, aid in CANDIDATES:
        aid_bytes = bytes.fromhex(aid)
        # SELECT by DF name: 00 A4 04 00 Lc <AID> 00 (with Le).
        apdu = bytes([0x00, 0xA4, 0x04, 0x00, len(aid_bytes)]) + aid_bytes + b"\x00"
        try:
            resp = link.apdu(apdu, 3000)
        except RelayLinkError as e:
            print("%-28s: ERR %s" % (name, e))
            continue
        hexresp = resp.hex()
        sw = hexresp[-4:]
        hit = resp and sw not in NOT_FOUND
        print("%-28s: %s%s" % (name, hexresp, "  <== HIT" if hit else ""))
    link.close()


if __name__ == "__main__":
    main()
