"""Command-line interface: ``server`` / ``client`` / ``list`` subcommands,
mirroring the Rust ``s380-relay`` binary.

Logging is controlled by ``-v``/``-vv`` (or the ``S380_LOG`` env var, e.g.
``S380_LOG=debug``).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional

from . import cardside, client as client_mod, server as server_mod
from .protocol import TECH_A, TECH_B

DEFAULT_ADDR = "127.0.0.1:7878"


def _configure_logging(verbosity: int) -> None:
    env = os.environ.get("S380_LOG", "").lower()
    level = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}.get(env)
    if level is None:
        level = logging.WARNING if verbosity <= 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-v", "--verbose", action="count", default=0, help="info (-v) or debug (-vv) logging")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s380-relay",
        description="Relay ISO14443 (NFC-A/B) traffic between two readers at the APDU layer.",
    )
    _add_common(parser)
    sub = parser.add_subparsers(dest="command")

    srv = sub.add_parser("server", help="card side: hold the real card, relay to it")
    _add_common(srv)
    srv.add_argument("-l", "--listen", default=DEFAULT_ADDR, help="listen address (default: %(default)s)")
    srv.add_argument(
        "--tech",
        choices=["auto", "a", "b"],
        default="auto",
        help="real card's ISO14443 technology (default: %(default)s, detect A or B)",
    )
    srv.add_argument(
        "--reader",
        choices=[cardside.READER_PORT100, cardside.READER_PCSC],
        default=cardside.READER_PORT100,
        help="card-side reader backend (pcsc = e.g. RC-S300, needed for Type B)",
    )
    srv.add_argument("--pcsc-name", default=None, help="substring to pick the PC/SC reader")
    srv.add_argument("-d", "--device-index", type=int, default=0, help="which RC-S380 (port100; see 'list')")
    srv.add_argument("-t", "--timeout", type=int, default=1000, help="per-command timeout ms (default: %(default)s)")

    cli = sub.add_parser("client", help="phone side: emulate the card, relay taps")
    _add_common(cli)
    cli.add_argument("-c", "--connect", default=DEFAULT_ADDR, help="server address (default: %(default)s)")
    cli.add_argument("-d", "--device-index", type=int, default=1, help="which RC-S380 (default: 1; see 'list')")
    cli.add_argument("--no-wtx", action="store_true", help="do not send S(WTX) before relaying")
    cli.add_argument("--wtxm", type=int, default=10, help="waiting-time extension multiplier 1-59 (default: %(default)s)")
    cli.add_argument("-t", "--timeout", type=int, default=1000, help="per-command timeout ms (default: %(default)s)")
    cli.add_argument("-w", "--window", type=float, default=1.0, help="listen window seconds (default: %(default)s)")

    sub.add_parser("list", help="list attached RC-S380 readers and indices")
    return parser


def run_list() -> int:
    from .reader import list_port100

    readers = list_port100()
    if not readers:
        print("no RC-S380 (Port-100) readers found")
        return 0
    print("attached RC-S380 readers:")
    for r in readers:
        print("  index %d  bus %03d addr %03d  pid 0x%04X" % (r.index, r.bus, r.address, r.product_id))
    return 0


def run_server(args: argparse.Namespace) -> int:
    tech = {"a": TECH_A, "b": TECH_B}.get(args.tech, cardside.TECH_AUTO)
    try:
        card = cardside.open_card_side(args.reader, tech, args.device_index, args.pcsc_name)
    except (cardside.CardError, IOError, ValueError) as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1
    config = server_mod.ServerConfig(listen_addr=args.listen, timeout_ms=args.timeout)
    try:
        server_mod.run(card, config)
    except KeyboardInterrupt:
        pass
    finally:
        card.close()
    return 0


def run_client(args: argparse.Namespace) -> int:
    from .reader import open_port100

    try:
        clf = open_port100(args.device_index)
    except IOError as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1
    config = client_mod.ClientConfig(
        server_addr=args.connect,
        command_timeout_ms=args.timeout,
        listen_window_s=args.window,
        device_index=args.device_index,
        use_wtx=not args.no_wtx,
        wtxm=min(max(args.wtxm, 1), 59),
    )
    link = client_mod.ServerLink(config.server_addr)
    try:
        client_mod.run(clf, link, config)
    except client_mod.RelayLinkError as e:
        print("Error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        link.close()
        clf.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", 0))
    if args.command == "server":
        return run_server(args)
    if args.command == "client":
        return run_client(args)
    if args.command == "list":
        return run_list()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
