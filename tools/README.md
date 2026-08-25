# E10 label printer tools

Diagnostics and CLIs for the SUPVAN / Katasymbol E10, over Bluetooth Classic
RFCOMM. All are thin wrappers over `momir_vig_label_bot/e10.py`.

**Run these with a Python that has `socket.AF_BLUETOOTH`** — the project venv
(3.9) does not; the system `python3` does:

```sh
python3 -c "import socket; print(hasattr(socket,'AF_BLUETOOTH'))"
```

| Tool | Purpose |
|---|---|
| `e10_probe.py` | Read-only liveness + decoded status flags |
| `e10_info.py`  | Media (from the roll's RFID tag), firmware, device name |
| `e10_print.py` | Diagnostics + raw text/pattern printing |
| `e10_card.py`  | Render an MTG card label; preview or print |

```sh
python3 tools/e10_print.py --selftest            # 22 protocol vectors, no hardware
python3 tools/e10_print.py --info                # status + loaded media
python3 tools/e10_print.py --dry-run --text HI   # build everything, send nothing
python3 tools/e10_print.py --text HELLO --bar 4  # print text + solid ink bar
python3 tools/e10_card.py --card card.json --preview /tmp/label.png
python3 tools/e10_card.py --card card.json --print
```

## The one rule

**The raster canvas is always the PRINTHEAD width (12 mm = 96 dots), never the
media width `RETURN_MAT` reports (15 mm here).** The head is a fixed bar and the
tape runs centred under it. Send a wider line and the firmware feeds the tape,
burns nothing, and reports success at every step — a silent blank label.

## Notes

- The printer **auto-sleeps after ~1–2 min idle** and then refuses connections.
  Press its power button. `e10.connect()` retries 3×.
- `remaining_mm` from the RFID tag counts **millimetres of tape**, not labels.
- Density 8 prints cleanly on genuine Supvan stock.

See [`docs/e10-printer.md`](../docs/e10-printer.md) for the firmware traps —
every one of them reports success while printing nothing.

Protocol reference: <https://github.com/heeen/supvan-cups/blob/HEAD/docs/PROTOCOL.md>
