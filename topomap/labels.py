"""
Beschriftung der Linienkarte: Stationen und Streckennamen.

Stationen. Jede beschriftete Station bekommt eine Marke -- ein Kreis fuer
eine Linie, eine Pille quer ueber ein Buendel, an Kreuzungsbahnhoefen eine
Flaeche ueber beide Buendel -- und daneben ihren Namen. Unter dem Namen
stehen die Signets der Linien, die dort enden: voll fuer Endpunkte, weiss
fuer Zwischenenden. Wo der Name steht, sagt je Station ein `Place`; ohne
Angabe sucht die Automatik eine freie Lage.

Streckennamen. Kursiv, entlang der Strecke, aussen neben dem Buendel und
immer von links nach rechts lesbar. Gelegt werden sie an den Abschnitt
einer Linie zwischen zwei Stationen; mehrzeilige Namen stehen waagerecht,
auf Wunsch mit Pfeil auf ihre Strecke. Am Ring stehen innen die Signets
der Ringlinien mit Pfeilen in ihre Fahrtrichtung.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from netmap.model import _ADVANCE, _ADVANCE_DEFAULT, badge_text, text_width

from .lines import _arc, _resample, _smooth, _unit

Pt = Tuple[float, float]
Box = Tuple[float, float, float, float]


@dataclass(frozen=True)
class LabelStyle:
    """Masse der Beschriftung, in Kartenpixeln."""

    font_family: str = "Myriad Pro, Source Sans Pro, Helvetica, Arial, sans-serif"
    name_font: float = 9.5
    name_paren_font: float = 8.0  # Zusatz in Klammern, etwa "(Großgörschenstr.)"
    name_fill: str = "#111111"
    badge_font: float = 9.0       # bestimmt die Breite des Signets
    badge_number_font: float = 9.0  # Schrift darin
    badge_height: float = 12.0
    badge_padding: float = 2.4
    badge_gap: float = 1.8
    badge_outline: float = 1.1    # Rand des weissen Signets (Zwischenende)
    track_font: float = 9.0
    track_weight: float = 0.3     # Kontur in Schriftfarbe: Streckennamen etwas kraeftiger
    track_fill: str = "#333333"
    halo: str = "#ffffff"
    halo_width: float = 2.5
    mark_radius: float = 2.7      # Stationsmarken: Kreis bzw. Pillenenden
    mark_stroke: str = "#222222"
    mark_stroke_width: float = 0.9
    gap: float = 3.0              # Abstand Marke -> Name


# ==========================================================================
# TEXT UND SIGNETS
# ==========================================================================


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _teile(zeile: str) -> List[Tuple[str, bool]]:
    """Zeile -> [(Text, in Klammern?)]."""
    return [(t, t.startswith("(")) for t in re.split(r"(\([^)]*\))", zeile) if t]


def _name_breite(zeile: str, st: LabelStyle) -> float:
    return sum(text_width(t, st.name_paren_font if k else st.name_font)
               for t, k in _teile(zeile))


def _name_svg(zeile: str, st: LabelStyle) -> str:
    """Stationsname; ein Zusatz in Klammern kleiner."""
    return "".join(f'<tspan font-size="{st.name_paren_font:g}">{_esc(t)}</tspan>' if k
                   else _esc(t) for t, k in _teile(zeile))


def _mit_rand(texte: str, st: LabelStyle, attr: str, fill: str, weight: float = 0.0) -> str:
    """Text mit weissem Rand: der Rand als eigene Ebene darunter -- nicht
    jeder Renderer kennt paint-order."""
    kopf = f'<g font-family="{st.font_family}" {attr}'
    return (kopf + f'fill="{st.halo}" stroke="{st.halo}" stroke-width="{st.halo_width:g}" '
            f'stroke-linejoin="round">' + texte + "</g>"
            + kopf + f'fill="{fill}" stroke="{fill}" stroke-width="{weight:g}" '
            f'stroke-linejoin="round">' + texte + "</g>")


def _badge_width(lid: str, st: LabelStyle) -> float:
    return len(badge_text(lid)) * st.badge_font * 0.50 + 2 * st.badge_padding


def _badge_svg(x: float, y: float, lid: str, farbe: str, st: LabelStyle,
               hohl: bool = False, fmt: str = ".1f") -> List[str]:
    """Ein Signet, linke obere Ecke bei (x, y); hohl = weiss mit farbigem
    Rand und farbiger Schrift (Zwischenende)."""
    b, h = _badge_width(lid, st), st.badge_height
    if hohl:
        r = st.badge_outline
        rahmen = (f'<rect x="{x + r / 2:.2f}" y="{y + r / 2:.2f}" width="{b - r:.2f}" '
                  f'height="{h - r:.2f}" rx="{(h - r) / 2:.2f}" fill="#fff" '
                  f'stroke="{farbe}" stroke-width="{r:g}"/>')
        schrift = f'fill="{farbe}"'
    elif fmt == ".1f":
        rahmen = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{b:.1f}" height="{h:.1f}" '
                  f'rx="{h / 2:.1f}" fill="{farbe}"/>')
        schrift = 'fill="#fff"'
    else:
        rahmen = (f'<rect x="{x:.2f}" y="{y:.2f}" width="{b:.2f}" height="{h:g}" '
                  f'rx="{h / 2:g}" fill="{farbe}"/>')
        schrift = 'fill="#fff"'
    ty = y + h / 2 + st.badge_number_font * 0.35
    return [rahmen,
            f'<text x="{x + b / 2:{fmt}}" y="{ty:{fmt}}" '
            f'font-size="{st.badge_number_font:g}" font-weight="600" {schrift} '
            f'text-anchor="middle">{_esc(badge_text(lid))}</text>']


def _badges_svg(x: float, y: float, lids: Sequence[str], colors: Mapping[str, str],
                st: LabelStyle, hollow: Sequence[str] = ()) -> List[str]:
    """Signets nebeneinander; die in `hollow` weiss (Zwischenenden)."""
    out = []
    for lid in lids:
        out += _badge_svg(x, y, lid, colors[lid], st, lid in hollow)
        x += _badge_width(lid, st) + st.badge_gap
    return out


# ==========================================================================
# GEOMETRIE-HELFER
# ==========================================================================


_RICHTUNGEN = [np.array(v, dtype=float) / np.hypot(*v) for v in
               ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))]
_SEITEN = [np.array([1.0, 0.0]), np.array([-1.0, 0.0])]


def _box_bei(p: np.ndarray, u: np.ndarray, w: float, h: float, gap: float) -> Box:
    """Kasten w x h, der in Richtung u an Punkt p anliegt (Abstand gap)."""
    q = p + u * gap
    x0 = q[0] if u[0] > 0.35 else (q[0] - w if u[0] < -0.35 else q[0] - w / 2)
    y0 = q[1] if u[1] > 0.35 else (q[1] - h if u[1] < -0.35 else q[1] - h / 2)
    return x0, y0, x0 + w, y0 + h


def _verschoben(box: Box, d: Pt) -> Box:
    return box[0] + d[0], box[1] + d[1], box[2] + d[0], box[3] + d[1]


def _in_box(pts: np.ndarray, box: Box, pad: float) -> int:
    x0, y0, x1, y1 = box
    return int(np.count_nonzero((pts[:, 0] > x0 - pad) & (pts[:, 0] < x1 + pad)
                                & (pts[:, 1] > y0 - pad) & (pts[:, 1] < y1 + pad)))


def _ueberlappt(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _anker(u: np.ndarray, box: Box, align: Optional[str]) -> Tuple[str, float]:
    """Textausrichtung im Kasten: nach der Richtung u, oder von Hand."""
    x0, _, x1, _ = box
    if align is None:
        align = "start" if u[0] > 0.35 else ("end" if u[0] < -0.35 else "middle")
    return align, {"start": x0, "end": x1, "middle": (x0 + x1) / 2}[align]


def _drehe(v: np.ndarray, w: float) -> np.ndarray:
    c, s_ = np.cos(w), np.sin(w)
    return np.array([c * v[0] - s_ * v[1], s_ * v[0] + c * v[1]])


# ==========================================================================
# STATIONSMARKEN
# ==========================================================================


@dataclass
class Mark:
    """Eine Stationsmarke: Kreis (eine Linie) oder Pille quer ueber
    mehrere nebeneinander liegende Linien, mit runden Enden.

    center  Mitte
    n       Richtung quer zur Strecke (die lange Achse der Pille)
    length  Abstand der beiden Endmittelpunkte (0 = Kreis)
    lines   die Linien, ueber die sie reicht
    """

    center: np.ndarray
    n: np.ndarray
    length: float
    lines: Tuple[str, ...] = ()

    def extent(self, radius: float) -> float:
        return self.length / 2 + radius

    def bbox(self, radius: float) -> Box:
        h = self.n * self.length / 2
        ex, ey = abs(h[0]) + radius, abs(h[1]) + radius
        return (self.center[0] - ex, self.center[1] - ey,
                self.center[0] + ex, self.center[1] + ey)

    def points(self, radius: float) -> np.ndarray:
        """Punkte auf der Flaeche -- als Hindernis fuer Namen."""
        k = max(int(self.length / 1.5), 1)
        achse = self.center + np.outer(np.linspace(-self.length / 2, self.length / 2, k + 1), self.n)
        t = np.array([-self.n[1], self.n[0]])
        return np.vstack([achse + t * r for r in (-radius, 0.0, radius)])


@dataclass
class CrossMark:
    """Marke eines Kreuzungsbahnhofs: die Flaeche, in der sich zwei Buendel
    kreuzen (ein Parallelogramm, bei rechtem Winkel ein Rechteck), mit
    runden Ecken vom Radius der Pillen."""

    center: np.ndarray
    corners: np.ndarray        # (4, 2)
    n: np.ndarray              # Querrichtung des ersten Buendels
    lines: Tuple[str, ...] = ()

    def extent(self, radius: float) -> float:
        return float(np.max(np.hypot(*(self.corners - self.center).T))) + radius

    def bbox(self, radius: float) -> Box:
        c = self.corners
        return (c[:, 0].min() - radius, c[:, 1].min() - radius,
                c[:, 0].max() + radius, c[:, 1].max() + radius)

    def points(self, radius: float) -> np.ndarray:
        rand = np.vstack([np.linspace(a, b, 8) for a, b in zip(self.corners, np.roll(self.corners, -1, 0))])
        return np.vstack([rand, (rand + self.center) / 2, [self.center]])


@dataclass(frozen=True)
class Crossing:
    """Form einer Kreuzungsmarke.

    straighten  0 = so schraeg, wie sich die Buendel kreuzen; 1 = Rechteck.
                Beide Achsen drehen dafuer gleich weit aufeinander zu
    keep        eine Linie: ihr Buendel behaelt seine Richtung -- die Seiten
                der Marke laufen genau parallel zu ihm, und nur die Achse des
                anderen dreht (dafuer doppelt so weit)
    """

    straighten: float = 0.0
    keep: Optional[str] = None


def _kreuz(a: Mark, b: Mark, form: Crossing) -> Optional[CrossMark]:
    """Die Kreuzungsflaeche zweier Pillen: Punkte p mit |(p-X)·n_a| <= L_a/2
    und |(p-X)·n_b| <= L_b/2, X der Schnitt ihrer Streckenachsen."""
    if abs(float(a.n[0] * b.n[1] - a.n[1] * b.n[0])) < 0.2:
        return None                            # fast parallel: keine Kreuzung
    ta, tb = np.array([-a.n[1], a.n[0]]), np.array([-b.n[1], b.n[0]])
    B = np.array([[ta[0], -tb[0]], [ta[1], -tb[1]]])
    x = a.center + ta * np.linalg.solve(B, b.center - a.center)[0]
    na, nb = a.n, b.n
    if form.straighten:
        # Winkel von na nach nb (vorzeichenbehaftet) Richtung +-90 Grad bringen
        w = np.arctan2(na[0] * nb[1] - na[1] * nb[0], float(na @ nb))
        dw = (np.sign(w) * np.pi / 2 - w) * form.straighten / 2
        if form.keep in a.lines:
            nb = _drehe(nb, 2 * dw)
        elif form.keep in b.lines:
            na = _drehe(na, -2 * dw)
        else:
            na, nb = _drehe(na, -dw), _drehe(nb, dw)
    A = np.array([na, nb])
    ecken = [x + np.linalg.solve(A, [sa * a.length / 2, sb * b.length / 2])
             for sa, sb in ((1, 1), (1, -1), (-1, -1), (-1, 1))]
    return CrossMark(x, np.array(ecken), na, a.lines + b.lines)


def station_marks(
    paths: Mapping[str, np.ndarray],
    points: Mapping[str, Pt],
    serving: Mapping[str, Sequence[str]],
    ends: Mapping[str, Sequence[Tuple[str, str]]],
    *,
    spacing: float,
    reach: float = 12.0,
    crossings: Optional[Mapping[str, Crossing]] = None,
) -> Dict[str, List]:
    """Stationsmarken: Station -> Marken.

    serving    Station -> Linien, die dort halten (durchfahrend oder endend)
    ends       Station -> [(Linie, "start" | "end")] fuer die, die dort enden
    crossings  Station -> `Crossing`: dort wird aus den beiden Pillen eine
               Marke ueber die ganze Kreuzungsflaeche (4 x 4 Spuren an
               Ostkreuz)

    Je Linie ein Punkt: ihr Ende, wenn sie dort endet, sonst der Punkt
    ihres Zugs, der der Station am naechsten liegt (hoechstens `reach`
    entfernt). Punkte, die nebeneinander in einem Buendel liegen (Abstand
    bis 1,6 Spuren, hoechstens 15 Grad auseinander), werden eine Pille; ein
    Kreuzungsbahnhof bekommt so je Buendel eine eigene.
    """
    crossings = crossings or {}
    out: Dict[str, List] = {}
    for sid, lids in serving.items():
        endet = dict(ends.get(sid, ()))
        pkt, tan, lang, bei = [], [], [], []
        for lid in lids:
            p = paths[lid]
            if lid in endet:
                zug = p if endet[lid] == "end" else p[::-1]
                a, b = zug[-1], zug[max(-7, -len(zug))]
                pkt.append(a)
                tan.append(_unit(a - b))
                lang.append(tan[-1])
            else:
                dist = np.hypot(*(p - np.asarray(points[sid])).T)
                i = int(np.argmin(dist))
                if dist[i] > reach:
                    continue
                pkt.append(p[i])
                # Richtung ueber +-12 px: die Strecke, nicht ein Knick der
                # Ausrundung; fuer eine einzelne Linie ueber +-40 px -- ihre
                # Marke richtet eine Kreuzungsmarke aus
                tan.append(_unit(p[min(i + 12, len(p) - 1)] - p[max(i - 12, 0)]))
                lang.append(_unit(p[min(i + 40, len(p) - 1)] - p[max(i - 40, 0)]))
            bei.append(lid)
        if not pkt:
            continue
        # Buendel: zusammenhaengend ueber kurze Abstaende und gleiche Richtung
        n_ = len(pkt)
        gruppe = list(range(n_))

        def wurzel(a):
            while gruppe[a] != a:
                a = gruppe[a]
            return a
        for a in range(n_):
            for b in range(a + 1, n_):
                if (np.hypot(*(pkt[a] - pkt[b])) < 1.6 * spacing
                        and abs(float(tan[a] @ tan[b])) > np.cos(np.radians(15))):
                    gruppe[wurzel(a)] = wurzel(b)
        marken = []
        for g in {wurzel(a) for a in range(n_)}:
            idx = [a for a in range(n_) if wurzel(a) == g]
            if len(idx) == 1:
                t = lang[idx[0]]
            else:
                t = tan[idx[0]]
                t = _unit(np.sum([tan[a] if float(tan[a] @ t) >= 0 else -tan[a] for a in idx], axis=0))
            n = np.array([-t[1], t[0]])
            c = np.mean([pkt[a] for a in idx], axis=0)
            quer = [float((pkt[a] - c) @ n) for a in idx]
            lo, hi = min(quer), max(quer)
            marken.append(Mark(c + n * (lo + hi) / 2, n, hi - lo, tuple(bei[a] for a in idx)))
        if sid in crossings and len(marken) == 2:
            k = _kreuz(*marken, crossings[sid])
            if k is not None:
                marken = [k]
        out[sid] = marken
    return out


def marks_svg(marks: Mapping[str, Sequence], style: LabelStyle = LabelStyle()) -> str:
    """Marken zeichnen: Kreise und Pillen weiss mit dunklem Rand, darueber
    die Kreuzungsmarken (erst der Rand, dann die weisse Flaeche)."""
    radius, stroke, sw = style.mark_radius, style.mark_stroke, style.mark_stroke_width
    teile = [f'<g id="stationen" fill="#ffffff" stroke="{stroke}" stroke-width="{sw:g}">']
    kreuze = []
    for liste in marks.values():
        for m in liste:
            if isinstance(m, CrossMark):
                kreuze.append(m)
            elif m.length < 0.3:
                teile.append(f'<circle cx="{m.center[0]:.1f}" cy="{m.center[1]:.1f}" r="{radius:g}"/>')
            else:
                w = np.degrees(np.arctan2(m.n[1], m.n[0]))
                teile.append(
                    f'<rect x="{-m.length / 2 - radius:.2f}" y="{-radius:.2f}" '
                    f'width="{m.length + 2 * radius:.2f}" height="{2 * radius:.2f}" rx="{radius:g}" '
                    f'transform="translate({m.center[0]:.1f} {m.center[1]:.1f}) rotate({w:.1f})"/>')
    teile.append("</g>")
    for m in kreuze:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in m.corners)
        for farbe, breite in ((stroke, 2 * radius + sw), ("#ffffff", 2 * radius - sw)):
            teile.append(f'<polygon points="{pts}" fill="{farbe}" stroke="{farbe}" '
                         f'stroke-width="{breite:.2f}" stroke-linejoin="round"/>')
    return "".join(teile)


# ==========================================================================
# STATIONSNAMEN
# ==========================================================================


@dataclass(frozen=True)
class Place:
    """Wo der Name einer Station steht -- ein Block aus Name und, darunter,
    ihren Signets. Angelegt ueber die Funktionen darunter:

    auto()       die Automatik sucht (siehe `station_labels`)
    beside()     links oder rechts neben der Marke, vertikal zentriert; die
                 Seite und einen Abstand zu den Linien sucht die Automatik
    toward(d)    in Richtung d von der Marke aus anliegend
    corner(e)    eine Standardecke (Vorbild Schoeneweide)
    fixed(x, y)  linke obere Ecke fest in Kartenpixeln

    Allen gemeinsam:
    shift        den Block danach noch um (dx, dy) Pixel verschieben
    align        Name und Signets im Block "start" (links), "middle" oder
                 "end" (rechts) ausrichten; ohne Angabe nach der Seite
    badge_order  Reihenfolge der Signets, sonst erst die vollen, dann die
                 weissen
    """

    mode: str = "auto"
    direction: Pt = (1.0, 0.0)
    corner: Optional[str] = None
    shift: Pt = (0.0, 0.0)
    align: Optional[str] = None
    badge_order: Tuple[str, ...] = ()


def auto(shift: Pt = (0.0, 0.0), **kw) -> Place:
    return Place("auto", shift=shift, **kw)


def beside(shift: Pt = (0.0, 0.0), **kw) -> Place:
    return Place("beside", shift=shift, **kw)


def toward(direction: Pt, shift: Pt = (0.0, 0.0), **kw) -> Place:
    return Place("toward", direction=direction, shift=shift, **kw)


def corner(ecke: str, shift: Pt = (0.0, 0.0), **kw) -> Place:
    """Standardlage an einer Ecke der Marke, nach dem Vorbild Schoeneweide:
    "top_right", "bottom_right" oder "bottom_left". Der Name beruehrt mit
    seiner Ecke die Ecke des Rechtecks um die Marke; Signets stehen darunter
    und aendern seine Lage nicht."""
    richtung = {"top_right": (1, -1), "bottom_right": (1, 1), "bottom_left": (-1, 1)}[ecke]
    return Place("corner", direction=richtung, corner=ecke, shift=shift, **kw)


def fixed(x: float, y: float, **kw) -> Place:
    return Place("fixed", direction=(1, -1), shift=(x, y), **kw)


def station_labels(
    paths: Mapping[str, np.ndarray],
    marks: Mapping[str, Sequence],
    names: Mapping[str, str],
    colors: Mapping[str, str],
    *,
    badges: Mapping[str, Sequence[str]],
    ends: Mapping[str, Sequence[Tuple[str, str]]],
    hollow: Optional[Mapping[str, Sequence[str]]] = None,
    place: Optional[Mapping[str, Place]] = None,
    style: LabelStyle = LabelStyle(),
) -> str:
    """Stationsnamen neben ihren Marken.

    names   Station -> Name (Zeilen mit "\\n" getrennt); nur diese werden
            beschriftet
    badges  Station -> Linien, die dort enden: volles Signet
    hollow  Station -> Linien, von denen dort nur einzelne Zuggruppen enden
            (Zwischenenden): weisses Signet, hinter den vollen
    ends    Station -> [(Linie, "start" | "end")]: Richtung des Namens in
            Verlaengerung der Strecke
    place   Station -> `Place`; ohne Eintrag `auto()`

    Die Automatik: Endpunkte an Aussenaesten (dort enden alle Linien, die
    halten) stehen links oder rechts neben dem Linienende, solange dort
    nichts im Weg ist. Sonst acht Richtungen um die Marke, bevorzugt in
    Verlaengerung der Strecke (Endpunkte) bzw. quer zu ihr (Durchgangs-
    stationen), und dort, wo der Name am wenigsten Linien, Marken und andere
    Namen ueberdeckt. Stationen mit Signets kommen zuerst an die Reihe.
    """
    st = style
    radius = st.mark_radius
    place = place or {}
    hollow = hollow or {}
    hindernisse = [_resample(p, 1.0) for p in paths.values()]
    hindernisse += [m.points(radius) for ms in marks.values() for m in ms]
    alle = np.vstack(hindernisse)
    belegt: List[Box] = []
    texte, signets = [], []

    reihe = sorted(names, key=lambda sid: (not (badges.get(sid) or hollow.get(sid)), sid))
    for sid in reihe:
        ms = marks.get(sid, [])
        if not ms:
            continue
        pl = place.get(sid, Place())
        voll = sorted(badges.get(sid, ()), key=lambda l: (len(l), l))
        hohl = sorted((l for l in hollow.get(sid, ()) if l not in voll), key=lambda l: (len(l), l))
        lids = voll + hohl
        if pl.badge_order:
            lids = sorted(lids, key=lambda l: pl.badge_order.index(l)
                          if l in pl.badge_order else len(pl.badge_order))
        raus = [_unit(z[-1] - z[max(-7, -len(z))])
                for z in ((paths[l] if s == "end" else paths[l][::-1]) for l, s in ends.get(sid, ()))]
        p0 = np.mean([m.center for m in ms], axis=0)
        d = _unit(np.sum(raus, axis=0)) if raus else ms[0].n

        zeilen = names[sid].split("\n")
        font = st.name_font
        badge_b = (sum(_badge_width(l, st) for l in lids) + st.badge_gap * (len(lids) - 1)) if lids else 0.0
        w = max(max(_name_breite(z, st) for z in zeilen), badge_b)
        h = len(zeilen) * font * 1.1 + ((1.5 + st.badge_height) if lids else 0.0)
        rad = max(m.extent(radius) for m in ms) + st.gap

        def stoert(box: Box, pad: float = 1.5) -> int:
            return (10 * _in_box(alle, box, pad)
                    + 2000 * sum(_ueberlappt(box, b) for b in belegt))

        if pl.mode == "corner":
            u = _unit(np.asarray(pl.direction, dtype=float))
            bx = [m.bbox(radius) for m in ms]
            mx0, my0 = min(b[0] for b in bx), min(b[1] for b in bx)
            mx1, my1 = max(b[2] for b in bx), max(b[3] for b in bx)
            n = len(zeilen)
            oben_text = font * 0.08                        # Oberkante der Schrift ab y0
            unten_text = n * font + (n - 1) * font * 0.1   # Grundlinie + Unterlaenge
            x0 = mx0 - w if pl.corner == "bottom_left" else mx1
            y0 = my1 + 1.0 - oben_text if pl.corner.startswith("bottom") else my0 - 1.0 - unten_text
            if pl.corner.startswith("bottom"):
                # etwas hoeher -- die Ecke des Rechtecks um eine schraege
                # Pille liegt weiter weg als die Pille selbst -- und seitlich
                # etwas weiter hinaus
                x0 += -2.0 if pl.corner == "bottom_left" else 3.0
                y0 -= 2.5
            box = _verschoben((x0, y0, x0 + w, y0 + h), pl.shift)
        elif pl.mode == "fixed":
            u = _unit(np.asarray(pl.direction, dtype=float))
            ax, ay = pl.shift
            box = (ax, ay, ax + w, ay + h)
        elif pl.mode == "toward":
            u = _unit(np.asarray(pl.direction, dtype=float))
            box = _verschoben(_box_bei(p0, u, w, h, rad), pl.shift)
        elif pl.mode == "beside":
            # links oder rechts, vertikal zentriert; liegt der Block auf einer
            # Linie, rueckt er seitlich weiter hinaus oder etwas hoch/runter,
            # bis er ringsum 2 px frei hat
            beste = None
            for u_ in _SEITEN:
                b0 = _box_bei(p0, u_, w, h, rad)
                for dx in np.arange(0.0, 25.0, 1.0):
                    for dy in (0.0, -1, 1, -2, 2, -3, 3, -4, 4, -6, 6, -8, 8):
                        kandidat = (b0[0] + u_[0] * dx, b0[1] + dy, b0[2] + u_[0] * dx, b0[3] + dy)
                        kosten = stoert(kandidat, 2.0) + dx + 1.5 * abs(dy)
                        if beste is None or kosten < beste[0]:
                            beste = (kosten, kandidat, u_)
            _, box, u = beste
            if pl.shift != (0.0, 0.0):
                box = _verschoben(box, pl.shift)
        else:
            durch = not raus

            def bewerte(richtungen):
                beste = None
                for u_ in richtungen:
                    kandidat = _box_bei(p0, u_, w, h, rad)
                    vorzug = abs(float(u_ @ d)) if durch else float(u_ @ d)
                    s_ = stoert(kandidat)
                    kosten = s_ + 30 * (1 - vorzug)
                    if beste is None or kosten < beste[0]:
                        beste = (kosten, kandidat, u_, s_)
                return beste
            beste = None
            laufen = {l for m in ms for l in m.lines}
            if raus and laufen <= {l for l, _ in ends.get(sid, ())}:
                beste = bewerte(_SEITEN)
                if beste[3] > 0:
                    beste = None
            if beste is None:
                beste = bewerte(_RICHTUNGEN)
            _, box, u, _ = beste
            if pl.shift != (0.0, 0.0):
                box = _verschoben(box, pl.shift)
        belegt.append(box)

        # Name (Zeilen) und darunter die Signets, buendig nach `_anker`
        anker, xt = _anker(u, box, pl.align)
        y = box[1]
        for z in zeilen:
            y += font
            texte.append(f'<text x="{xt:.1f}" y="{y - font * 0.2:.1f}" '
                         f'text-anchor="{anker}">{_name_svg(z, st)}</text>')
            y += font * 0.1
        if lids:
            y += 1.5
            bx0 = xt if anker == "start" else (xt - badge_b if anker == "end" else xt - badge_b / 2)
            signets.extend(_badges_svg(bx0, y, lids, colors, st, hohl))

    return (
        '<g id="stationsnamen">'
        + _mit_rand("".join(texte), st, f'font-size="{st.name_font:g}" font-weight="600" ',
                    st.name_fill)
        + f'<g font-family="{st.font_family}">' + "".join(signets) + "</g></g>"
    )


# ==========================================================================
# STRECKENNAMEN
# ==========================================================================


@dataclass(frozen=True)
class TrackName:
    """Ein Streckenname an der Strecke zwischen zwei Stationen.

    text     der Name; mehrere Zeilen mit "\\n"
    a, b     Stationen; der Name steht in der Mitte dazwischen
    line     die Linie, an deren Zug er liegt (ohne Angabe: die erste, die
             a und b nacheinander befaehrt)
    side     "links" / "rechts" in Richtung a -> b; ohne Angabe die Seite,
             auf der er weniger ueberdeckt
    shift    entlang der Strecke verschieben (Pixel, Richtung a -> b)
    start    statt mittig: der Text beginnt so weit (Pixel) hinter a
    horizontal
             waagerecht statt entlang der Strecke, als Block neben ihr --
             fuer mehrzeilige Namen
    arrow    (dx, dy): der waagerechte Block steht so weit vom Streckenpunkt
             entfernt (linke obere Ecke), ein gebogener Pfeil zeigt vom Text
             auf die Strecke
    ring     Ringlinien, etwa ("S41", "S42"): der Name steht aussen am Ring,
             innen stehen ihre Signets, jedes mit einem Pfeil in seine
             Fahrtrichtung
    against  Ringlinien, die gegen ihre Schreibrichtung fahren
    ring_shift
             die Signets in Leserichtung verschieben (Pixel)
    """

    text: str
    a: str
    b: str
    line: Optional[str] = None
    side: Optional[str] = None
    shift: float = 0.0
    start: Optional[float] = None
    horizontal: bool = False
    arrow: Optional[Pt] = None
    ring: Tuple[str, ...] = ()
    against: Tuple[str, ...] = ()
    ring_shift: float = 0.0


def _text_entlang(bahn: np.ndarray, text: str, font: float) -> List[str]:
    """Text mittig entlang eines Linienzugs, Buchstabe fuer Buchstabe --
    jeder mit eigener Lage und Drehung. So kann es jeder Renderer, auch
    einer ohne textPath."""
    s = _arc(bahn)
    breiten = [font * _ADVANCE.get(c, _ADVANCE_DEFAULT) for c in text]
    pos = s[-1] / 2 - sum(breiten) / 2
    out = []
    for c, b in zip(text, breiten):
        m = pos + b / 2
        pos += b
        if c == " ":
            continue
        x, y = np.interp(m, s, bahn[:, 0]), np.interp(m, s, bahn[:, 1])
        d = (np.array([np.interp(m + 1, s, bahn[:, 0]), np.interp(m + 1, s, bahn[:, 1])])
             - np.array([np.interp(m - 1, s, bahn[:, 0]), np.interp(m - 1, s, bahn[:, 1])]))
        w = np.degrees(np.arctan2(d[1], d[0]))
        out.append(f'<text x="{x:.1f}" y="{y:.1f}" transform="rotate({w:.1f} {x:.1f} {y:.1f})" '
                   f'text-anchor="middle">{_esc(c)}</text>')
    return out


def _text_block(c: np.ndarray, u: np.ndarray, zeilen: Sequence[str], st: LabelStyle,
                abstand: float, alle: np.ndarray) -> List[str]:
    """Waagerechter Textblock neben der Strecke: in Richtung u (quer zur
    Strecke) an c anliegend, so weit hinaus, bis er keine Linie beruehrt."""
    font = st.track_font
    w = max(text_width(z, font) for z in zeilen)
    h = len(zeilen) * font * 1.1
    for extra in np.arange(0.0, 30.0, 1.0):
        box = _box_bei(c, u, w, h, abstand + extra)
        if _in_box(alle, box, 1.0) == 0:
            break
    anker, xt = _anker(u, box, None)
    return [f'<text x="{xt:.1f}" y="{box[1] + (zi + 1) * font * 1.1 - font * 0.3:.1f}" '
            f'text-anchor="{anker}">{_esc(z)}</text>' for zi, z in enumerate(zeilen)]


def _text_block_pfeil(c: np.ndarray, zeilen: Sequence[str], st: LabelStyle,
                      versatz: Pt, line_width: float) -> List[str]:
    """Waagerechter Block, zentriert, linke obere Ecke bei c + versatz, dazu
    ein gebogener Pfeil vom Anfang der ersten Zeile auf den Streckenpunkt c
    -- waagerecht hinaus, dann auf die Strecke; er endet knapp davor."""
    font = st.track_font
    x0, y0 = c[0] + versatz[0], c[1] + versatz[1]
    xm = x0 + max(text_width(z, font) for z in zeilen) / 2
    out = []
    for zi, z in enumerate(zeilen):
        # die erste Zeile beginnt dort, wo der Pfeil ansetzt; die weiteren
        # stehen mittig darunter
        if zi == 0:
            xz, anker = xm - text_width(z, font) / 2, "start"
        else:
            xz, anker = xm, "middle"
        out.append(f'<text x="{xz:.1f}" y="{y0 + (zi + 1) * font * 1.1 - font * 0.3:.1f}" '
                   f'text-anchor="{anker}">{_esc(z)}</text>')
    a = np.array([x0 - 2.0, y0 + font * 0.55])
    ziel = np.asarray(c, dtype=float)
    k = np.array([ziel[0], a[1]])                     # Kontrollpunkt
    t_end = _unit(ziel - k) if np.hypot(*(ziel - k)) > 1e-6 else _unit(ziel - a)
    e = ziel - t_end * line_width * 2.5
    n = np.array([-t_end[1], t_end[0]])
    p1, p2 = e - t_end * 3.0 + n * 1.6, e - t_end * 3.0 - n * 1.6
    out.append(f'<path d="M{a[0]:.1f} {a[1]:.1f}Q{k[0]:.1f} {k[1]:.1f} {e[0]:.1f} {e[1]:.1f}" '
               f'fill="none" stroke-width="0.8"/>')
    out.append(f'<path d="M{p1[0]:.1f} {p1[1]:.1f}L{e[0]:.1f} {e[1]:.1f}L{p2[0]:.1f} {p2[1]:.1f}" '
               f'fill="none" stroke-width="0.8" stroke-linejoin="miter"/>')
    return out


def _ring_signets(c: np.ndarray, innen: np.ndarray, abstand: float, tn: TrackName,
                  zuege: Mapping[str, np.ndarray], colors: Mapping[str, str],
                  st: LabelStyle) -> str:
    """Signets der Ringlinien innen neben der Strecke, nebeneinander und
    lesbar ausgerichtet; zwischen ihnen und der Strecke je ein Pfeil in die
    Fahrtrichtung der Linie. Wer in Leserichtung faehrt, steht rechts."""
    t = np.array([-innen[1], innen[0]])
    if t[0] < 0:
        t = -t                                          # Leserichtung
    winkel = np.degrees(np.arctan2(t[1], t[0]))
    # lokale y-Achse nach der Drehung: rechts von t; innen = +y oder -y
    sy = 1.0 if float(np.array([-t[1], t[0]]) @ innen) > 0 else -1.0
    h, gap = st.badge_height, 2.0
    pfeil_y = abstand + 3.5                             # Pfeilmitte
    badge_y = pfeil_y + 4.0                             # Signet-Kante zur Strecke
    teile = []
    for lid in tn.ring:
        q = zuege[lid]
        i = int(np.argmin(np.hypot(*(q - c).T)))
        d = _unit(q[min(i + 5, len(q) - 1)] - q[max(i - 5, 0)])
        teile.append(((float(d @ t) > 0) != (lid in tn.against), lid))
    teile.sort()                                        # rueckwaerts links
    breiten = [_badge_width(l, st) for _, l in teile]
    x = -(sum(breiten) + gap * (len(breiten) - 1)) / 2 + tn.ring_shift
    out = []
    for (vor, lid), b in zip(teile, breiten):
        yk = sy * badge_y if sy > 0 else -badge_y - h   # Oberkante des Signets
        out += _badge_svg(x, yk, lid, colors.get(lid, "#888"), st, fmt=".2f")
        xm, ya, l2 = x + b / 2, sy * pfeil_y, 4.5
        r = 1.0 if vor else -1.0
        out.append(f'<path d="M{xm - r * l2:.2f} {ya:.2f}L{xm + r * l2:.2f} {ya:.2f}'
                   f'M{xm + r * (l2 - 2.2):.2f} {ya - 1.8:.2f}L{xm + r * l2:.2f} {ya:.2f}'
                   f'L{xm + r * (l2 - 2.2):.2f} {ya + 1.8:.2f}" fill="none" '
                   f'stroke="{st.track_fill}" stroke-width="1.4" stroke-linecap="round" '
                   f'stroke-linejoin="round"/>')
        x += b + gap
    return (f'<g transform="translate({c[0]:.1f} {c[1]:.1f}) rotate({winkel:.1f})">'
            + "".join(out) + "</g>")


def track_labels(
    paths: Mapping[str, np.ndarray],
    stations: Mapping[str, Pt],
    sequences: Mapping[str, Sequence[str]],
    names: Sequence[TrackName],
    *,
    colors: Mapping[str, str],
    style: LabelStyle = LabelStyle(),
    line_width: float = 3.6,
) -> str:
    """Streckennamen entlang der Linien."""
    st = style
    zuege = {lid: _resample(p, 1.0) for lid, p in paths.items()}
    alle = np.vstack(list(zuege.values()))
    texte: List[str] = []
    extra: List[str] = []
    for tn in names:
        lid = tn.line or next(
            (l for l, f in sequences.items()
             if tn.a in f and tn.b in f and abs(f.index(tn.a) - f.index(tn.b)) == 1),
            None)
        if lid is None:
            raise ValueError(f"Streckenname {tn.text!r}: keine Linie faehrt {tn.a} -> {tn.b}")
        q = zuege[lid]
        sq = _arc(q)
        ia = int(np.argmin(np.hypot(*(q - np.asarray(stations[tn.a])).T)))
        ib = int(np.argmin(np.hypot(*(q - np.asarray(stations[tn.b])).T)))
        vorwaerts = ib >= ia
        zeilen = tn.text.split("\n")
        laenge = max(text_width(z, st.track_font) for z in zeilen) * 1.05
        # Stueck um die Mitte zwischen a und b, etwas laenger als der Text --
        # aus dem ganzen Zug, auch wenn a und b naeher beieinander liegen
        mitte = (sq[ia] + sq[ib]) / 2 + (tn.shift if vorwaerts else -tn.shift)
        if tn.start is not None:
            weg = tn.start + laenge / 1.05 / 2 + tn.shift
            mitte = sq[ia] + (weg if vorwaerts else -weg)
        rand = 0.8 * laenge
        weit = (sq >= mitte - rand - 20) & (sq <= mitte + rand + 20)
        glatt = _smooth(q[weit], 15)
        sg_ = sq[weit]
        stueck = glatt[(sg_ >= mitte - rand) & (sg_ <= mitte + rand)]
        if not vorwaerts:
            stueck = stueck[::-1]                              # a -> b
        if len(stueck) < 3:
            continue
        e = np.gradient(stueck, axis=0)
        e /= np.maximum(np.hypot(e[:, 0], e[:, 1]), 1e-9)[:, None]
        links = np.column_stack([e[:, 1], -e[:, 0]])     # links in Richtung a -> b
        # wie breit das Buendel an der Mitte ist, je Seite
        c = stueck[len(stueck) // 2]
        nc, ec = links[len(stueck) // 2], e[len(stueck) // 2]
        rel = alle - c
        nah = (np.abs(rel @ ec) < 3) & (np.hypot(*rel.T) < 25)
        quer = rel[nah] @ nc
        ausdehnung = {1: max(quer.max(initial=0.0), 0.0), -1: max(-quer.min(initial=0.0), 0.0)}
        abstand = {sg: ausdehnung[sg] + line_width / 2 + 2.0 for sg in (1, -1)}
        if tn.ring:
            # aussen: weiter weg von der Mitte des Rings; innen die Signets
            mitte_ring = zuege[tn.ring[0]].mean(axis=0)
            seite = max((1, -1), key=lambda sg: float(np.hypot(*(c + nc * sg - mitte_ring))))
            extra.append(_ring_signets(c, nc * -seite, abstand[-seite], tn, zuege, colors, st))
        elif tn.side:
            seite = 1 if tn.side == "links" else -1
        else:
            kosten = {}
            for sg in (1, -1):
                band = c + nc * sg * (abstand[sg] + st.track_font * 0.5)
                kosten[sg] = int(np.count_nonzero(np.hypot(*(alle - band).T) < st.track_font * 0.8))
            seite = min((1, -1), key=lambda sg: kosten[sg])
        if tn.horizontal and tn.arrow is not None:
            texte.extend(_text_block_pfeil(c, zeilen, st, tn.arrow, line_width))
            continue
        if tn.horizontal:
            texte.extend(_text_block(c, nc * seite, zeilen, st, abstand[seite], alle))
            continue
        # lesbar von links nach rechts: sonst andersherum schreiben
        umdrehen = stueck[-1, 0] < stueck[0, 0]
        zeilen_h = st.track_font * 1.1
        for zi, z in enumerate(zeilen):
            # Zeilen von der Strecke weg; Grundlinie so, dass der Text aussen liegt
            text_links = (seite == 1) != umdrehen      # Text waechst nach links
            reihe = zi if text_links else len(zeilen) - 1 - zi
            if text_links:
                versatz = abstand[seite] + reihe * zeilen_h
            else:
                versatz = abstand[seite] + st.track_font * 0.72 + reihe * zeilen_h
            bahn = stueck + links * (seite * versatz)
            if umdrehen:
                bahn = bahn[::-1]
            texte.extend(_text_entlang(bahn, z, st.track_font))
    return (
        '<g id="streckennamen">'
        + _mit_rand("".join(texte), st, f'font-size="{st.track_font:g}" font-style="italic" ',
                    st.track_fill, st.track_weight)
        + f'<g font-family="{st.font_family}">' + "".join(extra) + "</g>"
        + "</g>"
    )
