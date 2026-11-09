#!/usr/bin/env python3
"""Find Trezor branding left in the Ambire build.

    firmware/branding/check_strings.py                  # translations only
    firmware/branding/check_strings.py out/ambire_p0    # and the built binaries

1. Every English string the Delizia layout shows (core/translations/en.json,
   the "Delizia" variant where a string has one) that names Trezor, its app
   (Suite) or trezor.io must be reworded in ambire_strings.rs. Run it after an
   upstream sync: a new string naming Trezor fails here.
2. With a directory, checks every "Trezor" in its *.bin files that reads as a
   word (not a module path or identifier such as trezor/ui or trezorio). The
   upstream English of a reworded string is still in the binary (never shown),
   and KEPT lists what stays on purpose; anything else is printed with the
   text around it, for a person to judge.

Exit status 1 when a string in (1) is not reworded.
"""
import json
import re
import sys
from pathlib import Path

FW = Path(__file__).resolve().parent.parent / "trezor-firmware"
EN = FW / "core/translations/en.json"
OVERRIDES = FW / "core/embed/rust/src/translations/ambire_strings.rs"
BRAND = re.compile(r"trezor|suite", re.I)
# "Trezor" as a word: not inside an identifier or a module path (trezor.ui),
# but trezor.io counts
WORD = re.compile(rb"(?i)(?<![A-Za-z0-9_/.])trezor(?![A-Za-z0-9_/]|\.(?!io)[a-z])")
# Kept on purpose (firmware/README.md, "Trezor's name, where it stays")
KEPT = {
    rb"Trezor FIDO (Root CA|EE) Serial": "FIDO attestation certificate",
    rb"Trezor Steakhouse": "names of third-party Ethereum vaults",
    rb"(?<![\x20-\x7e])trezor\.io(?![\x20-\x7e])": "Features.vendor (trezorlib)",
    rb"Trezor Vendor Header": "vendor header hash domain",
    rb"TREZOR Interface": "upstream default, replaced by MODEL_USB_INTERFACE",
    rb"Trezor Core enforces": "error message to the host",
}


def main():
    en = json.loads(EN.read_text())["translations"]
    done = set(re.findall(r"TR::(\w+) =>", OVERRIDES.read_text()))
    missing, reworded = [], []
    for key, value in en.items():
        text = value.get("Delizia", "") if isinstance(value, dict) else value
        if BRAND.search(text) and key not in done:
            missing.append(f"  {key}: {text!r}")
        if key in done and text:
            reworded.append(text.encode())
    if missing:
        print(f"Not reworded in {OVERRIDES.name}:", *missing, sep="\n")
    else:
        print(f"translations: every Delizia string naming Trezor is reworded ({len(done)})")

    for d in sys.argv[1:]:
        for binfile in sorted(Path(d).glob("*.bin")):
            data = binfile.read_bytes()
            # spans that are never shown: reworded English, kept on purpose
            spans = [m.span() for t in reworded for m in re.finditer(re.escape(t), data)]
            kept = {}
            for pattern, why in KEPT.items():
                for m in re.finditer(pattern, data):
                    spans.append(m.span())
                    kept[why] = kept.get(why, 0) + 1
            left = sorted({
                data[max(m.start() - 40, 0):m.end() + 40].decode("ascii", "replace")
                for m in WORD.finditer(data)
                if not any(a <= m.start() < b for a, b in spans)
            })
            print(f"{binfile.name}: {len(left)} unexplained 'Trezor'"
                  + "".join(f"; {n}x {why}" for why, n in kept.items()))
            for h in left:
                print("  " + re.sub(r"[^\x20-\x7e]", ".", h))
    sys.exit(1 if missing else 0)


if __name__ == "__main__":
    main()
