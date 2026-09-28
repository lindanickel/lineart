"""
Linien auf der Grundkarte, in zwei Stufen.

1. Gleislage. Aus den OSM-Gleisen entsteht ein Netz von Trassen: zwischen je
   zwei Stationen der kuerzeste Gleisweg, parallele Gleise zu einer Trasse
   fusioniert (ihre Mitte), jede Trasse aus Geraden und Kreisboegen, Abzweige
   tangential. Das gilt ueberall gleich -- wo Gleise wirklich auseinander-
   laufen, behaelt jedes seine eigene Trasse und seine eigene Kurve.

2. Linien. Jede Linie liegt mit ihrem Versatz (in Spuren, positiv rechts der
   Fahrtrichtung) neben dieser Trasse. Die Reihenfolge im Buendel bestimmt
   die Automatik oder, von Hand, Korridore und Seitenvorgaben. Spurwechsel
   sind Rampen entlang der Trasse; in einer Kurve faehrt die Linie statt-
   dessen die Kurve der Trasse, verschoben auf ihre Spuren davor und
   dahinter.

Ablauf:
    centerline_graph()       Gleise -> Mittelgleis als Graph (centerline.py)
    locate_stations()        Stations-IDs -> Punkt auf der Karte
    route_edges()            Stationspaare -> Linienzug ueber die Gleise
    build_network()          Kanten -> fusionierte Stuecke (Trassen)
    build_tracks()           Trassen: verschweissen, Geraden/Kreisboegen,
                             glatte Stoesse und Verzweigungen
    bundle_slots()           Versatz je Linie und Stueck
    line_paths()             versetzte Linienzuege
    lines_svg()              zeichnen
"""

from __future__ import annotations

import heapq
import re
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cmp_to_key
from math import atan2
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .geometry import path_d, simplify

Pt = Tuple[float, float]
Edge = Tuple[str, str]
Project = Callable[[np.ndarray, np.ndarray], Tuple[np.ndarray, np.ndarray]]


# ==========================================================================
# GLEISGRAPH
# ==========================================================================


@dataclass
class TrackGraph:
    """Gleise als ungerichteter Graph in Kartenpixeln (aus dem Mittelgleis,
    siehe `centerline.centerline_graph`)."""

    ids: np.ndarray                     # Knoten-IDs
    xy: np.ndarray                      # (n, 2) Kartenpixel
    adj: Dict[int, List[Tuple[int, float]]]   # Index -> [(Nachbar, Laenge)]

    def near(self, p: Pt, radius: float) -> Dict[int, float]:
        """Knoten im Umkreis -> Abstand."""
        d = np.hypot(self.xy[:, 0] - p[0], self.xy[:, 1] - p[1])
        return {int(i): float(d[i]) for i in np.nonzero(d <= radius)[0]}

    def distance_to(self, p: Pt) -> float:
        return float(np.min(np.hypot(self.xy[:, 0] - p[0], self.xy[:, 1] - p[1])))

    def shortest_path(
        self, sources: Mapping[int, float], targets: Mapping[int, float],
    ) -> Optional[List[int]]:
        """Dijkstra von mehreren Starts zu mehreren Zielen.

        Start- und Zielknoten tragen Anfangskosten -- so gewinnt der Knoten
        nahe der Station, auch wenn ein entfernterer Knoten den Weg etwas
        abkuerzen wuerde.
        """
        dist: Dict[int, float] = {}
        prev: Dict[int, int] = {}
        heap = [(c, n, -1) for n, c in sources.items()]
        heapq.heapify(heap)
        best, best_node = float("inf"), None
        while heap:
            d, n, p = heapq.heappop(heap)
            if n in dist:
                continue
            if d >= best:
                break
            dist[n] = d
            if p >= 0:
                prev[n] = p
            if n in targets and d + targets[n] < best:
                best, best_node = d + targets[n], n
            for m, w in self.adj.get(n, ()):
                if m not in dist:
                    heapq.heappush(heap, (d + w, m, n))
        if best_node is None:
            return None
        weg = [best_node]
        while weg[-1] in prev:
            weg.append(prev[weg[-1]])
        return weg[::-1]


# ==========================================================================
# STATIONEN
# ==========================================================================


def normalize_name(name: str) -> str:
    """Stationsnamen vergleichbar machen.

    Weg faellt, was OSM und Plan verschieden schreiben: Verkehrsmittel-
    Praefixe ("S+U "), "Berlin-" vorn, Ortszusaetze wie "(b Berlin)" oder
    "(Mark)" hinten, "Hbf" gegen "Hauptbahnhof". Namensteile in Klammern,
    die zum Namen gehoeren ("Messe Sued (Eichkamp)"), bleiben.
    """
    n = name.lower().replace("ß", "ss")
    n = re.sub(r"^(s\+u|s|u)\s+", "", n)
    n = re.sub(r"^berlin[- ]", "", n)
    n = re.sub(r"\s*\((b|bei)\.? [^)]*\)|\s*\(mark\)|\s*\(berlin\)", "", n)
    n = re.sub(r"\bhbf\b", "hauptbahnhof", n)
    return re.sub(r"[^a-z0-9äöü]", "", n)


def locate_stations(
    names: Mapping[str, str],
    stops: Iterable[Mapping],
    graph: TrackGraph,
    project: Project,
    *,
    max_dist: float,
    aliases: Optional[Mapping[str, str]] = None,
) -> Dict[str, Pt]:
    """Station-ID -> Kartenpunkt, ueber den Namen aus OSM-Knoten.

    names     Station-ID -> Name, wie im Plan
    stops     OSM-Knoten mit Namen (Bahnhoefe, Haltepunkte, Halteposi-
              tionen); mehrere pro Station sind die Regel
    max_dist  Kandidaten weiter als das (Pixel) vom Gleisnetz zaehlen
              nicht -- ein gleichnamiger U-Bahnhof am anderen Ende der
              Stadt waere sonst ein Treffer
    aliases   Station-ID -> Name in OSM, wo die Normalisierung nicht reicht

    Der Punkt ist das Mittel der Kandidaten, die nahe am besten liegen
    (innerhalb von max_dist um ihn) -- Halteposition beider Richtungen und
    Bahnhofsknoten mitteln sich zur Bahnsteigmitte.
    """
    aliases = aliases or {}
    kandidaten: Dict[str, List[Pt]] = defaultdict(list)
    for el in stops:
        name = el.get("tags", {}).get("name")
        if name and "lon" in el:
            kandidaten[normalize_name(name)].append((el["lon"], el["lat"]))

    out: Dict[str, Pt] = {}
    fehlend: List[str] = []
    for sid, name in names.items():
        roh = kandidaten.get(normalize_name(aliases.get(sid, name)), [])
        if not roh:
            fehlend.append(f"{sid} ({name})")
            continue
        ll = np.asarray(roh, dtype=float)
        x, y = project(ll[:, 0], ll[:, 1])
        pts = np.column_stack([x, y])
        d = np.array([graph.distance_to(p) for p in pts])
        if d.min() > max_dist:
            fehlend.append(f"{sid} ({name}): {d.min():.0f} px vom Gleis")
            continue
        bester = pts[int(np.argmin(d))]
        nah = pts[(np.hypot(*(pts - bester).T) <= max_dist) & (d <= max_dist)]
        out[sid] = (float(nah[:, 0].mean()), float(nah[:, 1].mean()))
    if fehlend:
        raise ValueError(
            "Stationen ohne Gegenstueck in OSM -- Alias setzen:\n  "
            + "\n  ".join(fehlend)
        )
    return out


# ==========================================================================
# KANTEN ROUTEN
# ==========================================================================


def _resample(pts: np.ndarray, step: float) -> np.ndarray:
    seg = np.hypot(*np.diff(pts, axis=0).T)
    s = np.r_[0.0, np.cumsum(seg)]
    if s[-1] == 0:
        return pts[:1]
    n = max(int(np.ceil(s[-1] / step)), 1)
    t = np.linspace(0.0, s[-1], n + 1)
    return np.column_stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])])


def _arc(pts: np.ndarray) -> np.ndarray:
    return np.r_[0.0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]


def _slice(pts: np.ndarray, s0: float, s1: float) -> np.ndarray:
    """Das Stueck eines Linienzugs zwischen den Bogenlaengen s0 und s1."""
    s = _arc(pts)
    innen = pts[(s > s0) & (s < s1)]
    rand = lambda t: np.array([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])])
    return np.vstack([rand(s0), innen, rand(s1)])


def route_edges(
    edges: Iterable[Edge],
    stations: Mapping[str, Pt],
    graph: TrackGraph,
    *,
    radius: float,
    snap_weight: float = 3.0,
    step: float = 1.0,
) -> Dict[Edge, np.ndarray]:
    """Jede Kante (a, b) -> Linienzug ueber die Gleise von a nach b.

    Gestartet wird an allen Gleisknoten im Umkreis `radius` um die Station,
    mit dem `snap_weight`-fachen Abstand als Anfangskosten. An einem
    Kreuzungsbahnhof mit Gleisen auf zwei Ebenen findet so jede Kante ihre
    eigene Ebene.

    Die Enden bleiben auf dem Gleis und werden NICHT auf den Stationspunkt
    gezogen: der liegt an einem solchen Bahnhof zwischen den Ebenen, neben
    jedem Gleis, und der Haken dorthin wuerde die versetzten Linien eines
    Buendels ueberkreuzen. Abgetastet wird in `step`-Abstaenden.
    """
    out: Dict[Edge, np.ndarray] = {}
    for a, b in edges:
        if (a, b) in out or (b, a) in out:
            continue
        src = {n: d * snap_weight for n, d in graph.near(stations[a], radius).items()}
        dst = {n: d * snap_weight for n, d in graph.near(stations[b], radius).items()}
        weg = graph.shortest_path(src, dst) if src and dst else None
        if weg is None:
            raise ValueError(f"Kein Gleisweg zwischen {a} und {b}")
        out[(a, b)] = _resample(graph.xy[weg], step)
    return out


# ==========================================================================
# STUECKE: GEMEINSAME UND EIGENE STRECKE
# ==========================================================================


@dataclass
class Piece:
    """Ein Streckenstueck; alle Linien darauf liegen im selben Buendel.

    pts         Linienzug von `start` nach `end`
    start, end  Knoten: eine Station (ihre ID) oder eine Trennstelle
    """

    pts: np.ndarray
    start: object
    end: object


@dataclass
class Network:
    """Stuecke und je Linie ihre Folge daraus: (Stueck, +1 vorwaerts /
    -1 rueckwaerts)."""

    pieces: List[Piece]
    lines: Dict[str, List[Tuple[int, int]]]
    # Stationspaar (a, b) -> seine Stuecke in Richtung a -> b
    edges: Dict[Edge, List[Tuple[int, int]]] = field(default_factory=dict)


def _gemeinsam(p: np.ndarray, q: np.ndarray, tol: float, min_cos: float = 0.966) -> float:
    """Wie weit p von seinem Anfang an neben q herlaeuft: naeher als `tol`
    und in dieselbe Richtung. Die Richtung zaehlt, weil an einer Station
    JEDE Kante nahe jeder anderen beginnt -- auch die, die in die
    Gegenrichtung davonfaehrt."""
    d2 = (p[:, None, 0] - q[None, :, 0]) ** 2 + (p[:, None, 1] - q[None, :, 1]) ** 2
    naechst = np.argmin(d2, axis=1)
    d = np.sqrt(d2[np.arange(len(p)), naechst])
    # Richtung ueber +-4 Punkte gemessen: das Gleisrauschen zaehlt nicht,
    # aber die Trennstelle liegt dort, wo ein Gleis anfaengt wegzuschwenken
    # (15 Grad), nicht erst, wo es schon weit weg ist
    def tangenten(a):
        k = 4
        v = a[np.minimum(np.arange(len(a)) + k, len(a) - 1)] - a[np.maximum(np.arange(len(a)) - k, 0)]
        return v
    tp, tq = tangenten(p), tangenten(q)
    tp /= np.maximum(np.hypot(tp[:, 0], tp[:, 1]), 1e-9)[:, None]
    tq /= np.maximum(np.hypot(tq[:, 0], tq[:, 1]), 1e-9)[:, None]
    gleich = np.sum(tp * tq[naechst], axis=1) >= min_cos
    weg = np.nonzero((d > tol) | ~gleich)[0]
    k = weg[0] if len(weg) else len(p)
    return float(_arc(p)[k - 1]) if k > 0 else 0.0


