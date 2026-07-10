#!/usr/bin/env python3
"""Conduit run analysis pipeline (Python script equivalent of the notebook).

Run from the workspace root:

    python run_analysis.py                      # uses most recent export set
    python run_analysis.py --timestamp 20260417_141519
    python run_analysis.py --folder "Revit Exports" --cover 100 --tolerance 2000
    python run_analysis.py --open               # open HTMLs in browser when done
    python run_analysis.py --no-viz             # skip HTML generation

Outputs:
    plan_bend_outlines_<TS>.csv
    straight_outlines_<TS>.csv
    runs_3d.html
    runs_plan.html
    chain_order_<TS>.csv
"""

import os
import sys
import argparse
import webbrowser

# make the conduit_analysis package importable when this script is run
# directly (script lives inside the package folder, so add the PARENT folder)
THIS_DIR   = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(THIS_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from conduit_analysis import (
    find_export_sets,
    parse_file, get_od, get_od_map,
    cluster_and_order, cluster_runs_into_collections,
    build_run_curves, compute_collection_ods,
    compute_plan_bend_outlines, outlines_to_csv,
    compute_straight_run_outlines, straight_outlines_to_csv,
    compute_sloped_straight_outlines, sloped_straight_outlines_to_csv,
    unify_collection_z_ranges,
    unify_sloped_collection_dims,
    generate_html, generate_plan_html,
)


def parse_args():
    ap = argparse.ArgumentParser(
        description="Run the full conduit analysis pipeline.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--folder", default="Revit Exports",
                    help="folder containing pipes_*.csv / fittings_*.csv pairs")
    ap.add_argument("--timestamp",
                    help="specific export timestamp (YYYYMMDD_HHMMSS); "
                         "if omitted, uses the most recent set")
    ap.add_argument("--cover", type=float, default=100.0,
                    help="extra cover added to outer/inner radii and straight cross-section (mm)")
    ap.add_argument("--tolerance", type=float, default=2000.0,
                    help="distance tolerance for clustering runs into collections (mm)")
    ap.add_argument("--no-viz", action="store_true",
                    help="skip generating the 3D and plan HTML views")
    ap.add_argument("--open", dest="open_browser", action="store_true",
                    help="open the generated HTML views in the default browser")
    ap.add_argument("--no-chain-csv", action="store_true",
                    help="skip writing chain_order_<TS>.csv")
    return ap.parse_args()


def choose_export_set(folder, requested_ts=None):
    sets = find_export_sets(folder)
    if not sets:
        print("ERROR: no export sets found in: {}".format(os.path.abspath(folder)))
        sys.exit(1)
    if requested_ts:
        for s in sets:
            if s["timestamp"] == requested_ts:
                return s
        print("ERROR: timestamp '{}' not found. Available sets:".format(requested_ts))
        for s in sets:
            print("  {}  ({} pipes, {} fittings)".format(
                s["timestamp"], s.get("n_pipes", "?"), s.get("n_fittings", "?")))
        sys.exit(1)
    return sets[0]  # most recent


def hrule(title=None):
    bar = "=" * 60
    if title:
        print("\n" + bar); print(title); print(bar)
    else:
        print(bar)


def main():
    args = parse_args()

    # ---------- 1. Pick export set ----------
    hrule("1. Selecting export set")
    selected = choose_export_set(args.folder, args.timestamp)
    ts = selected["timestamp"]
    print("Using:     {}".format(selected.get("label", ts)))
    print("Pipes:     {}".format(selected["pipes"]))
    print("Fittings:  {}".format(selected["fittings"]))

    # ---------- 2. Load ----------
    hrule("2. Loading data")
    pipes    = parse_file(selected["pipes"])
    fittings = parse_file(selected["fittings"])
    od_by_id, default_od = get_od_map(pipes)
    print("Pipes:     {}".format(len(pipes)))
    print("Fittings:  {}".format(len(fittings)))
    if od_by_id:
        _ods = sorted(set(od_by_id.values()))
        if len(_ods) == 1:
            print("OD:        {:.1f} mm (uniform)".format(_ods[0]))
        else:
            print("OD:        {:.1f}-{:.1f} mm (per-pipe; {} distinct)".format(
                _ods[0], _ods[-1], len(_ods)))
    else:
        print("OD:        {:.1f} mm (default - no OD_mm in export)".format(default_od))
    print("Cover:     {:.0f} mm".format(args.cover))
    print("Tolerance: {:.0f} mm".format(args.tolerance))

    # ---------- 3. Cluster ----------
    hrule("3. Clustering & ordering runs")
    elems, ordered_runs = cluster_and_order(pipes, fittings)
    print("Runs found: {}".format(len(ordered_runs)))

    # ---------- 4. Build curves ----------
    hrule("4. Building run curves")
    all_run_curves = [build_run_curves(chain, elems) for chain in ordered_runs]

    # ---------- 5. Collections ----------
    hrule("5. Grouping into collections")
    collections = cluster_runs_into_collections(
        all_run_curves, tolerance=args.tolerance)
    print("Collections: {}".format(len(collections)))

    # Per-collection OD: each collection (single pipe or bundle) is sized to the
    # largest pipe OD it actually contains, rather than one OD for the whole job.
    od_mm = compute_collection_ods(all_run_curves, collections, default_od=default_od)

    # ---------- 6. Plan bend outlines ----------
    hrule("6. Computing plan bend outlines")
    outlines = compute_plan_bend_outlines(
        all_run_curves, collections, od_mm=od_mm, cover_mm=args.cover)
    print("Plan bend outlines: {}".format(len(outlines)))

    # ---------- 7. Straight outlines ----------
    hrule("7. Computing straight run outlines")
    straight_outlines = compute_straight_run_outlines(
        all_run_curves, collections,
        od_mm=od_mm, cover_mm=args.cover,
        plan_bend_outlines=outlines)
    print("Horizontal straight outlines: {}".format(len(straight_outlines)))

    sloped_outlines = compute_sloped_straight_outlines(
        all_run_curves, collections,
        od_mm=od_mm, cover_mm=args.cover)
    print("Sloped straight outlines:     {}".format(len(sloped_outlines)))

    # ---------- 8. Unify Z ranges ----------
    hrule("8. Unifying Z ranges (straight <- adjacent plan bends)")
    unify_collection_z_ranges(outlines, straight_outlines)
    # quick sanity summary
    from collections import defaultdict
    heights_by_col = defaultdict(list)
    for o in outlines:
        heights_by_col[o["collection_id"]].append(o["top_z_mm"] - o["bottom_z_mm"])
    tall = 0
    for s in straight_outlines:
        h = s["top_z_mm"] - s["bottom_z_mm"]
        bh = heights_by_col.get(s["collection_id"])
        if bh and h > max(bh) * 1.3:
            tall += 1
    print("Straights >30% taller than any bend in their collection: {}".format(tall))

    # Unify sloped dims with their collection's horizontal width + plan-bend height
    unify_sloped_collection_dims(outlines, straight_outlines, sloped_outlines)
    if sloped_outlines:
        print("Unified {} sloped segments to collection dimensions.".format(
            len(sloped_outlines)))

    # ---------- 9. CSVs ----------
    hrule("9. Writing CSVs")
    outline_csv        = "plan_bend_outlines_{}.csv".format(ts)
    straight_csv       = "straight_outlines_{}.csv".format(ts)
    sloped_csv         = "sloped_straights_{}.csv".format(ts)
    outlines_to_csv(outlines, outline_csv)
    straight_outlines_to_csv(straight_outlines, straight_csv)
    sloped_straight_outlines_to_csv(sloped_outlines, sloped_csv)
    print("Wrote: {}".format(outline_csv))
    print("Wrote: {}".format(straight_csv))
    print("Wrote: {}".format(sloped_csv))

    # ---------- 9b. Duct centrelines CSV ----------
    # One row per straight (horizontal + sloped), grouped by collection,
    # sorted by SegmentIdx. Width/Height are taken from the collection's
    # canonical dims (first horizontal straight; fallback to first sloped).
    # These are the exact numbers the Build Ducts button reads to place ducts.
    duct_centrelines_csv = "duct_centrelines_{}.csv".format(ts)
    import math as _math
    per_col = {}  # col_id -> list of rows
    for o in straight_outlines:
        sp = o["duct_start"]; ep = o["duct_end"]
        per_col.setdefault(o["collection_id"], []).append({
            "seg":    o["segment_idx"],
            "kind":   "horizontal",
            "sp":     sp,
            "ep":     ep,
            "width":  float(o["duct_width_mm"]),
            "height": float(o["duct_height_mm"]),
        })
    for o in sloped_outlines:
        per_col.setdefault(o["collection_id"], []).append({
            "seg":    o["segment_idx"],
            "kind":   "sloped",
            "sp":     o["centre_start"],
            "ep":     o["centre_end"],
            "width":  float(o["half_width_mm"])  * 2.0,
            "height": float(o["half_height_mm"]) * 2.0,
        })
    # Canonical collection dims: prefer first horizontal; fallback to first sloped.
    col_dims = {}
    for cid, rows in per_col.items():
        horiz = [r for r in rows if r["kind"] == "horizontal"]
        src = horiz[0] if horiz else rows[0]
        col_dims[cid] = (src["width"], src["height"])

    with open(duct_centrelines_csv, "w") as f:
        f.write("Collection,Order,SegmentIdx,Kind,"
                "StartX_mm,StartY_mm,StartZ_mm,"
                "EndX_mm,EndY_mm,EndZ_mm,"
                "Width_mm,Height_mm,Length_mm\n")
        n_rows = 0
        for cid in sorted(per_col.keys()):
            rows = sorted(per_col[cid], key=lambda r: r["seg"])
            w, h = col_dims[cid]
            for order, r in enumerate(rows, start=1):
                sp = r["sp"]; ep = r["ep"]
                length = _math.sqrt(
                    (ep[0]-sp[0])**2 + (ep[1]-sp[1])**2 + (ep[2]-sp[2])**2)
                f.write("{},{},{},{},"
                        "{:.3f},{:.3f},{:.3f},"
                        "{:.3f},{:.3f},{:.3f},"
                        "{:.3f},{:.3f},{:.3f}\n".format(
                    cid + 1, order, r["seg"], r["kind"],
                    sp[0], sp[1], sp[2],
                    ep[0], ep[1], ep[2],
                    w, h, length))
                n_rows += 1
    print("Wrote: {} ({} centrelines across {} collection(s))".format(
        duct_centrelines_csv, n_rows, len(per_col)))

    if not args.no_chain_csv:
        chain_csv = "chain_order_{}.csv".format(ts)
        run_to_col = {}
        for ci, run_ids in enumerate(collections):
            for r in run_ids:
                run_to_col[r] = ci + 1
        with open(chain_csv, "w") as f:
            f.write("Collection,Run,ChainOrder,ElementType,ID\n")
            for run_id, chain in enumerate(ordered_runs):
                col = run_to_col.get(run_id, 0)
                for order, idx in enumerate(chain, 1):
                    e = elems[idx]
                    f.write("{},{},{},{},{}\n".format(
                        col, run_id + 1, order, e["type"], e.get("id", "")))
        print("Wrote: {}".format(chain_csv))

    # ---------- 10. Visualisations ----------
    written_html = []
    if not args.no_viz:
        hrule("10. Generating visualisations")
        html_3d   = "runs_3d.html"
        html_plan = "runs_plan.html"
        generate_html(
            all_run_curves, od_mm=od_mm, collections=collections,
            plan_bend_outlines=outlines, straight_outlines=straight_outlines,
            sloped_outlines=sloped_outlines,
            output_path=html_3d)
        generate_plan_html(
            all_run_curves, collections=collections,
            plan_bend_outlines=outlines, straight_outlines=straight_outlines,
            output_path=html_plan)
        print("Wrote: {}".format(html_3d))
        print("Wrote: {}".format(html_plan))
        written_html = [html_3d, html_plan]

    # ---------- Done ----------
    hrule("Done")
    if args.open_browser and written_html:
        for h in written_html:
            uri = "file://" + os.path.abspath(h)
            print("Opening: {}".format(uri))
            webbrowser.open(uri)


if __name__ == "__main__":
    main()
