#!/usr/bin/env python3
"""The programming jig's two boards, placed (and the base routed) by script.

    KICAD_PY jig/makeJig.py probe     # writes jig/probe.kicad_pcb: placed, planes drawn, not routed
    KICAD_PY jig/makeJig.py base      # writes jig/base.kicad_pcb: placed and routed

(from hardware/ambire-hw). The probe board is then routed by humanizedKicadAutoRouter
(jig/README.md, "Making the boards"). One LoadBoard per process: run each board apart.

Coordinates are the DEVICE board's (x east, y south, mm, seen from its front), so every
spring pin sits over the pad it lands on without a transform.

  probe  4 layers, over the device's front. An RP2040 runs Raspberry Pi's debugprobe
         firmware as built for the Pico (debugprobe_on_pico.uf2: SWCLK GP2, SWDIO GP3,
         reset GP1, LED GP25), a CMSIS-DAP probe on USB-C, which firmware/swd.py drives
         with --probe cmsis-dap. Five spring pins underneath land on the device's front
         pads TP_3V3, TP_GND, TP_SWDIO, TP_SWCLK, TP_NRST. A second regulator powers the
         device through TP_3V3 (switch TARGET 3V3). A selector sends SWD to the device's MCU
         pins or, through a cable to the base, to the radio's pads on the device's back.
  base   2 layers, under the device's back. The device rests on three M2 standoffs whose
         threads go through its holes H1, H2, H4; two spring pins rise into the radio's
         pads TP_RSWDIO and TP_RSWCLK; a JST SH socket takes the cable from the probe;
         two taller posts carry the probe board.
Heights (from the base's top face): the device's back 5.0 (standoffs), its front 6.0, the
probe board's underside 11.0 (posts of 5 + 6 mm): every spring pin works at its 5.0 mm.
"""
import json
import os
import sys
import uuid

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KICAD_FP = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
JIG_LIB = os.path.join(HERE, "jig.pretty")
WALLET_LIB = os.path.join(ROOT, "board", "wallet.pretty")
WALLET_PRO = os.path.join(ROOT, "board", "wallet.kicad_pro")

mm = pcbnew.FromMM
pt = lambda x, y: pcbnew.VECTOR2I(mm(x), mm(y))

# ── the device board: where the jig lands (board/wallet.kicad_pcb, 2026-11-01) ──
DEVICE_FRONT_PADS = {"TP_3V3": (4.00, 15.00), "TP_SWDIO": (4.14, 17.68), "TP_SWCLK": (4.00, 20.28),
                     "TP_GND": (1.29, 20.74), "TP_NRST": (33.60, 26.80)}
DEVICE_BACK_PADS = {"TP_RSWDIO": (5.40, 79.40), "TP_RSWCLK": (6.10, 81.90)}
DEVICE_HOLES = {"H1": (3.20, 3.20), "H2": (44.80, 3.20), "H4": (44.80, 83.80)}   # 2.2 mm, plated
DEVICE_OUTLINE = (0.0, 0.0, 48.0, 87.0, 4.0)

POSTS = [(-5.0, 21.0), (53.0, 21.0)]          # the probe board's two posts, outside the device
PROBE_OUTLINE = (-9.0, 8.0, 57.0, 36.0, 2.0)  # x0, y0, x1, y1, corner radius
BASE_OUTLINE = (-9.0, -4.0, 57.0, 91.0, 3.0)
FEET = [(-6.0, -1.0), (54.0, -1.0), (-6.0, 88.0), (54.0, 88.0)]

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
    "JST4": ("SM04B-SRSS-TB", (f"{KICAD_FP}/Connector_JST.pretty", "JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal"), "C160404", "SM04B-SRSS-TB(LF)(SN)"),
    "POGO": ("YZP0063-20068-01", (JIG_LIB, "Xinyangze_YZP0063-20068-01"), "C5157248", "YZP0063-20068-01"),
}
NOT_ASSEMBLED = {
    "HOLE": (JIG_LIB, "Jig_Hole_M2_NPTH"),
    "FOOT": (JIG_LIB, "Jig_Hole_M3_Foot"),
    "BOOT": (f"{KICAD_FP}/Jumper.pretty", "SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm"),
}

