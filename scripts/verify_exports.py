#!/usr/bin/env python3
"""Verify the 3D utilities viewer's dashboard exports against the source
LandXML (stdlib only).

Structures (STRUCTS-*.json, kind ol-utilities-structures): every row's
rim/sump/z/depth must come from the <Struct> element's OWN elevRim /
elevSump attributes - never from connected pipe <Invert> records:

  rim_m   = elevRim
  sump_m  = elevSump          (missing -> elevRim - 1.2)
  rim_m   = sump_m + 1.2      (only when rim <= sump)
  depth_m = rim_m - sump_m    (3 d.p., never 0)
  z_m     = sump_m

Pipes (PIPES-*.json, kind ol-utilities-pipes) DO use inverts - each
end's z_m must equal its structure's <Invert elev> for that refPipe
(flowDir out at the start, in at the end).

rotation_deg: LandXML carries no structure rotation, so the viewer
derives one from the connected pipes - the largest-diameter run through
the structure sets the angle (degrees CCW from east, folded to
[0,180); ties go to the longer plan run; no usable pipe -> 0). The
verifier recomputes that rule from the XML and compares.

Usage:
  python3 scripts/verify_exports.py --xml FILE.xml --structs STRUCTS.json
                                    [--pipes PIPES.json]

Exits 0 when every check passes, 1 otherwise.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET

TOL = 0.005


def strip_ns(tag):
    return tag.rsplit("}", 1)[-1]


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def is_dummy(name, desc):
    n = (name or "").lower().replace(" ", "")
    return "nullstruct" in n or "dummy null" in (desc or "").lower()


def metresish(v):
    """Mirror of the viewer's metresish(): linear dims >= 50 are mm."""
    if v is None:
        return None
    return v / 1000.0 if abs(v) >= 50 else v


def parse_landxml(path):
    """-> (structs, pipes): structs[name] = {rim, sump, x, y,
    inverts:[...]}, pipes[name] = {refStart, refEnd, dia, dead}.
    Names are the raw LandXML names."""
    structs, pipes = {}, {}
    for _, el in ET.iterparse(path, events=("end",)):
        tag = strip_ns(el.tag)
        if tag == "Struct":
            name = el.get("name") or "(unnamed)"
            inverts = []
            x = y = None
            for ch in el:
                ctag = strip_ns(ch.tag)
                if ctag == "Invert":
                    inverts.append({
                        "elev": fnum(ch.get("elev")),
                        "dir": (ch.get("flowDir") or "").lower(),
                        "ref": ch.get("refPipe"),
                    })
                elif ctag == "Center" and ch.text:
                    parts = ch.text.split()
                    if len(parts) >= 2:
                        a, b = fnum(parts[0]), fnum(parts[1])
                        if a is not None and b is not None:
                            # column order: NORTHING then EASTING
                            x, y = b, a
            structs[name] = {
                "rim": fnum(el.get("elevRim")),
                "sump": fnum(el.get("elevSump")),
                "x": x, "y": y,
                "inverts": inverts,
                "dummy": is_dummy(name, el.get("desc")),
            }
            el.clear()
        elif tag == "Pipe":
            dia, dead = None, False
            for ch in el:
                ctag = strip_ns(ch.tag)
                if ctag == "CircPipe":
                    dv = fnum(ch.get("diameter"))
                    if dv is not None:
                        dia = dv / 1000.0      # viewer default: mm
                        if dv <= 0:
                            dead = True
                elif ctag in ("RectPipe", "EggPipe", "ElliPipe"):
                    dia = metresish(fnum(ch.get("width"))) or 0.1
            pipes[el.get("name") or "(unnamed)"] = {
                "refStart": el.get("refStart"),
                "refEnd": el.get("refEnd"),
                "dia": dia, "dead": dead,
            }
            el.clear()
    return structs, pipes


def has_xy(st):
    return (st is not None and st["x"] is not None and st["y"] is not None
            and not (abs(st["x"]) < 1e-6 and abs(st["y"]) < 1e-6))