def _mean_track(tracks: Sequence[np.ndarray], n: int = 0) -> np.ndarray:
    """Mittellinie mehrerer ungefaehr paralleler Linienzuege gleicher
    Richtung: jeder wird auf dieselbe Zahl Punkte (nach relativer
    Bogenlaenge) gebracht, dann wird punktweise gemittelt."""
    if len(tracks) == 1:
        return tracks[0]
    n = n or max(len(t) for t in tracks)
    proben = []
    for tr in tracks:
        s = _arc(tr)
        u = np.linspace(0.0, s[-1], n) if s[-1] > 0 else np.zeros(n)
        proben.append(np.column_stack([np.interp(u, s, tr[:, 0]), np.interp(u, s, tr[:, 1])]))
    return np.mean(proben, axis=0)


def _komponenten(ids: Sequence[int], verbunden) -> List[List[int]]:
    rest, out = list(ids), []
    while rest:
        komp, rand = [rest.pop(0)], None
        rand = list(komp)
        while rand:
            i = rand.pop()
            for j in [j for j in rest if verbunden(i, j)]:
                rest.remove(j)
                komp.append(j)
                rand.append(j)
        out.append(komp)
    return out


def build_network(
    lines: Mapping[str, Sequence[str]],
    edges: Mapping[Edge, np.ndarray],
    *,
    tolerance: float,
    min_shared: float = 6.0,
) -> Network:
    """Linien -> Stuecke, geteilt an den echten Trennstellen der Gleise.

    tolerance   so nah nebeneinander (und in dieselbe Richtung) gilt als
                eine Trasse
    min_shared  kuerzer gemeinsam (Pixel) ist Rauschen am Bahnsteig, keine
                gemeinsame Strecke

    Zwei Kanten, die an derselben Station beginnen, fahren oft noch ein
    Stueck auf demselben Gleis: hinter Friedrichsfelde Ost S5 und S7, vor
    Treptower Park die S9 von der Warschauer Strasse und der Ring von
    Ostkreuz. Wie weit, wird gemessen -- solange beide innerhalb
    `tolerance` nebeneinander liegen. Dieses Stueck wird EIN gemeinsames
    Stueck, die Kanten beginnen erst an der Trennstelle ihr eigenes. Bei
    mehr als zwei Kanten entsteht ein Baum: drei zusammen, dann zwei, dann
    jede fuer sich.
    """
    pieces: List[Piece] = []
    # Kante -> Station -> (gemeinsame Stuecke nach aussen, ab wo eigen, Knoten dort)
    enden: Dict[Edge, Dict[str, Tuple[List[int], float, object]]] = defaultdict(dict)

    an: Dict[str, List[Tuple[Edge, np.ndarray]]] = defaultdict(list)
    for (u, v), pts in edges.items():
        an[u].append(((u, v), pts))
        an[v].append(((u, v), pts[::-1]))

    for station, inc in an.items():
        n = len(inc)
        laenge = [float(_arc(p)[-1]) for _, p in inc]
        L = np.zeros((n, n))
        for i in range(n):
            for j in range(i + 1, n):
                pi, pj = inc[i][1], inc[j][1]
                v = min(_gemeinsam(pi, pj, tolerance), _gemeinsam(pj, pi, tolerance))
                # Ein paar Pixel gemeinsam sind Rauschen am Bahnsteig, keine
                # gemeinsame Strecke; und nie die ganze Kante -- dahinter
                # muss jede noch ankommen
                if v < min_shared:
                    v = 0.0
                L[i, j] = L[j, i] = min(v, 0.8 * min(laenge[i], laenge[j]))
        zaehler = [0]

        def teile(ids: List[int], s0: float, knoten: object, stuecke: List[int]) -> None:
            for komp in _komponenten(ids, lambda i, j: L[i, j] > s0):
                if len(komp) == 1:
                    enden[inc[komp[0]][0]][station] = (stuecke, s0, knoten)
                    continue
                s1 = None
                for v in sorted({L[i, j] for i in komp for j in komp if i < j and L[i, j] > s0}):
                    if len(_komponenten(komp, lambda i, j: L[i, j] > v)) > 1:
                        s1 = v
                        break
                zaehler[0] += 1
                trenn = (station, zaehler[0])
                # Eine Trasse: die Mitte aller beteiligten Gleise
                geo = _mean_track([_slice(inc[i][1], s0, s1) for i in komp])
                if s0 == 0.0:
                    geo[0] = inc[komp[0]][1][0]     # exakt am Knoten
                pieces.append(Piece(geo, knoten, trenn))
                teile(komp, s1, trenn, stuecke + [len(pieces) - 1])

        teile(list(range(n)), 0.0, station, [])

    folge_je_kante: Dict[Edge, List[Tuple[int, int]]] = {}
    for (u, v), pts in edges.items():
        pu, su, nu = enden[(u, v)][u]
        pv, sv, nv = enden[(u, v)][v]
        gesamt = float(_arc(pts)[-1])
        folge = [(p, 1) for p in pu]
        if gesamt - su - sv > 1e-6:
            pieces.append(Piece(_slice(pts, su, gesamt - sv), nu, nv))
            folge.append((len(pieces) - 1, 1))
        folge += [(p, -1) for p in reversed(pv)]
        folge_je_kante[(u, v)] = folge

    out: Dict[str, List[Tuple[int, int]]] = {}
    for lid, stationen in lines.items():
        folge: List[Tuple[int, int]] = []
        for a, b in zip(stationen, stationen[1:]):
            if (a, b) in folge_je_kante:
                folge += folge_je_kante[(a, b)]
            else:
                folge += [(p, -d) for p, d in reversed(folge_je_kante[(b, a)])]
        out[lid] = folge
    return Network(pieces=pieces, lines=out, edges=folge_je_kante)


def _oriented(net: Network, p: int, d: int) -> np.ndarray:
    pts = net.pieces[p].pts
    return pts if d > 0 else pts[::-1]


# ==========================================================================
# STUFE 1: GLEISLAGE
# ==========================================================================


def build_tracks(
    net: Network,
    *,
    join: float = 4.0,
    blend: float = 20.0,
    step: float = 1.0,
    simplify_tol: float = 1.5,
    max_radius: float = 400.0,
    lane: float = 0.0,
    joint_reach: float = 10.0,
) -> Network:
    """Stufe 1, Gleislage: Stoesse verschweissen, Strecken zu Geraden und
    Kreisboegen machen, Stoesse und Verzweigungen glatt anschliessen.

    join    Stueckenden, die eine Linie direkt nacheinander befaehrt und die
            naeher als das (Pixel) beieinander liegen, bekommen einen
            gemeinsamen Punkt. Sonst springt die Linie am Stoss um den
            Versatz zweier Gleise -- beim gemeinsamen Stueck gibt das Gleis
            einer Kante die Lage vor, beim anschliessenden das einer anderen
            -- und daraus wird mit dem Buendelversatz ein Knick
    blend   ueber diese Laenge wird ein Stueck zum gemeinsamen Punkt gezogen
    simplify_tol, max_radius
            Vereinfachung und groesster Bogenradius der Trassen (Pixel)
    lane    Spurabstand -- der kleinste Bogen muss das breiteste Buendel
            einer Trasse fassen

    Liefert ein neues Netz; die Folgen der Linien bleiben unveraendert.
    """
    # Stossstellen: (Stueck, 0 = Anfang / 1 = Ende)
    eltern: Dict[Tuple[int, int], Tuple[int, int]] = {}

    def wurzel(k):
        eltern.setdefault(k, k)
        while eltern[k] != k:
            eltern[k] = eltern[eltern[k]]
            k = eltern[k]
        return k

    punkt = lambda k: net.pieces[k[0]].pts[0 if k[1] == 0 else -1]
    for folge in net.lines.values():
        for (p, d), (q, e) in zip(folge, folge[1:]):
            aus = (p, 1 if d > 0 else 0)       # wo die Linie p verlaesst
            ein = (q, 0 if e > 0 else 1)       # wo sie q betritt
            if np.hypot(*(punkt(aus) - punkt(ein))) < join:
                eltern[wurzel(aus)] = wurzel(ein)

    gruppen: Dict[Tuple[int, int], List[Tuple[int, int]]] = defaultdict(list)
    for k in list(eltern):
        gruppen[wurzel(k)].append(k)
    ziel: Dict[Tuple[int, int], np.ndarray] = {}
    for mitglieder in gruppen.values():
        m = np.mean([punkt(k) for k in mitglieder], axis=0)
        for k in mitglieder:
            ziel[k] = m

    neu: List[Piece] = []
    for i, pc in enumerate(net.pieces):
        pts = _resample(pc.pts, step) if len(pc.pts) > 1 else pc.pts
        s = _arc(pts)
        gesamt = s[-1]
        b = min(blend, gesamt / 2) if gesamt > 0 else 0.0
        for ende, gewicht in (
            (0, _smoothstep(1 - s / b) if b else (s == 0).astype(float)),
            (1, _smoothstep(1 - (gesamt - s) / b) if b else (s == gesamt).astype(float)),
        ):
            if (i, ende) in ziel:
                verschub = ziel[(i, ende)] - pts[0 if ende == 0 else -1]
                pts = pts + gewicht[:, None] * verschub
        neu.append(Piece(pts, pc.start, pc.end))
    if simplify_tol > 0:
        neu = _straighten(neu, net, simplify_tol, max_radius, step, lane=lane)
    neu = _smooth_joints(neu, net, reach=joint_reach)
    return Network(pieces=neu, lines=net.lines, edges=net.edges)


def _hermite(p0, m0, p1, m1, n: int) -> np.ndarray:
    u = np.linspace(0.0, 1.0, n)[:, None]
    h00, h10 = 2 * u**3 - 3 * u**2 + 1, u**3 - 2 * u**2 + u
    h01, h11 = -2 * u**3 + 3 * u**2, u**3 - u**2
    return h00 * p0 + h10 * m0 + h01 * p1 + h11 * m1


