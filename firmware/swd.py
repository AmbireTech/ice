#!/usr/bin/env python3
"""Bring up, flash and inspect the board over SWD (OpenOCD + arm-none-eabi-gdb).

    firmware/swd.py info                 # chip, option bytes, what is in flash, pins, boot command
    firmware/swd.py trustzone            # blank chip: TZEN = 1, SECBOOTADD0 = boardloader
    firmware/swd.py flash [image.bin]    # combined image (default: out/<board>/combined-firmware.bin)
    firmware/swd.py state [--halt]       # halt, where it is (symbolised), fault registers, RSOD info
    firmware/swd.py gdb                  # interactive gdb with every stage's symbols
    firmware/swd.py reset
    firmware/swd.py radio-info           # the nRF52840 radio, on its own SWD pads (TP_RSWDIO/TP_RSWCLK)
    firmware/swd.py radio-recover        # erase-all + unlock through Nordic's CTRL-AP (APPROTECT)
    firmware/swd.py radio-flash app.hex  # write, verify, reset

Options (before or after the command): --probe stlink|cmsis-dap|jlink|rpi|beagley|beagle (default stlink,
or $SWD_PROBE), --khz N (SWD clock, default 1000: flying leads), --under-reset (hold NRST
while connecting; needs TP_NRST wired), --board NAME (default ambire_p0).

No probe: a Linux board's GPIO is one (--probe rpi|beagle --host user@HOST, or $SWD_HOST).
OpenOCD runs on that board over SSH (`setup` installs it there), gdb here talks to it over
the network. Pins are found by their line names on the board (gpioinfo), so the gpiochip
numbering of the model does not matter:
  Raspberry Pi (any model): header 22 = GPIO25 -> SWCLK, 18 = GPIO24 -> SWDIO,
                            12 = GPIO18 -> NRST (optional), 20 = GND
  BeagleY-AI (--probe beagley): the Pi layout, but SWDIO on header 15 = GPIO22: its
                            pin 18 (GPIO24) is a wake-up-domain UART pad
                            (WKUP_UART0_TXD) and garbled SWD replies on proto 0
  BeagleBone (Black/Green): P9_12 -> SWCLK, P9_15 -> SWDIO, P9_23 -> NRST (optional),
                            P9_1 = GND

Register addresses and bit positions come from the vendored CMSIS headers
(trezor-firmware/vendor/cmsis_device_u5, core_cm33.h); the memory map from
core/embed/models/T3T2/memory.h.
"""
import argparse
import os
import re
import shlex
import shutil
import tempfile
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

PROBES = {
    "stlink": ["-f", "interface/stlink.cfg", "-c", "transport select hla_swd"],
    "cmsis-dap": ["-f", "interface/cmsis-dap.cfg", "-c", "transport select swd"],
    "jlink": ["-f", "interface/jlink.cfg", "-c", "transport select swd"],
}
# Linux boards as probes: GPIO line names for SWCLK, SWDIO, NRST. The Pi's are the pins
# OpenOCD's own raspberrypi configs use; the BeagleBone's are GPIO1 pins that boot in
# GPIO mode and are free of HDMI and eMMC.
# BeagleY-AI ("beagley"): Pi-layout header, but header pin 18 (GPIO24) is the AM67A's
# wake-up-domain pad WKUP_UART0_TXD / MCU_GPIO0_10; SWDIO goes on pin 15 (GPIO22 =
# main-domain GPIO0_41) instead, next to SWCLK's GPIO0_42 on pin 22.
GPIO_PROBES = {"rpi": ("GPIO25", "GPIO24", "GPIO18"), "beagley": ("GPIO25", "GPIO22", "GPIO18"),
               "beagle": ("P9_12", "P9_15", "P9_23")}
CHUNK_DIR = "/tmp/ambire-swd-chunks"
FLASH_BASE_S, BANK_PAGES = 0x0C000000, 128  # U585: 2 x 1 MB banks of 8 KB pages
SECKEYR, SECSR, SECCR = 0x5002200C, 0x50022024, 0x5002202C  # FLASH secure key/status/control
CHUNK = SECTOR = 8 * 1024  # one piece per flash sector: an empty sector is a pure erase
# The power latch (ambire_p0.h POWER_HOLD: PE5), secure GPIOE; GPIOE's clock is AHB2ENR1 bit 4
HOLD_GPIO, HOLD_PIN, HOLD_PORT_BIT = 0x52021000, 5, 4

# T3T2 memory map (models/T3T2/memory.h), secure aliases
BOARDLOADER_START = 0x0C004000
BOOTLOADER_START = 0x0C016000
FIRMWARE_START = 0x0C046000
BOOTARGS_START = 0x30000000

FLASH_R_NS = 0x40022000  # FLASH_R_BASE_NS; option registers read the same from both worlds
DBGMCU_IDCODE = 0xE0044000
FLASHSIZE = 0x0BFA07A0
UID = 0x0BFA0700
GPIOA_NS, GPIOE_NS = 0x42020000, 0x42021000
RCC_NS = 0x46020C00
SECURE_OFFSET = 0x10000000  # peripheral secure alias = non-secure + 0x1000_0000

