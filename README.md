# s380-relay (Python)

Relay ISO14443 smartcard traffic between two readers over the network, at the
**APDU layer**, built in Python on top of [`nfcpy`](https://nfcpy.readthedocs.io).
This is a Python port of the Rust [`s380-relay`](../S380-relay); it speaks the
**same newline-delimited JSON wire protocol**, so the Python server, the Python
client, the Rust binary and the Android HCE client all interoperate.

> 日本語版は [README_ja.md](README_ja.md) にあります。

```
 terminal ⇢ (Type A / ISO-DEP) ⇢ [client: RC-S380] ──APDU over TCP──▶ [server: RC-S380 / PC-SC] ⇢ real card
            └ ISO-DEP terminated here                                  └ ISO-DEP terminated here
                                     only ISO 7816-4 APDUs cross the link
```

The **server** holds a real card on its reader. The **client** presents a
synthetic Type 4 (NFC-A) card to a terminal/phone. Every command APDU the
terminal sends is relayed over TCP to the server's reader, replayed to the real
card, and the response APDU is played back. From the terminal's point of view it
is talking to the real card. The real card may be **Type A or Type B** — both
converge at ISO14443-4, and only the APDUs cross the link.

For use with **your own readers and cards** (or cards you are authorised to
test) in research, CTF and interoperability testing. Relaying a card you do not
own or are not authorised to test may be illegal; the burden is on you to have
authorisation.

## Why nfcpy

The idea here is the user's: *"RC-S380 なら nfcpy で行ける — emulation はできない
けどコマンドのやり取りはできる"*. nfcpy drives the RC-S380 (NFC Port-100) in both
roles:

- **reader** (`sense` / `exchange`) for the card side and the client's terminal
  front end,
- **card emulation** (`listen` as a Type A Type-4 target, answering RATS) for
  the client/phone side.

The RC-S380 drives **both Type A and Type B** cards on the card side, including
a Type B card's full ISO-DEP data phase. nfcpy has **no RC-S300 (Port-400)
driver**, but a PC/SC reader is not required for Type B: the card side can
optionally use any **PC/SC** reader through `pyscard` — e.g. an RC-S300 /
PaSoRi 4.0 with Sony's own driver — but a single RC-S380 is enough.

## Requirements

- Python 3.8+
- `nfcpy>=1.0` (`pip install nfcpy`)
- Card side via PC/SC (optional): `pyscard` (`pip install "s380-relay[pcsc]"`)
- One Sony RC-S380 per side (two on one host, or one each on two hosts)
- USB access to the reader. On **Windows**, nfcpy talks to the RC-S380 through
  `libusb`, so the reader must be bound to the **WinUSB** driver with
  [Zadig](https://zadig.akeo.ie/) (Options → List All Devices → select
  `SONY RC-S380`, USB ID `054C:06C1` → install **WinUSB**). This replaces Sony's
  FeliCa driver; to go back, uninstall the WinUSB driver in Device Manager.

## Install

```sh
pip install -e .            # from this directory
pip install -e ".[pcsc]"    # with the PC/SC card-side backend
pip install -e ".[dev]"     # with pytest
```

Or run without installing: `python -m s380_relay ...` from this directory.

## Usage

One command, subcommands `server` / `client` / `list`. Logging via `-v` (info)
or `-vv` (debug), or the `S380_LOG` env var.

### List readers

```sh
$ s380-relay list
attached RC-S380 readers:
  index 0  bus 001 addr 014  pid 0x06C1
  index 1  bus 001 addr 015  pid 0x06C1
```

On one host with two readers, give each side a different `--device-index` (the
server defaults to `0`, the client to `1`). On two hosts the defaults work.

### Server (card side)

Place the real card on this reader, then:

```sh
s380-relay -v server --listen 0.0.0.0:7878
```

| Flag | Default | Meaning |
|------|---------|---------|
| `-l, --listen <addr:port>` | `127.0.0.1:7878` | TCP listen address |
| `--tech <a\|b>` | `a` | Real card's ISO14443 technology |
| `--reader <port100\|pcsc>` | `port100` | Card-side backend |
| `--pcsc-name <substr>` | (Sony) | Substring to pick the PC/SC reader |
| `-d, --device-index <n>` | `0` | Which RC-S380 (port100; see `list`) |
| `-t, --timeout <ms>` | `1000` | Per-command timeout |

> **Type B works on the RC-S380.** Both Type A and Type B cards are relayed on
> the card side with `--reader port100` — the RC-S380 carries the Type B data
> phase (ISO-DEP I-/R-/S-blocks, WTX, chaining) end to end; it was verified
> relaying a My Number card. A PC/SC reader (`--reader pcsc`, e.g. an RC-S300 /
> PaSoRi 4.0) is only an optional alternative, not a requirement.

### Client (phone side)

Connect to the server and emulate a Type 4 card; then tap a phone/terminal:

```sh
s380-relay -v client --connect <server-ip>:7878
```

| Flag | Default | Meaning |
|------|---------|---------|
| `-c, --connect <addr:port>` | `127.0.0.1:7878` | Server address |
| `-d, --device-index <n>` | `1` | Which RC-S380 (see `list`) |
| `--no-wtx` | (off) | Do not send S(WTX) before relaying |
| `--wtxm <1-59>` | `10` | Waiting-time extension multiplier |
| `-t, --timeout <ms>` | `1000` | Per-command timeout |
| `-w, --window <seconds>` | `1.0` | Listen window length |

`S(WTX)` is sent to the phone before each relayed command so the network
round-trip stays within the phone's frame-waiting time.

## Wire protocol

Newline-delimited JSON over TCP — identical to the Rust and Android clients:

```jsonc
// client → server
{"type":"get_card"}
{"type":"apdu","data":"00A4040007A0000002471001","timeout_ms":1000}
// server → client
{"type":"card","tech":"B","info":"5090be4e5b000005e0b381a100"}
{"type":"apdu","data":"6F..9000"}
{"type":"error","message":"..."}
```

All `data` fields are hex-encoded ISO 7816-4 APDUs. ISO-DEP framing (PCB, CRC,
chaining, WTX) is handled on each side and never crosses the link.

## Diagnostic examples

Run from this directory (they import the package):

| Example | What it does |
|---------|--------------|
| `python examples/relay_test_client.py [host:port]` | Connects to the server, sends a few probe APDUs (no reader needed) |
| `python examples/find_aid.py [host:port]` | SELECTs candidate AIDs against the real card to identify it |
| `python examples/terminal.py [index]` | Drives an RC-S380 as an ISO-DEP reader to tap the emulated card and exercise the full relay |
| `python examples/probe_typeb.py [index] [nocid\|senseonly]` | RC-S380 Type-B bring-up probe: sense SENSB_RES, then optionally ATTRIB + one I-block |

## How the relay works

1. The client asks the server for the card (`get_card`). The server activates
   the real card into ISO-DEP — Type A via `sense` + `RATS`, or Type B via
   `SENSB` + `ATTRIB` (or PC/SC `connect`) — and reports its ATS/ATQB/ATR.
2. The client presents a synthetic Type 4 (NFC-A) card with nfcpy's `listen`;
   nfcpy answers `RATS`. When a phone taps, the first command block is handed
   back and the client reassembles each **command APDU**.
3. The command APDU is relayed over TCP. The server runs its PCD-side ISO-DEP
   state machine (`isodep.Pcd`) against the real card — block-number toggling,
   command/response chaining and S(WTX) — and returns the response APDU.
4. The client wraps the response back into ISO-DEP I-block(s) for the phone. The
   two ISO-DEP sessions are independent; only the APDUs are shared.

## Notes & limitations

- Only ISO14443-4 cards can be relayed; a plain Type 2 tag has no APDU layer.
- The emulated card's UID/ATS is synthetic, not the real card's.
- Type B (as well as Type A) is relayed on the RC-S380 directly; a PC/SC reader is optional.
- One physical reader per side means the server serves one client at a time.

## Tests

```sh
pytest
```

The suite covers the protocol codec, the ISO-DEP state machine and the server
request handling (with a fake card) — no hardware required.

## License

Apache-2.0 — see `LICENSE`.
