#!/usr/bin/env python3
"""Turn work/parts-raw.csv (from export_fab.py) into the JLC BOM + CPL, query JLC stock, run parity checks.

    python3 -I build_jlc_files.py <outdir> [boards=10]

Writes into <outdir>:
  wallet_bom-jlc.csv        Comment, Designator, Footprint, LCSC Part #        (upload)
  wallet_positions-jlc.csv  Designator, Mid X, Mid Y, Layer, Rotation          (upload)
  work/stock.csv            JLC cart API reading per LCSC code
  work/rotation-review.csv  every placed part, its rotation provenance and whether it needs an eye check
  work/excluded.csv         footprints left out of both files, and why
  hand-fit.csv              parts fitted by hand (no footprint on the board)
"""
import csv
import json
import os
import subprocess
import sys
import time
from collections import OrderedDict

API = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
DEPRIORITIZED = {"C1518668": "OPTIGA Trust M V3: sourcing deprioritized (known 0 at JLC)"}
# Not stocked by JLC: bought into the order through JLC Global Sourcing (distributor stock,
# JLC mounts it, never shipped to us). Checked 2026-10-31; recheck the offer at order time.
GLOBAL_SOURCING = {"C21905480": "global sourcing: DigiKey ED10763CT-ND cut tape, 117,259 in stock, MOQ 1, 8-14 business days"}

# Hand-fitted, no footprint on the board (docs/design.md @ 49053dc, README "fitted by hand").
HAND_FIT = [
    ("PANEL", "NHD-2.4-240320AF-CSXP-CTP", "Newhaven 2.4in IPS 240x320 + CTP; tails into J_TFT (Molex 54132-4062) and J_CTP (6P 1.0 mm FPC)"),
    ("CAMERA", "Camemake CM_DVP_GC2145_UNIVERSAL (GC2145 24-pin DVP module)", "no refocus; flex into J_CAM (Jushuo AFC07-S24FCA-00)"),
    ("CELL", "one AAA cell (NiMH, alkaline or lithium primary)", "snapped into the front clips BT_N/BT_P (MYOUNG MY-AAA-19-J / -20-J); + end at BT_P"),
]

# Polarity-free two-terminal chips: JLC and KiCad share the 0-degree reference, no correction exists or is needed.
NONPOLAR_PREFIX = ("C_", "R_", "L_")


def curl_json(payload):
    for attempt in range(3):
        try:
            out = subprocess.run(["curl", "-s", "--max-time", "25", API, "-H", "content-type: application/json",
                                  "-H", "user-agent: Mozilla/5.0", "--data", json.dumps(payload)],
                                 capture_output=True, text=True, check=True).stdout
            return json.loads(out)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            print(f"  JLC API attempt {attempt + 1} for {payload['keyword']} failed: {exc!r}", file=sys.stderr)
            time.sleep(2 + 2 * attempt)
    raise RuntimeError(f"JLC API unreachable for {payload['keyword']}")


def lookup(code):
    doc = curl_json({"keyword": code, "currentPage": 1, "pageSize": 5})
    for c in ((doc.get("data") or {}).get("componentPageInfo") or {}).get("list") or []:
        if c.get("componentCode") == code:
            return c
    return None


def unit_price(rec, qty):
    for tier in rec.get("componentPrices") or []:
        if tier["startNumber"] <= qty and (tier["endNumber"] == -1 or qty <= tier["endNumber"]):
            return tier["productPrice"]
    prices = rec.get("componentPrices") or []
    return prices[0]["productPrice"] if prices else None


