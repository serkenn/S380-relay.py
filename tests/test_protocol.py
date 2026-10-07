import json

import pytest

from s380_relay import protocol as p


def test_get_card_round_trips():
    line = p.encode(p.GetCard())
    assert json.loads(line) == {"type": "get_card"}
    assert isinstance(p.decode_request(line), p.GetCard)


def test_apdu_request_round_trips_with_and_without_timeout():
    line = p.encode(p.ApduRequest(bytes.fromhex("00a40400"), 500))
    req = p.decode_request(line)
    assert isinstance(req, p.ApduRequest)
    assert req.data == bytes.fromhex("00a40400")
    assert req.timeout_ms == 500

    req2 = p.decode_request('{"type":"apdu","data":"00a4"}')
    assert isinstance(req2, p.ApduRequest) and req2.timeout_ms is None


def test_card_and_apdu_responses_round_trip():
    card = p.decode_response(p.encode(p.Card("B", "5090be4e5b")))
    assert isinstance(card, p.Card) and card.tech == "B" and card.info == "5090be4e5b"

    apdu = p.decode_response(p.encode(p.ApduResponse(bytes.fromhex("9000"))))
    assert isinstance(apdu, p.ApduResponse) and apdu.data == b"\x90\x00"

    err = p.decode_response(p.encode(p.Error("boom")))
    assert isinstance(err, p.Error) and err.message == "boom"


def test_hex_is_lowercase_and_compact():
    assert p.encode(p.ApduResponse(b"\x6f\x00\x90\x00")) == b'{"type":"apdu","data":"6f009000"}\n'


def test_rust_wire_examples_decode():
    # Lines taken verbatim from the Rust README's protocol section.
    assert isinstance(p.decode_request('{"type":"get_card"}'), p.GetCard)
    req = p.decode_request('{"type":"apdu","data":"00A4040007A0000002471001","timeout_ms":1000}')
    assert req.data.hex() == "00a4040007a0000002471001"
    card = p.decode_response('{"type":"card","tech":"B","info":"5090be4e5b000005e0b381a100"}')
    assert isinstance(card, p.Card) and card.tech == "B"


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        "{}",
        '{"type":123}',
        '{"type":"apdu"}',  # missing data
        '{"type":"apdu","data":"zz"}',  # bad hex
        '{"type":"nope"}',
    ],
)
def test_bad_requests_raise(line):
    with pytest.raises(p.ProtocolError):
        p.decode_request(line)


@pytest.mark.parametrize(
    "line",
    [
        '{"type":"card","tech":"C","info":""}',  # bad tech
        '{"type":"apdu","data":"zz"}',
        '{"type":"nope"}',
    ],
)
def test_bad_responses_raise(line):
    with pytest.raises(p.ProtocolError):
        p.decode_response(line)


def test_timeout_bounds():
    with pytest.raises(p.ProtocolError):
        p.decode_request('{"type":"apdu","data":"00","timeout_ms":70000}')
    # bool must not pass as int
    with pytest.raises(p.ProtocolError):
        p.decode_request('{"type":"apdu","data":"00","timeout_ms":true}')
