"""
Topologische Karte der S-Bahn Berlin, Stand 2026.

Das Netz kommt aus `berlin_2026.py` -- Linien als Stationsfolgen und ihre
Farben. Die Geometrie ist neu: die Linien liegen auf den echten Gleisen aus
OSM, ueber der Grundkarte aus `berlin_basemap.py`. Anders als im
schematischen Plan hat hier jede Linie ihre eigene Spur, auch innerhalb
einer Familie (S2, S25 und S26 nebeneinander); nur ein zeitweiser Ast
(`branch_of`) liegt auf der Spur seiner Linie.

    python berlin_top_2026.py [ziel.svg]
        ->  outputs/berlin_sbahn_topologische_karte_2026.svg (und .png)

Die Gleise und Halte kommen wie die Grundkarte aus dem Cache (`cache/osm/`).
"""

import sys
from pathlib import Path as FilePath
from typing import List

import numpy as np

from berlin_2026 import NET
from berlin_basemap import BASEMAP
from topomap import (
    Beside, Corridor, build_network, bundle_slots, line_paths, lines_svg,
    build_tracks, locate_stations, route_edges, write_basemap,
)
from topomap.centerline import centerline_graph, track_lines
from topomap.labels import (
    Crossing, LabelStyle, TrackName, auto, beside, corner, fixed, marks_svg,
    station_labels, station_marks, toward, track_labels,
)
from topomap.osm import overpass

# ============================================================================
# DATEN AUS OSM
# ============================================================================

_BOX = ",".join(f"{v:.5f}" for v in BASEMAP.data_bbox)

# Die Gleise: alles, was eine S-Bahn-Linienrelation befaehrt, dazu jedes
# S-Bahn-Gleis ohne Nebengleis-Attribut -- falls eine Linie im Plan einen Weg
# nimmt, den OSM gerade keiner Relation zuordnet.
GLEISE = f'''[out:json][timeout:300];
rel["route"~"^(light_rail|train)$"]["ref"~"^S[0-9]"]({_BOX})->.r;
.r out tags;
(way(r.r)["railway"]; way["railway"="light_rail"][!"service"]({_BOX}););
out geom;'''

# Die Halte: Haltepositionen der Relationen und alle Bahnhoefe im Gebiet
HALTE = f'''[out:json][timeout:300];
rel["route"~"^(light_rail|train)$"]["ref"~"^S[0-9]"]({_BOX})->.r;
(node(r.r); node["railway"~"^(station|halt)$"]({_BOX}););
out;'''

# ============================================================================
# DARSTELLUNG
# ============================================================================

# Meter -> Pixel dieser Karte
_M = BASEMAP.view.scale

LINE_WIDTH = 3.6
SLOT_SPACING = 4.2          # Mitte zu Mitte; der Rest ist der weisse Spalt
STATION_MAX_DIST = 250 * _M  # so weit darf ein OSM-Halt vom Gleis liegen
ROUTE_RADIUS = 300 * _M      # Gleisknoten, die noch zur Station zaehlen
MITTELGLEIS_BAND = 40 * _M   # Gleise naeher als 2 x das werden eine Strecke
MERGE_TOLERANCE = 1.0        # Wege auf dem Mittelgleis liegen exakt aufeinander
MARKE_REICHWEITE = 35.0      # so weit darf eine Linie von ihrer Station liegen