def expected_rotations(structs, pipes):
    """{struct name: degrees} - the viewer's derivation rule, recomputed
    independently: largest-dia pipe through the structure, folded to
    [0,180), ties to the longer plan run. A pipe ending on a dummy
    NullStruct contributes to the nearest real structure within 1 m;
    directly-referenced pipes always win over nearby ones."""
    import math
    orient = {}    # name -> (rank, az, dia, plan); rank 0 direct, 1 near

    def consider(name, rank, az, dia, plan):
        cur = orient.get(name)
        if cur and cur[0] < rank:
            return
        if (cur is None or cur[0] > rank or dia > cur[2] + 1e-9
                or (abs(dia - cur[2]) <= 1e-9 and plan > cur[3])):
            orient[name] = (rank, az, dia, plan)

    real_xy = [(n, s) for n, s in structs.items()
               if not s["dummy"] and has_xy(s)]

    def nearest_real(st):
        best, bd = None, 1.0
        for n, s in real_xy:
            d = math.hypot(s["x"] - st["x"], s["y"] - st["y"])
            if d <= bd:
                bd, best = d, n
        return best

    for pname, pp in pipes.items():
        if pp["dead"]:
            continue
        s = structs.get(pp["refStart"])
        e = structs.get(pp["refEnd"])
        if not has_xy(s) or not has_xy(e):
            continue
        dx, dy = e["x"] - s["x"], e["y"] - s["y"]
        plan = math.sqrt(dx * dx + dy * dy)
        if plan < 0.05:
            continue
        az = math.degrees(math.atan2(dy, dx)) % 180.0
        dia = pp["dia"] if pp["dia"] is not None else 0
        for ref in (pp["refStart"], pp["refEnd"]):
            st = structs.get(ref)
            if st is None:
                continue
            if not st["dummy"]:
                consider(ref, 0, az, dia, plan)
            else:
                near = nearest_real(st)
                if near:
                    consider(near, 1, az, dia, plan)
    return dict((n, o[1]) for n, o in orient.items())


def xml_name(row_name, layer, structs):
    """Export rows are named '<name> (<layer>)'; raw LandXML names may or
    may not already carry that suffix."""
    if row_name in structs:
        return row_name
    sfx = " ({})".format(layer)
    if row_name.endswith(sfx) and row_name[:-len(sfx)] in structs:
        return row_name[:-len(sfx)]
    return None


def expected_levels(st):
    rim, sump = st["rim"], st["sump"]
    if sump is None:
        sump = (rim if rim is not None else 0.0) - 1.2
    if rim is None or rim <= sump:
        rim = sump + 1.2
    return rim, sump


