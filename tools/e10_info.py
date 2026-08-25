#!/usr/bin/env python3
"""Read-only: query loaded media (RETURN_MAT 0x30), firmware (0xC5), name (0x16).

None of these commands print or feed paper.
"""
import socket, sys, time

MAC = sys.argv[1] if len(sys.argv) > 1 else "A4:93:40:B7:3B:F0"
HDR = 22  # BT_RESP_HEADER_LEN


def make_cmd(cmd, block_size=0, block_count=0):
    f = bytearray(16)
    f[0], f[1], f[2] = 0x7E, 0x5A, 0x0C
    f[4], f[5], f[6], f[7] = 0x10, 0x01, 0xAA, cmd
    f[11] = 0x01
    f[12:14] = block_size.to_bytes(2, "little")
    f[14:16] = block_count.to_bytes(2, "little")
    f[8:10] = (sum(f[10:16]) & 0xFFFF).to_bytes(2, "little")
    return bytes(f)


def txn(s, cmd, label):
    s.send(make_cmd(cmd))
    time.sleep(0.25)
    try:
        r = s.recv(512)
    except socket.timeout:
        print(f"{label:14s} <timeout>")
        return None
    print(f"{label:14s} {len(r):3d}B  {r.hex(' ')}")
    return r


s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
s.settimeout(4.0)
s.connect((MAC, 1))
print(f"connected {MAC} ch1\n")

txn(s, 0x12, "CHECK_DEVICE")
fw = txn(s, 0xC5, "READ_FWVER")
rev = txn(s, 0x17, "READ_REV")
name = txn(s, 0x16, "RD_DEV_NAME")
mat = txn(s, 0x30, "RETURN_MAT")
s.close()

print()
if fw and len(fw) > HDR:
    print(f"firmware version byte: {fw[HDR]} (0x{fw[HDR]:02x})")
if rev and len(rev) >= HDR + 3:
    print(f"protocol rev: {rev[HDR:HDR+3].decode('ascii', 'replace')}")
if name and len(name) > HDR:
    nm = name[HDR:].split(b"\x00")[0].decode("ascii", "replace")
    print(f"device name: {nm}")

if mat and len(mat) >= HDR + 21:
    p = mat[HDR:]
    print("\n--- MATERIAL (from RFID tag) ---")
    print(f"  tag UID    : {p[0:7].hex().upper()}")
    print(f"  tag code   : {p[7:15].hex().upper()}   <- non-zero = genuine")
    print(f"  label SN   : {int.from_bytes(p[15:17],'little')}")
    print(f"  label_type : {p[17]}")
    print(f"  width_mm   : {p[18]}")
    print(f"  height_mm  : {p[19]}  (0 = continuous)")
    print(f"  gap_mm     : {p[20]}")
    if len(p) >= 25:
        print(f"  remaining  : {int.from_bytes(p[21:25],'little')} labels")
    print(f"\n  => print width {p[18]} mm = {p[18]*8} dots = {-(-p[18]*8//8)} bytes/line")
else:
    print("\nRETURN_MAT: short/absent response")
