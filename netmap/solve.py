"""
Phasen 2 bis 4b: aus der Definition wird Geometrie.

    2   Netz parsen und ausrichten      build_network()
    3   Koordinaten und Laengen loesen  solve_measures()
    4   Korridore bauen                 build_tracks()
    2-4 alles zusammen                  solve_layout()
    4b  Linien fuehren und buendeln     build_line_layout()
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace
from math import hypot, radians, sqrt, tan
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from .model import (
    CFG, COMPASS, Corner, Corridor, GeometryError, LayoutResult, LegSpec,
    LengthSpec, LengthVariable, LineLayout, LinePath, Measures, Network,
    NetworkConfig, ParsedLine, Path, Pt, SegmentSpec, ShapeSignature,
    SolverConfig, Station, Step, Tracks, Turn, TurnLine, _norm, _pair_key,
)


# ==========================================================================
# PHASE 2a: PARSEN
# ==========================================================================


def _fixed_length(value: float, min_gap: Optional[float] = None) -> LengthSpec:
    """Starres Beinstueck mit fester Laenge (Default oder ueber FixPath()).

    `min_gap`/`max_gap` sind fuer starre Beine keine echte Schranke (der
    Solver bindet nur elastische Laengen), gehen aber in die Korridor-
    Signatur ein -- zwei Linien ueber derselben Kante muessen hier denselben
    Wert haben. Deshalb wird `min_gap` explizit mitgefuehrt statt aus `value`
    abgeleitet; `max_gap` folgt immer `value`.
    """
    return LengthSpec(
        preferred=value,
        elastic=False,
        group=None,
        min_gap=value if min_gap is None else min_gap,
        max_gap=value,
        flex=1.0,
    )


def _flex_length(path: Path, spacing: float, netz: NetworkConfig) -> LengthSpec:
    """Elastisches Beinstueck aus einem FlexPath(); offene Felder fallen auf
    den Stationsabstand der Linie bzw. die Config-Vorgabe zurueck. Ohne
    `max_length` ist die Laenge nach oben unbeschraenkt."""
    return LengthSpec(
        preferred=spacing if path.length is None else path.length,
        elastic=True,
        group=path.group,
        min_gap=netz.min_gap_default if path.minimum is None else path.minimum,
        max_gap=float("inf") if path.maximum is None else path.maximum,
        flex=path.flex,
    )


def _build_legs_from_modifiers(
    modifiers: Sequence[Union[Turn, Path]],
    start_offset: int,
    spacing: float,
    netz: NetworkConfig,
    line_id: str,
    a_name: str,
    b_name: str,
) -> Tuple[Tuple[LegSpec, ...], int]:
    """Uebersetzt eine Folge aus Turn()/FlexPath()/FixPath() (in Reihenfolge,
    wie sie zwischen zwei Stationen in `steps` stehen) in konkrete Legs.

    Regel: jeder Turn schliesst das Beinstueck DAVOR ab (Standardlaenge =
    dieses Turns eigener radius, oder line.spacing/2 falls radius=None). Ein
    FlexPath()/FixPath() markiert das gerade offene (noch nicht
    abgeschlossene) Beinstueck als elastisch bzw. mit expliziter starrer
    Laenge -- das ueberschreibt die radius/spacing-Vorgabe fuer genau dieses
    eine Stueck. Das letzte Beinstueck (nach dem letzten Turn, oder das
    einzige Stueck falls gar kein Turn vorkommt) wird beim Erreichen der
    naechsten Station abgeschlossen; sein Default ist der radius des
    letzten Turns, oder line.spacing, falls kein Turn vorkam.
    """
    legs: List[LegSpec] = []
    offset = start_offset
    pending_path: Optional[Path] = None
    pending_corner: Optional[Corner] = None
    last_turn_radius: Optional[float] = None

    def close(bearing_offset: int, default_len: float, corner: Optional[Corner]) -> None:
        nonlocal pending_path
        if bearing_offset not in COMPASS:
            raise GeometryError(
                f"{line_id}: Segment {a_name} -> {b_name} fuehrt auf "
                f"ungueltige Richtung {bearing_offset} Grad"
            )
        if pending_path is not None:
            if pending_path.flexibility:
                length = _flex_length(pending_path, spacing, netz)
            else:
                length = _fixed_length(pending_path.length)
            pending_path = None
        else:
            length = _fixed_length(default_len)
        legs.append(LegSpec(bearing_offset=bearing_offset, length=length, corner=corner))

    for mod in modifiers:
        if isinstance(mod, Path):
            if pending_path is not None:
                raise GeometryError(
                    f"{line_id}: zwei FlexPath()/FixPath() direkt hintereinander "
                    f"zwischen {a_name} und {b_name} -- pro Beinstueck nur eins"
                )
            pending_path = mod
            continue
        # Turn.radius ist ein KREISRADIUS; die Beinlaenge daneben ist die
        # zugehoerige Tangentenlaenge. Dadurch fuellen bei zwei
        # aufeinanderfolgenden Turns die beiden Tangenten das Stueck
        # dazwischen genau aus -- kein gerades Reststueck.
        radius = (
            spacing / 2
            if mod.radius is None
            else mod.radius * tan(radians(abs(mod.delta)) / 2)
        )
        close(offset, radius, pending_corner)
        offset = (offset + mod.delta) % 360
        last_turn_radius = radius
        # Radius sofort aufloesen: er bestimmt ueber die Tangente, wie viel
        # Platz der Bogen auf seinen Nachbarbeinen braucht, und geht deshalb
        # noch in dieser Phase als Laengenschranke in den Solver ein.
        pending_corner = Corner(
            delta=mod.delta,
            radius=netz.curve_radius(mod.delta) if mod.radius is None else mod.radius,
            forced=mod.radius is not None,
        )

    trailing_default = spacing if last_turn_radius is None else last_turn_radius
    close(offset, trailing_default, pending_corner)
    return _with_curve_room(tuple(legs)), offset


def _with_curve_room(legs: Tuple[LegSpec, ...]) -> Tuple[LegSpec, ...]:
    """Zieht die untere Laengenschranke jedes elastischen Beins so weit hoch,
    dass die Boegen an seinen beiden Enden darauf Platz haben.

    Ein Bogen verbraucht an jedem seiner Nachbarbeine seine Tangentenlaenge
    t = R * tan(delta/2). Ein Bein zwischen zwei Knicken braucht also die
    Summe beider Tangenten, sonst muesste beim Rendern einer der Radien
    gedeckelt werden. Starre Beine (FixPath) bleiben unangetastet -- dort
    ist die Laenge eine Ansage; passt der Radius nicht, greift beim Rendern
    weiterhin der Deckel.
    """
    neu: List[LegSpec] = []
    for i, leg in enumerate(legs):
        vorne = leg.corner.tangent() if leg.corner else 0.0
        nach = legs[i + 1].corner if i + 1 < len(legs) else None
        bedarf = vorne + (nach.tangent() if nach else 0.0)
        if bedarf > leg.length.min_gap and leg.length.elastic:
            neu.append(
                replace(
                    leg,
                    length=replace(
                        leg.length,
                        min_gap=bedarf,
                        preferred=max(leg.length.preferred, bedarf),
                        max_gap=max(leg.length.max_gap, bedarf),
                    ),
                )
            )
        else:
            neu.append(leg)
    return tuple(neu)


def parse_line(
    line_id: str,
    line: TurnLine,
    netz: NetworkConfig,
    station_registry: Mapping[str, Station],
) -> ParsedLine:
    stations: List[str] = []
    segments: List[SegmentSpec] = []

    current_station: Optional[str] = None
    current_bearing_offset = 0
    pending_modifiers: List[Union[Turn, Path]] = []

    for raw in line.steps:
        if isinstance(raw, (Turn, Path)):
            if current_station is None:
                raise GeometryError(
                    f"{line_id}: {type(raw).__name__} darf erst nach der "
                    "ersten Station kommen"
                )
            pending_modifiers.append(raw)
            continue

        if not isinstance(raw, str):
            raise TypeError(f"{line_id}: Unbekannter Schritt {raw!r}")

        if raw not in station_registry:
            raise GeometryError(f"{line_id}: Unbekannte Station-ID '{raw}'")

        # Ring schliessen: bei closed=True darf die LETZTE Station exakt die
        # erste sein -- das erzeugt die schliessende Segmentkante, ohne sie
        # als neue (doppelte) Station zu fuehren.
        closing = (
            line.closed and stations and raw == stations[0]
            and current_station != stations[0]
        )
        if raw in stations and not closing:
            raise GeometryError(f"{line_id}: Station '{raw}' kommt mehrfach vor")

        if current_station is None:
            current_station = raw
            stations.append(raw)
            pending_modifiers = []
            continue

        if pending_modifiers:
            leg_specs, current_bearing_offset = _build_legs_from_modifiers(
                pending_modifiers, current_bearing_offset, line.spacing, netz,
                line_id, current_station, raw,
            )
        else:
            if current_bearing_offset not in COMPASS:
                raise GeometryError(
                    f"{line_id}: Segment {current_station} -> {raw} hat "
                    f"ungueltige Richtung {current_bearing_offset}"
                )
            leg_specs = (
                LegSpec(
                    bearing_offset=current_bearing_offset,
                    length=_fixed_length(line.spacing, min_gap=netz.min_gap_default),
                ),
            )

        segments.append(
            SegmentSpec(
                line_id=line_id,
                index=len(segments),
                a=current_station,
                b=raw,
                legs=leg_specs,
            )
        )

        current_station = raw
        if not closing:
            stations.append(raw)
        pending_modifiers = []
        if closing:
            break

    if len(stations) < 2:
        raise GeometryError(f"{line_id}: Eine Linie braucht mindestens zwei Stationen")

    return ParsedLine(
        closed=line.closed,
        line_id=line_id,
        stations=stations,
        segments=segments,
        branch_of=line.branch_of,
    )


# ==========================================================================
# PHASE 2b: STARTWINKEL BESTIMMEN
# ==========================================================================


def _shape_legs(seg: SegmentSpec) -> List[LegSpec]:
    """Die formgebenden Beine eines Segments.

    Starre Beine der Laenge 0 (siehe FixPath) bleiben aussen vor: sie setzen
    nur einen Knick auf die Station und verschieben nichts. Zwei Linien, von
    denen eine an der Station abbiegt und die andere geradeaus faehrt, sollen
    denselben Korridor teilen duerfen -- physisch ist er identisch.
    """
    legs = [
        leg for leg in seg.legs
        if leg.length.elastic or abs(leg.length.preferred) > 1e-9
    ]
    return legs or list(seg.legs)


def _segment_shape_signature(seg: SegmentSpec) -> ShapeSignature:
    """
    Signatur eines Segments relativ zum ersten Bein.
    Wird verwendet, um gemeinsame Korridore auf Konsistenz zu pruefen.
    """
    shape = _shape_legs(seg)
    first = shape[0].bearing_offset
    signature = []
    for leg in shape:
        rel = (leg.bearing_offset - first) % 360
        signature.append(
            (
                rel,
                leg.length.elastic,
                round(leg.length.preferred, 8),
                round(leg.length.min_gap, 8),
                round(leg.length.max_gap, 8),
                round(leg.length.flex, 8),
            )
        )
    return tuple(signature)


def _reversed_shape_signature(seg: SegmentSpec) -> ShapeSignature:
    """Signatur desselben Segments, als wuerde man es in umgekehrter
    Stationsrichtung (b -> a) durchlaufen -- Beinreihenfolge gedreht und
    jede Richtung um 180 Grad gespiegelt."""
    legs = list(reversed(_shape_legs(seg)))
    if not legs:
        return ()
    first = (legs[0].bearing_offset + 180) % 360
    signature = []
    for leg in legs:
        b = (leg.bearing_offset + 180) % 360
        rel = (b - first) % 360
        signature.append(
            (
                rel,
                leg.length.elastic,
                round(leg.length.preferred, 8),
                round(leg.length.min_gap, 8),
                round(leg.length.max_gap, 8),
                round(leg.length.flex, 8),
            )
        )
    return tuple(signature)


def infer_start_bearings(
    lines: Mapping[str, TurnLine],
    parsed: Mapping[str, ParsedLine],
    netz: NetworkConfig,
) -> Dict[str, int]:
    """Bestimmt fehlende Startwinkel aus gemeinsamen Korridoren.

    Jede von zwei Linien befahrene Kante liefert eine feste Winkeldifferenz
    zwischen deren Startwinkeln. Aus explizit gesetzten `start`-Werten wird
    diese Beziehung durch das Netz propagiert; Linien ohne jede Verbindung
    fallen auf netz.default_start zurueck. Widersprueche und Kanten, deren
    Form in beiden Linien nicht uebereinstimmt, werden gemeldet.

    Die Fahrtrichtung darf sich unterscheiden -- rueckwaerts durchlaufene
    Kanten werden spiegelbildlich verglichen.
    """
    occurrences: Dict[frozenset[str], List[SegmentSpec]] = defaultdict(list)
    for pline in parsed.values():
        for seg in pline.segments:
            occurrences[_pair_key(seg.a, seg.b)].append(seg)

    graph: Dict[str, List[Tuple[str, int]]] = defaultdict(list)

    for pair, group in occurrences.items():
        if len(group) < 2:
            continue

        base = group[0]
        base_sig = _segment_shape_signature(base)

        for other in group[1:]:
            if base.a == other.a and base.b == other.b:
                reversed_edge = False
            elif base.a == other.b and base.b == other.a:
                reversed_edge = True
            else:
                # kann bei korrekter frozenset-Gruppierung nicht auftreten
                raise GeometryError(
                    f"Gemeinsamer Korridor {sorted(pair)}: unerwartete Stationspaare "
                    f"({base.a}->{base.b}) vs ({other.a}->{other.b})"
                )

            if not reversed_edge:
                if _segment_shape_signature(other) != base_sig:
                    raise GeometryError(
                        f"Gemeinsamer Korridor {base.a} -> {base.b} hat in "
                        f"{base.line_id} und {other.line_id} unterschiedliche Geometrie."
                    )
                delta = (
                    _shape_legs(base)[0].bearing_offset
                    - _shape_legs(other)[0].bearing_offset
                ) % 360
            else:
                # other durchlaeuft denselben physischen Abschnitt rueckwaerts
                # (b -> a) -- Spiegelbild-Vergleich statt exaktem Abgleich.
                if _segment_shape_signature(other) != _reversed_shape_signature(base):
                    raise GeometryError(
                        f"Gemeinsamer Korridor {base.a} <-> {base.b} hat in "
                        f"{base.line_id} und {other.line_id} (rueckwaerts) "
                        "unterschiedliche Geometrie."
                    )
                delta = (
                    _shape_legs(base)[-1].bearing_offset
                    - _shape_legs(other)[0].bearing_offset + 180
                ) % 360

            graph[base.line_id].append((other.line_id, delta))
            graph[other.line_id].append((base.line_id, (-delta) % 360))

    starts: Dict[str, int] = {}
    queue: deque[str] = deque()

    for lid, line in lines.items():
        if line.start is not None:
            starts[lid] = line.start % 360
            queue.append(lid)

    def propagate() -> None:
        while queue:
            lid = queue.popleft()
            for other, delta in graph.get(lid, []):
                candidate = (starts[lid] + delta) % 360
                if other in starts:
                    if starts[other] != candidate:
                        raise GeometryError(
                            f"Widerspruechliche Startwinkel fuer {other}: "
                            f"{starts[other]} Grad vs. {candidate} Grad"
                        )
                else:
                    starts[other] = candidate
                    queue.append(other)

    propagate()

    for lid in lines:
        if lid not in starts:
            starts[lid] = netz.default_start
            queue.append(lid)
            propagate()

    for lid, start in starts.items():
        if start not in COMPASS:
            raise GeometryError(
                f"{lid}: geloester Startwinkel {start} ist kein Vielfaches von 45 Grad"
            )

    return starts


# ==========================================================================
# PHASE 3: LAENGEN LOESEN
# ==========================================================================


def _collect_length_variables(
    parsed: Mapping[str, ParsedLine],
) -> Tuple[
    Dict[Tuple[str, int, int], Tuple[str, str]],
    Dict[Tuple[str, str], LengthVariable],
]:
    leg_to_var: Dict[Tuple[str, int, int], Tuple[str, str]] = {}
    variables: Dict[Tuple[str, str], LengthVariable] = {}

    for lid, pline in parsed.items():
        for seg in pline.segments:
            for leg_index, leg in enumerate(seg.legs):
                spec = leg.length
                if not spec.elastic:
                    continue

                local_group = spec.group or f"__seg{seg.index}_leg{leg_index}"
                key = (lid, local_group)
                leg_to_var[(lid, seg.index, leg_index)] = key

                if key not in variables:
                    variables[key] = LengthVariable(
                        key=key,
                        preferred=spec.preferred,
                        min_gap=spec.min_gap,
                        max_gap=spec.max_gap,
                        flex=spec.flex,
                        occurrences=[(lid, seg.index, leg_index)],
                    )
                else:
                    var = variables[key]
                    for attr in ("preferred", "min_gap", "max_gap", "flex"):
                        if abs(getattr(var, attr) - getattr(spec, attr)) > 1e-9:
                            raise GeometryError(
                                f"{lid}: Gruppe '{local_group}' verwendet "
                                f"unterschiedliche {attr}-Werte"
                            )
                    var.occurrences.append((lid, seg.index, leg_index))

    return leg_to_var, variables


def _station_components(parsed: Mapping[str, ParsedLine]) -> List[List[str]]:
    adjacency: Dict[str, set[str]] = defaultdict(set)
    order: List[str] = []
    seen: set[str] = set()

    for pline in parsed.values():
        for name in pline.stations:
            if name not in seen:
                seen.add(name)
                order.append(name)
            adjacency.setdefault(name, set())
        for seg in pline.segments:
            adjacency[seg.a].add(seg.b)
            adjacency[seg.b].add(seg.a)

    components: List[List[str]] = []
    visited: set[str] = set()
    for root in order:
        if root in visited:
            continue
        comp: List[str] = []
        q = deque([root])
        visited.add(root)
        while q:
            node = q.popleft()
            comp.append(node)
            for nxt in sorted(adjacency[node]):
                if nxt not in visited:
                    visited.add(nxt)
                    q.append(nxt)
        components.append(comp)

    return components


def _solve_kkt(hdiag: np.ndarray, z0: np.ndarray, A: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = len(z0)
    m = len(b)
    H = np.diag(hdiag)
    K = np.block(
        [
            [H, A.T],
            [A, np.zeros((m, m), dtype=float)],
        ]
    )
    rhs = np.concatenate((hdiag * z0, b))
    solution, *_ = np.linalg.lstsq(K, rhs, rcond=None)
    return solution[:n]


def build_network(
    lines: Mapping[str, TurnLine],
    *,
    stations: Mapping[str, Station],
    netz: NetworkConfig = CFG.netz,
) -> Network:
    """Phase 2 (Netz): Linien parsen und ausrichten.

    Prueft dabei die Definition (unbekannte Stationen, doppelte Stationen,
    ungueltige Richtungen) und die Konsistenz gemeinsamer Korridore.
    """
    if not lines:
        raise GeometryError("Es wurden keine Linien definiert")

    parsed = {lid: parse_line(lid, line, netz, stations) for lid, line in lines.items()}
    _resolve_branches(parsed)
    starts = infer_start_bearings(lines, parsed, netz)
    return Network(parsed=parsed, starts=starts)


def _resolve_branches(parsed: Mapping[str, ParsedLine]) -> None:
    """Sucht fuer jeden Zweig das Ende, mit dem er auf seine Stammlinie trifft.

    Genau eines der beiden Enden muss auf der Stammlinie liegen: das andere
    ist das freie, an dem der Zweig endet und sein Signet traegt. Liegen
    beide oder keines darauf, ist die Definition kein Zweig.
    """
    for lid, pline in parsed.items():
        if pline.branch_of is None:
            continue
        stamm = parsed.get(pline.branch_of)
        if stamm is None:
            raise GeometryError(
                f"{lid}: branch_of nennt '{pline.branch_of}', diese Linie gibt es nicht"
            )
        enden = (pline.stations[0], pline.stations[-1])
        treffer = [sid for sid in enden if sid in stamm.stations]
        if len(treffer) != 1:
            raise GeometryError(
                f"{lid}: Zweig von '{pline.branch_of}' -- genau EIN Ende muss auf "
                f"der Stammlinie liegen, es sind {len(treffer)} "
                f"({', '.join(enden)})"
            )
        pline.branch_join = treffer[0]


def solve_measures(
    lines: Mapping[str, TurnLine],
    network: Network,
    *,
    solver: SolverConfig = CFG.solver,
) -> Measures:
    """Phase 3 (Masse): Koordinaten und elastische Laengen loesen.

    Stellt das Gleichungssystem aus den Segmenten auf (p_b - p_a = Summe
    der Beinvektoren), verankert die Netzteile und loest es per Least
    Squares; untere und obere Laengenschranken werden ueber ein Active-Set
    nachgezogen.
    """
    parsed = network.parsed
    starts = network.starts

    station_order: List[str] = []
    station_seen: set[str] = set()
    for pline in parsed.values():
        for name in pline.stations:
            if name not in station_seen:
                station_seen.add(name)
                station_order.append(name)

    station_index = {name: i for i, name in enumerate(station_order)}

    leg_to_var, length_vars = _collect_length_variables(parsed)
    var_order = list(length_vars)

    coord_count = 2 * len(station_order)
    var_index = {key: coord_count + i for i, key in enumerate(var_order)}
    n_unknowns = coord_count + len(var_order)

    rows: List[np.ndarray] = []
    rhs: List[float] = []

    def x_idx(name: str) -> int:
        return 2 * station_index[name]

    def y_idx(name: str) -> int:
        return 2 * station_index[name] + 1

    def add_constraint(coeffs: Mapping[int, float], value: float) -> None:
        row = np.zeros(n_unknowns, dtype=float)
        for i, c in coeffs.items():
            row[i] = c
        rows.append(row)
        rhs.append(float(value))

    # Segmentgleichungen: p_b - p_a = Summe( dir_j * laenge_j )
    for lid, pline in parsed.items():
        for seg in pline.segments:
            x_coeffs = {x_idx(seg.b): 1.0, x_idx(seg.a): -1.0}
            y_coeffs = {y_idx(seg.b): 1.0, y_idx(seg.a): -1.0}
            x_rhs = 0.0
            y_rhs = 0.0

            for leg_idx, leg in enumerate(seg.legs):
                bearing = (starts[lid] + leg.bearing_offset) % 360
                if bearing not in COMPASS:
                    raise GeometryError(
                        f"{lid}: Segment {seg.a} -> {seg.b} enthaelt ungueltige "
                        f"Beinrichtung {bearing} Grad"
                    )
                dx, dy = COMPASS[bearing]
                key = leg_to_var.get((lid, seg.index, leg_idx))
                if key is None:
                    x_rhs += dx * leg.length.preferred
                    y_rhs += dy * leg.length.preferred
                else:
                    vi = var_index[key]
                    x_coeffs[vi] = x_coeffs.get(vi, 0.0) - dx
                    y_coeffs[vi] = y_coeffs.get(vi, 0.0) - dy

            add_constraint(x_coeffs, x_rhs)
            add_constraint(y_coeffs, y_rhs)

    # Anker
    components = _station_components(parsed)
    component_of: Dict[str, int] = {}
    for ci, comp in enumerate(components):
        for station in comp:
            component_of[station] = ci

    anchored_components: set[int] = set()
    for lid, line in lines.items():
        if line.anchor is None:
            continue
        first_station = parsed[lid].stations[0]
        ax, ay = line.anchor
        add_constraint({x_idx(first_station): 1.0}, ax)
        add_constraint({y_idx(first_station): 1.0}, ay)
        anchored_components.add(component_of[first_station])

    # Nicht verankerte Komponenten deterministisch platzieren
    for ci, comp in enumerate(components):
        if ci in anchored_components:
            continue
        root = comp[0]
        add_constraint({x_idx(root): 1.0}, ci * solver.component_gap)
        add_constraint({y_idx(root): 1.0}, 0.0)

    A_base = np.vstack(rows)
    b_base = np.asarray(rhs, dtype=float)

    z0 = np.zeros(n_unknowns, dtype=float)
    hdiag = np.full(n_unknowns, solver.coordinate_regularization, dtype=float)

    for key in var_order:
        idx = var_index[key]
        var = length_vars[key]
        z0[idx] = var.preferred
        hdiag[idx] = 1.0 / (var.flex * var.flex)

    active_bounds: Dict[Tuple[str, str], float] = {}
    z = np.zeros(n_unknowns, dtype=float)

    for _ in range(len(var_order) + 1):
        if active_bounds:
            bound_rows = []
            bound_rhs = []
            for key, lower in active_bounds.items():
                row = np.zeros(n_unknowns, dtype=float)
                row[var_index[key]] = 1.0
                bound_rows.append(row)
                bound_rhs.append(lower)
            A = np.vstack((A_base, np.vstack(bound_rows)))
            b = np.concatenate((b_base, np.asarray(bound_rhs)))
        else:
            A, b = A_base, b_base

        z = _solve_kkt(hdiag, z0, A, b)
        residual = A @ z - b
        max_residual = float(np.max(np.abs(residual))) if len(residual) else 0.0
        if max_residual > solver.constraint_tolerance:
            raise GeometryError(
                "Die Linienregeln sind geometrisch widerspruechlich. "
                f"Maximaler Gleichungsfehler: {max_residual:.3g}"
            )

        violations: List[Tuple[Tuple[str, str], float, float]] = []
        for key in var_order:
            if key in active_bounds:
                continue
            var = length_vars[key]
            value = z[var_index[key]]
            if value < var.min_gap - 1e-8:
                violations.append((key, var.min_gap, var.min_gap - value))
            elif value > var.max_gap + 1e-8:
                violations.append((key, var.max_gap, value - var.max_gap))
        if not violations:
            break

        worst_key, worst_bound, _ = max(violations, key=lambda item: item[2])
        active_bounds[worst_key] = worst_bound
    else:
        raise GeometryError("Laengengrenzen konnten nicht geloest werden")

    coords = {
        name: (float(z[x_idx(name)]), float(z[y_idx(name)]))
        for name in station_order
    }

    leg_lengths: Dict[Tuple[str, int, int], float] = {}
    for lid, pline in parsed.items():
        for seg in pline.segments:
            for leg_idx, leg in enumerate(seg.legs):
                key = leg_to_var.get((lid, seg.index, leg_idx))
                leg_lengths[(lid, seg.index, leg_idx)] = (
                    leg.length.preferred if key is None else float(z[var_index[key]])
                )

    return Measures(coords=coords, leg_lengths=leg_lengths)


# ==========================================================================
# PHASE 4: GLEISE
# ==========================================================================


def build_tracks(network: Network, measures: Measures) -> Tracks:
    """Phase 4 (Gleise): aus Koordinaten und geloesten Laengen die
    Polylinien der Streckenabschnitte bauen.

    Rein rekonstruierend -- braucht keine Konfiguration. Jedes Stationspaar
    wird genau einmal erzeugt, damit ein von mehreren Linien befahrener
    Korridor auch nur eine Mittellinie hat.
    """
    parsed = network.parsed
    starts = network.starts
    coords = measures.coords

    corridor_paths: Dict[frozenset[str], List[Pt]] = {}
    corridor_corners: Dict[frozenset[str], List[Optional[Corner]]] = {}
    for lid, pline in parsed.items():
        for seg in pline.segments:
            key = _pair_key(seg.a, seg.b)
            if key in corridor_paths:
                continue

            # corners[i] beschreibt den Knick AN points[i] -- das ist der
            # Corner des Beins, das AB points[i] weiterlaeuft (also legs[i],
            # nicht legs[i-1]). Der letzte Punkt ist die Station seg.b und
            # hat nie einen Knick.
            lengths = [
                measures.leg_lengths[(lid, seg.index, k)] for k in range(len(seg.legs))
            ]
            points = [coords[seg.a]]
            corners: List[Optional[Corner]] = [None]
            pos = coords[seg.a]
            for leg_idx, leg in enumerate(seg.legs):
                length = lengths[leg_idx]
                bearing = (starts[lid] + leg.bearing_offset) % 360
                dx, dy = COMPASS[bearing]
                pos = (pos[0] + dx * length, pos[1] + dy * length)
                points.append(pos)
                next_leg = seg.legs[leg_idx + 1] if leg_idx + 1 < len(seg.legs) else None
                if next_leg is None or next_leg.corner is None:
                    corners.append(None)
                else:
                    # Freier Platz auf beiden Nachbarbeinen: von jedem Bein
                    # geht ab, was der Knick am ANDEREN Ende dieses Beins
                    # schon beansprucht. Nur wenn ein Bein an beiden Enden
                    # einen Knick hat, teilen sich die zwei Boegen es also
                    # wirklich; sonst steht die volle Beinlaenge bereit.
                    davor = seg.legs[leg_idx].corner
                    danach = (
                        seg.legs[leg_idx + 2].corner
                        if leg_idx + 2 < len(seg.legs)
                        else None
                    )
                    frei_links = lengths[leg_idx] - (davor.tangent() if davor else 0.0)
                    frei_rechts = lengths[leg_idx + 1] - (
                        danach.tangent() if danach else 0.0
                    )
                    corners.append(
                        replace(
                            next_leg.corner,
                            max_tangent=max(min(frei_links, frei_rechts), 0.0),
                        )
                    )

            end = coords[seg.b]
            if hypot(points[-1][0] - end[0], points[-1][1] - end[1]) > 1e-6:
                raise GeometryError(
                    f"Interne Rekonstruktion stimmt nicht fuer {seg.a} -> {seg.b}"
                )

            corridor_paths[key] = points
            corridor_corners[key] = corners

    return Tracks(corridor_paths=corridor_paths, corridor_corners=corridor_corners)


def solve_layout(
    lines: Mapping[str, TurnLine],
    *,
    stations: Mapping[str, Station],
    cfg: Config = CFG,
) -> LayoutResult:
    """Phase 2-4 am Stueck: Netz -> Masse -> Gleise.

    Reine Verkettung; wer eine Zwischenstufe braucht (z.B. das Netz pruefen,
    ohne zu loesen), ruft die drei Funktionen einzeln auf.
    """
    network = build_network(lines, stations=stations, netz=cfg.netz)
    measures = solve_measures(lines, network, solver=cfg.solver)
    tracks = build_tracks(network, measures)
    return LayoutResult(network=network, measures=measures, tracks=tracks)


# ==========================================================================
# PHASE 4b: LINE ROUTING AND BUNDLING
# ==========================================================================


def _bearing_of(p_from: Pt, p_to: Pt) -> int:
    dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
    length = hypot(dx, dy)
    ux, uy = dx / length, dy / length
    for angle, (cx, cy) in COMPASS.items():
        if abs(ux - cx) < 1e-6 and abs(uy - cy) < 1e-6:
            return angle
    raise GeometryError("Kante ist nicht oktilinear")


def _edge_bearing(points: Sequence[Pt], *, at_start: bool) -> int:
    """Richtung, mit der eine Korridorkante beginnt bzw. endet.

    Ueberspringt Beine der Laenge 0 -- die entstehen aus FixPath(0.0), wenn
    ein Knick genau auf einer Station sitzt, und haben keine Richtung.
    """
    reihe = (
        range(len(points) - 1) if at_start else range(len(points) - 2, -1, -1)
    )
    for i in reihe:
        a, b = points[i], points[i + 1]
        if hypot(b[0] - a[0], b[1] - a[1]) > 1e-9:
            return _bearing_of(a, b)
    raise GeometryError("Korridorkante ohne Laenge")


class _UnionFind:
    def __init__(self, items: Iterable[frozenset]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: frozenset) -> frozenset:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: frozenset, b: frozenset) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _corridor_slot_overrides(
    layout: LayoutResult,
    corridors: Mapping[str, Corridor],
    edge_lines: Mapping[frozenset, set],
    families: Mapping[str, str],
) -> Tuple[Dict[frozenset, Dict[str, float]], set]:
    """Feste Spurlagen aus den von Hand definierten Korridoren.

    Das in `Corridor.offsets` notierte Vorzeichen gilt in Schreibrichtung
    des Korridors; hier wird es auf die gespeicherte Kantenrichtung
    umgerechnet, in der die Slots spaeter angewendet werden.
    """
    overrides: Dict[frozenset, Dict[str, float]] = {}
    # Kanten mit Corridor.immediate -> wo auf dem Bein der Schwenk sitzt
    sofort: Dict[frozenset, str] = {}
    # Startspuren aus Corridor.start_offsets, gleich normiert wie die Slots
    startspuren: Dict[frozenset, Dict[str, float]] = {}
    # Abweichende Bogenradien aus Corridor.radii, je Kante und Linie. Ohne
    # Vorzeichen: ein Radius hat keine Seite.
    bogen: Dict[frozenset, Dict[str, float]] = {}
    # Bezugsradien aus Corridor.radius_at: (Bezugslinie, ihr Radius) je Kante
    bezug: Dict[frozenset, Tuple[str, float]] = {}
    # Kanten mit Corridor.radius_from_centre: alte Lesart, Radius = Mitte
    mitte: set = set()
    for name, corridor in corridors.items():
        ids = [step for step in corridor.steps if isinstance(step, str)]
        for a, b in zip(ids, ids[1:]):
            key = _pair_key(a, b)
            if key not in layout.tracks.corridor_paths:
                raise GeometryError(
                    f"Korridor '{name}': {a} -> {b} ist keine Kante im Netz"
                )
            if key not in edge_lines:
                continue  # Kante wird von keiner der betrachteten Linien befahren

            points = layout.tracks.corridor_paths[key]
            start = layout.measures.coords[a]
            forward = hypot(points[0][0] - start[0], points[0][1] - start[1]) < 1e-6
            sign = 1 if forward else -1

            slots: Dict[str, float] = {}
            for line_id in edge_lines[key]:
                family = families.get(line_id, line_id)
                # Erst die Linien-ID, dann die Familie: das Spezielle
                # schlaegt das Allgemeine. Nur so lassen sich Linien
                # derselben Familie auf ihren GETRENNTEN Aesten
                # unterschiedlich legen (z.B. S8 und S85 im Norden), ohne
                # dass der Familienname beide erwischt.
                if line_id in corridor.offsets:
                    value = corridor.offsets[line_id]
                elif family in corridor.offsets:
                    value = corridor.offsets[family]
                else:
                    raise GeometryError(
                        f"Korridor '{name}': {line_id} faehrt {a} -> {b}, hat "
                        f"aber keinen Versatz (weder '{family}' noch '{line_id}' "
                        "in offsets)"
                    )
                slots[line_id] = value * sign
            overrides[key] = slots
            if corridor.start_offsets:
                startspuren[key] = {
                    lid: value * sign
                    for lid, value in corridor.start_offsets.items()
                }
            else:
                startspuren.pop(key, None)
            if corridor.radius_from_centre:
                mitte.add(key)
            else:
                mitte.discard(key)
            if corridor.radius_at:
                if len(corridor.radius_at) != 1:
                    raise GeometryError(
                        f"Korridor '{name}': radius_at nennt genau EINE "
                        "Bezugslinie, alle anderen liegen konzentrisch dazu"
                    )
                (ref_id, ref_r), = corridor.radius_at.items()
                if ref_id not in edge_lines[key]:
                    raise GeometryError(
                        f"Korridor '{name}': radius_at nennt '{ref_id}', die "
                        f"Kante {a} -> {b} aber nicht befaehrt"
                    )
                bezug[key] = (ref_id, ref_r)
            else:
                bezug.pop(key, None)
            if corridor.radii:
                bogen[key] = {
                    line_id: (corridor.radii[line_id]
                              if line_id in corridor.radii
                              else corridor.radii[families.get(line_id, line_id)])
                    for line_id in edge_lines[key]
                    if line_id in corridor.radii
                    or families.get(line_id, line_id) in corridor.radii
                }
            else:
                bogen.pop(key, None)
            if corridor.immediate:
                sofort[key] = corridor.shift_at
            else:
                sofort.pop(key, None)
    return overrides, sofort, startspuren, bogen, bezug, mitte


def _compute_bundle_slots(
    layout: LayoutResult,
    line_ids: Iterable[str],
    families: Optional[Mapping[str, str]] = None,
    corridors: Optional[Mapping[str, Corridor]] = None,
) -> Tuple[Dict[frozenset, Dict[str, float]], Dict[frozenset, str],
             Dict[frozenset, Dict[str, float]], Dict[frozenset, Dict[str, float]],
             Dict[frozenset, Tuple[str, float]]]:
    """Weist jeder (Korridorkante, Linie) einen zentrierten Slot-Index zu,
    fuer alle Kanten, die mindestens eine der `line_ids` befaehrt.

    Slots werden pro FAMILIE vergeben, nicht pro Linie: Varianten derselben
    Linie (S2/S25/S26, S1/S15, ...) teilen sich einen Platz und liegen auf
    gemeinsamen Abschnitten uebereinander statt nebeneinander. Ohne
    `families` ist jede Linie ihre eigene Familie.

    Kanten werden zu "Straecken" zusammengefasst, wenn sie an einer
    gemeinsamen Station geradeaus (ohne Richtungswechsel) ineinander
    uebergehen UND von genau derselben Menge an FAMILIEN befahren werden --
    der Slot bleibt dann fuer die ganze Strecke gleich. Endet nur eine
    Variante, waehrend ihre Stammlinie weiterlaeuft, aendert sich die
    Familienmenge nicht und das Buendel bleibt unveraendert. Erst wenn eine
    ganze Familie dazukommt oder terminates_at, beginnt eine neue Strecke und eine
    dann allein verbleibende Familie zentriert sich wieder (Slot 0).

    Der Slot gilt in der gespeicherten Richtung seiner Kante. Innerhalb
    einer Strecke sind die Kanten gleichlaeufig gespeichert (geprueft: 0 von
    133 Vereinigungen gegenlaeufig), ein Slot meint dort also durchgehend
    dieselbe Seite.
    """
    fam = (lambda lid: lid) if families is None else (lambda lid: families.get(lid, lid))

    edge_lines: Dict[frozenset, set] = defaultdict(set)
    edge_families: Dict[frozenset, set] = defaultdict(set)
    for line_id in line_ids:
        for seg in layout.network.parsed[line_id].segments:
            key = _pair_key(seg.a, seg.b)
            edge_lines[key].add(line_id)
            edge_families[key].add(fam(line_id))

    edges = list(edge_lines)

    def endpoint_bearings(key: frozenset, station_id: str) -> Tuple[int, int]:
        """(Ankunfts-, Abfahrts-)Winkel an `station_id` ueber Kante `key`."""
        points = layout.tracks.corridor_paths[key]
        start = layout.measures.coords[station_id]
        if hypot(points[0][0] - start[0], points[0][1] - start[1]) < 1e-6:
            depart = _edge_bearing(points, at_start=True)
            return (depart + 180) % 360, depart
        arrive = _edge_bearing(points, at_start=False)
        return arrive, (arrive + 180) % 360

    station_edges: Dict[str, List[frozenset]] = defaultdict(list)
    for key in edges:
        for station_id in key:
            station_edges[station_id].append(key)

    uf = _UnionFind(edges)
    for station_id, keys in station_edges.items():
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                k1, k2 = keys[i], keys[j]
                if edge_families[k1] != edge_families[k2]:
                    continue
                arrive1, _ = endpoint_bearings(k1, station_id)
                _, depart2 = endpoint_bearings(k2, station_id)
                arrive2, _ = endpoint_bearings(k2, station_id)
                _, depart1 = endpoint_bearings(k1, station_id)
                if arrive1 == depart2 or arrive2 == depart1:
                    uf.union(k1, k2)

    stretch_families: Dict[frozenset, set] = defaultdict(set)
    for key in edges:
        stretch_families[uf.find(key)] |= edge_families[key]

    edge_line_slot: Dict[frozenset, Dict[str, float]] = {}
    for key in edges:
        ordered = sorted(stretch_families[uf.find(key)])
        n = len(ordered)
        slot_of = {name: i - (n - 1) / 2 for i, name in enumerate(ordered)}
        # jede Linie erbt den Slot ihrer Familie -- Varianten liegen dadurch
        # exakt uebereinander
        edge_line_slot[key] = {
            lid: slot_of[fam(lid)] for lid in edge_lines[key]
        }

    # Von Hand definierte Korridore schlagen die automatische Vergabe.
    sofort: Dict[frozenset, str] = {}
    startspuren: Dict[frozenset, Dict[str, float]] = {}
    bogen: Dict[frozenset, Dict[str, float]] = {}
    bezug: Dict[frozenset, Tuple[str, float]] = {}
    mitte: set = set()
    if corridors:
        fam_map = {lid: fam(lid) for lids in edge_lines.values() for lid in lids}
        overrides, sofort, startspuren, bogen, bezug, mitte = _corridor_slot_overrides(
            layout, corridors, edge_lines, fam_map
        )
        edge_line_slot.update(overrides)
    return edge_line_slot, sofort, startspuren, bogen, bezug, mitte


def _offset_points(
    points: Sequence[Pt], slot: float, spacing: float
) -> List[Pt]:
    """Versetzt die Polylinie um `slot * spacing` Gitter-Einheiten sideways.

    Innenpunkte werden auf GEHRUNG gesetzt: die versetzten Beine werden als
    Geraden geschnitten, statt jeden Punkt nur senkrecht zu einem
    Nachbarbein zu schieben. Das ist der Unterschied zwischen einer echten
    Parallelen und einer verzerrten Kopie -- nur so bleibt jedes Bein exakt
    parallel zum Original (und damit oktilinear), und der Knick wandert um
    das noetige Stueck entlang der Fahrtrichtung mit. Genau diese
    Verschiebung laesst die Linie sauber auf die versetzte Fortsetzung
    einschwenken, ohne dass ein Zusatzknick entsteht.

    Fuer den Schnittpunkt gilt mit den Normalen `nu`, `nv` der beiden Beine:
        m = d * (nu + nv) / (1 + nu . nv)
    denn dieses m hat zu beiden versetzten Geraden genau den Abstand d.
    """
    d = slot * spacing
    if d == 0 or len(points) < 2:
        return list(points)

    def normal(a: Pt, b: Pt) -> Pt:
        return COMPASS[(_bearing_of(a, b) + 90) % 360]

    first = normal(points[0], points[1])
    out: List[Pt] = [(points[0][0] + first[0] * d, points[0][1] + first[1] * d)]

    for i in range(1, len(points) - 1):
        nu = normal(points[i - 1], points[i])
        nv = normal(points[i], points[i + 1])
        denom = 1.0 + nu[0] * nv[0] + nu[1] * nv[1]
        if abs(denom) < 1e-9:
            # 180-Grad-Kehre: kein Schnittpunkt, senkrecht ausweichen
            out.append((points[i][0] + nu[0] * d, points[i][1] + nu[1] * d))
            continue
        out.append(
            (
                points[i][0] + d * (nu[0] + nv[0]) / denom,
                points[i][1] + d * (nu[1] + nv[1]) / denom,
            )
        )

    last = normal(points[-2], points[-1])
    out.append((points[-1][0] + last[0] * d, points[-1][1] + last[1] * d))
    return out


def _leg_key(a: Pt, b: Pt) -> Tuple[float, float]:
    """Identitaet EINES physischen Beins auf der Trassenmitte: seine Mitte.

    Alle Linien, die dasselbe Bein befahren, bekommen denselben Schluessel --
    unabhaengig davon, in welcher Richtung sie es durchfahren.
    """
    return (round((a[0] + b[0]) / 2, 6), round((a[1] + b[1]) / 2, 6))


def _canonical_lane(a: Pt, b: Pt, off: float) -> float:
    """Rechnet den Versatz eines Beins zwischen Fahrt- und Bezugsrichtung um.

    `off` gilt entlang der FAHRTrichtung der Linie; zwei gegenlaeufige Linien
    auf demselben Bein haetten also fuer dieselbe Seite verschiedene
    Vorzeichen. Bezugsrichtung ist deshalb die lexikografisch kleinere.

    Die Umrechnung ist ein Vorzeichenwechsel und damit ihre eigene Umkehrung:
    dieselbe Funktion fuehrt vom Fahrt- ins Bezugssystem und zurueck.
    """
    return off if a < b else -off


def _lane_map(
    layout: LayoutResult,
    line_ids: Sequence[str],
    families: Mapping[str, str],
    **legs_kwargs,
) -> Dict[Tuple[float, float], Dict[str, float]]:
    """Je physischem Bein: welche Familie liegt dort auf welcher Spur.

    Grundlage der Nachbarschaftsfrage in `_neighbour_in_curve`. Vergeben wird
    pro FAMILIE, weil Varianten derselben Linie uebereinander liegen und
    fuereinander keine Nachbarn sind.
    """
    lanes: Dict[Tuple[float, float], Dict[str, float]] = defaultdict(dict)
    for lid in line_ids:
        legs, _, offs = _line_legs(layout, lid, **legs_kwargs)
        for k, leg in enumerate(legs):
            a, b = leg[0], leg[1]
            lanes[_leg_key(a, b)][families.get(lid, lid)] = _canonical_lane(a, b, offs[k])
    return lanes


def _vertex_key(p: Pt) -> Tuple[float, float]:
    """Knickpunkt auf der TRASSENMITTE als Schluessel.

    Die Beine in `_line_legs` laufen auf der Mittellinie, nicht auf der
    versetzten Spur -- derselbe Knick hat fuer jede Linie der Kante deshalb
    denselben Punkt, und der taugt als gemeinsamer Schluessel.
    """
    return (round(p[0], 6), round(p[1], 6))


def _radius_targets(
    layout: LayoutResult,
    bezug: Mapping[frozenset, Tuple[str, float]],
    eigene: Mapping[frozenset, Mapping[str, float]],
    mitte: Iterable[frozenset],
    bundle_spacing: float,
    lanes: Optional[Mapping[Tuple[float, float], Mapping[str, float]]] = None,
    families: Optional[Mapping[str, str]] = None,
    **legs_kwargs,
) -> Tuple[Dict[Tuple[float, float], Tuple[float, float]],
           Dict[Tuple[str, Tuple[float, float]], float],
           set]:
    """Knickpunkt -> (Radius der Bezugslinie, ihre Versatzkorrektur).

    Fuer jede Kante mit `Corridor.radius_at` wird die Bezugslinie einmal
    aufgebaut und an ihren Knicken auf dieser Kante festgehalten, wie stark
    `_corner_radius` ihren Radius korrigieren wird. Aus beidem bildet
    `_line_path` den Wert der Mittellinie zurueck.
    """
    # Corridor.radii: (Linie, Knickpunkt) -> Radius, den genau sie zeichnet.
    je_linie: Dict[Tuple[str, Tuple[float, float]], float] = {}
    for key, radien in eigene.items():
        for punkt in layout.tracks.corridor_paths[key]:
            for lid, r in radien.items():
                je_linie[(lid, _vertex_key(punkt))] = r

    families = families or {}
    # Kanten mit `radius_from_centre`, als Knickpunkte
    zentral = {
        _vertex_key(p)
        for key in mitte
        for p in layout.tracks.corridor_paths[key]
    }

    ziele: Dict[Tuple[float, float], Tuple[float, float]] = {}
    for key, (ref_id, ref_r) in bezug.items():
        # Alle Punkte der Kante, auch ihre Enden: ein Knick kann genau auf
        # einer Station sitzen, und dort gehoert er zur Kante, die ihn
        # nennt.
        ecken = {_vertex_key(p) for p in layout.tracks.corridor_paths[key]}
        legs, _, offs = _line_legs(layout, ref_id, **legs_kwargs)
        for k in range(1, len(legs)):
            corner = legs[k][3]
            if corner is None or not _leg_turns(legs, k):
                continue
            punkt = _vertex_key(legs[k][0])
            if punkt not in ecken:
                continue
            sgn = 1 if corner.delta > 0 else -1
            versatz = (offs[k - 1] + offs[k]) / 2 * bundle_spacing
            innerste = (
                _curve_inner(lanes, legs, offs, k, families.get(ref_id, ref_id),
                             bundle_spacing)
                if lanes is not None else sgn * versatz
            )
            ziele[punkt] = (ref_r, sgn * versatz - innerste)
    return ziele, je_linie, zentral


def _curve_inner(
    lanes: Mapping[Tuple[float, float], Mapping[str, float]],
    legs: Sequence[tuple],
    offs: Sequence[float],
    k: int,
    family: str,
    bundle_spacing: float,
) -> float:
    """Wie weit innen liegt die INNERSTE Spur des Buendels in diesem Bogen?

    Gemessen als der Betrag, den `_corner_radius` ihr vom Radius abzoege --
    je groesser, desto weiter innen. Zurueck kommt das Maximum ueber die
    Linie selbst und alle Nachbarfamilien, die den Knick mit
    GLEICHBLEIBENDEM Abstand mitfahren; nur die gehoeren zum selben Buendel
    und muessen konzentrisch bleiben. Wer den Bogen allein faehrt, ist seine
    eigene innerste Spur.

    Damit bekommt die innerste Spur den vollen Default-Radius und jede
    weiter aussen liegende genau so viel mehr, wie sie danebenliegt. Der
    Radius in der Definition ist so eine Untergrenze: enger als er wird
    keine Kurve gezeichnet, auch nicht im Buendel.
    """
    vor_leg, nach_leg = legs[k - 1], legs[k]
    sgn = 1 if (nach_leg[3] and nach_leg[3].delta > 0) else -1
    eigen = (offs[k - 1] + offs[k]) / 2 * bundle_spacing
    innerste = sgn * eigen
    vor = lanes.get(_leg_key(vor_leg[0], vor_leg[1]), {})
    nach = lanes.get(_leg_key(nach_leg[0], nach_leg[1]), {})
    for fremd, lane_vor in vor.items():
        if fremd == family or fremd not in nach:
            continue
        # In die FAHRTrichtung dieser Linie zurueckgerechnet -- das
        # Bezugssystem der Spurkarte haengt am Bein und kippt am Knick.
        abstand_vor = _canonical_lane(*vor_leg[:2], lane_vor) - offs[k - 1]
        abstand_nach = _canonical_lane(*nach_leg[:2], nach[fremd]) - offs[k]
        if abs(abstand_vor - abstand_nach) > 1e-9:
            continue          # kein gleichbleibender Abstand: fremdes Buendel
        innerste = max(innerste, sgn * (eigen + abstand_vor * bundle_spacing))
    return innerste


def _neighbour_in_curve(
    lanes: Mapping[Tuple[float, float], Mapping[str, float]],
    legs: Sequence[tuple],
    offs: Sequence[float],
    k: int,
    family: str,
) -> bool:
    """Laeuft im Bogen zwischen Bein k-1 und k eine andere Familie mit
    GLEICHBLEIBENDEM Abstand daneben?

    Nur dann muessen die Boegen konzentrisch sein, und nur dann ist die
    Versatzkorrektur in `_corner_radius` gerechtfertigt. Massgeblich ist
    nicht, dass der eigene Versatz gleich bleibt, sondern dass der ABSTAND
    zum Nachbarn erhalten bleibt -- das ist auch erfuellt, wenn das ganze
    Buendel am Knick gemeinsam umschwenkt (Adlershof -> Altglienicke: alle
    Spuren um -0.5).

    Faehrt die Linie den Bogen dagegen allein -- oder wechselt sie dort, wie
    S8 und S9 am Treptower Park, von einem Buendel in ein anderes, ohne dabei
    einen durchgehenden Nachbarn zu haben -- gibt es niemanden, zu dem sie
    konzentrisch sein muesste. Die Korrektur wuerde den Bogen dann ohne Grund
    verziehen; er behaelt stattdessen den Default-Radius.
    """
    vor_leg, nach_leg = legs[k - 1], legs[k]
    vor = lanes.get(_leg_key(vor_leg[0], vor_leg[1]), {})
    nach = lanes.get(_leg_key(nach_leg[0], nach_leg[1]), {})
    for fremd, lane_vor in vor.items():
        if fremd == family or fremd not in nach:
            continue
        # Verglichen wird in der FAHRTrichtung DIESER Linie, in die beide
        # Nachbarwerte zurueckgerechnet werden. Das Bezugssystem der Spurkarte
        # haengt am einzelnen Bein und kippt am Knick mit der Beinrichtung --
        # ein darin gemessener Abstand wechselte dort sein Vorzeichen, obwohl
        # der Nachbar unveraendert danebenlaeuft. Die Fahrtrichtung dreht am
        # Knick mit, genau wie der Versatz selbst.
        abstand_vor = _canonical_lane(*vor_leg[:2], lane_vor) - offs[k - 1]
        abstand_nach = _canonical_lane(*nach_leg[:2], nach[fremd]) - offs[k]
        if abs(abstand_vor) > 1e-9 and abs(abstand_vor - abstand_nach) < 1e-9:
            return True
    return False


def _line_legs(
    layout: LayoutResult,
    line_id: str,
    *,
    edge_slots: Optional[Mapping[frozenset, Mapping[str, float]]] = None,
    immediate_edges: Optional[Mapping[frozenset, str]] = None,
    start_slots: Optional[Mapping[frozenset, Mapping[str, float]]] = None,
) -> Tuple[List[tuple], List[Tuple[str, str, int, int]], List[float]]:
    """Die Beine einer Linie in Fahrtreihenfolge, mit endgueltigem Versatz.

    Getrennt von `_line_path`, weil die Nachbarschaftsfrage am Knick die
    Versatzfolge ALLER Linien braucht, bevor die erste gezeichnet wird.

    Der Versatz gilt je BEIN, nicht je Kante, und ein Wechsel wird bis zur
    naechsten Kurve aufgeschoben. Der Eckpunkt ist dann der Schnittpunkt der
    beiden versetzten Beingeraden -- bei gleichem Versatz die gewohnte
    Gehrung, bei unterschiedlichem setzt die Kurve entsprechend spaeter ein
    und EIN Bogen fuehrt von der alten auf die neue Spur. Damit entfaellt
    jeder Sprung im Track.

    Zurueck kommen die Beine (Anfang, Ende, Slot, Knick davor, Lage des
    Sofort-Schwenks), die Stationsspannen und der Versatz je Bein.
    """
    pline = layout.network.parsed[line_id]

    # 1) Beine der ganzen Linie in Fahrtreihenfolge, noch auf der Mittellinie
    # (Anfang, Ende, Slot, Knick davor, Lage des Sofort-Schwenks)
    legs: List[Tuple[Pt, Pt, float, Optional[Corner], Optional[str]]] = []
    spans: List[Tuple[str, str, int, int]] = []   # Station a, b, erstes/letztes Bein
    start_slot: Optional[float] = None            # Corridor.start_offsets
    for seg in pline.segments:
        key = _pair_key(seg.a, seg.b)
        pts = layout.tracks.corridor_paths[key]
        cs = layout.tracks.corridor_corners[key]
        start_pt = layout.measures.coords[seg.a]
        reversed_edge = hypot(pts[0][0] - start_pt[0], pts[0][1] - start_pt[1]) > 1e-6
        if reversed_edge:
            # Die Knicke der Kante sind einmal je Stationspaar gebaut, in der
            # GESPEICHERTEN Richtung. Wer sie rueckwaerts durchfaehrt, sieht
            # aus einem Rechtsknick einen Linksknick -- das Vorzeichen muss
            # mitgedreht werden. Sonst korrigiert `_corner_radius` den Bogen
            # zur falschen Seite, und eine rueckwaerts fahrende Linie
            # bekommt im Buendel den Radius ihres Gegenuebers.
            pts = list(reversed(pts))
            cs = [None if c is None else replace(c, delta=-c.delta)
                  for c in reversed(cs)]
        slot = 0.0
        if edge_slots is not None:
            on_edge = edge_slots.get(key, {})
            slot = on_edge.get(line_id, 0.0)
            # Die Slots sind auf die GESPEICHERTE Kantenrichtung normiert,
            # angewendet wird der Versatz hier aber entlang der FAHRTrichtung.
            # Befaehrt diese Linie die Kante rueckwaerts, muss das Vorzeichen
            # kippen -- sonst landen zwei gegenlaeufige Linien trotz
            # verschiedener Slots auf derselben Seite.
            if reversed_edge:
                slot = -slot
        sofort = (immediate_edges or {}).get(key)
        if not legs and start_slots is not None:
            # Diese Linie faengt hier an. Statt sofort auf die Spur der Kante
            # zu springen, darf sie bis zur ersten Kurve auf einer eigenen
            # Startspur liegen (Corridor.start_offsets).
            wert = start_slots.get(key, {}).get(line_id)
            if wert is not None:
                start_slot = -wert if reversed_edge else wert
        first = len(legs)
        # Beine der Laenge 0 (FixPath(0.0), Knick genau auf der Station)
        # haben keine Richtung. Sie fliegen hier raus; ein Bogen, der an so
        # einem Bein haengt, wandert auf das naechste richtige weiter.
        vererbt: Optional[Corner] = None
        for j in range(len(pts) - 1):
            ecke = cs[j] if j > 0 else None
            if hypot(pts[j + 1][0] - pts[j][0], pts[j + 1][1] - pts[j][1]) < 1e-9:
                vererbt = vererbt or ecke
                continue
            legs.append((
                pts[j], pts[j + 1], slot, ecke or vererbt,
                sofort if len(legs) == first else None,
            ))
            vererbt = None
        if len(legs) == first:
            continue          # Kante bestand nur aus Null-Beinen
        spans.append((seg.a, seg.b, first, len(legs) - 1))

    if not legs:
        return [], [], []

    # 2) Versatz je Bein: Wechsel erst in der naechsten Kurve uebernehmen.
    # `immediate` erlaubt ihn schon an der Kantengrenze; der Bogen dafuer
    # entsteht in `_line_path` als 45-Grad-Versatzknick.
    offs: List[float] = []
    current = legs[0][2] if start_slot is None else start_slot
    for k in range(len(legs)):
        if k > 0 and (_leg_turns(legs, k) or legs[k][4]):
            current = legs[k][2]
        offs.append(current)

    return legs, spans, offs


def _leg_direction(leg: tuple) -> Pt:
    a, b = leg[0], leg[1]
    length = hypot(b[0] - a[0], b[1] - a[1])
    return ((b[0] - a[0]) / length, (b[1] - a[1]) / length)


def _leg_turns(legs: Sequence[tuple], k: int) -> bool:
    """Knickt die Linie zwischen Bein k-1 und k ab?"""
    u, v = _leg_direction(legs[k - 1]), _leg_direction(legs[k])
    return abs(v[0] - u[0]) > 1e-9 or abs(v[1] - u[1]) > 1e-9


def _line_path(
    layout: LayoutResult,
    line_id: str,
    *,
    lanes: Optional[Mapping[Tuple[float, float], Mapping[str, float]]] = None,
    family: str = "",
    bundle_spacing: float = 0.0,
    bezier_shift: bool = False,
    bezier_span: float = 1.0,
    radius_targets: Optional[Mapping[Tuple[float, float], Tuple[float, float]]] = None,
    radius_own: Optional[Mapping[Tuple[str, Tuple[float, float]], float]] = None,
    radius_central: Optional[Iterable[Tuple[float, float]]] = None,
    **legs_kwargs,
) -> LinePath:
    """Rekonstruiert den durchgehenden Streckenzug einer Linie in
    Fahrtreihenfolge samt Knicken und Stationslagen.

    Der Versatz gilt je BEIN, nicht je Kante, und ein Wechsel wird bis zur
    naechsten Kurve aufgeschoben. Der Eckpunkt ist dann der Schnittpunkt der
    beiden versetzten Beingeraden -- bei gleichem Versatz die gewohnte
    Gehrung, bei unterschiedlichem setzt die Kurve entsprechend spaeter ein
    und EIN Bogen fuehrt von der alten auf die neue Spur. Damit entfaellt
    jeder Sprung im Track.

    Nur wenn zwei Beine mit verschiedenem Versatz kollinear sind, gibt es
    keinen Schnittpunkt -- dort bleibt ein 45-Grad-Versatzknick noetig.

    `lanes` (aus `_lane_map`) beantwortet am Knick die Frage, ob ueberhaupt
    ein Nachbar daneben laeuft; ohne Nachbarn behaelt der Bogen seinen
    Default-Radius.
    """
    legs, spans, offs = _line_legs(layout, line_id, **legs_kwargs)
    if not legs:
        return LinePath([], [])

    dirs = [_leg_direction(leg) for leg in legs]
    lens = [hypot(leg[1][0] - leg[0][0], leg[1][1] - leg[0][1]) for leg in legs]

    def turns(k: int) -> bool:
        return _leg_turns(legs, k)

    def normal(u: Pt) -> Pt:
        return (-u[1], u[0])

    def shifted(point: Pt, k: int) -> Pt:
        nx, ny = normal(dirs[k])
        d = offs[k] * bundle_spacing
        return (point[0] + nx * d, point[1] + ny * d)

    points: List[Pt] = [shifted(legs[0][0], 0)]
    corners: List[Optional[Corner]] = [None]
    beziers: List[int] = []

    for k in range(1, len(legs)):
        if not turns(k):
            if abs(offs[k] - offs[k - 1]) < 1e-12:
                points.append(shifted(legs[k][0], k))
                corners.append(None)
                continue
            # Kollinear mit Versatzwechsel: die Linie wechselt auf gerader
            # Strecke die Spur. Der Schwenk laeuft ueber 45 Grad, ist also
            # laengs genau so lang wie der Versatz breit ist, und sitzt in
            # der MITTE des Beins statt an seinem Anfang -- so haengt er
            # nicht an der Station.
            #
            # Die beiden Knicke bekommen einen Bogen, dessen Radius den
            # schraegen Zwischenteil genau ausfuellt: die Tangente ist seine
            # halbe Laenge, also |delta|*sqrt(2)/2, und daraus folgt
            # R = t / tan(22.5 Grad). Ergebnis sind zwei tangentiale
            # Kreisboegen, die zusammen ein sauberes S bilden.
            base = shifted(legs[k - 1][1], k - 1)
            delta = (offs[k] - offs[k - 1]) * bundle_spacing
            ux, uy = dirs[k]
            nx, ny = normal(dirs[k])
            laengs = abs(delta)
            if bezier_shift:
                # Die Bezier darf laenger ausholen, damit sie flach wird --
                # aber nie ueber das Bein hinaus.
                laengs = min(laengs * bezier_span, lens[k])
            # Lage auf dem Bein, in Fahrtrichtung der Linie
            frei = max(lens[k] - laengs, 0.0)
            vor = {"start": 0.0, "end": frei}.get(legs[k][4] or "middle", frei / 2)
            anfang = (base[0] + ux * vor, base[1] + uy * vor)
            ende = (anfang[0] + ux * laengs + nx * delta,
                    anfang[1] + uy * laengs + ny * delta)
            if bezier_shift:
                # Eine kubische Bezier zwischen genau denselben zwei Punkten
                # -- gleicher Platzbedarf wie die Bogenvariante, nur andere
                # Form. Beide Enden sind tangential zur Geraden.
                beziers.append(len(points))
                points.append(anfang)
                corners.append(None)
                points.append(ende)
                corners.append(None)
                continue
            # Zwei tangentiale Kreisboegen ueber den schraegen Zwischenteil.
            # Der Radius ist so gewaehlt, dass die Tangente genau dessen
            # halbe Laenge ist -- dann geht der erste Bogen ohne Gerade
            # dazwischen in den zweiten ueber.
            tangente = laengs * sqrt(2.0) / 2
            radius = tangente / tan(radians(22.5))
            dreh = 45 if delta > 0 else -45
            points.append(anfang)
            corners.append(Corner(delta=dreh, radius=radius, max_tangent=tangente))
            points.append(ende)
            corners.append(Corner(delta=-dreh, radius=radius, max_tangent=tangente))
            continue

        # Kurve: Schnittpunkt der beiden versetzten Beingeraden
        p = shifted(legs[k - 1][0], k - 1)
        q = shifted(legs[k][0], k)
        ux, uy = dirs[k - 1]
        vx, vy = dirs[k]
        cross = ux * vy - uy * vx
        t = ((q[0] - p[0]) * vy - (q[1] - p[1]) * vx) / cross
        corner = legs[k][3]
        if corner is not None:
            # Versatz nur einrechnen, wenn im Bogen wirklich ein Nachbar
            # danebenlaeuft (siehe _neighbour_in_curve). Dann muessen die
            # Boegen konzentrisch sein, sonst liefen sie auseinander. Faehrt
            # die Linie den Bogen allein, verzoege die Korrektur ihn ohne
            # Grund -- sie behaelt den Default-Radius.
            punkt = _vertex_key(legs[k][0])
            gesetzt = (radius_own or {}).get((line_id, punkt))
            bezug = (radius_targets or {}).get(punkt)
            # Massgeblich ist die MITTE aus der Spur vor und hinter dem Knick,
            # nicht die davor. Der Grund ist die Fahrtrichtung: "davor" liegt
            # fuer zwei gegenlaeufige Linien an entgegengesetzten Enden
            # desselben Bogens. Wechselt das Buendel dort die Spur, nimmt jede
            # ihren Wert von ihrer Seite -- und zwei Linien, die durchgehend
            # eine Spur nebeneinander laufen, bekaemen Radien, die um den
            # ganzen Spurwechsel auseinanderliegen statt um eine Spur.
            #
            # Der Mittelwert ist richtungsunabhaengig: rueckwaerts kehren sich
            # beide Summanden um UND der Drehsinn, das Produkt bleibt gleich.
            # Wo eine Linie ihre Spur behaelt, ist er ohnehin ihr Versatz.
            # Der Radius gilt fuer die INNERSTE Spur des Buendels: sie
            # zeichnet ihn unveraendert, jede weiter aussen liegende bekommt
            # genau so viel mehr, wie sie danebenliegt. Faehrt die Linie den
            # Bogen allein, ist sie selbst die innerste und behaelt ihren
            # Wert -- auch wenn ihre Spur neben der Trassenmitte liegt.
            versatz = (offs[k - 1] + offs[k]) / 2 * bundle_spacing
            sgn = 1 if corner.delta > 0 else -1
            if radius_central is not None and punkt in radius_central:
                # `Corridor.radius_from_centre`: die aeltere Lesart -- der
                # Radius gehoert der Trassenmitte, jede Spur weicht um ihren
                # eigenen Versatz davon ab. Nur dort, wo eine Achse ihre
                # gewachsene Form behalten soll.
                parallel = lanes is not None and _neighbour_in_curve(
                    lanes, legs, offs, k, family
                )
                korrektur = versatz if parallel else 0.0
            else:
                innerste = (
                    _curve_inner(lanes, legs, offs, k, family, bundle_spacing)
                    if lanes is not None else sgn * versatz
                )
                korrektur = sgn * (sgn * versatz - innerste)
            if gesetzt is not None:
                # `Corridor.radii`: der Notausgang -- diese Linie zeichnet
                # genau diesen Bogen, ohne Ruecksicht auf die Nachbarn und
                # ohne Deckel aus den Beinlaengen.
                corner = replace(corner, radius=gesetzt, offset=0.0, forced=True)
            elif bezug is not None:
                # `Corridor.radius_at`: der Bogen ist an EINER Linie
                # festgemacht. `bezug` ist ihr Radius plus die Korrektur, die
                # `_corner_radius` bei ihr abziehen wird -- die Summe ist der
                # Wert der Mittellinie. Zieht die eigene Korrektur davon ab,
                # bleibt ein zur Bezugslinie konzentrischer Radius, und fuer
                # sie selbst genau der gesetzte Wert.
                #
                # Die Korrektur gilt hier IMMER, auch ohne Nachbarn im Bogen:
                # ein ausdruecklich gesetzter Radius soll nicht davon
                # abhaengen, wer sonst noch mitfaehrt.
                ref_r, ref_korrektur = bezug
                corner = replace(
                    corner, radius=ref_r + ref_korrektur, offset=korrektur
                )
            else:
                corner = replace(corner, offset=korrektur)
        points.append((p[0] + ux * t, p[1] + uy * t))
        corners.append(corner)

    points.append(shifted(legs[-1][1], len(legs) - 1))
    corners.append(None)

    # Stationslage je Linie: Anfang des ersten bzw. Ende des letzten Beins
    # des jeweiligen Segments, mit dem dort geltenden Versatz.
    stations: Dict[str, Pt] = {}
    lane_changes: Dict[str, Pt] = {}
    for a, b, first, last in spans:
        ein = shifted(legs[first][0], first)
        if a in stations and hypot(stations[a][0] - ein[0],
                                   stations[a][1] - ein[1]) > 1e-9:
            # Die Linie verlaesst die Station auf einer anderen Spur, als sie
            # angekommen ist -- beide Lagen festhalten.
            lane_changes[a] = ein
        stations.setdefault(a, ein)
        stations[b] = shifted(legs[last][1], last)

    return LinePath(points, corners, stations,
                    lane_changes=lane_changes, beziers=beziers)


def build_line_layout(
    layout: LayoutResult,
    line_ids: Iterable[str],
    *,
    bundle_spacing: float = 0.0,
    families: Optional[Mapping[str, str]] = None,
    corridors: Optional[Mapping[str, Corridor]] = None,
    bezier_shift: bool = False,
    bezier_span: float = 1.0,
) -> LineLayout:
    """Phase 4b (Linienfuehrung): Streckenzuege der betrachteten Linien
    bauen und auf gemeinsamen Abschnitten parallel versetzen.

    `line_ids` bestimmt, gegen welche Linien gebuendelt wird -- nur diese
    zaehlen bei der Slot-Vergabe. Uebergibt man alle Linien des Netzes,
    entsteht das vollstaendige Buendelbild; uebergibt man nur die farbig
    hervorgehobenen, bleiben die uebrigen Korridore mittig.

    `bundle_spacing` ist der seitliche Abstand in Gitter-Einheiten; 0
    schaltet den Versatz ab und liefert reine Mittellinien.

    `families` fasst Linienvarianten zusammen (siehe TurnLine.family): sie
    teilen sich einen Slot und laufen auf gemeinsamen Abschnitten
    uebereinander statt nebeneinander.

    `corridors` legt die Spurlage auf einzelnen Abschnitten von Hand fest
    und schlaegt dort die automatische Vergabe (siehe Corridor).
    """
    ids = list(line_ids)
    fams = {lid: (families or {}).get(lid, lid) for lid in ids}
    slots, immediate, startspuren, bogen, bezug, mitte = _compute_bundle_slots(
        layout, ids, fams, corridors
    )
    legs_kwargs = dict(
        edge_slots=slots,
        immediate_edges=immediate,
        start_slots=startspuren,
    )
    # Erst die Spurkarte ueber ALLE Linien, dann zeichnen: ob ein Bogen
    # konzentrisch zu einem Nachbarn liegen muss, laesst sich nicht an einer
    # Linie allein entscheiden.
    lanes = _lane_map(layout, ids, fams, **legs_kwargs)
    ziele, eigene, zentral = _radius_targets(
        layout, bezug, bogen, mitte, bundle_spacing,
        lanes=lanes, families=fams, **legs_kwargs
    )
    paths = {
        line_id: _line_path(
            layout, line_id,
            lanes=lanes,
            family=fams[line_id],
            bundle_spacing=bundle_spacing,
            radius_targets=ziele,
            radius_own=eigene,
            radius_central=zentral,
            bezier_shift=bezier_shift,
            bezier_span=bezier_span,
            **legs_kwargs,
        )
        for line_id in ids
    }
    return LineLayout(paths=paths, slots=slots, families=fams)
