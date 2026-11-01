#!/usr/bin/env python3
"""The programming jig: one board, every part on one face, placed by script.

    KICAD_PY jig/makeJig.py          # writes jig/jig.kicad_pcb: placed, planes drawn, not routed
    KICAD_PY jig/makeJig.py --check  # only: do the device board's pads and holes still match the jig?

(from hardware/ambire-hw). It is then routed by humanizedKicadAutoRouter (jig/README.md,
"Making the board"). It overwrites the board.

Every programming pad of the device board is on its front (TP_3V3, TP_GND, TP_SWDIO,
TP_SWCLK, TP_NRST for the MCU; TP_RSWDIO, TP_RSWCLK for the radio). The jig lies on the
table with its parts face up; the device goes on it face down, three M2 screws through
its holes H1, H2, H4 locate it, three 5 mm standoffs carry it, three nuts on its back
hold it down. Seven spring pins, each working at its 5.0 mm, press up into the pads.

Its parts are on KiCad's B side, so the board is drawn in the DEVICE board's own
coordinates (x east, y south, seen from the device's front): a pin sits at its pad's
coordinates with no transform. Under the device go only parts lower than the 5 mm gap
(the device's front is bare under its display); the joystick, taller than the gap, gets a
cut-out. The USB-C, the switches and the lights are on a strip west of the device, where
they stay in reach and in sight.

The probe is an RP2040 wired as Raspberry Pi's debugprobe firmware is built for the Pico
(debugprobe_on_pico.uf2: SWCLK GP2, SWDIO GP3, reset GP1, LED GP25): a CMSIS-DAP probe on
USB-C, which firmware/swd.py drives with --probe cmsis-dap. A second regulator powers the
device through TP_3V3 (switch TARGET 3V3); a selector sends SWD to the MCU's pins or the
radio's.
"""
import json
import math
import os
import subprocess
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "jig.kicad_pcb")
PRO = os.path.join(HERE, "jig.kicad_pro")
KICAD_FP = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
JIG_LIB = os.path.join(HERE, "jig.pretty")
WALLET_LIB = os.path.join(ROOT, "board", "wallet.pretty")
WALLET_PRO = os.path.join(ROOT, "board", "wallet.kicad_pro")
M, mm = pcbnew.ToMM, pcbnew.FromMM
pt = lambda x, y: pcbnew.VECTOR2I(mm(x), mm(y))

# ── the device board: where the jig lands (board/wallet.kicad_pcb, 2026-11-01) ──
PADS = {"TP_3V3": (4.00, 15.00), "TP_SWDIO": (4.14, 17.68), "TP_SWCLK": (4.00, 20.28),
        "TP_GND": (1.29, 20.74), "TP_NRST": (33.60, 26.80),
        "TP_RSWDIO": (2.04, 78.90), "TP_RSWCLK": (2.00, 81.50)}
HOLES = {"H1": (3.20, 3.20), "H2": (44.80, 3.20), "H4": (44.80, 83.80)}   # 2.2 mm, plated
DEVICE_BOARD = os.path.join(ROOT, "board", "wallet.kicad_pcb")
PIN_PITCH_MIN = 2.5      # the spring pins' 2.0 mm flanges, side by side, with JLC's placement play
DEVICE_OUTLINE = (0.0, 0.0, 48.0, 87.0, 4.0)
JOYSTICK_CUT = (19.0, 75.8, 29.0, 85.8, 1.0)   # the ALPS joystick (19.5-28.5 x 76.3-85.3), taller than the gap
EDGE_X = -13.0                                  # the jig's west edge: the strip west of the device
OUTLINE = (EDGE_X, -3.0, 49.0, 90.0, 2.0)

