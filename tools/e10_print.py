#!/usr/bin/env python3
"""Diagnostics + raw print CLI for the SUPVAN / Katasymbol E10.

Thin CLI over momir_vig_label_bot.e10 (the single source of truth).

    tools/e10_print.py --selftest              # validate against reference vectors
    tools/e10_print.py --dry-run --text "HI"   # build everything, send nothing
    tools/e10_print.py --text "HELLO" --bar 4  # print text + solid ink bar
    tools/e10_print.py --solid --mm 15         # solid block ink test

Without --dry-run this FEEDS AND PRINTS, consuming tape.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from momir_vig_label_bot import e10
from momir_vig_label_bot.e10 import (
    DOTS_PER_MM, HEAD_MM, MARGIN_DOTS, build_data_frames, build_page_reg_bits,
    build_print_buffer, calc_speed, compress_lzma, connect, make_cmd,
    print_rows, raster_to_column_major, solid_pattern, split_into_buffers,
    CMD_BUF_FULL, CMD_CHECK_DEVICE, CMD_INQUIRY_STA, CMD_NEXT_ZIPPEDBULK)


def render_text(lines, head_dots, pad_dots=16, font_path=None, flip=False):
    """Plain text rows, baseline running along the feed axis."""
    from PIL import Image, ImageDraw
    n, gap = len(lines), 2
    size = max(6, (head_dots - gap * (n - 1)) // n)
    while size > 5:
        font = e10._font(font_path or e10.FONT_BOLD, size)
        metrics = [font.getbbox(t or " ") for t in lines]
        heights = [m[3] - m[1] for m in metrics]
        widths = [m[2] - m[0] for m in metrics]
        if sum(heights) + gap * (n - 1) <= head_dots:
            break
        size -= 1
    feed = max(widths) + pad_dots * 2
    img = Image.new("1", (feed, head_dots), 0)
    d = ImageDraw.Draw(img)
    total = sum(heights) + gap * (n - 1)
    y = (head_dots - total) // 2
    for t, m, h in zip(lines, metrics, heights):
        d.text((pad_dots - m[0], y - m[1]), t, font=font, fill=1)
        y += h + gap
    if flip:
        img = img.transpose(Image.ROTATE_180)
    return e10.image_to_rows(img, head_dots), feed, size


def test_pattern(width_dots, height_dots):
    """Border + crossing diagonals + centre bar: geometry errors show up loud."""
    bpl = -(-width_dots // 8)
    rows = []
    for y in range(height_dots):
        r = bytearray(bpl)

        def setpx(x):
            if 0 <= x < width_dots:
                r[x >> 3] |= 1 << (7 - (x & 7))
        if y < 2 or y >= height_dots - 2:
            for x in range(width_dots):
                setpx(x)
        else:
            setpx(0); setpx(1); setpx(width_dots - 2); setpx(width_dots - 1)
            d = (y * width_dots) // height_dots
            for k in (-1, 0, 1):
                setpx(d + k); setpx(width_dots - 1 - d + k)
            if height_dots // 2 - 6 <= y <= height_dots // 2 + 6:
                for x in range(width_dots // 4, 3 * width_dots // 4):
                    setpx(x)
        rows.append(bytes(r))
    return rows


def selftest():
    ok = True

    def chk(name, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {name}: {got!r}"
              + ("" if good else f"  expected {want!r}"))

    print("Reference vectors from supvan-cups unit tests:\n")
    chk("make_cmd(CHECK_DEVICE) checksum", make_cmd(CMD_CHECK_DEVICE)[8:10], b"\x01\x00")
    chk("make_cmd param LE", make_cmd(CMD_INQUIRY_STA, 0x1234)[12:14], b"\x34\x12")
    chk("page_reg_bits defaults", build_page_reg_bits(False, False, False, 4), (0x00, 0x50))
    chk("page_reg_bits first/last", build_page_reg_bits(True, True, True, 4), (0x0E, 0x50))

    b = build_print_buffer(bytes(84 * 48), 48, 84, True, True, True, 8, 8, 4, 4)
    chk("buffer bytes/line", b[6], 48)
    chk("buffer cols LE", b[4:6], (84).to_bytes(2, "little"))
    chk("buffer margin_top", b[8], 8)
    b2 = build_print_buffer(bytes(84 * 48), 48, 84, True, True, True, 8, 8, 3, 12)
    chk("black in nodu field", (b2[3] >> 2) & 0x0F, 3)
    chk("red in buf[12]", b2[12], 12)

    bufs = split_into_buffers(bytes(240 * 48), 48, 240, 8, 8, 4, 4)
    chk("split 240 cols -> buffers", len(bufs), 3)
    chk("  first has PageSt", bufs[0][2] & 0x02, 0x02)
    chk("  last has PageEnd|PrtEnd", bufs[2][2] & 0x0C, 0x0C)

    c = compress_lzma(bytes(4096))
    chk("lzma props byte (lc3 lp0 pb2)", c[0], 0x5D)
    chk("lzma dict_size", c[1:5], (8192).to_bytes(4, "little"))
    chk("lzma uncompressed size", c[5:13], (4096).to_bytes(8, "little"))

    fr = build_data_frames(bytes(1200))
    chk("1200B -> frames", len(fr), 3)
    chk("frame size", len(fr[0]), 512)
    chk("frame header", fr[0][0:6], bytes([0x7E, 0x5A, 0xFC, 0x01, 0x10, 0x02]))
    chk("packet magic", fr[0][6:8], b"\xaa\xbb")
    chk("packet idx/total", (fr[0][10], fr[0][11]), (0, 3))
    chk("speed(4000)/(600)/(100)",
        (calc_speed(4000), calc_speed(600), calc_speed(100)), (10, 55, 60))

    print(f"\n{'ALL PASS' if ok else 'FAILURES PRESENT'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mac", default=None)
    ap.add_argument("--mm", type=int, default=25, help="length (pattern/solid)")
    ap.add_argument("--head-mm", type=int, default=HEAD_MM,
                    help="PRINTHEAD width mm (E10 = 12). NOT the media width.")
    ap.add_argument("--density", type=int, default=8)
    ap.add_argument("--text", action="append", default=None)
    ap.add_argument("--solid", action="store_true")
    ap.add_argument("--bar", type=int, default=0, help="prefix N mm solid bar")
    ap.add_argument("--flip", action="store_true")
    ap.add_argument("--font", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--info", action="store_true", help="query status + media only")
    ap.add_argument("--noise", type=int, default=0, metavar="MM",
                    help="N mm of random dots: 1 buffer but MANY data frames")
    ap.add_argument("--twobuf", action="store_true",
                    help="2-buffer diagnostic: a solid bar inside EACH buffer")
    ap.add_argument("--overlap", type=int, default=e10.BOUNDARY_OVERLAP,
                    help="columns re-sent at each buffer boundary")
    ap.add_argument("--stream", choices=("perbuf", "single"), default="perbuf",
                    help="transfer mode: one BUF_FULL per buffer, or one stream")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    from momir_vig_label_bot.credentials import E10_MAC
    mac = e10.resolve_mac(a.mac or E10_MAC or None)
    head_dots = a.head_mm * DOTS_PER_MM
    bpl = -(-head_dots // 8)

    if a.info:
        dev = connect(mac)
        try:
            print(f"status  : {sorted(dev.status() or [])}")
            m = dev.material()
            if m:
                for k, v in m.items():
                    print(f"{k:12s}: {v}")
        finally:
            dev.close()
        return 0

    if a.text:
        rows, height_dots, fsize = render_text(a.text, head_dots,
                                               font_path=a.font, flip=a.flip)
        if a.bar:
            rows = (solid_pattern(head_dots, a.bar * DOTS_PER_MM)
                    + [bytes(bpl)] * (2 * DOTS_PER_MM) + rows)
            height_dots = len(rows)
        print(f"[*] text {a.text} @ {fsize}px -> {height_dots / DOTS_PER_MM:.1f} mm")
    elif a.noise:
        import random
        random.seed(1)
        height_dots = a.noise * DOTS_PER_MM
        # Random dots barely compress, so a short label still needs several
        # 500-byte data frames while staying inside ONE 4096-byte print buffer.
        rows = [bytes(random.getrandbits(8) for _ in range(bpl))
                for _ in range(height_dots)]
        print(f"[*] NOISE {a.noise} mm (1 buffer, many frames)")
    elif a.twobuf:
        # max_cols = 4074//12 = 339 payload columns per buffer, so 439 payload
        # columns straddles exactly two. Put an unmistakable solid bar inside
        # each buffer's own span: bar 1 proves buffer 1 arrived, bar 2 proves
        # buffer 2 did.
        payload = 439
        height_dots = payload + 2 * MARGIN_DOTS
        b1_end = MARGIN_DOTS + 339
        rows = []
        for y in range(height_dots):
            in_bar1 = MARGIN_DOTS <= y < MARGIN_DOTS + 60
            in_bar2 = b1_end + 30 <= y < b1_end + 90
            rows.append(bytes([0xFF] * bpl) if (in_bar1 or in_bar2) else bytes(bpl))
        print(f"[*] TWO-BUFFER diagnostic {height_dots / DOTS_PER_MM:.0f} mm: "
              f"bar1 in buffer 1 (mm {MARGIN_DOTS/8:.0f}-{(MARGIN_DOTS+60)/8:.0f}), "
              f"bar2 in buffer 2 (mm {(b1_end+30)/8:.0f}-{(b1_end+90)/8:.0f})")
    elif a.solid:
        height_dots = a.mm * DOTS_PER_MM
        rows = solid_pattern(head_dots, height_dots)
        print(f"[*] SOLID block {a.mm} mm")
    else:
        height_dots = a.mm * DOTS_PER_MM
        rows = test_pattern(head_dots, height_dots)
        print(f"[*] test pattern {a.mm} mm")

    print(f"[*] canvas {head_dots}x{height_dots} dots, {bpl} bytes/line, "
          f"density={a.density}")

    if a.dry_run:
        img, cols, bpl2 = raster_to_column_major(rows, head_dots, len(rows))
        bufs = split_into_buffers(img, bpl2, cols, MARGIN_DOTS, MARGIN_DOTS,
                                  a.density, a.density)
        comp = compress_lzma(b"".join(bufs))
        frames = build_data_frames(comp)
        speed = calc_speed(len(comp) // len(bufs))
        print(f"[*] {len(bufs)} buffer(s) -> {len(comp)}B lzma, "
              f"{len(frames)} frame(s), speed={speed}")
        print("\n--- DRY RUN, nothing sent ---")
        print(f"  NEXT_ZIPPEDBULK: {make_cmd(CMD_NEXT_ZIPPEDBULK, 512, len(frames)).hex(' ')}")
        print(f"  BUF_FULL       : {make_cmd(CMD_BUF_FULL, len(comp), speed).hex(' ')}")
        return 0

    class _L:
        @staticmethod
        def info(fmt, *args):
            print("[*] " + (fmt % args if args else fmt))

    try:
        print_rows(rows, mac, head_dots=head_dots, density=a.density,
                   log=_L, mode=a.stream, overlap=a.overlap)
    except e10.E10Error as exc:
        print(f"[!] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