def invert_for(st, pipe_name, direction):
    """Mirror of the viewer's invertFor(): refPipe match first (preferring
    the flowDir), then flowDir alone, then the lowest invert."""
    with_ref = [i for i in st["inverts"]
                if i["ref"] == pipe_name and i["elev"] is not None]
    if with_ref:
        for i in with_ref:
            if i["dir"] == direction:
                return i["elev"]
        return with_ref[0]["elev"]
    for i in st["inverts"]:
        if i["dir"] == direction and i["elev"] is not None:
            return i["elev"]
    lows = [i["elev"] for i in st["inverts"] if i["elev"] is not None]
    return min(lows) if lows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", required=True)
    ap.add_argument("--structs", required=True)
    ap.add_argument("--pipes")
    args = ap.parse_args()

    structs, pipes = parse_landxml(args.xml)
    fails = []

    def check(ok, msg):
        print(("PASS  " if ok else "FAIL  ") + msg)
        if not ok:
            fails.append(msg)

    # ---------------- structures ----------------
    sdoc = json.load(open(args.structs))
    check(sdoc.get("kind") == "ol-utilities-structures",
          "structs kind is ol-utilities-structures")
    rows = sdoc.get("structures", [])
    real = {n: s for n, s in structs.items() if not s["dummy"]}
    check(len(rows) == len(real),
          "row count {} == {} non-dummy XML structs".format(
              len(rows), len(real)))

    bad_rim, bad_sump, bad_depth0, bad_arith, bad_z, unmatched = \
        [], [], [], [], [], []
    seen = set()
    for r in rows:
        xn = xml_name(r["name"], r.get("layer", ""), structs)
        if xn is None:
            unmatched.append(r["name"])
            continue
        seen.add(xn)
        st = structs[xn]
        exp_rim, exp_sump = expected_levels(st)
        if abs(r["rim_m"] - exp_rim) > TOL:
            bad_rim.append((r["name"], r["rim_m"], exp_rim))
        if abs(r["sump_m"] - exp_sump) > TOL:
            bad_sump.append((r["name"], r["sump_m"], exp_sump))
        if r["depth_m"] == 0:
            bad_depth0.append(r["name"])
        if abs(r["depth_m"] - (r["rim_m"] - r["sump_m"])) > 0.0015:
            bad_arith.append(r["name"])
        if r["z_m"] != r["sump_m"]:
            bad_z.append(r["name"])

    check(not unmatched,
          "every row matches an XML struct (unmatched: {})".format(
              unmatched[:5]))
    check(not bad_rim,
          "rim_m == elevRim +-{} for all rows (bad: {})".format(
              TOL, bad_rim[:5]))
    check(not bad_sump,
          "sump_m == elevSump +-{} (elevRim-1.2 where missing) "
          "(bad: {})".format(TOL, bad_sump[:5]))
    check(not bad_depth0,
          "0 rows with depth_m == 0 (got {}: {})".format(
              len(bad_depth0), bad_depth0[:5]))
    check(not bad_arith, "depth_m == rim_m - sump_m on every row")
    check(not bad_z, "z_m == sump_m on every row")

    missing = sorted(set(real) - seen)
    check(not missing,
          "every non-dummy XML struct exported (missing: {})".format(
              missing[:5]))

    # the named example from the bug report
    elv = next((r for r in rows if r["name"] == "ELV-001 (ELV-P1)"), None)
    check(elv is not None, "ELV-001 (ELV-P1) present")
    if elv:
        ok = (abs(elv["rim_m"] - 10.73) <= TOL
              and abs(elv["sump_m"] - 9.83) <= TOL
              and abs(elv["depth_m"] - 0.90) <= TOL
              and abs(elv["z_m"] - 9.83) <= TOL)
        check(ok, "ELV-001 (ELV-P1) rim 10.73 sump 9.83 depth 0.90 "
                  "z 9.83 (got rim {} sump {} depth {} z {})".format(
                      elv["rim_m"], elv["sump_m"], elv["depth_m"],
                      elv["z_m"]))

    # rotation: recompute the pipe-run derivation from the XML
    exp_rot = expected_rotations(structs, pipes)
    bad_rot, nonzero = [], 0
    for r in rows:
        xn = xml_name(r["name"], r.get("layer", ""), structs)
        if xn is None:
            continue
        want = exp_rot.get(xn, 0.0)
        got = float(r.get("rotation_deg") or 0.0)
        diff = abs(got - want) % 180.0
        diff = min(diff, 180.0 - diff)
        if diff > 0.06:
            bad_rot.append((r["name"], got, round(want, 2)))
        if got:
            nonzero += 1
    check(not bad_rot,
          "rotation_deg matches the dominant-pipe-run rule on every row "
          "({} rows carry a non-zero rotation) (bad: {})".format(
              nonzero, bad_rot[:5]))

    # the invert-less structures must all have real depth now
    no_inv = {n for n, s in real.items() if not s["inverts"]}
    row_by_xml = {}
    for r in rows:
        xn = xml_name(r["name"], r.get("layer", ""), structs)
        if xn:
            row_by_xml[xn] = r
    flat = [n for n in no_inv
            if n in row_by_xml and row_by_xml[n]["depth_m"] <= 0]
    check(not flat,
          "all {} invert-less structures have depth_m > 0 "
          "(flat: {})".format(len(no_inv), flat[:5]))

    # ---------------- pipes (untouched by the fix) ----------------
    if args.pipes:
        pdoc = json.load(open(args.pipes))
        check(pdoc.get("kind") == "ol-utilities-pipes",
              "pipes kind is ol-utilities-pipes")
        prows = pdoc.get("pipes", [])
        checked, bad_ends = 0, []
        for r in prows:
            pn = r["name"]
            if pn not in pipes:
                sfx = " ({})".format(r.get("layer", ""))
                pn = pn[:-len(sfx)] if pn.endswith(sfx) else pn
            pp = pipes.get(pn)
            if not pp:
                continue
            for key, ref, direction in (("start", pp["refStart"], "out"),
                                        ("end", pp["refEnd"], "in")):
                st = structs.get(ref)
                if not st:
                    continue
                exp = invert_for(st, pn, direction)
                if exp is None:
                    continue
                checked += 1
                if abs(r[key]["z_m"] - exp) > TOL:
                    bad_ends.append((r["name"], key, r[key]["z_m"], exp))
        check(checked > 0, "pipe invert checks ran ({})".format(checked))
        check(not bad_ends,
              "all {} pipe end z_m values equal their <Invert elev> "
              "(bad: {})".format(checked, bad_ends[:5]))

    print()
    if fails:
        print("{} FAILURE(S)".format(len(fails)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