# STM32U5 OPTR (FLASH_OPTR_*_Pos)
OPTR_BITS = {
    12: "nRST_STOP", 13: "nRST_STDBY", 14: "nRST_SHDW", 16: "IWDG_SW", 20: "SWAP_BANK",
    21: "DUALBANK", 26: "nSWBOOT0", 27: "nBOOT0", 28: "PA15_PUPEN", 31: "TZEN",
}
CFSR_BITS = {
    0: "IACCVIOL", 1: "DACCVIOL", 3: "MUNSTKERR", 4: "MSTKERR", 5: "MLSPERR", 7: "MMARVALID",
    8: "IBUSERR", 9: "PRECISERR", 10: "IMPRECISERR", 11: "UNSTKERR", 12: "STKERR", 13: "LSPERR",
    15: "BFARVALID", 16: "UNDEFINSTR", 17: "INVSTATE", 18: "INVPC", 19: "NOCP", 20: "STKOF",
    24: "UNALIGNED", 25: "DIVBYZERO",
}
HFSR_BITS = {1: "VECTTBL", 30: "FORCED", 31: "DEBUGEVT"}
SFSR_BITS = {0: "INVEP", 1: "INVIS", 2: "INVER", 3: "AUVIOL", 4: "INVTRAN", 5: "LSPERR",
             6: "SFARVALID", 7: "LSERR"}
CSR_RESET_BITS = {25: "option-byte load", 26: "NRST pin", 27: "brown-out/power-on",
                  28: "software", 29: "independent watchdog", 30: "window watchdog",
                  31: "low-power"}
BOOT_COMMANDS = {  # sys/startup/inc/sys/bootargs.h
    0x00000000: "NONE", 0x0FC35A96: "STOP_AND_WAIT", 0x3B7E1C64: "STOP_AND_CONNECT",
    0xFA4A5C8D: "INSTALL_UPGRADE", 0x7CD945A0: "SHOW_RSOD", 0xD965CE36: "WIPE",
    0xA5C3D4E2: "REBOOT", 0x4EEE8828: "POWER_OFF",
}
MAGIC = {0x515A5254: "TRZQ (bootloader header)", 0x425A5254: "TRZB (old bootloader header)",
         0x565A5254: "TRZV (vendor header)",
         0xFFFFFFFF: "erased"}
# Stages in the order a PC is matched against them: the first whose symbols cover it wins.
STAGES = ["boardloader", "bootloader", "secmon", "kernel", "firmware"]


def bits(value, names):
    return [name for pos, name in sorted(names.items()) if value >> pos & 1]


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


