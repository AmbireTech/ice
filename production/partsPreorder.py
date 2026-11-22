#!/usr/bin/env python3
"""What the JLC parts library holds for a run of Ice, what to buy, and (with --apply) the cart.

    python3 production/partsPreorder.py --boards 200            # plan: library vs need, live JLC stock
    python3 production/partsPreorder.py --boards 200 --apply    # also set the JLC parts cart to the shortfall
    python3 production/partsPreorder.py --boards 200 --all      # basic-library parts too

One Ice = one device board (production/order/wallet_bom-jlc.csv) + one battery pack
(production/pack-order/pack_bom-jlc.csv). Need per line = per board x boards + --spare.

Reads the library live from jlcpcb.com in a Chrome profile signed in with the owner's passkey
(headless when the saved session is still valid; otherwise a window opens and waits for the
passkey). The plan goes to production/preorder/plan-<boards>.csv, one row written as each part is
read, so an interrupted run keeps what it read.

Which lines go in the cart: every extended-library part the library cannot cover, plus any basic
part whose JLC stock cannot cover the run (JLC takes basic parts from its own stock at assembly;
--all adds every basic shortfall). A line JLC can only partly reserve from its own stock goes in as
a pre-order (JLC buys the balance). A part JLC has nothing left to reserve of (none in stock, or
oversold: a negative reservable count) stays out of the cart: buy it through JLC Global Sourcing
(or the maker, consigned to JLC) into the library.

--apply only edits the parts cart (lines already there are set to the target, never stacked;
lines of other projects are left alone). It never pays: check out in Parts Manager > Parts Cart.
"""
import argparse
import csv
import json
import os
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOMS = [("device", HERE / "order" / "wallet_bom-jlc.csv"), ("pack", HERE / "pack-order" / "pack_bom-jlc.csv")]
OUT_DIR = HERE / "preorder"
PROFILE = Path(os.environ.get("JLC_CHROME_PROFILE", Path.home() / ".gridcore" / "jlc-chrome"))
PACE = 1.2
# On the JLC BOM but bought by the owner outside JLC: never a shortfall here.
OWNER_SUPPLIED = {"C25254888": "the display, bought from Newhaven direct"}

SEARCH_URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
USER_URL = "/api/overseas-pcb-order/v1/getUserInfo"
SITE = "/api/overseas-smt-component-order-platform/v1/overseasSmtComponentOrder/"
ADD_URL = SITE + "smtComponentShopCart/addSmtComponentGoods"
UPD_URL = SITE + "smtComponentShopCart/updateSmtComponentCartGoods"
CART_LIST = SITE + "smtComponentShopCart/querySmtComponentCartGoods"
LIB_STOCK = SITE + "myLibrary/getCustomerComponentStock"
LIB_PAGE = "https://jlcpcb.com/user-center/smtPrivateLibrary/"
CART_PAGE = "https://jlcpcb.com/user-center/smtPrivateLibrary/partsCart/"


# ---------- BOM and JLC's public record ----------

def read_boms():
    """{lcsc: {per, mpn, refs, boards}} for one Ice (device + pack)."""
    lines = {}
    for board, path in BOMS:
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                code = row["LCSC Part #"].strip()
                refs = [r.strip() for r in row["Designator"].split(",") if r.strip()]
                L = lines.setdefault(code, {"per": 0, "mpn": row["Comment"], "refs": [], "boards": []})
                L["per"] += len(refs)
                L["refs"] += refs
                if board not in L["boards"]:
                    L["boards"].append(board)
    return lines


def jlc_record(code):
    """JLC's public catalog entry (stock, reservable, library type, ids), None if it has none."""
    body = json.dumps({"keyword": code, "currentPage": 1, "pageSize": 5}).encode()
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(SEARCH_URL, data=body, headers={"Content-Type": "application/json",
                                                                         "User-Agent": "Mozilla/5.0"})
            d = json.load(urllib.request.urlopen(req, timeout=30))
            hits = ((d.get("data") or {}).get("componentPageInfo") or {}).get("list") or []
            return next((h for h in hits if h.get("componentCode") == code), None)
        except (OSError, ValueError) as e:
            last = e
            print(f"   JLC search retry {attempt + 1}/3 for {code}: {e}", flush=True)
            time.sleep(2 + 2 * attempt)
    raise SystemExit(f"JLC search failed for {code}: {last}")


