#!/usr/bin/env python3
"""Render an MTG card as an E10 label: preview to PNG, or print.

The FULL oracle text is always kept - a wordier card yields a longer label,
never truncated text.

    tools/e10_card.py --card card.json --preview /tmp/label.png
    tools/e10_card.py --card card.json --print
    tools/e10_card.py --name "Grizzly Bears" --cost "{1}{G}" \
                      --type "Creature - Bear" --pt 2/2 --print
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from momir_vig_label_bot import e10


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", help="JSON with name/mana_cost/type_line/pt/text")
    ap.add_argument("--name"); ap.add_argument("--cost", default="")
    ap.add_argument("--type", default=""); ap.add_argument("--pt", default="")
    ap.add_argument("--oracle", default="")
    ap.add_argument("--head-mm", type=int, default=e10.HEAD_MM)
    ap.add_argument("--target-mm", type=int, default=70,
                    help="minimum label length; grows to fit the text")
    ap.add_argument("--density", type=int, default=8)
    ap.add_argument("--preview"); ap.add_argument("--print", action="store_true")
    ap.add_argument("--mac", default=None)
    a = ap.parse_args()

    if a.card:
        card = json.load(open(a.card))
    else:
        card = {"name": a.name or "Unnamed", "mana_cost": a.cost,
                "type_line": a.type, "pt": a.pt, "text": a.oracle}

    head_dots = a.head_mm * e10.DOTS_PER_MM
    img, size, n_or = e10.render_card(card, head_dots=head_dots,
                                      target_mm=a.target_mm)
    print(f"[*] {card['name']!r}: {img.size[0]}x{img.size[1]} dots = "
          f"{img.size[0] / e10.DOTS_PER_MM:.0f} x {a.head_mm} mm, "
          f"title {size}px, {n_or} oracle line(s)")

    if a.preview:
        from PIL import Image
        img.convert("L").point(lambda v: 255 - v * 255).resize(
            (img.size[0] * 3, img.size[1] * 3), Image.NEAREST).save(a.preview)
        print(f"[*] preview -> {a.preview} (3x, black on white)")

    if a.print:
        from momir_vig_label_bot.credentials import E10_MAC

        class _L:
            @staticmethod
            def info(fmt, *args):
                print("[*] " + (fmt % args if args else fmt))
        try:
            e10.print_rows(e10.image_to_rows(img, head_dots), a.mac or E10_MAC,
                           head_dots=head_dots, density=a.density, log=_L)
        except e10.E10Error as exc:
            print(f"[!] {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