def _smooth_joints(pieces: List[Piece], net: Network, reach: float) -> List[Piece]:
    """Stossstellen und Verzweigungen glatt machen.

    An jedem Knoten wird das Stueck jedes angeschlossenen Zugs bis `reach`
    Pixel vor dem Knoten ersetzt. Das durchgehende Paar (die beiden Zuege,
    die am geradesten ineinander uebergehen) bekommt eine Hermite-Kurve vom
    Punkt bei `reach` auf dem einen zum Punkt bei `reach` auf dem anderen,
    jeweils in Gleisrichtung; der Knoten rueckt auf deren Mitte. Weitere
    Zuege (Aeste) laufen von dort tangential zum Paar ab und liegen bei
    `reach` wieder auf ihrem Gleis. So gibt es an keinem Stoss einen
    Richtungssprung -- dieselbe Regel fuer jeden Knoten.
    """
    out = [Piece(pc.pts.copy(), pc.start, pc.end) for pc in pieces]
    an: Dict[object, List[Tuple[int, int]]] = defaultdict(list)
    for i, pc in enumerate(out):
        an[pc.start].append((i, 0))
        if pc.end != pc.start:
            an[pc.end].append((i, 1))

    def weg(i: int, ende: int) -> np.ndarray:
        """Zug vom Knoten weg."""
        pts = out[i].pts
        return pts if ende == 0 else pts[::-1]

    def setze(i: int, ende: int, pts_weg: np.ndarray) -> None:
        out[i] = Piece(pts_weg if ende == 0 else pts_weg[::-1], out[i].start, out[i].end)

    def punkt_bei(a: np.ndarray, L: float):
        s = _arc(a)
        L = min(L, s[-1] * 0.45)
        j = int(np.clip(np.searchsorted(s, L), 1, len(a) - 2))
        t = _unit(a[min(j + 2, len(a) - 1)] - a[max(j - 2, 0)])
        return j, a[j], t, s[j]

    # Welche Stuecke an einem Knoten zusammengehoeren: die, die eine Linie
    # direkt nacheinander befaehrt. An einem Kreuzungsbahnhof haengen zwei
    # Strecken am selben Knoten, gehoeren aber nicht zusammen
    folgen = set()
    for folge in net.lines.values():
        for (p, _), (q, _) in zip(folge, folge[1:]):
            if p != q:
                folgen.add(frozenset((p, q)))

    gruppen_je_knoten = []
    for k, enden in an.items():
        ids = sorted({i for i, _ in enden})
        for komp in _komponenten(ids, lambda a, b: frozenset((a, b)) in folgen):
            gruppe = [(i, e) for i, e in enden if i in komp]
            if len(gruppe) >= 2:
                gruppen_je_knoten.append(gruppe)

    for enden in gruppen_je_knoten:
        dat = []
        for i, e in enden:
            a = weg(i, e)
            if len(a) < 6:
                continue
            j, q, t, L = punkt_bei(a, reach)
            dat.append((i, e, a, j, q, t, L))
        if len(dat) < 2:
            continue
        # durchgehendes Paar: am geradesten
        best = None
        for x in range(len(dat)):
            for y in range(x + 1, len(dat)):
                c = float(dat[x][5] @ dat[y][5])       # beide vom Knoten weg
                if best is None or c < best[0]:
                    best = (c, x, y)
        if best[0] > -0.5:                             # kein Durchgang (> 120 Grad Knick)
            continue
        _, x, y = best
        (i1, e1, a1, j1, q1, t1, L1), (i2, e2, a2, j2, q2, t2, L2) = dat[x], dat[y]
        chord = float(np.hypot(*(q2 - q1)))
        n = max(int(np.ceil(chord)), 4)
        kurve = _hermite(q1, -t1 * chord, q2, t2 * chord, 2 * n + 1)   # q1 -> q2
        mitte = kurve[n]
        t_mitte = _unit(kurve[n + 1] - kurve[n - 1])                   # Richtung q1 -> q2
        # Paar: Zug 1 vom Knoten weg = Kurve rueckwaerts von der Mitte bis q1
        setze(i1, e1, np.vstack([kurve[n::-1], a1[j1 + 1:]]))
        setze(i2, e2, np.vstack([kurve[n:], a2[j2 + 1:]]))
        # Aeste
        for z, (i, e, a, j, q, t, L) in enumerate(dat):
            if z in (x, y):
                continue
            t0 = t_mitte if float(t_mitte @ t) >= 0 else -t_mitte
            ch = float(np.hypot(*(q - mitte)))
            m = max(int(np.ceil(ch)), 4)
            ast = _hermite(mitte, t0 * ch, q, t * ch, m + 1)
            setze(i, e, np.vstack([ast, a[j + 1:]]))
    return out


def _straighten(pieces: List[Piece], net: Network, tol: float, max_radius: float,
                step: float, corner_tol: float = 1.5, lane: float = 0.0) -> List[Piece]:
    """Strecken aus Geraden und Kreisboegen.

    Stuecke werden zu Strecken verkettet, solange an einem Knoten genau zwei
    Stuecke zusammenstossen, und jede Strecke als Ganzes vereinfacht
    (Douglas-Peucker, Toleranz tol) und mit Kreisboegen ausgerundet. Danach
    wird sie an den alten Stossstellen wieder in Stuecke geteilt -- alle
    Linien einer Strecke bekommen so dieselbe Geometrie und bleiben parallel.
    """
    an: Dict[object, List[Tuple[int, int]]] = defaultdict(list)
    for i, pc in enumerate(pieces):
        an[pc.start].append((i, 0))
        an[pc.end].append((i, 1))
    besucht = [False] * len(pieces)
    out = list(pieces)

    # Durchgaenge an jedem Knoten: Paare von Stuecken, die eine Linie direkt
    # nacheinander befaehrt und die fast geradeaus ineinander uebergehen. Eine
    # Strecke laeuft durch einen Knoten, wenn er zwei Stuecke hat oder wenn
    # sie ein solches Paar bilden -- abzweigende Aeste haengen sich an
    def richtung_weg(i: int, k) -> np.ndarray:
        a = pieces[i].pts if pieces[i].start == k else pieces[i].pts[::-1]
        j = min(len(a) - 1, max(1, int(np.searchsorted(_arc(a), 8.0))))
        return _unit(a[j] - a[0])

    linien_auf: Dict[int, set] = defaultdict(set)
    for lid, folge in net.lines.items():
        for p, _ in folge:
            linien_auf[p].add(lid)
    folgen = set()
    for folge in net.lines.values():
        for (p, _), (q, _) in zip(folge, folge[1:]):
            folgen.add(frozenset((p, q)))
    partner: Dict[Tuple[object, int], int] = {}
    for k, liste in an.items():
        stuecke = sorted({i for i, _ in liste})
        if len(stuecke) == 2:
            a, b = stuecke
            partner[(k, a)], partner[(k, b)] = b, a
            continue
        kand = []
        for x in range(len(stuecke)):
            for y in range(x + 1, len(stuecke)):
                a, b = stuecke[x], stuecke[y]
                if frozenset((a, b)) not in folgen:
                    continue
                w = float(np.degrees(np.arccos(np.clip(-richtung_weg(a, k) @ richtung_weg(b, k), -1, 1))))
                # dieselben Linien auf beiden Seiten: eindeutig eine Strecke,
                # auch in einer Kurve an der Station
                gleich = linien_auf[a] == linien_auf[b]
                if w < (60 if gleich else 25):
                    # zuerst das Paar mit den meisten gemeinsamen Linien --
                    # das Hauptbuendel laeuft durch, Aeste haengen sich an
                    geteilt = len(linien_auf[a] & linien_auf[b])
                    kand.append((-geteilt, w, a, b))
        for _, w, a, b in sorted(kand):
            if (k, a) not in partner and (k, b) not in partner:
                partner[(k, a)], partner[(k, b)] = b, a

    def weiter(k, i):
        j = partner.get((k, i))
        if j is None:
            return None
        return (j, 0 if pieces[j].start == k else 1)

    lage: Dict[object, np.ndarray] = {}
    for start in range(len(pieces)):
        if besucht[start]:
            continue
        # zum Anfang der Kette laufen
        i, rueck = start, pieces[start].start
        gesehen = {start}
        while True:
            nb = weiter(rueck, i)
            if nb is None or nb[0] in gesehen:
                break
            j, seite = nb
            gesehen.add(j)
            i = j
            rueck = pieces[j].end if seite == 0 else pieces[j].start
        # vorwaerts sammeln: (Stueck, vorwaerts?)
        kette = []
        k = rueck
        while True:
            seite = 0 if pieces[i].start == k else 1
            kette.append((i, seite == 0))
            besucht[i] = True
            k = pieces[i].end if seite == 0 else pieces[i].start
            nb = weiter(k, i)
            if nb is None or besucht[nb[0]]:
                break
            i = nb[0]
        zuege = [pieces[i].pts if vw else pieces[i].pts[::-1] for i, vw in kette]
        if sum(len(z) for z in zuege) < 4:
            continue
        voll = np.vstack([zuege[0]] + [z[1:] for z in zuege[1:]])
        grenzen = np.cumsum([len(z) - 1 for z in zuege])[:-1]
        stosspunkte = voll[grenzen]
        poly = simplify(voll, tol)
        # Mindestradius: das Buendel muss um den Bogen passen, sonst hat die
        # innerste Linie einen Radius nahe null und macht eine Ecke
        zahl = max(len(linien_auf[i]) for i, _ in kette)
        # aeusserster Versatz plus zwei Spuren fuer die innerste Linie
        r_min = ((zahl - 1) / 2 + 2) * lane if lane else 0.0
        glatt = _arcs_polyline(poly, max_radius=max_radius, tol=corner_tol, step=step,
                               track=voll, min_radius=r_min)
        # an den Stossstellen wieder teilen (naechster Punkt, der Reihe nach)
        schnitte, ab = [], 0
        for q in stosspunkte:
            d = np.hypot(*(glatt[ab:] - q).T)
            j = ab + int(np.argmin(d))
            schnitte.append(max(j, ab + 1))
            ab = schnitte[-1]
        # Nachbarn teilen sich den Punkt an der Stossstelle
        kanten = [0] + schnitte + [len(glatt) - 1]
        for (i, vw), lo, hi in zip(kette, kanten, kanten[1:]):
            teil = glatt[lo:hi + 1]
            out[i] = Piece(teil if vw else teil[::-1], pieces[i].start, pieces[i].end)
        # neue Lage der inneren Knoten dieser Strecke
        for (i, vw), hi in zip(kette[:-1], kanten[1:-1]):
            k = pieces[i].end if vw else pieces[i].start
            lage[k] = glatt[hi]

    # Abzweigende Aeste an die neue Lage ihres Knotens anschliessen
    for i, pc in enumerate(out):
        pts = pc.pts
        for ende, k in ((0, pc.start), (1, pc.end)):
            if k not in lage:
                continue
            ziel = lage[k]
            v = ziel - (pts[0] if ende == 0 else pts[-1])
            if np.hypot(*v) < 1e-9:
                continue
            sa = _arc(pts)
            b = min(30.0, sa[-1] / 2) or 1.0
            w = _smoothstep(1 - sa / b) if ende == 0 else _smoothstep(1 - (sa[-1] - sa) / b)
            pts = pts + w[:, None] * v
        out[i] = Piece(pts, pc.start, pc.end)
    return out


def _smooth(pts: np.ndarray, window: int, cyclic: bool = False) -> np.ndarray:
    """Gleitendes Mittel; Anfang und Ende bleiben fest -- ausser im Kreis,
    da laeuft das Mittel ueber die Naht hinweg."""
    if window < 2 or len(pts) <= window:
        return pts
    k = np.ones(window) / window
    pad = window // 2
    if cyclic:
        ext = np.vstack([pts[-pad:], pts, pts[:pad]])
    else:
        ext = np.vstack([np.repeat(pts[:1], pad, 0), pts, np.repeat(pts[-1:], pad, 0)])
    out = np.column_stack([
        np.convolve(ext[:, 0], k, mode="valid"),
        np.convolve(ext[:, 1], k, mode="valid"),
    ])[: len(pts)]
    if not cyclic:
        out[0], out[-1] = pts[0], pts[-1]
    return out


def _circle_arc(p1: np.ndarray, e_in: np.ndarray, e_out: np.ndarray, r: float,
                step: float = 1.0) -> np.ndarray:
    """Kreisbogen ab p1, Anfangsrichtung e_in, Endrichtung e_out, Radius r."""
    cross = e_in[0] * e_out[1] - e_in[1] * e_out[0]
    theta = atan2(cross, float(e_in @ e_out))
    seite = 1.0 if cross > 0 else -1.0
    # Mittelpunkt: links/rechts von e_in (y nach unten: rechts = (-y, x))
    nrm = np.array([-e_in[1], e_in[0]]) * seite
    m = p1 + nrm * r
    a0 = atan2(p1[1] - m[1], p1[0] - m[0])
    k = max(int(np.ceil(abs(theta) * r / step)), 2)
    w = a0 + np.linspace(0.0, theta, k + 1)
    return np.column_stack([m[0] + r * np.cos(w), m[1] + r * np.sin(w)])


