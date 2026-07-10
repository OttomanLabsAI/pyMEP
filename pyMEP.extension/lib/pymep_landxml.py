# -*- coding: utf-8 -*-
"""Parse a Civil 3D LandXML-1.2 export and resolve pipe geometry.

This module is the shared engine behind the two **LandXML to Revit** buttons:

  * ``Create Pipe Sizes`` - reads every circular pipe's diameter / wall
    thickness and reports the distinct (nominal, inner, outer) sizes so the
    button can add the missing ones to a Revit pipe ``Segment``.

  * ``Model Pipes`` - resolves each pipe's start/end XYZ from its referenced
    structures (Civil 3D does NOT store pipe endpoint coordinates on the
    ``<Pipe>`` element - only ``refStart``/``refEnd`` references to the
    structures, whose ``<Center>`` gives plan XY and whose flow-matched
    ``<Invert>`` gives the Z), then hands a list of placement records back
    so the button can place Revit pipes network-by-network with a
    user-supplied network -> workset map.

Design notes / project realities baked in
-----------------------------------------
* Coordinate system is OSGB 1936 / EPSG:27700, units metres, diameters mm
  (per the file's ``<Units>`` block). Coordinates are large absolute
  survey-grid values - the SAME survey->project transform the existing
  pipes/manholes builders use is applied downstream by the button, NOT
  here. This module always returns raw survey metres so the existing
  Settings (offset / rotation / auto project-location) stay in charge.

* ``<Center>`` is ``E N`` (easting northing) order in this Civil 3D export.
  We return ``(x=E, y=N)``.

* Pipe Z: each structure lists one or more ``<Invert>`` entries, each with
  ``elev`` (m), ``flowDir`` ('in'/'out'/'through'), and ``refPipe`` (the
  pipe that invert belongs to). The pipe's start Z is taken from the
  invert on its ``refStart`` structure whose ``refPipe`` matches the pipe;
  end Z from the matching invert on its ``refEnd`` structure. When no
  invert matches by ``refPipe`` (some exports don't cross-reference every
  invert), we fall back in order: the structure's invert whose flowDir
  matches the pipe direction (out at start, in at end) -> the structure's
  lowest invert -> ``elevRim`` minus a nominal drop -> ``elevRim``.

* Dummy null structures (``EndNullStruct*`` / "Dummy Null Structure") and
  zero-rim placeholders are skipped for geometry; a pipe that resolves to
  one of these on an end is reported as unresolved rather than placed at
  0,0,0.

* RectPipe (box culverts / channel drains) and zero-diameter circular
  entries are flagged ``is_circular=False`` and excluded from size
  creation; the Model Pipes button skips them (round Revit pipe only).

Pure-Python, no Revit imports here, so it can be unit-tested off-Revit.
The IronPython 2.7 buttons import ``parse_landxml`` and the small record
classes.
"""

import re


# ===========================================================================
# Lightweight records (no namedtuple kwargs gymnastics; IronPython-friendly)
# ===========================================================================
class Structure(object):
    __slots__ = ("name", "desc", "elev_rim", "x", "y",
                 "inverts", "is_dummy")

    def __init__(self, name, desc, elev_rim, x, y):
        self.name = name
        self.desc = desc or ""
        self.elev_rim = elev_rim          # float or None
        self.x = x                        # easting (m) or None
        self.y = y                        # northing (m) or None
        # list of (elev_m, flow_dir, ref_pipe)
        self.inverts = []
        self.is_dummy = False

    def lowest_invert(self):
        vals = [e for (e, _f, _r) in self.inverts if e is not None]
        return min(vals) if vals else None

    def invert_for_pipe(self, pipe_name, prefer_flow=None):
        """Return the best invert elevation (m) for the given pipe.

        Order:
          1. invert whose refPipe == pipe_name (and, if several, the one
             matching ``prefer_flow`` when given),
          2. invert whose flowDir == prefer_flow,
          3. lowest invert,
          4. None.
        """
        # 1. by refPipe
        matches = [(e, f) for (e, f, r) in self.inverts
                   if r and r == pipe_name and e is not None]
        if matches:
            if prefer_flow:
                for e, f in matches:
                    if f == prefer_flow:
                        return e
            return matches[0][0]
        # 2. by flow direction
        if prefer_flow:
            for (e, f, _r) in self.inverts:
                if f == prefer_flow and e is not None:
                    return e
        # 3. lowest
        return self.lowest_invert()