class Swd:
    def __init__(self, args):
        self.args = args
        self.out = HERE / "out" / args.board
        self.host = None
        if args.probe in GPIO_PROBES:
            self.host = args.host or die(f"--probe {args.probe} needs --host user@HOST (or $SWD_HOST)")
        elif not shutil.which("openocd"):
            die("openocd not found (brew install open-ocd)")
        self._probe = None
        self._pins = []  # (gpiochip, line) of the probe board's SWD pins

    def ssh(self, cmd, **kw):
        return subprocess.run(["ssh", self.host, cmd], capture_output=True, text=True, **kw)

    def gpio_lines(self):
        """{line name: (chip, offset)} from gpioinfo on the host (libgpiod v1 and v2 print alike)."""
        out = self.ssh("gpioinfo")
        if out.returncode != 0:
            die(f"gpioinfo on {self.host} failed ({out.stderr.strip()}): run `setup` first")
        lines, chip = {}, None
        for l in out.stdout.splitlines():
            m = re.match(r"^gpiochip(\d+)", l)
            if m:
                chip = int(m.group(1))
            m = re.match(r'^\s+line\s+(\d+):\s+"([^"]+)"', l)
            if m and chip is not None:
                lines[m.group(2)] = (chip, int(m.group(1)))
        return lines

    def gpio_probe(self):
        lines = self.gpio_lines()
        # Debian 13 builds OpenOCD 0.12 without linuxgpiod (0.12 speaks only libgpiod v1);
        # sysfsgpio is there, slower, and wants the kernel-wide GPIO numbers.
        drivers = self.ssh("openocd -c 'adapter list' -c shutdown 2>&1").stdout
        gpiod = re.search(r"\blinuxgpiod\b", drivers) is not None
        if gpiod:
            args = ["-c", "adapter driver linuxgpiod", "-c", "transport select swd"]
        elif re.search(r"\bsysfsgpio\b", drivers):
            args = ["-c", "adapter driver sysfsgpio", "-c", "transport select swd"]
            labels = dict(re.findall(r"^gpiochip(\d+) \[([^\]]+)\]", self.ssh("gpiodetect").stdout, re.M))
            bases = dict(re.findall(r"^(\S+) (\d+)$", self.ssh(
                "for c in /sys/class/gpio/gpiochip*; do echo \"$(cat $c/label) $(cat $c/base)\"; done").stdout, re.M))
        else:
            die(f"{self.host}'s OpenOCD has neither linuxgpiod nor sysfsgpio")
        sigs = ("swclk", "swdio", "srst") if self.args.under_reset else ("swclk", "swdio")
        for sig, want in zip(sigs, GPIO_PROBES[self.args.probe]):
            # BeagleBone names carry the ball after the header pin: "P9_12 [gpio1_28]", "P9_15A"
            found = [v for k, v in lines.items() if re.fullmatch(rf"{want}A?(\s.*)?", k)]
            if len(found) != 1:
                die(f"{len(found)} GPIO lines named {want} on {self.host} (want 1): "
                    f"is it a {self.args.probe}? Line names: {', '.join(sorted(lines))[:600]}")
            chip, offset = found[0]
            self._pins.append((chip, offset))
            if gpiod:
                args += ["-c", f"adapter gpio {sig} {offset} -chip {chip}"]  # srst drive mode: reset_config
            else:
                label = labels.get(str(chip))
                if label not in bases:
                    die(f"no sysfs GPIO base for gpiochip{chip} ({label}) on {self.host}")
                args += ["-c", f"sysfsgpio {sig}_num {int(bases[label]) + offset}"]
        return args

    def base(self):
        if self._probe is None:
            self._probe = self.gpio_probe() if self.host else PROBES[self.args.probe]
        radio = self.args.command.startswith("radio-")
        a = ["openocd", *self._probe, "-f", "target/nrf52.cfg" if radio else "target/stm32u5x.cfg"]
        if not self.host:  # bit-banged GPIO runs as fast as it can; no clock to set
            a += ["-c", f"adapter speed {self.args.khz}"]
        elif not radio:  # the target config's reset-start sets a clock, which GPIO adapters do not have
            a += ["-c", "$_TARGETNAME configure -event reset-start {}"]
        if self.args.under_reset:
            a += ["-c", "reset_config srst_only srst_nogate srst_open_drain connect_assert_srst"]
        return a

    def release_pins(self):
        """Shell to turn the probe board's SWD pins back into inputs.

        OpenOCD's GPIO drivers leave them as outputs when they exit (SWCLK
        high): between sessions that feeds the target through its SWD pin
        protection diodes, so unplugging the target's own supply does not take
        it down to a power-on reset. gpioget requests each line as an input
        (libgpiod v2 syntax first, then v1).
        """
        return "; ".join(f"(gpioget -c gpiochip{c} {o} || gpioget gpiochip{c} {o}) >/dev/null 2>&1"
                         for c, o in self._pins)

    def command(self, a):
        """An OpenOCD argv, run here or on the probe board (pins released after)."""
        if not self.host:
            return a
        return ["ssh", self.host, f"{shlex.join(a)}; rc=$?; {self.release_pins() or 'true'}; exit $rc"]

    def setup(self):
        if not self.host:
            die("setup is for a Linux board as the probe: it installs OpenOCD there")
        pins = " ".join(GPIO_PROBES[self.args.probe])
        p = subprocess.run(["ssh", self.host, "command -v openocd >/dev/null && command -v gpioinfo >/dev/null || "
                            "(sudo apt-get update && sudo apt-get install -y openocd gpiod) || exit 1; "
                            # BeagleBone: make sure the pins are in GPIO mode
                            f"if command -v config-pin >/dev/null; then for p in {pins}; do config-pin $p gpio; done; fi; "
                            "openocd --version 2>&1 | head -1; id -nG"])
        if p.returncode != 0:
            die(f"setup on {self.host} failed")
        v = self.ssh("openocd --version 2>&1 | head -1").stdout
        m = re.search(r"(\d+)\.(\d+)\.\d+", v)
        if not m or (int(m.group(1)), int(m.group(2))) < (0, 12):
            die(f"{self.host} has {v.strip() or 'no openocd'}: the STM32U5 and the GPIO pin syntax need "
                "OpenOCD 0.12 or newer (a newer OS image, or a source build with --enable-linuxgpiod)")
        if self.ssh("test -f /usr/share/openocd/scripts/target/stm32u5x.cfg || "
                    "test -f /usr/local/share/openocd/scripts/target/stm32u5x.cfg").returncode:
            die(f"{self.host}'s OpenOCD has no target/stm32u5x.cfg")
        print(f"probe pins: {' '.join(self.gpio_probe())}")

    def run(self, script, check=True):
        """One OpenOCD session; lines our Tcl echoes start with '@@'."""
        p = subprocess.run(self.command(self.base() + ["-c", script]), capture_output=True, text=True)
        log = p.stdout + p.stderr
        if check and p.returncode != 0:
            print(log, file=sys.stderr)
            die(f"openocd exited with {p.returncode} (log above)")
        return log, {m.group(1): m.group(2) for m in re.finditer(r"^@@ (\S+) (.*)$", log, re.M)}

    # --- info -------------------------------------------------------------
    def read_regs(self, regs):
        """regs: list of (name, address). Values come back as ints, or the error text."""
        lines = "\n".join(f"rd {n} {a:#010x}" for n, a in regs)
        # Secure bus access where TrustZone is on: a running bootloader or firmware makes
        # the flash's secure registers read 0 through non-secure accesses.
        script = f"""init
catch {{stm32x5x_ahb_ap_secure_access}}
proc rd {{name addr}} {{
  if {{[catch {{[target current] read_memory $addr 32 1}} v]}} {{
    echo "@@ $name ERR read failed [string map {{"\\n" " "}} $v]"
  }} else {{
    echo [format "@@ %s %08X" $name [lindex $v 0]]
  }}
}}
{lines}
shutdown"""
        log, got = self.run(script)
        vals = {}
        for n, _ in regs:
            v = got.get(n)
            if v is None:
                print(log, file=sys.stderr)
                die(f"no value for {n} in the OpenOCD log")
            vals[n] = int(v, 16) if not v.startswith("ERR") else v
        return vals

    def info(self):
        r = self.read_regs([
            ("idcode", DBGMCU_IDCODE), ("flashsize", FLASHSIZE),
            ("uid0", UID), ("uid1", UID + 4), ("uid2", UID + 8),
            ("optr", FLASH_R_NS + 0x40), ("nsboot0", FLASH_R_NS + 0x44),
            ("secboot0", FLASH_R_NS + 0x4C), ("secwm1r1", FLASH_R_NS + 0x50),
            ("secboot0_s", FLASH_R_NS + SECURE_OFFSET + 0x4C),
            ("secwm1r2", FLASH_R_NS + 0x54), ("wrp1a", FLASH_R_NS + 0x58),
            ("wrp1b", FLASH_R_NS + 0x5C),
        ])
        # The ID and option registers must read; a running firmware may lock others
        bad = {k: v for k, v in r.items() if isinstance(v, str) and k in ("idcode", "flashsize", "optr")}
        if bad:
            die(f"cannot read the chip's system registers: {bad}")

        def hx(k):
            return r[k] if isinstance(r[k], str) else f"{r[k]:#010x}"
        optr = r["optr"]
        tz = optr >> 31 & 1
        rdp = optr & 0xFF
        rdp_s = {0xAA: "0 (open)", 0x55: "0.5", 0xCC: "2 (SWD closed for good)"}.get(rdp, "1")
        dev = r["idcode"] & 0xFFF
        print(f"chip      DBGMCU_IDCODE {r['idcode']:#010x}  DEV_ID {dev:#05x}"
              f" ({'STM32U575/585' if dev == 0x482 else 'NOT a U575/585'})"
              f"  REV_ID {r['idcode'] >> 16:#06x}")
        uid = "".join(r[k] if isinstance(r[k], str) else f"{r[k]:08x}" for k in ("uid2", "uid1", "uid0"))
        print(f"          flash {r['flashsize'] & 0xFFFF} KB, UID {uid}")
        print(f"options   OPTR {optr:#010x}: RDP level {rdp_s}, BOR_LEV {optr >> 8 & 7}, "
              f"set: {' '.join(bits(optr, OPTR_BITS))}")
        # the secure alias where it reads (TrustZone on), else the non-secure one
        sb = r["secboot0_s"] if isinstance(r["secboot0_s"], int) and r["secboot0_s"] else r["secboot0"]
        print(f"          SECBOOTADD0R {sb:#010x}: secure boot address {sb & 0xFFFFFF80:#010x}"
              f"{', BOOT_LOCK' if sb & 1 else ''}   NSBOOTADD0R {hx('nsboot0')}")
        print(f"          SECWM1R1 {hx('secwm1r1')} SECWM1R2 {hx('secwm1r2')} "
              f"WRP1AR {hx('wrp1a')} WRP1BR {hx('wrp1b')}")
        if not tz:
            print("TrustZone is OFF: run `firmware/swd.py trustzone` before flashing.")
            return
        if sb & 0xFFFFFF80 != BOARDLOADER_START:
            print(f"SECBOOTADD0 is not the boardloader ({BOARDLOADER_START:#010x}): "
                  "run `firmware/swd.py trustzone`.")

        s = SECURE_OFFSET
        r2 = self.read_regs([
            ("bl_sp", BOARDLOADER_START), ("bl_reset", BOARDLOADER_START + 4),
            ("bld", BOOTLOADER_START), ("fw", FIRMWARE_START),
            ("bootcmd", BOOTARGS_START), ("csr", RCC_NS + s + 0xF4),
            ("ahb2enr1", RCC_NS + s + 0x8C),
            ("pa_moder", GPIOA_NS + s), ("pa_pupdr", GPIOA_NS + s + 0x0C),
            ("pa_idr", GPIOA_NS + s + 0x10),
            ("pe_moder", GPIOE_NS + s), ("pe_idr", GPIOE_NS + s + 0x10),
            ("pe_odr", GPIOE_NS + s + 0x14),
        ])

        def word(n):
            v = r2[n]
            return v if isinstance(v, str) else f"{v:#010x}"

        bl = "erased" if r2["bl_sp"] == 0xFFFFFFFF else \
            f"vector table (SP {word('bl_sp')}, reset {word('bl_reset')})"
        print(f"flash     boardloader @{BOARDLOADER_START:#010x}: {bl}")
        for n, a in (("bld", BOOTLOADER_START), ("fw", FIRMWARE_START)):
            v = r2[n]
            label = "bootloader" if n == "bld" else "firmware  "
            print(f"          {label}  @{a:#010x}: {word(n)} {MAGIC.get(v, '') if isinstance(v, int) else ''}")
        cmd = r2["bootcmd"]
        if isinstance(cmd, int):
            print(f"boot cmd  {cmd:#010x} {BOOT_COMMANDS.get(cmd, '(not a boot command: RAM not set by a stage)')}"
                  " (the bootloader clears it on entry: `state` reads its saved copy and any error)")
        csr = r2["csr"]
        if isinstance(csr, int):
            print(f"reset     RCC_CSR {csr:#010x}: last reset by {', '.join(bits(csr, CSR_RESET_BITS)) or '?'}")

        en = r2["ahb2enr1"]
        if not isinstance(en, int) or not (en & 1 and en >> 4 & 1):
            print(f"pins      GPIOA/GPIOE clocks off (RCC_AHB2ENR1 {word('ahb2enr1')}): no pin readout")
            return
        pa, pe = r2["pa_idr"], r2["pe_idr"]
        hold_out = (r2["pe_moder"] >> 10 & 3) == 1
        print(f"pins      PWR_HOLD (PE5) {'output ' + str(r2['pe_odr'] >> 5 & 1) if hold_out else 'NOT an output'}"
              f"   -> the power latch {'holds' if hold_out and r2['pe_odr'] >> 5 & 1 else 'is NOT held'}")
        def pin(idr, moder, n, label, meaning):
            # U5 pins reset to analog mode, where IDR reads 0 whatever the pin carries
            mode = moder >> 2 * n & 3
            if mode == 3:
                print(f"          {label} not read: still in analog mode (no stage set it up yet)")
            else:
                print(f"          {label} {idr >> n & 1}  ({meaning})")
        pin(pa, r2["pa_moder"], 9, "VBUS_SENSE (PA9)", "1 = USB power present")
        pin(pe, r2["pe_moder"], 7, "AIRGAP_SENSE (PE7)", "1 = airgapped: USB data and radio off")
        pull = r2["pa_pupdr"] & 3
        pin(pa, r2["pa_moder"], 0, "BTN_N (PA0)", "0 = button pressed"
            + ("" if pull == 1 else "; no pull-up set on PA0, so a released button floats"))

    # --- trustzone --------------------------------------------------------
    def trustzone(self):
        r = self.read_regs([("optr", FLASH_R_NS + 0x40), ("secboot0", FLASH_R_NS + 0x4C)])
        optr, sb = r["optr"], r["secboot0"]
        if isinstance(optr, str) or isinstance(sb, str):
            die(f"cannot read the option registers: {optr} {sb}")
        if optr & 0xFF != 0xAA:
            die(f"RDP is not level 0 (OPTR {optr:#010x}): TZEN can only be set at level 0")
        if not optr >> 31 & 1:
            print("setting TZEN = 1 (the chip reloads its option bytes and resets)")
            log, _ = self.run("init; reset halt; stm32l4x trustzone 0 enable; shutdown", check=False)
            print("\n".join(l for l in log.splitlines() if "TZEN" in l or "rror" in l))
            time.sleep(0.5)
        if sb & 0xFFFFFF80 != BOARDLOADER_START:
            print(f"setting SECBOOTADD0 = {BOARDLOADER_START:#010x}")
            log, _ = self.run(
                f"init; reset halt; stm32l4x option_write 0 0x4C {BOARDLOADER_START:#x} 0xFFFFFF80;"
                " stm32l4x option_load 0; shutdown", check=False)
            print("\n".join(l for l in log.splitlines() if "rror" in l))
            time.sleep(0.5)
        r = self.read_regs([("optr", FLASH_R_NS + 0x40), ("secboot0", FLASH_R_NS + 0x4C)])
        ok = isinstance(r["optr"], int) and r["optr"] >> 31 & 1 and \
            isinstance(r["secboot0"], int) and r["secboot0"] & 0xFFFFFF80 == BOARDLOADER_START
        print(f"now OPTR {r['optr']:#010x} SECBOOTADD0R {r['secboot0']:#010x}: "
              + ("TrustZone on, boots the boardloader" if ok else "NOT as wanted"))
        if not ok:
            die("set TZEN = 1 and SECBOOTADD0 = 0x0C004000 with STM32CubeProgrammer "
                "(trezor-firmware/docs/core/misc/trustzone.md), then rerun `info`")

    # --- flash ------------------------------------------------------------
    def flash(self, image, at=BOARDLOADER_START, start=None, wait=0):
        image = Path(image or self.out / "combined-firmware.bin").resolve()
        if not image.is_file():
            die(f"no image at {image}: build with firmware/docker-build.sh")
        # --wait: connect during the boot window instead (firmware that leaves SWD
        # unreadable has not started yet); the session's first step halts the core
        r = {"optr": 1 << 31, "secboot0": BOARDLOADER_START} if wait else \
            self.read_regs([("optr", FLASH_R_NS + 0x40), ("secboot0", FLASH_R_NS + SECURE_OFFSET + 0x4C)])
        if isinstance(r["optr"], str) or isinstance(r["secboot0"], str):
            die(f"cannot read the option registers: {r}")
        if not r["optr"] >> 31 & 1 or r["secboot0"] & 0xFFFFFF80 != BOARDLOADER_START:
            die(f"TrustZone is off or the chip does not boot the boardloader (OPTR {r['optr']:#010x}, "
                f"SECBOOTADD0R {r['secboot0']:#010x}): run `firmware/swd.py trustzone` first")
        data = image.read_bytes()
        # Verified pieces with retries: one glitch on a bit-banged link costs a piece, not
        # the run. Pieces are whole flash sectors (8 KB), so an erased one is just erased.
        work = Path(tempfile.mkdtemp(prefix="swd-chunks-"))
        steps = []
        for off in range(0, len(data), CHUNK):
            piece, addr = data[off:off + CHUNK], at + off
            if start is not None and addr < start:
                continue  # --from: already written
            if piece.count(0xFF) == len(piece):
                steps.append(f"wipe {addr:#x}")
            else:
                (work / f"{addr:08x}.bin").write_bytes(piece)
                steps.append(f"put {{{CHUNK_DIR if self.host else work}/{addr:08x}.bin}} {addr:#x}")
        if self.host:
            self.ssh(f"rm -rf {CHUNK_DIR}")
            if subprocess.run(["scp", "-q", "-r", str(work), f"{self.host}:{CHUNK_DIR}"]).returncode:
                die(f"could not copy the image to {self.host}")
        shutil.rmtree(work)
        print(f"writing {image.name} ({len(data)} bytes) at {at:#010x}: {len(steps)} pieces of "
              f"{CHUNK // 1024} KB")
        t = time.monotonic()
        sec = [FLASH_R_NS + SECURE_OFFSET + o for o in (0x80, 0x84, 0x88, 0x8C, 0xA0, 0xA4, 0xA8, 0xAC)]
        script = f"""init
# A core held in reset does not drive PWR_HOLD, so the board's latch drops ~1 s after
# the reset: set the pin from here at once. Then secure bus access and a secure RAM
# buffer (verify_image's checksum runs there).
#
# Flash is programmed by hand through the secure registers. After a reset only the
# watermarked pages (secret + boardloader) are secure; marking every page secure, as the
# boardloader does at run time (tz_configure_flash; a reset clears it), makes one path
# for all of them. OpenOCD 0.12's own stm32l4x write programmed zeros on this chip, and
# its non-secure bank drove the secure registers for non-secure pages (silently ignored).
proc rd {{a}} {{ return [lindex [[target current] read_memory $a 32 1] 0] }}
proc prep {{}} {{
  reset halt
  mmw {RCC_NS + SECURE_OFFSET + 0x8C:#x} {1 << HOLD_PORT_BIT:#x} 0
  mww {HOLD_GPIO + 0x18:#x} {1 << HOLD_PIN:#x}
  mmw {HOLD_GPIO:#x} {1 << 2 * HOLD_PIN:#x} {2 << 2 * HOLD_PIN:#x}
  stm32x5x_ahb_ap_secure_access
  [target current] configure -work-area-phys {BOOTARGS_START:#x} -work-area-size 0x4000
  foreach a {{{" ".join(f"{a:#x}" for a in sec)}}} {{ mww $a 0xFFFFFFFF }}
  # unlock once: a second key sequence on an unlocked register locks it until reset
  if {{[rd {SECCR:#x}] & 0x80000000}} {{ mww {SECKEYR:#x} 0x45670123; mww {SECKEYR:#x} 0xCDEF89AB }}
}}
proc swait {{}} {{
  for {{set i 0}} {{$i < 5000}} {{incr i}} {{ if {{([rd {SECSR:#x}] & 0x10000) == 0}} return }}
  error "flash busy"
}}
proc serr {{what}} {{
  set sr [rd {SECSR:#x}]
  if {{$sr & 0xFA}} {{ error [format "%s: SECSR %08X" $what $sr] }}
}}
proc serase {{a}} {{
  set page [expr {{($a - {FLASH_BASE_S:#x}) / {SECTOR:#x}}}]
  set cr [expr {{0x2 | (($page % {BANK_PAGES}) << 3) | ($page >= {BANK_PAGES} ? 0x800 : 0)}}]
  mww {SECSR:#x} 0xFFFFFFFF
  mww {SECCR:#x} $cr
  mww {SECCR:#x} [expr {{$cr | 0x10000}}]
  swait
  serr erase
}}
proc sprog {{f a}} {{
  mww {SECSR:#x} 0xFFFFFFFF
  mww {SECCR:#x} 0x1
  load_image $f $a bin
  swait
  mww {SECCR:#x} 0
  serr program
}}
proc attempt {{what cmd}} {{
  for {{set i 0}} {{$i < 4}} {{incr i}} {{
    if {{[catch {{uplevel 1 $cmd}} e] == 0}} {{ echo "@@ ok $what"; return }}
    echo "@@ retry $what [string map {{"\n" " "}} $e]"
    catch {{prep}}
  }}
  error "$what failed 4 times"
}}
# Resuming: pieces that already match are skipped, up to the first that does not (where
# an interrupted run stopped); from there on every piece is written.
set writing 0
proc put {{f a}} {{
  global writing
  if {{!$writing}} {{
    if {{[catch {{verify_image $f $a}}] == 0}} {{ echo "@@ ok $a"; echo "@@ same $a"; return }}
    set writing 1
  }}
  attempt $a "serase $a; sprog $f $a; verify_image $f $a"
}}
proc wipe {{a}} {{ attempt $a "serase $a" }}
prep
{chr(10).join(steps)}
reset run
shutdown"""
        deadline = time.monotonic() + wait
        while True:
            log, _ = self.run(script, check=False)
            if "DPIDR 0x0be12477" in log:
                print("  connected: flashing")
                break
            if time.monotonic() > deadline:
                break
            print("  no clean SWD connection yet, retrying")
        same = len(re.findall(r"^@@ same ", log, re.M))
        if same:
            print(f"  {same} pieces already on the chip, skipped")
        retries = re.findall(r"^@@ retry (\S+) (.*)$", log, re.M)
        done = len(re.findall(r"^@@ ok ", log, re.M))
        for a, why in retries:
            print(f"  retried {a}: {why[:120]}")
        if done != len(steps):
            print(log[-3000:], file=sys.stderr)
            die(f"{done} of {len(steps)} pieces written (log above)")
        print(f"written and verified in {time.monotonic() - t:.0f} s ({len(retries)} retries); the chip "
              "is running. First boot: the boardloader programs the rest of the option bytes and restarts.")

    # --- radio (nRF52840 module, on its own SWD pads) -----------------------
    # The radio's supply is on only while the airgap switch is off AIR and the
    # firmware allows it ([ambire_radio] or the BLE driver): check the RADIO light.
    def radio_info(self):
        log, got = self.run("""init
echo "@@ lock [expr {[catch {[dap names] apreg 1 0xc} v] ? {?} : $v}]"
catch {nrf52_check_ap_lock}
if {[catch {set part [lindex [[target current] read_memory 0x10000100 32 1] 0]} e]} {
  echo "@@ part ERR"
} else {
  echo [format "@@ part %08X" $part]
  echo [format "@@ variant %08X" [lindex [[target current] read_memory 0x10000104 32 1] 0]]
  echo [format "@@ flash0 %08X" [lindex [[target current] read_memory 0x00000000 32 1] 0]]
}
shutdown""", check=False)
        lock = got.get("lock", "?")
        print(f"CTRL-AP APPROTECTSTATUS: {lock} (1 = debug access open, 0 = locked: run radio-recover)")
        part = got.get("part")
        if part is None or part.startswith("ERR"):
            print(log[-1500:], file=sys.stderr)
            die("could not read the radio's FICR: no power (RADIO light off?), wiring, or APPROTECT")
        variant = int(got["variant"], 16).to_bytes(4, "big").decode("ascii", "replace")
        print(f"part nRF{part.lstrip('0')}  variant {variant}  first flash word {got['flash0']}"
              f" ({'erased' if got['flash0'] == 'FFFFFFFF' else 'programmed'})")

    def radio_recover(self):
        log, _ = self.run("init; nrf52_recover; shutdown", check=False)
        print("\n".join(l for l in log.splitlines() if "nRF52" in l or "rror" in l or "erase" in l.lower()))

    def radio_flash(self, image):
        image = Path(image or "").resolve()
        if not image.is_file():
            die(f"no radio image at {image}")
        target = image
        if self.host:
            remote = f"/tmp/ambire-radio{image.suffix}"
            if subprocess.run(["scp", "-q", str(image), f"{self.host}:{remote}"]).returncode:
                die(f"could not copy the image to {self.host}")
            target = remote
        t = time.monotonic()
        log, _ = self.run(f"init; reset halt; program {{{target}}} verify; reset run; shutdown", check=False)
        if "Verified OK" not in log and "verified" not in log.lower():
            print(log[-2500:], file=sys.stderr)
            die("radio flash not verified (log above)")
        print(f"radio flashed and verified in {time.monotonic() - t:.0f} s")

    # --- state / gdb --------------------------------------------------------
    def elfs(self):
        found = [self.out / f"{s}.elf" for s in STAGES if (self.out / f"{s}.elf").is_file()]
        if not found:
            die(f"no ELF files in {self.out}: build with firmware/docker-build.sh")
        return found

    def server(self):
        """OpenOCD as a gdb server on :3333, in its own process group."""
        a = self.base() + (["-c", "bindto 0.0.0.0"] if self.host else []) + ["-c", "init"]
        p = subprocess.Popen(self.command(a), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=True)
        deadline = time.monotonic() + 10
        log = []
        while time.monotonic() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            log.append(line)
            if "Listening on port 3333" in line:
                return p
        self.stop(p)
        print("".join(log), file=sys.stderr)
        die("OpenOCD did not start a gdb server (log above)")

    def stop(self, p):
        os.killpg(p.pid, signal.SIGTERM)
        p.wait()
        if self.host:  # ssh going away does not reliably take the remote OpenOCD with it
            self.ssh(f"pkill -f '^openocd .*bindto 0.0.0.0'; sleep 0.3; {self.release_pins() or 'true'}")

    def gdb_target(self):
        return f"target extended-remote {self.host.split('@')[-1] if self.host else 'localhost'}:3333"

    def stage_for(self, addr):
        for elf in self.elfs():
            out = subprocess.run(["arm-none-eabi-gdb", "-nx", "-batch", "-ex",
                                  f"info symbol {addr:#x}", str(elf)],
                                 capture_output=True, text=True).stdout
            if out.strip() and "No symbol matches" not in out:
                return elf, out.strip()
        return None, "no stage's symbols cover it"

    def gdb_batch(self, elf, cmds):
        a = ["arm-none-eabi-gdb", "-nx", "-batch", "-ex", "set pagination off",
             "-ex", "set print pretty on", "-ex", self.gdb_target()]
        for c in cmds:
            a += ["-ex", c]
        return subprocess.run(a + ([str(elf)] if elf else []), capture_output=True,
                              text=True).stdout

    def state(self, keep_halted):
        # One OpenOCD session halts the core and reads everything, and leaves it halted, so
        # the gdb sessions below (symbols, backtrace) see the same PC.
        regs = [("cfsr", 0xE000ED28), ("hfsr", 0xE000ED2C), ("mmfar", 0xE000ED34),
                ("bfar", 0xE000ED38), ("sfsr", 0xE000EDE4), ("sfar", 0xE000EDE8),
                ("cfsr_ns", 0xE002ED28), ("hfsr_ns", 0xE002ED2C), ("cmd", BOOTARGS_START)]
        reads = "\n".join(f'echo [format "@@ {n} %08X" [lindex [[target current] read_memory {a:#x} 32 1] 0]]'
                          for n, a in regs)
        log, got = self.run("init; halt\n"
                            + "\n".join(f'echo "@@ {r} [lindex [reg {r}] 2]"'
                                        for r in ("pc", "lr", "sp", "xPSR"))
                            + f"\n{reads}\nshutdown")
        try:
            v = {k: int(got[k], 16) for k in ["pc", "lr", "sp", "xPSR"] + [n for n, _ in regs]}
        except (KeyError, ValueError) as e:
            print(log, file=sys.stderr)
            die(f"could not read the core state ({e!r}; OpenOCD log above)")
        pc, lr, sp, xpsr = v["pc"], v["lr"], v["sp"], v["xPSR"]
        cfsr, hfsr, mmfar, bfar, sfsr, sfar, cfsr_ns, hfsr_ns, cmd = (v[n] for n, _ in regs)
        srv = self.server()
        try:
            elf, sym = self.stage_for(pc)
            print(f"pc {pc:#010x}  in {elf.stem if elf else '?'}: {sym}")
            print(f"lr {lr:#010x}  sp {sp:#010x}  xPSR {xpsr:#010x} (exception {xpsr & 0x1FF})")
            for name, val, names in (("CFSR (secure)", cfsr, CFSR_BITS), ("HFSR (secure)", hfsr, HFSR_BITS),
                                     ("CFSR (non-secure)", cfsr_ns, CFSR_BITS),
                                     ("HFSR (non-secure)", hfsr_ns, HFSR_BITS), ("SFSR", sfsr, SFSR_BITS)):
                if val:
                    print(f"{name} {val:#010x}: {' '.join(bits(val, names))}")
            if cfsr & 1 << 7:
                print(f"MMFAR {mmfar:#010x}")
            if cfsr & 1 << 15:
                print(f"BFAR {bfar:#010x}")
            if sfsr & 1 << 6:
                print(f"SFAR {sfar:#010x}")
            if elf:
                bt = self.gdb_batch(elf, ["bt 25"])
                print("backtrace:\n" + "\n".join(l for l in bt.splitlines() if l.startswith("#")))
            if elf and elf.stem == "bootloader":
                # The bootloader clears the command in RAM on entry and keeps a copy.
                out = self.gdb_batch(elf, ["p/x g_boot_command_saved"])
                m = re.search(r"\$1 = (0x[0-9a-f]+)", out)
                if not m:
                    print(out, file=sys.stderr)
                    die("could not read g_boot_command_saved (gdb output above)")
                cmd = int(m.group(1), 16)
                print(f"boot command this bootloader run started with: {cmd:#010x} {BOOT_COMMANDS.get(cmd, '')}")
            else:
                print(f"boot command for the next boot: {cmd:#010x} {BOOT_COMMANDS.get(cmd, '')}")
            if cmd == 0x7CD945A0:
                pm = self.gdb_batch(self.out / "bootloader.elf",
                                    ["p 'bootutils.c'::g_boot_args.pminfo"])
                print("RSOD info (bootargs):\n" + pm[pm.find("$1"):].rstrip())
                # The record is a union: its fault registers mean something only for a fault
                is_fault = "TASK_TERM_REASON_FAULT" in pm
                for k, names in (("cfsr", CFSR_BITS), ("hfsr", HFSR_BITS), ("sfsr", SFSR_BITS)):
                    m = re.search(rf"\b{k} = (\d+)", pm)
                    if is_fault and m and int(m.group(1)):
                        print(f"  {k} {int(m.group(1)):#010x}: {' '.join(bits(int(m.group(1)), names))}")
                m = re.search(r"\bpc = (\d+)", pm)
                if is_fault and m and int(m.group(1)):
                    e2, s2 = self.stage_for(int(m.group(1)))
                    print(f"  faulting pc {int(m.group(1)):#010x} in {e2.stem if e2 else '?'}: {s2}")
        finally:
            self.stop(srv)
        # gdb's detach may already have resumed the core; resuming a running core is not an error
        self.run("init; halt; shutdown" if keep_halted else
                 "init; if {[[target current] curstate] eq \"halted\"} {resume}; shutdown")
        print("(left halted)" if keep_halted else "(resumed)")

    def gdb(self):
        srv = self.server()
        elfs = self.elfs()
        try:
            a = ["arm-none-eabi-gdb", "-nx", "-ex", "set confirm off", "-ex",
                 self.gdb_target(), "-ex", "monitor halt"]
            for e in elfs[1:]:
                a += ["-ex", f"add-symbol-file {e}"]
            subprocess.run(a + [str(elfs[0])])
        finally:
            self.stop(srv)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["info", "trustzone", "flash", "state", "gdb", "reset", "setup",
                                        "radio-info", "radio-recover", "radio-flash"])
    ap.add_argument("image", nargs="?")
    ap.add_argument("--probe", default=os.environ.get("SWD_PROBE", "stlink"), choices=[*PROBES, *GPIO_PROBES])
    ap.add_argument("--khz", type=int, default=1000)
    ap.add_argument("--host", default=os.environ.get("SWD_HOST"), help="--probe rpi: the Pi, user@host")
    ap.add_argument("--under-reset", action="store_true")
    ap.add_argument("--board", default="ambire_p0")
    ap.add_argument("--halt", action="store_true", help="state: leave the core halted")
    ap.add_argument("--at", type=lambda v: int(v, 0), default=BOARDLOADER_START,
                    help="flash: address to write the image at (default: the boardloader's)")
    ap.add_argument("--wait", type=int, default=0, metavar="S",
                    help="flash: keep retrying the connection for S seconds (restart the board meanwhile)")
    ap.add_argument("--from", dest="start", type=lambda v: int(v, 0),
                    help="flash: resume, skipping the pieces below this address")
    args = ap.parse_args()
    swd = Swd(args)
    if args.command == "setup":
        swd.setup()
    elif args.command == "info":
        swd.info()
    elif args.command == "trustzone":
        swd.trustzone()
    elif args.command == "flash":
        swd.flash(args.image, args.at, args.start, args.wait)
    elif args.command == "state":
        swd.state(args.halt)
    elif args.command == "gdb":
        swd.gdb()
    elif args.command == "reset":
        swd.run("init; reset run; shutdown")
        print("reset")
    elif args.command == "radio-info":
        swd.radio_info()
    elif args.command == "radio-recover":
        swd.radio_recover()
    elif args.command == "radio-flash":
        swd.radio_flash(args.image)


if __name__ == "__main__":
    main()
