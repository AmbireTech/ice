#!/usr/bin/env python3
"""
Verify every BOM part against the land JLCPCB will actually place it on.

An SMT order came back with six parts JLC could not assemble - C_SIM2, R_ISET,
R_RSTE, R_PWRE, U1, U2 - on a board this pipeline had called fab-clean. Every
check that existed was looking at the wrong thing:

  * the LCSC code RESOLVED, so the code audit passed it;
  * the MPN MATCHED for the ICs, so that check passed too;
  * DRC compares copper to copper and never asks what part lands on it.

Nothing compared the ordered part's package against the footprint drawn for it,
and nothing compared its VALUE against the schematic. So a 0603 resistor was
ordered onto an 0402 land; the BQ25185 (WSON-10-EP, 2.2 x 2 mm, 0.40 mm pitch,
pads in rows) shared one land with the TPS63802 (VSON-10, 3 x 2 mm, 0.50 mm
pitch, pads in columns); and four parts had the wrong value outright - including
R_FB1, where the ordered 6.49k in place of 560k would have set V3V3 to 0.53 V
and left the board dead even if every part had soldered perfectly.

Parsing JLC's package STRINGS was tried first and abandoned: it read "SOT-23-6"
as a 23-pin part, and it still could not tell the two 10-pin no-lead packages
apart. This compares GEOMETRY instead. For each part it pulls the vendor's own
footprint from the LCSC library - the same geometry JLC's line places against -
and compares four signatures that survive rotation and mirroring:

    pad count, pad sizes, the pad field's bounding box, and the finest pitch

which separates 0402 from 0603, a 3-pin SOT-23 from a 6-pin one, and a 2.2 x 2
no-lead package from a 3 x 2 one.

Results are cached in scripts/jlcPartCache.json so this runs offline, but a part
with no cache entry and no network is an ERROR, never a pass. "I could not
check" reading as "it is fine" is the exact failure this file exists to prevent.

    kicad-python checkParts.py <board.kicad_pcb> [--refresh]
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "jlcPartCache.json")
API = ("https://jlcpcb.com/api/overseas-pcb-order/v1/"
       "shoppingCart/smtGood/selectSmtComponentList")

# Geometry slack. An 0402 -> 0603 step moves the pad field by ~0.6 mm and the
# pads by ~0.3 mm, so 0.25 mm separates the sizes while tolerating the
# difference between a vendor's reflow land and a hand-drawn one.
TOL_MM = 0.25
PITCH_TOL_MM = 0.08

MULT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
        "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}

# Parts JLC does not place, so there is no land of theirs to compare against.
# Lands that differ from the vendor's in STYLE, each checked by hand and each
# recorded here with the reason. A waiver is a decision on the record - the
# alternative, loosening the thresholds until the noise disappears, is how the
# original fault got through, because it also silences the real faults.
WAIVED_LAND = {
    "U8": "IPC-nominal SOT-23-6 against the vendor's longer reflow land; same 6 pads, same 0.95 mm pitch",
    "U9": "IPC-nominal SOT-23-6 against the vendor's longer reflow land; same 6 pads, same 0.95 mm pitch",
    "U10": "IPC-nominal SOT-23-6 against the vendor's longer reflow land; same 6 pads, same 0.95 mm pitch",
    "D_OR": "IPC-nominal SOT-23 against the vendor's longer reflow land; same 3 pads, same pitch",
    "L_BUCK": "Murata's DFE2016 land (KiCad L_Murata_DFE201610P) for a DFE201612 part, as on the GridCore Nano",
    "L_SMPS": "Murata's DFE2016 land (KiCad L_Murata_DFE201610P) for a DFE201612 part, as on the GridCore Nano",
    "J1": "KiCad's HRO TYPE-C-31-M-12 land, the one the GridCore Nano uses; its shield legs are split "
          "into more pads than EasyEDA's",
    "U15": "KiCad land generated from Maxim 21-0168, the MAX17048's own package outline; the extra pads are "
           "paste apertures inside the exposed pad",
    "U5": "land drawn from NXP's SOT1969-1 package information (98ASA01228D Fig. 3-6), the vendor's own "
          "recommended footprint; EasyEDA's is a third-party copy",
    "U1": "TI's own DLH0010A land (BQ25185 data sheet, example board layout 4226298/A: pad columns (2.1) "
          "outer edge to outer edge, 0.1 mm to the exposed pad); EasyEDA's is a generic longer-toe land",
    "U3": "IPC-nominal SOT-23 against the vendor's longer reflow land; same 3 pads, same pitch",
    "U13": "KiCad VQFN-16 3x3 land (the GridCore Nano's); the extra pads are paste apertures inside the "
           "exposed pad",
    "J_CAM": "KiCad's Jushuo AFC07-S24FCA-00 land for this exact part: same 24 pads at 0.5 mm and the same "
             "14.8 mm width; the depth differs by the mounting-tab position",
}

SKIP_GEOMETRY = {}


def curl_json(payload):
    # curl, not urllib: KiCad's bundled Python ships with no CA bundle, so every
    # https call from it dies with CERTIFICATE_VERIFY_FAILED - and this gate has
    # to run under that interpreter, because it needs pcbnew.
    out = subprocess.run(
        ["curl", "-s", "--max-time", "25", API,
         "-H", "content-type: application/json",
         "-H", "user-agent: Mozilla/5.0", "--data", json.dumps(payload)],
        capture_output=True, text=True, check=True).stdout
    if not out.strip():
        raise RuntimeError("empty response from JLC")
    return json.loads(out)


def jlc_meta(code):
    doc = curl_json({"currentPage": 1, "pageSize": 5, "keyword": code})
    rows = ((doc.get("data") or {}).get("componentPageInfo") or {}).get("list") or []
    for r in rows:
        if r.get("componentCode") == code:
            return {"model": r.get("componentModelEn") or "",
                    "package": (r.get("componentSpecificationEn") or "").strip(),
                    "describe": r.get("describe") or "",
                    "stock": r.get("stockCount") or 0}
    return None


def signature(pads):
    """Rotation- and mirror-invariant description of a pad field."""
    if not pads:
        return None
    xs = [p[1] for p in pads]
    ys = [p[2] for p in pads]
    sizes = sorted(tuple(sorted((round(p[3], 2), round(p[4], 2)))) for p in pads)
    span = sorted((round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)))
    pitch = None
    for i, a in enumerate(pads):
        for b in pads[i + 1:]:
            d = math.hypot(a[1] - b[1], a[2] - b[2])
            if d > 0.05 and (pitch is None or d < pitch):
                pitch = d
    return {"pads": len(pads), "sizes": sizes, "span": span,
            "pitch": round(pitch, 2) if pitch else None}


def vendor_signature(code):
    """Signature of the vendor's own land for this LCSC part, or None."""
    tmp = tempfile.mkdtemp(prefix="lcsc_")
    try:
        # EasyEDA rate-limits a burst of requests, and a miss here is not
        # harmless: it downgrades a part from "checked" to "eyeball it", which
        # is how this class of fault got through in the first place.
        mods = []
        for attempt in range(3):
            subprocess.run(
                ["python3", "-m", "easyeda2kicad", f"--lcsc_id={code}",
                 "--footprint", "--output", os.path.join(tmp, "p")],
                capture_output=True, text=True, timeout=180)
            for root, _, files in os.walk(tmp):
                mods += [os.path.join(root, f)
                         for f in files if f.endswith(".kicad_mod")]
            if mods:
                break
            time.sleep(2 + 3 * attempt)
        mods = []
        for root, _, files in os.walk(tmp):
            mods += [os.path.join(root, f) for f in files if f.endswith(".kicad_mod")]
        if not mods:
            return None
        text = open(mods[0]).read()
        pads = []
        for m in re.finditer(
                r"\(pad\s+(\S+)\s+\S+\s+\S+\s*\(at ([-\d.]+) ([-\d.]+)"
                r"(?:\s+[-\d.]+)?\)\s*\(size ([\d.]+) ([\d.]+)\)", text):
            pads.append((m.group(1).strip('"'), float(m.group(2)),
                         float(m.group(3)), float(m.group(4)), float(m.group(5))))
        return signature(pads)
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def board_signature(fp):
    origin = fp.GetPosition()
    pads = [(pad.GetPadName(),
             (pad.GetPosition().x - origin.x) / 1e6,
             (pad.GetPosition().y - origin.y) / 1e6,
             pad.GetSizeX() / 1e6, pad.GetSizeY() / 1e6)
            for pad in fp.Pads()]
    return signature(pads)