def _unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.hypot(*v)), 1e-12)


def _corner_radius(theta: float, tol: float, max_radius: float) -> float:
    """Groesster Radius, dessen Bogen hoechstens `tol` an der Ecke vorbeigeht."""
    sek = 1.0 / np.cos(theta / 2) - 1.0
    return max_radius if sek <= 1e-9 else min(max_radius, tol / sek)


def _line_intersection(p1, p2, q1, q2) -> Optional[np.ndarray]:
    r, u = p2 - p1, q2 - q1
    nen = r[0] * u[1] - r[1] * u[0]
    if abs(nen) < 1e-9:
        return None
    t = ((q1 - p1)[0] * u[1] - (q1 - p1)[1] * u[0]) / nen
    return p1 + t * r


def _merge_tight_corners(poly: np.ndarray, r_min: float) -> np.ndarray:
    """Benachbarte Ecken mit gleicher Drehrichtung zusammenlegen, solange
    die Gerade zwischen ihnen zu kurz ist fuer zwei Boegen mit r_min.

    Die neue Ecke liegt im Schnittpunkt der beiden aeusseren Geraden -- aus
    zwei engen Knicken wird eine Ecke mit einem Bogen, der gross genug ist.
    """
    pts = [np.asarray(p, dtype=float) for p in poly]
    while len(pts) >= 4:
        besser = None
        for k in range(1, len(pts) - 2):
            a, b, c, d = pts[k - 1], pts[k], pts[k + 1], pts[k + 2]
            e0, e1, e2 = _unit(b - a), _unit(c - b), _unit(d - c)
            x1 = e0[0] * e1[1] - e0[1] * e1[0]
            x2 = e1[0] * e2[1] - e1[1] * e2[0]
            if x1 * x2 <= 0:
                continue                  # S-Kurve: nicht zusammenlegbar
            t1 = abs(atan2(x1, float(e0 @ e1)))
            t2 = abs(atan2(x2, float(e1 @ e2)))
            bedarf = r_min * (np.tan(t1 / 2) + np.tan(t2 / 2))
            laenge = float(np.hypot(*(c - b)))
            if bedarf <= laenge or t1 + t2 >= np.pi * 0.95:
                continue
            x = _line_intersection(a, b, c, d)
            if x is None:
                continue
            mangel = bedarf - laenge
            if besser is None or mangel > besser[0]:
                besser = (mangel, k, x)
        if besser is None:
            break
        _, k, x = besser
        pts = pts[:k] + [x] + pts[k + 2:]
    return np.array(pts)


def _arcs_polyline(poly: np.ndarray, *, max_radius: float, tol: float,
                   step: float = 1.0, track: Optional[np.ndarray] = None,
                   curve_cost: float = 12.0, max_dev: float = 3.0,
                   min_radius: float = 0.0) -> np.ndarray:
    """Polylinie -> Geraden und Kreisboegen, dicht abgetastet.

    Jede Ecke bekommt den groessten Kreisbogen, der hoechstens `tol` neben
    dem echten Gleis `track` liegt (bis max_radius) -- ohne Gleis: der
    hoechstens `tol` an der Ecke vorbeigeht. Reicht eine Gerade nicht fuer die
    Boegen an ihren beiden Enden, teilen die beiden sie sich -- aus einer
    Gleiskurve wird so eine durchgehende Folge von Kreisboegen. Anfang und
    Ende bleiben fest.
    """
    if min_radius > 0:
        poly = _merge_tight_corners(poly, min_radius)
    n = len(poly)
    if n < 3:
        return _resample(poly, step) if n > 1 else poly
    seg = np.hypot(*np.diff(poly, axis=0).T)
    theta = np.zeros(n)
    e = [_unit(poly[i + 1] - poly[i]) for i in range(n - 1)]
    for i in range(1, n - 1):
        theta[i] = abs(atan2(e[i - 1][0] * e[i][1] - e[i - 1][1] * e[i][0], float(e[i - 1] @ e[i])))
    d = np.zeros(n)
    for i in range(1, n - 1):
        if theta[i] <= 1e-4:
            continue
        tan2 = np.tan(theta[i] / 2)
        # Die ganze angrenzende Gerade darf genutzt werden; teilen muessen
        # sich zwei Boegen sie erst unten
        d_max = min(seg[i - 1], seg[i])
        if track is None:
            d[i] = min(_corner_radius(theta[i], tol, max_radius) * tan2, d_max)
            continue
        # Radius mit der kleinsten Abweichung vom echten Gleis: ein Bogen zu
        # klein laesst die Ecke stehen, einer zu gross schneidet die Kurve
        nah = track[np.hypot(*(track - poly[i]).T) <= d_max * 1.2 + 3]
        if not len(nah):
            d[i] = min(_corner_radius(theta[i], tol, max_radius) * tan2, d_max)
            continue
        # Abwaegen: Abweichung vom Gleis gegen enge Boegen. Ein kleiner
        # Radius kostet `curve_cost / r` Pixel -- er muss sich durch bessere
        # Gleistreue lohnen. Mehr als `max_dev` darf keiner abweichen
        best = (np.inf, 0.0)
        dd = min(d_max, max_radius * tan2)
        while dd > 0.3:
            r = dd / tan2
            bogen = _circle_arc(poly[i] - e[i - 1] * dd, e[i - 1], e[i], r, 0.7)
            fehler = np.sqrt(((bogen[:, None, :] - nah[None, :, :]) ** 2).sum(-1)).min(1).max()
            kosten = (fehler + curve_cost / r + (1e3 if fehler > max_dev else 0.0)
                      + (1e4 * (min_radius - r) if r < min_radius else 0.0))
            if kosten < best[0]:
                best = (kosten, dd)
            dd *= 0.85
        d[i] = best[1]
    # Tangentenlaengen an den Geraden begrenzen
    for _ in range(4):
        for k in range(n - 1):
            summe = d[k] + d[k + 1]
            if summe > seg[k] and summe > 0:
                f = seg[k] / summe
                d[k] *= f
                d[k + 1] *= f
    pts = [poly[0]]
    for i in range(1, n - 1):
        if d[i] <= 1e-6 or theta[i] <= 1e-4:
            pts.append(poly[i])
            continue
        r = d[i] / np.tan(theta[i] / 2)
        pts.extend(_circle_arc(poly[i] - e[i - 1] * d[i], e[i - 1], e[i], r, step / 2))
    pts.append(poly[-1])
    return _resample(np.array(pts), step)


def _cut_loops(pts: np.ndarray, window: int = 80) -> np.ndarray:
    """Schlaufen aus einem Linienzug schneiden.

    Auf der Innenseite einer spitzen Ecke schneidet ein versetzter Zug sich
    selbst. Die Schlaufe zwischen den beiden sich schneidenden Segmenten
    faellt weg, der Schnittpunkt wird zur Ecke.
    """
    n = len(pts) - 1
    if n < 3:
        return pts
    a, b = pts[:-1], pts[1:]
    d = b - a
    treffer: Dict[int, Tuple[int, np.ndarray]] = {}
    for k in range(2, min(window, n)):
        i = np.arange(n - k)
        j = i + k
        r, s_ = d[i], d[j]
        nenner = r[:, 0] * s_[:, 1] - r[:, 1] * s_[:, 0]
        q = a[j] - a[i]
        with np.errstate(divide="ignore", invalid="ignore"):
            tt = (q[:, 0] * s_[:, 1] - q[:, 1] * s_[:, 0]) / nenner
            uu = (q[:, 0] * r[:, 1] - q[:, 1] * r[:, 0]) / nenner
        ok = (nenner != 0) & (tt > 0) & (tt < 1) & (uu > 0) & (uu < 1)
        for ii in np.nonzero(ok)[0]:
            treffer[int(ii)] = (int(j[ii]), a[ii] + tt[ii] * r[ii])
    if not treffer:
        return pts
    out, i = [], 0
    while i <= n:
        out.append(pts[i])
        if i in treffer:
            j, x = treffer[i]
            out.append(x)
            i = j + 1
            continue
        i += 1
    return np.array(out)


def _ecken(pts: np.ndarray, radius: float, step: float = 1.0) -> List[int]:
    """Indizes der Ecken eines Linienzugs: Stellen, an denen er enger dreht
    als `radius`, je zusammenhaengender Stelle die staerkste Drehung."""
    k = max(int(round(2.0 / step)), 1)          # Drehung ueber 2k Schritte
    if len(pts) < 8 * k:
        return []
    d = np.diff(pts, axis=0)
    h = np.unwrap(np.arctan2(d[:, 1], d[:, 0]))
    dreh = np.abs(h[2 * k:] - h[:-2 * k])
    eng = np.nonzero(dreh > (2 * k * step) / radius)[0]
    if not len(eng):
        return []
    ecken, lauf = [], [eng[0]]
    for i in eng[1:]:
        if i - lauf[-1] > k:
            ecken.append(int(max(lauf, key=lambda x: dreh[x])) + k)
            lauf = []
        lauf.append(i)
    ecken.append(int(max(lauf, key=lambda x: dreh[x])) + k)
    return ecken


def _round_corners(paths: Dict[str, np.ndarray], radius: float, reach: float,
                   step: float = 1.0, forks: Sequence[np.ndarray] = (),
                   fork_radius: float = 0.0) -> Dict[str, np.ndarray]:
    """Ecken aller Linien ausrunden, Buendel fuer Buendel gleich.

    Erst werden die Ecken aller Linien gesammelt. Dann wird jede Linie, die
    naeher als `reach` an einer Ecke vorbeifuehrt, dort geglaettet (Gauss,
    Breite nach `radius`), und die Glaettung laeuft zu beiden Seiten weich
    aus. Weil jede Linie eines Buendels dieselbe Glaettung an derselben
    Stelle bekommt, bleiben die Linien parallel -- auch die aeussere, die
    selbst gar keine Ecke hatte. Ein echter Gleisbogen dreht weiter als
    `radius` und bleibt, wie er ist.

    `forks` sind Gabelungen, an denen eine Linie mit einem Knick aus einem
    Buendel abgeht oder dazukommt; dort wird zuerst mit `fork_radius`
    ausgerundet -- weicher, und fuer die Linien, die dort nachruecken,
    gleich mit.
    """
    zuege = {lid: _resample(p, step) for lid, p in paths.items()}
    if len(forks) and fork_radius > 0:
        zuege = _glaetten_an(zuege, np.array(forks), fork_radius, reach, step)
    for _ in range(3):
        stellen = [zuege[lid][i] for lid in zuege for i in _ecken(zuege[lid], radius, step)]
        if not stellen:
            break
        zuege = _glaetten_an(zuege, np.array(stellen), radius, reach, step)
    return zuege


def _glaetten_an(zuege: Dict[str, np.ndarray], stellen: np.ndarray,
                 radius: float, reach: float, step: float) -> Dict[str, np.ndarray]:
    """Ein Durchgang von `_round_corners`: jede Linie an den Stellen
    glaetten, an denen sie naeher als `reach` vorbeifuehrt."""
    sigma = max(radius / 2 / step, 1.0)
    rr = int(np.ceil(3 * sigma))
    kern = np.exp(-0.5 * (np.arange(-rr, rr + 1) / sigma) ** 2)
    kern /= kern.sum()
    out = {}
    for lid, q in zuege.items():
        s = _arc(q)
        w = np.zeros(len(q))
        for x in stellen:
            dist = np.hypot(*(q - x).T)
            i = int(np.argmin(dist))
            if dist[i] > reach:
                continue
            w = np.maximum(w, _smoothstep(1 - (np.abs(s - s[i]) - radius / 2) / (1.5 * radius)))
        if not w.any():
            out[lid] = q
            continue
        rand = np.vstack([np.repeat(q[:1], rr, 0), q, np.repeat(q[-1:], rr, 0)])
        glatt = np.column_stack([np.convolve(rand[:, 0], kern, mode="valid"),
                                 np.convolve(rand[:, 1], kern, mode="valid")])
        w[0] = w[-1] = 0.0                       # Enden bleiben fest
        out[lid] = _resample(q + w[:, None] * (glatt - q), step)
    return out


