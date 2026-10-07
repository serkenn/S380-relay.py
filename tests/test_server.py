import io

from s380_relay import protocol as p
from s380_relay import server
from s380_relay.cardside import CardError, CardSide


class FakeCard(CardSide):
    def __init__(self, tech="A", responses=None, fail_times=0):
        self.tech = tech
        self.responses = responses or {}
        self.fail_times = fail_times
        self.activations = 0

    def label(self):
        return "FakeCard"

    def activate(self, timeout_ms):
        self.activations += 1
        return "3b8f8001"  # fake ATR/ATS hex

    def exchange(self, apdu, timeout_ms):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise CardError("transient")
        key = apdu.hex()
        if key in self.responses:
            return self.responses[key]
        return b"\x6a\x82"  # file not found


def drive(card, requests, config=None):
    """Feeds encoded requests through handle_client, returns decoded responses."""
    config = config or server.ServerConfig()
    rfile = io.BytesIO(b"".join(p.encode(r) for r in requests))
    wfile = io.BytesIO()
    server.handle_client(rfile, wfile, card, config)
    wfile.seek(0)
    return [p.decode_response(line) for line in wfile.read().splitlines()]


def test_get_card_then_apdu():
    card = FakeCard(responses={"00a40400": b"\x6f\x10\x90\x00"})
    out = drive(card, [p.GetCard(), p.ApduRequest(bytes.fromhex("00a40400"))])
    assert isinstance(out[0], p.Card) and out[0].tech == "A" and out[0].info == "3b8f8001"
    assert isinstance(out[1], p.ApduResponse) and out[1].data == bytes.fromhex("6f109000")


def test_apdu_without_get_card_auto_activates():
    card = FakeCard()
    out = drive(card, [p.ApduRequest(bytes.fromhex("00a40000"))])
    assert isinstance(out[0], p.ApduResponse)
    assert card.activations == 1


def test_select_by_aid_replayed_after_reactivation():
    # SELECT by AID (P1=04) sets its own state, so it is safe to replay.
    aid = "00a40400" + "0a" + "d392f00026010000000100"[:20]
    card = FakeCard(responses={aid: b"\x6f\x10\x90\x00"}, fail_times=1)
    out = drive(card, [p.GetCard(), p.ApduRequest(bytes.fromhex(aid))])
    assert isinstance(out[1], p.ApduResponse) and out[1].data == bytes.fromhex("6f109000")
    assert card.activations == 2  # initial + re-activation


def test_non_select_not_replayed_after_reactivation():
    # READ BINARY depends on the selected EF, so after re-activation it must be
    # reported as a failure, not replayed against a reset card.
    card = FakeCard(responses={"00b0000000": b"\x01\x90\x00"}, fail_times=1)
    out = drive(card, [p.GetCard(), p.ApduRequest(bytes.fromhex("00b0000000"))])
    assert isinstance(out[1], p.Error)
    assert "state lost" in out[1].message
    assert card.activations == 2  # re-activated once, but not replayed


def test_is_select_by_aid():
    assert server.is_select_by_aid(bytes.fromhex("00a4040c0ad392f00026010000000100"))
    assert not server.is_select_by_aid(bytes.fromhex("00a4020c02000a"))  # SELECT EF by id
    assert not server.is_select_by_aid(bytes.fromhex("00b0000000"))  # READ BINARY
    assert not server.is_select_by_aid(bytes.fromhex("00a4"))


def test_bad_request_line_reports_error_and_continues():
    card = FakeCard()
    rfile = io.BytesIO(b"not json\n" + p.encode(p.GetCard()))
    wfile = io.BytesIO()
    server.handle_client(rfile, wfile, card, server.ServerConfig())
    wfile.seek(0)
    out = [p.decode_response(line) for line in wfile.read().splitlines()]
    assert isinstance(out[0], p.Error)
    assert isinstance(out[1], p.Card)


def test_parse_addr():
    assert server.parse_addr("0.0.0.0:7878") == ("0.0.0.0", 7878)
    assert server.parse_addr("7878") == ("127.0.0.1", 7878)
    assert server.parse_addr("[::1]:9999") == ("::1", 9999)