# Seitenlage von Hand, wo die Automatik anders entscheiden wuerde: Linie ->
# Linien, links von denen sie in IHRER Fahrtrichtung liegt, auf der ganzen
# gemeinsamen Strecke.
LEFT_OF = {
    # Von Sueden durch den Nord-Sued-Tunnel bis Schoenholz links der S25;
    # dahinter biegt die S25 auf die Kremmener Bahn ab und kreuzt die S1
    # dort, statt vorher auf der ganzen Strecke links zu liegen
    "S1": ["S25"],
    # Goerlitzer Bahn von Suedwest nach Nordost: S47, S46, S9, S85, S8.
    # S46 und S47 sind nach Nordwesten notiert, Suedwest ist fuer sie links.
    # Die S47 liegt ganz aussen -- auch am Ring, wo aussen (Sueden) in
    # ihrer Fahrtrichtung West ebenfalls links ist
    "S46": ["S8", "S85", "S9"],
    "S47": ["S8", "S85", "S9", "S46", "S41", "S42"],
    # Gesundbrunnen: die Nord-Sued-Linien innen am Ring, bis sie nach
    # Norden abbiegen. Der Ring ist im Uhrzeigersinn notiert, innen ist
    # fuer ihn rechts
    "S41": ["S1", "S2", "S25", "S26"],
    "S42": ["S1", "S2", "S25", "S26"],
    # Bornholmer Strasse: die S8 ganz rechts (Osten). Sie ist suedwaerts
    # notiert, Osten ist fuer sie links. Gegenueber S2 und S26 auf der
    # ganzen Strecke bis Blankenburg: die S8 kreuzt die S2 erst, wo sie auf
    # den Aussenring abbiegt
    "S8": ["S2", "S26"] + [
        Beside(l, at=("bornholmer_strasse",)) for l in ("S1", "S25", "S85")
    ],
}


# Dasselbe fuer rechts. Der Ring: S41 und S42 trennen sich nie, ohne
# Vorgabe entschiede nur der Name, und das kippt an Stuecken, auf denen
# weitere Linien mitfahren. In berlin_2026 sind beide mit derselben
# Stationsfolge im Uhrzeigersinn notiert (die Gegenrichtung der S42 steht
# nur im Label) -- rechts heisst fuer beide also innen, und die S41 liegt
# innen.
RIGHT_OF = {
    # die S15 aussen am Ring (aussen ist fuer den Ring links)
    "S41": ["S42", "S15"],
    "S42": ["S15"],
    # Goerlitzer Bahn, Fortsetzung: S9, S85 und S8 fahren nach Suedosten,
    # Suedwest ist fuer sie rechts. S85 und S8 teilen sich auch den
    # Nordostring -- die Vorgabe gilt nur, wo die S9 mitfaehrt
    "S9": ["S85", "S8"],
    # Bornholmer Strasse: die S85 ganz links (Westen), suedwaerts notiert
    # also rechts. Gegenueber S1 und S25 auf der ganzen Nordbahn -- sonst
    # schwenkte sie gleich hinter dem Bahnhof wieder zurueck; die S25
    # kreuzt dafuer hinter Schoenholz beide, wo sie ohnehin abbiegt
    "S85": [Beside("S8", along=("S9",)), "S1", "S25"] + [
        Beside(l, at=("bornholmer_strasse",)) for l in ("S2", "S26")
    ],
}

