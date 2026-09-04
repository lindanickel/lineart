"""
S-Bahn Berlin – Turn-basierter Netzplan-Generator (eigenstaendig)
=================================================================

Vollstaendig unabhaengige Engine: Eine Linie wird NICHT ueber feste
Stationskoordinaten definiert, sondern ueber eine Startrichtung + eine
Folge RELATIVER Turns. Die Stationspositionen ergeben sich aus dem
Ablaufen ("walk") dieser Turn-Folge. Diese Datei enthaelt alles selbst
(Config, Kompass, Bündelung, Rendering, Turn-Modell, Liniendaten) und
haengt von keiner anderen Datei ab.

Ausfuehren:
  python3 turn_engine.py   ->  outputs/netzplan_turns.svg

LINIENDEFINITION (TurnLine)
---------------------------
  start   : Startrichtung ABSOLUT in Grad (0 = Norden, im Uhrzeigersinn).
            Nur dieser eine Wert ist absolut.
  anchor  : Startkoordinate (x, y) ODER Name einer bereits gepinnten Station.
  steps   : Folge aus
              - Stationsname (str)
              - relativer Turn (int/float, Grad; + = im Uhrzeigersinn/rechts,
                - = gegen den Uhrzeigersinn/links)
              - (delta, laenge) -- relativer Turn mit eigener Knick-Bein-Laenge
            Turns sind IMMER relativ zur aktuellen Fahrtrichtung. Nur
            Vielfache von 45 Grad sind erlaubt. Mehrere Turns hintereinander
            (ohne Station dazwischen) ergeben eine S-Kurve.

KONSISTENZ (Buendelung): Geteilte Stationen muessen fuer alle Linien
dieselbe Koordinate haben. Deshalb "pin-on-first-definition" -- die erste
Linie, die eine Station platziert, fixiert sie; spaetere Linien SNAPPEN
beim Erreichen darauf (mit Warnung, falls der Walk weit daneben lag).
"""

from dataclasses import dataclass
from math import atan2, degrees, hypot
from typing import Dict, List, Optional, Tuple, Union

Pt = Tuple[float, float]

# ===========================================================================
# KONFIGURATION
# ===========================================================================

@dataclass
class Config:
    grid: float = 12.0        # px pro Rastereinheit
    margin: float = 96.0      # Rand um den Plot (dynamisch drumherum)
    line_scale: float = 1.5   # globaler Faktor auf alle Linien-Masse
    text_scale: float = 1.0   # globaler Faktor auf alle Schriftgroessen
    # Basiswerte als OFFIZIELLE PROPORTIONEN zur Linienstaerke (Vorlage:
    # Linienstaerke 8 -> line_gap 12 (1.5x), Punkt-Radius 3 (0.375x),
    # Umsteige-Rahmen 2 (0.25x), Versalhoehe ~11 (1.375x) => Schrift ~1.9x).
    line_width: float = 6.0   # Strichstaerke der Linien (Anker)
    line_gap: float = 9.0     # Abstand paralleler Linien (= 1.5 x line_width)
    corner_radius: float = 6.0  # Rundungsradius an Knicken (0 = scharf)
    dot_r: float = 2.25      # weisser Stationspunkt (= 0.375 x line_width)
    hub_pad: float = 2.0      # Polster des weissen Pills um das Buendel
    hub_stroke: float = 1.05  # schwarzer Rahmen der Umsteige-Pills (0.25x)
    # Versalhoehe der Vorlage = 1.375 x Linienstaerke. Bei finaler
    # Linienstaerke 6.3 -> Versalhoehe ~8.66 -> em ~12.2 (Versal ~0.71 em).
    font: float = 10        # Schriftgroesse Stationsnamen
    hub_font: float = 10    # Schriftgroesse Umsteigebahnhoefe
    # Schriftfamilie: Vorlage nutzt "Transit" (Berliner Verkehrs-Hausschrift,
    # proprietaer, grosse x-Hoehe). Freie Naeherung: Fira Sans (auch von
    # Spiekermann); System-Fallback Helvetica/Arial.
    font_family: str = "'Fira Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif"
    label_bold: bool = True   # Stationsnamen fett (wie Vorlage)
    badges: bool = True       # Linien-Badges (S 1 ...) an Endstationen

    def __post_init__(self):
        for f in ("line_width", "line_gap", "corner_radius", "dot_r",
                   "hub_pad", "hub_stroke"):
            setattr(self, f, getattr(self, f) * self.line_scale)
        for f in ("font", "hub_font"):
            setattr(self, f, getattr(self, f) * self.text_scale)

