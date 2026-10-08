"""s380-relay (Python): relay ISO14443 (NFC-A/B) smartcard traffic between two
readers over the network, at the APDU layer, using nfcpy for the RC-S380.

See :mod:`s380_relay.protocol` for the wire format (shared with the Rust and
Android implementations), :mod:`s380_relay.cardside` for the card side, and
:mod:`s380_relay.client` for the phone side.
"""

__version__ = "0.2.0"
