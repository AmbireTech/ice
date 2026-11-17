#!/usr/bin/env python3
"""Camera bring-up over SWD: raw frames to the Mac, sensor registers live.

Talks to the kernel's camera debug mailbox (g_camera_debug in
io/camera/stm32u5/camera.c) through OpenOCD while the camera test runs on the
device. The core is never halted: the probe reads and writes RAM in the
background.

    camdbg.py status                       # the driver's status, as the screen shows it
    camdbg.py grab [-o DIR] [-n N]         # hold N raw frames, save PNG + .y8 each
    camdbg.py w BANK REG VAL [REG VAL ..]  # register writes (hex; BANK 0/1 OV2640, - none)
    camdbg.py r BANK REG [REG ..]          # register reads
    camdbg.py dump BANK                    # all 256 registers of a bank

Options: --probe cmsis-dap (default; the jig) | stlink, --board ambire_v4 (ELF
for symbols), --khz 4000.
"""

import argparse
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
FW = HERE.parent.parent
PROBES = {
    "cmsis-dap": ["-f", "interface/cmsis-dap.cfg", "-c", "transport select swd"],
    "stlink": ["-f", "interface/stlink.cfg", "-c", "transport select hla_swd"],
}
TCL_PORT = 6666
MAGIC = 0xCA3DB600
OP_WRITE, OP_READ = 1, 2
NO_BANK = 0xFFFF

STATUS_FIELDS = [
    "state", "sensor_id", "raw_id", "open_error", "error_reg", "zoom", "side",
    "clock", "tune", "scale_x10", "frames", "failures", "last_bytes",
    "last_flags", "last_capture_ms", "vsync_high_pm", "vsync_edges",
    "href_high_pm", "href_edges", "pclk_edges", "vsync_blank_high",
]


def die(msg):
    print(f"camdbg: {msg}", file=sys.stderr)
    sys.exit(1)


def gdb_eval(elf, exprs):
    """Values of C expressions from the ELF's debug info (addresses, offsets)"""
    args = ["arm-none-eabi-gdb", "-batch", "-nx"]
    for e in exprs:
        args += ["-ex", f"printf \"@@%u\\n\", (unsigned)({e})"]
    out = subprocess.run(args + [str(elf)], capture_output=True, text=True)
    vals = [int(line[2:]) for line in out.stdout.splitlines() if line.startswith("@@")]
    if len(vals) != len(exprs):
        die(f"gdb could not evaluate {exprs} in {elf}:\n{out.stdout}{out.stderr}")
    return vals


class Layout:
    def __init__(self, elf):
        names = ["magic", "req_seq", "ack_seq", "n_ops", "hold_req", "held",
                 "frame_addr", "frame_side", "frame_seq", "st", "ops"]
        exprs = ["&g_camera_debug"] + [
            f"(char *)&g_camera_debug.{n} - (char *)&g_camera_debug" for n in names]
        exprs += ["sizeof(g_camera_debug.ops[0])", "sizeof(g_camera_debug.ops)/sizeof(g_camera_debug.ops[0])",
                  "(char *)&g_camera_debug.ops[0].bank - (char *)&g_camera_debug.ops[0]",
                  "(char *)&g_camera_debug.ops[0].reg - (char *)&g_camera_debug.ops[0]",
                  "(char *)&g_camera_debug.ops[0].val - (char *)&g_camera_debug.ops[0]",
                  "(char *)&g_camera_debug.ops[0].result - (char *)&g_camera_debug.ops[0]",
                  "(char *)&g_camera_debug.ops[0].ok - (char *)&g_camera_debug.ops[0]",
                  "(char *)&g_camera_debug.st.last_capture_ms - (char *)&g_camera_debug.st"]
        v = gdb_eval(elf, exprs)
        self.base = v[0]
        self.off = dict(zip(names, v[1:12]))
        (self.op_size, self.n_max, self.op_bank, self.op_reg, self.op_val,
         self.op_result, self.op_ok, lcm) = v[12:20]
        if lcm != STATUS_FIELDS.index("last_capture_ms") * 4:
            die("camera_status_t layout changed: update STATUS_FIELDS")

    def addr(self, name):
        return self.base + self.off[name]