CFG = Config()

# Kompass: Grad (0 = Norden, im Uhrzeigersinn) -> Einheitsvektor im Raster
# (x nach rechts, y nach unten). Nur Vielfache von 45 Grad sind gueltig.
COMPASS: Dict[int, Pt] = {
    0: (0, -1), 45: (1, -1), 90: (1, 0), 135: (1, 1),
    180: (0, 1), 225: (-1, 1), 270: (-1, 0), 315: (-1, -1),
}

# ===========================================================================
# GEOMETRIE-HELFER (Buendelung / Versatz / Rundung)
# ===========================================================================

def seg_key(p: Pt, q: Pt):
    a = (round(p[0], 3), round(p[1], 3))
    b = (round(q[0], 3), round(q[1], 3))
    return (a, b) if a <= b else (b, a)


def _norm(v: Pt) -> Pt:
    l = hypot(v[0], v[1])
    return (v[0] / l, v[1] / l)


def canonical_normal(key) -> Pt:
    """Einheitsnormale des Segments, unabhaengig von der Fahrtrichtung.
    Zeigt immer nach Osten bzw. (bei senkrechten Segmenten) nach Sueden,
    damit ein NIEDRIGER rank immer weiter Norden/Westen liegt."""
    (ax, ay), (bx, by) = key
    dx, dy = bx - ax, by - ay
    n = _norm((-dy, dx))
    if n[0] < -1e-9 or (abs(n[0]) <= 1e-9 and n[1] < 0):
        n = (-n[0], -n[1])
    return n


