"""
Die Datentypen einer Grundkarte: Ebenen und die Karte selbst.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Tuple

from .projection import View


@dataclass(frozen=True)
class Layer:
    """Eine Ebene: was aus OSM geholt wird und wie es aussieht.

    name        Kennung; steht als id am SVG-Element und im Cache-Dateinamen
    selectors   Overpass-Selektoren ohne Ausschnitt, etwa
                'way["highway"="primary"]'; der Ausschnitt wird angehaengt
    kind        "line" -- jeder Weg ein Linienzug, Relationen tragen ihre
                Mitgliedswege bei; "area" -- geschlossene Wege und
                Multipolygone als Flaechen
    fill        Flaechenfarbe, nur fuer "area"
    stroke      Linienfarbe; bei "area" die Kontur
    width       Linienbreite in Pixeln
    dash        SVG-stroke-dasharray, z.B. "4 2"
    opacity     Deckkraft der ganzen Ebene
    simplify    Douglas-Peucker-Toleranz in Pixeln
    min_area    Flaechen darunter (Pixel^2) fallen weg -- Teiche, die auf der
                Karte nur ein Fleck waeren
    tags        False holt nur die Geometrie (`out skel`); reicht immer,
                solange der Stil an der Ebene haengt und nicht am Objekt
    """

    name: str
    selectors: Sequence[str]
    kind: str = "line"
    fill: Optional[str] = None
    stroke: Optional[str] = None
    width: float = 1.0
    dash: Optional[str] = None
    opacity: float = 1.0
    simplify: float = 0.35
    min_area: float = 0.0
    tags: bool = False

    def __post_init__(self) -> None:
        if self.kind not in ("line", "area"):
            raise ValueError(f"Ebene {self.name}: kind ist 'line' oder 'area'")
        if self.kind == "line" and not self.stroke:
            raise ValueError(f"Ebene {self.name}: eine Linienebene braucht stroke")


@dataclass(frozen=True)
class Basemap:
    """Eine Grundkarte: Ausschnitt, Ebenen von unten nach oben, Rahmen.

    background   Farbe hinter allem, also das Umland
    attribution  steht klein in der Ecke rechts unten; die ODbL verlangt
                 den Hinweis auf OpenStreetMap
    cache_dir    wohin die Overpass-Antworten gehen
    data_bbox    (Sued, West, Nord, Ost) des Datengebiets. Ohne Angabe folgt
                 es dem Ausschnitt -- dann ist jede Aenderung am Ausschnitt
                 eine neue Abfrage und damit ein neuer Download. Fest
                 gesetzt bleibt der Cache gueltig, solange der Ausschnitt
                 innerhalb liegt
    """

    view: View
    layers: Sequence[Layer]
    background: str = "#ffffff"
    attribution: Optional[str] = "© OpenStreetMap-Mitwirkende"
    cache_dir: Path = field(default=Path("cache/osm"))
    data_bbox: Optional[Tuple[float, float, float, float]] = None
    font_family: str = "Myriad Pro, Source Sans Pro, Helvetica, Arial, sans-serif"

    def project(self, lon, lat) -> Tuple:
        """Laenge/Breite -> Kartenpixel; fuer alles, was spaeter darueber
        gezeichnet wird, damit es auf derselben Karte landet."""
        return self.view.project(lon, lat)