# ── the probe board ───────────────────────────────────────────────────────────
# RP2040 (QFN-56, KiCad's MCU_RaspberryPi symbol): pin -> net. Unlisted pins stay unconnected
# (GPIO0, GPIO4-24 but 25, GPIO26-29, the RP2040's own SWD port).
U_RP = (24.0, 25.5)
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

# (ref, part, x, y, rotation, side, {pad: net}); side "F" or "B"
PROBE = [
    ("U1", "RP2040", *U_RP, 0, "F", RP_PINS),
    # flash, north-west of the RP2040's QSPI pins (W25Q16JV SOIC-8: 1 /CS, 2 DO, 3 /WP, 4 GND, 5 DI, 6 CLK, 7 /HOLD, 8 VCC)
    ("U2", "FLASH", 15.0, 15.2, 0, "F", {"1": "QSPI_SS", "2": "QSPI_SD1", "3": "QSPI_SD2", "4": "GND",
                                          "5": "QSPI_SD0", "6": "QSPI_SCLK", "7": "QSPI_SD3", "8": "V3V3_PROBE"}),
    ("C_FL", "C100n", 20.6, 13.4, 90, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    # crystal south of XIN/XOUT, 1k into XOUT, 15 pF each side (RP2040 hardware design guide)
    ("Y1", "XTAL", 21.0, 32.8, 0, "F", {"1": "XIN", "3": "XOUT_R", "2": "GND", "4": "GND"}),
    ("R_XO", "R1k", 24.0, 30.4, 0, "F", {"1": "XOUT", "2": "XOUT_R"}),
    ("C_XI", "C15p", 18.0, 32.8, 90, "F", {"1": "XIN", "2": "GND"}),
    ("C_XO", "C15p", 24.4, 32.8, 90, "F", {"1": "XOUT_R", "2": "GND"}),
    # decoupling: 100 nF at each IOVDD, ADC_AVDD, USB_VDD and DVDD pin; 1 uF on VREG_VIN and
    # VREG_VOUT. The row over the north pins leaves x 24-26.6 to the USB pair (pins 46, 47)
    ("C_IO1", "C100n", 18.6, 22.4, 0, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_IO10", "C100n", 18.6, 27.2, 0, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_IO22", "C100n", 26.0, 30.4, 0, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_DV23", "C100n", 28.2, 30.4, 0, "F", {"1": "V1V1", "2": "GND"}),
    ("C_IO33", "C100n", 29.4, 27.0, 90, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_IO42", "C100n", 29.4, 23.2, 90, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_IO49", "C100n", 20.0, 19.6, 90, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_DV50", "C100n", 21.2, 19.6, 90, "F", {"1": "V1V1", "2": "GND"}),
    ("C_UV", "C100n", 22.4, 19.6, 90, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("R_DP", "R27", 24.6, 19.6, 90, "F", {"1": "USB_DP", "2": "USBR_DP"}),
    ("R_DN", "R27", 25.8, 19.6, 90, "F", {"1": "USB_DN", "2": "USBR_DN"}),
    ("C_VRO", "C1u", 27.4, 19.6, 90, "F", {"1": "V1V1", "2": "GND"}),
    ("C_VRI", "C1u", 28.6, 19.6, 90, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("C_AV", "C100n", 29.8, 19.6, 90, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("R_RUN", "R10k", 26.6, 32.0, 0, "F", {"1": "V3V3_PROBE", "2": "RUN"}),
    # BOOTSEL: a solder-jumper pad pair (bridge it with tweezers while plugging in USB);
    # only needed to reflash the probe: a blank flash boots into the USB loader by itself
    ("R_BOOT", "R1k", 10.6, 22.6, 90, "F", {"1": "QSPI_SS", "2": "BOOT_R"}),
    ("JP_BOOT", "BOOT", 10.6, 26.0, 90, "F", {"1": "BOOT_R", "2": "GND"}),
    # USB-C, mouth north on the board's north edge; ESD; 5.1k CC pull-downs; 27 R series
    ("J_USB", "USBC", 41.0, 11.6, 180, "F", {"A4": "VBUS", "B9": "VBUS", "B4": "VBUS", "A9": "VBUS",
                                              "A1": "GND", "B12": "GND", "B1": "GND", "A12": "GND", "SH": "GND",
                                              "A5": "CC1", "B5": "CC2", "A6": "USB_DP", "B6": "USB_DP",
                                              "A7": "USB_DN", "B7": "USB_DN"}),
    ("R_CC1", "R5k1", 43.8, 18.8, 90, "F", {"1": "CC1", "2": "GND"}),
    ("R_CC2", "R5k1", 38.2, 18.8, 90, "F", {"1": "CC2", "2": "GND"}),
    ("D_ESD", "ESD", 41.0, 19.4, 0, "F", {"1": "USB_DP", "6": "USB_DP", "3": "USB_DN", "4": "USB_DN",
                                           "2": "GND", "5": "VBUS"}),
    # power: two ME6211 (1 VIN, 2 VSS, 3 CE, 4 NC, 5 VOUT): the probe's own 3.3 V, and the
    # device's, switched by its CE (the switch carries no current) and current-limited by the regulator
    ("C_VBUS", "C10u", 34.0, 10.6, 90, "F", {"1": "VBUS", "2": "GND"}),
    ("U_LP", "LDO", 31.0, 11.4, 0, "F", {"1": "VBUS", "2": "GND", "3": "VBUS", "5": "V3V3_PROBE"}),
    ("C_3V3P", "C10u", 27.6, 11.4, 90, "F", {"1": "V3V3_PROBE", "2": "GND"}),
    ("U_LT", "LDO", 31.0, 15.4, 0, "F", {"1": "VBUS", "2": "GND", "3": "CE_TGT", "5": "V3V3_TGT"}),
    ("C_TGT", "C10u", 27.6, 15.4, 90, "F", {"1": "V3V3_TGT", "2": "GND"}),
    ("R_CE", "R100k", 34.0, 14.4, 90, "F", {"1": "CE_TGT", "2": "GND"}),
    # TARGET 3V3 switch on the south edge, actuator out: west = ON (common 2 to 1 = VBUS)
    ("SW_PWR", "SPDT", 44.0, 34.28, 0, "F", {"1": "VBUS", "2": "CE_TGT", "3": "GND", "SH": "GND"}),
    ("R_LT", "R1k", 40.8, 27.4, 0, "F", {"1": "V3V3_TGT", "2": "LED_TGT_A"}),
    ("D_TGT", "LEDR", 43.4, 27.4, 180, "F", {"2": "LED_TGT_A", "1": "GND"}),
    ("R_LU", "R1k", 31.4, 24.9, 0, "F", {"1": "LED_USB", "2": "LED_USB_A"}),
    ("D_USB", "LEDG", 31.4, 23.0, 180, "F", {"2": "LED_USB_A", "1": "GND"}),
    # SWD out of the probe: 100 R on clock and data, 1k on reset (debugprobe drives it)
    ("R_NRST", "R1k", 16.6, 23.4, 0, "F", {"1": "PRB_NRST", "2": "NRST"}),
    ("R_SWC", "R100", 16.6, 24.5, 0, "F", {"1": "PRB_SWCLK", "2": "SWCLK_P"}),
    ("R_SWD", "R100", 16.6, 25.6, 0, "F", {"1": "PRB_SWDIO", "2": "SWDIO_P"}),
    # MCU / RADIO selector: knob north = MCU (commons 2, 5 to 1, 6), south = RADIO (to 3, 4)
    ("SW_SEL", "DPDT", 5.6, 30.4, 0, "F", {"2": "SWDIO_P", "1": "MCU_SWDIO", "3": "RAD_SWDIO_S",
                                           "5": "SWCLK_P", "6": "MCU_SWCLK", "4": "RAD_SWCLK_S"}),
    # the radio branch: 1k each, so a radio that is not powered yet is never back-powered hard
    ("R_RSWD", "R1k", -5.0, 27.0, 90, "F", {"1": "RAD_SWDIO_S", "2": "RAD_SWDIO"}),
    ("R_RSWC", "R1k", -3.8, 27.0, 90, "F", {"1": "RAD_SWCLK_S", "2": "RAD_SWCLK"}),
    # the cable to the base (JST SH 4: 1 GND, 2 SWDIO, 3 SWCLK, 4 GND), mouth on the south edge
    ("J_RAD", "JST4", -4.5, 32.5, 180, "F", {"1": "GND", "2": "RAD_SWDIO", "3": "RAD_SWCLK", "4": "GND", "MP": "GND"}),
    # the spring pins, underneath, over the device's front pads
    ("P_3V3", "POGO", *DEVICE_FRONT_PADS["TP_3V3"], 0, "B", {"1": "V3V3_TGT"}),
    ("P_SWDIO", "POGO", *DEVICE_FRONT_PADS["TP_SWDIO"], 0, "B", {"1": "MCU_SWDIO"}),
    ("P_SWCLK", "POGO", *DEVICE_FRONT_PADS["TP_SWCLK"], 0, "B", {"1": "MCU_SWCLK"}),
    ("P_GND", "POGO", *DEVICE_FRONT_PADS["TP_GND"], 0, "B", {"1": "GND"}),
    ("P_NRST", "POGO", *DEVICE_FRONT_PADS["TP_NRST"], 0, "B", {"1": "NRST"}),
    ("H_P1", "HOLE", *POSTS[0], 0, "F", {}),
    ("H_P2", "HOLE", *POSTS[1], 0, "F", {}),
]
PROBE_SILK = [   # (text, x, y, size, layer)
    ("AMBIRE ICE SWD JIG", 1.0, 10.2, 1.0, "F"),
    ("debugprobe (CMSIS-DAP)", 1.0, 12.0, 0.7, "F"),
    ("MCU", 12.8, 29.0, 0.8, "F"), ("RADIO", 12.0, 32.9, 0.8, "F"),
    ("TARGET 3V3", 44.0, 30.6, 0.8, "F"), ("ON", 38.4, 33.6, 0.7, "F"), ("OFF", 49.6, 33.6, 0.7, "F"),
    ("3V3", 46.4, 27.4, 0.7, "F"), ("USB", 34.6, 23.0, 0.7, "F"),
    ("BOOT", 12.9, 26.0, 0.7, "F"),
    ("TO BASE", -4.5, 24.6, 0.7, "F"),
]
# netclasses: (name, track, clearance, via d, via drill, pair width, pair gap), patterns
PROBE_CLASSES = [("Default", 0.15, 0.15, 0.45, 0.25, 0.2, 0.25), ("Rail", 0.4, 0.15, 0.6, 0.3, 0.2, 0.25),
                 ("USB", 0.2, 0.15, 0.45, 0.25, 0.2, 0.2)]
PROBE_PATTERNS = [("Rail", n) for n in ("VBUS", "V3V3_PROBE", "V3V3_TGT", "V1V1")] + \
                 [("USB", n) for n in ("USB_DP", "USB_DN", "USBR_DP", "USBR_DN")]

# ── the base board ───────────────────────────────────────────────────────────
BASE = [
    ("P_RSWDIO", "POGO", *DEVICE_BACK_PADS["TP_RSWDIO"], 0, "F", {"1": "RAD_SWDIO"}),
    ("P_RSWCLK", "POGO", *DEVICE_BACK_PADS["TP_RSWCLK"], 0, "F", {"1": "RAD_SWCLK"}),
    # mouth south, pins 4..1 west to east at y 46.0
    ("J_RAD", "JST4", -4.5, 44.0, 180, "F", {"1": "GND", "2": "RAD_SWDIO", "3": "RAD_SWCLK", "4": "GND", "MP": "GND"}),
    ("H_1", "HOLE", *DEVICE_HOLES["H1"], 0, "F", {}),
    ("H_2", "HOLE", *DEVICE_HOLES["H2"], 0, "F", {}),
    ("H_4", "HOLE", *DEVICE_HOLES["H4"], 0, "F", {}),
    ("H_P1", "HOLE", *POSTS[0], 0, "F", {}),
    ("H_P2", "HOLE", *POSTS[1], 0, "F", {}),
] + [(f"H_F{i + 1}", "FOOT", x, y, 0, "F", {}) for i, (x, y) in enumerate(FEET)]
# the two lines, by hand: west from each pin, 45 deg, then north up the device's west side
# to the socket (its SWDIO pin at x -4.0, SWCLK at -5.0)
BASE_TRACKS = [
    ("RAD_SWDIO", 0.25, [(5.40, 79.40), (-1.00, 79.40), (-4.00, 76.40), (-4.00, 46.00)]),
    ("RAD_SWCLK", 0.25, [(6.10, 81.90), (-2.00, 81.90), (-5.00, 78.90), (-5.00, 46.00)]),
]
BASE_SILK = [
    ("AMBIRE ICE SWD JIG: BASE", 24.0, 89.6, 1.0, "F"),
    ("device front up; H1, H2, H4 on the standoffs", 24.0, -2.4, 0.7, "F"),
    ("RADIO SWD", 5.8, 85.0, 0.7, "F"),
    ("TO PROBE", -4.5, 40.0, 0.7, "F"),
]
BASE_CLASSES = [("Default", 0.25, 0.2, 0.6, 0.3, 0.2, 0.25)]


def layers_text(n):
    cu = ['\t\t(0 "F.Cu" signal)'] + (['\t\t(4 "In1.Cu" signal)', '\t\t(6 "In2.Cu" signal)'] if n == 4 else []) + \
         ['\t\t(2 "B.Cu" signal)']
    rest = ['(9 "F.Adhes" user "F.Adhesive")', '(11 "B.Adhes" user "B.Adhesive")', '(13 "F.Paste" user)',
            '(15 "B.Paste" user)', '(5 "F.SilkS" user "F.Silkscreen")', '(7 "B.SilkS" user "B.Silkscreen")',
            '(1 "F.Mask" user)', '(3 "B.Mask" user)', '(17 "Dwgs.User" user "User.Drawings")',
            '(19 "Cmts.User" user "User.Comments")', '(25 "Edge.Cuts" user)', '(27 "Margin" user)',
            '(31 "F.CrtYd" user "F.Courtyard")', '(29 "B.CrtYd" user "B.Courtyard")', '(35 "F.Fab" user)',
            '(33 "B.Fab" user)']
    return "\t(layers\n" + "\n".join(cu + ["\t\t" + r for r in rest]) + "\n\t)"


def write_project(path, classes, patterns):
    """The board's project: the device board's rules (JLC 4-layer), the jig's own net classes."""
    d = json.load(open(WALLET_PRO))
    tmpl = next(c for c in d["net_settings"]["classes"] if c["name"] == "Default")
    out = []
    for name, tw, clr, vd, vdr, dw, dg in classes:
        c = dict(tmpl)
        c.update(name=name, track_width=tw, clearance=clr, via_diameter=vd, via_drill=vdr,
                 diff_pair_width=dw, diff_pair_gap=dg)
        out.append(c)
    d["net_settings"]["classes"] = out
    d["net_settings"]["netclass_patterns"] = [{"netclass": c, "pattern": p} for c, p in patterns]
    d["net_settings"]["netclass_assignments"] = None
    d["meta"]["filename"] = os.path.basename(path)
    json.dump(d, open(path, "w"), indent=2)


def start_file(path, ncu, title, thickness=1.6):
    open(path, "w", encoding="utf-8").write(
        "(kicad_pcb\n\t(version 20260206)\n\t(generator \"pcbnew\")\n\t(generator_version \"10.0\")\n"
        f"\t(general\n\t\t(thickness {thickness})\n\t\t(legacy_teardrops no)\n\t)\n\t(paper \"A4\")\n"
        f"\t(title_block\n\t\t(title \"{title}\")\n\t\t(rev \"1\")\n\t)\n"
        f"{layers_text(ncu)}\n\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t\t(allow_soldermask_bridges_in_footprints no)\n"
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


def text(board, s, x, y, size, side):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s); t.SetPosition(pt(x, y))
    t.SetLayer(pcbnew.F_SilkS if side == "F" else pcbnew.B_SilkS)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); t.SetTextThickness(mm(max(0.1, size * 0.15)))
    if side == "B":
        t.SetMirrored(True)
    board.Add(t)


def zone(board, layer, net, outline, clearance=0.25):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer); z.SetNet(net)
    z.SetLocalClearance(mm(clearance)); z.SetMinThickness(mm(0.2))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    x0, y0, x1, y1, _ = outline
    poly = z.Outline(); poly.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        poly.Append(mm(x), mm(y))
    board.Add(z)


def place(board, parts, nets):
    def net(name):
        if name not in nets:
            n = pcbnew.NETINFO_ITEM(board, name)
            board.Add(n)
            nets[name] = n
        return nets[name]
    for ref, part, x, y, rot, side, padnets in parts:
        if part in P:
            value, (lib, name), lcsc, mpn = P[part]
        else:
            value, (lib, name), lcsc, mpn = "", NOT_ASSEMBLED[part], "", ""
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
        if side == "B":
            f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        f.SetPosition(pt(x, y)); f.SetOrientationDegrees(rot)
        f.Reference().SetVisible(False)
        for p in f.Pads():
            n = padnets.get(p.GetNumber())
            if n:
                p.SetNet(net(n))
        missing = set(padnets) - {p.GetNumber() for p in f.Pads()}
        if missing:
            sys.exit(f"{ref}: pads {sorted(missing)} not in {name}")


def build_probe():
    out = os.path.join(HERE, "probe.kicad_pcb")
    write_project(os.path.join(HERE, "probe.kicad_pro"), PROBE_CLASSES, PROBE_PATTERNS)
    start_file(out, 4, "Ambire Ice SWD jig: probe")
    board = pcbnew.LoadBoard(out)
    rounded_rect(board, pcbnew.Edge_Cuts, *PROBE_OUTLINE)
    nets = {}
    place(board, PROBE, nets)
    zone(board, pcbnew.In1_Cu, nets["GND"], PROBE_OUTLINE)            # solid ground
    zone(board, pcbnew.In2_Cu, nets["V3V3_PROBE"], PROBE_OUTLINE)     # the probe's 3.3 V plane
    for s, x, y, size, side in PROBE_SILK:
        text(board, s, x, y, size, side)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(out, board)
    print(f"wrote {out}: {len(list(board.GetFootprints()))} parts, nets {len(nets)}")


def build_base():
    out = os.path.join(HERE, "base.kicad_pcb")
    write_project(os.path.join(HERE, "base.kicad_pro"), BASE_CLASSES, [])
    start_file(out, 2, "Ambire Ice SWD jig: base")
    board = pcbnew.LoadBoard(out)
    rounded_rect(board, pcbnew.Edge_Cuts, *BASE_OUTLINE)
    rounded_rect(board, pcbnew.F_SilkS, *DEVICE_OUTLINE, width=0.15)   # where the device lies
    nets = {}
    place(board, BASE, nets)
    for n, w, pts in BASE_TRACKS:
        for a, b in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pt(*a)); t.SetEnd(pt(*b)); t.SetWidth(mm(w)); t.SetLayer(pcbnew.F_Cu); t.SetNet(nets[n])
            board.Add(t)
    zone(board, pcbnew.F_Cu, nets["GND"], BASE_OUTLINE)
    for s, x, y, size, side in BASE_SILK:
        text(board, s, x, y, size, side)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(out, board)
    pads = {f"{f.GetReference()}.{p.GetNumber()}": (round(p.GetPosition().x / 1e6, 2), round(p.GetPosition().y / 1e6, 2))
            for f in board.GetFootprints() if f.GetReference() == "J_RAD" for p in f.Pads()}
    print(f"wrote {out}: {len(list(board.GetFootprints()))} parts; socket pads {pads}")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "probe":
        build_probe()
    elif what == "base":
        build_base()
    else:
        sys.exit(__doc__)
