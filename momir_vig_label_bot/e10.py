"""SUPVAN / Katasymbol E10 label printer driver (Bluetooth Classic RFCOMM).

Pure stdlib + Pillow. Protocol reverse-engineered reference:
https://github.com/heeen/supvan-cups/blob/HEAD/docs/PROTOCOL.md

THE ONE RULE THAT MATTERS: the raster canvas is always the PRINTHEAD width
(12 mm = 96 dots on the E10), never the media width that RETURN_MAT reports.
The head is a fixed bar and the tape runs centred under it. Send a line wider
than the head and the firmware feeds the tape, burns nothing, and reports
success at every step - a silent blank label.
"""
import lzma
import os
import socket
import time

DOTS_PER_MM = 8
HEAD_MM = 12                      # E10 printhead width
HEAD_DOTS = HEAD_MM * DOTS_PER_MM  # 96
MARGIN_DOTS = 8
PRINT_BUF_SIZE = 4096
PRINT_BUF_HEADER = 14
MAX_BUF_DATA = 4074
DATA_PAYLOAD_SIZE = 500
BT_RESP_HEADER_LEN = 22

CMD_BUF_FULL, CMD_INQUIRY_STA, CMD_CHECK_DEVICE = 0x10, 0x11, 0x12
CMD_START_PRINT, CMD_STOP_PRINT = 0x13, 0x14
CMD_RETURN_MAT, CMD_NEXT_ZIPPEDBULK = 0x30, 0x5C

MSTA_LO = {0x01: "buf_full", 0x02: "label_rw_error", 0x04: "label_end",
           0x08: "label_mode_error", 0x10: "ribbon_rw_error",
           0x20: "ribbon_end", 0x40: "low_battery"}
MSTA_HI = {0x04: "device_busy", 0x08: "head_temp_high"}
FSTA_LO = {0x08: "cover_open", 0x10: "insert_usb", 0x40: "printing"}
FSTA_HI = {0x01: "label_not_installed"}
ERROR_FLAGS = {"label_rw_error", "label_end", "label_mode_error",
               "ribbon_rw_error", "ribbon_end", "cover_open",
               "head_temp_high", "label_not_installed"}

# MSTA low bit 0x20 is named "ribbon_end" after the ribbon-based T-series. The
# E10 is direct thermal and has no ribbon, but the bit is NOT cosmetic here:
# while it is set the printer accepts every command, reports a clean
# completion, and prints nothing at all - verified by the RFID tape counter not
# moving a single millimetre across a whole job. It latches and survives
# STOP_PRINT and CHECK_DEVICE; only a power cycle clears it. So it stays fatal.
FATAL_FLAGS = set(ERROR_FLAGS)

FLAG_HINTS = {
    "ribbon_end":
        "power-cycle the printer (hold the power button until it switches off, "
        "then on) and re-seat the label roll",
    "ribbon_rw_error": "power-cycle the printer and re-seat the label roll",
    "label_not_installed": "no roll detected - open the lid and re-seat it",
    "label_end": "the roll is finished",
    "label_rw_error": "the roll's RFID sticker could not be read - is it intact?",
    "label_mode_error": "this roll type is not compatible",
    "cover_open": "close the lid",
    "head_temp_high": "the printhead is too hot - let it cool for a minute",
}


def _flag_message(flags):
    parts = []
    for f in sorted(flags):
        hint = FLAG_HINTS.get(f)
        parts.append(f"{f} ({hint})" if hint else f)
    return "Printer reports: " + ", ".join(parts)

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"
FONT_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


class E10Error(Exception):
    """Printing failed; message is safe to show a user."""


# --------------------------------------------------------------- framing
def make_cmd(cmd, block_size=0, block_count=0):
    f = bytearray(16)
    f[0], f[1], f[2] = 0x7E, 0x5A, 0x0C
    f[4], f[5], f[6], f[7] = 0x10, 0x01, 0xAA, cmd
    f[11] = 0x01
    f[12:14] = block_size.to_bytes(2, "little")
    f[14:16] = block_count.to_bytes(2, "little")
    f[8:10] = (sum(f[10:16]) & 0xFFFF).to_bytes(2, "little")
    return bytes(f)


def make_data_packet(chunk, idx, total):
    p = bytearray(506)
    p[0], p[1] = 0xAA, 0xBB
    p[4], p[5] = idx, total
    p[6:6 + len(chunk)] = chunk
    p[2:4] = (sum(p[4:506]) & 0xFFFF).to_bytes(2, "little")
    return bytes(p)


