"""Generate interactive 3D visualization of conduit runs."""
import json
import numpy as np


COLORS = ['#e6194b', '#3cb44b', '#4363d8', '#f58231',
          '#911eb4', '#42d4f4', '#f032e6', '#bfef45']


def classify_segment(curve):
    """Categorise a segment: 'straight', 'plan_bend', 'vertical_bend'."""
    if curve["type"] == "Line":
        return "straight"
    pts = curve["points"]
    sp = np.array(pts[0])
    ep = np.array(pts[-1])
    dz = abs(ep[2] - sp[2])
    if dz < 5.0:
        return "plan_bend"
    return "vertical_bend"


def generate_html(all_run_curves, od_mm=None, collections=None,
                  plan_bend_outlines=None, straight_outlines=None,
                  sloped_outlines=None, output_path=None):
    """Generate interactive Plotly HTML of conduit runs.

    collections: optional list of run-index lists.
    plan_bend_outlines: optional plan-bend outline dicts.
    straight_outlines: optional horizontal straight outline dicts.
    sloped_outlines: optional sloped (tilted) straight outline dicts.

    Returns HTML string. If output_path is given, also writes to file.
    """
    run_to_col = {}
    if collections:
        for ci, run_ids in enumerate(collections):
            for r in run_ids:
                run_to_col[r] = ci

    run_segments = []
    run_starts = []
    run_ends = []

    for ri, curves in enumerate(all_run_curves):
        color = COLORS[ri % len(COLORS)]
        col_id = run_to_col.get(ri, 0)
        if collections:
            run_label = "Col {} / Run {}".format(col_id + 1, ri + 1)
        else:
            run_label = "Run {}".format(ri + 1)

        for curve in curves:
            cat = classify_segment(curve)
            pts = [np.array(p).tolist() for p in curve["points"]]
            run_segments.append({
                "run_idx": ri,
                "col_id": col_id,
                "color": color,
                "name": run_label,
                "category": cat,
                "x": [p[0] for p in pts],
                "y": [p[1] for p in pts],
                "z": [p[2] for p in pts],
            })

        first_pt = np.array(curves[0]["points"][0]).tolist()
        last_pt = np.array(curves[-1]["points"][-1]).tolist()
        run_starts.append({"run_idx": ri, "col_id": col_id, "color": color,
                           "name": run_label,
                           "x": [first_pt[0]], "y": [first_pt[1]], "z": [first_pt[2]]})
        run_ends.append({"run_idx": ri, "col_id": col_id, "color": color,
                         "name": run_label,
                         "x": [last_pt[0]], "y": [last_pt[1]], "z": [last_pt[2]]})

    # Prepare outline traces (one per outline, each with all parts concatenated via None separators)
    outline_traces = []
    if plan_bend_outlines:
        for o in plan_bend_outlines:
            ox, oy, oz = [], [], []
            # Outer arc
            for p in o["outer_arc_points"]:
                ox.append(p[0]); oy.append(p[1]); oz.append(p[2])
            ox.append(None); oy.append(None); oz.append(None)
            # Connecting line 2 (outer end → inner end)
            ox.append(o["outer_end"][0]);   oy.append(o["outer_end"][1]);   oz.append(o["outer_end"][2])
            ox.append(o["inner_end"][0]);   oy.append(o["inner_end"][1]);   oz.append(o["inner_end"][2])
            ox.append(None); oy.append(None); oz.append(None)
            # Inner arc (reversed so we traverse back)
            for p in reversed(o["inner_arc_points"]):
                ox.append(p[0]); oy.append(p[1]); oz.append(p[2])
            ox.append(None); oy.append(None); oz.append(None)
            # Connecting line 1 (inner start → outer start, closing shape)
            ox.append(o["inner_start"][0]); oy.append(o["inner_start"][1]); oz.append(o["inner_start"][2])
            ox.append(o["outer_start"][0]); oy.append(o["outer_start"][1]); oz.append(o["outer_start"][2])
            outline_traces.append({
                "x": ox, "y": oy, "z": oz,
                "col_id": o["collection_id"],
                "name": "Col {} Bend {}".format(o["collection_id"] + 1, o["bend_idx"]),
            })

    # Straight outline traces — single closed 4-corner loop at top_z
    # (matches the flat style used by plan_bend_outlines above).
    straight_traces = []
    if straight_outlines:
        for o in straight_outlines:
            c1, c2, c3, c4 = o["corner1"], o["corner2"], o["corner3"], o["corner4"]
            z = o["top_z_mm"]
            ox, oy, oz = [], [], []
            for p in [c1, c2, c3, c4, c1]:
                ox.append(p[0]); oy.append(p[1]); oz.append(z)
            straight_traces.append({
                "x": ox, "y": oy, "z": oz,
                "col_id": o["collection_id"],
                "name": "Col {} Straight {}".format(o["collection_id"] + 1, o["segment_idx"]),
            })

    # Per-collection straight centrelines (horizontal + sloped combined),
    # sorted by segment_idx so numbering follows chain order start -> finish.
    # One line per straight + one numbered text label at the midpoint.
    centreline_traces = []
    centreline_labels = []
    per_col = {}  # col_id -> list of (segment_idx, centre_start, centre_end, kind)
    if straight_outlines:
        for o in straight_outlines:
            # Horizontal straights: use the envelope-midpoint duct axis (same
            # points the duct_centrelines CSV and Build Ducts button use).
            sp = o.get("duct_start", o["centre_start"])
            ep = o.get("duct_end",   o["centre_end"])
            per_col.setdefault(o["collection_id"], []).append(
                (o["segment_idx"], sp, ep, "horizontal"))
    if sloped_outlines:
        for o in sloped_outlines:
            per_col.setdefault(o["collection_id"], []).append(
                (o["segment_idx"], o["centre_start"], o["centre_end"], "sloped"))
    for col_id in sorted(per_col.keys()):
        segs = sorted(per_col[col_id], key=lambda t: t[0])
        for order, (seg_idx, sp, ep, kind) in enumerate(segs, start=1):
            mp = [(sp[0] + ep[0]) / 2.0,
                  (sp[1] + ep[1]) / 2.0,
                  (sp[2] + ep[2]) / 2.0]
            centreline_traces.append({
                "x": [sp[0], ep[0]], "y": [sp[1], ep[1]], "z": [sp[2], ep[2]],
                "col_id": col_id, "order": order, "kind": kind,
                "name": "Col {} #{} ({}) seg {}".format(
                    col_id + 1, order, kind, seg_idx),
            })
            centreline_labels.append({
                "x": [mp[0]], "y": [mp[1]], "z": [mp[2]],
                "col_id": col_id, "order": order,
                "text": [str(order)],
            })

    # od_mm may be a per-collection dict {col_id: od}; reduce to a single value
    # for the JSON stat and a label string for the info banner.
    if isinstance(od_mm, dict):
        _vals = sorted(set(v for v in od_mm.values() if v))
        od_stat = _vals[-1] if _vals else 0
        if not _vals:
            od_label = ""
        elif len(_vals) == 1:
            od_label = "{:.0f}".format(_vals[0])
        else:
            od_label = "{:.0f}-{:.0f}".format(_vals[0], _vals[-1])
    else:
        od_stat = od_mm or 0
        od_label = "{:.0f}".format(od_mm) if od_mm else ""

    data = json.dumps({
        "run_segments": run_segments,
        "run_starts": run_starts,
        "run_ends": run_ends,
        "outlines": outline_traces,
        "straights": straight_traces,
        "centrelines": centreline_traces,
        "centreline_labels": centreline_labels,
        "stats": {
            "n_runs": len(all_run_curves),
            "od_mm": od_stat,
        }
    })

    # Info bar text
    info_bits = []
    if od_label:
        info_bits.append('OD: <span>{} mm</span>'.format(od_label))
    info_bits.append('Runs: <span>{}</span>'.format(len(all_run_curves)))
    if collections:
        info_bits.append('Collections: <span>{}</span>'.format(len(collections)))
    info_html = ' | '.join(info_bits)

    # Collection checkbox HTML
    col_checks = ""
    if collections:
        col_checks = '<h4>Collections</h4>\n'
        for ci in range(len(collections)):
            n = len(collections[ci])
            col_checks += (
                '<label><input type="checkbox" class="col_filter" id="f_col_{}" '
                'data-col="{}" checked onchange="applyFilters()"> '
                'Collection {} ({} runs)</label>\n'.format(ci, ci, ci + 1, n))
        col_checks += '<div class="divider"></div>\n'

    html = '''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:Arial,sans-serif;background:#0f0f1a;color:#fff}
#plot{width:100vw;height:100vh}
.info{position:absolute;bottom:10px;left:10px;z-index:100;
  background:rgba(15,15,26,0.9);padding:10px 14px;border-radius:8px;
  font-size:12px;color:#aaa;border:1px solid #333}
.info span{color:#fff;font-weight:bold}
.controls{position:absolute;top:10px;right:10px;z-index:100;
  background:rgba(15,15,26,0.9);padding:10px 14px;border-radius:8px;
  font-size:12px;border:1px solid #333}
button{background:#4363d8;color:#fff;border:none;padding:6px 12px;
  border-radius:4px;cursor:pointer;margin:2px;font-size:11px}
button:hover{background:#5a7df5}
.filters label{display:block;margin:4px 0;cursor:pointer;font-size:12px;color:#ccc}
.filters input{margin-right:6px}
.filters h4{margin:8px 0 4px 0;font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px}
.divider{height:1px;background:#333;margin:8px 0}
</style>
</head>
<body>
<div id="plot"></div>
<div class="info">''' + info_html + '''</div>
<div class="controls filters">
  ''' + col_checks + '''<h4>Show</h4>
  <label><input type="checkbox" id="f_straight" checked onchange="applyFilters()"> Straight pipes</label>
  <label><input type="checkbox" id="f_plan" checked onchange="applyFilters()"> Plan bends</label>
  <label><input type="checkbox" id="f_vert" checked onchange="applyFilters()"> Vertical bends</label>
  <div class="divider"></div>
  <label><input type="checkbox" id="f_markers" checked onchange="applyFilters()"> Start / End markers</label>
  <label><input type="checkbox" id="f_outlines" checked onchange="applyFilters()"> Plan bend outlines</label>
  <label><input type="checkbox" id="f_straights" checked onchange="applyFilters()"> Straight outlines</label>
  <label><input type="checkbox" id="f_centrelines" checked onchange="applyFilters()"> Straight centrelines</label>
  <div class="divider"></div>
  <button onclick="resetView()">Reset View</button>
</div>
<script>
var D = ''' + data + ''';
var traces = [];
var traceMeta = [];

var runLegendShown = {};

D.run_segments.forEach(function(s){
  var showLegend = !runLegendShown[s.run_idx];
  runLegendShown[s.run_idx] = true;
  traces.push({
    type:'scatter3d', mode:'lines',
    x:s.x, y:s.y, z:s.z,
    name:s.name, legendgroup:s.name,
    line:{color:s.color, width:3},
    showlegend:showLegend,
    hovertemplate:s.name+' ('+s.category+')<br>X:%{x:.0f} Y:%{y:.0f} Z:%{z:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'segment', col_id:s.col_id, category:s.category});
});

D.run_starts.forEach(function(s){
  traces.push({type:'scatter3d', mode:'markers',
    x:s.x, y:s.y, z:s.z,
    name:s.name+' start',
    marker:{color:s.color, size:8, symbol:'circle', line:{color:'#fff', width:1}},
    showlegend:false,
    hovertemplate:s.name+' START<br>X:%{x:.0f} Y:%{y:.0f} Z:%{z:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'start', col_id:s.col_id});
});

D.run_ends.forEach(function(s){
  traces.push({type:'scatter3d', mode:'markers',
    x:s.x, y:s.y, z:s.z,
    name:s.name+' end',
    marker:{color:s.color, size:8, symbol:'diamond', line:{color:'#fff', width:1}},
    showlegend:false,
    hovertemplate:s.name+' END<br>X:%{x:.0f} Y:%{y:.0f} Z:%{z:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'end', col_id:s.col_id});
});

// Plan bend outlines
var outlineLegendShown = false;
D.outlines.forEach(function(o){
  traces.push({type:'scatter3d', mode:'lines',
    x:o.x, y:o.y, z:o.z,
    name: outlineLegendShown ? o.name : 'Plan bend outlines',
    legendgroup:'outlines',
    line:{color:'#ffcc00', width:4},
    showlegend: !outlineLegendShown,
    hovertemplate: o.name + '<br>X:%{x:.0f} Y:%{y:.0f} Z:%{z:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'outline', col_id:o.col_id});
  outlineLegendShown = true;
});

// Straight outlines
var straightLegendShown = false;
D.straights.forEach(function(o){
  traces.push({type:'scatter3d', mode:'lines',
    x:o.x, y:o.y, z:o.z,
    name: straightLegendShown ? o.name : 'Straight outlines',
    legendgroup:'straights',
    line:{color:'#66ff66', width:3},
    showlegend: !straightLegendShown,
    hovertemplate: o.name + '<br>X:%{x:.0f} Y:%{y:.0f} Z:%{z:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'straight_out', col_id:o.col_id});
  straightLegendShown = true;
});

// Straight centrelines (horizontal + sloped) with numbered labels.
// One dashed cyan line per straight + a text label at the midpoint showing
// the 1..N order within the collection (chain order start -> finish).
var centrelineLegendShown = false;
D.centrelines.forEach(function(c){
  traces.push({type:'scatter3d', mode:'lines',
    x:c.x, y:c.y, z:c.z,
    name: centrelineLegendShown ? c.name : 'Straight centrelines',
    legendgroup:'centrelines',
    line:{color:'#00e5ff', width:5, dash:'dash'},
    showlegend: !centrelineLegendShown,
    hovertemplate: c.name + '<br>X:%{x:.0f} Y:%{y:.0f} Z:%{z:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'centreline', col_id:c.col_id});
  centrelineLegendShown = true;
});

D.centreline_labels.forEach(function(l){
  traces.push({type:'scatter3d', mode:'text',
    x:l.x, y:l.y, z:l.z,
    text:l.text,
    textfont:{color:'#00e5ff', size:14, family:'Arial Black'},
    textposition:'middle center',
    showlegend:false,
    hoverinfo:'skip'
  });
  traceMeta.push({kind:'centreline_label', col_id:l.col_id});
});

var layout = {
  title:{text:'Conduit Runs', font:{color:'#fff', size:16}},
  paper_bgcolor:'#0f0f1a',
  scene:{
    xaxis:{title:'X (mm)', color:'#666', gridcolor:'#222'},
    yaxis:{title:'Y (mm)', color:'#666', gridcolor:'#222'},
    zaxis:{title:'Z (mm)', color:'#666', gridcolor:'#222'},
    bgcolor:'#0f0f1a',
    camera:{eye:{x:1.5, y:-1.5, z:0.6}},
    aspectmode:'data'
  },
  legend:{font:{color:'#ccc', size:11}, bgcolor:'rgba(0,0,0,0.4)', x:0, y:1, xanchor:'left'},
  margin:{l:0, r:0, t:40, b:0}
};

Plotly.newPlot('plot', traces, layout, {responsive:true});

function applyFilters(){
  var showStraight = document.getElementById('f_straight').checked;
  var showPlan     = document.getElementById('f_plan').checked;
  var showVert     = document.getElementById('f_vert').checked;
  var showMarkers  = document.getElementById('f_markers').checked;
  var showOutlines = document.getElementById('f_outlines') ? document.getElementById('f_outlines').checked : true;
  var showStraights = document.getElementById('f_straights') ? document.getElementById('f_straights').checked : true;
  var showCentrelines = document.getElementById('f_centrelines') ? document.getElementById('f_centrelines').checked : true;

  var colVisible = {};
  document.querySelectorAll('.col_filter').forEach(function(cb){
    colVisible[parseInt(cb.dataset.col)] = cb.checked;
  });

  var updates = [];
  var indices = [];

  traceMeta.forEach(function(m, i){
    var vis = true;
    if (Object.keys(colVisible).length > 0 && m.col_id !== undefined) {
      vis = vis && (colVisible[m.col_id] !== false);
    }
    if (m.kind === 'segment') {
      if (m.category === 'straight')      vis = vis && showStraight;
      if (m.category === 'plan_bend')     vis = vis && showPlan;
      if (m.category === 'vertical_bend') vis = vis && showVert;
    } else if (m.kind === 'start' || m.kind === 'end') {
      vis = vis && showMarkers;
    } else if (m.kind === 'outline') {
      vis = vis && showOutlines;
    } else if (m.kind === 'straight_out') {
      vis = vis && showStraights;
    } else if (m.kind === 'centreline' || m.kind === 'centreline_label') {
      vis = vis && showCentrelines;
    }
    updates.push(vis);
    indices.push(i);
  });

  Plotly.restyle('plot', {visible: updates}, indices);
}

function resetView(){
  Plotly.relayout('plot', {'scene.camera':{eye:{x:1.5, y:-1.5, z:0.6}}});
}
</script>
</body>
</html>'''

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    return html
