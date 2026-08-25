# Driving the Katasymbol / SUPVAN E10 from Linux

Hard-won notes from making `momir_vig_label_bot/e10.py` work. Everything here
was measured on a real device, not inferred. Read this before changing the
print path — **every failure mode below is reported by the firmware as
success**, so "it ran without error" proves nothing.

Protocol reference: <https://github.com/heeen/supvan-cups> (`docs/PROTOCOL.md`).
That project is excellent but targets the T-series; several of its assumptions
do not hold on the E10, and those differences are exactly what cost the most
time here.

---

## The device

| | |
|---|---|
| Model | SUPVAN E10 ("Katasymbol" is the app brand, not the maker) |
| Transport | **Bluetooth Classic SPP / RFCOMM, channel 1** — not BLE |
| Service UUID | `00001101-0000-1000-8000-00805f9b34fb` |
| MAC OUI | `A4:93:40` (Supvan) |
| Printhead | **12 mm = 96 dots = 12 bytes/line** at 203 dpi |
| Media (this roll) | 15 mm wide, continuous, genuine Supvan RFID tag |
| Protocols NOT spoken | ESC/POS, ZPL, EPL, IPP. No USB data path — USB-C is charge-only |

So `lp` / `lpr` / CUPS can never reach it. The only way in is its own framed
binary protocol over RFCOMM.

---

## The five traps

### 1. The canvas is the PRINTHEAD width, never the media width

`RETURN_MAT` reports the *media* width (15 mm here). The head is 12 mm. Size
the raster to the media and `per_line_byte` becomes 15, which exceeds what the
firmware will render: **it feeds the whole label and burns nothing.**

Media width tells you where to *centre* content. It is not the raster width.

```python
head_dots = 12 * 8      # 96
bpl       = head_dots // 8   # 12   <- always this
```

### 2. Send buffers ONE AT A TIME, not as one stream

Labels past ~44 mm need more than one 4096-byte print buffer
(`MAX_BUF_DATA 4074 // 12 = 339 columns = 42 mm`).

Upstream concatenates every buffer into a single LZMA stream and sends one
`NEXT_ZIPPEDBULK` + one `BUF_FULL`. That works on their T50M Pro. On the E10 it
renders **only the first buffer**, feeds the full label length, and reports
success — a label truncated mid-word with no error anywhere.

Correct shape, per buffer: compress it alone → `NEXT_ZIPPEDBULK` → data frames
→ `BUF_FULL`.

### 3. Drain the replies you deliberately don't read

Two replies are never read during a transfer: the ack for the **final** data
frame, and (if you fire it blind) the `BUF_FULL` reply. They still sit in the
socket. From the second buffer onward every read then returns the **previous**
command's reply. The tell:

```
buffer 2/2
  frame 1/4 ack: 7e 5a 13 00 10 03 55 5c ...
                                      ^^ 0x5C = NEXT_ZIPPEDBULK, not 0xBB
```

A healthy data-frame ack echoes **`0xBB` at byte 7**. Any other opcode there
means the stream has desynced. Symptom is *intermittent*: sometimes the job
survives, sometimes it prints nothing.

Fix: `drain()` between phases, validate the echoed opcode on every reply, and
prefer reading the `BUF_FULL` reply over draining it.

### 4. `speed` is validated — it is not a free dial

Every buffer must declare exactly its own `calc_speed(len(compressed))`. The
firmware checks it against the declared length and **silently refuses the whole
job** on any mismatch: acks everything, reports success, prints nothing.

Measured, twice each, zero tape consumed both times:

| Buffer | Its own speed | Forced to | Result |
|---|---|---|---|
| 1317 B | 45 | 55 | nothing printed |
| 517 B | 55 | 45 | nothing printed |

So the motor rate **does** change at every seam, and cannot be equalised.

### 5. `ribbon_end` (MSTA low `0x20`) blocks printing — despite no ribbon

