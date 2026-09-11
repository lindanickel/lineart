"""
Modell und Konfiguration.

Drei Schichten, von unten nach oben: geometrische Helfer (reine Vektor- und
Pfadrechnung), die nach Phasen getrennte Konfiguration, und die
Definitionssprache samt internem Modell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, hypot, pi, radians, sin, sqrt, tan
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

Pt = Tuple[float, float]


# ==========================================================================
# GEOMETRY
# ==========================================================================


def _pair_key(a: str, b: str) -> frozenset[str]:
    return frozenset((a, b))


def _norm(v: Pt) -> Pt:
    l = hypot(v[0], v[1])
    if l < 1e-12:
        return (0.0, 0.0)
    return (v[0] / l, v[1] / l)


def _corner_tangent_factor(delta: int) -> float:
    """tan(delta/2) -- der Faktor zwischen Kreisradius und Tangentenlaenge:
    t = R * tan(delta/2), R = t / tan(delta/2)."""
    return tan(radians(abs(delta)) / 2)


def _segment_hits_box(
    a: Pt, b: Pt, box: Tuple[float, float, float, float], rand: float
) -> bool:
    """Schneidet die Strecke a-b das (um `rand` aufgeweitete) Rechteck?
    Liang-Barsky-Clipping."""
    x0, y0, x1, y1 = box
    x0 -= rand
    y0 -= rand
    x1 += rand
    y1 += rand
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(pk) < 1e-12:
            if qk < 0:
                return False
        else:
            r = qk / pk
            if pk < 0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                if r < t0:
                    return False
                t1 = min(t1, r)
    return t0 <= t1


# Vorschubbreiten der Beschriftungsschrift (Helvetica Neue Bold), als
# Vielfaches der Schriftgroesse. Gemessen, nicht geschaetzt: je Zeichen wurde
# eine Kette aus vier und aus acht Exemplaren gerendert und die Differenz
# durch vier geteilt -- so fallen die Seitenraender heraus und uebrig bleibt
# der reine Vorschub.
#
# Eine mittlere Zeichenbreite reicht seit der adaptiven Zeichenflaeche nicht
# mehr: deren Rand wird aus diesen Breiten bestimmt. Ein zu grosser Wert
# traegt sichtbar zu viel Rand auf einer Seite auf, ein zu kleiner schneidet
# ab.
_ADVANCE: Dict[str, float] = {
    " ": 0.2786, "(": 0.2950, ")": 0.2975, "-": 0.4075, "/": 0.3700,
    "A": 0.6850, "B": 0.7050, "C": 0.7400, "D": 0.7400, "E": 0.6475,
    "F": 0.5925, "G": 0.7600, "H": 0.7400, "I": 0.2950, "J": 0.5550,
    "K": 0.7225, "L": 0.5925, "M": 0.9075, "N": 0.7400, "O": 0.7775,
    "P": 0.6675, "R": 0.7225, "S": 0.6500, "T": 0.6125, "V": 0.6300,
    "W": 0.9450, "Y": 0.6650, "Z": 0.6475,
    "a": 0.5725, "b": 0.6100, "c": 0.5750, "d": 0.6125, "e": 0.5750,
    "f": 0.3150, "g": 0.6100, "h": 0.5925, "i": 0.2575, "k": 0.5750,
    "l": 0.2575, "m": 0.9050, "n": 0.5925, "o": 0.6100, "p": 0.6100,
    "r": 0.3875, "s": 0.5375, "t": 0.3500, "u": 0.5925, "v": 0.5200,
    "w": 0.8150, "x": 0.5375, "y": 0.5200, "z": 0.5175,
    "ß": 0.6100, "ä": 0.5725, "ö": 0.6100, "ü": 0.5925,
}
# Fuer Zeichen ausserhalb der Tabelle -- lieber etwas zu breit als zu schmal.
_ADVANCE_DEFAULT = 0.62


def text_width(zeile: str, font: float) -> float:
    """Breite einer einzelnen Textzeile in Pixeln."""
    return font * sum(_ADVANCE.get(c, _ADVANCE_DEFAULT) for c in zeile)


def badge_text(line_id: str) -> str:
    """"S85" -> "S 85", wie auf den Liniensignets."""
    return line_id[0] + " " + line_id[1:] if len(line_id) > 1 else line_id


def badge_width(line_id: str, style: "StyleConfig") -> float:
    """Breite des Liniensignets. Steht hier und nicht im Renderer, weil die
    Legende dieselben Signets zeichnet und sie exakt gleich aussehen sollen."""
    return (len(badge_text(line_id)) * style.badge_font * 0.50
            + 2 * style.badge_padding)


def polyline_path_d(points: Sequence[Pt]) -> str:
    return "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in points)


def rounded_path_d(
    points: Sequence[Pt],
    radii: Sequence[Optional[float]],
    beziers: Sequence[int] = (),
) -> str:
    """SVG-Pfad durch `points`, an inneren Punkten mit radii[i] > 0 durch
    einen KREISBOGEN abgerundet (radii[0]/radii[-1] werden ignoriert -- das
    sind Stationen, keine Knicke).

    `radii[i]` ist der KREISRADIUS des Bogens. Die Tangentenlaenge -- der
    Abstand vom Knick, an dem die Rundung beginnt -- folgt daraus als
    t = R * tan(delta/2).

    Kreisboegen und keine Bezierkurven, weil nur bei ihnen die Parallelkurve
    wieder exakt ein Kreisbogen ist (konzentrisch, R -/+ Versatz) -- eine
    versetzte Bezier haette ueber den Bogen einen schwankenden Abstand.

    Ausnahme: `beziers` listet Segmente (Index i meint points[i] ->
    points[i+1]), die als kubische Bezier gezeichnet werden. Das ist nur
    fuer den Spurwechsel auf gerader Strecke gedacht -- dort laeuft die
    Linie allein, also gibt es keine Parallele, die exakt mitlaufen
    muesste."""
    if len(points) < 3:
        return polyline_path_d(points)

    bez = {i for i in beziers if 0 <= i < len(points) - 1}
    parts = [f"M {points[0][0]:.3f} {points[0][1]:.3f}"]
    gezeichnet = 0
    for i in range(1, len(points)):
        if i <= gezeichnet:
            continue
        if (i - 1) in bez:
            c1, c2 = _bezier_controls(points[i - 2] if i >= 2 else points[i - 1],
                                      points[i - 1], points[i])
            parts.append(
                f"C {c1[0]:.3f} {c1[1]:.3f} {c2[0]:.3f} {c2[1]:.3f} "
                f"{points[i][0]:.3f} {points[i][1]:.3f}"
            )
            gezeichnet = i
            continue
        if i == len(points) - 1:
            break
        gezeichnet = i
        radius = radii[i]
        prev_pt, cur_pt, next_pt = points[i - 1], points[i], points[i + 1]
        d_in = hypot(cur_pt[0] - prev_pt[0], cur_pt[1] - prev_pt[1])
        d_out = hypot(next_pt[0] - cur_pt[0], next_pt[1] - cur_pt[1])
        if not radius or d_in < 1e-9 or d_out < 1e-9:
            parts.append(f"L {cur_pt[0]:.3f} {cur_pt[1]:.3f}")
            continue

        ux_in, uy_in = (cur_pt[0] - prev_pt[0]) / d_in, (cur_pt[1] - prev_pt[1]) / d_in
        ux_out, uy_out = (next_pt[0] - cur_pt[0]) / d_out, (next_pt[1] - cur_pt[1]) / d_out

        # Richtungsaenderung am Knick, vorzeichenbehaftet
        cross = ux_in * uy_out - uy_in * ux_out
        dot = ux_in * ux_out + uy_in * uy_out
        delta = atan2(cross, dot)
        if abs(delta) < 1e-9 or abs(abs(delta) - pi) < 1e-9:
            # geradeaus oder Kehre -- nichts zu runden
            parts.append(f"L {cur_pt[0]:.3f} {cur_pt[1]:.3f}")
            continue

        # Tangentenlaenge aus dem Radius. Der eigentliche Deckel sitzt in
        # _corner_radius und rechnet mit der Mittellinie, damit gebuendelte
        # Spuren konzentrisch bleiben. Hier wird nur noch nachgezogen, wenn
        # die Tangente auf den TATSAECHLICHEN (versetzten) Beinen nicht
        # unterkommt -- das passiert, wo ein Versatzwechsel den Eckpunkt so
        # weit verschiebt, dass kaum Bein uebrig bleibt. Ein kleinerer Bogen
        # ist dort besser als eine scharfe Ecke.
        half = tan(abs(delta) / 2)
        t = radius * half
        fit = min(d_in, d_out)
        if t > fit:
            t = fit
        if t < 1e-9:
            parts.append(f"L {cur_pt[0]:.3f} {cur_pt[1]:.3f}")
            continue
        arc_r = t / half
        p_in = (cur_pt[0] - ux_in * t, cur_pt[1] - uy_in * t)
        p_out = (cur_pt[0] + ux_out * t, cur_pt[1] + uy_out * t)
        # y zeigt nach unten: positives Kreuzprodukt = Rechtsbogen
        sweep = 1 if cross > 0 else 0
        parts.append(f"L {p_in[0]:.3f} {p_in[1]:.3f}")
        parts.append(
            f"A {arc_r:.3f} {arc_r:.3f} 0 0 {sweep} "
            f"{p_out[0]:.3f} {p_out[1]:.3f}"
        )

    if gezeichnet < len(points) - 1:
        parts.append(f"L {points[-1][0]:.3f} {points[-1][1]:.3f}")
    return " ".join(parts)


def _bezier_controls(prev_pt: Pt, a: Pt, b: Pt) -> Tuple[Pt, Pt]:
    """Kontrollpunkte einer S-Bezier von `a` nach `b`, die an beiden Enden
    tangential zur Fahrtrichtung `prev_pt -> a` liegt.

    Beide Kontrollpunkte sitzen laengs der Fahrtrichtung, je auf halber
    Laengsstrecke -- damit geht die Kurve ohne Knick aus der Geraden heraus
    und wieder in sie hinein.
    """
    dx, dy = a[0] - prev_pt[0], a[1] - prev_pt[1]
    d = hypot(dx, dy)
    if d < 1e-9:
        return a, b
    ux, uy = dx / d, dy / d
    laengs = (b[0] - a[0]) * ux + (b[1] - a[1]) * uy
    c = laengs / 2
    return (a[0] + ux * c, a[1] + uy * c), (b[0] - ux * c, b[1] - uy * c)


def _bezier_flat(a: Pt, c1: Pt, c2: Pt, b: Pt, schritte: int) -> List[Pt]:
    """Kubische Bezier als Polygonzug (ohne den Startpunkt `a`)."""
    out: List[Pt] = []
    for k in range(1, schritte + 1):
        t = k / schritte
        m = 1.0 - t
        w = (m * m * m, 3 * m * m * t, 3 * m * t * t, t * t * t)
        out.append((
            w[0] * a[0] + w[1] * c1[0] + w[2] * c2[0] + w[3] * b[0],
            w[0] * a[1] + w[1] * c1[1] + w[2] * c2[1] + w[3] * b[1],
        ))
    return out


def rounded_points(
    points: Sequence[Pt],
    radii: Sequence[Optional[float]],
    schritte: int = 8,
    beziers: Sequence[int] = (),
) -> List[Pt]:
    """Derselbe Streckenzug wie `rounded_path_d`, aber als Polygonzug -- die
    Kreisboegen sind in `schritte` Sehnen zerlegt.

    Fuer geometrische Auswertungen am TATSAECHLICH gezeichneten Verlauf
    (z.B. Kreuzungspunkte). Der reine Stuetzpunktzug liegt in den Kurven
    daneben, weil er den Knick aussen umfaehrt statt ihn zu runden.
    """
    if len(points) < 3:
        return list(points)

    bez = {i for i in beziers if 0 <= i < len(points) - 1}
    out: List[Pt] = [points[0]]
    gezeichnet = 0
    for i in range(1, len(points)):
        if i <= gezeichnet:
            continue
        if (i - 1) in bez:
            c1, c2 = _bezier_controls(points[i - 2] if i >= 2 else points[i - 1],
                                      points[i - 1], points[i])
            out.extend(_bezier_flat(points[i - 1], c1, c2, points[i], schritte))
            gezeichnet = i
            continue
        if i == len(points) - 1:
            break
        gezeichnet = i
        radius = radii[i]
        prev_pt, cur_pt, next_pt = points[i - 1], points[i], points[i + 1]
        d_in = hypot(cur_pt[0] - prev_pt[0], cur_pt[1] - prev_pt[1])
        d_out = hypot(next_pt[0] - cur_pt[0], next_pt[1] - cur_pt[1])
        if not radius or d_in < 1e-9 or d_out < 1e-9:
            out.append(cur_pt)
            continue
        ux_in, uy_in = (cur_pt[0] - prev_pt[0]) / d_in, (cur_pt[1] - prev_pt[1]) / d_in
        ux_out, uy_out = (next_pt[0] - cur_pt[0]) / d_out, (next_pt[1] - cur_pt[1]) / d_out
        cross = ux_in * uy_out - uy_in * ux_out
        dot = ux_in * ux_out + uy_in * uy_out
        delta = atan2(cross, dot)
        if abs(delta) < 1e-9 or abs(abs(delta) - pi) < 1e-9:
            out.append(cur_pt)
            continue
        half = tan(abs(delta) / 2)
        t = min(radius * half, d_in, d_out)
        if t < 1e-9:
            out.append(cur_pt)
            continue
        arc_r = t / half
        p_in = (cur_pt[0] - ux_in * t, cur_pt[1] - uy_in * t)
        p_out = (cur_pt[0] + ux_out * t, cur_pt[1] + uy_out * t)
        # Mittelpunkt liegt senkrecht zur Einfahrt, auf der Kurveninnenseite.
        # y zeigt nach unten, deshalb ist (-uy, ux) "rechts der Fahrtrichtung".
        vz = 1.0 if cross > 0 else -1.0
        mx = p_in[0] + vz * arc_r * -uy_in
        my = p_in[1] + vz * arc_r * ux_in
        a0 = atan2(p_in[1] - my, p_in[0] - mx)
        out.append(p_in)
        for k in range(1, schritte):
            a = a0 + delta * k / schritte
            out.append((mx + arc_r * cos(a), my + arc_r * sin(a)))
        out.append(p_out)

    if gezeichnet < len(points) - 1:
        out.append(points[-1])
    return out


# ==========================================================================
# CONFIGURATION
# ==========================================================================


@dataclass
class NetworkConfig:
    """Phase 2 (Netz): Vorgaben beim Parsen und Ausrichten."""
    # Startwinkel fuer Linien ohne eigenen `start` und ohne Verbindung zu
    # einer bereits ausgerichteten Linie. Grad, 0 = Nord, im Uhrzeigersinn.
    default_start: int = 45
    # Untere Laengenschranke fuer FlexPath() ohne eigenes min_length.
    min_gap_default: float = 0.2

    # KREISRADIEN der Rundungen, in Gitter-Einheiten (wie spacing) -- nicht
    # in Pixeln, und nicht die Tangentenlaenge. Sie gelten fuer die
    # TRASSENMITTE. Was eine einzelne Linie zeichnet, haengt daneben von
    # ihrer Spur ab: laeuft im Bogen ein Nachbar mit, werden die Boegen
    # konzentrisch und die aeussere Spur bekommt den groesseren Radius.
    # Faehrt eine Linie den Bogen allein, behaelt sie diesen Wert, auch wenn
    # ihre Spur neben der Trassenmitte liegt. Wer den Radius an einer
    # sichtbaren Linie festmachen will statt an der Mitte, schreibt ihn in
    # `Corridor.radius_at`. Die Tangente wird intern
    # berechnet: t = R * tan(delta/2). Ein explizites Turn(radius=...) ist
    # ebenfalls ein Kreisradius und schlaegt diese Defaults.
    #
    # Je Knickwinkel ein eigener Wert, weil derselbe Radius bei 45 und bei
    # 135 Grad optisch sehr unterschiedlich wirkt (die Bogenlaenge ist
    # dreimal so gross). Sollen zwei Winkel gleich aussehen, gib ihnen
    # einfach denselben Wert -- das ist der Sinn echter Radien.
    #
    # Stehen hier und nicht in StyleConfig, weil eine Kurve Platz auf ihren
    # Nachbarbeinen braucht: der Radius geht als untere Laengenschranke in
    # den Solver ein (siehe _build_legs_from_modifiers). Ein Radius ist
    # damit Geometrie, keine Darstellung.
    curve_radius_45: float = 0.8
    curve_radius_90: float = 1.2
    curve_radius_135: float = 0.4

    # Form des Spurwechsels auf gerader Strecke (Corridor(immediate=True)).
    # False: zwei tangentiale Kreisboegen ueber einen 45-Grad-Schraegteil,
    #        die einander genau beruehren -- passt zum Rest der Karte.
    # True:  eine kubische Bezier zwischen denselben beiden Punkten.
    #        Nur hier vertretbar, weil die Linie im Schwenk allein laeuft.
    shift_bezier: bool = True
    # Nur fuer shift_bezier: Laengsstrecke des Schwenks als Vielfaches der
    # Versatzbreite. 1.0 waere der gleiche Fussabdruck wie die Bogenvariante
    # -- dann steckt die ganze Kruemmung in der Mitte und die Kurve wirkt
    # eckig. Groessere Werte ziehen den Schwenk in die Laenge und machen ihn
    # flacher; begrenzt wird er ohnehin durch die Laenge des Beins.
    shift_bezier_span: float = 3.0

    def curve_radius(self, delta: int) -> float:
        """Default-Kreisradius fuer einen Knick um `delta` Grad."""
        angle = abs(((delta + 180) % 360) - 180)
        return {
            45: self.curve_radius_45,
            135: self.curve_radius_135,
        }.get(angle, self.curve_radius_90)


@dataclass
class SolverConfig:
    """Phase 3 (Masse): numerische Parameter des Least-Squares-Solvers."""
    # Horizontaler Abstand zwischen Netzteilen, die untereinander keine
    # gemeinsame Station haben.
    component_gap: float = 40.0
    coordinate_regularization: float = 1e-10
    constraint_tolerance: float = 1e-7


@dataclass
class StyleConfig:
    """Phase 5 (Darstellung): reine Optik. Aendert nie die Geometrie --
    diese Werte lassen sich anpassen und neu rendern, ohne neu zu loesen."""
    # Massstab: eine Gitter-Einheit in Pixeln, plus Rand um die Karte.
    grid: float = 28.0
    margin: float = 36.0

    # Korridore und hervorgehobene Linien
    track_color: str = "#444444"
    line_width: float = 1.0
    highlight_line_width: float = 8.0

    # Stationen
    station_r: float = 2.6
    station_stroke: float = 1.0
    # Legende (Tabelle der Zuggruppen)
    # Legende: Schrift und Signets uebernimmt sie von der Karte
    # (label_font bzw. badge_*), eigene Werte gibt es nur fuer das Raster.
    legend_row_height: float = 18.0
    # Zeilenhoehe, wenn eine Linie mehrere Zuggruppen traegt -- etwas
    # kompakter als eine alleinstehende Zeile, damit die Tabelle bei
    # vielen mehrzeiligen Linien nicht unnoetig hoch wird.
    legend_row_height_multi: float = 14.0
    legend_padding: float = 5.0
    # Die Viertelzuege ruecken etwas enger zusammen als frueher (Abstand 2.0),
    # bleiben aber deutlich voneinander abgesetzt -- man muss sie zaehlen
    # koennen. Die groessere Breite gleicht den kleineren Abstand aus, damit
    # ein Vollzug gleich lang bleibt: 4*15.75 + 3*1 == 4*15 + 3*2.
    legend_car_width: float = 16.0
    legend_car_height: float = 5.0
    legend_car_gap: float = 0.5

    # Deckkraft der Stationsmarkierung bei Station(planned=True) -- geplante,
    # noch nicht gebaute Halte stehen dadurch blasser auf der Linie. Gilt fuer
    # den weissen Punkt ebenso wie fuer den Endpunktring samt Farbpunkt, damit
    # beide gleich blass wirken.
    planned_opacity: float = 0.6
    # Hubs bekommen ein weisses Rechteck mit schwarzem Rand ueber die
    # Linien gelegt, darauf je Linie einen Punkt in ihrer Farbe.
    # `hub_pill_r` ist der Eckradius; das Rechteck wird um genau diesen Wert
    # ueber die aeussersten Stationspunkte hinaus aufgeweitet, wodurch seine
    # schmale Seite 2*r misst und die Enden zu Halbkreisen werden.
    hub_pill_r: float = 6.0
    hub_pill_stroke: float = 2.5
    hub_dot_r: float = 3.0
    # Endstationen: weisser Kreis mit schwarzem Rand und Farbpunkt darin.
    terminus_ring_r: float = 6.0
    terminus_ring_stroke: float = 2.5
    terminus_dot_r: float = 3.0

    # Buendelung: Zwischenraum zwischen zwei Linien, die sich einen Track
    # teilen -- als Vielfaches der Linienstaerke. 0.5 = eine halbe
    # Linienstaerke Luft zwischen den Linienraendern. Der Versatz je
    # Slot-Schritt ist damit Linienstaerke + Zwischenraum, ein Slot von 0.5
    # rueckt eine Linie also um die Haelfte davon aus der Mitte.
    bundle_gap_factor: float = 0.5

    @property
    def bundle_gap(self) -> float:
        """Zwischenraum zwischen zwei benachbarten Linien, in Pixeln."""
        return self.highlight_line_width * self.bundle_gap_factor

    @property
    def bundle_spacing(self) -> float:
        """Abstand zweier benachbarter Linienmitten, in Gitter-Einheiten."""
        return (self.highlight_line_width + self.bundle_gap) / self.grid

    # Labels
    label_font: float = 10.0
    label_family: str = "'Helvetica Neue', Helvetica, Arial, sans-serif"
    label_fill: str = "#111111"
    # Weisser Rand der oben liegenden Linie an Kreuzungen ohne Hub, je Seite.
    crossing_casing: float = 2.0

    # Linien-Plaketten an den Endpunkten: farbiges Oval unter dem
    # Stationsnamen, weisser fetter Liniennamen darin.
    badge_font: float = 12.0
    badge_height: float = 15.0
    badge_padding: float = 3.0
    badge_gap: float = 3.0
    # Randstaerke des umrandeten Signets an Zwischenenden (weiss gefuellt,
    # Rand und Schrift in Linienfarbe). Der Rand liegt innen.
    badge_outline: float = 1.5
    # Um so viel ist das umrandete Signet rundum groesser als ein volles --
    # Schrift und Randstaerke bleiben dabei gleich. Die Mitte bleibt auf der
    # Hoehe der Reihe; in der Breite nimmt es entsprechend mehr Platz ein,
    # der Abstand zum Nachbarn bleibt `badge_gap`.
    badge_outline_grow: float = 0.25
    # Farbige Schrift auf Weiss wirkt duenner als weisse auf Farbe. Eine
    # feine Kontur in derselben Farbe gleicht das aus; ein hoeheres
    # font-weight taete es nicht zuverlaessig, weil nicht jeder Betrachter
    # dafuer einen eigenen Schnitt findet.
    badge_outline_text: float = 0.3

    # Richtungspfeile (`Net.direction_arrows`): zwei spitze Dreiecke, eines
    # in Linienfarbe und darunter ein weisses, in Fahrtrichtung so weit
    # vorgeschoben, dass an beiden Flanken ein Rand von `arrow_edge` stehen
    # bleibt. Die Grundseite ist je Seite um `arrow_overhang` breiter als die
    # Linie, der Winkel an der Spitze `arrow_tip_angle` (Grad). Jeder Pfeil
    # steht um `arrow_stagger` gegen seine Fahrtrichtung versetzt -- zwei
    # gegenlaeufige nebeneinander ruecken so auseinander. Pixel.
    arrow_overhang: float = 2.5
    arrow_tip_angle: float = 50.0
    arrow_edge: float = 1.25
    arrow_stagger: float = 10.0

    label_clearance: float = 8.0
    # Hubs und Endstationen tragen ein groesseres Symbol als ein einfacher
    # Stationspunkt. Seitliche Beschriftungen ruecken dort um diesen Betrag
    # weiter nach aussen, sonst kleben sie am Marker.
    label_marker_clearance: float = 2.0
    # Geklammerte Namenszusaetze ("(Eichkamp)") werden um so viele Punkte
    # kleiner gesetzt als der Stationsname selbst.
    label_suffix_smaller: float = 1.5


@dataclass
class Config:
    """Alle Phasen-Konfigurationen unter einem Dach.

    `solve_layout()` bekommt das ganze Config-Objekt (es durchlaeuft Phase
    2-4), `build_track_svg()` dagegen nur `.style` -- damit ist an der
    Signatur ablesbar, dass das Rendern die Geometrie nicht beeinflusst.
    """
    netz: NetworkConfig = field(default_factory=NetworkConfig)
    solver: SolverConfig = field(default_factory=SolverConfig)
    style: StyleConfig = field(default_factory=StyleConfig)


CFG = Config()


# ==========================================================================
# DEFINITIONSSPRACHE
# ==========================================================================


# Normierte 45-Grad-Richtungen
_D = 1.0 / sqrt(2.0)


COMPASS: Dict[int, Pt] = {
    0: (0.0, -1.0),
    45: (_D, -_D),
    90: (1.0, 0.0),
    135: (_D, _D),
    180: (0.0, 1.0),
    225: (-_D, _D),
    270: (-1.0, 0.0),
    315: (-_D, -_D),
}


class GeometryError(ValueError):
    pass


# Die acht moeglichen Lagen einer Beschriftung, als Einheitsvektor vom
# Stationspunkt aus (y zeigt nach unten).
LABEL_DIRECTION: Dict[str, Pt] = {
    "top": (0.0, -1.0),
    "bottom": (0.0, 1.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "top_left": (-_D, -_D),
    "top_right": (_D, -_D),
    "bottom_left": (-_D, _D),
    "bottom_right": (_D, _D),
}


@dataclass(frozen=True)
class Turn:
    """Relativer Richtungswechsel zwischen zwei Stationen.

    `radius` ist der KREISRADIUS der Rundung. Er legt zugleich die
    Standard-Beinlaenge auf JEDER Seite dieses Turns fest (vor UND nach dem
    Knick), solange dort kein FlexPath() steht -- naemlich auf die
    zugehoerige Tangentenlaenge R * tan(delta/2). None = die
    Haelfte des normalen Stationsabstands der Linie (line.spacing / 2) --
    das setzt den Knick standardmaessig genau in die Mitte des Abschnitts:
        "A", Turn(45), "B"                  -> Knick genau in der Mitte
        "A", Turn(45), FlexPath(), "B"      -> festes halbes Stueck bis zum
                                               Knick, danach elastisch
        "A", FlexPath(), Turn(45), "B"      -> Spiegelbild davon
    """
    delta: int
    radius: Optional[float] = None

    def __post_init__(self) -> None:
        if self.delta % 45 != 0:
            raise ValueError("Turns muessen Vielfache von 45 Grad sein")
        if self.radius is not None and self.radius <= 0:
            raise ValueError("radius muss positiv sein")


@dataclass(frozen=True)
class Path:
    """Markiert das direkt angrenzende Beinstueck -- egal ob es das Stueck
    vor einem Turn, das Stueck danach, oder ein ganz gerader Sprung ohne
    jeden Turn ist. Wird nicht direkt konstruiert, sondern ueber die
    Fabrikfunktionen `FlexPath()` bzw. `FixPath()`.

    length        bei flexibility=True die bevorzugte (preferred) Laenge,
                  die der Solver anpassen darf; bei flexibility=False die
                  exakte, starre Laenge.
    minimum       untere Schranke fuer den Solver. Nur bei flexibility=True
                  gesetzt, sonst None.
    maximum       obere Schranke fuer den Solver. Nur bei flexibility=True
                  gesetzt, sonst None.
    flexibility   True = elastisch (Solver bestimmt die Laenge zwischen
                  minimum und maximum), False = starr mit exakt `length`.
    flex          Steifigkeit des elastischen Beins (nur flexibility=True).
    group         koppelt mehrere elastische Beinstuecke derselben Linie auf
                  eine gemeinsame Laenge (z.B. gleichmaessige Abstaende
                  entlang einer Ringseite).
    """
    length: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    flexibility: bool = True
    flex: float = 1.0
    group: Optional[str] = None

    def __post_init__(self) -> None:
        if self.flexibility:
            if self.length is not None and self.length <= 0:
                raise ValueError("length muss positiv sein")
            if self.minimum is not None and self.minimum <= 0:
                raise ValueError("minimum muss positiv sein")
            if self.maximum is not None and self.maximum <= 0:
                raise ValueError("maximum muss positiv sein")
            if (
                self.minimum is not None
                and self.maximum is not None
                and self.minimum > self.maximum
            ):
                raise ValueError("minimum darf nicht groesser als maximum sein")
            if self.flex <= 0:
                raise ValueError("flex muss positiv sein")
        else:
            if self.length is None or self.length < 0:
                raise ValueError("FixPath benoetigt eine Laenge >= 0")
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("FixPath hat kein minimum/maximum")


def FlexPath(
    preferred: Optional[float] = None,
    min_length: Optional[float] = None,
    max_length: Optional[float] = None,
    flex: float = 1.0,
    group: Optional[str] = None,
) -> Path:
    """Elastisches Beinstueck. Ohne Angabe wird `preferred` zum normalen
    Stationsabstand der Linie (line.spacing); `min_length`/`max_length`
    fallen ohne Angabe auf die Config-Vorgabe bzw. unbeschraenkt zurueck."""
    return Path(
        length=preferred,
        minimum=min_length,
        maximum=max_length,
        flexibility=True,
        flex=flex,
        group=group,
    )


def FixPath(length: float) -> Path:
    """Starres Beinstueck mit einer explizit angegebenen Laenge (statt dem
    sonst verwendeten spacing bzw. Turn-radius).

    Laenge 0 ist erlaubt und hat eine eigene Bedeutung: `FixPath(0.0)` vor
    einem Turn setzt den Knick GENAU auf die Station statt irgendwo zwischen
    zwei Stationen. Nur so kann eine Linie an einem Bahnhof abbiegen, dessen
    beide Nachbarkanten sie mit geradeaus fahrenden Linien teilt -- ein Bein
    der Laenge 0 verschiebt die Nachbarstation nicht und zaehlt deshalb auch
    nicht zur Form des gemeinsamen Korridors."""
    return Path(length=length, flexibility=False)


Step = Union[str, Turn, Path]


@dataclass(frozen=True)
class Corridor:
    """Ein von Hand festgelegter Streckenabschnitt mit fester Spurlage.

    steps    Stationsfolge -- wie die Strecken-Konstanten, Turn()/FlexPath()
             darin werden ignoriert, es zaehlen nur die Stationen.
    offsets  Linien-ID oder Familie -> Versatz in Slot-Einheiten. Die
             Linien-ID gewinnt, eine Familie gilt also als Vorgabe fuer alle
             ihre Linien, die keinen eigenen Eintrag haben.
             also Vielfache von StyleConfig.bundle_spacing. Gilt fuer JEDE
             Kante des Korridors -- auch dort, wo eine Linie allein faehrt.

    Eine Linie, die den Korridor befaehrt, aber weder ueber ihre Familie
    noch ueber ihre ID in `offsets` steht, ist ein Fehler -- sonst rutscht
    eine neu hinzugefuegte Linie unbemerkt an den Rand.

    immediate
             Versatzwechsel schon an der Kantengrenze uebernehmen, mit einem
             Schwenk auf gerader Strecke, statt auf die naechste Kurve zu
             warten. Nur dort einsetzen, wo eine Nachbarspur frei wird und
             die Linie ohne Kurve nachruecken soll -- sonst entstehen
             Schlangenlinien, genau die, gegen die die Verzoegerung gedacht
             ist.
    start_offsets
             Spurlage fuer Linien, die auf dieser Kante BEGINNEN -- und zwar
             nur bis zur ersten Kurve, danach gilt wieder `offsets`. Ohne
             Angabe setzt eine beginnende Linie sofort auf `offsets` auf.
             Das ist meist richtig, nicht aber dort, wo eine durchfahrende
             Linie ihren Versatz erst in der Kurve uebernimmt und die
             beginnende Linie bis dahin neben ihr laufen soll.
    radius_from_centre
             Haelt an der aelteren Lesart fest: der Radius gilt fuer die
             TRASSENMITTE, jede Spur bekommt ihn um ihren eigenen Versatz
             korrigiert -- die innere also enger als den Definitionswert.
             Normalerweise gilt er fuer die innerste Spur des Buendels und
             ist damit eine Untergrenze. Gedacht fuer Achsen, deren Form
             steht und sich durch eine Regeländerung nicht verschieben soll
             (die Ringbahn).
    radius_at
             Kreisradius der Knicke DIESER Kante, ausgedrueckt an EINER
             Linie: {"S15": 0.8} heisst "die S15 zeichnet einen Bogen mit
             Radius 0.8". Alle anderen Linien der Kante bleiben dazu
             konzentrisch -- ihr Radius ergibt sich aus dem Spurabstand zur
             Bezugslinie. Das ist die Schreibweise, die man beim Hinsehen
             pruefen kann: der Wert gilt fuer eine Linie, die man auf der
             Karte findet, nicht fuer die unsichtbare Trassenmitte. Er gilt
             ausserdem unabhaengig davon, ob im Bogen ein Nachbar mitlaeuft.
    radii
             Notausgang: Kreisradius je Linie, OHNE Ruecksicht auf
             Konzentrizitaet. Nur dort einsetzen, wo die Linien sich hinter
             dem Bogen ohnehin trennen -- innerhalb eines durchlaufenden
             Buendels driften sie damit im Bogen auseinander. Ersetzt den
             Wert, den die Trasse traegt. Ein Buendel faehrt dieselbe
             Trasse, seine Linien liegen darauf aber auf verschiedenen
             Spuren -- und die aussen liegende darf ihren Bogen weiter
             ausfahren als die innere. Wirkt nur auf die Zeichnung: die
             Trasse selbst, und damit jede Laenge im Netz, bleibt
             unveraendert. Schluessel ist die Linien-ID oder ihre Familie.
    shift_at
             Wo auf dem Bein der Schwenk sitzt: "start", "middle" (Default)
             oder "end". Gemeint ist die FAHRTRICHTUNG der schwenkenden
             Linie, nicht die Schreibrichtung des Korridors -- "start"
             heisst also: gleich nachdem die Linie die Kante betritt.
             Wirkt nur zusammen mit immediate=True.

    Das Vorzeichen bezieht sich auf die Richtung, in der `steps` geschrieben
    ist.
    """
    steps: Sequence[Step]
    offsets: Mapping[str, float]
    start_offsets: Mapping[str, float] = field(default_factory=dict)
    radius_from_centre: bool = False
    radius_at: Mapping[str, float] = field(default_factory=dict)
    radii: Mapping[str, float] = field(default_factory=dict)
    immediate: bool = False
    shift_at: str = "middle"

    def __post_init__(self) -> None:
        if self.shift_at not in ("start", "middle", "end"):
            raise GeometryError(
                f"Corridor.shift_at: '{self.shift_at}' ist keine Lage "
                f"(erlaubt: start, middle, end)"
            )


@dataclass(frozen=True)
class Station:
    """Eine Station im Netz.

    id      eindeutiger Bezeichner, in Strecken und Linien verwendet
            (z.B. "gesundbrunnen"). Nie am Nutzer sichtbar.
    name    Anzeigename (z.B. "Gesundbrunnen").
    label   Text fuer die Karte. Standard: identisch mit `name` -- nur bei
            Bedarf abweichend definieren, z.B. fuer einen Zeilenumbruch:
            label="Berlin\\nHauptbahnhof".
    kind    "station" oder "hub" -- Hubs bekommen in der zweiten
            Rendering-Schicht ein weisses "Pill" quer zum Linienbuendel.
    label_pos
            Lage der Beschriftung, falls die automatische Regel nicht passt:
            "top", "bottom", "left", "right" sowie die vier Diagonalen
            "top_left", "top_right", "bottom_left", "bottom_right".
            None = automatisch aus der Trassenrichtung.
    planned Station ist geplant, aber noch nicht in Betrieb. Ihr weisser
            Punkt wird blasser gezeichnet (StyleConfig.planned_opacity).
            Die Klammern um den Namen gehoeren in `label` -- das ist eine
            Frage der Beschriftung, keine des Renderers.
    hidden  Reiner Korridor-Wegpunkt: zaehlt fuer Strecken, Linien und
            Spurversatz wie jede andere Station, bekommt aber keinen
            Stationspunkt gezeichnet. Das Label bleibt davon unberuehrt --
            fuer einen wirklich unsichtbaren Punkt zusaetzlich label="".
    pill_with
            ID eines Hubs, dessen Pille diese Station mit ueberspannen soll.
            Fuer den Fall, dass EIN Bahnhof auf zwei Trassen liegt, die sich
            nicht treffen: jede Trasse braucht ihren eigenen Knoten, aber
            gezeichnet wird eine einzige Pille ueber beide. Der Marker der
            zweiten Station entfaellt damit; ihre Beschriftung sollte leer
            sein, den Namen traegt der Hub.
    """
    id: str
    name: str
    label: Optional[str] = None
    kind: str = "station"
    label_pos: Optional[str] = None
    planned: bool = False
    hidden: bool = False
    pill_with: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("id darf nicht leer sein")
        if not self.name.strip():
            raise ValueError("name darf nicht leer sein")
        if self.kind not in ("station", "hub"):
            raise ValueError("kind muss 'station' oder 'hub' sein")
        if self.label_pos is not None and self.label_pos not in LABEL_DIRECTION:
            raise ValueError(
                f"label_pos '{self.label_pos}' unbekannt -- erlaubt: "
                + ", ".join(sorted(LABEL_DIRECTION))
            )
        if self.label is None:
            object.__setattr__(self, "label", self.name)


def _station_registry(stations: Iterable[Station]) -> Dict[str, Station]:
    registry: Dict[str, Station] = {}
    for station in stations:
        if station.id in registry:
            raise ValueError(f"Station-ID '{station.id}' ist doppelt vergeben")
        registry[station.id] = station
    return registry


@dataclass
class TrainGroup:
    """Eine Zuggruppe einer Linie -- eine Zeile in der Legendentabelle.

    kind    Bezeichnung der Gruppe, so wie sie in der Tabelle steht:
            "Stammzuggruppe", "Tageszuggruppe", "HVZ-Verstaerker" oder
            "Alle Zuggruppen". Freier Text, damit die Tabelle nicht an eine
            feste Aufzaehlung gebunden ist.
    route   Laufweg, ebenfalls wortwoertlich: "Wannsee <> Oranienburg".
            Die beiden Namen steuern auch die Karte: endet eine Gruppe an
            einer Station, an der die Linie selbst weiterfaehrt, bekommt
            diese ein umrandetes Signet. Sie muessen deshalb genau so heissen
            wie die Station (`Station.name`); ein Laufweg ohne " <> " wie
            "Ringbahn in beide Richtungen" bleibt ohne Signet. Dazwischen
            darf ein Durchfahrtspunkt stehen ("Zehlendorf <> Hbf <>
            Frohnau") -- Endpunkte sind nur der erste und der letzte Name.
    cars    Zugstaerke in Viertelzuegen (1 bis 4) -- so viele Wagenzeichen
            stehen in der letzten Spalte.
    hollow  Wie viele der `cars` nur umrandet statt gefuellt gezeichnet
            werden. Fuer Abschnitte, auf denen die Linie schwaecher faehrt.
    note    Fussnote unter der Tabelle, z.B. der abweichende Abschnitt.
    note_cars
            Zugstaerke, die in der Fussnote hinter dem Text steht. 0 = keine.
    """
    kind: str
    route: str
    cars: int = 4
    hollow: int = 0
    note: str = ""
    note_cars: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.cars <= 4:
            raise ValueError("cars muss zwischen 1 und 4 liegen")
        if not 0 <= self.hollow <= self.cars:
            raise ValueError("hollow darf hoechstens cars sein")


@dataclass
class TurnLine:
    """Eine Linie in der Definitionssprache.

    color       fuer die spaetere zweite Rendering-Schicht; die Gleiskarte
                zeichnet noch einheitlich graue Korridore.
    steps       Stationen, Turn() und FlexPath() in Fahrtreihenfolge.
    start       absoluter Startwinkel in Grad (0 = Nord, im Uhrzeigersinn).
                None = aus den gemeinsamen Korridoren herleiten.
    anchor      feste Startkoordinate; None = Solver waehlt sie.
    spacing     Standardabstand zwischen zwei Stationen.
    direction   reines Label fuer den Report, ohne Wirkung auf die Geometrie.
    closed      letzte Station == erste Station -> Ring schliessen.
    groups      Zuggruppen der Linie fuer die Legendentabelle. Leer = die
                Linie taucht dort nicht auf.
    family      Linienfamilie: Varianten derselben Linie (S2/S25/S26,
                S1/S15, S8/S85, S46/S47) teilen sich EINEN Platz im
                Buendel, laufen also auf gemeinsamen Abschnitten
                uebereinander statt nebeneinander. Ueblicherweise die ID
                der Stammlinie; None = die Linie bildet ihre eigene
                Familie.
    branch_of   Diese Linie ist ein ZWEIG der genannten Linie: ein zweiter
                Streckenzug derselben Linie, der irgendwo auf sie trifft.
                Gedacht fuer den zeitweisen Laufweg -- die S85 faehrt
                ausserhalb der HVZ nicht bis Frohnau, sondern ab der
                Bornholmer Strasse nach Pankow. Eine TurnLine ist EIN
                Streckenzug, zwei Nordenden passen nicht hinein.
                Ein Zweig traegt das Signet seiner Stammlinie und zeigt es
                nur an seinem FREIEN Ende; an dem Ende, mit dem er auf die
                Stammlinie trifft, endet nichts -- dort gibt es weder
                Signet noch Endstationsring.
    """
    color: str
    steps: List[Step]
    start: Optional[int] = None
    anchor: Optional[Pt] = None
    spacing: float = 1.2
    direction: str = ""
    closed: bool = False
    family: Optional[str] = None
    branch_of: Optional[str] = None
    groups: Sequence[TrainGroup] = ()

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("Eine Linie benoetigt mindestens zwei Stationen")
        if self.start is not None and self.start % 45 != 0:
            raise ValueError("start muss ein Vielfaches von 45 Grad sein")
        if self.spacing <= 0:
            raise ValueError("spacing muss positiv sein")


# ==========================================================================
# INTERNES MODELL
# ==========================================================================


@dataclass(frozen=True)
class LengthSpec:
    preferred: float
    elastic: bool
    group: Optional[str]
    min_gap: float
    max_gap: float
    flex: float


@dataclass(frozen=True)
class Corner:
    """Ein Knick zwischen zwei Beinen, mit fertig aufgeloestem Kreisradius.

    Der Radius ist Geometrie, nicht Optik: ueber seine Tangente
    t = R * tan(delta/2) belegt der Bogen Platz auf beiden Nachbarbeinen.
    Deshalb steht er schon beim Parsen fest und geht in Phase 3 als untere
    Laengenschranke in den Solver ein.

    delta   Drehwinkel des Turns.
    radius  KREISRADIUS -- explizites Turn(radius=...) oder, falls dort
            nichts steht, NetworkConfig.curve_radius(delta).
    forced
            True bei explizitem Turn(radius=...). Solche Radien werden
            NICHT gedeckelt: nur so laesst sich eine Kurve bewusst so weit
            aufziehen, dass das gerade Stueck dazwischen verschwindet.
    offset  seitlicher Versatz der Linie an diesem Knick (0 = Trassenmitte),
            in Gitter-Einheiten. Eine gebuendelte Linie faehrt den Bogen
            innen enger und aussen weiter -- sonst laufen die Linien in der
            Kurve nicht mehr parallel.
    max_tangent
            obere Schranke fuer die TANGENTENLAENGE (nicht den Radius), denn
            begrenzend ist der Platz auf den Beinen. Aus den Beinlaengen der
            MITTELLINIE (freier Rest des kuerzeren Nachbarbeins, also
            abzueglich dessen, was der Knick am anderen Ende schon
            belegt). Muss aus der
            Mittellinie kommen und fuer alle Spuren gleich sein: die
            versetzten Spuren haben durch die Gehrung unterschiedlich lange
            Beine, ein je Spur eigener Deckel wuerde die Boegen wieder
            unkonzentrisch machen.
    """
    delta: int
    radius: Optional[float] = None
    offset: float = 0.0
    max_tangent: Optional[float] = None
    forced: bool = False

    def tangent(self) -> float:
        """Platzbedarf des Bogens auf JEDEM der beiden Nachbarbeine."""
        return 0.0 if self.radius is None else self.radius * _corner_tangent_factor(self.delta)


@dataclass(frozen=True)
class LegSpec:
    bearing_offset: int   # relativ zum Startwinkel der Linie
    length: LengthSpec
    corner: Optional[Corner] = None  # Knick VOR diesem Bein; None = kein Knick


# Form eines Segments, unabhaengig von seinem absoluten Startwinkel:
# je Bein (Richtung relativ zum ersten Bein, elastisch, preferred, min_gap, max_gap, flex)
ShapeSignature = Tuple[Tuple[int, bool, float, float, float, float], ...]


@dataclass(frozen=True)
class SegmentSpec:
    line_id: str
    index: int
    a: str
    b: str
    legs: Tuple[LegSpec, ...]


@dataclass
class ParsedLine:
    line_id: str
    stations: List[str]
    segments: List[SegmentSpec]
    closed: bool = False   # Ringlinie: hat weder Anfangs- noch Endstation
    branch_of: Optional[str] = None   # siehe TurnLine.branch_of
    # Das Ende, mit dem ein Zweig auf seine Stammlinie trifft. Steht erst
    # nach build_network fest, denn dafuer muss die Stammlinie geparst sein.
    branch_join: Optional[str] = None


@dataclass
class LengthVariable:
    key: Tuple[str, str]
    preferred: float
    min_gap: float
    max_gap: float
    flex: float
    occurrences: List[Tuple[str, int, int]] = field(default_factory=list)


@dataclass
class Network:
    """Ergebnis von Phase 2 (Netz): welche Stationen, welche Kanten, welche
    Form -- und in welcher absoluten Richtung jede Linie startet. Noch ohne
    jede Koordinate."""
    parsed: Dict[str, ParsedLine]
    starts: Dict[str, int]        # Linie -> absoluter Startwinkel in Grad


@dataclass
class Measures:
    """Ergebnis von Phase 3 (Masse): die geloesten Zahlen. Hier bekommen die
    FlexPaths ihre Laenge und die Stationen ihre Koordinaten."""
    coords: Dict[str, Pt]
    leg_lengths: Dict[Tuple[str, int, int], float]  # (Linie, Segment, Bein) -> Laenge


@dataclass
class Tracks:
    """Ergebnis von Phase 4 (Gleise): fertige Polylinien je physischem
    Streckenabschnitt, pro Stationspaar genau einmal (gemeinsame Korridore
    sind dedupliziert)."""
    corridor_paths: Dict[frozenset[str], List[Pt]]
    corridor_corners: Dict[frozenset[str], List[Optional[Corner]]]  # je Punkt; Enden immer None


@dataclass
class LayoutResult:
    """Die Ergebnisse der Phasen 2-4 zusammen -- das, was Phase 5 zum
    Zeichnen braucht. Die Teile sind einzeln erzeugbar (`build_network`,
    `solve_measures`, `build_tracks`); `solve_layout` verkettet sie nur."""
    network: Network
    measures: Measures
    tracks: Tracks


@dataclass
class LinePath:
    """Der durchgehende Streckenzug einer Linie in Fahrtreihenfolge,
    inklusive Buendel-Versatz. `corners[i]` gehoert zu `points[i]`.

    `stations` haelt zusaetzlich fest, wo jede Station AUF DIESER LINIE
    liegt -- also mit ihrem Versatz, nicht auf der Trassenmitte. Damit
    bekommt jede Spur eines Buendels ihren eigenen Stationspunkt.
    """
    points: List[Pt]
    corners: List[Optional[Corner]]
    stations: Dict[str, Pt] = field(default_factory=dict)
    # Zweite Lage an Stationen, an denen die Linie die Spur WECHSELT -- dort
    # liegt sie vor und hinter der Station verschieden. `stations` haelt die
    # ankommende Seite, hier steht die abfahrende. Die Hub-Pille braucht
    # beide, sonst deckt sie nur die halbe Station ab.
    lane_changes: Dict[str, Pt] = field(default_factory=dict)
    # Segmente, die als kubische Bezier statt als Gerade gezeichnet werden:
    # Index i meint points[i] -> points[i+1]. Nur fuer den Spurwechsel auf
    # gerader Strecke, siehe NetworkConfig.shift_bezier.
    beziers: List[int] = field(default_factory=list)


@dataclass
class LineLayout:
    """Ergebnis von Phase 4b (Linienfuehrung).

    paths  je betrachteter Linie ihr fertiger Streckenzug
    slots  je Korridorkante und Linie der vergebene Slot (0 = Mitte);
           dient der Nachvollziehbarkeit und zum Testen der Buendelung
    """
    paths: Dict[str, LinePath]
    slots: Dict[frozenset[str], Dict[str, float]]
    families: Dict[str, str]   # Linie -> Familie (Linien ohne Familie: sich selbst)