def _intersect(a1: Pt, b1: Pt, a2: Pt, b2: Pt) -> Optional[Pt]:
    r = (b1[0] - a1[0], b1[1] - a1[1])
    s = (b2[0] - a2[0], b2[1] - a2[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-9:
        return None
    t = ((a2[0] - a1[0]) * s[1] - (a2[1] - a1[1]) * s[0]) / den
    return (a1[0] + t * r[0], a1[1] + t * r[1])


def offset_polyline(pts: List[Pt], vecs: List[Pt], closed: bool) -> List[Pt]:
    """Verschiebt jede Kante um ihren Vektor und verbindet die Kanten.

    a) Echter Knick: Miter-Join ueber den Schnittpunkt der versetzten Geraden.
    b) Gleiche Richtung, anderer Offset (Buendel aendert sich im geraden
       Korridor): 45-Grad-Rampe statt schraegem Versprung.
    Rueckgabe: (versetzte Punktfolge, idx_map)."""
    n = len(pts)
    shifted = [((pts[i][0] + vecs[i][0], pts[i][1] + vecs[i][1]),
                (pts[i + 1][0] + vecs[i][0], pts[i + 1][1] + vecs[i][1]))
               for i in range(n - 1)]
    out: List[Pt] = [shifted[0][0]]
    idx_map: List[int] = [0]
    for i in range(1, n - 1):
        d_prev = _norm((pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
        d_next = _norm((pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))
        collinear = (abs(d_prev[0] - d_next[0]) < 1e-9
                     and abs(d_prev[1] - d_next[1]) < 1e-9)

        if collinear:
            delta = hypot(vecs[i][0] - vecs[i - 1][0],
                          vecs[i][1] - vecs[i - 1][1])
            if delta > 1e-9:
                h = delta / 2
                a, b = shifted[i - 1][1], shifted[i][0]
                out.append((a[0] - d_prev[0] * h, a[1] - d_prev[1] * h))
                idx_map.append(len(out) - 1)
                out.append((b[0] + d_next[0] * h, b[1] + d_next[1] * h))
                continue
            out.append(shifted[i][0])
            idx_map.append(len(out) - 1)
            continue

        p = _intersect(*shifted[i - 1], *shifted[i])
        if p is None or hypot(p[0] - pts[i][0], p[1] - pts[i][1]) > 60:
            p = ((shifted[i - 1][1][0] + shifted[i][0][0]) / 2,
                 (shifted[i - 1][1][1] + shifted[i][0][1]) / 2)
        out.append(p)
        idx_map.append(len(out) - 1)
    out.append(shifted[-1][1])
    idx_map.append(len(out) - 1)
    if closed:
        p = _intersect(*shifted[-1], *shifted[0])
        if p is not None:
            out[0] = out[-1] = p
    return out, idx_map


def rounded_path_d(pts: List[Pt], closed: bool, radius: float) -> str:
    """SVG-Pfaddaten mit abgerundeten statt scharfen Knicken. Radius wird pro
    Ecke auf hoechstens die halbe Nachbarstrecke begrenzt."""
    def sub(a, b): return (a[0] - b[0], a[1] - b[1])
    def add(a, b): return (a[0] + b[0], a[1] + b[1])
    def scale(a, s): return (a[0] * s, a[1] * s)

    def fillet(corner: Pt, prev_p: Pt, next_p: Pt) -> Tuple[Pt, Pt]:
        len1 = hypot(*sub(corner, prev_p))
        len2 = hypot(*sub(next_p, corner))
        r = min(radius, len1 / 2, len2 / 2) if len1 > 1e-9 and len2 > 1e-9 else 0
        d1 = _norm(sub(corner, prev_p)) if len1 > 1e-9 else (0.0, 0.0)
        d2 = _norm(sub(next_p, corner)) if len2 > 1e-9 else (0.0, 0.0)
        return sub(corner, scale(d1, r)), add(corner, scale(d2, r))

    if radius <= 0:
        d = "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in pts)
        return d + (" Z" if closed else "")

    if closed:
        core = pts[:-1]
        m = len(core)
        if m < 3:
            d = "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in pts)
            return d + " Z"
        p_in, p_out = [None] * m, [None] * m
        for i in range(m):
            p_in[i], p_out[i] = fillet(core[i], core[i - 1], core[(i + 1) % m])
        start = p_out[0]
        parts = [f"M {start[0]:.3f} {start[1]:.3f}"]
        for i in range(m):
            nxt = (i + 1) % m
            parts.append(f"L {p_in[nxt][0]:.3f} {p_in[nxt][1]:.3f}")
            parts.append(f"Q {core[nxt][0]:.3f} {core[nxt][1]:.3f} "
                         f"{p_out[nxt][0]:.3f} {p_out[nxt][1]:.3f}")
        return " ".join(parts)

    m = len(pts)
    if m < 3:
        return "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in pts)
    parts = [f"M {pts[0][0]:.3f} {pts[0][1]:.3f}"]
    for i in range(1, m - 1):
        p_in, p_out = fillet(pts[i], pts[i - 1], pts[i + 1])
        parts.append(f"L {p_in[0]:.3f} {p_in[1]:.3f}")
        parts.append(f"Q {pts[i][0]:.3f} {pts[i][1]:.3f} "
                     f"{p_out[0]:.3f} {p_out[1]:.3f}")
    parts.append(f"L {pts[-1][0]:.3f} {pts[-1][1]:.3f}")
    return " ".join(parts)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def auto_label(direction: Pt, clearance: float) -> Tuple[float, float, str]:
    """Label-Versatz (dx, dy, text-anchor) aus der lokalen Fahrtrichtung.
      - waagerechte Linie -> unterhalb, zentriert
      - senkrechte Linie  -> rechts, linksbuendig
      - diagonale Linie   -> obere (noerdliche) Seite, Text waechst weg."""
    ux, uy = _norm(direction) if direction != (0, 0) else (1.0, 0.0)
    if abs(uy) < 0.35:
        return (0.0, clearance + CFG.font, "middle")
    if abs(ux) < 0.35:
        return (clearance, 0.35 * CFG.font, "start")
    nx, ny = -uy, ux
    if ny > 0:
        nx, ny = -nx, -ny
    dx, dy = nx * clearance, ny * clearance
    anchor = "start" if dx >= 0 else "end"
    return (dx, dy - 1.0, anchor)


# ===========================================================================
# RENDERING (Buendelung -> SVG)
# ===========================================================================

def build_svg(lines, coords, paths, st_maps, hubs=None, hub_rects=None,
              labels=None, seg_ranks=None, auto_labels=True) -> str:
    hubs = set() if hubs is None else set(hubs)
    hub_rects = {} if hub_rects is None else hub_rects
    labels = {} if labels is None else labels
    seg_ranks = {} if seg_ranks is None else seg_ranks

    base_rank = {name: (ln.rank if ln.rank is not None else i)
                 for i, (name, ln) in enumerate(lines.items())}

    # Pass 2: Segmentbelegung + Zuordnung Geometrie -> Stationspaar
    usage: Dict[object, List[str]] = {}
    seg_pair: Dict[object, frozenset] = {}
    for lid, line in lines.items():
        pts = paths[lid]
        rev_map = {i: n for i, n in st_maps[lid].items()}
        for i in range(len(pts) - 1):
            k = seg_key(pts[i], pts[i + 1])
            usage.setdefault(k, [])
            if lid not in usage[k]:
                usage[k].append(lid)
            a, b = rev_map.get(i), rev_map.get(i + 1)
            if a and b and k not in seg_pair:
                seg_pair[k] = frozenset((a, b))

    for k, group in usage.items():
        ov = seg_ranks.get(seg_pair.get(k, frozenset()), {})
        group.sort(key=lambda l: (ov.get(l, base_rank[l]), l))

    # Grid -> px
    all_pts = [p for pts in paths.values() for p in pts]
    minx = min(p[0] for p in all_pts)
    miny = min(p[1] for p in all_pts)
    maxx = max(p[0] for p in all_pts)
    maxy = max(p[1] for p in all_pts)

    def px(p: Pt) -> Pt:
        return ((p[0] - minx) * CFG.grid + CFG.margin,
                (p[1] - miny) * CFG.grid + CFG.margin)

    W = (maxx - minx) * CFG.grid + 2 * CFG.margin
    H = (maxy - miny) * CFG.grid + 2 * CFG.margin

    # Pass 3: versetzte Pfade in px
    off_paths: Dict[str, List[Pt]] = {}
    idx_maps: Dict[str, List[int]] = {}
    for lid, pts in paths.items():
        vecs: List[Pt] = []
        for i in range(len(pts) - 1):
            key = seg_key(pts[i], pts[i + 1])
            group = usage[key]
            slot = group.index(lid)
            off = (slot - (len(group) - 1) / 2) * CFG.line_gap
            nx, ny = canonical_normal(key)
            vecs.append((nx * off, ny * off))
        off_paths[lid], idx_maps[lid] = offset_polyline(
            [px(p) for p in pts], vecs, lines[lid].closed)

    svg: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" '
        f'height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}" '
        f'font-family="{esc(CFG.font_family)}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]

    # Linien
    for lid, opts in off_paths.items():
        d = rounded_path_d(opts, lines[lid].closed, CFG.corner_radius)
        svg.append(f'<path d="{d}" fill="none" stroke="{lines[lid].color}" '
                   f'stroke-width="{CFG.line_width}" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')

    # Umsteige-Pills (weiss, ueber den Linien) + Richtung je Station
    hub_pts: Dict[str, List[Pt]] = {}
    hub_dir: Dict[str, Pt] = {}
    for lid, smap in st_maps.items():
        for idx, name in smap.items():
            hub_pts.setdefault(name, []).append(
                off_paths[lid][idx_maps[lid][idx]])
            if name not in hub_dir:
                p = paths[lid]
                j = idx if idx < len(p) - 1 else idx - 1
                hub_dir[name] = _norm((p[j + 1][0] - p[j][0],
                                       p[j + 1][1] - p[j][1]))

    def hub_pill(name: str):
        c = px(coords[name])
        if name in hub_rects:
            w, h = hub_rects[name]
            w *= CFG.line_scale
            h *= CFG.line_scale
            return (f'<rect x="{c[0]-w/2:.1f}" y="{c[1]-h/2:.1f}" '
                    f'width="{w:.1f}" height="{h:.1f}" '
                    f'rx="{7 * CFG.line_scale:.1f}" fill="white" '
                    f'stroke="black" stroke-width="{CFG.hub_stroke}"/>')
        pts = hub_pts.get(name, [c])
        d = hub_dir.get(name, (1, 0))
        nx, ny = -d[1], d[0]
        proj = [(p[0]-c[0]) * nx + (p[1]-c[1]) * ny for p in pts]
        length = (max(proj) - min(proj)) + CFG.line_width + 2 * CFG.hub_pad
        thick = CFG.line_width + 2 * CFG.hub_pad
        ang = degrees(atan2(ny, nx))
        return (f'<g transform="translate({c[0]:.1f},{c[1]:.1f}) '
                f'rotate({ang:.1f})"><rect x="{-length/2:.1f}" '
                f'y="{-thick/2:.1f}" width="{length:.1f}" '
                f'height="{thick:.1f}" rx="{thick/2:.1f}" fill="white" '
                f'stroke="black" stroke-width="{CFG.hub_stroke}"/></g>')

    hub_names = set(hubs) | set(hub_rects)
    for name in hub_names:
        svg.append(hub_pill(name))

    # Stationspunkte (weisse Dots pro Linie)
    seen = set()
    for lid, smap in st_maps.items():
        for idx, name in smap.items():
            if name in hub_names or (lid, name) in seen:
                continue
            seen.add((lid, name))
            x, y = off_paths[lid][idx_maps[lid][idx]]
            svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{CFG.dot_r}" '
                       f'fill="white"/>')

    # Linien-Badges an Endstationen
    if CFG.badges:
        for lid, line in lines.items():
            if line.closed:
                continue
            bs = CFG.text_scale
            for end, other in ((0, 1), (-1, -2)):
                p, q = off_paths[lid][end], off_paths[lid][other]
                d = _norm((p[0] - q[0], p[1] - q[1]))
                gap = 17 * max(CFG.line_scale, bs)
                bx, by = p[0] + d[0] * gap, p[1] + d[1] * gap
                svg.append(
                    f'<g><rect x="{bx-12*bs:.1f}" y="{by-6.5*bs:.1f}" '
                    f'width="{24*bs:.1f}" height="{13*bs:.1f}" '
                    f'rx="{6.5*bs:.1f}" fill="{line.color}"/>'
                    f'<text x="{bx:.1f}" y="{by+3*bs:.1f}" '
                    f'font-size="{8*bs:.1f}" font-weight="bold" fill="white" '
                    f'text-anchor="middle">{lid}</text></g>')

    # Beschriftung: auto aus Geometrie, manueller Override hat Vorrang
    for name, coord in coords.items():
        if not any(name in m.values() for m in st_maps.values()):
            continue
        x, y = px(coord)
        if name in labels:
            dx, dy, anchor = labels[name]
        elif auto_labels:
            clr = CFG.line_width / 2 + CFG.hub_pad + 2
            dx, dy, anchor = auto_label(hub_dir.get(name, (1, 0)), clr)
        else:
            dx, dy, anchor = (9, -5, "start")
        big = name in hub_names
        fs = CFG.hub_font if big else CFG.font
        fw = ' font-weight="bold"' if (big or CFG.label_bold) else ""
        svg.append(f'<text x="{x+dx:.1f}" y="{y+dy:.1f}" font-size="{fs}"'
                   f'{fw} fill="#111" text-anchor="{anchor}">'
                   f'{esc(name)}</text>')

    svg.append("</svg>")

    bundles = sorted(usage.values(), key=len)[-1] if usage else []
    print(f"Linien: {len(lines)}  |  Stationen: "
          f"{len({n for m in st_maps.values() for n in m.values()})}  |  "
          f"Segmente: {len(usage)}  |  groesstes Buendel: {len(bundles)} "
          f"({', '.join(bundles)})")
    return "\n".join(svg)


# ===========================================================================
# TURN-MODELL
# ===========================================================================

@dataclass
class TurnLine:
    color: str
    start: float                     # Startrichtung ABSOLUT, Grad (0=N, im UZS)
    anchor: Union[Pt, str]           # Startkoordinate ODER Name gepinnter Station
    steps: List[object]              # Stationsnamen + relative Turns
    spacing: float = 2.0             # Rasterschritte je Stationssprung
    corner_step: float = 2.0         # Default-Laenge eines Knick-Beins
    direction: str = ""
    rank: Optional[float] = None
    closed: bool = False


def _turn_delta(step) -> Tuple[float, Optional[float]]:
    """Turn-Schritt -> (relative Drehung in Grad, Knick-Bein-Laenge|None)."""
    if isinstance(step, tuple):
        return step[0], step[1]
    return step, None


def walk_turnline(line: TurnLine, pins: Dict[str, Pt]
                  ) -> Tuple[List[Pt], Dict[int, str], Dict[str, Pt]]:
    """Laeuft eine TurnLine ab und leitet daraus Geometrie + Positionen ab.

    Rueckgabe:
      pts     : vollstaendige Punktfolge im Raster
      st_idx  : {Pfadindex: Stationsname}
      newpins : {Stationsname: Rasterkoordinate} -- neu platzierte Stationen
    """
    bearing = int(round(line.start)) % 360
    if bearing not in COMPASS:
        raise ValueError(f"{line.color}: Startrichtung {line.start} ist kein "
                         f"Vielfaches von 45 Grad")
    if isinstance(line.anchor, str):
        if line.anchor not in pins:
            raise ValueError(
                f"anchor '{line.anchor}' ist noch nicht gepinnt -- entweder "
                f"eine fruehere Linie muss ihn platzieren oder eine "
                f"Koordinate als anchor angeben")
        pos = pins[line.anchor]
    else:
        pos = (float(line.anchor[0]), float(line.anchor[1]))

    pts: List[Pt] = [pos]
    st_idx: Dict[int, str] = {}
    newpins: Dict[str, Pt] = {}
    first_station = True

    def emit(newpos: Pt):
        if hypot(newpos[0] - pts[-1][0], newpos[1] - pts[-1][1]) > 1e-9:
            pts.append(newpos)

    for step in line.steps:
        if isinstance(step, str):
            if first_station:
                if step in pins:
                    pos = pins[step]
                pts[0] = pos
                first_station = False
            else:
                ux, uy = COMPASS[bearing]
                pos = (pos[0] + line.spacing * ux, pos[1] + line.spacing * uy)
                if step in pins:
                    walked = pos
                    pos = pins[step]
                    if hypot(walked[0] - pos[0], walked[1] - pos[1]) > 1e-6:
                        print(f"  WARNUNG: {line.color}: Station '{step}' "
                              f"gelaufen bei {walked}, gepinnt bei {pos} "
                              f"-- Snap; Turn-Definition pruefen")
                emit(pos)
            st_idx[len(pts) - 1] = step
            newpins.setdefault(step, pos)
        else:
            delta, length = _turn_delta(step)
            bearing = int(round(bearing + delta)) % 360
            if bearing not in COMPASS:
                raise ValueError(f"{line.color}: relativer Turn {delta} fuehrt "
                                 f"auf {bearing} Grad -- kein Vielfaches von 45")
            leg = line.corner_step if length is None else length
            ux, uy = COMPASS[bearing]
            pos = (pos[0] + leg * ux, pos[1] + leg * uy)
            emit(pos)

    if line.closed:
        emit(pts[0])
    return pts, st_idx, newpins


def build_turns_svg(turn_lines: Dict[str, TurnLine], label_override=None) -> str:
    """Rendert turn-basierte Linien ueber die Buendelungs-/Rendering-Pipeline.
    Geteilte Stationen bleiben per pin-on-first-definition konsistent."""
    pins: Dict[str, Pt] = {}
    paths: Dict[str, List[Pt]] = {}
    st_maps: Dict[str, Dict[int, str]] = {}
    for lid, line in turn_lines.items():
        pts, st_idx, newpins = walk_turnline(line, pins)
        for name, pt in newpins.items():
            pins.setdefault(name, pt)
        paths[lid], st_maps[lid] = pts, st_idx
    return build_svg(turn_lines, pins, paths, st_maps,
                     labels=label_override or {}, auto_labels=True)


# ===========================================================================
# KORRIDOR-BAUSTEINE (wiederverwendbare Stationsfolgen)
# ===========================================================================

_STADTBAHN = ["Westkreuz", "Charlottenburg", "Savignyplatz",
              "Zoologischer Garten", "Tiergarten", "Bellevue", "Hauptbahnhof",
              "Friedrichstraße", "Hackescher Markt", "Alexanderplatz",
              "Jannowitzbrücke", "Ostbahnhof", "Warschauer Straße", "Ostkreuz"]
_NORD_SUED_TUNNEL = ["Gesundbrunnen", "Humboldthain", "Nordbahnhof",
           "Oranienburger Straße", "Friedrichstraße", "Brandenburger Tor",
           "Potsdamer Platz", "Anhalter Bahnhof", "Yorckstraße"]

_TUNNEL = ["Gesundbrunnen", "Humboldthain", "Nordbahnhof",
           "Oranienburger Straße", "Friedrichstraße", "Brandenburger Tor",
           "Potsdamer Platz", "Anhalter Bahnhof", "Yorckstraße"]
_ANHALTER_SUED = ["Südkreuz", "Priesterweg"]
_LICHTERFELDE = ["Südende", "Lankwitz", "Lichterfelde Ost", "Osdorfer Straße",
                 "Lichterfelde Süd", "Teltow Stadt"]
_STADTBAHN = ["Westkreuz", "Charlottenburg", "Savignyplatz",
              "Zoologischer Garten", "Tiergarten", "Bellevue", "Hauptbahnhof",
              "Friedrichstraße", "Hackescher Markt", "Alexanderplatz",
              "Jannowitzbrücke", "Ostbahnhof", "Warschauer Straße", "Ostkreuz"]
_SPANDAU = ["Spandau", "Stresow", "Pichelsberg", "Olympiastadion",
            "Heerstraße", "Messe Süd"]
_RING_OST = ["Schönhauser Allee", "Prenzlauer Allee", "Greifswalder Straße",
             (62, 20), "Landsberger Allee", (66, 24), "Storkower Straße",
             "Frankfurter Allee", "Ostkreuz", "Treptower Park"]
_GOERLITZER = ["Treptower Park", "Plänterwald", "Baumschulenweg",
               "Schöneweide"]
_BER_AST = ["Adlershof", "Altglienicke", "Grünbergallee", "Schönefeld",
            "Waßmannsdorf", "Flughafen BER"]
# ===========================================================================
# OFFIZIELLE LINIENFARBEN  (exakt aus der Vorlage S-Bahn_Berlin_-_Netzplan.svg)
# ===========================================================================
# Die Vorlage nutzt Farb-FAMILIEN: Aeste derselben Stammlinie teilen sich eine
# Farbe (S2/S25/S26 gruen, S8/S85 hellgruen, S7/S75 violett, S46/S47 ocker).
LINE_COLORS: Dict[str, str] = {
    "S1":  "#DA6BA2",   # rosa
    "S2":  "#007734",   # gruen
    "S3":  "#0066AD",   # blau
    "S5":  "#EC7405",   # orange
    "S7":  "#816DA6",   # violett
    "S8":  "#66AA22",   # hellgruen
    "S9":  "#992746",   # weinrot
    "S41": "#AD5937",   # Ring rotbraun
    "S42": "#CB6418",   # Ring orangebraun
    "S45": "#CD9C53",   # Ring ocker
}

# ===========================================================================
# TURN-LINIENDATEN  (waechst Linie fuer Linie)
# ===========================================================================

# Manuelle Label-Ausnahmen (Vorrang vor auto_label). Werte: (dx, dy, anchor)
# in px, relativ zum Stationspunkt. Fuer Stationen, an denen die Automatik
# nicht passt (z.B. Hubs mit eigener Position, mehrzeilige Labels).
TURN_LABEL_OVERRIDE: Dict[str, Tuple[float, float, str]] = {}

TURN_LINES: Dict[str, TurnLine] = {
    # start=45 (NE). Turns relativ: -45 (links auf Nord), +45 (zurueck NE),
    # +45 (auf Ost fuer die Stadtbahn), -45 (zurueck NE fuer den Ostast).
    "S1": TurnLine(LINE_COLORS["S1"], start=45, anchor=(10, 66), steps=[
        "Wannsee", "Nikolassee", 45, "Schlachtensee", "Mexikoplatz", -45, 
        "Zehlendorf", "Sundgauer Straße", "Lichterfelde West",
        "Botanischer Garten", "Rathaus Steglitz", "Feuerbachstraße",
        "Friedenau", "Schöneberg", "Julius-Leber-Brücke", -45,
        "Yorckstraße (Großgörschenstraße)", *_NORD_SUED_TUNNEL[:-1],
        "Bornholmer Straße", "Wollankstraße", "Schönholz", "Wilhelmsruh",
        "Wittenau", "Waidmannslust", "Hermsdorf", "Frohnau",
        "Hohen Neuendorf", "Birkenwerder", "Borgsdorf", "Lehnitz",
        "Oranienburg"],
        direction="Potsdam Hbf -> Ahrensfelde (Suedwest -> Nordost)"),
    "S7": TurnLine(LINE_COLORS["S7"], start=45, anchor=(4, 72), steps=[
        "Potsdam Hbf", "Babelsberg", "Griebnitzsee", "Wannsee", "Nikolassee",
        -45, 45, "Grunewald",
        45, *_STADTBAHN,
        -45,
        "Nöldnerplatz", "Lichtenberg", "Friedrichsfelde Ost", "Springpfuhl",
        "Poelchaustraße", "Marzahn", "Raoul-Wallenberg-Straße",
        "Mehrower Allee", "Ahrensfelde"],
        direction="Potsdam Hbf -> Ahrensfelde (Suedwest -> Nordost)"),
}


if __name__ == "__main__":
    out = "outputs/netzplan_turns.svg"
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_turns_svg(TURN_LINES, TURN_LABEL_OVERRIDE))
    print(f"Geschrieben: {out}")
