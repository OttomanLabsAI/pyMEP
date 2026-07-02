"""2D plan view of conduit runs + plan bend outlines."""
import json
import numpy as np
from .visualization import COLORS, classify_segment


def generate_plan_html(all_run_curves, collections=None,
                      plan_bend_outlines=None, straight_outlines=None,
                      output_path=None):
    """Generate interactive 2D plan view HTML (top-down).

    Shows pipe runs, start/end markers, plan bend outlines, straight outlines.
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
                "run_idx": ri, "col_id": col_id, "color": color,
                "name": run_label, "category": cat,
                "x": [p[0] for p in pts], "y": [p[1] for p in pts],
            })

        first_pt = np.array(curves[0]["points"][0]).tolist()
        last_pt  = np.array(curves[-1]["points"][-1]).tolist()
        run_starts.append({"col_id": col_id, "color": color, "name": run_label,
                           "x": [first_pt[0]], "y": [first_pt[1]]})
        run_ends.append({"col_id": col_id, "color": color, "name": run_label,
                         "x": [last_pt[0]], "y": [last_pt[1]]})

    # Outline traces (2D projection)
    outline_traces = []
    if plan_bend_outlines:
        for o in plan_bend_outlines:
            ox, oy = [], []
            for p in o["outer_arc_points"]:
                ox.append(p[0]); oy.append(p[1])
            # Line from outer end to inner end
            ox.append(o["outer_end"][0]); oy.append(o["outer_end"][1])
            ox.append(o["inner_end"][0]); oy.append(o["inner_end"][1])
            # Inner arc reversed
            for p in reversed(o["inner_arc_points"]):
                ox.append(p[0]); oy.append(p[1])
            # Close: inner start to outer start
            ox.append(o["inner_start"][0]); oy.append(o["inner_start"][1])
            ox.append(o["outer_start"][0]); oy.append(o["outer_start"][1])
            outline_traces.append({
                "x": ox, "y": oy,
                "col_id": o["collection_id"],
                "name": "Col {} Bend {}".format(o["collection_id"] + 1, o["bend_idx"]),
            })

    # Straight outline traces (plan: 4-corner polygon closed)
    straight_traces = []
    if straight_outlines:
        for o in straight_outlines:
            cs = [o["corner1"], o["corner2"], o["corner3"], o["corner4"], o["corner1"]]
            straight_traces.append({
                "x": [c[0] for c in cs],
                "y": [c[1] for c in cs],
                "col_id": o["collection_id"],
                "name": "Col {} Straight {}".format(o["collection_id"] + 1, o["segment_idx"]),
            })

    data = json.dumps({
        "run_segments": run_segments,
        "run_starts": run_starts,
        "run_ends": run_ends,
        "outlines": outline_traces,
        "straights": straight_traces,
    })

    col_checks = ""
    if collections:
        col_checks = '<h4>Collections</h4>\n'
        for ci in range(len(collections)):
            n = len(collections[ci])
            col_checks += (
                '<div class="col_row">'
                '<label><input type="checkbox" class="col_filter" id="f_col_{ci}" '
                'data-col="{ci}" checked onchange="applyFilters()"> '
                'Collection {num} ({n} runs)</label>'
                '<button class="only_btn" onclick="isolateCollection({ci})">only</button>'
                '</div>\n'.format(ci=ci, num=ci + 1, n=n))
        if len(collections) > 1:
            col_checks += (
                '<button class="show_all_btn" onclick="showAllCollections()">'
                'Show all collections</button>\n')
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
.controls{position:absolute;top:50px;right:10px;z-index:100;
  background:rgba(15,15,26,0.9);padding:10px 14px;border-radius:8px;
  font-size:12px;border:1px solid #333;max-height:calc(100vh - 70px);
  overflow-y:auto}
button{background:#4363d8;color:#fff;border:none;padding:6px 12px;
  border-radius:4px;cursor:pointer;margin:2px;font-size:11px}
button:hover{background:#5a7df5}
.filters label{display:block;margin:4px 0;cursor:pointer;font-size:12px;color:#ccc}
.filters input{margin-right:6px}
.filters h4{margin:8px 0 4px 0;font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px}
.divider{height:1px;background:#333;margin:8px 0}
.col_row{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:4px 0}
.col_row label{flex:1;margin:0}
.only_btn{background:transparent;color:#6b8fff;border:1px solid #2a3a66;
  padding:2px 6px;border-radius:3px;font-size:10px;cursor:pointer;margin:0;
  text-transform:uppercase;letter-spacing:0.5px}
.only_btn:hover{background:#2a3a66;color:#fff}
.show_all_btn{width:100%;margin:4px 0 2px 0;background:#2a3a66;font-size:10px;
  padding:4px 8px;text-transform:uppercase;letter-spacing:0.5px}
.show_all_btn:hover{background:#3a4a76}
</style>
</head>
<body>
<div id="plot"></div>
<div class="controls filters">
  ''' + col_checks + '''<h4>Show</h4>
  <label><input type="checkbox" id="f_straight" checked onchange="applyFilters()"> Straight pipes</label>
  <label><input type="checkbox" id="f_plan" checked onchange="applyFilters()"> Plan bends</label>
  <label><input type="checkbox" id="f_vert" checked onchange="applyFilters()"> Vertical bends</label>
  <div class="divider"></div>
  <label><input type="checkbox" id="f_markers" checked onchange="applyFilters()"> Start / End markers</label>
  <label><input type="checkbox" id="f_outlines" checked onchange="applyFilters()"> Plan bend outlines</label>
  <label><input type="checkbox" id="f_straights" checked onchange="applyFilters()"> Straight outlines</label>
</div>
<script>
var D = ''' + data + ''';
var traces = [];
var traceMeta = [];
var runLegendShown = {};

D.run_segments.forEach(function(s){
  var showLegend = !runLegendShown[s.name];
  runLegendShown[s.name] = true;
  traces.push({
    type:'scatter', mode:'lines',
    x:s.x, y:s.y,
    name:s.name, legendgroup:s.name,
    line:{color:s.color, width:2},
    showlegend:showLegend,
    hovertemplate:s.name+' ('+s.category+')<br>X:%{x:.0f} Y:%{y:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'segment', col_id:s.col_id, category:s.category});
});

D.run_starts.forEach(function(s){
  traces.push({type:'scatter', mode:'markers',
    x:s.x, y:s.y,
    name:s.name+' start',
    marker:{color:s.color, size:8, symbol:'circle', line:{color:'#fff', width:1}},
    showlegend:false,
    hovertemplate:s.name+' START<br>X:%{x:.0f} Y:%{y:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'start', col_id:s.col_id});
});

D.run_ends.forEach(function(s){
  traces.push({type:'scatter', mode:'markers',
    x:s.x, y:s.y,
    name:s.name+' end',
    marker:{color:s.color, size:8, symbol:'diamond', line:{color:'#fff', width:1}},
    showlegend:false,
    hovertemplate:s.name+' END<br>X:%{x:.0f} Y:%{y:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'end', col_id:s.col_id});
});

var outlineLegendShown = false;
D.outlines.forEach(function(o){
  traces.push({type:'scatter', mode:'lines',
    x:o.x, y:o.y,
    name: outlineLegendShown ? o.name : 'Plan bend outlines',
    legendgroup:'outlines',
    line:{color:'#ffcc00', width:3},
    showlegend: !outlineLegendShown,
    hovertemplate: o.name + '<br>X:%{x:.0f} Y:%{y:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'outline', col_id:o.col_id});
  outlineLegendShown = true;
});

var straightLegendShown = false;
D.straights.forEach(function(o){
  traces.push({type:'scatter', mode:'lines',
    x:o.x, y:o.y,
    name: straightLegendShown ? o.name : 'Straight outlines',
    legendgroup:'straights',
    line:{color:'#66ff66', width:2},
    showlegend: !straightLegendShown,
    hovertemplate: o.name + '<br>X:%{x:.0f} Y:%{y:.0f}<extra></extra>'
  });
  traceMeta.push({kind:'straight_out', col_id:o.col_id});
  straightLegendShown = true;
});

var layout = {
  title:{text:'Plan View (Top-Down)', font:{color:'#fff', size:16}},
  paper_bgcolor:'#0f0f1a',
  plot_bgcolor:'#0f0f1a',
  dragmode:'pan',
  xaxis:{title:'X (mm)', color:'#666', gridcolor:'#222', scaleanchor:'y', scaleratio:1},
  yaxis:{title:'Y (mm)', color:'#666', gridcolor:'#222'},
  legend:{font:{color:'#ccc', size:11}, bgcolor:'rgba(0,0,0,0.4)', x:0, y:1, xanchor:'left'},
  margin:{l:60, r:20, t:40, b:40}
};

Plotly.newPlot('plot', traces, layout, {responsive:true, scrollZoom:true});

function applyFilters(){
  var showStraight = document.getElementById('f_straight').checked;
  var showPlan     = document.getElementById('f_plan').checked;
  var showVert     = document.getElementById('f_vert').checked;
  var showMarkers  = document.getElementById('f_markers').checked;
  var showOutlines = document.getElementById('f_outlines').checked;
  var showStraights = document.getElementById('f_straights') ? document.getElementById('f_straights').checked : true;

  var colVisible = {};
  document.querySelectorAll('.col_filter').forEach(function(cb){
    colVisible[parseInt(cb.dataset.col)] = cb.checked;
  });

  var updates = [], indices = [];
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
    }
    updates.push(vis); indices.push(i);
  });
  Plotly.restyle('plot', {visible: updates}, indices);
}

function isolateCollection(ci){
  document.querySelectorAll('.col_filter').forEach(function(cb){
    cb.checked = (parseInt(cb.dataset.col) === ci);
  });
  applyFilters();
}

function showAllCollections(){
  document.querySelectorAll('.col_filter').forEach(function(cb){
    cb.checked = true;
  });
  applyFilters();
}
</script>
</body>
</html>'''

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
    return html