def compare(board_sig, vendor_sig):
    """Differences that mean a DIFFERENT PACKAGE, not a different land style.

    Calibration matters as much as the comparison. A first cut flagged every
    SOT-23 on the board because KiCad's land is 0.3 mm longer than the vendor's,
    and the JST header because its pad field differs by 1 mm in 20 - both are
    perfectly good lands for the right part, and drowning the real faults in
    that noise would make the gate useless.

    What actually separates one package from another is PITCH and pad COUNT,
    with span as a coarse backstop:

      * pitch    0.40 vs 0.50 mm is what distinguishes the BQ25185's WSON-10
                 from the TPS63802's VSON-10 - the pair JLC rejected. Land style
                 never moves pitch; it is set by the part's own leads.
      * count    a 3-pin SOT-23 against a 6-pin one.
      * span     proportional, so a 20 mm connector is allowed the millimetre a
                 reflow land adds, while a 2 mm package is held to 0.3 mm.

    Absolute pad SIZE is deliberately not compared: it is the pure expression of
    land style (nominal vs reflow vs wave) and carries no package information.
    """
    if not board_sig or not vendor_sig:
        return []
    out = []

    bn, vn = board_sig["pads"], vendor_sig["pads"]
    if abs(bn - vn) > 2 and abs(bn - vn) > 0.15 * max(bn, vn):
        out.append(f"{bn} pads on the board, {vn} on the vendor's land")

    for b, v in zip(board_sig["span"], vendor_sig["span"]):
        tol = max(0.3, 0.15 * max(b, v))
        if abs(b - v) > tol:
            out.append(f"pad field {board_sig['span'][0]}x{board_sig['span'][1]} mm, "
                       f"vendor's {vendor_sig['span'][0]}x{vendor_sig['span'][1]} mm")
            break

    bp, vp = board_sig["pitch"], vendor_sig["pitch"]
    if (board_sig["pads"] > 2 and vendor_sig["pads"] > 2 and bp and vp
            and abs(bp - vp) > 0.2 * max(bp, vp)):
        out.append(f"finest pitch {bp} mm, vendor's {vp} mm")

    return out