def _smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ==========================================================================
# STUFE 2: LINIEN
# ==========================================================================


def _eintrag(folge: Sequence[Tuple[int, int]], i: int, ring: bool):
    if ring:
        return folge[i % len(folge)]
    return folge[i] if 0 <= i < len(folge) else None


def _richtung(pts: np.ndarray, laenge: float, vom_ende: bool) -> Optional[np.ndarray]:
    """Richtung der ersten (bzw. letzten) `laenge` Pixel eines Linienzugs."""
    s = _arc(pts)
    if s[-1] == 0:
        return None
    if vom_ende:
        k = np.searchsorted(s, s[-1] - laenge)
        return pts[-1] - pts[max(min(k, len(pts) - 2), 0)]
    k = np.searchsorted(s, laenge)
    return pts[min(max(k, 1), len(pts) - 1)] - pts[0]


@dataclass(frozen=True)
class Corridor:
    """Ein Streckenabschnitt mit fest vorgegebener Spurlage, wie in netmap.

    steps    Stationsfolge; die Schreibrichtung bestimmt das Vorzeichen
    offsets  Linie -> Versatz in Spuren, positiv RECHTS der Schreibrichtung.
             0 liegt auf dem Gleis. Ungerade Werte sind erlaubt (-0.5, +0.5)

    Gilt auf jedem Stueck zwischen zwei aufeinanderfolgenden Stationen, auf
    dem mindestens zwei Linien fahren -- eine Linie allein bleibt mittig auf
    dem Gleis. Liegt ein Stueck in zwei Korridoren, gilt der zuerst
    genannte. Linien, die dort fahren, aber nicht genannt sind, legt die
    Automatik aussen daneben, auf der Seite, auf die sie ohnehin gehoeren.

    complete  gilt nur auf Stuecken, auf denen alle genannten Linien fahren
              -- fuer das gemeinsame Stueck vor einer Verzweigung, das zur
              selben Kante gehoert wie das dahinter
    """

    steps: Sequence[str]
    offsets: Mapping[str, float]
    complete: bool = False


@dataclass(frozen=True)
class Beside:
    """Eine Seitenvorgabe (`left_of`/`right_of`), die nur abschnittsweise gilt.

    line     die andere Linie
    along    gilt nur, wo mindestens eine dieser Linien mitfaehrt
    apart    gilt nur, wo keine dieser Linien mitfaehrt
    at       gilt nur auf Stuecken, die an einer dieser Stationen beginnen
             oder enden (auch an ihren Trennstellen) -- eine Reihenfolge,
             die nur an einem Knoten gelten soll

    So wechselt eine Linie die Seite genau dort, wo ein Buendel beginnt: die
    S47 liegt am Ring links der S46, auf der Goerlitzer Bahn -- zusammen mit
    S8, S85 und S9 -- rechts, und die beiden kreuzen sich, wo die
    Goerlitzer Bahn erreicht ist.
    """

    line: str
    along: Tuple[str, ...] = ()
    apart: Tuple[str, ...] = ()
    at: Tuple[str, ...] = ()

    def gilt(self, spuren_hier, spur, piece: Optional["Piece"] = None) -> bool:
        if self.at and piece is not None:
            station = lambda k: k[0] if isinstance(k, tuple) else k
            if not {station(piece.start), station(piece.end)} & set(self.at):
                return False
        da = {spur(l) for l in self.along}, {spur(l) for l in self.apart}
        if self.along and not (da[0] & set(spuren_hier)):
            return False
        return not (da[1] & set(spuren_hier))


def _winkel(d: np.ndarray, b: np.ndarray) -> float:
    """Abbiegewinkel von d nach b; positiv = rechts (y zeigt nach unten)."""
    return atan2(d[0] * b[1] - d[1] * b[0], d[0] * b[0] + d[1] * b[1])


def bundle_slots(
    net: Network,
    *,
    tracks: Optional[Mapping[str, str]] = None,
    rings: Iterable[str] = (),
    probe: float = 12.0,
    min_run: float = 60.0,
    cross_run: float = 40.0,
    left_of: Optional[Mapping[str, Iterable[str]]] = None,
    right_of: Optional[Mapping[str, Iterable[str]]] = None,
    corridors: Iterable[Corridor] = (),
) -> Dict[str, np.ndarray]:
    """Stufe 2: Linie -> Slot je Stueck ihrer Folge, positiv rechts der
    Fahrtrichtung, 0 = auf der Trasse.

    tracks  Linie -> Spur; Linien mit derselben Spur liegen uebereinander
            (ein zeitweiser Ast auf der Spur seiner Linie). Ohne Angabe hat
            jede Linie ihre eigene
    rings   Linien, die im Kreis fahren -- ihre Folge laeuft beim Vergleich
            ueber das Ende hinaus weiter
    probe   wie weit (Pixel) um eine Trennstelle die Richtung gemessen wird
    min_run kuerzer (Pixel) darf ein Seitenwechsel zweier Linien nicht
            dauern, wenn sie danach gleich wieder zuruecktauschen
    cross_run
            kreuzen sich zwei Linien auf einem gemeinsamen Lauf, der
            kuerzer ist als das (Pixel), buendeln sie dort nicht, sondern
            kreuzen sich als X
    left_of Vorgabe von Hand: Linie -> Linien, links von denen sie liegt,
            in IHRER Fahrtrichtung, auf jedem Stueck, das sie sich teilen.
            Schlaegt die automatische Wahl; wo sich die Gleise dann
            trennen, kreuzen die beiden eben dort. Statt einer Linie darf
            ein `Beside` stehen: dann gilt die Vorgabe nur auf Stuecken, auf
            denen bestimmte andere Linien mitfahren (oder fehlen)
    right_of
            dasselbe fuer rechts -- noetig fuer gegenlaeufige Linien wie
            den Ring, wo jede in IHRER Richtung rechts der anderen liegt
    corridors
            feste Spurlagen von Hand, siehe `Corridor`
    """
    tracks = dict(tracks or {})
    rings = set(rings)
    spur = lambda lid: tracks.get(lid, lid)
    auf: Dict[int, Dict[str, Tuple[str, int]]] = defaultdict(dict)
    for lid, folge in net.lines.items():
        for i, (p, _) in enumerate(folge):
            auf[p].setdefault(spur(lid), (lid, i))

    def vergleich(la, ia, sa, lb, ib, sb) -> int:
        """+1: a liegt rechts von b, in Gehrichtung; 0: unentschieden."""
        fa, fb = net.lines[la], net.lines[lb]
        ra, rb = la in rings, lb in rings
        grenze = len(fa) + len(fb)
        for k in range(1, grenze + 1):
            ea, eb = _eintrag(fa, ia + k * sa, ra), _eintrag(fb, ib + k * sb, rb)
            if ea is None or eb is None:
                return 0
            # effektive Richtung: Eintragsrichtung mal Gehrichtung
            ea_, eb_ = (ea[0], ea[1] * sa), (eb[0], eb[1] * sb)
            if ea_ != eb_:
                vor = _eintrag(fa, ia + (k - 1) * sa, ra)
                d = _richtung(_oriented(net, vor[0], vor[1] * sa), probe, True)
                ba = _richtung(_oriented(net, *ea_), probe, False)
                bb = _richtung(_oriented(net, *eb_), probe, False)
                if d is None or ba is None or bb is None:
                    return 0
                wa, wb = _winkel(d, ba), _winkel(d, bb)
                return (wa > wb) - (wa < wb)
        return 0

    # 1. Paarweise: rel[p][(a, b)] = +1, wenn Spur a auf Stueck p rechts von
    #    b liegt (in Richtung start -> end des Stuecks)
    rel: Dict[int, Dict[Tuple[str, str], int]] = {}
    bezug_von: Dict[int, int] = {}
    # Paare, die sich auf dem Stueck kreuzen: vorwaerts und rueckwaerts
    # gesehen liegen sie verschieden herum
    kreuzt: Dict[int, set] = defaultdict(set)
    for p, spuren in auf.items():
        # Unentschieden (zwei Linien, die sich nie trennen -- der Ring in
        # beiden Richtungen) entscheidet der Name, aber in Fahrtrichtung der
        # ersten Spur: in der gespeicherten Richtung des Stuecks gerechnet,
        # wechselte die Seite mit jedem Stueck, das andersherum abgelegt ist
        erste = min(spuren)
        bezug = net.lines[spuren[erste][0]][spuren[erste][1]][1]
        bezug_von[p] = bezug
        r_p: Dict[Tuple[str, str], int] = {}
        namen = sorted(spuren)
        for x, sa in enumerate(namen):
            for sb in namen[x + 1:]:
                (la, ia), (lb, ib) = spuren[sa], spuren[sb]
                # Gehrichtung so, dass das Stueck vorwaerts (start -> end) laeuft
                da, db = net.lines[la][ia][1], net.lines[lb][ib][1]
                vor = vergleich(la, ia, da, lb, ib, db)
                # rueckwaerts: wer dort rechts abbiegt, liegt vorwaerts links
                zurueck = -vergleich(la, ia, -da, lb, ib, -db)
                if vor and zurueck and vor != zurueck:
                    kreuzt[p].add(frozenset((sa, sb)))
                r = vor or zurueck
                if r == 0:
                    r = -bezug     # sa < sb: links in Fahrtrichtung der ersten
                r_p[(sa, sb)], r_p[(sb, sa)] = r, -r
        rel[p] = r_p

    # 2. Kein Flechten: wechseln zwei Spuren auf einem kurzen Stueck die
    #    Seiten und gleich wieder zurueck, bleiben sie auf ihrer Seite
    laenge = [float(_arc(pc.pts)[-1]) for pc in net.pieces]
    for lid, folge in net.lines.items():
        sa = spur(lid)
        for sb in {sb for p, _ in folge for sb in auf[p] if sb != sa}:
            # Laeufe: (Wert in Fahrtrichtung, [Positionen]); None = getrennt
            laeufe: List[List] = []
            for i, (p, d) in enumerate(folge):
                v = rel[p][(sa, sb)] * d if sb in auf[p] else None
                if laeufe and laeufe[-1][0] == v:
                    laeufe[-1][1].append(i)
                else:
                    laeufe.append([v, [i]])
            for k in range(1, len(laeufe) - 1):
                v, idx = laeufe[k]
                vor, nach = laeufe[k - 1][0], laeufe[k + 1][0]
                if v is None or vor is None or nach is None or vor != nach or v == vor:
                    continue
                if sum(laenge[folge[i][0]] for i in idx) >= min_run:
                    continue
                for i in idx:
                    p, d = folge[i]
                    rel[p][(sa, sb)], rel[p][(sb, sa)] = vor * d, -vor * d
                laeufe[k][0] = vor

    # 3. Vorgaben von Hand schlagen alles davor
    fest: Dict[int, set] = defaultdict(set)
    vorgaben = [(left_of or {}, -1), (right_of or {}, 1)]
    for vorgabe, seite in vorgaben:
        for lid, andere in vorgabe.items():
            sa = spur(lid)
            for p, d in net.lines[lid]:
                for eintrag in andere:
                    b = eintrag if isinstance(eintrag, Beside) else Beside(eintrag)
                    sb = spur(b.line)
                    if sb not in auf[p] or sb == sa or not b.gilt(auf[p], spur, net.pieces[p]):
                        continue
                    # links (-1) / rechts (+1) in Fahrtrichtung; in
                    # Stueckrichtung mal d
                    rel[p][(sa, sb)], rel[p][(sb, sa)] = seite * d, -seite * d
                    fest[p] |= {(sa, sb), (sb, sa)}

    # 4. Kreuzen statt Durchwandern: zwei Linien, die sich auf einem kurzen
    #    gemeinsamen Stueck ohnehin kreuzen, buendeln dort nicht. Jede Gruppe
    #    bleibt mittig auf ihrem Gleis, und sie kreuzen sich als X -- statt
    #    dass eine quer durch ein breites Buendel wandert. Kurz heisst: der
    #    gemeinsame Lauf der beiden ist kuerzer als `cross_run`
    def gemeinsamer_lauf(lid: str, i: int, sb: str) -> float:
        folge, summe = net.lines[lid], 0.0
        for schritt in (1, -1):
            k = i if schritt == 1 else i - 1
            while 0 <= k < len(folge) and sb in auf[folge[k][0]]:
                summe += laenge[folge[k][0]]
                k += schritt
        return summe

    getrennt: Dict[int, set] = defaultdict(set)
    for p, paare in kreuzt.items():
        for paar in paare:
            sa, sb = sorted(paar)
            la, ia = auf[p][sa]
            if (sa, sb) in fest[p] or gemeinsamer_lauf(la, ia, sb) >= cross_run:
                continue
            getrennt[p].add(paar)

    # 5. Reihenfolge je Stueck aus den paarweisen Lagen, je Teilbuendel
    ordnung: Dict[int, Dict[str, float]] = {}
    for p, spuren in auf.items():
        r_p = rel[p]
        ordnung[p] = {}
        teile = _komponenten(
            sorted(spuren), lambda a, b: frozenset((a, b)) not in getrennt[p],
        )
        for teil in teile:
            reihe = sorted(teil, key=cmp_to_key(lambda a, b: r_p[(a, b)] if a != b else 0))
            mitte = (len(reihe) - 1) / 2
            ordnung[p].update({sp: i - mitte for i, sp in enumerate(reihe)})

    # 6. Korridore: feste Spurlagen von Hand. Teilen sich zwei Korridore ein
    #    Stueck, gewinnt der zuerst genannte
    belegt: set = set()
    for kor in corridors:
        for a, b in zip(kor.steps, kor.steps[1:]):
            if (a, b) in net.edges:
                stuecke = net.edges[(a, b)]
            elif (b, a) in net.edges:
                stuecke = [(p, -d) for p, d in reversed(net.edges[(b, a)])]
            else:
                raise ValueError(f"Korridor: {a} -> {b} ist keine Kante im Netz")
            for p, d in stuecke:
                spuren = ordnung[p]
                if len(spuren) < 2 or p in belegt:
                    continue
                if kor.complete and any(spur(l) not in spuren for l in kor.offsets):
                    continue
                fest_hier = {}
                for lid, wert in kor.offsets.items():
                    if spur(lid) in spuren:
                        fest_hier[spur(lid)] = wert * d     # in Stueckrichtung
                if not fest_hier:
                    continue
                rest = sorted((v, sp) for sp, v in spuren.items() if sp not in fest_hier)
                lo, hi = min(fest_hier.values()), max(fest_hier.values())
                # Nicht genannte Linien: auf ihre Seite, aussen an die festen
                auto_fest = [spuren[sp] for sp in fest_hier]
                mitte_fest = float(np.mean(auto_fest))
                links = [sp for v, sp in rest if v < mitte_fest]
                rechts = [sp for v, sp in rest if v >= mitte_fest]
                neu = dict(fest_hier)
                for k, sp in enumerate(reversed(links)):
                    neu[sp] = lo - 1 - k
                for k, sp in enumerate(rechts):
                    neu[sp] = hi + 1 + k
                ordnung[p] = neu
                belegt.add(p)

    return {
        lid: np.array([ordnung[p][spur(lid)] * d for p, d in folge])
        for lid, folge in net.lines.items()
    }


