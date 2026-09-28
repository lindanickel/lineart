"""
Grundkarte Berlin aus OpenStreetMap -- der Unterbau fuer einen
geografischen S-Bahn-Plan.

Ausschnitt und Ebenen folgen der OSM-basierten Streckenkarte "Berlin
S-Bahn Streckennetz": Berlin cremefarben auf weissem Umland, Gemeinde-
grenzen, Gewaesser, ein blasses Strassennetz, darueber U-Bahn und
Strassenbahn als duenne farbige Linien. Die S-Bahn-Linien fehlen noch; sie
kommen spaeter ueber `BASEMAP.project()` auf dieselbe Karte.

Der erste Lauf laedt einige hundert MB von Overpass und dauert ein paar
Minuten; danach kommt alles aus `cache/osm/`.
"""

import sys
from pathlib import Path as FilePath

from topomap import Basemap, Layer, View, write_basemap

# ============================================================================
# FARBEN
# ============================================================================

LAND_BERLIN = "#feffef"
WASSER = "#eef6fd"
UFER = "#8cc3ec"
STRASSE_NEBEN = "#e8e8e8"
STRASSE_HAUPT = "#d4d4d4"
BAHN = "#a9a9a9"
GRENZE = "#9b9b9b"
TRAM = "#e8779f"
UBAHN = "#6d9fdb"

BERLIN = 'relation["boundary"="administrative"]["admin_level"="4"]["name"="Berlin"]'

# ============================================================================
# EBENEN, von unten nach oben
# ============================================================================

LAYERS = [
    Layer("berlin", [BERLIN], kind="area", fill=LAND_BERLIN, tags=True),
    Layer(
        "wasser",
        [
            'way["natural"="water"]',
            'relation["natural"="water"]',
            'way["waterway"="riverbank"]',
            'relation["waterway"="riverbank"]',
        ],
        kind="area", fill=WASSER, stroke=UFER, width=0.4, min_area=3.0,
        tags=True,
    ),
    Layer(
        "fluesse",
        ['way["waterway"~"^(river|canal)$"]'],
        stroke=UFER, width=0.6,
    ),
    Layer(
        "strassen_neben",
        ['way["highway"~"^(residential|unclassified|living_street)$"]'],
        stroke=STRASSE_NEBEN, width=0.45, simplify=0.5,
    ),
    Layer(
        "strassen_haupt",
        ['way["highway"~"^(motorway|trunk|primary|secondary|tertiary)(_link)?$"]'],
        stroke=STRASSE_HAUPT, width=0.8,
    ),
    Layer(
        "bahn",
        ['way["railway"~"^(rail|light_rail)$"][!"service"]'],
        stroke=BAHN, width=0.6,
    ),
    Layer(
        "grenzen",
        ['relation["boundary"="administrative"]["admin_level"="8"]'],
        stroke=GRENZE, width=0.9,
    ),
    # tags=True wie bei der Flaeche: gleiche Abfrage, eine Datei im Cache
    Layer("berlin_grenze", [BERLIN], stroke=GRENZE, width=1.6, tags=True),
    Layer(
        "tram",
        ['way["railway"="tram"][!"service"]'],
        stroke=TRAM, width=0.7,
    ),
    Layer(
        "ubahn",
        ['way["railway"="subway"][!"service"]'],
        stroke=UBAHN, width=0.8,
    ),
]

# ============================================================================
# KARTE
# ============================================================================

# Ausgangspunkt war der Ausschnitt der Vorlage (2000 x 1557 px, 56 x 43,6 km),
# eingepasst an Wannsee, Teltow Stadt, Spandau, Hennigsdorf, Blankenfelde,
# Zeuthen und Erkner. Nach Norden, Osten und Sueden ist er erweitert, bis
# Oranienburg, Strausberg Nord (Name links der Station), Koenigs
# Wusterhausen und Wildau mit ihren Namen hineinpassen, mit mindestens 1 km
# Rand; der Westrand und der Massstab (1 km = 35,7 px) sind geblieben.
BASEMAP = Basemap(
    view=View(center=(13.45455, 52.52542), width_km=63.812, height_km=53.592,
              width_px=2279.0),
    layers=LAYERS,
    cache_dir=FilePath("cache/osm"),
    # Das Datengebiet ist fest und groesser als der Ausschnitt (70 x 54,5 km
    # um 13,445 / 52,53, plus 5 % Rand): Ausschnitt verschieben oder
    # vergroessern bis Oranienburg und Strausberg geht ohne neuen Download.
    # Neue Daten nur, wenn man cache/osm/ loescht.
    data_bbox=(52.26027, 12.87297, 52.79973, 14.01703),
)

ZIEL = FilePath("outputs/berlin_basemap.svg")


if __name__ == "__main__":
    write_basemap(BASEMAP, sys.argv[1] if len(sys.argv) > 1 else ZIEL)
