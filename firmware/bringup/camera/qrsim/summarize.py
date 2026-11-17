#!/usr/bin/env python3
"""Decode rates from a sweep CSV (sweep.py), one table per experiment.

    summarize.py IN.csv [--by ppm|n,frac|method,ppm|version,ppm] [--out SUMMARY.csv]

Columns: the stock decoder per threshold mode, the fixed decoders (aff,
regrid, ext; "ext:<mode>" for ext with another threshold), and retries:
"otsu>X" decodes with Otsu, then (only when Otsu found a grid) with X.
"""
import argparse
import csv
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--by", default="cond,ppm")
    ap.add_argument("--out")
    ap.add_argument("--cols")
    a = ap.parse_args()
    by = a.by.split(",")

    frames = defaultdict(dict)  # (point key, trial) -> {column: row}
    point_of = {}
    for row in csv.DictReader(open(a.csv)):
        col = row["mode"] if row["decoder"] == "stock" else (
            row["decoder"] if row["mode"] == "otsu" else row["decoder"] + ":" + row["mode"])
        k = (row["key"], int(row["trial"]))
        frames[k][col] = row
        point_of[k] = tuple(row[b] for b in by)

    def dec(r, col):
        return col in r and r[col]["decoded"] == "1"

    stats = defaultdict(lambda: defaultdict(int))
    cols = set()
    for k, r in frames.items():
        g = point_of[k]
        stats[g]["n"] += 1
        stats[g]["fits"] += int(next(iter(r.values()))["fits"])
        for col in r:
            cols.add(col)
            stats[g][col] += dec(r, col)
        # Retries: Otsu first; only when it found a grid, the other settings
        for base in ("", "ext:"):
            first = "otsu" if base == "" else "ext"
            if first not in r:
                continue
            found = int(r[first]["grids"]) > 0
            for alts in (["otsu90"], ["otsu110"], ["box16"], ["box32"], ["rowavg"],
                         ["box32", "otsu110"]):
                if all(base + x in r for x in alts):
                    c = base + "otsu>" + ">".join(alts)
                    cols.add(c)
                    stats[g][c] += dec(r, first) or (found and any(dec(r, base + x) for x in alts))
    order = ["otsu", "fixed", "rowavg", "box8", "box16", "box32", "box16t10", "otsu90", "otsu110",
             "otsu>otsu90", "otsu>otsu110", "otsu>box16", "otsu>box32", "otsu>rowavg"]
    cols = [c for c in order if c in cols] + sorted(c for c in cols if c not in order)
    if a.cols:
        cols = [c for c in a.cols.split(",") if c in cols]

    def numkey(g):
        return tuple(float(x) if x.replace(".", "").isdigit() else x for x in g)

    print("  ".join(["%-8s" % b for b in by] + ["%4s" % "n"] + ["%7s" % c[:11] for c in cols]))
    out = []
    for g in sorted(stats, key=numkey):
        s = stats[g]
        rates = {c: 100.0 * s[c] / s["n"] for c in cols}
        print("  ".join(["%-8s" % x for x in g] + ["%4d" % s["n"]] +
                        ["%7.0f" % rates[c] for c in cols]) +
              ("" if s["fits"] == s["n"] else "  (fits %d)" % s["fits"]))
        out.append(dict(zip(by, g), n=s["n"], **{c: round(rates[c], 1) for c in cols}))
    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=by + ["n"] + cols)
            w.writeheader()
            w.writerows(out)


if __name__ == "__main__":
    main()