# ==========================================================================
# LINIENZUEGE
# ==========================================================================


def _kurven(t: np.ndarray, phi: np.ndarray, kappa_min: float, turn_min: float,
            gap: float) -> List[Tuple[int, int]]:
    """Kurven eines Zugs: Laeufe mit |Kruemmung| ueber kappa_min und gleicher
    Drehrichtung, die sich zusammen um mindestens turn_min (Bogenmass)
    drehen. Laeufe derselben Richtung mit einer Luecke unter `gap` Pixeln
    gehoeren zu einer Kurve -- eine Gleiskurve aus mehreren Boegen, oder
    eine, die ueber einen Stoss hinweggeht. -> [(erster, letzter Index)]"""
    if len(t) < 3:
        return []
    kappa = np.gradient(phi) / np.maximum(np.gradient(t), 1e-9)
    sgn = np.where(np.abs(kappa) > kappa_min, np.sign(kappa), 0.0)
    laeufe: List[List] = []
    i = 0
    while i < len(t):
        if sgn[i] == 0:
            i += 1
            continue
        j = i
        while j + 1 < len(t) and sgn[j + 1] == sgn[i]:
            j += 1
        if laeufe and laeufe[-1][2] == sgn[i] and t[i] - t[laeufe[-1][1]] < gap:
            laeufe[-1][1] = j
        else:
            laeufe.append([i, j, sgn[i]])
        i = j + 1
    return [(i, j) for i, j, _ in laeufe if abs(phi[j] - phi[i]) >= turn_min]