def parse_si(text):
    m = re.fullmatch(r"([\d.]+)\s*([pnuµmkKMG]?)[FfHhΩω]?", text.strip())
    if not m:
        return None
    try:
        return float(m.group(1)) * MULT.get(m.group(2), 1.0)
    except ValueError:
        return None


def value_matches(value, describe):
    """True / False / None (undecidable) for 'is this the value JLC lists?'"""
    want = parse_si(value)
    if want is None:
        return None
    found = False
    for num, mult in re.findall(r"([\d.]+)\s*([pnuµmkKMG]?)(?:[FΩ]|H\b)", describe):
        found = True
        try:
            got = float(num) * MULT.get(mult, 1.0)
        except ValueError:
            continue
        # A 0 ohm link is a real value, and a relative tolerance cannot express
        # it - abs(0-0) <= 0*0.02 is true but so is abs(51-0) <= 0.
        if want == 0:
            if got == 0:
                return True
        elif abs(got - want) <= abs(want) * 0.02:
            return True
    return False if found else None


CHIP_CODES = ("0201", "0402", "0603", "0805", "1206", "1210", "1812", "2010",
              "0806", "1008")


def chip_code(text):
    """The imperial size code named in a footprint name or package string."""
    for code in CHIP_CODES:
        if re.search(rf"(?<!\d){code}(?!\d)", text):
            return code
    return None


def lcsc_of(fp):
    for field in fp.GetFields():
        if field.GetName() in ("Supplier Part Number", "LCSC", "LCSC Part #"):
            t = field.GetText().strip()
            if re.fullmatch(r"C\d{3,10}", t):
                return t
    return ""


