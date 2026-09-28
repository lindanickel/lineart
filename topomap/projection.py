"""
Der Kartenausschnitt: von Laenge/Breite zu Pixeln.

Projiziert wird transversal-mercatorisch mit dem Mittelmeridian in der
Kartenmitte. Damit ist Norden in der Mitte exakt oben, die Abbildung
winkeltreu, und auf 70 km Breite bleibt die Laengenverzerrung unter einem
Promille -- fuer einen Stadtplan genauer als noetig. UTM (EPSG:25833) waere
die amtliche Wahl, liegt mit 15 Grad Ost aber so weit neben Berlin, dass die
Karte um gut einen Grad gedreht erscheint.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians
from typing import Tuple

import numpy as np

# GRS80, das Ellipsoid von ETRS89
_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)


def _meridian_arc(phi):
    e2, e4, e6 = _E2, _E2 ** 2, _E2 ** 3
    return _A * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * np.sin(2 * phi)
        + (15 * e4 / 256 + 45 * e6 / 1024) * np.sin(4 * phi)
        - (35 * e6 / 3072) * np.sin(6 * phi)
    )


def transverse_mercator(lon, lat, lon0: float, lat0: float):
    """Laenge/Breite in Grad -> (Ost, Nord) in Metern ab (lon0, lat0).

    Reihenentwicklung nach Snyder, "Map Projections -- A Working Manual",
    Gl. 8-9 und 8-10, mit k0 = 1.
    """
    phi = np.radians(np.asarray(lat, dtype=float))
    lam = np.radians(np.asarray(lon, dtype=float) - lon0)
    sin_p, cos_p, tan_p = np.sin(phi), np.cos(phi), np.tan(phi)
    n = _A / np.sqrt(1 - _E2 * sin_p ** 2)
    t = tan_p ** 2
    c = _EP2 * cos_p ** 2
    a = lam * cos_p
    x = n * (
        a
        + (1 - t + c) * a ** 3 / 6
        + (5 - 18 * t + t ** 2 + 72 * c - 58 * _EP2) * a ** 5 / 120
    )
    y = _meridian_arc(phi) - _meridian_arc(radians(lat0)) + n * tan_p * (
        a ** 2 / 2
        + (5 - t + 9 * c + 4 * c ** 2) * a ** 4 / 24
        + (61 - 58 * t + t ** 2 + 600 * c - 330 * _EP2) * a ** 6 / 720
    )
    return x, y


@dataclass(frozen=True)
class View:
    """Ein rechteckiger Ausschnitt, festgelegt in Kilometern um eine Mitte.

    center      (Laenge, Breite) der Kartenmitte in Grad
    width_km    Breite und Hoehe des Ausschnitts
    height_km
    width_px    Breite der Karte in Pixeln; die Hoehe folgt aus dem
                Seitenverhaeltnis, der Massstab ist in beiden Richtungen
                derselbe
    """

    center: Tuple[float, float]
    width_km: float
    height_km: float
    width_px: float = 2000.0

    @property
    def scale(self) -> float:
        """Pixel pro Meter."""
        return self.width_px / (self.width_km * 1000.0)

    @property
    def height_px(self) -> float:
        return self.height_km * 1000.0 * self.scale

    def project(self, lon, lat):
        """Laenge/Breite -> Pixel, Ursprung links oben, y nach unten."""
        x, y = transverse_mercator(lon, lat, *self.center)
        return (
            x * self.scale + self.width_px / 2,
            self.height_px / 2 - y * self.scale,
        )

    def bbox(self, margin: float = 0.05) -> Tuple[float, float, float, float]:
        """(Sued, West, Nord, Ost) in Grad, fuer die Overpass-Abfrage.

        Grosszuegig statt exakt: ein paar Prozent Rand, damit am Kartenrand
        keine Strasse abreisst, bevor der Clip sie abschneidet. Die
        Breitenabhaengigkeit der Laengengrade wird am weiter vom Aequator
        entfernten Rand genommen, dort sind sie am kuerzesten.
        """
        lon0, lat0 = self.center
        dlat = self.height_km * (0.5 + margin) / 111.13
        rand = abs(lat0) + dlat
        dlon = self.width_km * (0.5 + margin) / (111.32 * cos(radians(rand)))
        return (lat0 - dlat, lon0 - dlon, lat0 + dlat, lon0 + dlon)