class OpenOcd:
    def __init__(self, probe, khz):
        self.proc = subprocess.Popen(
            ["openocd", *PROBES[probe], "-c", f"adapter speed {khz}",
             "-f", "target/stm32u5x.cfg",
             "-c", f"tcl_port {TCL_PORT}", "-c", "gdb_port disabled",
             "-c", "telnet_port disabled", "-c", "init"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + 10
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", TCL_PORT), timeout=1)
                break
            except OSError as e:
                if self.proc.poll() is not None:
                    die("OpenOCD exited:\n" + self.proc.stdout.read())
                if time.monotonic() > deadline:
                    self.close()
                    die(f"OpenOCD did not open its TCL port: {e}")
                time.sleep(0.2)
        self.sock.settimeout(30)

    def cmd(self, c):
        self.sock.sendall(c.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            chunk = self.sock.recv(65536)
            if not chunk:
                die(f"OpenOCD closed the connection during: {c}")
            buf += chunk
        return buf[:-1].decode()

    def rd(self, addr, n=1):
        out = self.cmd(f"read_memory {addr:#x} 32 {n}")
        try:
            return [int(x, 16) for x in out.split()]
        except ValueError:
            die(f"memory read at {addr:#x} failed: {out}")

    def wr(self, addr, vals, width=32):
        vals = vals if isinstance(vals, (list, tuple)) else [vals]
        out = self.cmd(f"write_memory {addr:#x} {width} {{{' '.join(hex(v) for v in vals)}}}")
        if out.strip():
            die(f"memory write at {addr:#x} failed: {out}")

    def dump(self, addr, size, path):
        out = self.cmd(f"dump_image {path} {addr:#x} {size}")
        if "dumped" not in out:
            die(f"dump at {addr:#x} failed: {out}")

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                print("camdbg: OpenOCD did not stop, killing it", file=sys.stderr)
                self.proc.kill()


def png_gray(path, w, h, pixels):
    raw = b"".join(b"\x00" + pixels[y * w:(y + 1) * w] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def stats(px):
    n = len(px)
    hist = [0] * 256
    for v in px:
        hist[v] += 1
    mean = sum(i * c for i, c in enumerate(hist)) / n
    clip_hi = sum(hist[250:]) * 100 / n
    clip_lo = sum(hist[:6]) * 100 / n
    acc, p = 0, {}
    for i, c in enumerate(hist):
        acc += c
        for q in (1, 50, 99):
            if q not in p and acc >= n * q / 100:
                p[q] = i
    return f"mean {mean:.0f}  p1/p50/p99 {p[1]}/{p[50]}/{p[99]}  clip>=250 {clip_hi:.1f}%  <=5 {clip_lo:.1f}%"


class Dbg:
    def __init__(self, ocd, lay):
        self.o, self.l = ocd, lay
        magic = self.o.rd(lay.base)[0]
        if magic != MAGIC:
            die(f"no camera debug mailbox at {lay.base:#x} (read {magic:#x}): "
                "is the debug firmware running, and the ELF the one flashed?")

    def status(self):
        words = self.o.rd(self.l.addr("st"), len(STATUS_FIELDS))
        return dict(zip(STATUS_FIELDS, words))

    def ops(self, ops, timeout=5):
        """ops: [(op, bank, reg, val)]; returns [(ok, result)]"""
        if len(ops) > self.l.n_max:
            die(f"at most {self.l.n_max} ops per call")
        base = self.l.addr("ops")
        for i, (op, bank, reg, val) in enumerate(ops):
            a = base + i * self.l.op_size
            self.o.wr(a, [op, bank, reg], 16)
            self.o.wr(a + self.l.op_val, [val & 0xFF, 0], 8)
            self.o.wr(a + self.l.op_ok, 0)
        self.o.wr(self.l.addr("n_ops"), len(ops))
        seq = self.o.rd(self.l.addr("req_seq"))[0] + 1
        self.o.wr(self.l.addr("req_seq"), seq)
        deadline = time.monotonic() + timeout
        while self.o.rd(self.l.addr("ack_seq"))[0] != seq:
            if time.monotonic() > deadline:
                die("the driver did not take the ops: is the camera test running "
                    "(screen on, camera open)?")
            time.sleep(0.02)
        res = []
        for i in range(len(ops)):
            a = base + i * self.l.op_size
            w = self.o.rd(a + (self.l.op_val & ~3), 1)[0]
            result = (w >> (8 * ((self.l.op_result) % 4))) & 0xFF
            ok = self.o.rd(a + self.l.op_ok)[0]
            res.append((ok, result))
        return res

    def grab(self, outdir, n, timeout=10):
        outdir.mkdir(parents=True, exist_ok=True)
        saved = []
        try:
            for k in range(n):
                seq0 = self.o.rd(self.l.addr("frame_seq"))[0]
                self.o.wr(self.l.addr("hold_req"), 1)
                deadline = time.monotonic() + timeout
                while self.o.rd(self.l.addr("frame_seq"))[0] == seq0:
                    if time.monotonic() > deadline:
                        die("no frame held in time: is the camera test capturing?")
                    time.sleep(0.02)
                addr, side = self.o.rd(self.l.addr("frame_addr"), 2)
                st = self.status()
                with tempfile.TemporaryDirectory() as td:
                    raw = Path(td) / "f.y8"
                    self.o.dump(addr, side * side, raw)
                    px = raw.read_bytes()
                # Released before the next one: the app captures a fresh frame
                self.o.wr(self.l.addr("hold_req"), 0)
                stamp = time.strftime("%H%M%S")
                name = (f"{stamp}_{k:02d}_s{side}_z{st['zoom']}_c{st['clock']}"
                        f"_t{st['tune']}")
                (outdir / f"{name}.y8").write_bytes(px)
                png_gray(outdir / f"{name}.png", side, side, px)
                print(f"{outdir / name}.png  {stats(px)}  capture {st['last_capture_ms']} ms")
                saved.append(outdir / f"{name}.png")
                if k + 1 < n:
                    time.sleep(0.15)
        finally:
            self.o.wr(self.l.addr("hold_req"), 0)
        return saved


def parse_bank(s):
    return NO_BANK if s in ("-", "none") else int(s, 16)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", default=os.environ.get("SWD_PROBE", "cmsis-dap"), choices=PROBES)
    ap.add_argument("--board", default="ambire_v4")
    ap.add_argument("--khz", type=int, default=4000)
    ap.add_argument("-o", "--out", default=str(FW / "out" / "camframes"))
    ap.add_argument("-n", type=int, default=1)
    ap.add_argument("cmd", choices=["status", "grab", "w", "r", "dump"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()

    elf = FW / "out" / a.board / "kernel.elf"
    if not elf.is_file():
        die(f"{elf} missing: build the board first")
    lay = Layout(elf)
    ocd = OpenOcd(a.probe, a.khz)
    try:
        d = Dbg(ocd, lay)
        if a.cmd == "status":
            st = d.status()
            print("  ".join(f"{k} {v:#x}" if k in ("sensor_id", "raw_id", "last_flags") else f"{k} {v}"
                            for k, v in st.items()))
        elif a.cmd == "grab":
            d.grab(Path(a.out), a.n)
        elif a.cmd == "w":
            if len(a.args) < 3 or len(a.args) % 2 == 0:
                die("w BANK REG VAL [REG VAL ...]")
            bank = parse_bank(a.args[0])
            pairs = [(int(a.args[i], 16), int(a.args[i + 1], 16)) for i in range(1, len(a.args), 2)]
            res = d.ops([(OP_WRITE, bank, r, v) for r, v in pairs])
            for (r, v), (ok, _) in zip(pairs, res):
                print(f"{r:#04x} <- {v:#04x}  {'ok' if ok else 'NACK'}")
        elif a.cmd in ("r", "dump"):
            bank = parse_bank(a.args[0]) if a.args else die("bank needed")
            regs = list(range(256)) if a.cmd == "dump" else [int(x, 16) for x in a.args[1:]]
            out = []
            for i in range(0, len(regs), lay.n_max):
                part = regs[i:i + lay.n_max]
                out += zip(part, d.ops([(OP_READ, bank, r, 0) for r in part]))
            if a.cmd == "dump":
                for i in range(0, 256, 16):
                    print(f"{i:02x}: " + " ".join(f"{res:02x}" if ok else "--" for _, (ok, res) in out[i:i + 16]))
            else:
                for r, (ok, res) in out:
                    print(f"{r:#04x} = {res:#04x}" if ok else f"{r:#04x} NACK")
    finally:
        ocd.close()


if __name__ == "__main__":
    main()
