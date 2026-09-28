"""
topomap -- Engine fuer topografische Grundkarten aus OpenStreetMap.

Anders als `netmap` rechnet hier nichts schematisch: jede Linie liegt dort,
wo sie in OSM liegt. Eine Karte ist ein Ausschnitt (`View`) und ein Stapel
von Ebenen (`Layer`), jede mit ihrer Overpass-Abfrage und ihrem Stil.

    from topomap import Basemap, Layer, View, write_basemap

    karte = Basemap(
        view=View(center=(13.40, 52.52), width_km=70, height_km=54),
        layers=[Layer("wasser", ['way["natural"="water"]'], kind="area",
                      fill="#eef6fd")],
    )
    write_basemap(karte, "outputs/karte.svg")

Die Overpass-Antworten landen im Cache (`Basemap.cache_dir`); nur der erste
Lauf braucht das Netz. `Basemap.project(lon, lat)` liefert die Pixel-
koordinaten auf derselben Karte -- fuer alles, was spaeter darueber kommt.
"""

from .lines import (
    Beside, Corridor, Network, Piece, TrackGraph, build_network, build_tracks,
    bundle_slots, line_paths, lines_svg, locate_stations, normalize_name, route_edges,
)
from .model import Basemap, Layer
from .projection import View, transverse_mercator
from .render import layer_path, render_svg, write_basemap

__all__ = [
    "Basemap", "Layer", "View", "transverse_mercator",
    "layer_path", "render_svg", "write_basemap",
    "TrackGraph", "locate_stations", "normalize_name", "route_edges",
    "Beside", "Corridor", "Network", "Piece", "build_network", "bundle_slots", "line_paths",
    "lines_svg", "build_tracks",
]