def line_paths(
    net: Network,
    slots: Mapping[str, np.ndarray],
    *,
    spacing: float,
    ramp: float = 24.0,
    step: float = 1.0,
    rings: Iterable[str] = (),
    normal_smooth: float = 10.0,
    curve_radius: float = 150.0,
    curve_turn: float = 20.0,
    corner_radius: float = 8.0,
    corner_reach: float = 12.0,
    fork_radius: float = 16.0,
    fork_turn: float = 15.0,
    hold: Optional[Mapping[str, Sequence[Tuple[str, str, float]]]] = None,
    _sync: Optional[Dict] = None,
) -> Dict[str, np.ndarray]:
    """Stufe 2: Linie -> fertiger Linienzug, versetzt neben der Trasse.

    spacing  Abstand zweier Spuren in Pixeln
    ramp     Laenge eines Spurwechsels um eine Spur
    rings    Linien, die im Kreis fahren. Ihr Zug wird geschlossen, die Naht
             liegt an einer ruhigen Stelle ohne Spurwechsel
    normal_smooth
             Glaettung (Pixel) nur fuer die Richtung, in der der Versatz
             angetragen wird -- die Lage der Trasse bleibt unberuehrt
    curve_radius, curve_turn
             was als Kurve zaehlt: enger als curve_radius (Pixel) und
             zusammen um mindestens curve_turn Grad
    corner_radius, corner_reach
             was danach noch enger dreht als corner_radius (Pixel) -- eine
             Gabelung, die im Mittelgleis spaet und steil liegt, die
             Innenseite eines Buendels --, wird ausgerundet; und zwar bei
             jeder Linie, die naeher als corner_reach vorbeifuehrt, gleich,
             damit das Buendel parallel bleibt. Echte Gleisboegen drehen
             weiter und behalten ihren Radius
    fork_radius, fork_turn
             an einer Gabelung, an der eine Linie mit mehr als fork_turn
             Grad Knick aus einem Buendel abgeht oder dazukommt, wird mit
             fork_radius ausgerundet -- sie loest sich weich, und die
             anderen ruecken nicht in ihren Platz, solange sie noch da ist
    hold     Linie -> [(a, b, Pixel)]: zwischen den Stationen a und b
             reicht die Lage, die die Linie auf der Seite von b hat, um so
             viel weiter in den Spurwechsel hinein -- er wird kuerzer und
             endet naeher an a

    Wo eine Linie allein faehrt, liegt sie in der Mitte ihrer Trasse. Kommen
    andere dazu, schmiegt sie sich an: sie bleibt in der Mitte, bis das
    Gleis der anderen so nahe ist, dass der Spurabstand nicht mehr reicht,
    und weicht dann gerade so weit aus -- die Form ihres Uebergangs kommt
    aus der Kurve, mit der das andere Gleis einlaeuft. Beim Abgehen gilt
    dasselbe rueckwaerts. Laufen die Gleise zu abrupt zusammen, bleibt
    mindestens eine Rampe am Knoten; die Rampe liegt dort, wo weniger Linien
    liegen. Wechseln Linien ohne Zu- oder Abgang die Spur, bekommen alle an
    derselben Stelle dasselbe Rampenfenster und bleiben dabei parallel.

    Faellt ein Spurwechsel in eine Kurve, gibt es keine Rampe. Die Linie
    faehrt die Kurve mit der Form der Trasse -- demselben Radius --, nur
    verschoben, so dass sie tangential an ihre versetzte Gerade davor und
    dahinter anschliesst; die Geraden werden dafuer laenger oder kuerzer.
    Sonst haette die Linie innen einen Radius nahe null und aussen einen
    aufgeweiteten Bogen. Linien, die an derselben Kurve um denselben Betrag
    wechseln, bleiben ein Buendel: seine Mitte hat den Radius der Trasse,
    die Linien liegen konzentrisch darum. Wechselt keine Linie, ist das
    der gewoehnliche Versatz.
    """
    # Buendelbreite je Stueck als Zahl der Spuren darauf
    lagen: Dict[int, set] = defaultdict(set)
    for lid, werte in slots.items():
        for (p, d), v in zip(net.lines[lid], werte):
            lagen[p].add(round(float(v) * d, 6))
    breite: Dict[int, int] = {p: len(l) for p, l in lagen.items()}

    # Durchgangsstuecke: alle Linien darauf kommen vom selben Stueck und
    # fahren aufs selbe weiter
    nachbarn: Dict[int, set] = defaultdict(set)
    for folge in net.lines.values():
        for i, (p, d) in enumerate(folge):
            vor = folge[i - 1][0] if i > 0 else None
            nach = folge[i + 1][0] if i + 1 < len(folge) else None
            nachbarn[p].add(frozenset((vor, nach)))
    durchgang = {
        p for p, n in nachbarn.items()
        if len(n) == 1 and None not in next(iter(n))
    }

    # Zwei Durchgaenge: erst sammelt jede Linie ihre Wechselfenster je
    # Stossstelle, dann bekommen alle Linien an derselben Stelle dasselbe
    if _sync is None:
        sync: Dict = {"phase": "sammeln", "fenster": {}, "boegen": defaultdict(set)}
        kw = dict(spacing=spacing, ramp=ramp, step=step, rings=list(rings),
                  normal_smooth=normal_smooth, curve_radius=curve_radius,
                  curve_turn=curve_turn, corner_radius=corner_radius,
                  corner_reach=corner_reach, fork_radius=fork_radius,
                  fork_turn=fork_turn, hold=hold)
        line_paths(net, slots, _sync=sync, **kw)
        sync["phase"] = "anwenden"
        return line_paths(net, slots, _sync=sync, **kw)
    rings = set(rings)
    stuecklaenge = [float(_arc(pc.pts)[-1]) for pc in net.pieces]

    out: Dict[str, np.ndarray] = {}
    gabeln: List[np.ndarray] = []
    for lid, folge in net.lines.items():
        werte = np.asarray(slots[lid], dtype=float)
        kreis = lid in rings
        if kreis:
            kandidaten = [
                j for j in range(1, len(folge))
                if werte[j - 1] == werte[j]
                and breite[folge[j - 1][0]] == breite[folge[j][0]]
            ]
            if kandidaten:
                j = max(kandidaten, key=lambda j: min(
                    stuecklaenge[folge[j - 1][0]], stuecklaenge[folge[j][0]]
                ))
                folge = list(folge[j:]) + list(folge[:j])
                werte = np.r_[werte[j:], werte[:j]]
        stuecke, index = [], []
        for j, (p, d) in enumerate(folge):
            teil = _oriented(net, p, d)
            if stuecke and np.hypot(*(teil[0] - stuecke[-1][-1])) < step / 2:
                teil = teil[1:]
            stuecke.append(teil)
            index.append(np.full(len(teil), j))
        mitte = np.vstack(stuecke)
        j_von = np.concatenate(index)
        if kreis and len(mitte) > 1 and np.hypot(*(mitte[-1] - mitte[0])) < step / 2:
            mitte, j_von = mitte[:-1], j_von[:-1]
        t = _arc(mitte)

        anfang = np.array([t[np.searchsorted(j_von, j)] for j in range(len(folge))])
        ende = np.r_[anfang[1:], t[-1]]
        lang = ende - anfang
        durch = np.array([p in durchgang for p, _ in folge])

        # Normale rechts der Fahrtrichtung (y nach unten: rechts von (dx, dy)
        # ist (-dy, dx)), aus einer leicht geglaetteten Richtung
        richtung = _smooth(mitte, max(int(round(normal_smooth / step)) | 1, 1), cyclic=kreis)
        if kreis:
            gg = (np.roll(richtung, -1, axis=0) - np.roll(richtung, 1, axis=0)) / 2
        else:
            gg = np.gradient(richtung, axis=0)
        nn = np.hypot(gg[:, 0], gg[:, 1])
        nn[nn == 0] = 1.0
        e = gg / nn[:, None]
        n = np.column_stack([-e[:, 1], e[:, 0]])

        fenster: List[List[float]] = []
        gabel_hier: List[Tuple[float, np.ndarray]] = []

        def abgleich(fw: List[float], p: int, q: int, ref_pq: float, ref_qp: float) -> None:
            if p < q:
                key, ref, sgn = (p, q), ref_pq, 1.0
            else:
                key, ref, sgn = (q, p), ref_qp, -1.0
            lo, hi = sorted((sgn * (fw[0] - ref), sgn * (fw[1] - ref)))
            speicher = _sync["fenster"]
            if _sync["phase"] == "sammeln":
                alt = speicher.get(key)
                speicher[key] = (lo, hi) if alt is None else (min(alt[0], lo), max(alt[1], hi))
            elif key in speicher:
                lo, hi = speicher[key]
                a, b = sorted((ref + sgn * lo, ref + sgn * hi))
                fw[0], fw[1] = max(a, 0.0), min(b, t[-1])

        def schmiegen(j: int, zusammen: bool) -> Optional[List]:
            """Spurwechsel dort, wo andere Linien dazukommen (zusammen) oder
            abgehen: die Linie bleibt auf ihrer Spur, bis das Gleis der
            anderen so nahe kommt, dass der Spurabstand zu ihnen nicht mehr
            reicht, und weicht dann gerade so weit aus. Die anderen tun
            dasselbe -- beide schmiegen sich aneinander, und die Form des
            Uebergangs kommt aus der Kurve, mit der das andere Gleis
            einlaeuft. Liefert ein Fenster mit Verlauf, oder None, wenn es
            keine anderen gibt, an denen sie sich ausrichten kann."""
            (p, dp), (q, dq) = folge[j], folge[j + 1]
            knoten = net.pieces[p].end if dp > 0 else net.pieces[p].start
            gemeinsam, ds, eigen = (q, dq, p) if zusammen else (p, dp, q)
            # "fern": auf der Seite, wo die Linie allein(er) ist
            o_fern, o_nah = (werte[j], werte[j + 1]) if zusammen else (werte[j + 1], werte[j])
            grenze = anfang[j + 1]
            weit = 6 * ramp
            if zusammen:
                idx = np.nonzero((t >= grenze - weit) & (t <= grenze))[0]
            else:
                idx = np.nonzero((t >= grenze) & (t <= grenze + weit))[0]
            if len(idx) < 3:
                return None
            w = np.zeros(len(idx))
            gefunden = False
            for andere, f_n in net.lines.items():
                if andere == lid:
                    continue
                ks = [k for k, (r_, _) in enumerate(f_n) if r_ == gemeinsam]
                if not ks:
                    continue
                k = ks[0]
                # das Stueck, mit dem die andere Linie an diesem Knoten
                # ankommt oder abgeht
                kr = None
                for kk in (k - 1, k + 1):
                    if 0 <= kk < len(f_n) and f_n[kk][0] != gemeinsam:
                        pc = net.pieces[f_n[kk][0]]
                        if knoten in (pc.start, pc.end):
                            kr = kk
                if kr is None or f_n[kr][0] == eigen:
                    continue
                o_n_nah = float(slots[andere][k]) * f_n[k][1] * ds
                if abs(o_n_nah - o_nah) < 1e-9:
                    continue            # gleiche Spur (ein Ast auf seiner Linie)
                pc = net.pieces[f_n[kr][0]]
                # in Richtung der Linie: zum Knoten hin (zusammen) bzw. von
                # ihm weg
                vorwaerts = (pc.end == knoten) if zusammen else (pc.start == knoten)
                gleis = pc.pts if vorwaerts else pc.pts[::-1]
                o_n_fern = float(slots[andere][kr]) * f_n[kr][1] * (1 if vorwaerts else -1)
                sg = np.sign(o_n_nah - o_nah)
                g0, g1 = sg * (o_n_fern - o_fern), sg * (o_n_nah - o_nah)
                if g1 - g0 <= 1e-6:
                    continue
                # seitlicher Abstand des anderen Gleises, in Spuren -- nur
                # Punkte querab zaehlen; liegt keiner querab, ist es weit weg
                rel = gleis[None, :, :] - mitte[idx, None, :]
                laengs = np.einsum("ijk,ik->ij", rel, e[idx])
                quer = np.einsum("ijk,ik->ij", rel, n[idx])
                quer = np.where(np.abs(laengs) <= step, quer, np.inf * sg)
                seite = quer[np.arange(len(idx)), np.argmin(np.abs(quer), axis=1)] / spacing
                if not np.isfinite(seite).any():
                    continue
                # liegt die andere Linie fern auf der falschen Seite, kreuzen
                # sich die beiden -- dann gibt es nichts zum Anschmiegen
                endlich = np.nonzero(np.isfinite(seite))[0]
                fern = endlich[0] if zusammen else endlich[-1]
                am_knoten = endlich[-1] if zusammen else endlich[0]
                # Geradeaus weiter: schert das eigene Gleis in die Richtung
                # aus, in die die Linie ohnehin muss, behaelt sie ihren Platz
                # neben dem Buendel, bis ihr Gleis unter ihr ist -- sie loest
                # sich tangential und rueckt nie an die anderen heran
                weg_gleis = seite - seite[am_knoten]
                if np.sign(weg_gleis[fern]) == np.sign(o_fern - o_nah) != 0:
                    # Abstand zum Buendelgleis konstant: o - seite bleibt,
                    # was er am Knoten war
                    halt = np.clip((o_nah + weg_gleis - o_fern) / (o_nah - o_fern), 0.0, 1.0)
                    w = np.maximum(w, np.nan_to_num(halt))
                    gefunden = True
                if sg * seite[fern] + g0 < 0:
                    continue
                # weich: tangential aus dem Buendel und tangential ins
                # eigene Gleis, wie ein Bogen im Y
                w = np.maximum(w, _smoothstep(np.nan_to_num(1 - sg * seite / (g1 - g0))))
                gefunden = True
            if not gefunden:
                return None
            # mindestens eine Rampe am Knoten, damit der Wechsel dort fertig ist
            r = min(ramp, lang[j] if zusammen else lang[j + 1])
            if zusammen:
                w = np.maximum(w, _smoothstep((t[idx] - (grenze - r)) / max(r, 1e-9)))
            else:
                w = np.maximum(w, _smoothstep(((grenze + r) - t[idx]) / max(r, 1e-9)))
            w = _smooth(np.column_stack([w, w]), max(int(round(ramp / 3 / step)) | 1, 1))[:, 0]
            # w: Anteil der Lage am Knoten -> Anteil des Wechsels o1 -> o2
            anteil = w if zusammen else 1 - w
            aktiv = np.nonzero((anteil > 1e-3) & (anteil < 1 - 1e-3))[0]
            if not len(aktiv):
                return None
            a, b = t[idx[aktiv[0]]], t[idx[aktiv[-1]]]
            if zusammen:
                b = grenze
            else:
                a = grenze
            return [a, b, werte[j], werte[j + 1], "schmiegen", t[idx], anteil]

        # Frei in der Lage: auf einem Durchgangsstueck, das kuerzer ist als
        # eine Rampe -- dort gleitet die Linie in einem Zug von der
        # ankommenden zur abgehenden Spur. Wo sie allein faehrt, liegt sie
        # in der Mitte der Trasse, und sei das Stueck noch so kurz
        frei = durch & (lang < ramp) & np.array([breite[p] > 1 for p, _ in folge])
        frei[0] = frei[-1] = False
        j = 0
        while j < len(folge) - 1:
            if frei[j]:
                k = j
                while k < len(folge) and frei[k]:
                    k += 1
                von, bis = werte[j - 1], werte[k]
                if von != bis:
                    a, b = anfang[j], ende[k - 1]
                    fw = [a, b, von, bis]
                    abgleich(fw, folge[j - 1][0], folge[k][0], a, b)
                    fenster.append(fw)
                j = k
                continue
            o1, o2 = werte[j], werte[j + 1]
            if frei[j + 1] or o1 == o2:
                j += 1
                continue
            b1, b2 = breite[folge[j][0]], breite[folge[j + 1][0]]
            grenze = anfang[j + 1]
            if b2 != b1:
                # Gabelung mit Knick? (Richtung der Trasse davor/dahinter)
                ia, ib, ic, idd = np.searchsorted(t, [grenze - 10, grenze - 2, grenze + 2, grenze + 10])
                ib, idd = min(ib, len(t) - 1), min(idd, len(t) - 1)
                if ia < ib and ic < idd:
                    u, v_ = _unit(mitte[ib] - mitte[ia]), _unit(mitte[idd] - mitte[ic])
                    if np.degrees(np.arccos(np.clip(u @ v_, -1, 1))) > fork_turn:
                        gabel_hier.append((grenze, mitte[min(np.searchsorted(t, grenze), len(t) - 1)]))
                # anschmiegen, wo andere Linien dazukommen oder abgehen
                fw = schmiegen(j, b2 > b1)
                if fw is not None:
                    fenster.append(fw)
                    j += 1
                    continue
            if b2 < b1:
                r = min(ramp, lang[j + 1])
                a0 = grenze
            elif b2 > b1:
                r = min(ramp, lang[j])
                a0 = grenze - r
            else:
                r = ramp * max(1.0, abs(o2 - o1) / 2)
                a0 = grenze - r / 2
            fw = [a0, a0 + r, o1, o2]
            abgleich(fw, folge[j][0], folge[j + 1][0], grenze, grenze)
            fenster.append(fw)
            j += 1

        # Ueberschneidet sich ein angeschmiegter Wechsel mit einem anderen,
        # werden beide einer: vom Anfang des ersten zum Ende des zweiten.
        # Hin und gleich wieder zurueck hebt sich so auf, statt eine Kerbe
        # zu geben
        fenster.sort(key=lambda f: f[0])
        k = 0
        while k < len(fenster) - 1:
            f, g = fenster[k], fenster[k + 1]
            if f[1] > g[0] and "schmiegen" in (f[4:5] + g[4:5]):
                if f[2] == g[3]:
                    del fenster[k:k + 2]
                else:
                    fenster[k:k + 2] = [[f[0], max(f[1], g[1]), f[2], g[3]]]
                continue
            k += 1
        # Andere ueberlappende Fenster teilen sich den Platz in der Mitte
        for f, g in zip(fenster, fenster[1:]):
            if f[1] > g[0]:
                m = min(max((f[1] + g[0]) / 2, f[0]), g[1])
                f[1] = g[0] = m
        for f in fenster:
            f[0], f[1] = max(f[0], 0.0), min(f[1], t[-1])

        # Lagen von Hand verlaengern
        for a_st, b_st, px in (hold or {}).get(lid, ()):
            knoten = [(anfang[jj], net.pieces[p].start if d > 0 else net.pieces[p].end)
                      for jj, (p, d) in enumerate(folge)] + [(t[-1], None)]
            ta = [x for x, kk in knoten if kk == a_st]
            tb = [x for x, kk in knoten if kk == b_st]
            paare = [(x, y) for x in ta for y in tb]
            if not paare:
                raise ValueError(f"hold: {lid} faehrt nicht von {a_st} nach {b_st}")
            x, y = min(paare, key=lambda xy: abs(xy[1] - xy[0]))
            lo, hi = min(x, y), max(x, y)
            for f in fenster:
                if f[1] > lo and f[0] < hi and len(f) == 4:
                    # vorwaerts: b liegt voraus, die Lage dort beginnt frueher
                    f += ["hand", px, y > x]

        # Spurwechsel in Kurven: die Rampen dort (bis `ramp` vor und hinter
        # der Kurve) fallen weg, die Kurve wird verschoben
        boegen = []
        phi = np.unwrap(np.arctan2(e[:, 1], e[:, 0]))
        kurven = _kurven(t, phi, 1.0 / curve_radius, np.radians(curve_turn), 3 * step)
        # Jeder Wechsel gehoert zu der Kurve, in der er liegt. Einer knapp
        # davor oder dahinter gehoert zu seinem Knoten: dort muss die Linie
        # ihre Spur schon haben, sonst kreuzt sie in der Kurve die anderen.
        # Ein angeschmiegter Wechsel bleibt, wie er ist, solange die Kurve
        # weit genug ist fuer den Versatz; nur in einer engen Kurve (die
        # innere Linie haette sonst fast keinen Radius) uebernimmt sie ihn
        kappa = np.abs(np.gradient(phi) / np.maximum(np.gradient(t), 1e-9))
        zu: Dict[int, List[List[float]]] = defaultdict(list)
        for f in fenster:
            abstand = [max(t[i0] - f[1], f[0] - t[i1], 0.0) for i0, i1 in kurven]
            if not abstand or min(abstand) > 3 * step:
                continue
            k = int(np.argmin(abstand))
            i0, i1 = kurven[k]
            radius = 1.0 / max(float(kappa[i0:i1 + 1].max()), 1e-9)
            if f[4:5] == ["schmiegen"] and radius > (max(abs(f[2]), abs(f[3])) + 2) * spacing:
                continue
            zu[k].append(f)
        for k, (i0, i1) in enumerate(kurven):
            s0, s1 = t[i0], t[i1]
            weg = zu.get(k)
            if not weg:
                continue
            o1, o2 = weg[0][2], weg[-1][3]
            # ein Wechsel, den schon eine Kurve uebernommen hat, gehoert ihr
            if o1 == o2 or any(f[4:5] == ["bogen"] for f in weg):
                continue
            # Buendel: Linien, die an derselben Kurve (Stueck am Anfang und
            # am Ende) um denselben Betrag wechseln. Gezaehlt wird in einer
            # festen Richtung, damit Gegenrichtungen dasselbe Buendel finden
            a_ = folge[j_von[i0]]
            b_ = folge[j_von[i1]]
            vor, rueck = (a_, b_), ((b_[0], -b_[1]), (a_[0], -a_[1]))
            gedreht = rueck < vor
            key = (min(vor, rueck), round(float(o2 - o1), 6))
            o1c = -o2 if gedreht else o1
            if _sync["phase"] == "sammeln":
                _sync["boegen"][key].add(round(float(o1c), 6))
            werte_c = _sync["boegen"].get(key) or {o1c}
            ref_c = (min(werte_c) + max(werte_c)) / 2
            ref1 = -(ref_c + o2 - o1) if gedreht else ref_c
            ref2 = ref1 + o2 - o1
            # Verschiebung v: Anfang der Kurve auf der Geraden davor (Versatz
            # ref1), Ende auf der dahinter (ref2)
            A = np.array([n[i0], n[i1]])
            if abs(np.linalg.det(A)) < np.sin(np.radians(curve_turn)) / 2:
                continue
            v = np.linalg.solve(A, np.array([ref1, ref2]) * spacing)
            # In einer Kehre liegen die Geraden davor und dahinter fast
            # parallel, und schon ein kleiner Versatz verschiebt die Kurve
            # weit weg vom Gleis -- dann lieber der gewoehnliche Wechsel
            if np.hypot(*v) > 1.5 * max(abs(ref1), abs(ref2), abs(o2 - o1)) * spacing:
                continue
            a, b = float(e[i0] @ v), float(e[i1] @ v)
            hand = [f for f in weg if f[4:5] == ["hand"]]
            if hand:
                # von Hand verlaengerte Lage: der Wechsel endet um px frueher
                # (bzw. beginnt spaeter) als die verschobene Kurve dort, und
                # ist steil -- so lang wie sein seitlicher Versatz, 45 Grad
                _, px, vorwaerts = hand[0][4:7]
                lang_w = max(abs(o2 - o1) * spacing, 2 * step)
                for f in weg:
                    fenster.remove(f)
                if vorwaerts:
                    ende = t[i1] + b - px
                    fenster.append([ende - lang_w, ende, o1, o2, "bogen"])
                else:
                    anf = t[i0] + a + px
                    fenster.append([anf, anf + lang_w, o1, o2, "bogen"])
                fenster.sort(key=lambda f: f[0])
                continue
            # belegt: Kurve, verlaengerte Geraden und die Anschluesse
            lo = s0 + a - ramp / 2 if a < 0 else s0
            hi = s1 + b + ramp / 2 if b > 0 else s1
            andere = [f for f in fenster if f not in weg]
            if lo < 0 or hi > t[-1] or any(f[1] > lo and f[0] < hi for f in andere) \
                    or any(lo < bg[6] and hi > bg[5] for bg in boegen):
                continue
            for f in weg:
                fenster.remove(f)
            m = (s0 + s1) / 2
            fenster.append([m, m, o1, o2, "bogen"])
            fenster.sort(key=lambda f: f[0])
            boegen.append((i0, i1, v, a, b, lo, hi, o1 - ref1))

        # Gabelungen weicher ausrunden -- nicht, wo eine Kurve mit echtem
        # Gleisradius liegt
        gabeln += [x for tg, x in gabel_hier
                   if not any(bg[5] - ramp <= tg <= bg[6] + ramp for bg in boegen)]
        for f in fenster:
            if f[4:5] == ["hand"]:
                # von Hand verlaengerte Lage ohne Kurve: Rampe verkuerzen
                _, px, vorwaerts = f[4:7]
                if vorwaerts:
                    f[1] = max(f[1] - px, f[0] + step)
                else:
                    f[0] = min(f[0] + px, f[1] - step)
                del f[4:]
        o = np.full_like(t, werte[0])
        for f in fenster:
            drin, danach = (t >= f[0]) & (t <= f[1]), t > f[1]
            if f[4:5] == ["schmiegen"]:
                # der Verlauf, auf das (vielleicht gekuerzte) Fenster gestreckt
                w0, w1 = np.interp([f[0], f[1]], f[5], f[6])
                w = (np.interp(t[drin], f[5], f[6]) - w0) / max(w1 - w0, 1e-9)
            elif f[1] > f[0]:
                w = _smoothstep((t[drin] - f[0]) / (f[1] - f[0]))
            else:
                w = np.ones(int(drin.sum()))
            o[drin] = f[2] + (f[3] - f[2]) * w
            o[danach] = f[3]

        basis = mitte + n * (o * spacing)[:, None]
        if boegen:
            def anschluss(p: np.ndarray, tp: np.ndarray, q: np.ndarray, tq: np.ndarray) -> np.ndarray:
                """Glatter Uebergang p -> q mit den Richtungen tp, tq: die
                Trasse neben einer gekuerzten Geraden ist selten ganz gerade,
                ihr Versatzpunkt liegt dann ein wenig neben der Kurve."""
                ch = float(np.hypot(*(q - p)))
                return _hermite(p, _unit(tp) * ch, q, _unit(tq) * ch, max(int(np.ceil(ch / step)), 2) + 1)

            teile, ab = [], 0
            for i0, i1, v, a, b, _, _, delta in sorted(boegen, key=lambda bg: bg[0]):
                kurve = mitte[i0:i1 + 1] + v + n[i0:i1 + 1] * (delta * spacing)
                # Gerade davor: gekuerzt, oder ueber den Kurvenanfang hinaus
                # verlaengert
                if a < 0:
                    k = max(int(np.searchsorted(t, t[i0] + a - ramp / 2)), ab + 1)
                    teile.append(basis[ab:k])
                    teile.append(anschluss(basis[k - 1], basis[k] - basis[k - 1], kurve[0], e[i0])[1:-1])
                else:
                    teile.append(basis[ab:i0])
                    teile.append(_resample(np.array([basis[i0], kurve[0]]), step)[:-1])
                teile.append(kurve)
                # Gerade dahinter
                if b < 0:
                    teile.append(_resample(np.array([kurve[-1], basis[i1]]), step)[1:])
                    ab = i1 + 1
                else:
                    k = min(int(np.searchsorted(t, t[i1] + b + ramp / 2, side="right")), len(t) - 1)
                    teile.append(anschluss(kurve[-1], e[i1], basis[k], basis[k] - basis[k - 1])[1:-1])
                    ab = k
            teile.append(basis[ab:])
            basis = np.vstack([x for x in teile if len(x)])
        zug = _cut_loops(basis)
        out[lid] = np.vstack([zug, zug[:1]]) if kreis else zug
    if corner_radius > 0 and _sync["phase"] == "anwenden":
        out = _round_corners(out, corner_radius, corner_reach, step,
                             forks=gabeln, fork_radius=fork_radius)
    return out