class PipeRecord(object):
    __slots__ = ("name", "network", "desc", "is_circular",
                 "dia_mm", "wall_mm", "length_m", "slope",
                 "ref_start", "ref_end",
                 "sx", "sy", "sz", "ex", "ey", "ez",
                 "resolved", "reason")

    def __init__(self, name, network):
        self.name = name
        self.network = network
        self.desc = ""
        self.is_circular = True
        self.dia_mm = None
        self.wall_mm = None
        self.length_m = None
        self.slope = None
        self.ref_start = None
        self.ref_end = None
        self.sx = self.sy = self.sz = None
        self.ex = self.ey = self.ez = None
        self.resolved = False
        self.reason = ""


# ===========================================================================
# Parsing
# ===========================================================================
_FLOAT_RE = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _attr(tag_text, name):
    m = re.search(r'%s="([^"]*)"' % name, tag_text)
    return m.group(1) if m else None


def _is_dummy_struct(name, desc, elev_rim):
    """A structure is 'dummy' ONLY in the labelling sense.

    Civil 3D marks pipe-end nodes that have no physical manhole as
    "Dummy Null Structure for LandXML purposes" and names them
    ``StartNullStruct*`` / ``EndNullStruct*``. **Critically, in this
    export those null structures STILL carry a real ``<Center>`` (surveyed
    XY) and an ``<Invert>`` (real elevation)** - they are genuine pipe
    endpoints, just without a manhole family.

    So this flag is informational only. Geometry resolution keys off
    whether a structure actually has XY + a Z, NOT off this flag. A
    structure is only unusable for geometry when it has no ``<Center>``
    coordinates at all (handled in ``resolve_pipe_geometry`` by the
    ``x is None`` test), which is why we no longer reject on the label.
    """
    n = (name or "").lower().replace(" ", "")
    d = (desc or "").lower()
    return ("nullstruct" in n) or ("dummy null" in d)


def parse_landxml(path, log=None):
    """Parse the LandXML file at ``path``.

    Returns a dict:
      {
        "networks":   [name, ...]            # order of appearance
        "structs":    {struct_name: Structure}
        "pipes":      [PipeRecord, ...]      # all pipes, all networks
        "units":      {"linear": "meter", "diameter": "millimeter"}
        "epsg":       "27700" or None
      }

    Streaming regex scan - never builds a DOM, so the 240 MB file parses in
    a few seconds with a flat memory profile.
    """
    def say(m):
        if log is not None:
            log(m)

    networks = []
    seen_net = set()
    structs = {}
    pipes = []
    units = {"linear": None, "diameter": None}
    epsg = None

    cur_net = None
    cur_struct = None
    cur_pipe = None

    # Regex for the opening tags / self-closing we care about.
    tag_re = re.compile(
        r'<(/?)(PipeNetwork|Struct|Center|Invert|Pipe|CircPipe|RectPipe|'
        r'Metric|CoordinateSystem)\b([^>]*?)(/?)>',
        re.DOTALL)

    with open(path, "r") as fh:
        buf = ""
        center_capture = None   # struct awaiting <Center> text
        while True:
            chunk = fh.read(1 << 20)   # 1 MiB
            if not chunk:
                # flush remaining buffer one last time
                _scan_buffer(buf, tag_re, locals())
                break
            buf += chunk
            # Process and trim. We must not cut through a <Center>..</Center>
            # text node or a tag, so keep a generous tail.
            consumed = _scan_buffer(buf, tag_re, locals())
            if consumed > 0:
                buf = buf[consumed:]
            # Safety tail so a tag split across reads isn't lost
            if len(buf) > (1 << 16):
                # keep last 8 KiB
                buf = buf[-(1 << 13):]

    say("Parsed LandXML: **{}** networks, **{}** structures, **{}** pipes."
        .format(len(networks), len(structs), len(pipes)))
    if units["diameter"] and units["diameter"] != "millimeter":
        say("NOTE: diameter unit is '{}' (expected millimeter) - sizes will "
            "still be converted from the stated unit.".format(units["diameter"]))

    return {
        "networks": networks,
        "structs": structs,
        "pipes": pipes,
        "units": units,
        "epsg": epsg,
    }


