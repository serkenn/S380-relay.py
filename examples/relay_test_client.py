"""Minimal relay test client — no reader needed. Connects to the relay server
over TCP, sends get_card, then a few probe APDUs, printing each JSON reply.
Exercises the server's card-side activate + ISO-DEP data phase against the real
card without tying up the second reader.

    python examples/relay_test_client.py [host:port]
"""

import sys

from s380_relay import protocol as p
from s380_relay.client import ServerLink, RelayLinkError


def main():
    addr = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:7878"
    link = ServerLink(addr)
    try:
        card = link.connect()
    except RelayLinkError as e:
        print("connect failed: %s" % e)
        return
    print("get_card -> NFC-%s (%s)" % (card.tech, card.info))

    apdus = [
        ("SELECT PPSE", "00a404000e325041592e5359532e4444463031"),
        ("SELECT eMRTD LDS1", "00a4040007a0000002471001"),
        ("SELECT MF", "00a40000023f00"),
        ("GET CHALLENGE", "0084000008"),
    ]
    for name, apdu in apdus:
        try:
            resp = link.apdu(bytes.fromhex(apdu), 2000)
            print("%s (%s) -> %s" % (name, apdu, resp.hex()))
        except RelayLinkError as e:
            print("%s -> ERROR: %s" % (name, e))
    link.close()


if __name__ == "__main__":
    main()