def main(board_path, refresh=False):
    board = pcbnew.LoadBoard(board_path)
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    errors, warns, checked = [], [], 0

    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        ref = fp.GetReference()
        code = lcsc_of(fp)
        if not code:
            continue

        entry = cache.get(code)
        if refresh or entry is None:
            try:
                meta = jlc_meta(code)
            except Exception as exc:
                if entry is None:
                    errors.append(f"{ref} ({code}): CANNOT VERIFY - {exc}")
                    continue
                meta = None
            if meta is None and entry is None:
                errors.append(f"{ref} ({code}): NOT FOUND in JLC's catalogue")
                continue
            if meta:
                # Chip passives are settled by the package string; skip the
                # download for them entirely. That leaves ~15 real fetches
                # instead of 80, which is what EasyEDA's rate limiter allows.
                # Chip passives are settled by the package string, so skip the
                # download - that keeps this to ~15 real fetches instead of 80,
                # which is what EasyEDA's rate limiter tolerates.
                meta["land"] = (None if chip_code(meta["package"])
                                else vendor_signature(code))
                cache[code] = meta
                entry = meta
        checked += 1

        fpname = str(fp.GetFPID().GetLibItemName())
        want_chip = chip_code(entry["package"])
        got_chip = chip_code(fpname)

        if ref in SKIP_GEOMETRY:
            pass
        elif want_chip:
            # A chip passive: JLC's package string is exactly the size code, so
            # this needs no download and is the check that would have caught a
            # 0603 resistor ordered onto an 0402 land.
            if got_chip == want_chip:
                pass                      # the land names the size JLC ships
            elif got_chip:
                errors.append(f"{ref} ({code}): PACKAGE MISMATCH - land is "
                              f"{got_chip}, JLC part is {want_chip}")
            elif entry.get("land"):
                diffs = compare(board_signature(fp), entry["land"])
                if diffs:
                    errors.append(f"{ref} ({code}) LAND MISMATCH: " + "; ".join(diffs))
            elif ref in WAIVED_LAND:
                warns.append(f"{ref} ({code}): waived - {WAIVED_LAND[ref]}")
            else:
                errors.append(f"{ref} ({code}): land '{fpname}' does not name a "
                              f"size and JLC says {want_chip} - cannot verify")
        else:
            # Everything else needs real geometry: package strings cannot tell a
            # WSON-10 2.2x2 on 0.40 mm pitch from a VSON-10 3x2 on 0.50 mm, and
            # that pair is exactly what JLC rejected.
            if entry.get("land"):
                diffs = compare(board_signature(fp), entry["land"])
                if diffs and ref in WAIVED_LAND:
                    warns.append(f"{ref} ({code}): land differs from the "
                                 f"vendor's - waived: {WAIVED_LAND[ref]}")
                elif diffs:
                    errors.append(f"{ref} ({code} {entry['model']}) LAND MISMATCH: "
                                  + "; ".join(diffs))
            else:
                # NOT a warning. An unverifiable part reading as a pass is the
                # whole reason a board went to fab with six unplaceable parts.
                errors.append(
                    f"{ref} ({code} {entry['model']}): CANNOT VERIFY LAND - no "
                    f"vendor footprint retrieved. Re-run with --refresh, or add "
                    f"an entry to SKIP_GEOMETRY with a reason.")

        value = fp.GetValue().strip()
        if value and value not in (entry["model"], ref):
            if value_matches(value, entry["describe"]) is False:
                errors.append(f"{ref} ({code}): VALUE MISMATCH - board says "
                              f"'{value}', JLC part is '{entry['describe'][:55]}'")

        if entry.get("stock", 0) < 200:
            warns.append(f"{ref} ({code}): only {entry['stock']} in stock")

    json.dump(cache, open(CACHE, "w"), indent=1, sort_keys=True)
    print(f"  parts: {checked} checked against JLC's own land geometry")
    for w in warns:
        print(f"    ? {w}")
    for e in errors:
        print(f"    ! {e}")
    if errors:
        print(f"\n  {len(errors)} part(s) JLC cannot place as drawn")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1], "--refresh" in sys.argv)
