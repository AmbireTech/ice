#!/usr/bin/env python3
"""Decode-rate sweeps over simulated camera frames (scene.py), decoded by the
firmware's quirc in build/harness (and its variants, build.sh).

    sweep.py EXPERIMENT OUT.csv [--trials N] [--jobs J]

Every row of OUT.csv is one frame and one decoder setting; rows are appended
as each batch finishes, and a rerun skips the (point, trial) pairs already in
the file.
"""
import argparse
import csv
import os
import random
import struct
import subprocess
import sys
import zlib
from multiprocessing import Pool

import scene

HERE = os.path.dirname(os.path.abspath(__file__))
MODES = ["otsu", "otsu90", "otsu110", "fixed", "rowavg", "box8", "box16", "box32", "box16t10"]


def harnesses():
    """name -> executable: the stock decoder and the variants build.sh made"""
    out = {"stock": os.path.join(HERE, "build", "harness")}
    for name in sorted(os.listdir(os.path.join(HERE, "build"))):
        if name.startswith("harness-"):
            out[name[len("harness-"):]] = os.path.join(HERE, "build", name)
    return out


# Each experiment: a list of points (dicts); every point gets `trials` scenes
def points(exp):
    pts = []
    if exp == "ppm":
        # (a), (d), (e), (f): decode rate against pixels per module, native
        for cond in ("normal", "uneven"):
            for ppm in (1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0, 5.0):
                pts.append(dict(method="native", n=480, ppm=ppm, cond=cond, version=11, prev=1))
    elif exp == "window":
        # (b): the code spans 60-85 % of the window (handheld), window size N
        for n in (240, 288, 320, 384, 400, 480):
            for frac in (0.6, 0.7, 0.85):
                pts.append(dict(method="native", n=n, frac=frac, cond="normal", version=11))
        for frac in (0.6, 0.7, 0.85):
            pts.append(dict(method="vga", n=480, frac=frac, cond="normal", version=11))
    elif exp == "wide":
        # (c): the same field of view by binning, skipping or a box scaler,
        # at equal output pixels per module
        for ppm in (1.5, 1.75, 2.0, 2.5, 3.0):
            for method, n in (("bin2", 384), ("skip2", 384), ("area768", 384),
                              ("skip3", 240), ("area720", 240), ("area600", 384),
                              ("native", 384)):
                pts.append(dict(method=method, n=n, ppm=ppm, cond="normal", version=11))
    elif exp == "version":
        # Margin: longer parts than the app's (versions 12-14)
        for version in (11, 12, 13, 14):
            for ppm in (2.0, 2.5, 3.0, 3.5, 4.0):
                pts.append(dict(method="native", n=384, ppm=ppm, cond="normal", version=version))
    else:
        raise SystemExit("unknown experiment " + exp)
    return pts


def key(pt):
    return "|".join("%s=%s" % (k, pt[k]) for k in sorted(pt))


def run_batch(args):
    pt, trials, modes_all = args
    recs = []
    meta = []
    for t in trials:
        rng = random.Random(zlib.crc32(("%s#%d" % (key(pt), t)).encode()))
        matrix, version = scene.qr_matrix(scene.ur_text(rng, pt["version"]))
        p = scene.scene_params(rng, uneven=pt["cond"] == "uneven")
        size = matrix.shape[0]
        if "frac" in pt:
            ppm = pt["frac"] * pt["n"] / size
        else:
            ppm = pt["ppm"]
        f = scene.frame(matrix, p, pt["method"], pt["n"], ppm)
        fixed = -1
        if pt.get("prev"):
            # The previous frame: another part, exposure a little different,
            # the hand moved a little
            q = dict(p)
            q.update(white=p["white"] * rng.uniform(0.9, 1.1), seed=p["seed"] ^ 1,
                     pos=(min(1, max(0, p["pos"][0] + rng.uniform(-0.1, 0.1))),
                          min(1, max(0, p["pos"][1] + rng.uniform(-0.1, 0.1)))))
            m2, _ = scene.qr_matrix(scene.ur_text(rng, pt["version"]))
            fixed = scene.otsu(scene.frame(m2, q, pt["method"], pt["n"], ppm))
        recs.append(struct.pack("<4i", f.shape[1], f.shape[0], fixed, t) + f.tobytes())
        meta.append((t, version, ppm, p["fits"], p["sigma"], p["motion"], p["tilt"], p["theta"]))
    data = b"".join(recs)
    rows = []
    for hname, exe in harnesses().items():
        modes = modes_all if hname in ("stock", "ext") else ["otsu"]
        res = subprocess.run([exe] + modes, input=data, capture_output=True)
        if res.returncode != 0:
            raise RuntimeError("%s failed: %s" % (exe, res.stderr.decode()))
        out = {}
        for line in res.stdout.decode().split("\n"):
            if line:
                f = line.split()
                out[(int(f[0]), f[1])] = f[2:]
        for t, version, ppm, fits, sigma, motion, tilt, theta in meta:
            for mode in modes:
                grids, decoded, err, _, thr, regions, caps = out[(t, mode)]
                rows.append(dict(pt, key=key(pt), trial=t, decoder=hname, mode=mode,
                                 version=version, ppm=round(ppm, 3), fits=int(fits),
                                 sigma=round(sigma, 2), motion=round(motion, 2),
                                 tilt=round(tilt, 1), theta=round(theta, 1), grids=grids,
                                 decoded=decoded, err=err, otsu=thr, regions=regions,
                                 capstones=caps))
    return rows


FIELDS = ["key", "method", "n", "ppm", "frac", "cond", "version", "prev", "trial", "decoder",
          "mode", "fits", "sigma", "motion", "tilt", "theta", "grids", "decoded", "err",
          "otsu", "regions", "capstones"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("experiment")
    ap.add_argument("out")
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--batch", type=int, default=5)
    ap.add_argument("--modes", default=",".join(MODES),
                    help="threshold modes for the stock and ext decoders")
    a = ap.parse_args()

    done = set()
    if os.path.exists(a.out):
        with open(a.out) as f:
            for row in csv.DictReader(f):
                done.add((row["key"], int(row["trial"])))
    work = []
    for pt in points(a.experiment):
        todo = [t for t in range(a.trials) if (key(pt), t) not in done]
        for i in range(0, len(todo), a.batch):
            work.append((pt, todo[i:i + a.batch], a.modes.split(",")))
    new = not os.path.exists(a.out)
    with open(a.out, "a", newline="") as f, Pool(a.jobs) as pool:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        for i, rows in enumerate(pool.imap_unordered(run_batch, work)):
            w.writerows(rows)
            f.flush()
            if i % 20 == 0:
                print("%d/%d batches" % (i + 1, len(work)), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