def wrap_data_frame(pkt506):
    f = bytearray(512)
    f[0], f[1] = 0x7E, 0x5A
    f[2:4] = (508).to_bytes(2, "little")
    f[4], f[5] = 0x10, 0x02
    f[6:512] = pkt506
    return bytes(f)


def build_data_frames(compressed):
    n = -(-len(compressed) // DATA_PAYLOAD_SIZE)
    return [wrap_data_frame(make_data_packet(
        compressed[i * DATA_PAYLOAD_SIZE:(i + 1) * DATA_PAYLOAD_SIZE], i, n))
        for i in range(n)]


# ---------------------------------------------------------------- raster
def raster_to_column_major(rows, width, height):
    bpl = -(-width // 8)
    out = bytearray(height * bpl)
    for y in range(height):
        row = rows[y]
        for x in range(width):
            if (row[x >> 3] >> (7 - (x & 7))) & 1:
                out[y * bpl + (x >> 3)] |= 1 << (x & 7)
    return bytes(out), height, bpl


def solid_pattern(width_dots, height_dots):
    return [bytes([0xFF] * (-(-width_dots // 8)))] * height_dots


# --------------------------------------------------------------- buffers
def build_page_reg_bits(page_st, page_end, prt_end, nodu, mat=1, first_cut=0):
    b0 = (0x02 if page_st else 0) | (0x04 if page_end else 0) | (0x08 if prt_end else 0)
    b1 = (first_cut & 0x03) | ((nodu & 0x0F) << 2) | ((mat & 0x03) << 6)
    return b0 & 0xFF, b1


def build_print_buffer(image_data, per_line_byte, cols_in_buf, page_st,
                       page_end, prt_end, margin_top, margin_bottom, black, red):
    buf = bytearray(PRINT_BUF_SIZE)
    buf[2], buf[3] = build_page_reg_bits(page_st, page_end, prt_end, black)
    buf[4:6] = cols_in_buf.to_bytes(2, "little")
    buf[6] = per_line_byte
    buf[8:10] = max(1, min(900, margin_top)).to_bytes(2, "little")
    buf[10:12] = max(1, min(900, margin_bottom)).to_bytes(2, "little")
    buf[12] = min(red, 15)
    n = min(len(image_data), PRINT_BUF_SIZE - PRINT_BUF_HEADER)
    buf[PRINT_BUF_HEADER:PRINT_BUF_HEADER + n] = image_data[:n]
    # sum(buf[2:14]) plus the byte at each 256-byte boundary inside the payload
    data_end = cols_in_buf * per_line_byte + PRINT_BUF_HEADER
    chk = sum(buf[2:14])
    for i in range(1, data_end // 256 + 1):
        idx = i * 256 - 1
        if idx < len(buf):
            chk += buf[idx]
    buf[0:2] = (chk & 0xFFFF).to_bytes(2, "little")
    return bytes(buf)


#: Columns re-sent at the start of every buffer after the first.
#:
#: A 1-dot white line appears at each buffer boundary. Re-sending the boundary
#: column was tried and made NO difference, so the column is not being dropped
#: from the data - the printer is starving mid-label and the tape advances a
#: step unburnt. Kept as a tunable, default off; the fix is to not starve it.
BOUNDARY_OVERLAP = 0


def _is_blank_column(image_data, per_line_byte, col):
    a = col * per_line_byte
    return not any(image_data[a:a + per_line_byte])


def choose_splits(image_data, per_line_byte, payload_cols, max_cols, margin_top):
    """Column count for each buffer, preferring seams that land in whitespace.

    The printer loses one printed column at every buffer boundary, and nothing
    host-side prevents it: re-sending the column changes nothing, streaming
    back to back changes nothing, and the speed cannot be held constant because
    each buffer must declare its own. So place the seam instead of fighting it
    - split on a column with no ink, where a missing dot column is invisible.
    Falls back to the largest legal buffer when no blank column is available.
    """
    splits, start, remaining = [], 0, payload_cols
    while remaining > 0:
        if remaining <= max_cols:
            splits.append(remaining)
            break
        n_left = -(-remaining // max_cols)
        lo = max(1, remaining - max_cols * (n_left - 1))
        hi = max_cols
        runs = [k for k in range(hi, lo - 1, -1)
                if _is_blank_column(image_data, per_line_byte,
                                    margin_top + start + k)]
        if runs:
            # Prefer the middle of a blank run so the lost column has ink-free
            # neighbours on both sides.
            best, best_score = runs[0], -1
            for k in runs:
                c = margin_top + start + k
                score = 0
                for d in (1, 2):
                    if _is_blank_column(image_data, per_line_byte, c - d):
                        score += 1
                    if _is_blank_column(image_data, per_line_byte, c + d):
                        score += 1
                if score > best_score or (score == best_score and k > best):
                    best, best_score = k, score
            k = best
        else:
            k = hi
        splits.append(k)
        start += k
        remaining -= k
    return splits


def split_into_buffers(image_data, per_line_byte, total_cols,
                       margin_top, margin_bottom, black, red,
                       overlap=BOUNDARY_OVERLAP, align_seams=True):
    cols = total_cols - margin_top - margin_bottom
    max_cols = MAX_BUF_DATA // per_line_byte
    step = max_cols - overlap
    if align_seams:
        sizes = choose_splits(image_data, per_line_byte, cols, step, margin_top)
    else:
        sizes, rem = [], cols
        while rem > 0:
            sizes.append(min(rem, step))
            rem -= sizes[-1]

    chunks, cur = [], 0
    for n in sizes:
        chunks.append((cur, n))
        cur += n
    last = len(chunks) - 1
    out = []
    for i, (st, n) in enumerate(chunks):
        if i and overlap:
            st -= overlap
            n += overlap
        assert n <= max_cols, f"buffer {i}: {n} cols exceeds {max_cols}"
        a = (margin_top + st) * per_line_byte
        out.append(build_print_buffer(
            image_data[a:a + n * per_line_byte], per_line_byte, n,
            i == 0, i == last, i == last,
            margin_top, margin_bottom, black, red))
    return out


def compress_lzma(data):
    """LZMA1-alone, dict 8192, lc3/lp0/pb2, header patched with the real size.

    Python writes -1 for the uncompressed size; the firmware reads that field.
    """
    c = bytearray(lzma.compress(
        data, format=lzma.FORMAT_ALONE,
        filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 8192, "lc": 3, "lp": 0,
                  "pb": 2, "nice_len": 128, "mode": lzma.MODE_NORMAL}]))
    c[5:13] = len(data).to_bytes(8, "little")
    return bytes(c)


def calc_speed(n):
    for thr, sp in ((3000, 10), (2800, 15), (2500, 20), (2000, 25),
                    (1500, 40), (1000, 45), (500, 55)):
        if n > thr:
            return sp
    return 60


# ---------------------------------------------------------------- device
class E10:
    def __init__(self, mac, timeout=4.0):
        self.timeout = timeout
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                               socket.BTPROTO_RFCOMM)
        self.s.settimeout(timeout)
        self.s.connect((mac, 1))

    def drain(self):
        """Discard unread replies.

        Two replies are deliberately never read during a bulk transfer: the ack
        for the final data frame, and the BUF_FULL reply. Reading them would
        stall the firmware into a 3-beep abort. But leaving them in the RX
        buffer desyncs every later read by one message - the next command's
        reply is then the PREVIOUS command's, which is how a multi-buffer job
        silently prints nothing. Drain between phases instead.
        """
        self.s.setblocking(False)
        try:
            while self.s.recv(4096):
                pass
        except (BlockingIOError, InterruptedError, OSError):
            pass
        finally:
            self.s.setblocking(True)
            self.s.settimeout(self.timeout)

    def cmd(self, c, p1=0, p2=0, want=True, tries=4):
        """Send a command; return its reply, matched by the echoed opcode."""
        self.drain()
        self.s.send(make_cmd(c, p1, p2))
        if not want:
            return None
        for _ in range(tries):
            try:
                r = self.s.recv(512)
            except socket.timeout:
                return None
            # byte 7 echoes the command; anything else is a stale reply.
            if len(r) >= 8 and r[0] == 0x7E and r[1] == 0x5A and r[7] == c:
                return r
        return None

    def status(self):
        r = self.cmd(CMD_INQUIRY_STA)
        if not r or len(r) < 20:
            return None
        f = set()
        for byte, tbl in ((r[14], MSTA_LO), (r[15], MSTA_HI),
                          (r[16], FSTA_LO), (r[17], FSTA_HI)):
            f |= {n for m, n in tbl.items() if byte & m}
        return f

    def material(self):
        r = self.cmd(CMD_RETURN_MAT)
        if not r or len(r) < BT_RESP_HEADER_LEN + 21:
            return None
        p = r[BT_RESP_HEADER_LEN:]
        return {"uuid": p[0:7].hex().upper(), "code": p[7:15].hex().upper(),
                "sn": int.from_bytes(p[15:17], "little"), "label_type": p[17],
                "width_mm": p[18], "height_mm": p[19], "gap_mm": p[20],
                # continuous stock counts this in MILLIMETRES of tape
                "remaining_mm": (int.from_bytes(p[21:25], "little")
                                 if len(p) >= 25 else None)}

    def send_frames(self, frames, log=None):
        for i, fr in enumerate(frames):
            self.s.send(fr)
            if i < len(frames) - 1:   # last frame's ack arrives as the BUF_FULL reply
                try:
                    ack = self.s.recv(512)
                    # A healthy ack echoes 0xBB at byte 7. Anything else means
                    # we are reading a stale reply and the stream has desynced.
                    if log and not (len(ack) >= 8 and ack[7] == 0xBB):
                        log.info("  frame %d/%d unexpected ack: %s",
                                 i + 1, len(frames),
                                 ack[:12].hex(" ") if ack else "<empty>")
                except socket.timeout:
                    if log:
                        log.info("  frame %d/%d ack: <TIMEOUT>", i + 1, len(frames))
            time.sleep(0.01)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def connect(mac, attempts=3, timeout=4.0):
    """Connect, retrying: the printer auto-sleeps after ~1-2 min idle."""
    last = None
    for i in range(attempts):
        try:
            return E10(mac, timeout)
        except (OSError, socket.timeout) as exc:
            last = exc
            if i < attempts - 1:
                time.sleep(1.5)
    raise E10Error(
        "Printer not reachable - it sleeps after a couple of minutes idle. "
        f"Press its power button and try again. ({type(last).__name__})")


# ----------------------------------------------------------------- fonts
def _font(path, size):
    from PIL import ImageFont
    for p in (path, FONT_FALLBACK):
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    raise E10Error("no usable TTF font found")


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _min_width_for_lines(draw, text, font, n_lines, lo, hi):
    """Smallest wrap width that fits `text` into at most n_lines. Never truncates."""
    if not text:
        return lo
    if len(_wrap(draw, text, font, hi)) > n_lines:
        return None                      # impossible even at max width
    best = hi
    while lo <= hi:
        mid = (lo + hi) // 2
        if mid > 0 and len(_wrap(draw, text, font, mid)) <= n_lines:
            best, hi = mid, mid - 1
        else:
            lo = mid + 1
    return best


def render_card(card, head_dots=HEAD_DOTS, target_mm=70, pad=12,
                max_len_mm=400):
    """Lay a card out across the head, text running along the feed axis.

    The FULL oracle text is always kept: a wordier card produces a longer
    label rather than truncated text.

    Returns (PIL '1' image, title_px, n_oracle_lines).
    """
    from PIL import Image, ImageDraw

    probe = Image.new("1", (10, 10))
    d = ImageDraw.Draw(probe)

    title = f"{card.get('name', '')} {card.get('mana_cost', '')}".strip()
    sub_bits = [b for b in (card.get("type_line", ""),
                            (card.get("pt") or "").strip("/")) if b]
    sub = "  |  ".join(sub_bits)
    oracle = " ".join((card.get("text") or "").split())

    target_w = max(80, target_mm * DOTS_PER_MM - 2 * pad)
    max_w = max_len_mm * DOTS_PER_MM - 2 * pad
    gap = 3

    for big in range(30, 10, -1):
        med, sml = max(9, int(big * 0.62)), max(8, int(big * 0.56))
        f_b, f_m, f_s = _font(FONT_BOLD, big), _font(FONT_REG, med), _font(FONT_REG, sml)
        hb, hm, hs = (f.getbbox("Ayg") for f in (f_b, f_m, f_s))
        h_title, h_sub, h_or = hb[3] - hb[1], hm[3] - hm[1], hs[3] - hs[1]

        used = h_title + gap + (h_sub + gap if sub else 0)
        avail = head_dots - 4 - used
        n_avail = avail // (h_or + 1) if h_or else 0
        if oracle and n_avail < 1:
            continue

        w_head = max(d.textlength(title, font=f_b), d.textlength(sub, font=f_m))
        w_or = _min_width_for_lines(d, oracle, f_s, n_avail,
                                    int(target_w * 0.5), int(max_w))
        if oracle and w_or is None:
            continue                       # need a smaller font to fit it all

        width_dots = int(max(target_w, w_head, w_or or 0))
        lines = _wrap(d, oracle, f_s, width_dots) if oracle else []
        if len(lines) > n_avail:
            continue
        total = used + len(lines) * (h_or + 1)
        if total > head_dots - 2:
            continue

        img = Image.new("1", (width_dots + 2 * pad, head_dots), 0)
        dr = ImageDraw.Draw(img)
        y = (head_dots - total) // 2
        dr.text((pad, y - hb[1]), title, font=f_b, fill=1)
        y += h_title + gap
        if sub:
            dr.text((pad, y - hm[1]), sub, font=f_m, fill=1)
            y += h_sub + gap
        for t in lines:
            dr.text((pad, y - hs[1]), t, font=f_s, fill=1)
            y += h_or + 1
        return img, big, len(lines)

    raise E10Error("Could not fit the card text onto a 12 mm label.")


def image_to_rows(img, head_dots=HEAD_DOTS):
    px = img.load()
    bpl = -(-head_dots // 8)
    rows = []
    for f in range(img.size[0]):
        r = bytearray(bpl)
        for x in range(head_dots):
            if px[f, x]:
                r[x >> 3] |= 1 << (7 - (x & 7))
        rows.append(bytes(r))
    return rows


# ------------------------------------------------------------------ print
def _read_tape_mm(dev, attempts=4, settle=1.0):
    """Remaining tape in mm, or None if it can't be read reliably.

    RETURN_MAT polled straight after a job answers 0 - the counter has not
    settled yet - which would read as "the whole roll was consumed". Wait, then
    retry, and treat 0 as "no reading" rather than as truth.
    """
    time.sleep(settle)
    for _ in range(attempts):
        mm = (dev.material() or {}).get("remaining_mm")
        if mm:                     # non-None and non-zero
            return mm
        time.sleep(0.4)
    return None


def _wait_buffer_ready(dev, attempts=80):
    """Poll until the printer's input buffer has room (buf_full clear)."""
    for _ in range(attempts):
        st = dev.status()
        if st is not None:
            bad = st & FATAL_FLAGS
            if bad:
                raise E10Error(_flag_message(bad))
            if "buf_full" not in st:
                return st
        time.sleep(0.05)
    raise E10Error("Printer buffer never drained.")


def prepare_buffers(bufs, log=None):
    """Compress and frame every buffer UP FRONT, before the motor starts.

    Anything done between buffers is time the printhead spends starving while
    the tape keeps advancing, so no compression happens mid-print.
    """
    comps = [compress_lzma(b) for b in bufs]
    # Every buffer MUST carry its own calc_speed(len(comp)). The value is not a
    # free speed dial - the firmware validates it against the declared length
    # and silently refuses the whole job otherwise: it acks everything, reports
    # success and prints nothing. Measured twice each, zero tape: a 1317B
    # buffer at 55 instead of its own 45, and a 517B buffer at 45 instead of
    # its own 55. So the motor rate DOES change at a seam and cannot be
    # equalised; see choose_splits for how the seam is hidden instead.
    prepared = []
    for i, comp in enumerate(comps):
        frames = build_data_frames(comp)
        prepared.append((comp, frames, calc_speed(len(comp))))
        if log:
            log.info("buffer %d/%d: %dB lzma, %d frame(s), speed=%d",
                     i + 1, len(comps), len(comp), len(frames),
                     prepared[-1][2])
    return prepared


def transfer_buffers(dev, prepared, log=None):
    """Stream prepared buffers back to back.

    Deliberately does NOT wait for buf_full to clear between buffers: that bit
    clears only once the printer has drained what it had, which is already too
    late - it stalls, and the tape advances a step unburnt, leaving a white
    line through whatever glyph sits at the seam. The per-frame acks are the
    real flow control; the firmware simply withholds them until it has room.
    """
    for i, (comp, frames, speed) in enumerate(prepared):
        if not dev.cmd(CMD_NEXT_ZIPPEDBULK, 512, len(frames)):
            raise E10Error(f"Printer did not answer NEXT_ZIPPEDBULK "
                           f"(buffer {i + 1}/{len(prepared)}).")
        dev.send_frames(frames, log=log)
        # Read the BUF_FULL reply rather than draining it: same round trip,
        # but it keeps the stream in sync and costs no extra delay.
        dev.cmd(CMD_BUF_FULL, len(comp), speed, want=True)


def transfer_single_stream(dev, bufs, log=None):
    """Upstream's shape: every 4096-byte buffer in ONE LZMA stream, one
    NEXT_ZIPPEDBULK, one BUF_FULL. Works on the T50M Pro upstream tested."""
    blob = b"".join(bufs)
    comp = compress_lzma(blob)
    speed = calc_speed(len(comp) // len(bufs))
    frames = build_data_frames(comp)
    if log:
        log.info("single stream: %d buffer(s), %dB lzma, %d frame(s), speed=%d",
                 len(bufs), len(comp), len(frames), speed)
    if not dev.cmd(CMD_NEXT_ZIPPEDBULK, 512, len(frames)):
        raise E10Error("Printer did not answer NEXT_ZIPPEDBULK.")
    dev.send_frames(frames, log=log)
    time.sleep(0.02)
    dev.cmd(CMD_BUF_FULL, len(comp), speed, want=False)
    time.sleep(0.05)
    dev.drain()   # final-frame ack + BUF_FULL reply, deliberately unread


def print_rows(rows, mac, head_dots=HEAD_DOTS, density=8, log=None,
               mode="perbuf", overlap=BOUNDARY_OVERLAP):
    """Send raster rows to the printer. Raises E10Error on failure."""
    def note(fmt, *args):
        if log:
            log.info("e10: " + fmt, *args)

    img, cols, bpl = raster_to_column_major(rows, head_dots, len(rows))
    bufs = split_into_buffers(img, bpl, cols, MARGIN_DOTS, MARGIN_DOTS,
                              density, density, overlap=overlap)
    note("%d lines (%.0f mm), %d buffer(s)", len(rows),
         len(rows) / DOTS_PER_MM, len(bufs))
    prepared = prepare_buffers(bufs, log=log) if mode != "single" else None

    dev = connect(mac)
    try:
        if not dev.cmd(CMD_CHECK_DEVICE):
            raise E10Error("Printer did not answer CHECK_DEVICE.")
        st = dev.status() or set()
        bad = st & FATAL_FLAGS
        if bad:
            raise E10Error(_flag_message(bad))
        if "low_battery" in st:
            note("low battery")

        # This firmware will happily no-op a whole job: it acks every command
        # and reports completion while printing nothing. The RFID tape counter
        # is the only honest witness, so snapshot it and verify afterwards.
        mat_before = dev.material()
        tape_before = (mat_before or {}).get("remaining_mm")

        dev.cmd(CMD_START_PRINT)
        for _ in range(40):
            s2 = dev.status() or set()
            if "printing" in s2 or "device_busy" in s2:
                break
            time.sleep(0.1)

        if mode == "single":
            transfer_single_stream(dev, bufs, log=log)
        else:
            transfer_buffers(dev, prepared, log=log)

        for _ in range(160):
            time.sleep(0.25)
            s2 = dev.status()
            if s2 is not None and "printing" not in s2 and "device_busy" not in s2:
                note("print complete")
                break
        else:
            raise E10Error("Timed out waiting for the printer to finish.")

        expect_mm = len(rows) / DOTS_PER_MM
        tape_after = _read_tape_mm(dev)
        if tape_before is not None and tape_after is not None:
            used = tape_before - tape_after
            note("tape used: %d mm (expected ~%.0f mm)", used, expect_mm)
            if used <= 0:
                raise E10Error(
                    "The printer reported success but used no tape, so nothing "
                    "was printed. Power-cycle it (hold the power button until "
                    "it switches off, then on) and try again.")
    finally:
        # Always end the job: bailing out without STOP_PRINT leaves the printer
        # in a started-print state that confuses the next job.
        try:
            dev.cmd(CMD_STOP_PRINT, want=False)
        except OSError:
            pass
        dev.close()


def print_card(card, mac, density=8, target_mm=70, log=None,
               overlap=BOUNDARY_OVERLAP):
    """Render and print one card label. Returns the label length in mm."""
    img, size, n_lines = render_card(card, target_mm=target_mm)
    if log:
        log.info("e10: %r -> %dx%d dots (%.0f mm), title %dpx, %d oracle line(s)",
                 card.get("name"), img.size[0], img.size[1],
                 img.size[0] / DOTS_PER_MM, size, n_lines)
    print_rows(image_to_rows(img), mac, density=density, log=log,
               overlap=overlap)
    return img.size[0] / DOTS_PER_MM
