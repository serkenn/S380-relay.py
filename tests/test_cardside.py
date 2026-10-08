import sys
import types

import pytest

from s380_relay import cardside
from s380_relay.cardside import TECH_AUTO, CardError, Port100Side


class RemoteTarget:
    def __init__(self, brty):
        self.brty = brty


class TimeoutError(Exception):
    pass


class CommunicationError(Exception):
    pass


@pytest.fixture(autouse=True)
def fake_nfc(monkeypatch):
    # Port100Side imports nfc.clf lazily; nfcpy itself is not needed here.
    clf = types.ModuleType("nfc.clf")
    clf.RemoteTarget = RemoteTarget
    clf.TimeoutError = TimeoutError
    clf.CommunicationError = CommunicationError
    nfc = types.ModuleType("nfc")
    nfc.clf = clf
    monkeypatch.setitem(sys.modules, "nfc", nfc)
    monkeypatch.setitem(sys.modules, "nfc.clf", clf)


# PUPI 01020304, FSCI 8 (0x80 in byte 10), CID supported (0x02 in byte 11).
SENSB_RES = bytes.fromhex("500102030400000000" + "0080" + "02")
ATS = bytes.fromhex("0578800270")


class FakeClf:
    """Answers only the sense for ``card_tech``, records every sense."""

    def __init__(self, card_tech):
        self.card_tech = card_tech
        self.senses = []
        self.device = types.SimpleNamespace(mute=lambda: None)

    def sense(self, target, iterations=1):
        self.senses.append(target.brty)
        if target.brty == "106A" and self.card_tech == "A":
            return types.SimpleNamespace(sel_res=b"\x20", sdd_res=b"\x01\x02\x03\x04")
        if target.brty == "106B" and self.card_tech == "B":
            return types.SimpleNamespace(sensb_res=SENSB_RES)
        return None

    def exchange(self, frame, timeout):
        if frame[0] == 0xE0:  # RATS
            return bytearray(ATS)
        if frame[0] == 0x1D:  # ATTRIB
            return bytearray(b"\x00")
        raise AssertionError("unexpected frame %s" % bytes(frame).hex())


def test_auto_detects_type_b():
    clf = FakeClf("B")
    side = Port100Side(clf, TECH_AUTO)
    assert side.activate(100) == SENSB_RES.hex()
    assert side.tech == "B"
    assert clf.senses == ["106A", "106B"]


def test_auto_detects_type_a():
    clf = FakeClf("A")
    side = Port100Side(clf, TECH_AUTO)
    assert side.activate(100) == ATS.hex()
    assert side.tech == "A"
    assert clf.senses == ["106A"]


def test_auto_reactivation_tries_last_tech_first():
    clf = FakeClf("B")
    side = Port100Side(clf, TECH_AUTO)
    side.activate(100)
    clf.senses.clear()
    side.activate(100)
    assert clf.senses == ["106B"]


def test_auto_reports_both_errors_without_card():
    side = Port100Side(FakeClf(None), TECH_AUTO)
    with pytest.raises(CardError) as e:
        side.activate(1)
    assert "NFC-A" in str(e.value) and "NFC-B" in str(e.value)


def test_fixed_tech_does_not_poll_other():
    clf = FakeClf("B")
    side = Port100Side(clf, "A")
    with pytest.raises(CardError):
        side.activate(1)
    assert set(clf.senses) == {"106A"}


def test_tech_from_atr():
    # Type B: 8 historical bytes built from the ATQB.
    assert cardside.tech_from_atr(bytes.fromhex("3b888001e1f35e1177a1810082")) == "B"
    # Type A: historical bytes from the ATS.
    assert cardside.tech_from_atr(bytes.fromhex("3b8180018080")) == "A"
