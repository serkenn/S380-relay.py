from s380_relay import isodep


def test_pcb_classification():
    assert isodep.is_i_block(0x02)
    assert isodep.is_i_block(0x03)
    assert isodep.has_chaining(0x12)
    assert not isodep.has_chaining(0x02)
    assert isodep.is_r_block(0xA2)
    assert isodep.is_r_block(0xB3)
    assert isodep.is_s_block(0xF2)
    assert isodep.is_s_wtx(0xF2)
    assert isodep.is_s_deselect(0xC2)
    assert isodep.block_number(0x03) == 1


def test_inf_skips_cid_and_nad():
    assert isodep.inf(bytes([0x02, 0xAA, 0xBB])) == b"\xAA\xBB"
    # I-block with CID (0x08) and NAD (0x04): skip two extra bytes
    assert isodep.inf(bytes([0x0E, 0x00, 0x00, 0xAA])) == b"\xAA"


def test_frame_sizes():
    assert isodep.frame_size_from_code(0) == 16
    assert isodep.frame_size_from_code(8) == 256
    assert isodep.frame_size_from_code(15) == 256
    assert isodep.fsc_from_ats(bytes([0x05, 0x78, 0x80, 0x70, 0x02])) == 256


def test_single_block_exchange_toggles_block_number():
    pcd = isodep.Pcd(256)
    sent = []

    def send(frame):
        sent.append(bytes(frame))
        return bytes([isodep.i_block(isodep.block_number(frame[0]), False), 0x90, 0x00])

    resp = pcd.transmit(send, bytes([0x00, 0xA4, 0x04, 0x00]))
    assert resp == b"\x90\x00"
    assert len(sent) == 1
    assert sent[0][0] == 0x02  # first I-block, block number 0
    assert pcd.block == 1  # toggled for the next APDU


def test_wtx_request_is_answered_then_response_read():
    pcd = isodep.Pcd(256)
    steps = {"n": 0}

    def send(frame):
        steps["n"] += 1
        if steps["n"] == 1:
            return bytes([0xF2, 0x05])  # card asks for more time
        assert bytes(frame) == bytes([0xF2, 0x05])  # we echo WTXM
        return bytes([isodep.i_block(0, False), 0x6A, 0x82])

    assert pcd.transmit(send, bytes([0x00, 0xA4])) == b"\x6A\x82"


def test_cid_is_included_in_blocks_and_skipped_on_response():
    pcd = isodep.Pcd(256, cid=0)
    sent = []

    def send(frame):
        sent.append(bytes(frame))
        return bytes([isodep.i_block(0, False) | 0x08, 0x00, 0x90, 0x00])

    resp = pcd.transmit(send, bytes([0x00, 0xA4]))
    assert sent[0] == bytes([0x0A, 0x00, 0x00, 0xA4])
    assert resp == b"\x90\x00"


def test_chained_response_is_reassembled():
    pcd = isodep.Pcd(256)
    steps = {"n": 0}

    def send(frame):
        steps["n"] += 1
        if steps["n"] == 1:
            return bytes([isodep.i_block(0, True), 0x11, 0x22])  # chaining
        return bytes([isodep.i_block(1, False), 0x33])  # final

    assert pcd.transmit(send, bytes([0x00, 0xB0])) == b"\x11\x22\x33"


def test_command_chaining_splits_long_apdu():
    pcd = isodep.Pcd(16)  # max_inf = 16 - 3 = 13
    apdu = bytes(range(30))
    sent = []

    def send(frame):
        sent.append(bytes(frame))
        pcb = frame[0]
        if isodep.has_chaining(pcb):
            return bytes([isodep.r_ack(isodep.block_number(pcb) ^ 1)])  # R(ACK)
        return bytes([isodep.i_block(isodep.block_number(pcb), False), 0x90, 0x00])

    resp = pcd.transmit(send, apdu)
    assert resp == b"\x90\x00"
    # 30 bytes / 13 per block -> 3 command blocks, first two chained.
    assert len(sent) == 3
    assert isodep.has_chaining(sent[0][0]) and isodep.has_chaining(sent[1][0])
    assert not isodep.has_chaining(sent[2][0])
    # Payload reassembles to the original APDU.
    assert b"".join(isodep.inf(s) for s in sent) == apdu