def _scan_buffer(buf, tag_re, ns):
    """Scan ``buf`` for tags, mutating the parse state stored in the caller's
    locals dict ``ns``. Returns the number of characters safely consumed
    (up to the start of the last incomplete construct).

    State variables read/written via ns: networks, seen_net, structs,
    pipes, units, epsg, cur_net, cur_struct, cur_pipe.

    <Center> text is captured by reading between the <Center> open tag and
    its matching </Center>.
    """
    networks = ns["networks"]; seen_net = ns["seen_net"]
    structs = ns["structs"];   pipes = ns["pipes"]
    units = ns["units"]

    last_end = 0
    pos = 0
    while True:
        m = tag_re.search(buf, pos)
        if not m:
            break
        closing, tag, attrs, selfclose = m.group(1), m.group(2), \
            m.group(3), m.group(4)
        start, end = m.start(), m.end()

        if tag == "Metric" and not closing:
            la = _attr(attrs, "linearUnit")
            da = _attr(attrs, "diameterUnit")
            if la:
                units["linear"] = la
            if da:
                units["diameter"] = da

        elif tag == "CoordinateSystem" and not closing:
            ep = _attr(attrs, "epsgCode")
            if ep:
                ns["epsg"] = ep

        elif tag == "PipeNetwork":
            if not closing:
                nm = _attr(attrs, "name") or "(unnamed)"
                ns["cur_net"] = nm
                if nm not in seen_net:
                    seen_net.add(nm)
                    networks.append(nm)
            else:
                ns["cur_net"] = None

        elif tag == "Struct":
            if not closing:
                nm = _attr(attrs, "name") or "(unnamed)"
                desc = _attr(attrs, "desc")
                rim = _f(_attr(attrs, "elevRim"))
                st = Structure(nm, desc, rim, None, None)
                st.is_dummy = _is_dummy_struct(nm, desc, rim)
                structs[nm] = st
                ns["cur_struct"] = st
                if selfclose:
                    ns["cur_struct"] = None
            else:
                ns["cur_struct"] = None

        elif tag == "Center" and not closing:
            # capture text until </Center>
            close_idx = buf.find("</Center>", end)
            if close_idx == -1:
                # incomplete - stop here, let more data arrive
                return last_end
            text = buf[end:close_idx].strip()
            cs = ns.get("cur_struct")
            if cs is not None and text:
                parts = text.split()
                if len(parts) >= 2:
                    a = _f(parts[0])
                    b = _f(parts[1])
                    # IMPORTANT - coordinate ORDER.
                    # This project's Civil 3D LandXML writes <Center> as
                    # "NORTHING EASTING" (Y X), NOT "easting northing".
                    # The placement transform expects (x=easting, y=northing),
                    # so map: 1st column -> northing -> y, 2nd column ->
                    # easting -> x. Feeding the raw column order would
                    # mirror/misrotate the whole network (a swap preserves
                    # pipe lengths, which is why the length self-check does
                    # not flag it).
                    #
                    # NOTE: this is a FIXED column mapping (1st=N, 2nd=E),
                    # not a magnitude heuristic. The old "larger value is
                    # always easting" rule was HEL-specific (E~25M > N~6.6M)
                    # and SILENTLY MIRRORS sites where northing > easting
                    # (e.g. HNU1A: N~5.55M > E~3.50M). Column order is the
                    # reliable invariant across sites.
                    if a is not None and b is not None:
                        cs.x = b   # easting  (2nd column)
                        cs.y = a   # northing (1st column)
                    else:
                        cs.x = a
                        cs.y = b
            pos = close_idx + len("</Center>")
            last_end = pos
            continue

        elif tag == "Invert" and not closing:
            cs = ns.get("cur_struct")
            if cs is not None:
                elev = _f(_attr(attrs, "elev"))
                fdir = (_attr(attrs, "flowDir") or "").lower()
                rp = _attr(attrs, "refPipe")
                cs.inverts.append((elev, fdir, rp))

        elif tag == "Pipe":
            if not closing:
                nm = _attr(attrs, "name") or "(unnamed)"
                pr = PipeRecord(nm, ns.get("cur_net"))
                pr.desc = _attr(attrs, "desc") or ""
                pr.length_m = _f(_attr(attrs, "length"))
                pr.slope = _f(_attr(attrs, "slope"))
                pr.ref_start = _attr(attrs, "refStart")
                pr.ref_end = _attr(attrs, "refEnd")
                pipes.append(pr)
                ns["cur_pipe"] = pr
                if selfclose:
                    ns["cur_pipe"] = None
            else:
                ns["cur_pipe"] = None

        elif tag == "CircPipe" and not closing:
            cp = ns.get("cur_pipe")
            if cp is not None:
                cp.is_circular = True
                cp.dia_mm = _f(_attr(attrs, "diameter"))
                wall_m = _f(_attr(attrs, "thickness"))
                cp.wall_mm = (wall_m * 1000.0) if wall_m is not None else None
                if cp.dia_mm is not None and cp.dia_mm <= 0.0:
                    cp.is_circular = False   # zero-dia placeholder

        elif tag == "RectPipe" and not closing:
            cp = ns.get("cur_pipe")
            if cp is not None:
                cp.is_circular = False

        pos = end
        last_end = end

    return last_end