# JLC parts: value, footprint (lib, name), LCSC, MPN
P = {
    "C100n": ("100nF", (f"{KICAD_FP}/Capacitor_SMD.pretty", "C_0402_1005Metric"), "C1525", "CL05B104KO5NNNC"),
    "C1u": ("1uF", (f"{KICAD_FP}/Capacitor_SMD.pretty", "C_0402_1005Metric"), "C52923", "CL05A105KA5NQNC"),
    "C10u": ("10uF", (f"{KICAD_FP}/Capacitor_SMD.pretty", "C_0603_1608Metric"), "C19702", "CL10A106KP8NNNC"),
    "C15p": ("15pF", (f"{KICAD_FP}/Capacitor_SMD.pretty", "C_0402_1005Metric"), "C1548", "0402CG150J500NT"),
    "R27": ("27", (f"{KICAD_FP}/Resistor_SMD.pretty", "R_0402_1005Metric"), "C25100", "0402WGF270JTCE"),
    "R100": ("100", (f"{KICAD_FP}/Resistor_SMD.pretty", "R_0402_1005Metric"), "C25076", "0402WGF1000TCE"),
    "R1k": ("1k", (f"{KICAD_FP}/Resistor_SMD.pretty", "R_0402_1005Metric"), "C11702", "0402WGF1001TCE"),
    "R5k1": ("5.1k", (f"{KICAD_FP}/Resistor_SMD.pretty", "R_0402_1005Metric"), "C25905", "0402WGF5101TCE"),
    "R10k": ("10k", (f"{KICAD_FP}/Resistor_SMD.pretty", "R_0402_1005Metric"), "C25744", "0402WGF1002TCE"),
    "R100k": ("100k", (f"{KICAD_FP}/Resistor_SMD.pretty", "R_0402_1005Metric"), "C25741", "0402WGF1003TCE"),
    "LEDG": ("green", (f"{KICAD_FP}/LED_SMD.pretty", "LED_0603_1608Metric"), "C12624", "KT-0603G"),
    "LEDR": ("red", (f"{KICAD_FP}/LED_SMD.pretty", "LED_0603_1608Metric"), "C2286", "KT-0603R"),
    "RP2040": ("RP2040", (f"{KICAD_FP}/Package_DFN_QFN.pretty", "QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm"), "C2040", "RP2040"),
    "FLASH": ("W25Q16JVSSIQ", (f"{KICAD_FP}/Package_SO.pretty", "SOIC-8_5.3x5.3mm_P1.27mm"), "C131025", "W25Q16JVSSIQ"),
    "XTAL": ("12MHz", (f"{KICAD_FP}/Crystal.pretty", "Crystal_SMD_3225-4Pin_3.2x2.5mm"), "C20625731", "ABM8-272-T3"),
    "LDO": ("ME6211C33", (f"{KICAD_FP}/Package_TO_SOT_SMD.pretty", "SOT-23-5"), "C82942", "ME6211C33M5G-N"),
    "ESD": ("USBLC6-2SC6", (f"{KICAD_FP}/Package_TO_SOT_SMD.pretty", "SOT-23-6"), "C7519", "USBLC6-2SC6"),
    "USBC": ("TYPE-C-31-M-12", (WALLET_LIB, "USB_C_Receptacle_HRO_TYPE-C-31-M-12_BoardEdge"), "C165948", "TYPE-C-31-M-12"),
    "SPDT": ("MSK12C02", (f"{KICAD_FP}/Button_Switch_SMD.pretty", "SW_SPDT_Shouhan_MSK12C02"), "C431540", "MSK12C02"),
    "DPDT": ("K3-2235S-K1", (JIG_LIB, "SW_DPDT_Hroparts_K3-2235S-K1"), "C145845", "K3-2235S-K1"),
    "POGO": ("YZP0063-20068-01", (JIG_LIB, "Xinyangze_YZP0063-20068-01"), "C5157248", "YZP0063-20068-01"),
}
NOT_ASSEMBLED = {
    "HOLE": (JIG_LIB, "Jig_Hole_M2_NPTH"),
    "BOOT": (f"{KICAD_FP}/Jumper.pretty", "SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm"),
}

