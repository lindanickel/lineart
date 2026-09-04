"""
Netzdefinition Berlin, Stand 2026: S-Bahn.

Ausfuehren erzeugt die Karte:

    python3 berlin_2026.py [ziel.svg]     ->  outputs/berlin_sbahn_2026.svg

Enthaelt alles, was diese eine Karte ausmacht -- Stationen, Linienfarben, die
gemeinsam befahrenen Strecken, die Linien selbst, die von Hand festgelegten
Spurlagen und die Feinkorrekturen der Beschriftung. Die Engine kennt davon
nichts; sie bekommt am Ende nur das `NET`-Objekt.

Strecken statt Linien
---------------------
Die physischen Strecken (Stadtbahn, Ring, Nord-Sued-Tunnel, ...) sind einmal
als Liste definiert -- inklusive ihrer Turns und Paths -- und werden in den
Liniendefinitionen per `_slice()` bzw. `_reversed()` wiederverwendet. Eine
Aenderung an einer Strecke wirkt damit auf alle Linien, die sie befahren.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from math import sqrt
from pathlib import Path as FilePath
from typing import Dict, List, Optional, Sequence, Tuple

from netmap import (
    CFG, Corridor, FixPath, FlexPath, Net, Path, Pt, Station, Step, TrainGroup,
    Turn, TurnLine, station_registry, write_map,
)

_station_registry = station_registry


STATIONS: Dict[str, Station] = _station_registry([
    Station("adlershof", "Adlershof", kind="hub", label_pos="top_right"),
    Station("ahrensfelde", "Ahrensfelde", label_pos="right"),
    Station("alexanderplatz", "Alexanderplatz", label="Alexander-\nplatz", label_pos="top"),
    Station("alt_reinickendorf", "Alt-Reinickendorf"),
    Station("altglienicke", "Altglienicke", label_pos="top_left"),
    Station("anhalter_bahnhof", "Anhalter Bahnhof"),
    Station("attilastrasse", "Attilastraße", label_pos="top_right"),
    Station("babelsberg", "Babelsberg", label_pos="top_left"),
    Station("baumschulenweg", "Baumschulenweg"),
    Station("bellevue", "Bellevue", label_pos="bottom"),
    Station("bergfelde", "Bergfelde", label_pos="top_right"),
    Station("bernau", "Bernau", label_pos="right"),
    Station("bernau_friedenstal", "Bernau-Friedenstal"),
    Station("betriebsbahnhof_rummelsburg", "Betriebsbahnhof Rummelsburg", label="Betriebsbahnhof\nRummelsburg", label_pos="top_right"),
    Station("beusselstrasse", "Beusselstraße", label="Beussel-\nstraße", label_pos="bottom"),
    Station("biesdorf", "Biesdorf"),
    Station("birkenstein", "Birkenstein"),
    Station("birkenwerder", "Birkenwerder", kind="hub"),
    Station("blankenburg", "Blankenburg", kind="hub"),
    Station("blankenfelde", "Blankenfelde", label_pos="right"),
    Station("borgsdorf", "Borgsdorf"),
    Station("bornholmer_strasse", "Bornholmer Straße", label="Bornholmer\nStraße", label_pos="right", kind="hub"),
    Station("botanischer_garten", "Botanischer Garten"),
    Station("brandenburger_tor", "Brandenburger Tor"),
    Station("buch", "Buch"),
    Station("buckower_chaussee", "Buckower Chaussee", label_pos="top_right"),
    Station("bundesplatz", "Bundesplatz", label_pos="bottom"),
    Station("charlottenburg", "Charlottenburg", label="Charlotten-\nburg", label_pos="bottom"),
    Station("eichborndamm", "Eichborndamm"),
    Station("eichwalde", "Eichwalde", label_pos="top_right"),
    Station("erkner", "Erkner", label_pos="right"),
    Station("feuerbachstrasse", "Feuerbachstraße"),
    Station("flughafen_ber", "Flughafen BER", kind="hub", label_pos="left"),
    Station("frankfurter_allee", "Frankfurter Allee", label="Frankfurter\nAllee", label_pos="right"),
    Station("fredersdorf", "Fredersdorf"),
    Station("friedenau", "Friedenau"),
    Station("friedrichsfelde_ost", "Friedrichsfelde Ost"),
    Station("friedrichshagen", "Friedrichshagen", label="Friedrichs-\nhagen"),
    Station("friedrichstrasse", "Friedrichstraße", label="Friedrich-\nstraße", kind="hub", label_pos="top_right"),
    Station("frohnau", "Frohnau", kind="hub"),
    Station("gehrenseestrasse", "Gehrenseestraße"),
    Station("gesundbrunnen", "Gesundbrunnen", label="Gesund-\nbrunnen", kind="hub", label_pos="top_left"),
    Station("greifswalder_strasse", "Greifswalder Straße", label="Greifswalder\nStraße", label_pos="top"),
    Station("griebnitzsee", "Griebnitzsee", label_pos="top_left"),
    Station("grunewald", "Grunewald", label_pos="top_left"),
    Station("gruenau", "Grünau", label_pos="top_right"),
    Station("gruenbergallee", "Grünbergallee", label_pos="top_left"),
    Station("hackescher_markt", "Hackescher Markt", label="Hackescher\nMarkt", label_pos="bottom"),
    Station("halensee", "Halensee", label_pos="left"),
    Station("hauptbahnhof", "Hauptbahnhof", label="Haupt-\nbahnhof", kind="hub", label_pos="top_left"),
    Station("heerstrasse", "Heerstraße"),
    Station("hegermuehle", "Hegermühle", label_pos="right"),
    Station("heidelberger_platz", "Heidelberger Platz", label="Heidelberger\nPlatz", label_pos="bottom_left"),
    Station("heiligensee", "Heiligensee"),
    Station("hennigsdorf", "Hennigsdorf", label_pos="left"),
    Station("hermannstrasse", "Hermannstraße", label="Hermann-\nstraße", label_pos="bottom"),
    Station("hermsdorf", "Hermsdorf"),
    Station("hirschgarten", "Hirschgarten"),
    Station("hohen_neuendorf", "Hohen Neuendorf"),
    Station("hohenschoenhausen", "Hohenschönhausen"),
    Station("hohenzollerndamm", "Hohenzollerndamm", label="Hohenzollern-\ndamm", label_pos="left"),
    Station("hoppegarten", "Hoppegarten"),
    Station("humboldthain", "Humboldthain"),
    Station("innsbrucker_platz", "Innsbrucker Platz", label="Innsbrucker\nPlatz", label_pos="bottom"),
    Station("jannowitzbruecke", "Jannowitzbrücke", label="Jannowitz-\nbrücke", label_pos="bottom"),
    Station("johannisthal", "Johannisthal"),
    Station("julius_leber_bruecke", "Julius-Leber-Brücke", label_pos="top_left"),
    Station("jungfernheide", "Jungfernheide", label_pos="top"),
    Station("karl_bonhoeffer_nervenklinik", "Karl-Bonhoeffer-Nervenklinik"),
    Station("karlshorst", "Karlshorst", label_pos="top_right"),
    Station("karow", "Karow"),
    Station("kaulsdorf", "Kaulsdorf"),
    Station("koellnische_heide", "Köllnische Heide", label="Köllnische\nHeide", label_pos="top"),
    Station("koenigs_wusterhausen", "Königs Wusterhausen", label_pos="right"),
    Station("koepenick", "Köpenick"),
    Station("landsberger_allee", "Landsberger Allee", label="Landsberger\nAllee", label_pos="top_right"),
    Station("lankwitz", "Lankwitz"),
    Station("lehnitz", "Lehnitz"),
    Station("lichtenberg", "Lichtenberg"),
    Station("lichtenrade", "Lichtenrade", label_pos="top_right"),
    Station("lichterfelde_ost", "Lichterfelde Ost"),
    Station("lichterfelde_sued", "Lichterfelde Süd"),
    Station("lichterfelde_west", "Lichterfelde West"),
    Station("mahlow", "Mahlow", label_pos="top_right"),
    Station("mahlsdorf", "Mahlsdorf"),
    Station("marienfelde", "Marienfelde", label_pos="top_right"),
    Station("marzahn", "Marzahn"),
    Station("mehrower_allee", "Mehrower Allee"),
    Station("messe_nord_zob", "Messe Nord/ZOB", label="Messe Nord/\nZOB",
            label_pos="left"),
    Station("messe_sued", "Messe Süd (Eichkamp)", label="Messe Süd\n(Eichkamp)"),
    Station("mexikoplatz", "Mexikoplatz", label_pos="bottom"),
    Station("muehlenbeck_moenchmuehle", "Mühlenbeck-Mönchmühle", label="Mühlenbeck-\nMönchmühle", label_pos="top_right"),
    Station("neuenhagen", "Neuenhagen"),
    Station("neukoelln", "Neukölln", label_pos="bottom"),
    Station("nikolassee", "Nikolassee", label_pos="top_left"),
    Station("nordbahnhof", "Nordbahnhof"),
    Station("noeldnerplatz", "Nöldnerplatz"),
    Station("oberspree", "Oberspree"),
    Station("olympiastadion", "Olympiastadion"),
    Station("oranienburg", "Oranienburg"),
    Station("oranienburger_strasse", "Oranienburger Straße", label="Oranienburger\nStraße"),
    Station("osdorfer_strasse", "Osdorfer Straße"),
    Station("ostbahnhof", "Ostbahnhof", label_pos="top"),
    Station("ostkreuz", "Ostkreuz", kind="hub", label_pos="top_left"),
    Station("pankow", "Pankow", kind="hub"),
    Station("pankow_heinersdorf", "Pankow-Heinersdorf"),
    Station("petershagen_nord", "Petershagen Nord", label="Petershagen\nNord"),
    Station("pichelsberg", "Pichelsberg"),
    Station("plaenterwald", "Plänterwald", label_pos="top_right"),
    Station("poelchaustrasse", "Poelchaustraße"),
    Station("potsdam_hbf", "Potsdam Hbf", label_pos="left"),
    Station("potsdamer_platz", "Potsdamer Platz"),
    Station("prenzlauer_allee", "Prenzlauer Allee", label="Prenzlauer\nAllee", label_pos="top"),
    Station("priesterweg", "Priesterweg", label_pos="right"),
    Station("rahnsdorf", "Rahnsdorf"),
    Station("raoul_wallenberg_strasse", "Raoul-Wallenberg-Straße"),
    Station("rathaus_steglitz", "Rathaus Steglitz"),
    Station("rummelsburg", "Rummelsburg", label_pos="top_right"),
    Station("roentgental", "Röntgental"),
    Station("savignyplatz", "Savignyplatz", label="Savigny-\nplatz", label_pos="top"),
    Station("schichauweg", "Schichauweg", label_pos="top_right"),
    Station("schlachtensee", "Schlachtensee", label_pos="bottom"),
    Station("schulzendorf", "Schulzendorf"),
    Station("schoeneberg", "Schöneberg", kind="hub", label_pos="top_left"),
    Station("schoenefeld", "Schönefeld", label_pos="top_left"),
    Station("schoeneweide", "Schöneweide", kind="hub"),
    Station("schoenfliess", "Schönfließ", label_pos="top_right"),
    Station("schoenhauser_allee", "Schönhauser Allee", label="Schönhauser\nAllee", kind="hub", label_pos="bottom"),
    Station("schoenholz", "Schönholz", kind="hub"),
    Station("sonnenallee", "Sonnenallee", label_pos="left"),
    Station("spandau", "Spandau", label_pos="left", kind="hub"),
    Station("spindlersfeld", "Spindlersfeld", label_pos="right"),
    Station("springpfuhl", "Springpfuhl", label_pos="top_left"),
    Station("storkower_strasse", "Storkower Straße", label="Storkower\nStraße", label_pos="right"),
    Station("strausberg", "Strausberg"),
    Station("strausberg_nord", "Strausberg Nord", label_pos="right"),
    Station("strausberg_stadt", "Strausberg Stadt", label_pos="right"),
    Station("stresow", "Stresow"),
    Station("sundgauer_strasse", "Sundgauer Straße"),
    Station("suedende", "Südende"),
    Station("suedkreuz", "Südkreuz", kind="hub", label_pos="top_right"),
    Station("tegel", "Tegel"),
    Station("teltow_stadt", "Teltow Stadt"),
    Station("tempelhof", "Tempelhof", label_pos="bottom"),
    Station("tiergarten", "Tiergarten", label_pos="top"),
    Station("treptower_park", "Treptower Park", label="Treptower\nPark", kind="hub", label_pos="right"),
    Station("waidmannslust", "Waidmannslust", label="Waidmanns-\nlust", label_pos="left"),
    Station("wannsee", "Wannsee", kind="hub", label_pos="top_left"),
    Station("warschauer_strasse", "Warschauer Straße", label="Warschauer\nStraße", kind="hub", label_pos="bottom"),
    Station("wartenberg", "Wartenberg"),
    Station("wassmannsdorf", "Waßmannsdorf", label_pos="top_left"),
    Station("wedding", "Wedding", label_pos="bottom"),
    Station("westend", "Westend", kind="hub", label_pos="left"),
    Station("westhafen", "Westhafen", label_pos="top"),
    Station("westkreuz", "Westkreuz", kind="hub", label_pos="top_right"),
    Station("wildau", "Wildau", kind="hub", label_pos="top_right"),
    Station("wilhelmshagen", "Wilhelmshagen", label="Wilhelms-\nhagen"),
    Station("wilhelmsruh", "Wilhelmsruh", label_pos="right"),
    Station("wittenau", "Wittenau", label_pos="right"),
    Station("wollankstrasse", "Wollankstraße"),
    Station("wuhletal", "Wuhletal"),
    Station("wuhlheide", "Wuhlheide"),
    Station("yorckstrasse", "Yorckstraße"),
    Station("yorckstrasse_grossgoerschenstrasse", "Yorckstraße (Großgörschenstraße)", label="Yorckstraße\n(Großgörschenstraße)", label_pos="top_left"),
    Station("zehlendorf", "Zehlendorf"),
    Station("zepernick", "Zepernick"),
    Station("zeuthen", "Zeuthen", label_pos="top_right"),
    Station("zoologischer_garten", "Zoologischer Garten", label="Zoologischer\nGarten", label_pos="bottom"),
    Station("gleisdreieck", "Gleisdreieck"),
])

LINE_COLORS: Dict[str, str] = {
    "S1": "#DA6BA2",
    "S15": "#DA6BA2",
    "S2": "#007734",
    "S25": "#007734",
    "S26": "#007734",
    "S3": "#0066AD",
    "S5": "#EC7405",
    # S6 faehrt erst im Zielnetz; die Farbe steht hier, damit beide Karten
    # dieselbe Palette benutzen.
    "S6": "#5FB4D3",
    "S7": "#816DA6",
    "S75": "#816DA6",
    "S8": "#66AA22",
    "S85": "#66AA22",
    # Der HVZ-Ast der S85 (siehe TRACK_LINES) faehrt in ihrer Farbe.
    "S85_pankow": "#66AA22",
    "S9": "#992746",
    "S41": "#AD5937",
    "S42": "#CB6418",
    "S46": "#CD9C53",
    "S47": "#CD9C53",
}

def _slice(corridor: Sequence[Step], start_name: str, end_name: str) -> List[Step]:
    """Schneidet ein Streckenstueck zwischen zwei Stationsnamen aus (beide
    Enden inklusive) -- samt aller dazwischenliegenden Turn()/FlexPath().

    Immer diese Funktion statt Index-Slices ([:3], [-3:]) benutzen: sobald
    eine Strecke Turn/FlexPath enthaelt, zaehlen die als Listenelemente mit
    und Index-Slices greifen still daneben.
    """
    try:
        start_idx = next(i for i, s in enumerate(corridor) if s == start_name)
    except StopIteration:
        raise GeometryError(f"Station '{start_name}' kommt in der Strecke nicht vor") from None
    try:
        end_idx = next(
            i for i in range(start_idx, len(corridor)) if corridor[i] == end_name
        )
    except StopIteration:
        raise GeometryError(
            f"Station '{end_name}' kommt in der Strecke nicht nach '{start_name}' vor"
        ) from None
    return list(corridor[start_idx:end_idx + 1])


def _reversed(corridor: Sequence[Step]) -> List[Step]:
    """Durchlaeuft ein Streckenstueck in Gegenrichtung: Reihenfolge drehen UND
    jeden Turn im Vorzeichen spiegeln (ein Rechtsknick vorwaerts ist
    rueckwaerts ein Linksknick). FlexPath bleibt unveraendert.

    Immer diese Funktion statt reversed()/[::-1] benutzen -- ein einfaches
    Umdrehen laesst die Turn-Vorzeichen stehen und verbiegt die Strecke.
    """
    return [
        Turn(-step.delta, step.radius) if isinstance(step, Turn) else step
        for step in reversed(corridor)
    ]


def _ring_flex(group: Optional[str] = None) -> Path:
    """Elastisches Ring-Beinstueck: darf sich deutlich staerker stauchen und
    strecken als ein normaler Sprung, damit sich der geschlossene Ring
    ueberhaupt schliessen kann."""
    return FlexPath(preferred=2.0, min_length=0.3, flex=2.0, group=group)


# ----------------------------------------------------------------------------
# Strecken (Korridore)
# ----------------------------------------------------------------------------
# Hier liegt die Geometrie EINMAL: Stationsfolge samt Turn()/FlexPath().
# Die Liniendefinitionen weiter unten schneiden sich daraus per _slice() bzw.
# _reversed() ihr Stueck heraus -- eine Aenderung hier wirkt damit sofort auf
# alle Linien, die diese Strecke befahren.
#
# Sortierung: alle radialen Aeste einheitlich INNEN (Ring/Zentrum) -> AUSSEN.
# Das entspricht der historischen Fahrtrichtung, nach der die Bahnen benannt
# sind (die Bahn NACH Dresden/Stettin/Wannsee/..., von Berlin aus gesehen).
# Ring und Stadtbahn behalten ihre etablierte Konvention (Uhrzeigersinn bzw.
# West -> Ost).

_STADTBAHN: List[Step] = [
    "westkreuz",
    FixPath(2.4), 
    "charlottenburg",
    FixPath(1.5), 
    "savignyplatz",
    FixPath(1.5), 
    "zoologischer_garten",
    FixPath(1.5), 
    "tiergarten",
    FixPath(1.5), 
    "bellevue",
    FixPath(2.2), 
    "hauptbahnhof",
    FixPath(1.8), 
    "friedrichstrasse",
    FixPath(2.0), 
    "hackescher_markt",
    FixPath(1.5), 
    "alexanderplatz",
    FixPath(1.5), 
    "jannowitzbruecke",
    FixPath(1.5), 
    "ostbahnhof",
    FixPath(1.5), 
    "warschauer_strasse",
    FixPath(2.6), 
    "ostkreuz",
]

_NORD_SUED_TUNNEL: List[Step] = [
    "anhalter_bahnhof",
    FixPath(1.2),
    "potsdamer_platz",
    FixPath(1.2),
    "brandenburger_tor",
    FixPath(2.4),
    "friedrichstrasse",
    FixPath(2.8),
    "oranienburger_strasse",
    FixPath(1.4),
    "nordbahnhof",
    FixPath(1.4),
    "humboldthain",
    FixPath(2.4),
    "gesundbrunnen",
]

_NORD_SUED_TUNNEL_HBF: List[Step] = [
    Turn(45), FlexPath(),
    "hauptbahnhof", 
    # FlexPath(), Turn(-45), FlexPath(), Turn(45, radius=1.2), FlexPath(preferred=1.0),
    # "potsdamer_platz",
    # FlexPath(), Turn(45), FlexPath(), Turn(-45), FlexPath(),
    # "gleisdreieck",
    # FlexPath(), Turn(-45), FlexPath(), Turn(45), FlexPath(),
    # "yorckstrasse",
]

# Ringbahn im Uhrzeigersinn, geschlossen (letzte Station == erste Station).
_RING: List[Step] = [
    "gesundbrunnen", FlexPath(),
    "schoenhauser_allee", FlexPath(),
    "prenzlauer_allee", FlexPath(),
    "greifswalder_strasse", FlexPath(), Turn(45, radius=1.2),
    "landsberger_allee", Turn(45, radius=1.2), FlexPath(),    # Ecke Nordost
    "storkower_strasse", FlexPath(),
    "frankfurter_allee", FixPath(3.2),
    "ostkreuz", FixPath(2.4),
    "treptower_park", FlexPath(),
    "sonnenallee", FlexPath(), Turn(90), FlexPath(),          # Ecke Suedost
    "neukoelln", FlexPath(),
    "hermannstrasse", FlexPath(),
    "tempelhof", FlexPath(),
    "suedkreuz", FlexPath(),
    "schoeneberg", FlexPath(2.2),
    "innsbrucker_platz", FlexPath(1.8),
    "bundesplatz", FlexPath(), Turn(45, radius=1.2),          # Ecke Suedwest
    "heidelberger_platz", Turn(45, radius=1.2), FlexPath(),
    "hohenzollerndamm", FlexPath(),
    "halensee", FixPath(3.2),
    "westkreuz", FixPath(3.2),
    "messe_nord_zob", FlexPath(),
    "westend", FlexPath(), Turn(90), FlexPath(),              # Ecke Nordwest
    "jungfernheide", FlexPath(),
    "beusselstrasse", FlexPath(),
    "westhafen", FixPath(4.4),
    "wedding", FixPath(1.8),
    "gesundbrunnen", FlexPath(),
]

_ANHALTER_BAHN: List[Step] = [
    "teltow_stadt",
    "lichterfelde_sued",
    "osdorfer_strasse",
    "lichterfelde_ost",
    "lankwitz",
    "suedende",
    FixPath(1.8),
    "priesterweg",
    FixPath(1.8),
    "suedkreuz",
    FlexPath(min_length=2.4),
    "yorckstrasse",
]

_DRESDNER_BAHN: List[Step] = [
    "blankenfelde",
    "mahlow",
    "lichtenrade",
    "schichauweg",
    "buckower_chaussee",
    "marienfelde",
    "attilastrasse",
]

_GOERLITZER_BAHN: List[Step] = [
    "plaenterwald", FlexPath(),
    "baumschulenweg",
    FixPath(1.8),
    "schoeneweide",
    FixPath(1.8),
    "johannisthal",
    FixPath(1.8),
    "adlershof",
    FixPath(2.4),
    "gruenau",
    "eichwalde",
    "zeuthen",
    FixPath(1.4),
    "wildau",
    FixPath(1.4),
    "koenigs_wusterhausen",
]

_BER_AST: List[Step] = [
    "altglienicke",
    "gruenbergallee",
    "schoenefeld",
    "wassmannsdorf",
    "flughafen_ber",
]

_SCHLESISCHE_BAHN: List[Step] = [
    "rummelsburg", FixPath(1.4),
    "betriebsbahnhof_rummelsburg", FixPath(1.4),
    "karlshorst", FixPath(1.4),
    Turn(-45), FixPath(1.4),
    "wuhlheide", FixPath(1.4),
    "koepenick", FixPath(1.4),
    "hirschgarten", FixPath(1.4),
    "friedrichshagen", FixPath(1.4),
    "rahnsdorf", FixPath(1.4),
    "wilhelmshagen", FixPath(1.6),
    "erkner",
]

_OST_BAHN: List[Step] = [
    "noeldnerplatz", FixPath(1.4),
    "lichtenberg", FixPath(1.4),
    "friedrichsfelde_ost", FixPath(1.2),
    Turn(45), FixPath(2.0),
    "biesdorf", FixPath(1.4),
    "wuhletal", FixPath(1.4),
    "kaulsdorf", FixPath(1.4),
    "mahlsdorf", FixPath(1.4),
    "birkenstein", FixPath(1.4),
    "hoppegarten", FixPath(1.4),
    "neuenhagen", FixPath(1.4),
    "fredersdorf", FixPath(1.4),
    "petershagen_nord", FixPath(1.4),
    "strausberg", FixPath(1.4),
    Turn(-90), FixPath(1.4),
    "hegermuehle", FixPath(1.4),
    "strausberg_stadt", FixPath(1.4),
    "strausberg_nord",
]

_WRIEZENER_BAHN: List[Step] = [
    *_slice(_OST_BAHN, "noeldnerplatz", "friedrichsfelde_ost"),
    FixPath(2.2),
    "springpfuhl",
    FixPath(2.2),
    "poelchaustrasse",
    "marzahn",
    "raoul_wallenberg_strasse",
    "mehrower_allee",
    "ahrensfelde",
]

_STETTINER_BAHN: List[Step] = [
    "pankow",
    FixPath(1.6),
    "pankow_heinersdorf",
    FixPath(1.6),
    "blankenburg", 
    FixPath(2.4),
    "karow",
    "buch",
    "roentgental",
    "zepernick",
    "bernau_friedenstal",
    "bernau",
]

_AUSSEN_RING: List[Step] = [
    "birkenwerder",
    "hohen_neuendorf", FixPath(0.2), Turn(-45),
    FlexPath(),
    "bergfelde",
    FlexPath(),
    "schoenfliess",
    FlexPath(),
    "muehlenbeck_moenchmuehle",
]

_NORD_BAHN: List[Step] = [
    "wollankstrasse", FixPath(1.0),
    "schoenholz", FixPath(2.0),
    "wilhelmsruh",
    "wittenau",
    "waidmannslust",
    "hermsdorf",
    "frohnau",
    "hohen_neuendorf",
    "birkenwerder", FixPath(1.0),
    "borgsdorf", FixPath(1.0),
    "lehnitz", FixPath(1.0),
    "oranienburg",
]

_KREMMENER_BAHN: List[Step] = [
    "alt_reinickendorf",
    "karl_bonhoeffer_nervenklinik",
    "eichborndamm",
    "tegel",
    "schulzendorf",
    "heiligensee",
    "hennigsdorf",
]

_WANNSEE_BAHN: List[Step] = [
    "potsdam_hbf",
    "babelsberg",
    "griebnitzsee",
    "wannsee",
    "nikolassee", Turn(45), FlexPath(),
    "schlachtensee", FlexPath(),
    "mexikoplatz", FlexPath(), Turn(-45),
    "zehlendorf",
    "sundgauer_strasse",
    "lichterfelde_west",
    "botanischer_garten",
    "rathaus_steglitz",
    "feuerbachstrasse",
    "friedenau", 
    FixPath(2.8),
    "schoeneberg", 
    FlexPath(),
    "julius_leber_bruecke", 
    FixPath(1.2),
    "yorckstrasse_grossgoerschenstrasse",
]

_SPANDAU: List[Step] = [
    "spandau",
    FixPath(1.8),
    "stresow",
    FixPath(1.8),
    "pichelsberg",
    FixPath(1.8),
    "olympiastadion",
    FixPath(1.8),
    "heerstrasse",
    FixPath(1.8),
    "messe_sued",
]

# ----------------------------------------------------------------------------
# Verbindungskurven zwischen zwei Strecken
# ----------------------------------------------------------------------------
# Jede Konstante enthaelt genau das, was ZWISCHEN den beiden im Namen
# genannten Stationen liegt (die Stationen selbst bringen die angrenzenden
# Strecken mit). Mehrfach befahrene Kurven muessen in allen Linien dieselbe
# Form haben -- deshalb stehen sie hier einmal statt in jeder Linie.

# Spandau/Messe Sued -> Stadtbahn (S3, S9)
_KURVE_MESSE_SUED_WESTKREUZ: List[Step] = [FixPath(2.2), Turn(-45), FixPath(2.2)]

# Grunewald -> Stadtbahn (S7)
_KURVE_GRUNEWALD_WESTKREUZ: List[Step] = [FlexPath(), Turn(45), FixPath(2.2)]

# Stadtbahn -> Ost-/Wriezener Bahn (S5, S7, S75)
#
# Weiter gefasst als der Default-Radius, weil hier ZWEI Spuren nebeneinander
# durch die Kurve gehen: S5 aussen, S7/S75 innen. Die innere Spur bekommt
# durch die konzentrische Korrektur den kleineren Radius, und mit dem Default
# 0.8 bliebe ihr davon fast nichts (0.8 - 1.5 Spurbreiten = 0.16) -- die
# Kurve knickte dort praktisch. Der groessere Grundradius gibt beiden Spuren
# einen sichtbaren Bogen.
_R_OSTKREUZ = 1.2
_KURVE_OSTKREUZ_NOELDNERPLATZ: List[Step] = [
    FixPath(1.8), Turn(-45, radius=_R_OSTKREUZ), FixPath(2.0),
]

# Stadtbahn -> Schlesische Bahn (S3)
#
# Die S3 faehrt diese Kurve allein und bekaeme deshalb den Default-Radius --
# neben der weiter gefassten Gegenkurve saehe sie zu eng aus. Hier steht
# darum von Hand genau der Radius, den die S5 als aeussere Spur ihres Paares
# faehrt: eine halbe Spurbreite enger als _R_OSTKREUZ, weil die S5 auf der
# Stadtbahn eine halbe Spur innerhalb der Trassenmitte liegt (Slot -0.5 von
# vier Familien). Damit gehen S3 und S5 hinter dem Ostkreuz spiegelbildlich
# auseinander.
#
# Dazu das gerade Stueck davor: Beide Kurven haetten auf der Trassenmitte
# denselben Scheitel, der gezeichnete wandert aber mit dem Spurversatz. Bei
# 45 Grad verschiebt ihn der Versatz VOR der Kurve um sich selbst, der
# Versatz DAHINTER um das Wurzel-Zwei-Fache:
#
#   S5   -0.5 -> +0.5 Spurbreiten   ->   Scheitel  +1.207
#   S3   +0.5 ->  0.0 Spurbreiten   ->   Scheitel  +0.500
#
# Die Differenz von sqrt(2)/2 Spurbreiten bekommt die S3 hier als Gerade
# dazu, dann setzen beide Boegen auf derselben Hoehe an und die Geraden vor
# dem Ostkreuz sind gleich lang.
_KURVE_OSTKREUZ_RUMMELSBURG: List[Step] = [
    FixPath(1.8 + sqrt(2) / 2 * CFG.style.bundle_spacing),
    Turn(45, radius=_R_OSTKREUZ - 0.5 * CFG.style.bundle_spacing),
    FixPath(2.0),
]

# Nord-Sued-Tunnel -> Stettiner Bahn (S2, S26; S8 faehrt sie rueckwaerts)
_KURVE_BORNHOLMER_PANKOW: List[Step] = [FixPath(0.8), Turn(45), FlexPath(2.6)]

# Nordbahn/Stettiner Bahn -> Ring (S8, S85)
_KURVE_BORNHOLMER_SCHOENHAUSER: List[Step] = [FlexPath(), Turn(-90), FlexPath()]

# Ring -> Goerlitzer Bahn (S8, S85, S9)
_KURVE_TREPTOW_PLAENTERWALD: List[Step] = [FixPath(1.2), Turn(-45), FlexPath()]

# Goerlitzer Bahn -> Flughafenast (S85, S9)
_KURVE_ADLERSHOF_ALTGLIENICKE: List[Step] = [FixPath(1.6), Turn(90), FixPath(1.6)]

# Neukoellner Spange: Goerlitzer Bahn -> Ring, mit Zwischenhalt (S46, S47)
_KURVE_BAUMSCHULENWEG_NEUKOELLN: List[Step] = [
    FixPath(1.6), Turn(-45), FlexPath(),
    "koellnische_heide", FlexPath(),
]

_ABSCHNITT_GESUNDBRUNNEN_WOLLANKSTRASSE: List[Step] = [FixPath(2.6), "bornholmer_strasse", FixPath(2.4)]
_ABSCHNITT_GESUNDBRUNNEN_BORNHOLMER: List[Step] = _ABSCHNITT_GESUNDBRUNNEN_WOLLANKSTRASSE[:2]
_ABSCHNITT_WOLLANKSTRASSE_BORNHOLMER: List[Step] = _reversed(_ABSCHNITT_GESUNDBRUNNEN_WOLLANKSTRASSE)[:2]

# Umsteigebahnhoefe -- bekommen in der zweiten Rendering-Schicht ein weisses
# "Pill" quer zum Linienbuendel. Manuell gepflegt: was ein "echter" Umsteige-
# bahnhof ist (S-Bahn-Kreuzung, U-Bahn-/Fernbahn-Anschluss), laesst sich nicht
# zuverlaessig aus der Linienverzweigung ableiten (z.B. ist Blankenburg ein
# reiner Abzweig ohne eigenen Umsteigehalt).
# ----------------------------------------------------------------------------
# Von Hand festgelegte Spurlagen
# ----------------------------------------------------------------------------
# Wo die automatische Slot-Vergabe kein gutes Bild liefert, wird die Spurlage
# hier fest vorgegeben. Vorzeichen in Schreibrichtung des Korridors.

# Stationen, an denen zwei Trassen auf DERSELBEN Diagonalen liegen. Die
# Beschriftung steht dort schraeg, und darunter ist kein Platz mehr fuer das
# Linien-Tag -- es geht deshalb auf die gegenueberliegende Ecke, der Name
# bleibt wo er ist.
#
# Bewusst eine Liste und keine automatische Regel: an den grossen Kreuzen
# (Westkreuz, Gesundbrunnen, Suedkreuz) steht die Beschriftung aus anderen
# Gruenden schraeg, dort gehoert das Tag schlicht unter den Namen.
BADGE_OPPOSITE_CORNER: frozenset = frozenset({
    "wannsee", "wildau", "blankenburg",
})


# Manuelle Feinkorrekturen in Pixeln, wo die Automatik nicht hinkommt.
# LABEL_NUDGE verschiebt Name UND Tag, BADGE_NUDGE nur das Tag.
LABEL_NUDGE: Dict[str, Pt] = {
    # Beide Kreuze tragen ihr Label als Ecke unmittelbar ueber der Stadtbahn
    # bzw. dem Ring -- das Tag darunter laege sonst auf den Linien.
    "westkreuz": (0.0, -18.0),
    "gesundbrunnen": (0.0, -18.0),
    "hauptbahnhof": (0.0, -18.0),
}

BADGE_NUDGE: Dict[str, Pt] = {
    # Hier bleibt der Name, wo er ist; nur das Tag wandert unter den Ring
    # und auf die andere Seite des Nord-Sued-Tunnels.
    # Der Tunnel liegt hier um eine halbe Spur nach rechts versetzt, das Tag
    # rueckt deshalb um dieselben 6 px mit.
    "suedkreuz": (-46.0, 37.0),
}


CORRIDORS: Dict[str, Corridor] = {
    "wannseebahn_west": Corridor(
        # Nur bis Nikolassee: dort trennen sich S1 und S7, danach faehrt
        # jede allein und liegt mittig auf ihrer Trasse. Die Kurve hinter
        # Nikolassee nimmt den Uebergang auf -- S1 ist ab Schlachtensee
        # zentriert, S7 ab Grunewald.
        steps=_slice(_WANNSEE_BAHN, "potsdam_hbf", "nikolassee"),
        offsets={"S7": -0.5, "S1": 0.5},
    ),
    "stadtbahn": Corridor(
        # Vier Linien parallel ueber die ganze Stadtbahn.
        steps=_STADTBAHN,
        offsets={"S7": -1.5, "S5": -0.5, "S3": 0.5, "S9": 1.5},
    ),
    "stadtbahn_zulauf_messe_sued": Corridor(
        # S3/S9 muessen ihre Stadtbahn-Spur schon im Bogen vor Westkreuz
        # einnehmen -- danach ist bis Ostkreuz keine Kurve mehr.
        steps=["messe_sued", "westkreuz"],
        offsets={"S3": 0.5, "S9": 1.5},
    ),
    "stadtbahn_zulauf_grunewald": Corridor(
        # dasselbe fuer die S7, die von Grunewald einbiegt
        steps=["grunewald", "westkreuz"],
        offsets={"S7": -1.5},
    ),
    "s9_treptower_park": Corridor(
        # S9 laege sonst genau auf der S42 (beide auf der Trassenmitte).
        # Zwei Slots nach innen, also noch innerhalb der S41.
        steps=["warschauer_strasse", "treptower_park"],
        offsets={"S9": 2.0},
    ),
    "ostbahn_lichtenberg": Corridor(
        # Ohne Vorgabe vergibt die Automatik alphabetisch (S5 vor S7) und
        # dreht damit die Reihenfolge der Stadtbahn um -- die beiden wuerden
        # sich hinter Ostkreuz kreuzen. S7 bleibt auf derselben Seite wie
        # auf der Stadtbahn.
        steps=["ostkreuz", "noeldnerplatz", "lichtenberg", "friedrichsfelde_ost"],
        offsets={"S7": -0.5, "S5": 0.5},
    ),
    "ring": Corridor(
        # S42 liegt fest auf der Mitte und verlaesst sie nie, S41 immer
        # innen daneben. Die Gastlinien fahren auf disjunkten Abschnitten
        # (S8 im Nordosten, S46/S47 im Sueden und Westen, S6 bei
        # Beusselstrasse, S1 bei Wedding) und teilen sich deshalb alle die
        # aeussere Spur -- so verschiebt sich der Ring nicht, wenn eine
        # dazukommt.
        steps=_RING,
        offsets={"S42": 0.0, "S41": 1.0, "S4": -1.0, "S8": -1.0, "S6": -1.0, "S1": -1.0},
    ),
    "ring_zulauf_schoenhauser": Corridor(
        # Wie bei Koellnische Heide: S8/S85 muessen schon im Bogen vor
        # Schoenhauser Allee auf die aeussere Ringspur, sonst laegen
        # Schoenhauser und Greifswalder noch mittig -- der naechste Bogen
        # kommt erst an der Nordostecke.
        steps=["bornholmer_strasse", "schoenhauser_allee"],
        offsets={"S8": -1.0},
    ),
    "ring_zulauf_koellnische_heide": Corridor(
        # S46/S47 muessen schon im Bogen bei Koellnische Heide auf die
        # aeussere Ringspur schwenken. Sonst faengt der Versatz erst in der
        # naechsten Kurve -- und die kommt erst vor Heidelberger Platz, also
        # laege der ganze Suedring auf der falschen Spur.
        steps=["baumschulenweg", "koellnische_heide", "neukoelln"],
        offsets={"S4": -1.0},
    ),
    # "hbf_potsdamer_platz": Corridor(
        # Einschwenken zum Potsdamer Platz: S15 auf dieselbe Spur wie die S1
        # (-0.5), S6 daneben auf -1.5. Sonst vergibt die Automatik
        # alphabetisch und legt die S15 auf die Seite der S2.
        # steps=["hauptbahnhof", "potsdamer_platz"],
        # offsets={"S15": 0.5},
        # ),
    "hbf_zulauf_wedding": Corridor(
        steps=["wedding", "hauptbahnhof"],
        offsets={"S15": 0.0},
    ),
    "nord_sued_s1": Corridor(
        # S1 durchgehend links (-0.5) von Anhalter bis Oranienburg. Der
        # Versatz kann nur in einer Kurve uebernommen werden, und S1s letzte
        # Kurve davor steckt in yorckstrasse_grossgoerschenstrasse ->
        # anhalter_bahnhof -- die Kante muss deshalb mit im Korridor sein,
        # sonst bleibt S1 auf der ganzen Strecke mittig (es kommt bis
        # Oranienburg keine weitere Kurve).
        #
        # S2 behaelt dabei ihre Tunnelspur rechts (+0.5); S8/S85 liegen
        # noerdlich von Gesundbrunnen mit auf der Strecke und bekommen die
        # aeussere Spur rechts daneben.
        steps=[
            "yorckstrasse_grossgoerschenstrasse",
            "anhalter_bahnhof", "potsdamer_platz", "brandenburger_tor",
            "friedrichstrasse", "oranienburger_strasse", "nordbahnhof",
            "humboldthain", "gesundbrunnen", "bornholmer_strasse",
            "wollankstrasse", "schoenholz", "wilhelmsruh", "wittenau",
            "waidmannslust", "hermsdorf", "frohnau", "hohen_neuendorf",
            "birkenwerder", "borgsdorf", "lehnitz", "oranienburg",
        ],
        # S85 faehrt den Nordast ueber Wollankstrasse/Schoenholz und bleibt
        # dort aussen (+1.5). S8 nimmt den Ostast und beruehrt diesen
        # Korridor nur zwischen Birkenwerder und Hohen Neuendorf -- dort
        # rechts neben S1 (+0.5).
        offsets={"S1": -0.5, "S2": 0.5, "S85": 1.5, "S8": 0.5},
    ),
    "nord_sued_zulauf_yorck": Corridor(
        # Muss NACH nord_sued_s1 stehen: die spaetere Definition gewinnt auf
        # einer doppelt belegten Kante.
        #
        # S1 und S15 fahren hier dieselbe Kurve, aber in entgegengesetzter
        # Richtung, und ein Versatzwechsel wird immer erst an der NAECHSTEN
        # Kurve uebernommen. Fuer die S1 (aus Suedwesten) liegt die Kurve
        # genau richtig: sie faehrt bis dorthin mittig und schwenkt in der
        # Kurve auf die Tunnelspur -0.5.
        #
        # Die S15 kommt von der anderen Seite -- fuer sie liegt die Kurve
        # VOR dem Wechsel, der erst an der Station Yorckstrasse faellig
        # waere. Danach kommt bis Nikolassee keine Kurve mehr, sie bliebe
        # also auf der ganzen Wannseebahn um -0.5 neben der S1. Deshalb
        # bekommt sie hier schon den Zielwert der Wannseebahn (0.0): der
        # Wechsel wird dann an der Station Anhalter Bahnhof faellig und in
        # derselben Kurve uebernommen wie bei der S1.
        steps=["yorckstrasse_grossgoerschenstrasse", "anhalter_bahnhof"],
        offsets={"S1": -0.5, "S15": 0.0},
    ),
    "goerlitzer_bahn": Corridor(
        # S8-Familie mittig, S9 aussen, S46/S47 auf der Gegenseite.
        # treptower_park ist mit drin, weil S8/S85/S9 ihren Versatz erst in
        # der Kurve auf DIESER Kante uebernehmen koennen.
        steps=["treptower_park", *_GOERLITZER_BAHN],
        offsets={"S8": 0.0, "S9": 1.0, "S4": -1.0},
    ),
    "goerlitzer_zulauf_spindlersfeld": Corridor(
        # S47 kommt von Spindlersfeld und uebernimmt ihren Versatz in der
        # Kurve auf oberspree -> schoeneweide; ohne diesen Eintrag laege sie
        # auf der Goerlitzer Bahn mittig statt bei ihrer Familie.
        steps=["oberspree", "schoeneweide"],
        offsets={"S4": 1.0},
    ),
    "s8_blankenburg_pankow": Corridor(
        # S8 zwischen Blankenburg und Pankow versetzt. Uebernommen wird der
        # Wert in der Kurve auf DIESER Kante -- danach kommt bis Pankow keine
        # mehr, der Versatz haelt also durch.
        steps=["muehlenbeck_moenchmuehle", "blankenburg"],
        offsets={"S8": -0.5},
    ),
    "s8_pankow_bornholmer": Corridor(
        # S8 soll an Bornholmer Strasse rechts aussen liegen (+1.5), wie S85
        # auf ihrem Ast. S2/S26 behalten ihre bisherige Spur.
        steps=["pankow", "bornholmer_strasse"],
        # S2 steuert von hier aus die ganze Bernau-Strecke: der Versatz wird
        # in der Kurve dieser Kante uebernommen und haelt bis Bernau, weil
        # danach keine Kurve mehr kommt.
        offsets={"S8": -1.5, "S2": 0.5},
        # Der HVZ-Ast der S85 faengt mitten auf dieser Kante an, in Pankow.
        # Ohne Vorgabe laege er sofort auf -1.5 -- die S8 uebernimmt diesen
        # Versatz aber erst in der Kurve und kommt bis dahin auf -0.5 aus
        # Blankenburg. Damit der Ast von Pankow bis zur Kurve wirklich auf
        # der S8 liegt, startet er ebenfalls auf -0.5.
        start_offsets={"S85_pankow": -0.5},
    ),
    "nordbahn_frohnau": Corridor(
        # Noerdlich von Wilhelmsruh faehrt die S85 allein neben der S1 und
        # liegt dicht daneben auf +0.5.
        # In derselben Richtung geschrieben wie nord_sued_s1, sonst kippt
        # das Vorzeichen des Versatzes.
        steps=["wilhelmsruh", "wittenau", "waidmannslust", "hermsdorf",
               "frohnau"],
        offsets={"S1": -0.5, "S85": 0.5},
    ),
    "nordbahn_wilhelmsruh": Corridor(
        # Suedlich von Wilhelmsruh kommt die S25 dazu und belegt +0.5, die
        # S85 muss also auf +1.5 hinaus. Auf dieser Kante gibt es keine
        # Kurve, an der ein Versatzwechsel sonst uebernommen wuerde --
        # `immediate` setzt ihn direkt an der Kantengrenze, als kleinen
        # S-Knick zwischen Wilhelmsruh und Schoenholz.
        steps=["schoenholz", "wilhelmsruh"],
        offsets={"S1": -0.5, "S85": 1.5},
        immediate=True,
    ),
    "nord_sued_s2_zulauf": Corridor(
        # S2s letzte Kurve vor dem Tunnel liegt bei Attilastrasse; erst dort
        # kann sie ihre Spur rechts uebernehmen.
        steps=["attilastrasse", "priesterweg", "suedkreuz", "yorckstrasse",
               "anhalter_bahnhof"],
        offsets={"S2": 0.5},
    ),
    "nord_sued_s25_s26_zulauf": Corridor(
        # S25/S26 kommen ueber den anderen Suedast und haben zwischen Teltow
        # Stadt und dem Tunnel gar keine Kurve -- ihr Versatz stammt also aus
        # der allerersten Kante und muss schon dort stimmen, sonst laufen sie
        # im Tunnel mittig statt auf der Spur ihrer Familie.
        steps=["teltow_stadt", "lichterfelde_sued", "osdorfer_strasse",
               "lichterfelde_ost", "lankwitz", "suedende", "priesterweg"],
        offsets={"S2": 0.5},
    ),
}


LABEL_OVERRIDE: Dict[str, Tuple[float, float, str]] = {
    # hier koennen spaeter manuelle Labelkorrekturen eingetragen werden
    # "friedrichstrasse": (0, -10, "middle"),
}

# Linien. Alle Geometrie kommt aus den Strecken und Verbindungskurven oben --
# hier stehen nur noch Startwinkel und die Reihenfolge der befahrenen Stuecke.
# Nur wirklich linieneigene Knicke (von genau EINER Linie befahren) stehen
# direkt in der Liniendefinition.
TRACK_LINES: Dict[str, TurnLine] = {
    "S1": TurnLine(
        color=LINE_COLORS["S1"],
        family="S1",
        start=45,
        steps=[
            *_slice(_WANNSEE_BAHN, "wannsee", "yorckstrasse_grossgoerschenstrasse"),
            FixPath(1.15), Turn(-45), FixPath(0.6),  # NO -> Nord, in den Tunnel
            *_NORD_SUED_TUNNEL,
            *_ABSCHNITT_GESUNDBRUNNEN_WOLLANKSTRASSE,
            *_NORD_BAHN,
        ],
        direction="Wannsee -> Oranienburg",
    ),
    "S15": TurnLine(
        color=LINE_COLORS["S15"],
        family="S1",
        start=270,
        steps=[
            *_reversed(_slice(_RING, "wedding", "gesundbrunnen")),
            FixPath(2.8), Turn(-135, radius=0.4), FlexPath(),
            *_NORD_SUED_TUNNEL_HBF, 
        ],
        direction="Gesundbrunnen -> Hauptbahnhof",
    ),
    "S2": TurnLine(
        color=LINE_COLORS["S2"],
        family="S2",
        start=315,
        steps=[
            *_DRESDNER_BAHN,
            FixPath(1.2), Turn(45), FixPath(0.8),       # SO -> Nord, auf die Anhalter Bahn
            *_slice(_ANHALTER_BAHN, "priesterweg", "yorckstrasse"),
            FixPath(1.4),
            *_NORD_SUED_TUNNEL,
            *_ABSCHNITT_GESUNDBRUNNEN_BORNHOLMER,
            *_KURVE_BORNHOLMER_PANKOW,
            *_STETTINER_BAHN,
        ],
        direction="Bernau -> Blankenfelde",
    ),
    "S25": TurnLine(
        color=LINE_COLORS["S25"],
        family="S2",
        start=0,
        steps=[
            *_ANHALTER_BAHN,
            FixPath(1.4),
            *_NORD_SUED_TUNNEL,
            *_ABSCHNITT_GESUNDBRUNNEN_WOLLANKSTRASSE,
            *_slice(_NORD_BAHN, "wollankstrasse", "schoenholz"),
            FixPath(0.8),Turn(-45), FixPath(1.6),
            *_KREMMENER_BAHN,
        ],
        direction="Hennigsdorf -> Teltow Stadt",
    ),
    "S26": TurnLine(
        color=LINE_COLORS["S26"],
        family="S2",
        start=0,
        steps=[
            *_ANHALTER_BAHN,
            FixPath(1.4),
            *_NORD_SUED_TUNNEL,
            *_ABSCHNITT_GESUNDBRUNNEN_BORNHOLMER,
            *_KURVE_BORNHOLMER_PANKOW,
            *_slice(_STETTINER_BAHN, "pankow", "blankenburg"),
        ],
        direction="Blankenburg -> Teltow Stadt",
    ),
    "S3": TurnLine(
        color=LINE_COLORS["S3"],
        start=135,
        steps=[
            *_SPANDAU,
            *_KURVE_MESSE_SUED_WESTKREUZ,
            *_STADTBAHN,
            *_KURVE_OSTKREUZ_RUMMELSBURG,                     # Ostkreuz -> Schlesische Bahn
            *_SCHLESISCHE_BAHN,
        ],
        direction="Spandau -> Erkner",
    ),
    "S41": TurnLine(
        color=LINE_COLORS["S41"],
        start=90,
        closed=True,
        steps=[*_RING],
        direction="Ring im Uhrzeigersinn",
    ),
    "S42": TurnLine(
        color=LINE_COLORS["S42"],
        start=90,
        closed=True,
        # dieselbe Schiene wie S41 -- die aktuelle Rendering-Stufe zeigt
        # ohnehin nur eine Mittellinie je Korridor, S42 liegt also exakt auf
        # S41 (kein eigener Versatz fuer den Gegenzug).
        steps=[*_RING],
        direction="Ring gegen den Uhrzeigersinn",
    ),
    "S46": TurnLine(
        color=LINE_COLORS["S46"],
        family="S4",
        start=315,
        steps=[
            *_reversed(_slice(_GOERLITZER_BAHN, "baumschulenweg", "koenigs_wusterhausen")),
            *_KURVE_BAUMSCHULENWEG_NEUKOELLN,
            *_slice(_RING, "neukoelln", "westend"),
        ],
        direction="Westend -> Königs Wusterhausen",
    ),
    "S47": TurnLine(
        color=LINE_COLORS["S47"],
        family="S4",
        start=225,
        steps=[
            "spindlersfeld",
            FixPath(1.4),
            "oberspree",
            FixPath(2.2), Turn(90), FixPath(1.4),   # Spindlersfelder Ast -> Goerlitzer Bahn
            *_reversed(_slice(_GOERLITZER_BAHN, "baumschulenweg", "schoeneweide")),
            *_KURVE_BAUMSCHULENWEG_NEUKOELLN,
            *_slice(_RING, "neukoelln", "suedkreuz"),
        ],
        direction="Südkreuz -> Spindlersfeld",
    ),
    "S5": TurnLine(
        color=LINE_COLORS["S5"],
        start=90,
        steps=[
            *_STADTBAHN,
            *_KURVE_OSTKREUZ_NOELDNERPLATZ,
            *_OST_BAHN,
        ],
        direction="Westkreuz -> Strausberg Nord",
    ),
    "S7": TurnLine(
        color=LINE_COLORS["S7"],
        family="S7",
        start=45,
        steps=[
            *_slice(_WANNSEE_BAHN, "potsdam_hbf", "nikolassee"),
            Turn(-45), FlexPath(), 
            Turn(45, radius=0.8), FixPath(2.4),  # Bogen um den Grunewald
            "grunewald",
            *_KURVE_GRUNEWALD_WESTKREUZ,
            *_STADTBAHN,
            *_KURVE_OSTKREUZ_NOELDNERPLATZ,
            *_WRIEZENER_BAHN,
        ],
        direction="Potsdam Hbf -> Ahrensfelde",
    ),
    "S75": TurnLine(
        color=LINE_COLORS["S75"],
        family="S7",
        start=90,
        steps=[
            # rueckwaerts zum direction=-Label geschrieben, damit die mit
            # S7/S5 geteilten Kanten dieselbe Richtung haben wie dort.
            *_slice(_STADTBAHN, "warschauer_strasse", "ostkreuz"),
            *_KURVE_OSTKREUZ_NOELDNERPLATZ,
            *_slice(_WRIEZENER_BAHN, "noeldnerplatz", "springpfuhl"),
            Turn(-45),                    # NO -> Nord, nach Wartenberg
            FixPath(1.6),
            "gehrenseestrasse", FixPath(1.4),
            "hohenschoenhausen", FixPath(1.4),
            "wartenberg",
        ],
        direction="Wartenberg -> Warschauer Straße",
    ),
    "S8": TurnLine(
        color=LINE_COLORS["S8"],
        family="S8",
        start=180,
        steps=[
            *_AUSSEN_RING,
            FixPath(2.6), Turn(90), FlexPath(),   # Aussenring -> Stettiner Bahn
            *_reversed(_slice(_STETTINER_BAHN, "pankow", "blankenburg")),
            *_reversed(_KURVE_BORNHOLMER_PANKOW),
            "bornholmer_strasse",
            *_KURVE_BORNHOLMER_SCHOENHAUSER,
            *_slice(_RING, "schoenhauser_allee", "treptower_park"),
            *_KURVE_TREPTOW_PLAENTERWALD,
            *_slice(_GOERLITZER_BAHN, "plaenterwald", "wildau"),
        ],
        direction="Birkenwerder -> Wildau",
    ),
    "S85": TurnLine(
        color=LINE_COLORS["S85"],
        family="S8",
        start=180,
        steps=[
            *_reversed(_slice(_NORD_BAHN, "wollankstrasse", "frohnau")),
            *_ABSCHNITT_WOLLANKSTRASSE_BORNHOLMER,
            *_KURVE_BORNHOLMER_SCHOENHAUSER,
            *_slice(_RING, "schoenhauser_allee", "treptower_park"),
            *_KURVE_TREPTOW_PLAENTERWALD,
            *_slice(_GOERLITZER_BAHN, "plaenterwald", "adlershof"),
            *_KURVE_ADLERSHOF_ALTGLIENICKE,
            *_BER_AST,
        ],
        direction="Frohnau -> Flughafen BER",
    ),
    # Ausserhalb der HVZ faehrt die S85 nicht bis Frohnau, sondern biegt an
    # der Bornholmer Strasse auf die Stettiner Bahn ab und endet in Pankow.
    # Das ist ein zweites Nordende, und eine TurnLine ist EIN Streckenzug --
    # der Ast steht deshalb als eigene Linie da. `branch_of="S85"` haelt
    # beides zusammen: er traegt das Signet der S85 und zeigt es nur an
    # seinem freien Ende in Pankow, nicht dort, wo er auf die Stammlinie
    # trifft. Ueber family="S8" liegt er auf der Spur der S8-Familie, wie im
    # Zielnetz, wo die S85 diesen Weg planmaessig faehrt.
    "S85_pankow": TurnLine(
        color=LINE_COLORS["S85"],
        family="S8",
        branch_of="S85",
        steps=[
            "pankow",
            *_reversed(_KURVE_BORNHOLMER_PANKOW),
            "bornholmer_strasse",
        ],
        direction="Pankow -> Bornholmer Straße (ausserhalb der HVZ)",
    ),
    "S9": TurnLine(
        color=LINE_COLORS["S9"],
        start=135,
        steps=[
            *_SPANDAU,
            *_KURVE_MESSE_SUED_WESTKREUZ,
            *_slice(_STADTBAHN, "westkreuz", "warschauer_strasse"),
            FlexPath(), Turn(90), FlexPath(),   # Stadtbahn -> Ring (nur S9)
            "treptower_park",
            *_KURVE_TREPTOW_PLAENTERWALD,
            *_slice(_GOERLITZER_BAHN, "plaenterwald", "adlershof"),
            *_KURVE_ADLERSHOF_ALTGLIENICKE,
            *_BER_AST,
        ],
        direction="Spandau -> Flughafen BER",
    ),
}

# ============================================================================
# THE COMPLETE NETWORK
# ============================================================================

# ----------------------------------------------------------------------------
# ZUGGRUPPEN
#
# Was in der Legendentabelle steht: je Linie ihre Zuggruppen mit Laufweg und
# Zugstaerke in Viertelzuegen. Die Tabelle selbst zeichnet netmap/legend.py --
# hier stehen nur die Daten, direkt bei der Linie, zu der sie gehoeren.
# ----------------------------------------------------------------------------

def _stamm(route: str, cars: int = 4, **rest) -> TrainGroup:
    return TrainGroup("Stammzuggruppe", route, cars, **rest)


def _tag(route: str, cars: int = 4) -> TrainGroup:
    return TrainGroup("Tageszuggruppe", route, cars)


def _hvz(route: str, cars: int = 2) -> TrainGroup:
    return TrainGroup("HVZ-Verstärker", route, cars)


GROUPS: Dict[str, List[TrainGroup]] = {
    "S1": [
        _stamm("Wannsee <> Oranienburg"),
        _tag("Wannsee <> Frohnau"),
        _hvz("Zehlendorf <> Potsdamer Platz"),
        _hvz("Zehlendorf <> Potsdamer Platz"),
    ],
    "S15": [_stamm("Hauptbahnhof <> Gesundbrunnen", 2)],
    "S2": [
        _stamm("Blankenfelde <> Bernau"),
        _tag("Lichtenrade <> Buch", 3),
    ],
    "S25": [
        _stamm("Teltow Stadt <> Hennigsdorf", 3),
    ],
    "S26": [
        _tag("Teltow Stadt <> Blankenburg", 2),
    ],
    "S3": [
        _stamm("Erkner <> Spandau"),
        _tag("Erkner <> Charlottenburg"),
        _hvz("Friedrichshagen <> Ostbahnhof"),
    ],
    # S41 und S42 haben dieselbe Zeile -- die Legende fasst sie deshalb zu
    # einem Block zusammen.
    "S41": [TrainGroup("Alle Zuggruppen", "Ringbahn in beide Richtungen")],
    "S42": [TrainGroup("Alle Zuggruppen", "Ringbahn in beide Richtungen")],
    "S46": [_stamm("Königs Wusterhausen <> Westend")],
    "S47": [_stamm("Spindlersfeld <> Südkreuz", 2)],
    "S5": [
        _stamm("Strausberg Nord <> Westkreuz"),
        _tag("Hoppegarten <> Westkreuz"),
        _hvz("Mahlsdorf <> Ostbahnhof"),
        _hvz("Mahlsdorf <> Ostbahnhof"),
    ],
    "S7": [
        _stamm("Ahrensfelde <> Potsdam Hbf"),
        _tag("Ahrensfelde <> Potsdam Hbf"),
    ],
    "S75": [
        _stamm("Wartenberg <> Ostbahnhof", 2),
        _tag("Wartenberg <> Warschauer Straße", 2),
    ],
    # Der Nordast faehrt schwaecher als der Rest der Linie: der dritte
    # Viertelzug steht deshalb nur umrandet in der Tabelle, und die Fussnote
    # darunter sagt, wo er fehlt.
    "S8": [
        _stamm("Wildau <> Hohen Neuendorf", 3, hollow=1,
               note="Blankenburg <> Hohen Neuendorf", note_cars=2),
    ],
    "S85": [_stamm("Flughafen BER <> Frohnau", 3)],
    "S9": [_stamm("Flughafen BER <> Spandau")],
}

TRACK_LINES = {
    lid: replace(line, groups=GROUPS.get(lid, ()))
    for lid, line in TRACK_LINES.items()
}



NET = Net(
    stations=STATIONS,
    lines=TRACK_LINES,
    colors=LINE_COLORS,
    corridors=CORRIDORS,
    label_override=LABEL_OVERRIDE,
    label_offsets=LABEL_NUDGE,
    badge_offsets=BADGE_NUDGE,
    badge_opposite_corner=BADGE_OPPOSITE_CORNER,
    # Linke Kante der Zuggruppen-Tabelle, in Kartenkoordinaten: links
    # oben, neben dem Nordwesten der Karte. Die Hoehe bleibt offen (None)
    # -- dann haengt die Tabelle so tief, wie ihre Spalte es zulaesst, und
    # traegt so wenig wie moeglich zur Kartenhoehe bei.
    legend_at=(-60.0, None),
)


# ============================================================================
# MAIN
# ============================================================================

ZIEL = FilePath("outputs/berlin_sbahn_2026.svg")


if __name__ == "__main__":
    # draw_corridors=True blendet die grauen Hilfstrassen ein -- nuetzlich,
    # um den Spurversatz gegen die Trassenmitte zu pruefen.
    write_map(NET, sys.argv[1] if len(sys.argv) > 1 else ZIEL)