def unit_price(rec, qty):
    for band in rec.get("componentPrices") or []:
        if (band.get("startNumber") or 0) <= qty <= (band.get("endNumber") or 10 ** 9):
            return float(band.get("productPrice") or 0)
    return 0.0


def cart_body(rec, qty):
    """The site's own add-to-cart payload: from JLC stock (goodsType 5) or pre-order (6)."""
    can = int(rec.get("canPresaleNumber") or 0)
    qty = max(qty, int(rec.get("minPurchaseNum") or 1))
    preorder = qty > can
    if preorder:
        qty = max(qty, int(rec.get("preMinPurchaseNum") or 0))
    return {"goodsType": 6 if preorder else 5, "turnBuy": bool(preorder and can > 0),
            "smtComponentAddCartGoodsVOS": [{"componentCode": rec["componentCode"],
                                             "lcscComponentId": rec["componentId"], "presaleNumber": qty,
                                             "presaleType": "buy" if preorder else "stock"}]}, qty, preorder


# ---------- the signed-in browser ----------

FETCH_JS = """async ({method, url, payload}) => {
  const keyId = JSON.parse(localStorage.getItem('keyPair') || '{}').keyId || '';
  const xsrf = (document.cookie.split('; ').find(x => x.startsWith('XSRF-TOKEN=')) || '').slice(11);
  const opt = {method, credentials: 'include', headers: {'Content-Type': 'application/json',
               'Accept': 'application/json', 'secretkey': keyId, 'X-XSRF-TOKEN': xsrf}};
  if (payload != null) opt.body = JSON.stringify(payload);
  const r = await fetch(url, opt);
  const text = await r.text();
  try { return {http: r.status, json: JSON.parse(text)}; }
  catch { return {http: r.status, json: null, text: text.slice(0, 200)}; }
}"""


def page_json(page, method, url, payload=None):
    return page.evaluate(FETCH_JS, {"method": method, "url": url, "payload": payload})


def ok_data(r):
    """data of a real success; None for any failure (data:null with a message is a failure)."""
    d = r.get("json") or {}
    if r.get("http") != 200 or d.get("code") not in (200, None):
        return None
    if d.get("data") is None:
        return None if (d.get("message") or d.get("msg")) else {}
    return d["data"]


def fail_msg(r):
    d = r.get("json") or {}
    return d.get("msg") or d.get("message") or r.get("text") or f"HTTP {r.get('http')}"


def signed_in(page):
    try:
        return bool(ok_data(page_json(page, "GET", USER_URL)))
    except Exception as e:  # the page is mid-navigation; the caller polls again
        print(f"   sign-in probe failed ({type(e).__name__}: {str(e)[:80]}), retrying", flush=True)
        return False


