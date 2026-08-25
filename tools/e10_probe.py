#!/usr/bin/env python3
"""Read-only probe for the SUPVAN / Katasymbol E10 over Bluetooth Classic RFCOMM.

Sends only CHECK_DEVICE (0x12) and INQUIRY_STA (0x11): neither prints nor
feeds paper. Purpose is to confirm the printer speaks the documented Supvan
framed protocol before investing in a full driver.

Protocol reference: https://github.com/heeen/supvan-cups/blob/HEAD/docs/PROTOCOL.md

Usage:
    python3 tools/e10_probe.py [MAC]     # MAC optional; discovered if omitted
"""
import socket
import sys
import time

SUPVAN_OUI = "A4:93:40"


def discover_mac():
    """Find a paired Supvan printer by its OUI. A specific MAC identifies
    somebody's physical device, so none is hardcoded here."""
    import subprocess
    try:
        out = subprocess.run(["bluetoothctl", "devices"], capture_output=True,
                             text=True, timeout=6).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        p = line.split(None, 2)
        if len(p) >= 2 and p[0] == "Device" and p[1].upper().startswith(SUPVAN_OUI):
            return p[1]
    return None

MAGIC1, MAGIC2 = 0x7E, 0x5A
PROTO_ID, PROTO_VER = 0x10, 0x01
REQ_MARKER, DATA_TYPE = 0xAA, 0x01

CHECK_DEVICE = 0x12
INQUIRY_STA = 0x11

# INQUIRY_STA status bits, BT frame offsets 14..20 (see PROTOCOL.md)
MSTA_LO = {
    0x01: "buf_full", 0x02: "label_rw_error", 0x04: "label_end",
    0x08: "label_mode_error", 0x10: "ribbon_rw_error", 0x20: "ribbon_end",
    0x40: "low_battery",
}
MSTA_HI = {0x04: "device_busy", 0x08: "head_temp_high"}
FSTA_LO = {0x08: "cover_open", 0x10: "insert_usb", 0x40: "printing"}
FSTA_HI = {0x01: "label_not_installed"}


def build_cmd(cmd, param=0, block_count=0):
    """16-byte host->device command frame."""
    f = bytearray(16)
    f[0], f[1] = MAGIC1, MAGIC2
    f[2], f[3] = 0x0C, 0x00           # payload length = 12 (LE)
    f[4], f[5] = PROTO_ID, PROTO_VER
    f[6], f[7] = REQ_MARKER, cmd
    f[10], f[11] = 0x00, DATA_TYPE
    f[12:14] = param.to_bytes(2, "little")
    f[14:16] = block_count.to_bytes(2, "little")
    f[8:10] = (sum(f[10:16]) & 0xFFFF).to_bytes(2, "little")  # checksum over [10..16]
    return bytes(f)


def decode_flags(byte, table):
    return [name for mask, name in table.items() if byte & mask]


def parse_response(data):
    if len(data) < 8:
        return f"  short response ({len(data)} bytes)"
    if data[0] != MAGIC1 or data[1] != MAGIC2:
        return f"  !! bad magic {data[0]:#04x} {data[1]:#04x} (expected 0x7e 0x5a)"
    out = [
        f"  magic OK  proto_id={data[4]:#04x} marker={data[5]:#04x}/{data[6]:#04x} "
        f"cmd={data[7]:#04x}  len={int.from_bytes(data[2:4], 'little')}"
    ]
    if data[7] == INQUIRY_STA and len(data) >= 20:
        flags = (decode_flags(data[14], MSTA_LO) + decode_flags(data[15], MSTA_HI)
                 + decode_flags(data[16], FSTA_LO) + decode_flags(data[17], FSTA_HI))
        out.append(f"  status flags: {', '.join(flags) if flags else 'none (idle, ready)'}")
        if len(data) >= 20:
            out.append(f"  print_count: {int.from_bytes(data[18:20], 'little')}")
    return "\n".join(out)


def try_connect(mac, channels=range(1, 31)):
    for ch in channels:
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s.settimeout(5.0)
        try:
            s.connect((mac, ch))
            print(f"[+] connected on RFCOMM channel {ch}")
            return s
        except OSError:
            s.close()
    return None


def main():
    mac = sys.argv[1] if len(sys.argv) > 1 else discover_mac()
    if not mac:
        print("[!] No paired Supvan printer found. Pair it, or pass its MAC.")
        return 1
    print(f"[*] target {mac}  (printer must be POWERED ON)")

    sock = try_connect(mac)
    if not sock:
        print("[!] could not open RFCOMM on any channel 1-30.")
        print("    Is the printer switched on and in range?")
        return 1

    try:
        for name, cmd in (("CHECK_DEVICE", CHECK_DEVICE), ("INQUIRY_STA", INQUIRY_STA)):
            frame = build_cmd(cmd)
            print(f"\n[>] {name} ({cmd:#04x}): {frame.hex(' ')}")
            sock.send(frame)
            time.sleep(0.2)
            try:
                resp = sock.recv(256)
            except socket.timeout:
                print("  <no response (timeout)>")
                continue
            print(f"[<] {len(resp)} bytes: {resp.hex(' ')}")
            print(parse_response(resp))
    finally:
        sock.close()
        print("\n[*] closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
