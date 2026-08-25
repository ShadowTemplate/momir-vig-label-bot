"""Subprocess entry point for printing one card label.

Exists because the bot's virtualenv (Python 3.9) is built WITHOUT Bluetooth
support - it has no ``socket.AF_BLUETOOTH`` - while the system Python does but
cannot import the pinned python-telegram-bot. So the bot shells out to a
Bluetooth-capable interpreter for the print step, exactly as it already shells
out to pango-view and convert.

    python3 -m momir_vig_label_bot.print_cli card.json [--density 8] [--target-mm 70]

Card JSON: {"name", "mana_cost", "type_line", "pt", "text"}.
Exits 0 on success (label length in mm on stdout), 1 with a message on stderr.
"""
import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("card_json")
    ap.add_argument("--mac", default=None)
    ap.add_argument("--density", type=int, default=None)
    ap.add_argument("--target-mm", type=int, default=None)
    a = ap.parse_args()

    import socket
    if not hasattr(socket, "AF_BLUETOOTH"):
        print(f"{sys.executable} has no socket.AF_BLUETOOTH (built without "
              f"Bluetooth support); cannot reach the printer.", file=sys.stderr)
        return 1

    from momir_vig_label_bot import e10
    from momir_vig_label_bot.constants import E10_DENSITY, E10_TARGET_MM
    from momir_vig_label_bot.credentials import E10_MAC

    with open(a.card_json) as fh:
        card = json.load(fh)

    try:
        mm = e10.print_card(
            card,
            mac=a.mac or E10_MAC,
            density=a.density if a.density is not None else E10_DENSITY,
            target_mm=a.target_mm if a.target_mm is not None else E10_TARGET_MM,
        )
    except e10.E10Error as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"{mm:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