# Korridore: feste Spurlagen von Hand, wie in netmap. Versatz in Spuren,
# positiv RECHTS der Schreibrichtung, 0 auf dem Gleis.
_RING_GEGEN_UHRZEIGER = [
    s for s in reversed(NET.lines["S41"].steps) if isinstance(s, str)
]
KORRIDORE = [
    # Ring gegen den Uhrzeigersinn geschrieben: rechts ist aussen. S42 auf
    # dem Gleis, S41 innen daneben, die S9 noch weiter innen; aussen S85
    # und S8 (Nordosten), S46 und S47 (Sueden), S15 (Norden)
    Corridor(_RING_GEGEN_UHRZEIGER, {
        "S42": 0.0, "S41": -1.0, "S9": -2.0,
        "S85": 1.0, "S8": 2.0, "S46": 1.0, "S47": 2.0, "S15": 1.0,
        # Nord-Sued-Linien bei Gesundbrunnen innen neben der S41
        "S1": -2.0, "S25": -3.0, "S26": -4.0, "S2": -5.0,
    }),
    # Nord-Sued-Tunnel nach Norden geschrieben: rechts ist Osten
    Corridor(["anhalter_bahnhof", "potsdamer_platz", "brandenburger_tor",
              "friedrichstrasse", "oranienburger_strasse", "nordbahnhof",
              "humboldthain", "gesundbrunnen"], {
        "S1": -1.5, "S25": -0.5, "S26": 0.5, "S2": 1.5,
    }),
    # Gesundbrunnen -> Bornholmer Strasse, nach Norden: S85 ganz im Westen,
    # S8 ganz im Osten
    Corridor(["gesundbrunnen", "bornholmer_strasse"], {
        "S85": -2.5, "S1": -1.5, "S25": -0.5, "S26": 0.5, "S2": 1.5, "S8": 2.5,
    }),
    # Goerlitzer Bahn stadteinwaerts geschrieben: rechts ist Nordost
    Corridor(["adlershof", "johannisthal", "schoeneweide", "baumschulenweg",
              "plaenterwald", "treptower_park"], {
        # S85 und S8 behalten ihren Ring-Versatz (+1, +2), die S9 wechselt
        # von innen am Ring (-2) auf das Gleis; S46 und S47 links daneben
        "S47": -2.0, "S46": -1.0, "S9": 0.0, "S85": 1.0, "S8": 2.0,
    }),
    # Ganz im Norden, nach Norden geschrieben: die S1 bleibt auf ihrem
    # Gleis, die S8 legt sich nur rechts (oestlich) daneben
    Corridor(["frohnau", "hohen_neuendorf", "birkenwerder"], {
        "S1": 0.0, "S8": 1.0,
    }),
    # Zwischen Adlershof und dem Abzweig zum BER fahren noch alle vier: die
    # Lage der Goerlitzer Bahn bleibt, S46 und S8 schwenken erst hinter dem
    # Abzweig ein
    Corridor(["gruenau", "adlershof"], {
        "S46": -1.0, "S9": 0.0, "S85": 1.0, "S8": 2.0,
    }, complete=True),
    # Hinter Adlershof, wenn S9 und S85 abgezweigt sind: S46 und S8 mittig
    Corridor(["wildau", "zeuthen", "eichwalde", "gruenau", "adlershof"], {
        "S46": -0.5, "S8": 0.5,
    }),
]

# Spurlage von Hand verlaengern: Linie -> [(a, b, Meter)]. Zwischen a und b
# reicht die Lage auf der Seite von b um so viel weiter in den Spurwechsel
# hinein. Die S9 bleibt 50 m laenger auf ihrer Spur an der Goerlitzer Bahn
# und schneidet die Ringlinien beim Einschwenken kuerzer
LAGE_LAENGER = {
    "S9": [("treptower_park", "plaenterwald", 50)],
}

# Linien, die vor ihrer Endstation aufhoeren: Linie -> (Station, Meter).
# Das Ende liegt so nicht mitten im Bahnhof, wo die anderen Linien
# weiterfahren
ENDE_FRUEHER = {
    "S47": ("suedkreuz", 100),
    "S15": ("gesundbrunnen", 100),
}


def _kuerzen(pts: np.ndarray, station, laenge: float) -> np.ndarray:
    """Den Linienzug an dem Ende, das naeher an `station` liegt, um
    `laenge` Pixel kuerzen."""
    vorn = np.hypot(*(pts[0] - station)) < np.hypot(*(pts[-1] - station))
    zug = pts[::-1] if vorn else pts
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(zug, axis=0).T))]
    ende = s[-1] - laenge
    k = int(np.searchsorted(s, ende))
    rest = np.vstack([zug[:k], [np.interp(ende, s, zug[:, 0]), np.interp(ende, s, zug[:, 1])]])
    return rest[::-1] if vorn else rest


