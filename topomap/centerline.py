"""
Mittelgleis: aus allen Einzelgleisen eine Mittellinie je Strecke.

In OSM ist jedes Gleis einzeln erfasst -- eine zweigleisige Strecke sind zwei
Linien, an einem Bahnhof liegen oft sechs nebeneinander. Fuer die Karte zaehlt
nur die Strecke. Die Gleise werden deshalb auf ein feines Raster gezeichnet,
jedes mit einem Band der Breite 2 * `radius`; parallele Gleise verschmelzen
so zu einer Flaeche. Diese wird auf ihre Mittellinie ausgeduennt (Skelett,
Zhang-Suen), und das Skelett wird ein Gleisgraph -- dieselbe Form wie
`TrackGraph`, also nutzbar fuer Stationszuordnung und Wegsuche.

Wo Strecken weiter als 2 * `radius` auseinanderlaufen, bleiben sie getrennte
Linien mit ihren eigenen Kurven.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

from .lines import TrackGraph, _resample

Project = object


def track_lines(
    elements: Iterable[Mapping],
    project,
    *,
    railway: Sequence[str] = ("light_rail", "rail"),
    skip_service: Sequence[str] = ("crossover", "siding", "yard", "spur"),
) -> List[np.ndarray]:
    """Gleise als Linienzuege in Kartenpixeln, ohne Weichenverbinder und
    Abstellgleise."""
    out = []
    for el in elements:
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        if tags.get("railway") not in railway or tags.get("service") in skip_service:
            continue
        geo = [p for p in el.get("geometry", ()) if p]
        if len(geo) < 2:
            continue
        ll = np.array([(p["lon"], p["lat"]) for p in geo])
        x, y = project(ll[:, 0], ll[:, 1])
        out.append(np.column_stack([x, y]))
    return out


def _rasterize(lines: Sequence[np.ndarray], res: float, radius: float):
    """Band um alle Linien auf ein Raster (Zellgroesse res Pixel)."""
    alle = np.vstack(lines)
    x0, y0 = alle.min(axis=0) - radius - 2 * res
    x1, y1 = alle.max(axis=0) + radius + 2 * res
    w, h = int(np.ceil((x1 - x0) / res)), int(np.ceil((y1 - y0) / res))
    grid = np.zeros((h, w), dtype=bool)
    r = int(np.ceil(radius / res))
    dy, dx = np.mgrid[-r:r + 1, -r:r + 1]
    scheibe = (dx ** 2 + dy ** 2) <= (radius / res) ** 2
    dx, dy = dx[scheibe], dy[scheibe]
    for ln in lines:
        pts = _resample(ln, res / 2) if len(ln) > 1 else ln
        cx = np.round((pts[:, 0] - x0) / res).astype(int)
        cy = np.round((pts[:, 1] - y0) / res).astype(int)
        # Mittelpunkte entdoppeln, dann die Scheibe um jeden
        c = np.unique(np.column_stack([cx, cy]), axis=0)
        gx = (c[:, 0:1] + dx[None, :]).ravel()
        gy = (c[:, 1:2] + dy[None, :]).ravel()
        ok = (gx >= 0) & (gx < w) & (gy >= 0) & (gy < h)
        grid[gy[ok], gx[ok]] = True
    return grid, (x0, y0)


def _thin(grid: np.ndarray) -> np.ndarray:
    """Zhang-Suen-Ausduennung, vektorisiert."""
    img = np.pad(grid, 1).astype(np.uint8)
    while True:
        geaendert = False
        for schritt in (0, 1):
            p2 = img[:-2, 1:-1]; p3 = img[:-2, 2:]; p4 = img[1:-1, 2:]
            p5 = img[2:, 2:]; p6 = img[2:, 1:-1]; p7 = img[2:, :-2]
            p8 = img[1:-1, :-2]; p9 = img[:-2, :-2]
            c = img[1:-1, 1:-1]
            nachbarn = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            folge = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            uebergaenge = sum(((folge[k] == 0) & (folge[k + 1] == 1)).astype(np.uint8)
                              for k in range(8))
            if schritt == 0:
                a = p2 * p4 * p6
                b = p4 * p6 * p8
            else:
                a = p2 * p4 * p8
                b = p2 * p6 * p8
            weg = (c == 1) & (nachbarn >= 2) & (nachbarn <= 6) & (uebergaenge == 1) \
                & (a == 0) & (b == 0)
            if weg.any():
                img[1:-1, 1:-1][weg] = 0
                geaendert = True
        if not geaendert:
            break
    return img[1:-1, 1:-1].astype(bool)


def _prune(skel: np.ndarray, laenge: int) -> np.ndarray:
    """Kurze Stummel (Aeste mit freiem Ende, kuerzer als `laenge` Zellen)
    entfernen -- sie entstehen, wo das Band an Bahnhoefen ausbeult."""
    skel = skel.copy()
    offs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    h, w = skel.shape

    def nachbarn(y, x):
        return [(y + a, x + b) for a, b in offs
                if 0 <= y + a < h and 0 <= x + b < w and skel[y + a, x + b]]

    for _ in range(3):
        ys, xs = np.nonzero(skel)
        enden = [(y, x) for y, x in zip(ys, xs) if len(nachbarn(y, x)) == 1]
        entfernt = False
        for y, x in enden:
            if not skel[y, x]:
                continue
            weg, vor, cur = [(y, x)], None, (y, x)
            while True:
                nb = [n for n in nachbarn(*cur) if n != vor and n not in weg]
                if len(nb) != 1 or len(weg) > laenge:
                    break
                vor, cur = cur, nb[0]
                if len(nachbarn(*cur)) > 2:
                    break
                weg.append(cur)
            if len(weg) <= laenge and len(nachbarn(*cur)) > 2:
                for p in weg:
                    skel[p] = False
                entfernt = True
        if not entfernt:
            break
    return skel


def centerline_graph(
    lines: Sequence[np.ndarray],
    *,
    radius: float,
    res: float = 0.33,
    prune: float = 0.0,
    smooth_iter: int = 150,
) -> TrackGraph:
    """Mittelgleis als Gleisgraph (Knoten = Skelettzellen, in Kartenpixeln).

    radius  halbe Bandbreite in Pixeln: Gleise, die naeher als 2 * radius
            beieinander liegen, werden eine Strecke
    res     Rasterweite in Pixeln
    prune   Stummel kuerzer als das (Pixel) werden entfernt; 0 = 2 * radius
    smooth_iter
            Durchgaenge der Taubin-Glaettung (0 = keine). Die Reichweite
            waechst mit der Wurzel: 150 Durchgaenge bei 0.33 px Raster
            glaetten Spruenge auf etwa 5 px Laenge
    """
    grid, (x0, y0) = _rasterize(lines, res, radius)
    skel = _thin(grid)
    skel = _prune(skel, int(np.ceil((prune or 2 * radius) / res)))
    ys, xs = np.nonzero(skel)
    index = -np.ones(skel.shape, dtype=np.int64)
    index[ys, xs] = np.arange(len(ys))
    xy = np.column_stack([x0 + xs * res, y0 + ys * res])
    adj: Dict[int, List[Tuple[int, float]]] = defaultdict(list)
    h, w = skel.shape
    for a, b in [(0, 1), (1, 0), (1, 1), (1, -1)]:
        yy, xx = ys + a, xs + b
        ok = (yy >= 0) & (yy < h) & (xx >= 0) & (xx < w)
        i = np.arange(len(ys))[ok]
        j = index[yy[ok], xx[ok]]
        m = j >= 0
        d = res * np.hypot(a, b)
        for u, v in zip(i[m], j[m]):
            adj[int(u)].append((int(v), d))
            adj[int(v)].append((int(u), d))
    if smooth_iter:
        xy = _taubin(xy, adj, smooth_iter)
    return TrackGraph(ids=np.arange(len(ys)), xy=xy, adj=dict(adj))


def _taubin(xy: np.ndarray, adj: Mapping[int, Sequence[Tuple[int, float]]],
            iterations: int, lam: float = 0.5, mu: float = -0.53) -> np.ndarray:
    """Taubin-Glaettung auf dem Graphen: abwechselnd zum Mittel der Nachbarn
    hin (lam) und ein Stueck zurueck (mu). Kurze Spruenge -- wo das Band
    breiter wird, weil ein Ast einmuendet -- werden zu weichen Uebergaengen,
    Kurven behalten ihren Radius. Auch Abzweigpunkte werden mitgeglaettet;
    nur freie Enden bleiben fest."""
    n = len(xy)
    rows, cols = [], []
    for i, nb in adj.items():
        for j, _ in nb:
            rows.append(i)
            cols.append(j)
    rows, cols = np.array(rows), np.array(cols)
    grad = np.bincount(rows, minlength=n).astype(float)
    frei = grad >= 2
    xy = xy.copy()
    for k in range(2 * iterations):
        summe = np.zeros_like(xy)
        np.add.at(summe, rows, xy[cols])
        mittel = summe / np.maximum(grad, 1)[:, None]
        f = lam if k % 2 == 0 else mu
        xy[frei] += f * (mittel[frei] - xy[frei])
    return xy