def main():
    outdir = sys.argv[1]
    boards = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    work = os.path.join(outdir, "work")
    parts = list(csv.DictReader(open(os.path.join(work, "parts-raw.csv"))))

    placed, excluded = [], []
    for p in parts:
        why = []
        if p["ExcludeBOM"] == "1":
            why.append("exclude-from-BOM")
        if p["DNP"] == "1":
            why.append("DNP")
        if p["BoardOnly"] == "1":
            why.append("board-only")
        if p["ExcludePos"] == "1":
            why.append("exclude-from-pos")
        if why:
            excluded.append({**p, "Why": ",".join(why)})
        else:
            placed.append(p)

    # ---- BOM: group by (value, footprint, LCSC)
    groups = OrderedDict()
    for p in placed:
        groups.setdefault((p["Value"], p["Footprint"], p["LCSC"]), []).append(p["Designator"])
    bom = [{"Comment": v, "Designator": ",".join(sorted(refs)), "Footprint": fp, "LCSC Part #": code}
           for (v, fp, code), refs in sorted(groups.items(), key=lambda kv: sorted(kv[1])[0])]
    with open(os.path.join(outdir, "wallet_bom-jlc.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Comment", "Designator", "Footprint", "LCSC Part #"])
        w.writeheader()
        w.writerows(bom)

    # ---- CPL (JLC header order)
    with open(os.path.join(outdir, "wallet_positions-jlc.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        w.writeheader()
        for p in sorted(placed, key=lambda r: r["Designator"]):
            w.writerow({"Designator": p["Designator"], "Mid X": p["Mid X"], "Mid Y": p["Mid Y"],
                        "Layer": "Bottom" if p["Layer"] == "bottom" else "Top", "Rotation": p["Rotation"]})

    with open(os.path.join(work, "excluded.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Designator", "Value", "FootprintFull", "LCSC", "Why"])
        w.writeheader()
        for p in excluded:
            w.writerow({k: p[k] for k in ("Designator", "Value", "FootprintFull", "LCSC", "Why")})
    with open(os.path.join(outdir, "hand-fit.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Item", "Part", "Notes"])
        w.writerows(HAND_FIT)

    # ---- rotation review
    review = []
    for p in sorted(placed, key=lambda r: r["Designator"]):
        nonpolar = p["Designator"].startswith(NONPOLAR_PREFIX) and p["Pads"] == "2"
        if p["DbRule"]:
            status = "db-rule (eye check: never seen on a GridCore BOTTOM part with a 90/270 offset)" \
                if p["Layer"] == "bottom" and float(p["DbOffset"]) % 180 else "db-rule"
        elif nonpolar:
            status = "n/a (non-polar 2-terminal)"
        else:
            status = "NO KNOWN CORRECTION -> eye check in JLC preview"
        review.append({"Designator": p["Designator"], "Value": p["Value"], "Footprint": p["FootprintFull"],
                       "LCSC": p["LCSC"], "Layer": p["Layer"], "BoardAngle": p["BoardAngle"],
                       "DbRule": p["DbRule"], "DbOffset": p["DbOffset"], "CPLRotation": p["Rotation"],
                       "AnchorToPadCentreMm": p["AnchorToPadCentreMm"], "Status": status})
    with open(os.path.join(work, "rotation-review.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(review[0]))
        w.writeheader()
        w.writerows(review)

    # ---- stock
    qty_per = OrderedDict()
    refs_of = {}
    for p in placed:
        if p["LCSC"]:
            qty_per[p["LCSC"]] = qty_per.get(p["LCSC"], 0) + 1
            refs_of.setdefault(p["LCSC"], []).append(p["Designator"])
    stock_rows = []
    for code in sorted(qty_per):
        rec = lookup(code)
        need = qty_per[code] * boards
        if rec is None:
            stock_rows.append({"LCSC": code, "Refs": ",".join(sorted(refs_of[code])), "PerBoard": qty_per[code],
                               "Need": need, "Stock": "", "Type": "", "MPN": "", "Package": "", "UnitUSD": "",
                               "Flag": "NOT FOUND at JLC"})
            continue
        stock = rec.get("stockCount") or 0
        lib = {"base": "Basic", "expand": "Extended"}.get(rec.get("componentLibraryType"), rec.get("componentLibraryType"))
        if lib == "Extended" and rec.get("preferredComponentFlag"):
            lib = "Extended (preferred)"
        flag = ""
        if stock < need:
            flag = "OUT/SHORT"
        elif stock < 3 * need:
            flag = "LOW (<3x)"
        if code in DEPRIORITIZED:
            flag = (flag + "; " if flag else "") + DEPRIORITIZED[code]
        if code in GLOBAL_SOURCING:
            flag = GLOBAL_SOURCING[code]
        stock_rows.append({"LCSC": code, "Refs": ",".join(sorted(refs_of[code])), "PerBoard": qty_per[code],
                           "Need": need, "Stock": stock, "Type": lib, "MPN": rec.get("componentModelEn") or "",
                           "Package": rec.get("componentSpecificationEn") or "",
                           "UnitUSD": unit_price(rec, need), "PresaleNum": rec.get("canPresaleNumber"),
                           "Flag": flag})
        time.sleep(0.3)
    fields = ["LCSC", "MPN", "Package", "Type", "Stock", "PresaleNum", "PerBoard", "Need", "UnitUSD", "Flag", "Refs"]
    with open(os.path.join(work, "stock.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(stock_rows)

    # ---- parity (expand groups, as ORDER-CHECKLIST item 16 says)
    bom_refs = {}
    for row in csv.DictReader(open(os.path.join(outdir, "wallet_bom-jlc.csv"))):
        for ref in (r.strip() for r in row["Designator"].split(",")):
            if ref:
                bom_refs[ref] = row["LCSC Part #"]
    cpl = {r["Designator"].strip() for r in csv.DictReader(open(os.path.join(outdir, "wallet_positions-jlc.csv")))}
    print(f"BOM refs {len(bom_refs)} in {len(bom)} lines; CPL rows {len(cpl)}; excluded {len(excluded)}")
    print("  CPL not BOM:", sorted(cpl - set(bom_refs)))
    print("  BOM not CPL:", sorted(set(bom_refs) - cpl))
    print("  no LCSC part:", sorted(r for r, c in bom_refs.items() if not c.strip()))
    print("  CPL layers:", sorted({r["Layer"] for r in csv.DictReader(open(os.path.join(outdir, "wallet_positions-jlc.csv")))}))
    print("excluded:", ", ".join(f"{p['Designator']}({p['Why']})" for p in excluded))
    print()
    print("stock (boards=%d):" % boards)
    for r in stock_rows:
        print(f"  {r['Flag'] or 'ok':12.12} {r['LCSC']:10} {str(r['Stock']):>9} need {r['Need']:>4} {r['Type']:20} "
              f"${r['UnitUSD']}  {r['MPN'][:26]:26} {r['Refs'][:60]}")
    print()
    print("rotation review:")
    for r in review:
        if not r["Status"].startswith("n/a"):
            print(f"  {r['Designator']:9} {r['Footprint'][:58]:58} board {r['BoardAngle']:>6} db {r['DbOffset']:>5} "
                  f"-> CPL {r['CPLRotation']:>5}  anchor-padctr {r['AnchorToPadCentreMm']}  {r['Status']}")


if __name__ == "__main__":
    main()