# Streckennamen: kursiv an der Strecke, zwischen zwei Stationen
STRECKEN = [
    TrackName("Nordbahn", "waidmannslust", "frohnau", line="S1", side="rechts"),
    # auf dem ersten langen Geraden hinter Hennigsdorf
    TrackName("Kremmener Bahn", "hennigsdorf", "heiligensee", start=2500 * _M, side="links"),
    # kurz hinter der Landesgrenze
    TrackName("Stettiner Bahn", "buch", "roentgental", start=2600 * _M),
    TrackName("Stettiner Bahn", "pankow_heinersdorf", "blankenburg", shift=-15 - 1000 * _M),
    TrackName("Außenring", "schoenfliess", "muehlenbeck_moenchmuehle"),
    TrackName("Außenring", "gehrenseestrasse", "hohenschoenhausen", side="rechts"),
    TrackName("Wriezener Bahn", "marzahn", "raoul_wallenberg_strasse", shift=1000 * _M),
    TrackName("Ostbahn", "kaulsdorf", "mahlsdorf", shift=-1500 * _M),
    TrackName("Schlesische Bahn", "wuhlheide", "koepenick", shift=-1500 * _M),
    TrackName("Görlitzer Bahn", "johannisthal", "adlershof", shift=25),
    TrackName("Görlitzer Bahn", "eichwalde", "zeuthen"),
    TrackName("Zweigbahn\nSchöneweide–\nSpindlersfeld", "oberspree", "spindlersfeld",
              horizontal=True),
    # oestlich der Goerlitzer Bahn, mit Pfeil auf die Verbindungsbahn
    TrackName("Verbindungsbahn\nBaumschulenweg–\nNeukölln", "baumschulenweg", "koellnische_heide",
              horizontal=True, shift=30, arrow=(60, -37)),
    # oberhalb (noerdlich) von S9 und S85, 1 km weiter westlich
    TrackName("Güteraußenring", "gruenbergallee", "schoenefeld", side="rechts",
              shift=2000 * _M),
    TrackName("Dresdner Bahn", "marienfelde", "buckower_chaussee"),
    TrackName("Anhalter Vorortbahn", "lichterfelde_ost", "osdorfer_strasse", side="rechts",
              shift=-500 * _M),
    TrackName("Wannseebahn", "nikolassee", "schlachtensee", line="S1", shift=2000 * _M,
              side="links"),
    TrackName("Stammbahn/Wannseebahn", "lichterfelde_west", "sundgauer_strasse",
              side="rechts", shift=-1000 * _M),
    TrackName("Wetzlarer Bahn", "grunewald", "nikolassee", side="rechts", shift=-500 * _M),
    TrackName("Stammbahn/Wannseebahn", "potsdam_hbf", "griebnitzsee", line="S7"),
    TrackName("Spandauer Vorortbahn", "spandau", "stresow", line="S3", start=300 * _M),
    # Ring: Name aussen, innen die Signets mit Fahrtrichtung
    TrackName("Ringbahn", "jungfernheide", "beusselstrasse", ring=("S41", "S42"),
              against=("S42",)),
    TrackName("Ringbahn", "tempelhof", "hermannstrasse", ring=("S41", "S42"),
              against=("S42",), shift=22, ring_shift=-6),
    TrackName("Stadtbahn", "bellevue", "tiergarten", side="rechts", shift=15),
    # auf Hoehe des Anhalter Bahnhofs
    TrackName("Nord-Süd-\nTunnel", "anhalter_bahnhof", "anhalter_bahnhof", line="S1",
              horizontal=True, side="rechts"),
]

# Wichtige Stationen: bekommen eine Marke und ihren Namen. Umsteige-
# bahnhoefe und die Stellen, an denen sich Linien trennen; die Endpunkte
# kommen von selbst dazu
WICHTIG = [
    "gesundbrunnen", "bornholmer_strasse", "friedrichstrasse",
    "hauptbahnhof", "ostbahnhof", "warschauer_strasse", "ostkreuz",
    "potsdamer_platz", "yorckstrasse", "yorckstrasse_grossgoerschenstrasse",
    "suedkreuz", "schoeneberg", "hermannstrasse", "westkreuz", "westend",
    "schoeneweide", "gruenau", "wannsee", "blankenburg", "zehlendorf",
    "lichtenrade", "buch", "mahlsdorf", "hoppegarten", "friedrichshagen",
    "wildau",
]

# Kreuzungsbahnhoefe: eine Marke ueber die ganze Kreuzungsflaeche der
# beiden Buendel statt zweier Pillen. Am Suedkreuz kreuzen sie sich sehr
# flach: die Marke dort ein wenig begradigt, die Seiten parallel zu den
# Nord-Sued-Linien
KREUZE = {
    "ostkreuz": Crossing(),
    "westkreuz": Crossing(),
    "friedrichstrasse": Crossing(),
    "suedkreuz": Crossing(straighten=0.1, keep="S2"),
    # Schoeneberg: die S1 kreuzt den Ring -- eine Pille ueber die Ringspuren,
    # laengs der S1
    "schoeneberg": Crossing(),
}

