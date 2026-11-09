#!/usr/bin/env python3
"""Generates io/camera/stm32u5/camera_sensors.h (the camera sensors' start-up
register tables) from Espressif's esp32-camera, whose OV5640 and GC2145
drivers the firmware's zoom modes follow.

    git clone --depth 1 https://github.com/espressif/esp32-camera /tmp/esp32-camera
    python3 -I firmware/bringup/camera/gen_sensor_tables.py \
        /tmp/esp32-camera/sensors/private_include \
        firmware/trezor-firmware/core/embed/io/camera/stm32u5/camera_sensors.h
    # then put the esp32-camera commit in the header's first comment (COMMIT)
"""
import re, sys
inc = sys.argv[1]
out = sys.argv[2]
names = {}
for line in open(f"{inc}/ov5640_regs.h"):
    m = re.match(r"#define\s+(\w+)\s+(0x[0-9a-fA-F]+)", line)
    if m: names[m.group(1)] = int(m.group(2), 16)
names["REG_DLY"] = 0xffff
names["REGLIST_TAIL"] = 0x0000

def table(path, name):
    src = open(path).read()
    m = re.search(r"\b" + name + r"\[\]\[2\]\s*=\s*\{(.*?)\n\};", src, re.S)
    body = m.group(1)
    rows = []
    for line in body.split("\n"):
        code, _, comment = line.partition("//")
        for a, b in re.findall(r"\{\s*([\w]+)\s*,\s*([\w]+)\s*\}", code):
            va = names[a] if a in names else int(a, 0)
            vb = names[b] if b in names else int(b, 0)
            rows.append((va, vb, comment.strip()))
    return rows

def emit(f, cname, rows, width):
    f.write(f"static const uint16_t {cname}[][2] = {{\n")
    for a, b, c in rows:
        if a == 0xffff:
            s = f"    {{CAM_REG_DELAY, {b}}},"
        elif a == 0 and b == 0:
            s = "    {CAM_REG_END, 0x00},"
        else:
            s = f"    {{0x{a:0{width}x}, 0x{b:02x}}},"
        if c:
            s += f"  // {c}"
        f.write(s + "\n")
    f.write("};\n\n")

ov = table(f"{inc}/ov5640_settings.h", "sensor_default_regs")
gc = table(f"{inc}/gc2145_settings.h", "gc2145_default_init_regs")
assert ov[-1][:2] == (0, 0) and gc[-1][:2] == (0, 0)
assert all(a != 0 for a, b, _ in ov[:-1]), "OV5640 table writes reg 0"
assert all(a != 0 for a, b, _ in gc[:-1]), "GC2145 table writes reg 0"
with open(out, "w") as f:
    f.write("""/*
 * Ambire boards, [ambire_camera]: start-up register tables for the camera
 * sensors, generated from espressif/esp32-camera (sensors/private_include/
 * ov5640_settings.h sensor_default_regs, gc2145_settings.h
 * gc2145_default_init_regs, commit COMMIT), symbolic register names replaced
 * by their values. Do not edit by hand.
 *
 * Copyright 2015-2021 Espressif Systems (Shanghai) PTE LTD
 * Licensed under the Apache License, Version 2.0: http://www.apache.org/licenses/LICENSE-2.0
 */

#pragma once

#include <trezor_types.h>

// Table markers (no sensor register sits at either address in these tables)
#define CAM_REG_DELAY 0xffff  // value: milliseconds to wait
#define CAM_REG_END 0x0000

// OV5640 (16-bit register addresses): reset, defaults, power on
""")
    emit(f, "ov5640_init_regs", ov, 4)
    f.write("// GC2145 (8-bit register addresses, 0xfe selects the page)\n")
    emit(f, "gc2145_init_regs", gc, 2)
print(len(ov), len(gc))