# RP2040 (QFN-56, KiCad's MCU_RaspberryPi symbol): pin -> net. Unlisted pins stay unconnected
# (GPIO0, GPIO4-24 but 25, GPIO26-29, the RP2040's own SWD port).
RP_PINS = {"1": "V3V3_PROBE", "10": "V3V3_PROBE", "22": "V3V3_PROBE", "33": "V3V3_PROBE", "42": "V3V3_PROBE",
           "49": "V3V3_PROBE", "43": "V3V3_PROBE", "44": "V3V3_PROBE", "48": "V3V3_PROBE",
           "23": "V1V1", "50": "V1V1", "45": "V1V1",
           "19": "GND", "57": "GND",
           "20": "XIN", "21": "XOUT",
           "26": "RUN",
           "46": "USBR_DN", "47": "USBR_DP",
           "51": "QSPI_SD3", "52": "QSPI_SCLK", "53": "QSPI_SD0", "54": "QSPI_SD2", "55": "QSPI_SD1", "56": "QSPI_SS",
           "3": "PRB_NRST", "4": "PRB_SWCLK", "5": "PRB_SWDIO",   # debugprobe on Pico: reset GP1, SWCLK GP2, SWDIO GP3
           "37": "LED_USB"}                                         # GP25: "USB connected"

