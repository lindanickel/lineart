"""
Linienzuege vereinfachen und als kompakte SVG-Pfade schreiben.

Alles hier arbeitet schon in Pixeln: vereinfacht wird auf die Aufloesung der
Karte, nicht auf Meter. Eine Strasse, die auf der Karte 0,3 px wackelt, hat
kein sichtbares Wackeln -- die Punkte dafuer kosten nur Dateigroesse.
"""

from __future__ import annotations

from typing import Iterable, List, Tuple

import numpy as np


def simplify(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker mit Toleranz `tol` (Pixel); Endpunkte bleiben.

    Iterativ statt rekursiv, damit ein Ufer mit zehntausend Punkten nicht
    an die Rekursionsgrenze stoesst.
    """
    n = len(pts)
    if n <= 2 or tol <= 0:
        return pts
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        seg = pts[i + 1 : j]
        d = b - a
        norm = np.hypot(d[0], d[1])
        if norm == 0.0:
            # geschlossener Ring: Abstand zum Punkt statt zur Geraden
            dist = np.hypot(seg[:, 0] - a[0], seg[:, 1] - a[1])
        else:
            dist = np.abs(d[0] * (seg[:, 1] - a[1]) - d[1] * (seg[:, 0] - a[0])) / norm
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return pts[keep]


def bbox_of(pts: np.ndarray) -> Tuple[float, float, float, float]:
    return (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())


def overlaps(box, width: float, height: float, pad: float) -> bool:
    x0, y0, x1, y1 = box
    return x1 >= -pad and y1 >= -pad and x0 <= width + pad and y0 <= height + pad


def ring_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _fmt(v: int) -> str:
    # v in Zehntelpixeln; "12.3", "-4", ".5" statt "0.5"
    s = f"{v / 10:.1f}".rstrip("0").rstrip(".")
    if s.startswith("0."):
        return s[1:]
    if s.startswith("-0."):
        return "-" + s[2:]
    return s or "0"


def path_d(polylines: Iterable[np.ndarray], closed: bool) -> str:
    """Viele Linienzuege als EIN Pfad, relativ in Zehntelpixeln.

    Gerundet wird vor dem Differenzieren: die Summe der relativen Schritte
    trifft damit exakt den gerundeten Endpunkt, statt Rundungsfehler ueber
    den Linienzug anzuhaeufen.
    """
    teile: List[str] = []
    for pts in polylines:
        q = np.rint(pts * 10).astype(np.int64)
        # doppelte Punkte, die erst durch das Runden entstehen, fallen weg
        q = q[np.r_[True, np.any(np.diff(q, axis=0) != 0, axis=1)]]
        if closed and len(q) > 1 and (q[0] == q[-1]).all():
            q = q[:-1]      # den Rueckweg zum Start uebernimmt das "z"
        if len(q) < 2:
            continue
        schritte = np.diff(q, axis=0)
        teile.append(
            f"M{_fmt(q[0, 0])} {_fmt(q[0, 1])}l"
            + " ".join(f"{_fmt(dx)} {_fmt(dy)}" for dx, dy in schritte)
            + ("z" if closed else "")
        )
    return "".join(teile)