def open_session(pw):
    """A page on jlcpcb.com with a live session: headless if the saved one holds, else a window."""
    for headless in (True, False):
        ctx = pw.chromium.launch_persistent_context(str(PROFILE), channel="chrome", headless=headless,
                                                    viewport={"width": 1280, "height": 900},
                                                    args=["--window-position=120,80", "--window-size=1320,960"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://jlcpcb.com/parts", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        if signed_in(page):
            return ctx, page
        if headless:
            print("Saved JLC session has expired: opening a window for the passkey sign-in.", flush=True)
            ctx.close()
            continue
        page.bring_to_front()
        print("Sign in with your passkey in the Chrome window (waits 15 min)…", flush=True)
        end = time.time() + 900
        while time.time() < end:
            if signed_in(page):
                return ctx, page
            time.sleep(3)
        ctx.close()
        raise SystemExit("timed out waiting for the JLC sign-in")


def read_library(page):
    """{lcsc: qty} held in the owner's JLC parts library: bought from JLC (privateStockCount), through
    Global Sourcing (overseasStockCount) and consigned (postStockCount). Parts published for resale
    (idleStockCount) are left out. Paged by pageNum (the endpoint ignores currentPage)."""
    page.goto(LIB_PAGE, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    lib, n, total = {}, 1, None
    while total is None or len(lib) < total:
        r = page_json(page, "GET", f"{LIB_STOCK}?pageNum={n}&pageSize=100")
        data = ok_data(r)
        if not data:
            raise SystemExit(f"could not read the parts library ({fail_msg(r)})")
        total = data.get("total") or 0
        rows = data.get("list") or []
        if not rows:
            break
        for row in rows:
            lib[row["componentCode"]] = sum(int(row.get(k) or 0) for k in
                                            ("privateStockCount", "overseasStockCount", "postStockCount"))
        n += 1
    if total and len(lib) < total:
        raise SystemExit(f"parts library read stopped at {len(lib)} of {total} rows")
    return lib


def read_cart(page):
    """{lcsc: row} of the whole JLC parts cart: in-stock, pre-order and Global Sourcing lines (the
    "private_library" list leaves pre-orders out). Exits if it cannot be read: adding against a
    failed read is how quantities once stacked."""
    page.goto(CART_PAGE, wait_until="domcontentloaded", timeout=60000)
    time.sleep(2)
    rows, n = {}, 1
    while True:
        r = page_json(page, "POST", CART_LIST, {"cartType": "", "pageSize": 100, "currentPage": n, "keywords": ""})
        data = ok_data(r)
        if data is None:
            raise SystemExit(f"could not read the parts cart ({fail_msg(r)}); changing nothing")
        groups = data.get("list") or []
        for g in groups:
            for item in g.get("goodsResultVOList") or []:
                code = (item.get("componentCode") or "").strip()
                if code:
                    rows[code] = {"qty": int(item.get("presaleNumber") or 0),
                                  "cartAccessId": g.get("shoppingCartAccessId"),
                                  "componentAccessId": item.get("componentGoodsAccessId"),
                                  "lcscComponentId": item.get("lcscComponentId"),
                                  "presaleType": item.get("presaleType") or "stock"}
        if len(groups) < 100:
            return rows
        n += 1


# ---------- plan and cart ----------

def plan(boards, spare, every_basic, library, out_csv):
    fields = ["lcsc", "mpn", "boards", "per", "need", "library", "short", "jlc_stock", "reservable",
              "library_type", "unit_usd", "short_usd", "action", "refs"]
    rows = []
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for code, L in sorted(read_boms().items()):
            rec = jlc_record(code)
            time.sleep(PACE)
            need = L["per"] * boards + spare
            have = library.get(code, 0)
            short = max(0, need - have)
            stock = int((rec or {}).get("stockCount") or 0)
            can = int((rec or {}).get("canPresaleNumber") or 0)
            basic = (rec or {}).get("componentLibraryType") == "base"
            price = unit_price(rec, short or need) if rec else 0.0
            if code in OWNER_SUPPLIED:
                short, action = 0, f"owner-supplied: {OWNER_SUPPLIED[code]}"
            elif not short:
                action = "covered by the library"
            elif basic and not every_basic and stock >= short:
                action = "basic part: JLC stock at assembly"
            elif not rec or not rec.get("componentId"):
                action = "GLOBAL SOURCING: not in JLC's catalog"
            elif can <= 0:
                action = f"GLOBAL SOURCING: JLC has none to reserve (stock {stock}, reservable {can})"
            elif can >= short:
                action = "cart: from JLC stock"
            else:
                action = f"cart: pre-order (JLC can reserve {can})"
            row = {"lcsc": code, "mpn": L["mpn"], "boards": "+".join(L["boards"]), "per": L["per"], "need": need,
                   "library": have, "short": short, "jlc_stock": stock, "reservable": can,
                   "library_type": (rec or {}).get("componentLibraryType", "?"), "unit_usd": f"{price:.4f}",
                   "short_usd": f"{price * short:.2f}", "action": action, "refs": ",".join(L["refs"])}
            w.writerow(row)
            fh.flush()
            row["rec"] = rec
            rows.append(row)
            print(f"  {code:10} {L['mpn'][:24]:24} need {need:5} library {have:5} short {short:5} "
                  f"JLC {stock:7} reservable {can:7}  {action}", flush=True)
    return rows


def apply_cart(page, rows):
    cart = read_cart(page)
    print(f"\nParts cart before: {len(cart)} lines", flush=True)
    todo = [r for r in rows if r["action"].startswith("cart:")]
    mine = {r["lcsc"] for r in todo}
    for code, c in cart.items():
        if code not in mine:
            print(f"  left alone (not this run's): {code} x{c['qty']}")
    for r in todo:
        code, target = r["lcsc"], r["short"]
        body, ask, _ = cart_body(r["rec"], target)
        if ask > target * 1.5:
            print(f"  SKIPPED {code}: JLC's minimum order is {ask} for a shortfall of {target} (your call)")
            continue
        if code in cart:
            c = cart[code]
            if c["qty"] == ask:
                print(f"  already in cart: {code} x{ask}")
                continue
            res = page_json(page, "POST", UPD_URL, {"cartAccessId": c["cartAccessId"],
                                                    "componentAccessId": c["componentAccessId"],
                                                    "componentCode": code, "lcscComponentId": c["lcscComponentId"],
                                                    "presaleNumber": str(ask), "presaleType": c["presaleType"]})
            verb = f"updated {c['qty']} -> {ask}"
        else:
            res = page_json(page, "POST", ADD_URL, body)
            verb = f"added x{ask}"
        good = (res.get("json") or {}).get("code") == 200
        print(f"  {'OK  ' if good else 'FAIL'} {code} {verb if good else fail_msg(res)}  {r['mpn']}", flush=True)
        time.sleep(PACE)
    after = read_cart(page)
    off = {r["lcsc"]: (after.get(r["lcsc"], {}).get("qty", 0), r["short"]) for r in todo
           if after.get(r["lcsc"], {}).get("qty", 0) != cart_body(r["rec"], r["short"])[1]}
    print("\nCart re-read: every line at its target." if not off else
          f"\nCart re-read: lines off target (have, short): {off}")
    print("Nothing is paid: check out in Parts Manager > Parts Cart.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boards", type=int, required=True)
    ap.add_argument("--spare", type=int, default=5, help="extra pieces per line (default 5)")
    ap.add_argument("--all", action="store_true", help="put basic-library shortfalls in the cart too")
    ap.add_argument("--apply", action="store_true", help="set the JLC parts cart to the shortfall")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    OUT_DIR.mkdir(exist_ok=True)
    out_csv = OUT_DIR / f"plan-{a.boards}.csv"
    with sync_playwright() as pw:
        ctx, page = open_session(pw)
        try:
            library = read_library(page)
            print(f"Library: {len(library)} part numbers, {sum(library.values())} pieces", flush=True)
            rows = plan(a.boards, a.spare, a.all, library, out_csv)
            buy = [r for r in rows if r["action"].startswith("cart:")]
            sourcing = [r for r in rows if r["action"].startswith("GLOBAL SOURCING")]
            print(f"\n{len(rows)} lines for {a.boards} Ice: {sum(1 for r in rows if not r['short'])} covered by the "
                  f"library, {len(buy)} to buy (${sum(float(r['short_usd']) for r in buy):,.0f} at JLC's price), "
                  f"{len(sourcing)} JLC cannot supply (Global Sourcing / maker, into the library)")
            for r in sourcing:
                print(f"  {r['lcsc']:10} {r['mpn'][:26]:26} short {r['short']:4}  {r['action'].split(': ', 1)[1]}")
            print(f"Plan: {out_csv}")
            if a.apply:
                apply_cart(page, rows)
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