# Namen auf der Karte, wo sie anders stehen sollen als im Plan
NAMEN = {
    "yorckstrasse_grossgoerschenstrasse": "Yorckstraße\n(Großgörschenstr.)",
    "westkreuz": "West-\nkreuz",
    "ostkreuz": "Ost-\nkreuz",
    "warschauer_strasse": "Warschauer\nStraße",
    "potsdamer_platz": "Potsdamer\nPlatz",
    "hauptbahnhof": "Haupt-\nbahnhof",
    "friedrichstrasse": "Friedrich-\nstraße",
}

# Wo die Namen stehen: Station -> Lage (siehe topomap.labels.Place). Ohne
# Eintrag sucht die Automatik. Die Regeln, von der allgemeinsten zur
# speziellsten:
#   corner(...)   Standardecke an der Marke, Vorbild Schoeneweide
#   beside(...)   links oder rechts, vertikal zentriert, frei von Linien
#   toward(...)   in eine feste Richtung, mit Feinkorrektur in Pixeln
#   fixed(x, y)   fest in Kartenpixeln, wo nur eine Stelle passt
# shift verschiebt den Block danach noch um (dx, dy) Pixel.
NAMENSLAGE = {
    # Standardecken
    "schoeneweide": corner("bottom_left"),
    "frohnau": corner("bottom_left"),
    "waidmannslust": corner("bottom_left"),
    "lichtenrade": corner("bottom_left"),
    "ostbahnhof": corner("bottom_left", shift=(1, 3)),
    "buch": corner("bottom_right"),
    "blankenburg": corner("bottom_right"),
    "wannsee": corner("bottom_right"),
    "zehlendorf": corner("bottom_right", shift=(-2, 2)),
    # neben der Marke
    "birkenwerder": beside(),
    "yorckstrasse": beside(shift=(1, 1)),
    "yorckstrasse_grossgoerschenstrasse": beside(),
    "potsdamer_platz": beside(shift=(0, 2)),
    "gruenau": beside(shift=(-3, -5)),
    # Automatik mit Feinkorrektur
    "warschauer_strasse": auto(shift=(4, 0)),
    "charlottenburg": auto(shift=(2, 0)),
    # Kreuzungsbahnhoefe: der Name in einer Ecke, zentriert, auch ueber
    # einer Linie; an Westkreuz die S7 vor der S5
    "ostkreuz": toward((1, 1), shift=(-8, 4), align="middle"),
    "westkreuz": toward((-1, 1), shift=(23, 7), align="middle", badge_order=("S7", "S5")),
    "friedrichstrasse": toward((1, 1), shift=(-5, -2)),
    "suedkreuz": toward((1, -1), shift=(4, 16)),
    "schoeneberg": toward((-1, -1), shift=(10, -1)),
    # linksbuendig unter der Pille
    "gesundbrunnen": toward((1, 1), shift=(-16, 5)),
    "hermannstrasse": toward((1, 1), shift=(-6, 2)),
    # seitlich bzw. mittig unter der Marke
    "westend": toward((-1, 0)),
    "strausberg_nord": toward((-1, 0), shift=(-5, 0)),
    "wildau": toward((1, 0), shift=(2, 0)),
    "hoppegarten": toward((0, 1), shift=(0, 1)),
    # "Haupt-/bahnhof" mit S15-Signet links der S15, so nah an der Pille,
    # wie es ohne Linie im Block geht
    "hauptbahnhof": fixed(884.5, 930.0, align="start"),
}


# Linien aus berlin_2026, die hier nicht gezeichnet werden: der HVZ-Ast der
# S85 nach Pankow
WEGLASSEN = {"S85_pankow"}


# Zeichenreihenfolge an Kreuzungen, von unten nach oben: die Nord-Sued-
# Linien (die S1 ueber der S2-Familie), darueber die Stadtbahn (S5 unter
# S3, S3 und S9 unter S7), darueber S8 und S85, ganz oben die Ringlinien in
# Nummernfolge (die S47 ueber der S41). Innerhalb einer Ebene gilt die
# Reihenfolge der Liste
EBENEN = [
    ["S2", "S25", "S26", "S15", "S1"],
    ["S5", "S3", "S9", "S75", "S7"],
    ["S8", "S85"],
    ["S41", "S42", "S46", "S47"],
]