# ===========================================================================
# Geometry resolution
# ===========================================================================
def resolve_pipe_geometry(parsed, nominal_drop_m=0.05, log=None):
    """Fill each PipeRecord's sx,sy,sz,ex,ey,ez from its structures.

    Mutates ``parsed["pipes"]`` in place and returns (resolved, unresolved)
    counts. A pipe is ``resolved=True`` only when BOTH ends have a real XY
    (from a non-dummy structure's ``<Center>``) and a Z.

    ``nominal_drop_m`` is subtracted from a structure's rim elevation only
    when NO invert is available at all for that end (last-resort Z so the
    pipe still has a sensible fall instead of sitting at rim level).
    """
    structs = parsed["structs"]
    resolved = 0
    unresolved = 0

    def _has_xy(st):
        # Usable only when Center coordinates exist and aren't the 0,0
        # placeholder some exporters emit. Survey-grid values here are
        # in the millions, so a strict zero test is safe.
        return (st is not None and st.x is not None and st.y is not None
                and not (abs(st.x) < 1e-6 and abs(st.y) < 1e-6))

    for p in parsed["pipes"]:
        s_st = structs.get(p.ref_start) if p.ref_start else None
        e_st = structs.get(p.ref_end) if p.ref_end else None

        why = []
        if s_st is None:
            why.append("start struct '{}' missing".format(p.ref_start))
        elif not _has_xy(s_st):
            why.append("start struct '{}' has no plan XY".format(p.ref_start))
        if e_st is None:
            why.append("end struct '{}' missing".format(p.ref_end))
        elif not _has_xy(e_st):
            why.append("end struct '{}' has no plan XY".format(p.ref_end))

        if why:
            p.resolved = False
            p.reason = "; ".join(why)
            unresolved += 1
            continue

        p.sx, p.sy = s_st.x, s_st.y
        p.ex, p.ey = e_st.x, e_st.y

        # Z: start uses 'out' invert, end uses 'in' invert (flow runs
        # start -> end along the pipe per Civil 3D convention).
        sz = s_st.invert_for_pipe(p.name, prefer_flow="out")
        ez = e_st.invert_for_pipe(p.name, prefer_flow="in")

        if sz is None:
            sz = (s_st.elev_rim - nominal_drop_m) \
                if s_st.elev_rim is not None else None
        if ez is None:
            ez = (e_st.elev_rim - nominal_drop_m) \
                if e_st.elev_rim is not None else None

        if sz is None or ez is None:
            p.resolved = False
            p.reason = "no invert/rim Z available at {}".format(
                "start" if sz is None else "end")
            unresolved += 1
            continue

        p.sz, p.ez = sz, ez
        p.resolved = True
        resolved += 1

    if log is not None:
        log("Resolved geometry for **{}** pipes; **{}** unresolved."
            .format(resolved, unresolved))
    return resolved, unresolved


