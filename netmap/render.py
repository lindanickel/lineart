"""
Phase 5: das geloeste Layout als SVG zeichnen.

Oben die Regeln, wo eine Beschriftung sitzt -- reine Rechnung ohne SVG.
Darunter das eigentliche Zeichnen und der Textreport.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import replace
from math import ceil, cos, floor, hypot, radians, sin, sqrt
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .model import (
    CFG, COMPASS, Corner, LABEL_DIRECTION, LayoutResult, LineLayout, Pt,
    Station, StyleConfig, TurnLine, _corner_tangent_factor, _norm, _pair_key,
    _segment_hits_box, polyline_path_d, rounded_path_d, rounded_points,
    badge_text, badge_width, text_width,
)
from .legend import build_legend
from .solve import _edge_bearing, _bearing_of, build_line_layout


# ==========================================================================
# LABEL PLACEMENT
# ==========================================================================


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def auto_label(direction: Pt, clearance: float) -> Tuple[float, float, str]:
    ux, uy = _norm(direction) if direction != (0.0, 0.0) else (1.0, 0.0)
    # waagerecht -> Label unten
    if abs(uy) < 0.35:
        return (0.0, clearance + 7.0, "middle")
    # senkrecht -> Label links
    if abs(ux) < 0.35:
        return (-clearance, 3.0, "end")
    # diagonal -> unter die Linie, also bottom_left bzw. bottom_right
    nx, ny = -uy, ux
    if ny < 0:
        nx, ny = -nx, -ny
    dx = nx * clearance
    anchor = "start" if dx >= 0 else "end"
    return (dx, ny * clearance + 7.0, anchor)


def suffix_start(text: str) -> Optional[int]:
    """Ab welcher Zeile eines Labels der geklammerte Namenszusatz beginnt.

    Ein Zusatz ist nur, was NACH dem eigentlichen Namen kommt -- etwa
    "Messe Sued\n(Eichkamp)". Steht dagegen der ganze Name in Klammern
    (also schon Zeile 0), ist die Klammer Teil des Namens und keine
    Ergaenzung: "(Perlegerberger Bruecke)" wird normal gross gesetzt.
    """
    for i, zeile in enumerate(text.split("\n")):
        if zeile.startswith("("):
            return None if i == 0 else i
    return None


def is_suffix(text: str, index: int) -> bool:
    """Gehoert Zeile `index` von `text` zum geklammerten Zusatz?"""
    start = suffix_start(text)
    return start is not None and index >= start


def suffix_font(text: str, index: int, style: "StyleConfig") -> float:
    """Schriftgroesse einer einzelnen Beschriftungszeile."""
    if is_suffix(text, index):
        return style.label_font - style.label_suffix_smaller
    return style.label_font


def label_width(text: str, font: float, zusatz_kleiner: float = 0.0) -> float:
    """Breite der breitesten Zeile einer Beschriftung in Pixeln.

    Ein geklammerter Namenszusatz wird kleiner gesetzt und deshalb auch
    schmaler gerechnet.
    """
    return max(
        text_width(z, font - zusatz_kleiner if is_suffix(text, i) else font)
        for i, z in enumerate(text.split("\n"))
    )


def label_offset(pos: str, clearance: float) -> Tuple[float, float, str]:
    """(dx, dy, text-anchor) fuer eine der acht Label-Lagen.

    `dy` beruecksichtigt, dass die y-Koordinate eines <text> auf der
    Grundlinie sitzt: unterhalb liegende Beschriftungen brauchen den vollen
    Zeilenabstand, oberhalb liegende nur einen kleinen Ausgleich.
    """
    nx, ny = LABEL_DIRECTION[pos]
    dx = nx * clearance
    if ny < -0.3:
        dy = ny * clearance - 1.0
    elif ny > 0.3:
        dy = ny * clearance + 7.0
    else:
        dy = 3.0
    if abs(nx) < 0.3:
        anchor = "middle"
    else:
        anchor = "start" if nx > 0 else "end"
    return dx, dy, anchor


def multiline_offset(ax: float, ay: float, versatz: float) -> Tuple[float, float]:
    """Zusatzverschiebung (dx, dy) fuer einen mehrzeiligen Beschriftungsblock.

    `versatz` ist der Abstand zwischen erster und letzter Grundlinie; der
    Block waechst also um diesen Betrag nach unten.

    Seitliche Lagen (left/right und die vier Schraegen) werden mittig zur
    Station gesetzt: halbe Blockhoehe hoch. Bei den Schraegen kommt derselbe
    Betrag waagerecht dazu, und zwar nach AUSSEN -- dann laeuft die
    Gesamtverschiebung parallel zur Trasse und der Abstand zur Linie bleibt
    exakt der einer einzeiligen Beschriftung. Ein reines `top` muss dagegen
    um die ganze Blockhoehe hoch, sonst waechst es in die Linien.
    """
    if versatz <= 0.0:
        return 0.0, 0.0
    if abs(ax) > 0.3:
        ddx = 0.0
        if abs(ay) > 0.3:
            ddx = versatz / 2 if ax > 0 else -versatz / 2
        return ddx, -versatz / 2
    if ay < -0.3:
        return 0.0, -versatz
    return 0.0, 0.0


def label_outward(direction: Pt) -> Pt:
    """Einheitsvektor, in den auto_label() die Beschriftung schiebt.

    Spiegelt dieselben Faelle wider und dient dazu, die Beschriftung bei
    gebuendelten Stationen zusaetzlich um die Buendelbreite nach aussen zu
    ruecken, statt sie an der Trassenmitte zu lassen.
    """
    ux, uy = _norm(direction) if direction != (0.0, 0.0) else (1.0, 0.0)
    if abs(uy) < 0.35:
        return (0.0, 1.0)      # waagerechte Trasse -> Label unten
    if abs(ux) < 0.35:
        return (-1.0, 0.0)     # senkrechte Trasse  -> Label links
    nx, ny = -uy, ux
    if ny < 0:
        nx, ny = -nx, -ny      # Diagonalen: Beschriftung unterhalb
    return (nx, ny)


# ==========================================================================
# SVG
# ==========================================================================


def _line_ends(pline) -> Tuple[str, ...]:
    """Die Enden einer Linie, an denen wirklich etwas endet.

    Bei einem Zweig (TurnLine.branch_of) ist das nur das FREIE Ende -- am
    anderen laeuft er auf seine Stammlinie auf und faehrt dort weiter.
    """
    enden = (pline.stations[0], pline.stations[-1])
    if pline.branch_of is None:
        return enden
    return tuple(sid for sid in enden if sid != pline.branch_join)


def _corner_radius(corner: Optional[Corner]) -> Optional[float]:
    """Loest einen Knick in seinen KREISRADIUS auf (Gitter-Einheiten).

    Der Radius steht bereits seit dem Parsen fest (explizites
    Turn(radius=...) oder NetworkConfig-Default) -- er ist Geometrie, weil der
    Solver Platz dafuer reserviert hat. Hier kommen nur noch der Deckel aus
    den tatsaechlichen Beinlaengen und die Versatzkorrektur dazu.
    """
    if corner is None:
        return None
    base = corner.radius if corner.radius is not None else 0.0

    # Deckel aus der Mittellinie -- vor der Versatzkorrektur, damit alle
    # Spuren von derselben Basis ausgehen und konzentrisch bleiben. Der
    # Deckel begrenzt die Tangente (dort ist der Platz knapp), muss also in
    # einen Radius umgerechnet werden. Ein explizites Turn(radius=...) ist
    # eine Anweisung und wird NICHT gedeckelt: nur so laesst sich eine Kurve
    # bewusst so weit aufziehen, dass das gerade Stueck dazwischen
    # vollstaendig verschwindet.
    if corner.max_tangent is not None and not corner.forced:
        base = min(base, corner.max_tangent / _corner_tangent_factor(corner.delta))

    # Versatz einrechnen: bei einer Rechtskurve (delta > 0) liegt die Seite
    # mit positivem Versatz innen und bekommt den kleineren Radius, aussen
    # den groesseren -- so bleiben die Boegen konzentrisch. Auf den Radius
    # wirkt der Versatz direkt, ohne Winkelfaktor.
    if corner.offset:
        base -= (1 if corner.delta > 0 else -1) * corner.offset
    return max(base, 0.0)


def build_track_svg(
    layout: LayoutResult,
    *,
    stations: Mapping[str, Station],
    highlight_lines: Optional[Mapping[str, str]] = None,
    line_layout: Optional[LineLayout] = None,
    draw_corridors: bool = True,
    style: StyleConfig = CFG.style,
    label_override: Optional[Mapping[str, Tuple[float, float, str]]] = None,
    badge_opposite_corner: Iterable[str] = (),
    legend_at: Optional[Pt] = None,
    legend_lines: Optional[Mapping[str, "TurnLine"]] = None,
    label_offsets: Optional[Mapping[str, Pt]] = None,
    badge_offsets: Optional[Mapping[str, Pt]] = None,
    badge_above: Iterable[str] = (),
    badge_order: Optional[Mapping[str, Sequence[str]]] = None,
    crossing_over: Optional[Mapping[str, Sequence[str]]] = None,
    draw_over: Optional[Mapping[str, Sequence[str]]] = None,
) -> str:
    """Phase 5 (Darstellung): zeichnet ein fertig geloestes `LayoutResult`.

    Erzeugt selbst keine Geometrie mehr -- `solve_layout()` (Phase 2-4) muss
    vorher aufgerufen werden. Dadurch laesst sich ein Layout einmal loesen,
    inspizieren und mehrfach unterschiedlich rendern.

    Die Streckenzuege der hervorgehobenen Linien kommen aus Phase 4b. Ohne
    `line_layout` werden sie hier bequemlichkeitshalber aus `highlight_lines`
    erzeugt; wer die Buendelung selbst steuern will (z.B. gegen ALLE Linien
    statt nur die hervorgehobenen), ruft `build_line_layout()` vorher auf und
    reicht das Ergebnis durch.

    `draw_corridors` blendet die duennen grauen Mittellinien der Korridore
    als Hilfstrassen ein. Sie werden ueber die Linien gezeichnet, damit sie
    sichtbar bleiben und man den Versatz gegen die Trassenmitte pruefen
    kann.
    """
    labels = {} if label_override is None else dict(label_override)
    highlights = {} if highlight_lines is None else dict(highlight_lines)
    # Feinkorrekturen der Beschriftung: kommen aus der Netzdefinition, nicht
    # aus dem Stil -- sie gelten fuer genau diese Karte.
    opposite_corner_stations = frozenset(badge_opposite_corner)
    # Stationen, an denen die Signetreihe UEBER den Namen gehoert statt
    # darunter. Sinnvoll, wo mehrere Linien enden: die Reihe wird dann breiter
    # als der Name und stiesse unter ihm an die Nachbarbeschriftung.
    badge_above_stations = frozenset(badge_above)
    # Reihenfolge der Signete an einer Station. Ohne Eintrag stehen sie
    # alphabetisch; genannt wird sie, wo die Reihe den Spuren folgen soll.
    badge_reihenfolge = {} if badge_order is None else {
        sid: list(ids) for sid, ids in badge_order.items()
    }
    nudge_label = {} if label_offsets is None else dict(label_offsets)
    nudge_badge = {} if badge_offsets is None else dict(badge_offsets)
    if line_layout is None:
        line_layout = build_line_layout(
            layout, highlights.keys(), bundle_spacing=style.bundle_spacing
        )

    # Linie -> Linien, ueber denen sie an einer Kreuzung liegen soll. Notiert
    # wird sie mit Linien-IDs, gefragt wird nach FAMILIEN -- eine Familie
    # teilt sich eine Spur, ihre Linien koennen an einer Kreuzung nicht
    # verschieden liegen. Der Name der Familie ist dabei nicht immer der der
    # Linie (die S46 gehoert zur Familie "S4"), deshalb die Uebersetzung.
    ueber: Dict[str, Set[str]] = defaultdict(set)
    for lid, unter in (crossing_over or {}).items():
        fam = line_layout.families.get(lid, lid)
        ueber[fam].update(line_layout.families.get(u, u) for u in unter)

    # Bounding Box ueber alle Korridorpunkte
    all_points = [p for path in layout.tracks.corridor_paths.values() for p in path]
    if not all_points:
        raise GeometryError("Keine Korridore zum Rendern gefunden")

    minx = min(x for x, _ in all_points)
    miny = min(y for _, y in all_points)
    maxx = max(x for x, _ in all_points)
    maxy = max(y for _, y in all_points)

    def px(point: Pt) -> Pt:
        return (
            (point[0] - minx) * style.grid + style.margin,
            (point[1] - miny) * style.grid + style.margin,
        )

    width = (maxx - minx) * style.grid + 2 * style.margin
    height = (maxy - miny) * style.grid + 2 * style.margin

    # Stationsrichtung fuer automatische Labels
    station_dirs: Dict[str, Pt] = {}
    for pline in layout.network.parsed.values():
        for seg in pline.segments:
            corridor = layout.tracks.corridor_paths[_pair_key(seg.a, seg.b)]
            if seg.a not in station_dirs:
                station_dirs[seg.a] = _norm(
                    (corridor[1][0] - corridor[0][0], corridor[1][1] - corridor[0][1])
                )
            if seg.b not in station_dirs:
                station_dirs[seg.b] = _norm(
                    (corridor[-1][0] - corridor[-2][0], corridor[-1][1] - corridor[-2][1])
                )

    # Die Zeichenflaeche ergibt sich nicht aus den Trassen allein --
    # Beschriftungen, Signets und Stationsmarken ragen darueber hinaus und
    # wuerden sonst abgeschnitten (z.B. die Endpunkte der S5 im Osten).
    # Jedes gezeichnete Element meldet deshalb seine Ausdehnung an `merke`;
    # der Kopf des SVG wird ganz am Ende daraus gesetzt.
    inhalt: List[Tuple[float, float, float, float]] = []

    def merke(x0: float, y0: float, x1: float, y1: float) -> None:
        inhalt.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    def merke_punkte(punkte: Iterable[Pt], rand: float = 0.0) -> None:
        pp = list(punkte)
        if not pp:
            return
        merke(min(q[0] for q in pp) - rand, min(q[1] for q in pp) - rand,
              max(q[0] for q in pp) + rand, max(q[1] for q in pp) + rand)

    svg: List[str] = [
        "",   # Platzhalter fuer den <svg>-Kopf, siehe unten
        "",   # Platzhalter fuer den weissen Grund
    ]

    def px_radii(corners: Sequence[Optional[Corner]]) -> List[Optional[float]]:
        """Knicke in Pixel-Rundungsradien aufloesen: Deckel und Versatz
        anwenden (Gitter-Einheiten), dann mit style.grid skalieren."""
        radii = [_corner_radius(c) for c in corners]
        return [None if r is None else r * style.grid for r in radii]

    # Hervorgehobene Linien: fertiger Streckenzug aus Phase 4b (inkl.
    # Buendel-Versatz) in Linienfarbe, oben drauf.
    # Innerhalb einer Familie die Stammlinie ZULETZT zeichnen, damit auf
    # gemeinsamen Abschnitten ihre Farbe sichtbar bleibt und nicht die der
    # darueber liegenden Variante. `draw_over` hebt einzelne Linien darueber
    # hinaus: jede dort genannte Linie liegt ueber allen, die sie aufzaehlt
    # -- auch ueber ihrer Stammlinie, deren Farbe sie auf einer gemeinsamen
    # Spur dann verdeckt. Linien, die es in dieser Karte nicht gibt, zaehlen
    # nicht; so kann eine spaetere Stufe den Eintrag erben, auch wenn eine
    # der genannten Linien dort wegfaellt.
    ebene: Dict[str, int] = {
        lid: 1 if line_layout.families.get(lid, lid) == lid else 0
        for lid in highlights
    }
    for _ in range(len(ebene) + 1):
        angehoben = False
        for lid, unter in (draw_over or {}).items():
            if lid not in ebene:
                continue
            ziel = max((ebene[u] + 1 for u in unter if u in ebene),
                       default=ebene[lid])
            if ziel > ebene[lid]:
                ebene[lid] = ziel
                angehoben = True
        if not angehoben:
            break
    else:
        raise ValueError("draw_over ist zirkulaer: "
                         + ", ".join(sorted(draw_over or {})))

    def stapel(lid: str) -> Tuple[int, str]:
        return (ebene[lid], lid)

    def draw_order(item: Tuple[str, str]) -> Tuple[int, str]:
        return stapel(item[0])

    path_d_by_line: Dict[str, str] = {}
    flat_paths: Dict[str, List[Pt]] = {}
    for line_id, color in sorted(highlights.items(), key=draw_order):
        line = line_layout.paths[line_id]
        ecken = px_radii(line.corners)
        punkte_px = [px(p) for p in line.points]
        path_d = rounded_path_d(punkte_px, ecken, line.beziers)
        path_d_by_line[line_id] = path_d
        flat_paths[line_id] = rounded_points(
            punkte_px, ecken, schritte=24, beziers=line.beziers
        )
        # SEGMENTWEISE anmelden, nicht als ein Kasten um die ganze Linie.
        # Fuer den Rahmen kommt dasselbe heraus -- die Vereinigung ist
        # dieselbe -- aber die Legende sucht sich ihren Platz spaeter in
        # diesen Kaesten, und ein einziger Kasten um die ganze S1 laege quer
        # ueber die halbe Karte und deckte jede freie Flaeche zu.
        halb = style.highlight_line_width / 2
        for a_, b_ in zip(flat_paths[line_id], flat_paths[line_id][1:]):
            merke_punkte((a_, b_), halb)
        svg.append(
            f'<path d="{path_d}" fill="none" stroke="{color}" '
            f'stroke-width="{style.highlight_line_width}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
        )

    # Hubs: Groesse und Lage der Pillen. Sie ergeben sich aus den
    # Stationspunkten aller dort verkehrenden Linien, aufgeweitet um den
    # Eckradius -- so deckt die Pille die Linien vollstaendig ab und ihre
    # Enden werden zu Halbkreisen. Gerechnet wird das hier oben, GEZEICHNET
    # erst weiter unten ueber den Linien: die Kreuzungserkennung braucht die
    # fertige Pillenform schon vorher, um zu wissen, was die Pille ohnehin
    # verdeckt.
    pills: Dict[str, Tuple[int, float, float, float, float]] = {}
    for station_id, station in stations.items():
        if station.kind != "hub":
            continue
        # Ein Bahnhof kann auf zwei Trassen liegen, die sich nicht treffen
        # (Potsdamer Platz: alter Tunnel und City-S-Bahn laufen gerade
        # aneinander vorbei). Dann hat er zwei Knoten, und der zweite meldet
        # sich ueber `pill_with` bei diesem an -- eine Pille ueber beide.
        knoten = [station_id] + [
            sid for sid, st in stations.items() if st.pill_with == station_id
        ]
        # Beide Lagen jeder Linie: wo sie ankommt und -- falls sie hier die
        # Spur wechselt -- wo sie wieder abfaehrt.
        punkte = [
            px(lage)
            for line_id in highlights
            for quelle in (line_layout.paths[line_id].stations,
                           line_layout.paths[line_id].lane_changes)
            for sid in knoten
            for lage in ([quelle[sid]] if sid in quelle else [])
        ]
        if not punkte:
            continue
        r = style.hub_pill_r
        # Ausrichtung: 0 oder 45 Grad, je nachdem was enger sitzt. Liegen die
        # Punkte auf einer Diagonalen, ist die gedrehte Box deutlich
        # schmaler; bei einer Buendelkreuzung gewinnt die achsparallele.
        # Ausrichtung nach den tatsaechlichen Gleisachsen, gewichtet mit der
        # Zahl der dort liegenden Linien.
        gewicht: Dict[int, int] = defaultdict(int)
        kurse: List[int] = []
        mitte = layout.measures.coords[station_id]
        for key, kpts in layout.tracks.corridor_paths.items():
            if station_id not in key:
                continue
            if hypot(kpts[0][0] - mitte[0], kpts[0][1] - mitte[1]) < 1e-6:
                kurs = _edge_bearing(kpts, at_start=True)
            else:
                kurs = _edge_bearing(kpts, at_start=False)
            gewicht[kurs % 90] += len(line_layout.slots.get(key, {}))
            kurse.append(kurs % 180)
        # Es gewinnt die Achse mit den MEISTEN Linien. Eine einzelne
        # kreuzende Linie soll die Pille nicht aufblaehen -- sie wird an
        # ihrem Kreuzungspunkt ohnehin mit abgedeckt.
        grad = max(sorted(gewicht), key=lambda ax: gewicht[ax]) if gewicht else 0

        # Kreuzen sich an einer Station zwei Achsen, kann eine einzige Pille
        # nicht beiden Buendeln folgen. Dann legt sie sich LAENGS der
        # Nebenachse (der kreuzenden Linie) und bleibt eine Spur dick -- die
        # Hauptachse wird dabei ueberquert. Sonst muesste sie sich zu einer
        # Raute aufblaehen, um alle Punkte zu umschliessen.
        laengs: Optional[int] = None
        if len(gewicht) > 1:
            neben = min(sorted(gewicht), key=lambda ax: gewicht[ax])
            grad = neben
            # im gedrehten System zeigt die Laengsrichtung entlang x' oder y'
            # Im um -grad gedrehten System liegt ein Kurs von `grad` auf der
            # y'-Achse und einer von grad+90 auf der x'-Achse.
            neben_kurs = next(k for k in kurse if k % 90 == neben)
            laengs = 1 if (neben_kurs - grad) % 180 == 0 else 0
        a = radians(grad)
        ca, sa = cos(a), sin(a)
        xs = [q[0] * ca + q[1] * sa for q in punkte]
        ys = [-q[0] * sa + q[1] * ca for q in punkte]

        # Streuung unterhalb einer Spurbreite kommt nicht von parallelen
        # Spuren, sondern daher, dass das Buendel als Ganzes aus der
        # Trassenmitte versetzt ist. Sie soll die Pille nicht verbreitern.
        spur = style.bundle_spacing * style.grid

        def spanne(werte: List[float]) -> Tuple[float, float]:
            lo, hi = min(werte), max(werte)
            if hi - lo < spur - 1e-6:
                # Nicht auf die Mitte zwischen den Werten legen, sondern auf
                # den aeussersten -- das ist die Lage des versetzten
                # Buendels. Sonst sitzt die Pille zwischen den Spuren und
                # trifft keine davon (Suedkreuz: S2-Familie auf +0.5).
                fest = max((lo, hi), key=abs)
                return fest, fest
            return lo, hi

        xlo, xhi = spanne(xs)
        ylo, yhi = spanne(ys)
        # An einer Achsenkreuzung bleibt die Pille quer zur Laengsrichtung
        # genau eine Spur dick. Ihre LAENGE misst sich dabei nicht an der
        # Projektion der Punkte auf die Pillenachse -- die faellt zu kurz aus
        # -- sondern an der Breite des ueberquerten Hauptbuendels, damit
        # wirklich alle dortigen Spuren ueberspannt werden.
        if laengs is not None:
            haupt = max(sorted(gewicht), key=lambda ax: gewicht[ax])
            hk = next(k for k in kurse if k % 90 == haupt)
            nx, ny = COMPASS[(hk + 90) % 360]
            quer = [q[0] * nx + q[1] * ny for q in punkte]
            # Die Pille laeuft schraeg zum ueberquerten Buendel. Um dessen
            # Breite senkrecht zu ueberbruecken, muss sie entsprechend
            # laenger sein -- bei 45 Grad um den Faktor 1/cos(45) = Wurzel 2.
            ux, uy = COMPASS[neben_kurs]
            schraeg = max(abs(ux * nx + uy * ny), 1e-6)
            halb = (max(quer) - min(quer)) / 2 / schraeg + r
            mx = (min(xs) + max(xs)) / 2
            my = (min(ys) + max(ys)) / 2
            if laengs == 0:
                xlo, xhi = mx - halb + r, mx + halb - r
                ylo = yhi = my
            else:
                xlo = xhi = mx
                ylo, yhi = my - halb + r, my + halb - r
        breite = xhi - xlo + 2 * r
        hoehe = yhi - ylo + 2 * r
        x0, y0 = xlo - r, ylo - r
        pills[station_id] = (grad, x0, y0, breite, hoehe)
        ar = radians(grad)
        merke_punkte([
            (ex * cos(ar) - ey * sin(ar), ex * sin(ar) + ey * cos(ar))
            for ex in (x0, x0 + breite) for ey in (y0, y0 + hoehe)
        ], style.hub_pill_stroke / 2)

    # Kreuzungen ausserhalb von Hubs: dort laufen zwei Linien ohne
    # Umsteigebeziehung uebereinander, was ohne Hinweis unuebersichtlich ist.
    # Die obenliegende bekommt deshalb genau im Kreuzungsbereich einen
    # weissen Rand -- ein kurzes, breiteres weisses Stueck, auf das ihre
    # eigene Farbe neu gezeichnet wird.
    def line_number(line_id: str) -> int:
        """Nummer der FAMILIE, nicht der einzelnen Linie.

        S25 und S26 fahren im Buendel der S2 und muessen sich deshalb genauso
        verhalten wie sie -- sonst gewaenne S25 (25) gegen S8 (8), waehrend
        S85 (85) gegen S2 (2) gewinnt, und dasselbe Buendel laege einmal oben
        und einmal unten."""
        stamm = line_layout.families.get(line_id, line_id)
        ziffern = "".join(c for c in stamm if c.isdigit())
        return int(ziffern) if ziffern else 0

    def vorfahrt(lid_a: str, lid_b: str) -> Optional[str]:
        """Von Hand gesetzte Vorfahrt aus `crossing_over`, sonst None.

        Gefragt wird nach FAMILIEN, wie ueberall in der Kreuzungslogik: wer
        die S8 unter die S6 legt, meint auch die S85, die in deren Buendel
        faehrt.
        """
        fam_a = line_layout.families.get(lid_a, lid_a)
        fam_b = line_layout.families.get(lid_b, lid_b)
        if fam_b in ueber.get(fam_a, ()):
            return lid_a
        if fam_a in ueber.get(fam_b, ()):
            return lid_b
        return None

    # Sperrbereich je Station: genau die Flaeche, die der Stationsmarker
    # ohnehin verdeckt. Bei einem Hub ist das die oben berechnete Pille --
    # ein abgerundetes, unter Umstaenden gedrehtes Rechteck, KEIN Kreis um
    # die Stationsmitte. Der Unterschied zaehlt an einer Buendelkreuzung wie
    # Schoeneberg: dort legt sich die Pille schmal und schraeg laengs der
    # kreuzenden S1 ueber den Ring, waehrend ein Kreis um ihre Mitte auch
    # daneben liegende, echte Kreuzungen schluckte -- die S6, die den Ring
    # ein Stueck weiter oestlich ueberquert, blieb so ohne weissen Rand.
    def im_marker(p: Pt) -> bool:
        for grad, x0, y0, breite, hoehe in pills.values():
            a = radians(grad)
            ca, sa = cos(a), sin(a)
            # in das (mit-)gedrehte System der Pille
            qx = p[0] * ca + p[1] * sa
            qy = -p[0] * sa + p[1] * ca
            r = style.hub_pill_r
            # Abstand zum abgerundeten Rechteck: auf den Kern zwischen den
            # Eckmittelpunkten klemmen, dann bleibt der Eckradius uebrig.
            kx = min(max(qx, x0 + r), x0 + breite - r)
            ky = min(max(qy, y0 + r), y0 + hoehe - r)
            if hypot(qx - kx, qy - ky) <= r:
                return True
        return any(hypot(p[0] - q[0], p[1] - q[1]) < sperre
                   for q, sperre in station_blocks)

    # Nicht-Hubs: der runde Stationspunkt. Er sitzt nicht zwangslaeufig auf
    # der Stationskoordinate -- bei mehreren Spuren liegt er dort, wo die
    # aeusserste Linie haelt -- deshalb wird bis zum aeussersten Punkt plus
    # Punktradius gesperrt.
    station_blocks: List[Tuple[Pt, float]] = []
    for sid, coord in layout.measures.coords.items():
        if sid in pills:
            continue
        mitte = px(coord)
        weit = max(
            (hypot(px(pt)[0] - mitte[0], px(pt)[1] - mitte[1])
             for lid in highlights
             for s2, pt in line_layout.paths[lid].stations.items() if s2 == sid),
            default=0.0,
        )
        station_blocks.append((mitte, weit + style.station_r))
    pfade_px = flat_paths

    # Ringzugehoerigkeit wird oertlich bestimmt, nicht ueber die Linien-ID:
    # auf dem Ring fahren auch Linien, die selbst nicht geschlossen sind
    # (S46/S47). Massgeblich ist, ob im selben Buendel eine geschlossene
    # Ringlinie parallel laeuft.
    ring_segments = [
        (a, b)
        for lid in highlights
        if layout.network.parsed[lid].closed
        for a, b in zip(pfade_px[lid], pfade_px[lid][1:])
    ]
    ring_nah = style.bundle_spacing * style.grid * 4

    def on_ring(u: Pt, p: Pt) -> bool:
        for a, b in ring_segments:
            dx_, dy_ = b[0] - a[0], b[1] - a[1]
            laenge = hypot(dx_, dy_)
            if laenge < 1e-9:
                continue
            if abs(u[0] * dy_ - u[1] * dx_) / laenge > 1e-6:
                continue                  # nicht parallel
            t_ = max(0.0, min(1.0, ((p[0] - a[0]) * dx_ + (p[1] - a[1]) * dy_) / laenge ** 2))
            if hypot(p[0] - a[0] - t_ * dx_, p[1] - a[1] - t_ * dy_) < ring_nah:
                return True
        return False

    def crossed_tracks(lid: str, p: Pt) -> int:
        """Wie viele fremde Spuren schneidet `lid` in der Umgebung von `p`?

        Gezaehlt werden Familien -- eine Familie teilt sich eine Spur. Nicht
        gezaehlt wird, was nur parallel danebenlaeuft: massgeblich ist, was
        die Linie tatsaechlich ueberquert. Sonst zaehlten an einer
        Einmuendung die Nachbarspuren des eigenen Korridors mit.
        """
        reichweite = style.highlight_line_width * 3
        eigene = line_layout.families.get(lid, lid)

        def nahe_segmente(punkte: Sequence[Pt]) -> List[Tuple[Pt, Pt]]:
            return [
                (a, b)
                for a, b in zip(punkte, punkte[1:])
                if min(hypot(a[0] - p[0], a[1] - p[1]),
                       hypot(b[0] - p[0], b[1] - p[1])) < reichweite * 2
            ]

        meine = nahe_segmente(flat_paths[lid])
        familien = set()
        for lid2 in highlights:
            fam = line_layout.families.get(lid2, lid2)
            if fam == eigene or fam in familien:
                continue
            for c1, c2 in nahe_segmente(flat_paths[lid2]):
                uc = (c2[0] - c1[0], c2[1] - c1[1])
                for m1, m2 in meine:
                    um = (m2[0] - m1[0], m2[1] - m1[1])
                    nen = um[0] * uc[1] - um[1] * uc[0]
                    if abs(nen) < 1e-9:
                        continue
                    tm = ((c1[0] - m1[0]) * uc[1] - (c1[1] - m1[1]) * uc[0]) / nen
                    tc = ((c1[0] - m1[0]) * um[1] - (c1[1] - m1[1]) * um[0]) / nen
                    if not (0.0 <= tm <= 1.0 and 0.0 <= tc <= 1.0):
                        continue
                    q = (m1[0] + tm * um[0], m1[1] + tm * um[1])
                    if hypot(q[0] - p[0], q[1] - p[1]) < reichweite:
                        familien.add(fam)
                        break
                if fam in familien:
                    break
        return len(familien)

    # (Kreuzungspunkt, obenliegende Linie, untenliegende Linie)
    crossings: List[Tuple[Pt, str, str]] = []
    ids = sorted(highlights)
    for i, lid_a in enumerate(ids):
        for lid_b in ids[i + 1:]:
            if (line_layout.families.get(lid_a, lid_a)
                    == line_layout.families.get(lid_b, lid_b)):
                # Gleiche Familie: gleiche Farbe, gleiche Spur -- ein Rand
                # zwischen ihnen wuerde nur die eigene Linie zerschneiden.
                continue
            for a1, a2 in zip(pfade_px[lid_a], pfade_px[lid_a][1:]):
                ua = (a2[0] - a1[0], a2[1] - a1[1])
                for b1, b2 in zip(pfade_px[lid_b], pfade_px[lid_b][1:]):
                    ub = (b2[0] - b1[0], b2[1] - b1[1])
                    nenner = ua[0] * ub[1] - ua[1] * ub[0]
                    if abs(nenner) < 1e-9:
                        continue          # parallel
                    t = ((b1[0] - a1[0]) * ub[1] - (b1[1] - a1[1]) * ub[0]) / nenner
                    s = ((b1[0] - a1[0]) * ua[1] - (b1[1] - a1[1]) * ua[0]) / nenner
                    if not (0.0 < t < 1.0 and 0.0 < s < 1.0):
                        continue
                    p = (a1[0] + t * ua[0], a1[1] + t * ua[1])
                    # An einer Station kreuzen die Linien nicht, sie treffen
                    # sich dort -- Pille bzw. Stationspunkt uebernehmen.
                    if im_marker(p):
                        continue
                    la, lb = hypot(*ua), hypot(*ub)
                    ea = (ua[0] / la, ua[1] / la)
                    eb = (ub[0] / lb, ub[1] / lb)
                    # Von Hand gesetzte Vorfahrt (`crossing_over`) geht
                    # allem voraus: sie gilt fuer dieses Linienpaar ueberall,
                    # wo es sich kreuzt.
                    # Von Hand gesetzte Vorfahrt geht allem voraus: sie
                    # gilt fuer dieses Linienpaar ueberall, wo es sich
                    # kreuzt, unabhaengig von Spurzahl und Nummer.
                    oben = vorfahrt(lid_a, lid_b)
                    if oben is None:
                        # Sonst liegt oben, wer MEHR fremde Spuren
                        # ueberquert -- also die einzelne Linie ueber der
                        # Gruppe. Das liest sich am klarsten: eine Linie,
                        # die drei Spuren kreuzt, gehoert sichtbar darueber.
                        quer_a = crossed_tracks(lid_a, p)
                        quer_b = crossed_tracks(lid_b, p)
                        if quer_a != quer_b:
                            oben = lid_a if quer_a > quer_b else lid_b
                        else:
                            # Gleich breit: der Ring wird ueberquert, sonst
                            # entscheidet die hoehere Familiennummer.
                            a_ring, b_ring = on_ring(ea, p), on_ring(eb, p)
                            if a_ring != b_ring:
                                oben = lid_b if a_ring else lid_a
                            elif line_number(lid_a) >= line_number(lid_b):
                                oben = lid_a
                            else:
                                oben = lid_b
                    unten = lid_b if oben == lid_a else lid_a
                    crossings.append((p, oben, unten))

    # Ummantelt wird ein Fenster um die Kreuzung. Seine Groesse kommt nicht
    # aus dem Schnittwinkel, sondern aus der tatsaechlichen Annaeherung: so
    # weit, wie die beiden Linien einander naeher als eine Strichbreite
    # kommen. Damit passt es auch dort, wo sich zwei Linien in einer Kurve
    # flach schneiden. Gezeichnet wird der ECHTE Verlauf, ausgeschnitten auf
    # dieses Fenster -- die weissen Raender folgen den Boegen also exakt.
    def punkt_segment(q: Pt, a: Pt, b: Pt) -> float:
        """Abstand des Punktes q zur STRECKE a-b (nicht zur Geraden)."""
        dx_, dy_ = b[0] - a[0], b[1] - a[1]
        quad = dx_ * dx_ + dy_ * dy_
        if quad < 1e-12:
            return hypot(q[0] - a[0], q[1] - a[1])
        t_ = max(0.0, min(1.0, ((q[0] - a[0]) * dx_ + (q[1] - a[1]) * dy_) / quad))
        return hypot(q[0] - a[0] - t_ * dx_, q[1] - a[1] - t_ * dy_)

    def overlap_window(oben: str, unten: str, p: Pt) -> float:
        schwelle = style.highlight_line_width + style.crossing_casing
        grenze = style.highlight_line_width * 6
        # Nur die Segmente der unteren Linie, die ueberhaupt in Frage kommen.
        # Gemessen wird zur STRECKE, nicht zu ihren Endpunkten: eine lange
        # Gerade -- etwa der Ring zwischen zwei weit entfernten Stationen --
        # hat beide Enden weit weg und faellt sonst raus, obwohl sie mitten
        # durch die Kreuzung laeuft.
        nah = [
            (a, b)
            for a, b in zip(flat_paths[unten], flat_paths[unten][1:])
            if punkt_segment(p, a, b) < grenze + schwelle
        ]

        def abstand(q: Pt) -> float:
            return min((punkt_segment(q, a, b) for a, b in nah), default=1e9)

        # Den Verlauf der oberen Linie in 1-px-Schritten abtasten -- nur an
        # den Stuetzpunkten zu messen reicht nicht, die Kreuzung liegt in der
        # Regel zwischen zweien.
        weit = 0.0
        for a, b in zip(flat_paths[oben], flat_paths[oben][1:]):
            laenge = hypot(b[0] - a[0], b[1] - a[1])
            if min(hypot(a[0] - p[0], a[1] - p[1]),
                   hypot(b[0] - p[0], b[1] - p[1])) > grenze + laenge:
                continue
            for k in range(max(1, int(laenge)) + 1):
                f = k / max(1, int(laenge))
                q = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
                r_ = hypot(q[0] - p[0], q[1] - p[1])
                if r_ <= grenze and abstand(q) < schwelle:
                    weit = max(weit, r_)
        return min(weit + style.crossing_casing + 1.0, grenze)

    windows: Dict[str, List[Tuple[Pt, float]]] = defaultdict(list)
    for p, oben, unten in crossings:
        windows[oben].append((p, overlap_window(oben, unten, p)))

    def fenster_stuecke(lid: str, mitte: Pt, radius: float) -> List[List[Pt]]:
        """Die Teilstuecke des Streckenzugs von `lid` innerhalb des Fensters.

        Frueher lag hier ein `clipPath` mit Kreisen, auf den der ganze
        Streckenzug beschnitten wurde. Das war knapp und elegant, aber nicht
        jeder SVG-Betrachter setzt Beschneidungen gleich um -- in manchen
        fehlte der weisse Rand auf einer Seite. Deshalb wird das Stueck jetzt
        selbst ausgerechnet und als eigener kurzer Pfad ausgegeben: reine
        Striche, nichts, was ein Betrachter anders auslegen koennte.

        Gerechnet wird auf dem geglaetteten Streckenzug -- die Boegen sind
        dort in Sehnen von rund einem Pixel zerlegt, die Abweichung vom
        echten Bogen liegt unter einem hundertstel Pixel.
        """
        def drin(q: Pt) -> bool:
            return hypot(q[0] - mitte[0], q[1] - mitte[1]) <= radius

        def schnitt(a: Pt, b: Pt) -> List[float]:
            """Parameter, bei denen die Strecke a-b den Fensterrand trifft."""
            dx_, dy_ = b[0] - a[0], b[1] - a[1]
            fx, fy = a[0] - mitte[0], a[1] - mitte[1]
            aa = dx_ * dx_ + dy_ * dy_
            if aa < 1e-12:
                return []
            bb = 2 * (fx * dx_ + fy * dy_)
            cc = fx * fx + fy * fy - radius * radius
            disk = bb * bb - 4 * aa * cc
            if disk <= 0:
                return []
            w = sqrt(disk)
            return sorted(t for t in ((-bb - w) / (2 * aa), (-bb + w) / (2 * aa))
                          if 0.0 < t < 1.0)

        stuecke: List[List[Pt]] = []
        lauf: List[Pt] = []
        for a, b in zip(flat_paths[lid], flat_paths[lid][1:]):
            ts = schnitt(a, b)
            grenzen = [(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                       for t in ts]
            if drin(a):
                if not lauf:
                    lauf = [a]
                else:
                    lauf.append(a)
            for g in grenzen:
                if lauf:
                    lauf.append(g)
                    stuecke.append(lauf)
                    lauf = []
                else:
                    lauf = [g]
        if lauf:
            lauf.append(flat_paths[lid][-1])
            stuecke.append(lauf)
        return [st for st in stuecke if len(st) > 1]

    if windows:
        # Reihenfolge: wer anderswo selbst UNTEN liegt, kommt zuerst dran.
        # Sonst malt seine eigene Farbe -- die im Fenster ueber den ganzen
        # Familienstrang neu gezogen wird -- den weissen Rand dessen wieder
        # zu, der ueber ihm liegt. Bei Westhafen ist das die S15: sie
        # ueberquert dort den Ring, wird ihrerseits aber von der S6
        # ueberfahren.
        ueberfahren: Dict[str, int] = defaultdict(int)
        for _, _, unter in crossings:
            ueberfahren[line_layout.families.get(unter, unter)] += 1
        reihenfolge = sorted(
            windows,
            key=lambda lid: (
                -ueberfahren[line_layout.families.get(lid, lid)], lid
            ),
        )
        # Je Fenster erst der weisse Rand, dann die Farbe darauf.
        for lid in reihenfolge:
            for schicht in ("weiss", "farbe"):
                familie = line_layout.families.get(lid, lid)
                # Nicht nur die kreuzende Linie selbst, sondern ihre ganze
                # Familie: die Schwesterlinie laeuft unmittelbar daneben und
                # wuerde sonst vom eigenen weissen Rand mit abgedeckt. In
                # derselben Reihenfolge wie oben, sonst laege im Fenster eine
                # andere Linie oben als auf der Strecke davor und danach.
                linien = ([lid] if schicht == "weiss" else
                          sorted((a for a in highlights
                                  if line_layout.families.get(a, a) == familie),
                                 key=stapel))
                for andere in linien:
                    farbe = ("white" if schicht == "weiss"
                             else highlights[andere])
                    breite = (style.highlight_line_width + 2 * style.crossing_casing
                              if schicht == "weiss" else style.highlight_line_width)
                    # Der weisse Rand ist breiter als die Linie und reicht
                    # deshalb ueber den Fensterrand hinaus -- an einer Gabel
                    # trifft er dort die Schwesterlinie, deren eigener
                    # Verlauf schon ausserhalb liegt (Westkreuz: der Rand
                    # der S7 schnitt in die S75). Fuer die Schwestern wird
                    # das Fenster deshalb um die halbe Randbreite groesser
                    # genommen; sie werden dann ueber ihn gelegt.
                    ueberstand = (0.0 if andere == lid else
                                  style.highlight_line_width / 2
                                  + style.crossing_casing)
                    for pkt, radius in windows[lid]:
                        for stueck in fenster_stuecke(andere, pkt,
                                                      radius + ueberstand):
                            # Stumpfe Enden: das Teilstueck hoert genau am
                            # Fensterrand auf, wie es die Beschneidung vorher
                            # auch getan hat. Runde Enden wuerden es um einen
                            # halben Strich verlaengern.
                            svg.append(
                                f'<path d="{polyline_path_d(stueck)}" fill="none" '
                                f'stroke="{farbe}" stroke-width="{breite}" '
                                f'stroke-linecap="butt" stroke-linejoin="round"/>'
                            )

    # Hilfstrassen: die duennen grauen Mittellinien der Korridore, NACH den
    # Linien gezeichnet, damit sie von den breiten Linien nicht verdeckt
    # werden.
    if draw_corridors:
        for pair_key, points in layout.tracks.corridor_paths.items():
            corners = layout.tracks.corridor_corners[pair_key]
            path_d = rounded_path_d([px(p) for p in points], px_radii(corners))
            merke_punkte([px(p) for p in points], style.line_width / 2)
            svg.append(
                f'<path d="{path_d}" fill="none" stroke="{style.track_color}" '
                f'stroke-width="{style.line_width}" stroke-linecap="round" '
                f'stroke-linejoin="round"/>'
            )

    # Stationen: ein weisser Punkt je Linie, an DEREN Lage -- auf einem
    # Buendel entstehen dadurch parallele Punktreihen statt eines einzelnen
    # Punktes auf der (womoeglich leeren) Trassenmitte. Hubs bekommen
    # stattdessen ihre Pille und deshalb hier gar keinen Punkt.
    def blass(station_id: str) -> str:
        """fill-opacity fuer geplante Stationen, sonst nichts."""
        if not stations[station_id].planned:
            return ""
        return f' fill-opacity="{style.planned_opacity:g}"'

    def blass_g(station_id: str) -> str:
        """opacity fuer geplante Stationen -- fuer Marker aus mehreren
        Elementen, bei denen auch Rand und weisse Fuellung mit abblenden
        muessen (Hub-Pille, Endpunktring)."""
        if not stations[station_id].planned:
            return ""
        return f' opacity="{style.planned_opacity:g}"'

    gesetzt: set[Tuple[int, int]] = set()
    for line_id in highlights:
        for station_id, point in line_layout.paths[line_id].stations.items():
            if stations[station_id].kind == "hub":
                continue          # dort markiert die Pille die Station
            if stations[station_id].hidden:
                continue          # reiner Korridor-Wegpunkt, kein Punkt
            x, y = px(point)
            key = (round(x * 10), round(y * 10))
            if key in gesetzt:
                continue          # gleiche Familie, gleiche Spur
            gesetzt.add(key)
            merke(x - style.station_r, y - style.station_r,
                  x + style.station_r, y + style.station_r)
            svg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.station_r}" '
                f'fill="white"{blass(station_id)}/>'
            )

    # Stationen, die von keiner gezeichneten Linie beruehrt werden, bekommen
    # ihren Punkt weiterhin auf der Trassenmitte.
    beruehrt = {
        station_id
        for line_id in highlights
        for station_id in line_layout.paths[line_id].stations
    }
    for station_id, coord in layout.measures.coords.items():
        if station_id in beruehrt or stations[station_id].hidden:
            continue
        x, y = px(coord)
        svg.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.station_r}" '
            f'fill="white"{blass(station_id)}/>'
        )

    # Hubs zeichnen: die oben berechneten Pillen, jetzt ueber die Linien.
    for station_id, (grad, x0, y0, breite, hoehe) in pills.items():
        r = style.hub_pill_r
        dreh = f' transform="rotate({grad})"' if grad else ""
        svg.append(
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{breite:.1f}" '
            f'height="{hoehe:.1f}" rx="{r}" ry="{r}" fill="white" '
            # Die Pille bleibt deckend, auch wenn die Station geplant ist:
            # sie ist das Umsteigesymbol der Karte, kein Stationspunkt. Blass
            # gezeichnet liest sie sich nicht mehr als Hub, sondern wie ein
            # Fehler. Dass die Station geplant ist, sagen die Klammern im
            # Namen und die blassen Linienpunkte darauf.
            f'stroke="#000000" stroke-width="{style.hub_pill_stroke}"{dreh}/>'
        )

    # darauf ein farbiger Punkt -- aber nur fuer Linien, die an diesem Hub
    # ENDEN. Durchfahrende Linien liegen unter der weissen Pille und
    # bekommen keinen Punkt.
    def direction_at(line_id: str, punkt: Pt) -> Pt:
        """Laufrichtung der Linie an der Stelle `punkt` (Pixel)."""
        q = [px(p) for p in line_layout.paths[line_id].points]
        bestes, beste_dist = (1.0, 0.0), None
        for a, b in zip(q, q[1:]):
            dx_, dy_ = b[0] - a[0], b[1] - a[1]
            laenge = hypot(dx_, dy_)
            if laenge < 1e-9:
                continue
            t_ = max(0.0, min(1.0, ((punkt[0] - a[0]) * dx_ + (punkt[1] - a[1]) * dy_) / laenge ** 2))
            d_ = hypot(punkt[0] - a[0] - t_ * dx_, punkt[1] - a[1] - t_ * dy_)
            if beste_dist is None or d_ < beste_dist:
                bestes, beste_dist = (dx_ / laenge, dy_ / laenge), d_
        return bestes

    def first_crossing(line_id: str, station_id: str, p0: Pt, u: Pt) -> Optional[Pt]:
        """Schnittpunkt mit der ZUERST gekreuzten Linie im Hub.

        `u` zeigt von aussen in den Hub hinein. Gesucht ist damit der
        Schnittpunkt mit dem kleinsten Parameter t entlang u -- also der,
        auf den die endende Linie zuerst trifft.
        """
        bestes_t: Optional[float] = None
        for lid2 in highlights:
            if lid2 == line_id:
                continue
            q2 = line_layout.paths[lid2].stations.get(station_id)
            if q2 is None:
                continue
            qq = px(q2)
            d = direction_at(lid2, qq)
            kreuz = u[0] * d[1] - u[1] * d[0]
            if abs(kreuz) < 1e-6:
                continue                  # parallel, keine Kreuzung
            t_ = ((qq[0] - p0[0]) * d[1] - (qq[1] - p0[1]) * d[0]) / kreuz
            if t_ > 1e-6:
                # Die Kreuzung laege HINTER dem Linienende -- dort ist gar
                # keine Linie mehr, auf die der Punkt gesetzt werden koennte.
                continue
            if bestes_t is None or t_ < bestes_t:
                bestes_t = t_
        if bestes_t is None:
            return None
        return (p0[0] + bestes_t * u[0], p0[1] + bestes_t * u[1])

    for line_id, color in highlights.items():
        pline = layout.network.parsed[line_id]
        if pline.closed:
            continue                      # Ringlinie hat keine Endstation
        endet_hier = set(_line_ends(pline))
        pfad = line_layout.paths[line_id].points
        for station_id, point in line_layout.paths[line_id].stations.items():
            if stations[station_id].kind != "hub" or station_id not in endet_hier:
                continue
            x, y = px(point)
            # Auf die zuerst gekreuzte Linie setzen: der Punkt markiert, wo
            # die endende Linie im Hub das querende Buendel erreicht -- also
            # an dessen zugewandtem Rand, nicht in der Pillenmitte. `u` zeigt
            # von aussen in den Hub, bei einer Startstation entsprechend
            # entgegen der Fahrtrichtung.
            if station_id == pline.stations[0]:
                p0_, p1_ = px(pfad[0]), px(pfad[1])
            else:
                p0_, p1_ = px(pfad[-1]), px(pfad[-2])
            u_ = _norm((p0_[0] - p1_[0], p0_[1] - p1_[1]))
            treffer = first_crossing(line_id, station_id, (x, y), u_)
            if treffer is not None:
                svg.append(
                    f'<circle cx="{treffer[0]:.1f}" cy="{treffer[1]:.1f}" '
                    f'r="{style.hub_dot_r}" fill="{color}"'
                    f'{blass_g(station_id)}/>'
                )
                continue
            # Kein querendes Buendel: in die Pille ruecken, quer zu ihrer
            # Laengsrichtung auf die Mittellinie. Sitzt das Buendel versetzt
            # zur Trasse, laege der Punkt sonst am Pillenrand.
            if station_id in pills:
                grad_p, px0, py0, pw, ph = pills[station_id]
                ap_ = radians(grad_p)
                cap, sap = cos(ap_), sin(ap_)
                rx_, ry_ = x * cap + y * sap, -x * sap + y * cap
                if pw <= ph:
                    rx_ = px0 + pw / 2
                else:
                    ry_ = py0 + ph / 2
                x, y = rx_ * cap - ry_ * sap, rx_ * sap + ry_ * cap
            svg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.hub_dot_r}" '
                f'fill="{color}"{blass_g(station_id)}/>'
            )

    # Endstationen hervorgehobener Linien: schwarzer Ring mit Farbpunkt, an
    # der tatsaechlichen (ggf. gebuendelt versetzten) Position der Linie --
    # nicht an der reinen Stationskoordinate.
    # Geschlossene Ringlinien haben keine Endstation.
    # Ist die Endstation zugleich ein Hub, hat sie bereits ihre Pille und
    # bekommt keinen zusaetzlichen Ring.
    for line_id, color in highlights.items():
        pline = layout.network.parsed[line_id]
        if pline.closed:
            continue
        line_stations = line_layout.paths[line_id].stations
        for station_id in _line_ends(pline):
            if stations[station_id].kind == "hub":
                continue
            point = line_stations.get(station_id)
            if point is None:
                continue
            x, y = px(point)
            _r_end = style.terminus_ring_r + style.terminus_ring_stroke / 2
            merke(x - _r_end, y - _r_end, x + _r_end, y + _r_end)
            # Drei Kreise uebereinander: schwarzer Ring, weisser Grund,
            # Farbpunkt. Der weisse Grund ist nicht ueberfluessig -- bei einer
            # geplanten Station wird NUR der Farbpunkt durchscheinend, und er
            # soll dabei auf Weiss aufhellen und nicht die Linie darunter
            # durchlassen. Ring und Grund bleiben deshalb immer deckend.
            svg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.terminus_ring_r}" '
                f'fill="white" stroke="#000000" '
                f'stroke-width="{style.terminus_ring_stroke}"/>'
            )
            svg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.terminus_dot_r}" '
                f'fill="white"/>'
            )
            svg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.terminus_dot_r}" '
                f'fill="{color}"{blass(station_id)}/>'
            )

    # Labels
    # Bei gebuendelten Stationen liegt die Beschriftung nicht an der
    # Trassenmitte, sondern am aeusseren Rand des Buendels -- sonst
    # ueberdeckt sie die aeusseren Spuren.
    station_points: Dict[str, List[Pt]] = defaultdict(list)
    for line_id in highlights:
        for sid, pt in line_layout.paths[line_id].stations.items():
            station_points[sid].append(px(pt))

    # Waagerechte Trassen: Beschriftung abwechselnd unter und ueber dem
    # Track, sonst draengen sich die Namen auf einer Seite. Gruppiert nach
    # der Hoehe der Trasse, innerhalb einer Gruppe nach x sortiert.
    alternating: Dict[str, str] = {}
    reihen: Dict[float, List[Tuple[float, str]]] = defaultdict(list)
    for sid, koord in layout.measures.coords.items():
        ux, uy = _norm(station_dirs.get(sid, (1.0, 0.0)))
        if abs(uy) >= 0.35:
            continue                       # nicht waagerecht
        reihen[round(koord[1], 6)].append((koord[0], sid))
    for _, reihe in sorted(reihen.items()):
        # Bewusst ALLE Stationen der Reihe durchzaehlen, auch die mit
        # eigenem label_pos. Wuerde man die ueberspringen, verschoebe jede
        # festgesetzte Station die Parität aller folgenden -- ein einzelnes
        # label_pos wuerde dann den halben Rest der Strecke mitkippen.
        for i, (_, sid) in enumerate(sorted(reihe)):
            alternating[sid] = "top" if i % 2 == 0 else "bottom"

    # Senkrechte Trassen: Beschriftung links, ausser sie liefe dort ueber
    # eine Linie -- dann nach rechts ausweichen.
    # Welche Stationen tragen ein Linien-Tag? Muss vor der Beschriftung
    # feststehen: das Tag zaehlt bei der Positionierung wie eine zusaetzliche
    # Zeile, ist aber etwas hoeher als eine Textzeile.
    # Ein Zweig (TurnLine.branch_of) traegt das Signet SEINER STAMMLINIE --
    # eingetragen wird deshalb deren ID, nicht die eigene. Faellt es mit dem
    # Signet der Stammlinie auf dieselbe Station, bleibt es bei einem.
    terminates_at: Dict[str, List[str]] = defaultdict(list)
    for line_id in sorted(highlights):
        pline = layout.network.parsed[line_id]
        if pline.closed:
            continue                      # Ringlinie hat keinen Endpunkt
        zeigt = pline.branch_of or line_id
        for sid in _line_ends(pline):
            if zeigt not in terminates_at[sid]:
                terminates_at[sid].append(zeigt)

    def gross_markiert(station_id: str) -> bool:
        """Traegt die Station Pille oder Endstationsring statt nur Punkt?"""
        return stations[station_id].kind == "hub" or station_id in terminates_at

    def side_height(punkte: Sequence[Pt], ax_: float, ay_: float, y_: float) -> float:
        """Hoehe einer seitlichen Beschriftung (left/right).

        Liegt die Trasse schraeg, ist die Spur nicht nur waagerecht, sondern
        genauso weit SENKRECHT aus der Stationskoordinate versetzt -- das
        Symbol sitzt ja auf der Spur. Bisher folgte die Beschriftung nur in
        x und stand dadurch zu tief bzw. zu hoch (Koenigs Wusterhausen,
        Potsdam Hbf).

        Genommen wird die Mitte der Spurpunkte: ein gleichmaessig versetztes
        Buendel zieht die Beschriftung mit, ein zur Trassenmitte
        symmetrisches laesst sie stehen.
        """
        if abs(ax_) <= 0.3 or abs(ay_) > 0.3 or not punkte:
            return y_
        return sum(q[1] for q in punkte) / len(punkte)

    def marker_clearance(station_id: str, ax_: float, ay_: float) -> float:
        """Zusatzabstand bei seitlicher Beschriftung an grossen Symbolen.

        Hubs tragen eine Pille, Endstationen einen Ring -- beide reichen
        weiter ueber die Trasse hinaus als ein Stationspunkt. Nur bei
        `left`/`right`, wo die Beschriftung direkt daneben sitzt.
        """
        if abs(ax_) <= 0.3 or abs(ay_) > 0.3:
            return 0.0
        return style.label_marker_clearance if gross_markiert(station_id) else 0.0

    def badge_extra_height(station_id: str, ax_: float, ay_: float) -> float:
        """Zusatzhoehe des Linien-Tags unter dem Textblock.

        Nur bei `left` und `right`. Dort sitzt der Textblock mittig zur
        Station, das Tag gehoert also in die Blockhoehe hinein. Bei `top`,
        `bottom` und den vier Schraeglagen bleibt es aussen vor -- der
        Stationsname soll auf seiner gewohnten Hoehe neben dem Punkt stehen
        und das Tag einfach darunter haengen.

        Gezaehlt wird es dabei wie eine gewoehnliche Textzeile, NICHT mit
        seiner groesseren tatsaechlichen Hoehe: so bleibt der weisse
        Zwischenraum zwischen Name und Tag auf Hoehe der Station.
        """
        if station_id not in terminates_at:
            return 0.0
        if abs(ax_) <= 0.3 or abs(ay_) > 0.3:
            return 0.0
        return style.label_font * 1.15

    segments: List[Tuple[Pt, Pt]] = []
    for line_id in highlights:
        q = [px(p) for p in line_layout.paths[line_id].points]
        segments.extend(zip(q, q[1:]))
    half_line = style.highlight_line_width / 2

    def label_box(
        station_id: str, pos: str, extra_dx: float = 0.0
    ) -> Tuple[float, float, float, float]:
        """Umriss des Beschriftungsblocks, genau wie er gezeichnet wird."""
        bx, by = px(layout.measures.coords[station_id])
        ax_, ay_ = LABEL_DIRECTION[pos]
        weit_ = max(
            ((q[0] - bx) * ax_ + (q[1] - by) * ay_
             for q in station_points.get(station_id, ())),
            default=0.0,
        )
        bx += ax_ * (weit_ + marker_clearance(station_id, ax_, ay_))
        by = side_height(station_points.get(station_id, ()), ax_, ay_, by + ay_ * weit_)
        dx_, dy_, anker_ = label_offset(pos, style.label_clearance)
        zeilen = stations[station_id].label.split("\n")
        versatz_ = (len(zeilen) - 1) * style.label_font * 1.15
        ddx_, ddy_ = multiline_offset(
            ax_, ay_, versatz_ + badge_extra_height(station_id, ax_, ay_)
        )
        breite = label_width(stations[station_id].label, style.label_font, style.label_suffix_smaller)
        lx, ly = bx + dx_ + ddx_ + extra_dx, by + dy_ + ddy_
        if anker_ == "middle":
            x0 = lx - breite / 2
        elif anker_ == "end":
            x0 = lx - breite
        else:
            x0 = lx
        return (
            x0,
            ly - 0.75 * style.label_font,
            x0 + breite,
            ly + versatz_ + 0.25 * style.label_font,
        )

    sideways: Dict[str, str] = {}
    for sid in layout.measures.coords:
        if stations[sid].label_pos is not None or sid in alternating:
            continue
        ux_, _uy = _norm(station_dirs.get(sid, (1.0, 0.0)))
        if abs(ux_) >= 0.35:
            continue                       # nicht senkrecht
        kasten = label_box(sid, "left")
        if any(_segment_hits_box(a, b, kasten, half_line) for a, b in segments):
            sideways[sid] = "right"

    def tinte_x(mitte: Pt, sx: float, y_oben: float,
                y_unten: float) -> Optional[float]:
        """Wie weit reicht die Zeichnung um `mitte` in x-Richtung `sx` --
        gemessen NUR im Hoehenband zwischen y_oben und y_unten.

        Der Unterschied zu "wie weit liegen die Stationspunkte auseinander"
        ist bei schraegen Trassen entscheidend: eine 45-Grad-Linie laeuft
        vom Bahnhof aus seitlich weg, steht auf Hoehe der Beschriftung also
        schon gar nicht mehr im Weg. Wer nur die Stationspunkte misst,
        schiebt die Beschriftung um genau diesen Betrag zu weit hinaus.
        """
        reichweite = 90.0
        halb = style.highlight_line_width / 2
        weit: Optional[float] = None

        def band(a: Pt, b: Pt, halb: float = halb) -> None:
            nonlocal weit
            ay_, by_ = a[1] - halb, b[1] + halb
            if max(a[1], b[1]) + halb < y_oben or min(a[1], b[1]) - halb > y_unten:
                return
            dy_ = b[1] - a[1]
            ts = [0.0, 1.0]
            if abs(dy_) > 1e-9:
                ts += [(y_oben - a[1]) / dy_, (y_unten - a[1]) / dy_]
            for t in ts:
                if -1e-9 <= t <= 1.0 + 1e-9:
                    qy = a[1] + t * dy_
                    if y_oben - halb <= qy <= y_unten + halb:
                        qx = a[0] + t * (b[0] - a[0])
                        wert = (qx - mitte[0]) * sx + halb
                        weit = wert if weit is None else max(weit, wert)

        for lid in highlights:
            pfad = flat_paths[lid]
            for a, b in zip(pfad, pfad[1:]):
                if min(hypot(a[0] - mitte[0], a[1] - mitte[1]),
                       hypot(b[0] - mitte[0], b[1] - mitte[1])) > reichweite:
                    continue
                band(a, b)
        return weit

    label_anchors: Dict[str, Tuple[float, float, str, Pt]] = {}
    for station_id, coord in layout.measures.coords.items():
        x, y = px(coord)
        richtung = station_dirs.get(station_id, (1.0, 0.0))
        pos = (
            stations[station_id].label_pos
            or alternating.get(station_id)
            or sideways.get(station_id)
        )
        if station_id in labels:
            dx, dy, anchor = labels[station_id]
        elif pos is not None:
            dx, dy, anchor = label_offset(pos, style.label_clearance)
        else:
            dx, dy, anchor = auto_label(richtung, style.label_clearance)

        ax, ay = LABEL_DIRECTION[pos] if pos else label_outward(richtung)
        label = stations[station_id].label
        label_lines = label.split("\n")
        versatz = (len(label_lines) - 1) * style.label_font * 1.15
        punkte = station_points.get(station_id, ())

        ddx = ddy = 0.0
        if station_id in labels:
            # `label_override`: die Lage steht von Hand fest, gemessen ab der
            # STATIONSMITTE. Die automatische Verschiebung nach aussen (um
            # die Buendelbreite, den Markerabstand und die Zeilenzahl) bleibt
            # deshalb ganz aus -- sonst addierte sich der von Hand gesetzte
            # Wert auf eine Lage, die man nicht sieht.
            pass
        elif (
            stations[station_id].kind == "hub"
            and abs(ax) > 0.3
            and abs(ay) > 0.3
        ):
            # Kreuzungs-Hub auf einer Schraeglage: an einem Kreuz laeuft in
            # BEIDE Achsen ein Buendel, eine Aufhaengung auf der 45-Grad-
            # Normalen landet also zwangslaeufig auf Linien. Die Ecke wird
            # deshalb aus den beiden Achsenlagen zusammengesetzt -- y wie bei
            # top/bottom, x wie bei left/right. Damit liegt die Beschriftung
            # ausserhalb beider Buendel, im freien Quadranten, und der
            # Abstand ist derselbe wie bei einer achsparallelen Lage.
            sx = 1.0 if ax > 0 else -1.0
            sy = 1.0 if ay > 0 else -1.0
            mitte = (x, y)
            y += sy * max(((q[1] - y) * sy for q in punkte), default=0.0)
            dy = (
                style.label_clearance + 7.0
                if sy > 0
                else -style.label_clearance - 1.0
            )
            anchor = "start" if sx > 0 else "end"
            # Waagerecht steht der Block schon frei; senkrecht gilt dieselbe
            # Regel wie bei top/bottom.
            ddx, ddy = multiline_offset(
                0.0, sy, versatz + badge_extra_height(station_id, ax, ay)
            )
            # Jetzt steht die Hoehe der Zeilen fest -- und erst damit laesst
            # sich sagen, wie weit die Zeichnung dort ueberhaupt reicht.
            grund = y + dy + ddy
            oben_ = grund - style.label_font * 0.8
            unten_ = grund + versatz + style.label_font * 0.25
            # Zwei Masse, das kleinere gewinnt:
            #
            #   aus den Stationspunkten -- wie breit das Buendel hier ist,
            #   aus der Tinte im Hoehenband -- was auf Hoehe der Zeilen
            #   wirklich im Weg steht.
            #
            # Das Band-Mass darf nur naeher heranruecken, nie weiter hinaus.
            # Sonst schiebt eine lange waagerechte Linie, die zufaellig durch
            # das Band laeuft, die Beschriftung quer durch die halbe Karte --
            # dagegen hilft nicht mehr Abstand, sondern eine andere Lage.
            aus_punkten = max(((q[0] - mitte[0]) * sx for q in punkte),
                              default=0.0)
            im_band = tinte_x(mitte, sx, oben_, unten_)
            gemessen = (aus_punkten if im_band is None
                        else min(aus_punkten, im_band))
            x = mitte[0] + sx * gemessen
            dx = sx * style.label_clearance
        else:
            weit = max(
                ((q[0] - x) * ax + (q[1] - y) * ay for q in punkte),
                default=0.0,
            )
            # Auch negativ anwenden: liegt das ganze Buendel auf der anderen
            # Seite der Trassenmitte, laeuft auf der Label-Seite gar keine
            # Linie mehr und die Beschriftung rueckt entsprechend naeher heran.
            x += ax * (weit + marker_clearance(station_id, ax, ay))
            y = side_height(punkte, ax, ay, y + ay * weit)
            ddx, ddy = multiline_offset(
                ax, ay, versatz + badge_extra_height(station_id, ax, ay)
            )

        dx += ddx
        dy += ddy
        fein = nudge_label.get(station_id)
        if fein:
            dx += fein[0]
            dy += fein[1]
        zeilen_hoehe = style.label_font * 1.15
        if len(label_lines) == 1:
            text = esc(label_lines[0])
        else:
            line_height = style.label_font * 1.15

            def tspan(i: int, line: str) -> str:
                # Ein geklammerter Zusatz bekommt eine eigene, kleinere
                # Schriftgroesse; die Grundschrift steht am <text>.
                groesse = (
                    f' font-size="{suffix_font(label, i, style):g}"'
                    if is_suffix(label, i)
                    else ""
                )
                dy_ = 0.0 if i == 0 else line_height
                return (
                    f'<tspan x="{x+dx:.1f}" dy="{dy_:.1f}"{groesse}>'
                    f'{esc(line)}</tspan>'
                )

            text = "".join(tspan(i, line) for i, line in enumerate(label_lines))

        # Ausdehnung des Textblocks: Breite aus der Schaetzung, Hoehe aus
        # Zeilenzahl und Grundlinie. Die waagerechte Lage haengt am
        # text-anchor.
        _bw = label_width(label, style.label_font, style.label_suffix_smaller)
        _bx = x + dx
        _links = (_bx if anchor == "start"
                  else _bx - _bw if anchor == "end" else _bx - _bw / 2)
        merke(_links, y + dy - style.label_font * 0.8,
              _links + _bw,
              y + dy + (len(label_lines) - 1) * zeilen_hoehe
              + style.label_font * 0.25)
        svg.append(
            f'<text x="{x+dx:.1f}" y="{y+dy:.1f}" '
            f'font-size="{style.label_font}" font-weight="bold" '
            f'fill="{style.label_fill}" text-anchor="{anchor}">{text}</text>'
        )
        # Fuer die Linien-Plaketten: wo endet der Textblock -- und wo faengt
        # er an, falls die Signete darueber gehoeren.
        label_anchors[station_id] = (
            x + dx,
            y + dy + (len(label_lines) - 1) * zeilen_hoehe,
            anchor,
            (ax, ay),
            y + dy - style.label_font * 0.8,
        )

    # Linien-Plaketten an den Endpunkten: farbiges Oval mit dem Liniennamen,
    # in einer Reihe unter den Stationsnamen gesetzt. Geschlossene Ringlinien
    # haben keinen Endpunkt und bekommen keine.
    def anchor_for(station_id: str, pos_: str) -> Tuple[float, float, str]:
        """Wo saesse die Grundlinie einer einzeiligen Beschriftung in `pos_`?"""
        ax_, ay_ = LABEL_DIRECTION[pos_]
        punkte_ = station_points.get(station_id, ())
        dx_, dy_, anker_ = label_offset(pos_, style.label_clearance)
        x_, y_ = px(layout.measures.coords[station_id])
        if stations[station_id].kind == "hub" and abs(ax_) > 0.3 and abs(ay_) > 0.3:
            sx = 1.0 if ax_ > 0 else -1.0
            sy = 1.0 if ay_ > 0 else -1.0
            x_ += sx * max(((q[0] - x_) * sx for q in punkte_), default=0.0)
            y_ += sy * max(((q[1] - y_) * sy for q in punkte_), default=0.0)
            dx_ = sx * style.label_clearance
            dy_ = (
                style.label_clearance + 7.0 if sy > 0 else -style.label_clearance - 1.0
            )
            anker_ = "start" if sx > 0 else "end"
        else:
            weit_ = max(
                ((q[0] - x_) * ax_ + (q[1] - y_) * ay_ for q in punkte_), default=0.0
            )
            x_ += ax_ * (weit_ + marker_clearance(station_id, ax_, ay_))
            y_ = side_height(punkte_, ax_, ay_, y_ + ay_ * weit_)
        return x_ + dx_, y_ + dy_, anker_

    # Liegen zwei Trassen auf derselben Diagonalen, steht die Beschriftung
    # schraeg -- darunter waere kein Platz mehr fuer das Tag. Es wandert dann
    # auf die GEGENUEBERLIEGENDE Ecke, die Beschriftung bleibt wo sie ist.
    OPPOSITE_CORNER = {
        "top_left": "bottom_right", "bottom_right": "top_left",
        "top_right": "bottom_left", "bottom_left": "top_right",
        "left": "right", "right": "left", "top": "bottom", "bottom": "top",
    }

    for sid, ids in sorted(terminates_at.items()):
        if sid not in label_anchors:
            continue
        wunsch = badge_reihenfolge.get(sid)
        if wunsch:
            # Nicht genannte Linien haengen sich hinten an, statt zu
            # verschwinden -- eine spaeter dazukommende Linie faellt so auf,
            # ohne dass die Karte eine Plakette verliert.
            ids = sorted(ids, key=lambda l: (
                wunsch.index(l) if l in wunsch else len(wunsch), l
            ))
        lx, ly, anker, (ax_l, ay_l), oberkante = label_anchors[sid]
        # Gilt fuer jede Label-Lage, nicht nur fuer die Diagonalen: das Tag
        # wandert auf die gegenueberliegende Seite der Station.
        gegenseite = sid in opposite_corner_stations
        if gegenseite:
            pos_l = max(
                LABEL_DIRECTION,
                key=lambda k: ax_l * LABEL_DIRECTION[k][0] + ay_l * LABEL_DIRECTION[k][1],
            )
            lx, ly, anker = anchor_for(sid, OPPOSITE_CORNER[pos_l])
            # Das Tag tritt an die Stelle einer Textzeile, also mittig zu
            # deren Grundlinie statt darunter.
            oben = ly - 0.35 * style.label_font - style.badge_height / 2
        elif sid in badge_above_stations:
            oben = oberkante - style.badge_gap - style.badge_height
        else:
            oben = ly + style.badge_gap + 1.0
        fein_b = nudge_badge.get(sid)
        if fein_b:
            lx += fein_b[0]
            oben += fein_b[1]
        breiten = [badge_width(l, style) for l in ids]
        gesamt = sum(breiten) + style.badge_gap * (len(ids) - 1)
        if anker == "middle":
            links = lx - gesamt / 2
        elif anker == "end":
            links = lx - gesamt
        else:
            links = lx
        for line_id, breite in zip(ids, breiten):
            merke(links, oben, links + breite, oben + style.badge_height)
            svg.append(
                f'<rect x="{links:.1f}" y="{oben:.1f}" width="{breite:.1f}" '
                f'height="{style.badge_height:.1f}" '
                f'rx="{style.badge_height / 2:.1f}" '
                f'ry="{style.badge_height / 2:.1f}" '
                f'fill="{highlights[line_id]}"/>'
            )
            svg.append(
                f'<text x="{links + breite / 2:.1f}" '
                f'y="{oben + style.badge_height / 2 + style.badge_font * 0.35:.1f}" '
                f'font-size="{style.badge_font}" font-weight="500" fill="white" '
                f'text-anchor="middle">{esc(badge_text(line_id))}</text>'
            )
            links += breite + style.badge_gap

    # Legende: eine eigene Schicht (netmap/legend.py), die nur liest, was in
    # den Linien steht. Sie kommt zuletzt, damit sie ueber allem liegt, und
    # meldet ihren Umriss wie jedes andere Element an.
    if legend_at is not None and legend_lines:
        x_l, y_l = legend_at
        elemente, kasten = build_legend(
            legend_lines or {}, highlights, style, x_l,
            0.0 if y_l is None else y_l,
        )
        if elemente and y_l is None:
            # Ohne feste Hoehe haengt die Tabelle so tief, wie sie kann: mit
            # genau `style.margin` Luft ueber dem Ersten, was in ihrer
            # Spalte liegt -- demselben Abstand, den der Rahmen ringsum
            # laesst. Weil sie ueber den Nordrand der Karte hinausragt,
            # macht jeder Pixel, den sie tiefer sitzt, die Karte kuerzer.
            breite = kasten[2] - kasten[0]
            hoehe = kasten[3] - kasten[1]
            oben_drunter = min(
                (b[1] for b in inhalt if b[0] < x_l + breite and b[2] > x_l),
                default=None,
            )
            y_l = (0.0 if oben_drunter is None
                   else oben_drunter - style.margin - hoehe)
            elemente, kasten = build_legend(
                legend_lines or {}, highlights, style, x_l, y_l
            )
        if elemente:
            svg.extend(elemente)
            merke(*kasten)

    # Jetzt steht fest, wie weit die Zeichnung wirklich reicht. Der Rahmen
    # ist ringsum gleich breit -- style.margin, unabhaengig davon, ob an der
    # Seite eine Linie, eine lange Beschriftung oder ein Signet aussen liegt.
    # Auf ganze Pixel nach aussen runden: nur dann stimmen width/height und
    # viewBox exakt ueberein. Bei krummen Werten rundet der Kopf auf ganze
    # Pixel auf, die viewBox nicht -- und der Betrachter rendert eine Zeile
    # ausserhalb des weissen Grundes, die dann schwarz bleibt.
    x0 = floor(min(b[0] for b in inhalt) - style.margin)
    y0 = floor(min(b[1] for b in inhalt) - style.margin)
    x1 = ceil(max(b[2] for b in inhalt) + style.margin)
    y1 = ceil(max(b[3] for b in inhalt) + style.margin)
    breite_ges, hoehe_ges = x1 - x0, y1 - y0
    svg[0] = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{breite_ges:d}" '
        f'height="{hoehe_ges:d}" '
        f'viewBox="{x0:d} {y0:d} {breite_ges:d} {hoehe_ges:d}" '
        f'font-family="{esc(style.label_family)}">'
    )
    svg[1] = (
        f'<rect x="{x0:d}" y="{y0:d}" width="{breite_ges:d}" '
        f'height="{hoehe_ges:d}" fill="white"/>'
    )

    svg.append("</svg>")
    return "\n".join(svg)


def print_layout_report(
    lines: Mapping[str, TurnLine],
    layout: LayoutResult,
    stations: Mapping[str, Station],
) -> None:
    print(f"Linien: {len(lines)}")
    print(f"Stationen: {len(layout.measures.coords)}")
    print(f"Korridore: {len(layout.tracks.corridor_paths)}")
    print("Startwinkel:")
    for lid in lines:
        mode = "definiert" if lines[lid].start is not None else "automatisch"
        print(f"  {lid}: {layout.network.starts[lid]} Grad ({mode})")

    # gemeinsame Korridore
    corridor_users: Dict[frozenset[str], List[str]] = defaultdict(list)
    for lid, pline in layout.network.parsed.items():
        for seg in pline.segments:
            corridor_users[_pair_key(seg.a, seg.b)].append(lid)

    shared = {k: v for k, v in corridor_users.items() if len(v) > 1}
    if shared:
        print("Gemeinsame Korridore:")
        for pair, users in sorted(shared.items(), key=lambda item: sorted(item[0])):
            names = sorted(stations[sid].name for sid in pair)
            print(f"  {names[0]} <-> {names[1]}: {', '.join(users)}")

    elastic_groups: Dict[Tuple[str, str], float] = {}
    for (lid, seg_idx, leg_idx), value in layout.measures.leg_lengths.items():
        seg = layout.network.parsed[lid].segments[seg_idx]
        spec = seg.legs[leg_idx].length
        if spec.elastic:
            group = spec.group or f"seg{seg_idx}_leg{leg_idx}"
            elastic_groups[(lid, group)] = value

    if elastic_groups:
        print("Geloeste elastische Laengen:")
        for (lid, group), value in sorted(elastic_groups.items()):
            print(f"  {lid} / {group}: {value:.3f}")