def _draw_order() -> List[str]:
    """Von unten nach oben nach EBENEN; Linien, die dort fehlen, zuunterst.
    Ein Ast liegt unter seiner Linie: sie teilen sich eine Spur, und dort
    soll die Linie selbst zu sehen sein."""
    rang = {lid: (e, i) for e, ebene in enumerate(EBENEN) for i, lid in enumerate(ebene)}

    def schluessel(lid: str):
        haupt = NET.lines[lid].branch_of or lid
        return rang.get(haupt, (-1, 0)) + (NET.lines[lid].branch_of is None,)

    return sorted((lid for lid in NET.lines if lid not in WEGLASSEN), key=schluessel)


# Zwischenenden nach den Taktinformationen von sbahn.berlin (Fahrplan
# 2026, Stand August 2026): Station -> Linien, von denen dort Zuege enden,
# waehrend die Linie weiterfaehrt -- in der HVZ, tagsueber, abends oder am
# Wochenende. Dazu kommen die Enden aus den Zuggruppen in berlin_2026. Enden
# nur im Nachtverkehr zaehlen nicht (S8 Pankow, S9 Westkreuz, S47
# Schoeneweide)
ZWISCHENENDEN_AUSSER_HVZ = {
    "zehlendorf": ["S1"],        # HVZ bis Potsdamer Platz, abends bis Gesundbrunnen
    "gesundbrunnen": ["S1"],     # abends Zehlendorf <> Gesundbrunnen
    "frohnau": ["S1"],           # tagsueber Wannsee <> Frohnau
    "potsdamer_platz": ["S26"],  # Sa+So und Mo-Fr spaet Teltow Stadt <> Potsdamer Platz
    "gruenau": ["S8", "S46"],    # S8 ausserhalb der HVZ; S46 Mo-Fr Verstaerker
    "hermannstrasse": ["S46"],   # Mo-Fr Verstaerker Gruenau <> Hermannstrasse
    "strausberg": ["S5"],        # abends und am Wochenende jeder zweite Zug
    "mahlsdorf": ["S5"],         # abends umsteigen in Mahlsdorf
    "westkreuz": ["S7"],         # abends Ahrensfelde <> Westkreuz
    "blankenburg": ["S8"],       # ab ca. 21 Uhr nur jeder dritte Zug weiter
    "waidmannslust": ["S85"],    # Mo-Fr abends bis Waidmannslust
}


def _zwischenenden(folgen, enden) -> dict:
    """Station -> Linien, von denen dort einzelne Zuggruppen enden, waehrend
    die Linie selbst weiterfaehrt ("Wannsee <> Frohnau" auf der S1). Wie im
    schematischen Plan abgelesen aus den Zuggruppen in berlin_2026: nur
    Anfang und Ende eines Laufwegs zaehlen, Namen, die nicht auf der Linie
    liegen, bleiben ohne Signet."""
    out: dict = {}
    for lid, f in folgen.items():
        if f[0] == f[-1]:
            continue                                   # Ring
        auf_linie = {NET.stations[sid].name: sid for sid in f}
        zeigt = NET.lines[lid].branch_of or lid
        for gruppe in NET.lines[lid].groups:
            teile = gruppe.route.split("<>")
            if len(teile) < 2:
                continue
            for name in (teile[0], teile[-1]):
                sid = auf_linie.get(name.strip())
                if sid is None or sid in (f[0], f[-1]):
                    continue
                if zeigt in [l for l, _ in enden.get(sid, ())]:
                    continue
                out.setdefault(sid, [])
                if zeigt not in out[sid]:
                    out[sid].append(zeigt)
    for sid, lids in ZWISCHENENDEN_AUSSER_HVZ.items():
        for lid in lids:
            f = folgen.get(lid, ())
            if sid not in f or sid in (f[0], f[-1]):
                raise ValueError(f"Zwischenende {sid}: liegt nicht innen auf der {lid}")
            if lid not in out.setdefault(sid, []):
                out[sid].append(lid)
    return out