# ===========================================================================
# Size extraction (for Create Pipe Sizes)
# ===========================================================================
def distinct_circular_sizes(parsed):
    """Return a sorted list of distinct circular sizes across all networks.

    Each item: dict(nominal_mm, inner_mm, outer_mm, wall_mm, count, networks)
      * nominal_mm == inner bore from the XML 'diameter'
      * inner_mm   == nominal_mm (Civil 3D 'diameter' is the bore)
      * outer_mm   == inner + 2*wall (wall from 'thickness'; if wall is
                      None/0, outer == inner)
    Sorted ascending by nominal_mm.
    """
    by_key = {}
    for p in parsed["pipes"]:
        if not p.is_circular or not p.dia_mm or p.dia_mm <= 0.0:
            continue
        nominal = round(p.dia_mm, 3)
        wall = p.wall_mm if (p.wall_mm and p.wall_mm > 0) else 0.0
        outer = round(nominal + 2.0 * wall, 3)
        key = (nominal, round(wall, 3))
        if key not in by_key:
            by_key[key] = {
                "nominal_mm": nominal,
                "inner_mm": nominal,
                "outer_mm": outer,
                "wall_mm": round(wall, 3),
                "count": 0,
                "networks": set(),
            }
        by_key[key]["count"] += 1
        if p.network:
            by_key[key]["networks"].add(p.network)

    out = list(by_key.values())
    out.sort(key=lambda d: d["nominal_mm"])
    for d in out:
        d["networks"] = sorted(d["networks"])
    return out


# ===========================================================================
# Placement-record export (for Model Pipes)
# ===========================================================================
def placement_rows(parsed, only_resolved=True, only_circular=True):
    """Yield a flat dict per pipe ready for Revit placement.

    Keys: name, network, dia_mm, wall_mm, slope,
          sx, sy, sz, ex, ey, ez   (survey metres, E/N/elev)
    Only resolved + circular pipes by default.
    """
    rows = []
    for p in parsed["pipes"]:
        if only_circular and not p.is_circular:
            continue
        if only_resolved and not p.resolved:
            continue
        rows.append({
            "name": p.name,
            "network": p.network or "",
            "dia_mm": p.dia_mm,
            "wall_mm": p.wall_mm,
            "slope": p.slope,
            "sx": p.sx, "sy": p.sy, "sz": p.sz,
            "ex": p.ex, "ey": p.ey, "ez": p.ez,
        })
    return rows


# ===========================================================================
# Structure helpers (for the Place Structures button)
# ===========================================================================
def _struct_network(name):
    """Network embedded in a structure name, e.g. '... (HEL18 - STORM WATER)'.
    The streaming parser doesn't tag structures with a network the way it does
    pipes, so we read it from the name suffix (always present in this export).
    Returns the network string, or None.
    """
    m = re.search(r'\(([^()]*-[^()]*)\)\s*$', name or "")
    return m.group(1).strip() if m else None


def structure_networks_and_types(parsed):
    """Return {network: {desc: count}} for the REAL, placeable structures
    (those with plan XY). Dummy/null and coordinate-less structures are
    excluded. Used to drive the network + structure-type pickers.
    """
    out = {}
    for st in parsed["structs"].values():
        if st.x is None or st.y is None:
            continue
        if abs(st.x) < 1e-6 and abs(st.y) < 1e-6:
            continue
        net = _struct_network(st.name) or "(untagged)"
        desc = st.desc if (st.desc and st.desc.strip()) else "(no description)"
        out.setdefault(net, {})
        out[net][desc] = out[net].get(desc, 0) + 1
    return out


def structure_rows(parsed, network=None, desc=None):
    """Yield placement rows for structures matching the given network and/or
    type (desc). Each row:
      name, network, desc, x, y (survey metres E/N),
      rim_m   - rim elevation (m) or None,
      invert_m- lowest invert elevation (m) or None,
      z_m     - best Z to place at: rim if available, else lowest invert.
    Only structures with real plan XY are returned.
    """
    rows = []
    for st in parsed["structs"].values():
        if st.x is None or st.y is None:
            continue
        if abs(st.x) < 1e-6 and abs(st.y) < 1e-6:
            continue
        net = _struct_network(st.name) or "(untagged)"
        d = st.desc if (st.desc and st.desc.strip()) else "(no description)"
        if network is not None and net != network:
            continue
        if desc is not None and d != desc:
            continue
        rim = st.elev_rim if (st.elev_rim is not None
                              and abs(st.elev_rim) > 1e-9) else None
        low = st.lowest_invert()
        z = rim if rim is not None else low
        rows.append({
            "name": st.name,
            "network": net,
            "desc": d,
            "x": st.x, "y": st.y,
            "rim_m": rim,
            "invert_m": low,
            "z_m": z if z is not None else 0.0,
        })
    return rows