def lines_svg(
    paths: Mapping[str, np.ndarray],
    colors: Mapping[str, str],
    *,
    width: float,
    casing: Optional[str] = "#ffffff",
    casing_width: float = 1.5,
    simplify_tol: float = 0.15,
) -> str:
    """Alle Linien als SVG-Gruppe, in der Reihenfolge von `paths`.

    Jede Linie liegt auf ihrer eigenen Kontur (`casing`), eine Linienbreite
    plus `casing_width` breit, und die gehoert unmittelbar unter sie, nicht
    unter alle Linien gemeinsam: nur so deckt eine obenliegende Linie an
    einer Kreuzung die untere ab, statt sie durch die Spalte ihres Buendels
    scheinen zu lassen. Im Buendel trennt dieselbe Kontur benachbarte
    Linien mit einem feinen Spalt.
    """
    teile = ['<g id="linien" fill="none" stroke-linecap="round" stroke-linejoin="round">']
    for lid, p in paths.items():
        # ein Ring (erster Punkt = letzter) schliesst mit "z" -- ohne Naht
        # und ohne zwei runde Linienenden uebereinander
        zu = len(p) > 2 and bool(np.all(p[0] == p[-1]))
        d = path_d([simplify(p, simplify_tol)], closed=zu)
        if casing:
            teile.append(
                f'<path stroke="{casing}" stroke-width="{width + casing_width:g}" d="{d}"/>'
            )
        teile.append(
            f'<path id="linie-{lid}" stroke="{colors[lid]}" stroke-width="{width:g}" d="{d}"/>'
        )
    teile.append("</g>")
    return "\n".join(teile)