Named for the ribbon-based T-series; the E10 is direct thermal. It is not
cosmetic. While set, the printer accepts every command, reports a clean
completion, and prints nothing. It **latches**, survives `STOP_PRINT` and
`CHECK_DEVICE`, and only a **power cycle** clears it.

Do not filter it out as a phantom flag. Also: always send `STOP_PRINT` on the
way out, including error paths — bailing without it leaves the printer in a
started-print state.

---

## The white line at buffer boundaries

A 1-dot white line appears at every buffer seam, straight through whatever
glyph sits there. Three fixes were tried and **all failed**:

| Attempt | Result |
|---|---|
| Re-send the boundary column (`overlap=1`) | No change — the column is not dropped from the data |
| Pre-compress + stream back to back (no starvation) | No change |
| Hold speed constant across buffers | Job refused entirely (see trap 4) |

It is a firmware property of the buffer switch. It cannot be removed.

**So place it instead.** `choose_splits()` picks the seam at a column with no
ink, where a missing dot column is invisible. On the reference card this moved
the seam from dot 347 (mid-"o" of "Aviator") to dot 334, in the gap before
"{3}" — and the line disappeared visually.

Caveat: content dense enough to have no blank column in the legal split range
falls back to the largest buffer, and the line will show.

---

## Verify with the tape counter, not the status

The firmware will ack everything, clear `printing`, and report success while
doing nothing. `RETURN_MAT`'s `remaining_mm` is the only honest witness — and
on continuous stock it counts **millimetres of tape**, not labels, despite the
upstream field name.

`print_rows` snapshots it before and after and fails the job if it did not
move. Keep that guard.

Read it **late**: polled immediately after a job it returns `0`, which naively
reads as "the whole roll was consumed". Wait ~1 s, retry, and treat `0` as "no
reading" rather than as truth.

---

## Python needs Bluetooth support compiled in

`socket.AF_BLUETOOTH` exists only if the interpreter was built against the
Bluetooth headers. This project's venv is not:

```
venv/bin/python  3.9.23   AF_BLUETOOTH False   <- cannot reach the printer
/usr/bin/python3 3.12     AF_BLUETOOTH True
```

But the system Python cannot import the pinned `python-telegram-bot==8.1.1`
(its vendored urllib3 breaks on 3.12). Neither interpreter can do both jobs, so
the bot shells out to `E10_PYTHON` for the print step — the same thing it
already does for `pango-view` and `convert`. See `momir_vig_label_bot/print_cli.py`.

Check first, before debugging anything else:

```sh
python3 -c "import socket; print(hasattr(socket,'AF_BLUETOOTH'))"
```

---

## Other observations

- **Auto power-off** after ~1–2 min idle; it then refuses RFCOMM connects with
  a timeout. `connect()` retries 3×; press the power button to wake it.
- **Undocumented telemetry** in the `INQUIRY_STA` reply past what upstream
  documents: bytes 24–25 are battery millivolts (observed 3976 → 4187 while
  charging), bytes 22–23 look like printhead temperature ×10 (28.7 °C idle →
  33.5 °C after a job).
- **Density 8** prints cleanly on genuine Supvan stock.
- The device's PnP record is `usb:v05ACp0239d0644` — an **Apple keyboard**
  VID/PID the vendor copy-pasted into the firmware. Ignore it.
- Text runs **along** the feed axis: render into a PIL image of
  `(feed_length × head_dots)` and transpose — printed line `f`, dot `x` across
  the head, is pixel `(f, x)`.

---

## Tools

`tools/` holds diagnostics; see `tools/README.md`. Most useful when something
breaks:

```sh
python3 tools/e10_print.py --selftest        # 22 protocol vectors, no hardware
python3 tools/e10_print.py --info            # status flags + loaded media
python3 tools/e10_print.py --dry-run --text HI
python3 tools/e10_print.py --twobuf          # 2-buffer seam diagnostic
python3 tools/e10_print.py --noise 20        # 1 buffer, many frames
```

`--twobuf` and `--noise` exist because the multi-buffer bug was only isolable
by separating "many buffers" from "many frames" — neither alone reproduced it.