# The probe's core, laid out around the RP2040 as for a top-side board (pin 1 north-west):
# (ref, part, dx, dy, rotation, {pad: net}). On the B side it is mirrored north-south, so
# the SWD pins (GP1-3) still face west, toward the pins and the selector.
CORE_AT = (14.0, 44.0)
CORE = [
    ("U1", "RP2040", 0.0, 0.0, 0, RP_PINS),
    # flash by the QSPI pins (W25Q16JV SOIC-8: 1 /CS, 2 DO, 3 /WP, 4 GND, 5 DI, 6 CLK, 7 /HOLD, 8 VCC)
    ("U2", "FLASH", -9.0, -10.3, 0, {"1": "QSPI_SS", "2": "QSPI_SD1", "3": "QSPI_SD2", "4": "GND",
                                     "5": "QSPI_SD0", "6": "QSPI_SCLK", "7": "QSPI_SD3", "8": "V3V3_PROBE"}),
    ("C_FL", "C100n", -3.4, -12.1, 90, {"1": "V3V3_PROBE", "2": "GND"}),
    # crystal by XIN/XOUT, 1k into XOUT, 15 pF each side (RP2040 hardware design guide)
    ("Y1", "XTAL", -3.0, 7.3, 0, {"1": "XIN", "3": "XOUT_R", "2": "GND", "4": "GND"}),
    ("R_XO", "R1k", 0.0, 4.9, 0, {"1": "XOUT", "2": "XOUT_R"}),
    ("C_XI", "C15p", -6.0, 7.3, 90, {"1": "XIN", "2": "GND"}),
    ("C_XO", "C15p", 0.4, 7.3, 90, {"1": "XOUT_R", "2": "GND"}),
    # decoupling: 100 nF at each IOVDD, ADC_AVDD, USB_VDD and DVDD pin; 1 uF on VREG_VIN and
    # VREG_VOUT. The row by the USB pins (46, 47) leaves them a gap for the pair
    ("C_IO1", "C100n", -5.4, -3.1, 0, {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_IO10", "C100n", -5.4, 1.7, 0, {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_IO22", "C100n", 2.0, 4.9, 0, {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_DV23", "C100n", 4.2, 4.9, 0, {"1": "V1V1", "2": "GND"}),
    ("C_IO33", "C100n", 5.4, 1.5, 90, {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_IO42", "C100n", 5.4, -2.3, 90, {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_IO49", "C100n", -4.0, -5.9, 90, {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_DV50", "C100n", -2.8, -5.9, 90, {"1": "V1V1", "2": "GND"}),
    ("C_UV", "C100n", -1.6, -5.9, 90, {"1": "V3V3_PROBE", "2": "GND"}),
    ("R_DP", "R27", 0.6, -5.9, 90, {"1": "USB_DP", "2": "USBR_DP"}),
    ("R_DN", "R27", 1.8, -5.9, 90, {"1": "USB_DN", "2": "USBR_DN"}),
    ("C_VRO", "C1u", 3.4, -5.9, 90, {"1": "V1V1", "2": "GND"}),
    ("C_VRI", "C1u", 4.6, -5.9, 90, {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_AV", "C100n", 5.8, -5.9, 90, {"1": "V3V3_PROBE", "2": "GND"}),
    ("R_RUN", "R10k", 2.6, 6.5, 0, {"1": "V3V3_PROBE", "2": "RUN"}),
    ("R_LU", "R1k", 7.4, -0.6, 0, {"1": "LED_USB", "2": "LED_USB_A"}),
    # BOOTSEL: a solder-jumper pad pair (bridge it with tweezers while plugging in USB);
    # only needed to reflash the probe: a blank flash boots into the USB loader by itself
    ("R_BOOT", "R1k", -11.4, -2.9, 90, {"1": "QSPI_SS", "2": "BOOT_R"}),
    ("JP_BOOT", "BOOT", -11.4, 0.5, 90, {"1": "BOOT_R", "2": "GND"}),
    # SWD out of the probe: 100 R on clock and data, 1k on reset (debugprobe drives it)
    ("R_NRST", "R1k", -7.4, -2.1, 0, {"1": "PRB_NRST", "2": "NRST"}),
    ("R_SWC", "R100", -7.4, -1.0, 0, {"1": "PRB_SWCLK", "2": "SWCLK_P"}),
    ("R_SWD", "R100", -7.4, 0.1, 0, {"1": "PRB_SWDIO", "2": "SWDIO_P"}),
]

# Everything else, at absolute device coordinates: (ref, part, x, y, rotation, {pad: net}).
# USB-C and TARGET 3V3 get their rotation and x from orient() (mouth / actuator west, on the edge).
OTHERS = [
    ("J_USB", "USBC", None, 44.0, None, {"A4": "VBUS", "B9": "VBUS", "B4": "VBUS", "A9": "VBUS",
                                         "A1": "GND", "B12": "GND", "B1": "GND", "A12": "GND", "SH": "GND",
                                         "A5": "CC1", "B5": "CC2", "A6": "USB_DP", "B6": "USB_DP",
                                         "A7": "USB_DN", "B7": "USB_DN"}),
    ("D_ESD", "ESD", -2.0, 44.0, 90, {"1": "USB_DP", "6": "USB_DP", "3": "USB_DN", "4": "USB_DN",
                                      "2": "GND", "5": "VBUS"}),
    ("R_CC1", "R5k1", -2.0, 40.2, 0, {"1": "CC1", "2": "GND"}),
    ("R_CC2", "R5k1", -2.0, 47.8, 0, {"1": "CC2", "2": "GND"}),
    # power: two ME6211 (1 VIN, 2 VSS, 3 CE, 4 NC, 5 VOUT): the probe's own 3.3 V, and the
    # device's, switched by its CE (the switch carries no current) and current-limited by the regulator
    ("C_VBUS", "C10u", -11.6, 35.0, 90, {"1": "VBUS", "2": "GND"}),
    ("U_LP", "LDO", -8.6, 35.0, 0, {"1": "VBUS", "2": "GND", "3": "VBUS", "5": "V3V3_PROBE"}),
    ("C_3V3P", "C10u", -8.6, 31.8, 0, {"1": "V3V3_PROBE", "2": "GND"}),
    ("U_LT", "LDO", -4.4, 35.0, 0, {"1": "VBUS", "2": "GND", "3": "CE_TGT", "5": "V3V3_TGT"}),
    ("C_TGT", "C10u", -4.4, 31.8, 0, {"1": "V3V3_TGT", "2": "GND"}),
    ("R_CE", "R100k", -1.6, 35.0, 90, {"1": "CE_TGT", "2": "GND"}),
    ("SW_PWR", "SPDT", None, 25.0, None, {"1": "VBUS", "2": "CE_TGT", "3": "GND", "SH": "GND"}),
    ("R_LT", "R1k", -4.0, 12.0, 0, {"1": "V3V3_TGT", "2": "LED_TGT_A"}),
    ("D_TGT", "LEDR", -4.0, 14.0, 0, {"2": "LED_TGT_A", "1": "GND"}),
    ("D_USB", "LEDG", -9.0, 14.0, 0, {"2": "LED_USB_A", "1": "GND"}),
    # MCU / RADIO selector, knob up: commons 2, 5; MCU throws 1, 6; RADIO throws 3, 4
    ("SW_SEL", "DPDT", -6.5, 57.0, 0, {"2": "SWDIO_P", "1": "MCU_SWDIO", "3": "RAD_SWDIO_S",
                                       "5": "SWCLK_P", "6": "MCU_SWCLK", "4": "RAD_SWCLK_S"}),
    # the radio branch: 1k each, so a radio that is not powered yet is never back-powered hard
    ("R_RSWD", "R1k", -2.6, 63.0, 90, {"1": "RAD_SWDIO_S", "2": "RAD_SWDIO"}),
    ("R_RSWC", "R1k", -1.4, 63.0, 90, {"1": "RAD_SWCLK_S", "2": "RAD_SWCLK"}),
    # the spring pins, under the device's pads
    ("P_3V3", "POGO", *PADS["TP_3V3"], 0, {"1": "V3V3_TGT"}),
    ("P_SWDIO", "POGO", *PADS["TP_SWDIO"], 0, {"1": "MCU_SWDIO"}),
    ("P_SWCLK", "POGO", *PADS["TP_SWCLK"], 0, {"1": "MCU_SWCLK"}),
    ("P_GND", "POGO", *PADS["TP_GND"], 0, {"1": "GND"}),
    ("P_NRST", "POGO", *PADS["TP_NRST"], 0, {"1": "NRST"}),
    ("P_RSWDIO", "POGO", *PADS["TP_RSWDIO"], 0, {"1": "RAD_SWDIO"}),
    ("P_RSWCLK", "POGO", *PADS["TP_RSWCLK"], 0, {"1": "RAD_SWCLK"}),
    ("H_1", "HOLE", *HOLES["H1"], 0, {}),
    ("H_2", "HOLE", *HOLES["H2"], 0, {}),
    ("H_4", "HOLE", *HOLES["H4"], 0, {}),
]
# A part whose working side faces west on the edge: (pad, its offset from the part's origin
# once turned so, x and +-y), offset of the origin from x EDGE_X)
ORIENT = {"J_USB": ("A6", (4.04, 0.25), 3.60),     # signal pads east of the mouth; mouth flush with the edge
          "SW_PWR": ("2", (1.95, 0.75), 1.72)}     # pins east of the actuator; the actuator out past the edge

# netclasses: (name, track, clearance, via d, via drill, pair width, pair gap), patterns
CLASSES = [("Default", 0.15, 0.15, 0.45, 0.25, 0.2, 0.25), ("Rail", 0.4, 0.15, 0.6, 0.3, 0.2, 0.25),
           ("USB", 0.2, 0.15, 0.45, 0.25, 0.2, 0.2)]
PATTERNS = [("Rail", n) for n in ("VBUS", "V3V3_PROBE", "V3V3_TGT", "V1V1")] + \
           [("USB", n) for n in ("USB_DP", "USB_DN", "USBR_DP", "USBR_DN")]


def layers_text():
    names = ['(0 "F.Cu" signal)', '(4 "In1.Cu" signal)', '(6 "In2.Cu" signal)', '(2 "B.Cu" signal)',
             '(9 "F.Adhes" user "F.Adhesive")', '(11 "B.Adhes" user "B.Adhesive")', '(13 "F.Paste" user)',
             '(15 "B.Paste" user)', '(5 "F.SilkS" user "F.Silkscreen")', '(7 "B.SilkS" user "B.Silkscreen")',
             '(1 "F.Mask" user)', '(3 "B.Mask" user)', '(17 "Dwgs.User" user "User.Drawings")',
             '(19 "Cmts.User" user "User.Comments")', '(25 "Edge.Cuts" user)', '(27 "Margin" user)',
             '(31 "F.CrtYd" user "F.Courtyard")', '(29 "B.CrtYd" user "B.Courtyard")', '(35 "F.Fab" user)',
             '(33 "B.Fab" user)']
    return "\t(layers\n" + "\n".join("\t\t" + n for n in names) + "\n\t)"


def write_project():
    """The jig's project: the device board's rules (JLC 4-layer), the jig's own net classes."""
    d = json.load(open(WALLET_PRO))
    tmpl = next(c for c in d["net_settings"]["classes"] if c["name"] == "Default")
    out = []
    for name, tw, clr, vd, vdr, dw, dg in CLASSES:
        c = dict(tmpl)
        c.update(name=name, track_width=tw, clearance=clr, via_diameter=vd, via_drill=vdr,
                 diff_pair_width=dw, diff_pair_gap=dg)
        out.append(c)
    d["net_settings"]["classes"] = out
    d["net_settings"]["netclass_patterns"] = [{"netclass": c, "pattern": p} for c, p in PATTERNS]
    d["net_settings"]["netclass_assignments"] = None
    d["meta"]["filename"] = os.path.basename(PRO)
    json.dump(d, open(PRO, "w"), indent=2)


def start_file():
    open(OUT, "w", encoding="utf-8").write(
        "(kicad_pcb\n\t(version 20260206)\n\t(generator \"pcbnew\")\n\t(generator_version \"10.0\")\n"
        "\t(general\n\t\t(thickness 1.6)\n\t\t(legacy_teardrops no)\n\t)\n\t(paper \"A4\")\n"
        "\t(title_block\n\t\t(title \"Ambire Ice SWD jig\")\n\t\t(rev \"2\")\n\t)\n"
        f"{layers_text()}\n\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t\t(allow_soldermask_bridges_in_footprints no)\n"
        "\t\t(tenting\n\t\t\t(front yes)\n\t\t\t(back yes)\n\t\t)\n\t)\n)\n")


def rounded_rect(board, layer, x0, y0, x1, y1, r, width=0.05):
    k = r * (1 - 2 ** -0.5)
    def seg(a, b):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pt(*a)); s.SetEnd(pt(*b)); s.SetLayer(layer); s.SetWidth(mm(width)); board.Add(s)
    def arc(a, m, b):
        s = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
        s.SetArcGeometry(pt(*a), pt(*m), pt(*b)); s.SetLayer(layer); s.SetWidth(mm(width)); board.Add(s)
    seg((x0 + r, y0), (x1 - r, y0)); arc((x1 - r, y0), (x1 - k, y0 + k), (x1, y0 + r))
    seg((x1, y0 + r), (x1, y1 - r)); arc((x1, y1 - r), (x1 - k, y1 - k), (x1 - r, y1))
    seg((x1 - r, y1), (x0 + r, y1)); arc((x0 + r, y1), (x0 + k, y1 - k), (x0, y1 - r))
    seg((x0, y1 - r), (x0, y0 + r)); arc((x0, y0 + r), (x0 + k, y0 + k), (x0 + r, y0))


def text(board, s, x, y, size=0.8):
    """Silkscreen on the parts' face (B), readable from that side."""
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s); t.SetPosition(pt(x, y)); t.SetLayer(pcbnew.B_SilkS)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); t.SetTextThickness(mm(max(0.1, size * 0.15)))
    t.SetMirrored(True)
    board.Add(t)


def zone(board, layer, net):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer); z.SetNet(net)
    z.SetLocalClearance(mm(0.25)); z.SetMinThickness(mm(0.2))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    x0, y0, x1, y1, _ = OUTLINE
    poly = z.Outline(); poly.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        poly.Append(mm(x), mm(y))
    board.Add(z)


def pad(fp, num):
    return next(p for p in fp.Pads() if p.GetNumber() == num)


def add(board, nets, ref, part, x, y, rot, padnets):
    def net(name):
        if name not in nets:
            n = pcbnew.NETINFO_ITEM(board, name)
            board.Add(n)
            nets[name] = n
        return nets[name]
    value, (lib, name), lcsc, mpn = P[part] if part in P else ("", NOT_ASSEMBLED[part], "", "")
    f = pcbnew.FootprintLoad(lib, name)
    if f is None:
        sys.exit(f"{ref}: footprint {name} not found in {lib}")
    f.SetReference(ref)
    if value:
        f.SetValue(value)
    if lcsc:
        f.SetField("LCSC Part #", lcsc); f.SetField("MPN", mpn)
        for fld in f.GetFields():
            if fld.GetName() in ("LCSC Part #", "MPN"):
                fld.SetVisible(False)
    else:
        f.SetExcludedFromBOM(True); f.SetExcludedFromPosFiles(True); f.SetBoardOnly(True)
    board.Add(f)
    f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)     # every part on the B side
    f.SetPosition(pt(x, y)); f.SetOrientationDegrees(rot)
    f.Reference().SetVisible(False)
    for p in f.Pads():
        n = padnets.get(p.GetNumber())
        if n:
            p.SetNet(net(n))
    missing = set(padnets) - {p.GetNumber() for p in f.Pads()}
    if missing:
        sys.exit(f"{ref}: pads {sorted(missing)} not in {name}")
    return f


def orient(f, ref):
    """Turn a part so its working side faces west, then put that side on the jig's west edge."""
    num, (dx, dy), edge = ORIENT[ref]
    y = M(f.GetPosition().y)
    for rot in (0, 90, 180, 270):
        f.SetOrientationDegrees(rot)
        f.SetPosition(pt(0, y))
        q = pad(f, num).GetPosition()
        if abs(M(q.x) - dx) < 0.05 and abs(abs(M(q.y) - y) - dy) < 0.05:
            f.SetPosition(pt(EDGE_X + edge, y))
            return rot
    sys.exit(f"{ref}: no rotation puts pad {num} at +{dx}, +-{dy}")


def check_device():
    """The device board's programming pads and holes against the jig's, read in a separate
    process (pcbnew: one LoadBoard per process). Exit 1 on any difference."""
    code = ("import json, pcbnew; b = pcbnew.LoadBoard(%r); M = pcbnew.ToMM; out = {}\n"
            "for f in b.GetFootprints():\n"
            "    r = f.GetReference()\n"
            "    if r.startswith('TP_') or r in ('H1', 'H2', 'H4'):\n"
            "        p = max(f.Pads(), key=lambda q: q.GetDrillSize().x) if r[0] == 'H' else f.Pads()[0]\n"
            "        out[r] = [round(M(p.GetPosition().x), 3), round(M(p.GetPosition().y), 3), f.GetLayer() == pcbnew.F_Cu]\n"
            "print(json.dumps(out))") % DEVICE_BOARD
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if run.returncode:
        sys.exit(f"could not read {DEVICE_BOARD}: {run.stderr[-500:]}")
    dev = json.loads(run.stdout.strip().splitlines()[-1])
    bad = []
    for ref, (x, y) in {**PADS, **HOLES}.items():
        if ref not in dev:
            bad.append(f"{ref}: not on the device board")
            continue
        dx, dy, front = dev[ref]
        if math.dist((x, y), (dx, dy)) > 0.02:
            bad.append(f"{ref}: the jig has it at {x:.2f}, {y:.2f}; the device board at {dx:.2f}, {dy:.2f}")
        if ref.startswith("TP_") and not front:
            bad.append(f"{ref}: on the device's back; the jig reaches the front only")
    for ref in sorted(set(dev) - set(PADS) - set(HOLES)):
        bad.append(f"{ref}: a programming pad the jig does not reach")
    pads = list(PADS.items())
    for i, (a, pa) in enumerate(pads):
        for b, pb in pads[i + 1:]:
            if math.dist(pa, pb) < PIN_PITCH_MIN:
                bad.append(f"{a} and {b} are {math.dist(pa, pb):.2f} mm apart: the pins need {PIN_PITCH_MIN}")
    if bad:
        sys.exit("the jig does not fit the device board:\n  " + "\n  ".join(bad))
    print(f"device board matches the jig: {len(PADS)} pads, {len(HOLES)} holes")


def main():
    check_device()
    if "--check" in sys.argv:
        return
    write_project()
    start_file()
    board = pcbnew.LoadBoard(OUT)
    rounded_rect(board, pcbnew.Edge_Cuts, *OUTLINE)
    rounded_rect(board, pcbnew.Edge_Cuts, *JOYSTICK_CUT)
    rounded_rect(board, pcbnew.B_SilkS, *DEVICE_OUTLINE, width=0.15)   # where the device lies, face down
    nets = {}

    # the core: find how the B side turns a footprint, so the RP2040's pin 1 lands south-west
    # (the north-south mirror of a top-side layout: its SWD pins keep facing west)
    cx, cy = CORE_AT
    rp = add(board, nets, *CORE[0][:2], cx, cy, 0, CORE[0][5])
    p1 = pad(rp, "1").GetPosition()
    if not (M(p1.x) < cx and M(p1.y) > cy):
        rp.SetOrientationDegrees(180)
        p1 = pad(rp, "1").GetPosition()
    turn = int(round(rp.GetOrientationDegrees())) % 360
    if not (M(p1.x) < cx and M(p1.y) > cy):
        sys.exit(f"RP2040 pin 1 at {M(p1.x):.2f}, {M(p1.y):.2f}: not south-west of {cx}, {cy}")
    for ref, part, dx, dy, rot, padnets in CORE[1:]:
        add(board, nets, ref, part, cx + dx, cy - dy, (rot + turn) % 360, padnets)

    for ref, part, x, y, rot, padnets in OTHERS:
        f = add(board, nets, ref, part, 0.0 if x is None else x, y, rot or 0, padnets)
        if ref in ORIENT:
            orient(f, ref)

    # the selector's and the power switch's labels go where their pads are
    sel = board.FindFootprintByReference("SW_SEL")
    mcu_end, rad_end = M(pad(sel, "1").GetPosition().y), M(pad(sel, "3").GetPosition().y)
    sx = M(sel.GetPosition().x)
    sy, half = M(sel.GetPosition().y), 4.55 + 0.9                 # past the body's ends (9.1 mm long)
    text(board, "MCU", sx, sy + (half if mcu_end > sy else -half))
    text(board, "RADIO", sx, sy + (half if rad_end > sy else -half))
    pw = board.FindFootprintByReference("SW_PWR")
    on_y, off_y = M(pad(pw, "1").GetPosition().y), M(pad(pw, "3").GetPosition().y)
    text(board, "TARGET 3V3", -4.2, M(pw.GetPosition().y), 0.7)
    text(board, "ON", -11.4, on_y + (-3.4 if on_y < off_y else 3.4), 0.7)
    text(board, "OFF", -11.4, off_y + (-3.4 if off_y < on_y else 3.4), 0.7)
    for s, x, y, size in [("AMBIRE ICE SWD JIG - debugprobe (CMSIS-DAP)", 18.0, -1.6, 0.9),
                          ("USB", -6.5, 37.4, 0.7), ("3V3", -4.0, 15.8, 0.7), ("USB", -9.0, 15.8, 0.7),
                          ("BOOT", 2.6, 41.4, 0.7),
                          ("device face down, H1 H2 H4 on the standoffs", 24.0, 88.6, 0.8)]:
        text(board, s, x, y, size)

    zone(board, pcbnew.In1_Cu, nets["GND"])            # solid ground
    zone(board, pcbnew.In2_Cu, nets["V3V3_PROBE"])     # the probe's 3.3 V plane
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(OUT, board)
    usb = board.FindFootprintByReference("J_USB")
    print(f"wrote {OUT}: {len(list(board.GetFootprints()))} parts, {len(nets)} nets; core turned {turn}; "
          f"USB-C at {M(usb.GetPosition().x):.2f}, {M(usb.GetPosition().y):.2f} turned {usb.GetOrientationDegrees():.0f}; "
          f"selector MCU end y {mcu_end:.1f}")


if __name__ == "__main__":
    main()