def build_overlay() -> str:
    gleise = overpass(GLEISE, BASEMAP.cache_dir, "sbahn_gleise")["elements"]
    halte = overpass(HALTE, BASEMAP.cache_dir, "sbahn_halte")["elements"]
    # Mittelgleis: aus allen Einzelgleisen eine Mittellinie je Strecke
    graph = centerline_graph(track_lines(gleise, BASEMAP.project), radius=MITTELGLEIS_BAND)

    folgen = {
        lid: [s for s in NET.lines[lid].steps if isinstance(s, str)]
        for lid in _draw_order()
    }
    benutzt = {s for f in folgen.values() for s in f}
    stationen = locate_stations(
        {sid: NET.stations[sid].name for sid in sorted(benutzt)},
        halte, graph, BASEMAP.project, max_dist=STATION_MAX_DIST,
    )
    kanten = route_edges(
        [(a, b) for f in folgen.values() for a, b in zip(f, f[1:])],
        stationen, graph, radius=ROUTE_RADIUS,
    )
    # Stufe 1: Gleislage -- Trassen, an denen sich alle Linien orientieren
    netz = build_tracks(
        build_network(folgen, kanten, tolerance=MERGE_TOLERANCE),
        lane=SLOT_SPACING,
    )
    # Stufe 2: Linien neben den Trassen
    ringe = [lid for lid, f in folgen.items() if f[0] == f[-1]]
    slots = bundle_slots(
        netz,
        tracks={lid: l.branch_of for lid, l in NET.lines.items() if l.branch_of},
        rings=ringe,
        left_of=LEFT_OF,
        right_of=RIGHT_OF,
        corridors=KORRIDORE,
    )
    paths = line_paths(
        netz, slots, spacing=SLOT_SPACING, rings=ringe,
        hold={lid: [(a, b, m * _M) for a, b, m in l] for lid, l in LAGE_LAENGER.items()},
    )
    for lid, (station, meter) in ENDE_FRUEHER.items():
        if lid in paths:
            paths[lid] = _kuerzen(paths[lid], np.array(stationen[station]), meter * _M)
    print(f"  {len(stationen)} Stationen, {len(paths)} Linien")

    # Endpunkte: Station -> Linien, die dort enden, mit ihrem Ende
    enden: dict = {}
    for lid, f in folgen.items():
        if lid in ringe:
            continue
        enden.setdefault(f[0], []).append((lid, "start"))
        enden.setdefault(f[-1], []).append((lid, "end"))
    zwischen = _zwischenenden(folgen, enden)
    stil = LabelStyle(font_family=BASEMAP.font_family)
    markiert = list(enden) + [sid for sid in WICHTIG if sid not in enden]
    markiert += [sid for sid in zwischen if sid not in markiert]
    halt = {sid: [lid for lid, f in folgen.items() if sid in f] for sid in markiert}
    marken = station_marks(paths, stationen, halt, enden, spacing=SLOT_SPACING,
                           reach=MARKE_REICHWEITE, crossings=KREUZE)
    return (
        lines_svg(paths, NET.colors, width=LINE_WIDTH)
        + track_labels(paths, stationen, folgen, STRECKEN, colors=NET.colors,
                       style=stil, line_width=LINE_WIDTH)
        + marks_svg(marken, stil)
        + station_labels(
            paths, marken, {sid: NAMEN.get(sid, NET.stations[sid].name) for sid in markiert},
            NET.colors,
            badges={sid: [lid for lid, _ in l] for sid, l in enden.items()},
            ends=enden, hollow=zwischen, place=NAMENSLAGE, style=stil,
        )
    )


ZIEL = FilePath("outputs/berlin_sbahn_topologische_karte_2026.svg")
# Das PNG in 1,5-facher Aufloesung: die feine Beschriftung bleibt scharf,
# die Datei nicht zu gross
PNG_SKALIERUNG = 1.5


if __name__ == "__main__":
    write_basemap(
        BASEMAP, sys.argv[1] if len(sys.argv) > 1 else ZIEL,
        overlay=build_overlay(), png_scale=PNG_SKALIERUNG,
    )
