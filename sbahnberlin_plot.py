"""
S-Bahn Berlin – Liniennetzplan-Generator (v2)
=============================================

Deklarative Definition des Netzes, automatisches Rendering als SVG.

DREI DEFINITIONSEBENEN
----------------------
1. STATIONS   : Name -> (x, y) im schematischen Raster.
2. LINES      : Linien-ID -> Line(color, waypoints, direction, rank, closed).
                waypoints sind Stationsnamen (str), (x, y)-Tupel als reine,
                unsichtbare Knickpunkte, ODER eine nackte Zahl als Knick-
                WINKEL (Grad, 0 = Norden, im Uhrzeigersinn, nur Vielfache
                von 45). Ein Winkel gilt fuer die Verbindung zum naechsten
                Waypoint: die Linie verlaesst den vorherigen Punkt exakt in
                diese Richtung, die Knick-Koordinate wird automatisch dazu
                berechnet -- kein Pixel-Rechnen von Hand mehr noetig. Ohne
                Winkel-Marker wird wie bisher automatisch octilinear
                (0/45/90 Grad) geroutet. Beispiel:
                    "Grunewald", 0, "Westkreuz"
                    -- verlaesst Grunewald exakt nach Norden, biegt dann
                       automatisch Richtung Westkreuz ab.

                Mehrere Winkel hintereinander (Kette) ergeben einen Knick,
                der nicht direkt an der Station sitzt, sondern erst ein
                Stueck dahinter beginnt -- fuer ruhigere Uebergaenge.
                Teilstrecken mit GLEICHEM Winkel werden dabei automatisch
                gleich lang gemacht (hoechstens 2 verschiedene Winkel je
                Kette). Beispiel:
                    "Grunewald", 45, 0, 45, "Westkreuz"
                    -- bleibt nach Grunewald erst noch ein Stueck auf der
                       45-Grad-Diagonale, wird dann senkrecht, und biegt
                       kurz vor Westkreuz wieder auf 45 Grad ein.

                direction : wie die Linie ueblicherweise genannt wird, z.B.
                            "Wannsee -> Oranienburg (Sued -> Nord)". Die
                            waypoints sind IN DIESER RICHTUNG zu lesen.
                            Die Geometrie ist richtungsunabhaengig -- das
                            Feld dient der Lesbarkeit der Definition.
                rank      : Querlage im Buendel. NIEDRIGER rank liegt weiter
                            NORD- bzw. WESTLICH:
                              waagerecht : klein = oben (Norden)
                              senkrecht  : klein = links (Westen)
                              diagonal   : klein = nordwestliche Seite
                            Damit ist steuerbar, ob z.B. S2 links oder
                            rechts neben S25 im Tunnel liegt.

3. SEGMENT_RANKS : punktuelle Ausnahme von der globalen rank-Reihenfolge.
                seg_rank_override("Station A", "Station B", {"S2": 99})
                dreht die Querlage NUR auf diesem einen Segment -- nuetzlich,
                wenn zwei Linien sich vor einer Verzweigung kreuzen wuerden.

4. Stations-Attribute:
   HUBS        : Stationen mit automatischem weissen "Pill" quer zum
                 Linienbuendel (Umsteigebahnhoefe). Manuell gepflegt.
   HUB_RECTS   : manuelle Rechteck-Groesse (px) fuer Kreuzungsbahnhoefe
                 wie Westkreuz/Ostkreuz, wo zwei Korridore kreuzen.
   LABEL_STYLE : Beschriftung links/rechts/unten pro Station.
   Alle Masse skalieren mit Config.line_scale bzw. Config.text_scale.

DIE ZENTRALE IDEE: LINIENBUENDELUNG (Offset-Engine)
---------------------------------------------------
Linien, die denselben Korridor befahren (z. B. S1/S2/S25/S26 im
Nord-Sued-Tunnel oder S41/S42/S8 auf dem Ring), teilen sich exakt
dieselben Segmentgeometrien (gleiche Stationskoordinaten). Der Renderer:
  1. sammelt pro Geometrie-Segment alle Linien, die es befahren,
  2. weist jeder Linie einen "Slot" zu (Reihenfolge = Reihenfolge in LINES),
  3. verschiebt jede Linie senkrecht zum Segment um
     (slot - (n-1)/2) * LINE_GAP,
  4. verbindet die versetzten Segmente an Knicken per Gehrungsschnitt
     (Miter-Join), sodass parallele Linien sauber um Kurven laufen.

LINIEN AENDERN / VERLAENGERN / VERLEGEN
---------------------------------------
Einfach die Waypoint-Liste der Linie anpassen (Stationen ergaenzen,
entfernen, andere Route waehlen) und das Skript neu laufen lassen.
Neue Stationen: erst in STATIONS eintragen, dann in der Linie verwenden.

BEKANNTE GRENZEN (v1 dieser Engine)
-----------------------------------
- Slot-Reihenfolge ist global konstant pro Kompassrichtung; an manchen
  Ringecken koennen sich parallele Linien daher einmal kreuzen. Das
  sauber zu loesen ist das "Line-Ordering-Problem" (LOOM loest es per ILP).
- Keine automatische Label-Kollisionsvermeidung.
- Stationskoordinaten sind von der Vorlage approximiert, nicht exakt.
"""

from dataclasses import dataclass, field
from math import atan2, degrees, hypot
from typing import Dict, List, Optional, Tuple, Union

Pt = Tuple[float, float]
Angle = Union[int, float]
Waypoint = Union[str, Pt, Angle]

# ===========================================================================
# KONFIGURATION
# ===========================================================================

@dataclass
class Config:
    grid: float = 13.0        # px pro Rastereinheit
    margin: float = 70.0      # Rand um den Plan
    line_scale: float = 1.5   # globaler Faktor auf alle Linien-Masse
    text_scale: float = 1.0   # globaler Faktor auf alle Schriftgroessen
    # Basiswerte (gelten bei line_scale/text_scale = 1.0):
    line_width: float = 4.2   # Strichstaerke der Linien
    line_gap: float = 5.4     # Abstand paralleler Linien (Mitte zu Mitte)
    corner_radius: float = 6.0  # Rundungsradius an Knicken (0 = scharf)
    dot_r: float = 1.7        # weisser Punkt (normale Station) pro Linie
    hub_pad: float = 3.5      # Polster des weissen Pills um das Buendel
    hub_stroke: float = 1.6   # schwarzer Rahmen der Umsteige-Pills
    font: float = 8.5         # Schriftgroesse Stationsnamen
    hub_font: float = 9.0     # Schriftgroesse Umsteigebahnhoefe
    badges: bool = True       # Linien-Badges (S 1 ...) an Endstationen

    def __post_init__(self):
        for f in ("line_width", "line_gap", "corner_radius", "dot_r",
                   "hub_pad", "hub_stroke"):
            setattr(self, f, getattr(self, f) * self.line_scale)
        for f in ("font", "hub_font"):
            setattr(self, f, getattr(self, f) * self.text_scale)

CFG = Config()

# ===========================================================================
# 1. STATIONEN  (Rasterkoordinaten; x nach rechts, y nach unten)
# ===========================================================================

STATIONS: Dict[str, Pt] = {
    # --- Nordbahn / Nord-Sued-Achse (x = 46) ---
    "Oranienburg": (46, -7), "Lehnitz": (46, -5), "Borgsdorf": (46, -3),
    "Birkenwerder": (46, -1), "Hohen Neuendorf": (46, 1), "Frohnau": (46, 3),
    "Hermsdorf": (46, 5), "Waidmannslust": (46, 7), "Wittenau": (46, 9),
    "Wilhelmsruh": (46, 11), "Schönholz": (46, 13), "Wollankstraße": (46, 15),
    "Bornholmer Straße": (46, 17), "Gesundbrunnen": (46, 20),
    "Humboldthain": (46, 23), "Nordbahnhof": (46, 26),
    "Oranienburger Straße": (46, 29), "Friedrichstraße": (46, 36),
    "Brandenburger Tor": (46, 39), "Potsdamer Platz": (46, 41),
    "Anhalter Bahnhof": (46, 44), "Yorckstraße": (46, 46),
    "Südkreuz": (46, 52), "Priesterweg": (46, 55), "Südende": (46, 58),
    "Lankwitz": (46, 60), "Lichterfelde Ost": (46, 62),
    "Osdorfer Straße": (46, 64), "Lichterfelde Süd": (46, 66),
    "Teltow Stadt": (46, 68),

    # --- Kremmener Bahn (S25 Nordwest) ---
    "Alt-Reinickendorf": (44, 11), "Karl-Bonhoeffer-Nervenklinik": (42, 9),
    "Eichborndamm": (40, 7), "Tegel": (38, 5), "Schulzendorf": (36, 3),
    "Heiligensee": (34, 1), "Hennigsdorf": (32, -1),

    # --- Stettiner Bahn / Pankow-Ast ---
    "Pankow": (49, 14), "Pankow-Heinersdorf": (52, 11),
    "Blankenburg": (55, 8), "Karow": (57, 6), "Buch": (59, 4),
    "Röntgental": (61, 2), "Zepernick": (63, 0),
    "Bernau-Friedenstal": (65, -2), "Bernau": (67, -4),

    # --- Aussenring-Ast der S8 ---
    "Bergfelde": (49, 4), "Schönfließ": (51, 6),
    "Mühlenbeck-Mönchmühle": (53, 8),

    # --- Dresdener Bahn (S2 Suedost) ---
    "Attilastraße": (48, 57), "Marienfelde": (50, 59),
    "Buckower Chaussee": (52, 61), "Schichauweg": (54, 63),
    "Lichtenrade": (56, 65), "Mahlow": (58, 67), "Blankenfelde": (60, 69),

    # --- Wannseebahn (S1 Suedwest) ---
    "Yorckstraße (Großgörschenstraße)": (44, 46),
    "Julius-Leber-Brücke": (42, 48), "Schöneberg": (38, 52),
    "Friedenau": (36, 54), "Feuerbachstraße": (34, 56),
    "Rathaus Steglitz": (32, 58), "Botanischer Garten": (30, 60),
    "Lichterfelde West": (28, 62), "Sundgauer Straße": (26, 64),
    "Zehlendorf": (24, 66), "Mexikoplatz": (21, 66),
    "Schlachtensee": (18, 66), "Nikolassee": (14, 62), "Wannsee": (12, 64),

    # --- S7 Suedwest ---
    "Griebnitzsee": (8, 68), "Babelsberg": (6, 70), "Potsdam Hbf": (4, 72),
    "Grunewald": (18, 58),

    # --- Stadtbahn (y = 36) ---
    "Spandau": (8, 22), "Stresow": (10, 24), "Pichelsberg": (12, 26),
    "Olympiastadion": (14, 28), "Heerstraße": (16, 30),
    "Messe Süd": (18, 32),
    "Westkreuz": (22, 36), "Charlottenburg": (26, 36),
    "Savignyplatz": (29, 36), "Zoologischer Garten": (32, 36),
    "Tiergarten": (35, 36), "Bellevue": (38, 36), "Hauptbahnhof": (42, 36),
    "Hackescher Markt": (50, 36), "Alexanderplatz": (53, 36),
    "Jannowitzbrücke": (56, 36), "Ostbahnhof": (59, 36),
    "Warschauer Straße": (62, 36), "Ostkreuz": (66, 36),

    # --- Ring ---
    "Westend": (22, 28), "Messe Nord/ZOB": (22, 32),
    "Halensee": (22, 40), "Hohenzollerndamm": (22, 44),
    "Heidelberger Platz": (24, 50), "Bundesplatz": (29, 52),
    "Innsbrucker Platz": (33, 52), "Tempelhof": (49, 52),
    "Hermannstraße": (52, 52), "Neukölln": (56, 52), "Sonnenallee": (60, 52),
    "Treptower Park": (66, 44), "Frankfurter Allee": (66, 32),
    "Storkower Straße": (66, 28), "Landsberger Allee": (64, 22),
    "Greifswalder Straße": (60, 20), "Prenzlauer Allee": (56, 20),
    "Schönhauser Allee": (52, 20), "Wedding": (40, 20),
    "Westhafen": (34, 20), "Beusselstraße": (30, 20),
    "Jungfernheide": (24, 22),

    # --- Ost (S3 / S5 / S7 / S75) ---
    "Rummelsburg": (70, 40), "Betriebsbahnhof Rummelsburg": (72, 42),
    "Karlshorst": (74, 44), "Wuhlheide": (78, 44), "Köpenick": (81, 44),
    "Hirschgarten": (84, 44), "Friedrichshagen": (87, 44),
    "Rahnsdorf": (90, 44), "Wilhelmshagen": (93, 44), "Erkner": (96, 44),
    "Nöldnerplatz": (69, 33), "Lichtenberg": (71, 31),
    "Friedrichsfelde Ost": (73, 29),
    "Biesdorf": (77, 28), "Wuhletal": (79, 28), "Kaulsdorf": (81, 28),
    "Mahlsdorf": (83, 28), "Birkenstein": (85, 28), "Hoppegarten": (87, 28),
    "Neuenhagen": (89, 28), "Fredersdorf": (91, 28),
    "Petershagen Nord": (93, 28), "Strausberg": (95, 28),
    "Hegermühle": (97, 26), "Strausberg Stadt": (97, 23),
    "Strausberg Nord": (97, 20),
    "Springpfuhl": (75, 27), "Poelchaustraße": (77, 25), "Marzahn": (79, 23),
    "Raoul-Wallenberg-Straße": (81, 21), "Mehrower Allee": (83, 19),
    "Ahrensfelde": (85, 17),
    "Gehrenseestraße": (75, 24), "Hohenschönhausen": (75, 21),
    "Wartenberg": (75, 18),

    # --- Goerlitzer Bahn / Suedost ---
    "Plänterwald": (68, 46), "Baumschulenweg": (70, 48),
    "Schöneweide": (72, 50), "Adlershof": (74, 52),
    "Köllnische Heide": (62, 54),
    "Oberspree": (74, 48), "Spindlersfeld": (77, 45),
    "Grünau": (77, 55), "Eichwalde": (79, 57), "Zeuthen": (81, 59),
    "Wildau": (83, 61), "Königs Wusterhausen": (85, 63),
    "Altglienicke": (72, 54), "Grünbergallee": (72, 58),
    "Schönefeld": (72, 61), "Waßmannsdorf": (70, 63),
    "Flughafen BER": (68, 65),
}

# ===========================================================================
# 2. LINIEN  (Reihenfolge im Dict = Slot-Reihenfolge im Buendel!)
# ===========================================================================

@dataclass
class Line:
    color: str
    waypoints: List[Waypoint]
    # Wie die Linie ueblicherweise genannt wird, z.B. "Wannsee -> Oranienburg".
    # Die Waypoint-Liste ist IN DIESER RICHTUNG zu lesen. Rein dokumentarisch
    # bzw. fuer Beschriftung -- die Geometrie ist richtungsunabhaengig.
    direction: str = ""
    # Querlage im Buendel. Niedriger rank = weiter nord- bzw. westlich.
    # None -> Position im LINES-Dict wird als rank benutzt.
    rank: Optional[float] = None
    closed: bool = False      # True fuer Ringlinien (erster == letzter Waypoint)
    elbow: str = "start"      # Auto-Knick nah am Segmentanfang ("start") / -ende ("end")


# Punktuelle Ausnahmen von der globalen rank-Reihenfolge.
# Key: ungeordnetes Stationspaar. Value: {Linien-ID: rank} nur fuer dieses
# Segment. Damit laesst sich z.B. erzwingen, dass zwei Linien vor einer
# Verzweigung die Seite tauschen, ohne die globale Reihenfolge zu aendern.
SEGMENT_RANKS: Dict[frozenset, Dict[str, float]] = {}


def seg_rank_override(a: str, b: str, ranks: Dict[str, float]) -> None:
    """Komfort-Helfer zum Setzen eines Segment-Overrides."""
    SEGMENT_RANKS[frozenset((a, b))] = ranks

def rev(seq):
    """Korridor-Baustein in Gegenrichtung lesen."""
    return list(reversed(seq))


# Der Nord-Sued-Tunnel als wiederverwendbarer Baustein:
_TUNNEL = ["Gesundbrunnen", "Humboldthain", "Nordbahnhof",
           "Oranienburger Straße", "Friedrichstraße", "Brandenburger Tor",
           "Potsdamer Platz", "Anhalter Bahnhof", "Yorckstraße"]
_ANHALTER_SUED = ["Südkreuz", "Priesterweg"]
_LICHTERFELDE = ["Südende", "Lankwitz", "Lichterfelde Ost", "Osdorfer Straße",
                 "Lichterfelde Süd", "Teltow Stadt"]
_STADTBAHN = ["Westkreuz", "Charlottenburg", "Savignyplatz",
              "Zoologischer Garten", "Tiergarten", "Bellevue", "Hauptbahnhof",
              "Friedrichstraße", "Hackescher Markt", "Alexanderplatz",
              "Jannowitzbrücke", "Ostbahnhof", "Warschauer Straße", "Ostkreuz"]
_SPANDAU = ["Spandau", "Stresow", "Pichelsberg", "Olympiastadion",
            "Heerstraße", "Messe Süd"]
_RING_OST = ["Schönhauser Allee", "Prenzlauer Allee", "Greifswalder Straße",
             (62, 20), "Landsberger Allee", (66, 24), "Storkower Straße",
             "Frankfurter Allee", "Ostkreuz", "Treptower Park"]
_GOERLITZER = ["Treptower Park", "Plänterwald", "Baumschulenweg",
               "Schöneweide"]
_BER_AST = ["Adlershof", "Altglienicke", "Grünbergallee", "Schönefeld",
            "Waßmannsdorf", "Flughafen BER"]

LINES: Dict[str, Line] = {
    "S1": Line("#DE4DA4", [
        "Wannsee", "Nikolassee", "Schlachtensee", "Mexikoplatz",
        "Zehlendorf", "Sundgauer Straße", "Lichterfelde West",
        "Botanischer Garten", "Rathaus Steglitz", "Feuerbachstraße",
        "Friedenau", "Schöneberg", "Julius-Leber-Brücke",
        "Yorckstraße (Großgörschenstraße)", *rev(_TUNNEL[:-1]),
        "Bornholmer Straße", "Wollankstraße", "Schönholz", "Wilhelmsruh",
        "Wittenau", "Waidmannslust", "Hermsdorf", "Frohnau",
        "Hohen Neuendorf", "Birkenwerder", "Borgsdorf", "Lehnitz",
        "Oranienburg"],
        direction="Wannsee -> Oranienburg (Sued -> Nord)", rank=10),
    "S2": Line("#006F35", [
        "Bernau", "Bernau-Friedenstal", "Zepernick", "Röntgental", "Buch",
        "Karow", "Blankenburg", "Pankow-Heinersdorf", "Pankow",
        "Bornholmer Straße", *_TUNNEL, *_ANHALTER_SUED,
        "Attilastraße", "Marienfelde", "Buckower Chaussee", "Schichauweg",
        "Lichtenrade", "Mahlow", "Blankenfelde"],
        direction="Bernau -> Blankenfelde (Nord -> Sued)", rank=11),
    "S25": Line("#00854A", [
        "Hennigsdorf", "Heiligensee", "Schulzendorf", "Tegel",
        "Eichborndamm", "Karl-Bonhoeffer-Nervenklinik", "Alt-Reinickendorf",
        "Schönholz", "Wollankstraße", "Bornholmer Straße",
        *_TUNNEL, *_ANHALTER_SUED, *_LICHTERFELDE],
        direction="Hennigsdorf -> Teltow Stadt (Nord -> Sued)", rank=12),
    "S26": Line("#4FA75C", [
        "Blankenburg", "Pankow-Heinersdorf", "Pankow", "Bornholmer Straße",
        *_TUNNEL, *_ANHALTER_SUED, *_LICHTERFELDE],
        direction="Blankenburg -> Teltow Stadt (Nord -> Sued)", rank=13),
    "S3": Line("#0A64A4", [
        *_SPANDAU, *_STADTBAHN,
        "Rummelsburg", "Betriebsbahnhof Rummelsburg", "Karlshorst",
        "Wuhlheide", "Köpenick", "Hirschgarten", "Friedrichshagen",
        "Rahnsdorf", "Wilhelmshagen", "Erkner"],
        direction="Spandau -> Erkner (West -> Ost)", rank=22),
    "S5": Line("#FF7A00", [
        *_STADTBAHN, "Nöldnerplatz", "Lichtenberg", "Friedrichsfelde Ost", (74, 28),
        "Biesdorf", "Wuhletal", "Kaulsdorf", "Mahlsdorf", "Birkenstein",
        "Hoppegarten", "Neuenhagen", "Fredersdorf", "Petershagen Nord",
        "Strausberg", "Hegermühle", "Strausberg Stadt", "Strausberg Nord"],
        direction="Westkreuz -> Strausberg Nord (West -> Ost)", rank=21),
    "S7": Line("#7A6DB0", [
        "Potsdam Hbf", "Babelsberg", "Griebnitzsee", "Wannsee", "Nikolassee",
        "Grunewald", 45, 0, 45, "Westkreuz", *_STADTBAHN[1:],
        "Nöldnerplatz", "Lichtenberg", "Friedrichsfelde Ost", "Springpfuhl",
        "Poelchaustraße", "Marzahn", "Raoul-Wallenberg-Straße",
        "Mehrower Allee", "Ahrensfelde"],
        direction="Potsdam Hbf -> Ahrensfelde (Suedwest -> Nordost)", rank=20),
    "S75": Line("#9B8AC6", [
        "Wartenberg", "Hohenschönhausen", "Gehrenseestraße", "Springpfuhl",
        "Friedrichsfelde Ost", "Lichtenberg", "Nöldnerplatz", "Ostkreuz",
        "Warschauer Straße"],
        direction="Wartenberg -> Warschauer Strasse (Nordost -> Sued)", rank=24),
    "S9": Line("#8E2C4A", [
        *_SPANDAU, *_STADTBAHN, *_GOERLITZER, *_BER_AST],
        direction="Spandau -> Flughafen BER (Nordwest -> Suedost)", rank=23),
    "S8": Line("#66B447", [
        "Birkenwerder", "Hohen Neuendorf", "Bergfelde", "Schönfließ",
        "Mühlenbeck-Mönchmühle", "Blankenburg", "Pankow-Heinersdorf",
        "Pankow", "Bornholmer Straße", (49, 17), *_RING_OST, "Plänterwald", "Baumschulenweg",
        "Schöneweide", "Adlershof", "Grünau", "Eichwalde", "Zeuthen",
        "Wildau"],
        direction="Birkenwerder -> Wildau (Nord -> Sued)", rank=32),
    # HINWEIS: S85-Linienweg aus der Vorlage nicht eindeutig ablesbar
    # (Pills an Frohnau, Pankow und Flughafen BER) -- hier als
    # Frohnau <-> BER via Bornholmer Kurve + Ostring transkribiert. Anpassen!
    "S85": Line("#54B948", [
        "Frohnau", "Hermsdorf", "Waidmannslust", "Wittenau", "Wilhelmsruh",
        "Schönholz", "Wollankstraße", "Bornholmer Straße", (49, 17), *_RING_OST,
        "Plänterwald", "Baumschulenweg", "Schöneweide", *_BER_AST],
        direction="Frohnau -> Flughafen BER (Nord -> Sued)", rank=33),
    "S15": Line("#E98ABF", [
        "Hauptbahnhof", (42, 22), "Wedding", "Gesundbrunnen"],
        direction="Hauptbahnhof -> Gesundbrunnen (Sued -> Nord)", rank=15),
    "S41": Line("#A5652A", [
        "Westend", (22, 24), "Jungfernheide", (26, 20), "Beusselstraße",
        "Westhafen", "Wedding", "Gesundbrunnen", "Schönhauser Allee",
        "Prenzlauer Allee", "Greifswalder Straße", (62, 20),
        "Landsberger Allee", (66, 24), "Storkower Straße",
        "Frankfurter Allee", "Ostkreuz", "Treptower Park", (66, 48),
        (62, 52), "Sonnenallee", "Neukölln", "Hermannstraße", "Tempelhof",
        "Südkreuz", "Schöneberg", "Innsbrucker Platz", "Bundesplatz",
        (26, 52), "Heidelberger Platz", (22, 48), "Hohenzollerndamm",
        "Halensee", "Westkreuz", "Messe Nord/ZOB", "Westend"], closed=True,
        direction="Ring im Uhrzeigersinn", rank=30),
    "S42": Line("#C4863C", [
        "Westend", (22, 24), "Jungfernheide", (26, 20), "Beusselstraße",
        "Westhafen", "Wedding", "Gesundbrunnen", "Schönhauser Allee",
        "Prenzlauer Allee", "Greifswalder Straße", (62, 20),
        "Landsberger Allee", (66, 24), "Storkower Straße",
        "Frankfurter Allee", "Ostkreuz", "Treptower Park", (66, 48),
        (62, 52), "Sonnenallee", "Neukölln", "Hermannstraße", "Tempelhof",
        "Südkreuz", "Schöneberg", "Innsbrucker Platz", "Bundesplatz",
        (26, 52), "Heidelberger Platz", (22, 48), "Hohenzollerndamm",
        "Halensee", "Westkreuz", "Messe Nord/ZOB", "Westend"], closed=True,
        direction="Ring gegen den Uhrzeigersinn", rank=31),
    "S46": Line("#CD9C53", [
        "Westend", "Messe Nord/ZOB", "Westkreuz", "Halensee",
        "Hohenzollerndamm", (22, 48), "Heidelberger Platz", (26, 52),
        "Bundesplatz", "Innsbrucker Platz", "Schöneberg", "Südkreuz",
        "Tempelhof", "Hermannstraße", "Neukölln", 135,
        "Köllnische Heide", 90, "Baumschulenweg", "Schöneweide",
        "Adlershof", "Grünau", "Eichwalde", "Zeuthen", "Wildau", "Königs Wusterhausen"],
        direction="Westend -> Koenigs Wusterhausen (Nord -> Sued)", rank=34),
    "S47": Line("#B98A45", [
        "Südkreuz", "Tempelhof", "Hermannstraße", "Neukölln", 135,
        "Köllnische Heide", 90, "Baumschulenweg", "Schöneweide",
        "Oberspree",
        "Spindlersfeld"],
        direction="Suedkreuz -> Spindlersfeld (West -> Ost)", rank=35),
}

# Stationen mit automatischem weissen "Pill" quer zum Linienbuendel
# (Umsteigebahnhoefe). Manuell gepflegt -- was ein "echter" Umsteige-
# bahnhof ist (S-Bahn-Kreuzung, U-Bahn-/Fernbahn-Anschluss), laesst
# sich nicht zuverlaessig aus der Linienverzweigung ableiten (z.B. ist
# Blankenburg ein reiner Abzweig ohne eigenen Umsteigehalt).
HUBS: set = {
    "Spandau", "Hauptbahnhof", "Friedrichstraße", "Alexanderplatz",
    "Ostbahnhof", "Warschauer Straße", "Bornholmer Straße", "Schönholz",
    "Pankow", "Birkenwerder", "Wannsee", "Nikolassee",
    "Treptower Park", "Schöneweide", "Baumschulenweg", "Adlershof",
    "Neukölln", "Westend", "Priesterweg", "Yorckstraße",
}
# Kreuzungsbahnhoefe: manuelles (Breite, Hoehe) in px, achsenparallel:
HUB_RECTS: Dict[str, Tuple[float, float]] = {
    "Westkreuz": (30, 30), "Ostkreuz": (30, 30), "Südkreuz": (28, 24),
    "Gesundbrunnen": (28, 24), "Schöneberg": (22, 22),
}

# Beschriftung: Standard rechts oberhalb; hier Abweichungen.
# Werte: (dx, dy, text-anchor)
_L = (-9, -5, "end")      # links oberhalb
_B = (0, 16, "middle")    # mittig unterhalb
LABEL_STYLE: Dict[str, Tuple[float, float, str]] = {
    **{n: _L for n in [
        "Spandau", "Stresow", "Pichelsberg", "Olympiastadion", "Heerstraße",
        "Messe Süd", "Grunewald", "Nikolassee", "Wannsee", "Griebnitzsee",
        "Babelsberg", "Potsdam Hbf", "Hennigsdorf", "Heiligensee",
        "Schulzendorf", "Tegel", "Eichborndamm",
        "Karl-Bonhoeffer-Nervenklinik", "Alt-Reinickendorf", "Westend",
        "Messe Nord/ZOB", "Halensee", "Hohenzollerndamm",
        "Heidelberger Platz", "Jungfernheide", "Beusselstraße",
        "Schlachtensee", "Mexikoplatz", "Zehlendorf", "Sundgauer Straße",
        "Lichterfelde West", "Botanischer Garten", "Rathaus Steglitz",
        "Feuerbachstraße", "Friedenau", "Julius-Leber-Brücke",
        "Yorckstraße (Großgörschenstraße)", "Schönholz", "Wollankstraße",
        "Wilhelmsruh", "Wittenau", "Waidmannslust", "Hermsdorf", "Frohnau",
        "Hohen Neuendorf", "Birkenwerder", "Borgsdorf", "Lehnitz",
        "Oranienburg", "Südende", "Lankwitz", "Lichterfelde Ost",
        "Osdorfer Straße", "Lichterfelde Süd", "Teltow Stadt",
        "Waßmannsdorf", "Schönefeld", "Grünbergallee", "Altglienicke",
        "Flughafen BER", "Köllnische Heide", "Baumschulenweg",
        "Sonnenallee", "Wartenberg", "Hohenschönhausen", "Gehrenseestraße",
    ]},
    **{n: _B for n in [
        "Charlottenburg", "Zoologischer Garten", "Bellevue",
        "Hackescher Markt", "Jannowitzbrücke", "Warschauer Straße",
        "Bundesplatz", "Innsbrucker Platz", "Tempelhof", "Hermannstraße",
        "Neukölln", "Wuhletal", "Mahlsdorf", "Hoppegarten", "Fredersdorf",
        "Strausberg", "Wuhlheide", "Hirschgarten", "Rahnsdorf",
    ]},
}

# ===========================================================================
# GEOMETRIE
# ===========================================================================

def resolve(wp: Waypoint) -> Pt:
    return STATIONS[wp] if isinstance(wp, str) else wp


def octilinear_route(p1: Pt, p2: Pt, elbow: str) -> List[Pt]:
    """Zwischenpunkte (ohne p1, mit p2) einer 0/45/90-Grad-Verbindung."""
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 or dy == 0 or abs(dx) == abs(dy):
        return [p2]
    diag = min(abs(dx), abs(dy))
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    if elbow == "start":
        bend = (x1 + sx * diag, y1 + sy * diag)
    elif abs(dx) > abs(dy):
        bend = (x2 - sx * diag, y1)
    else:
        bend = (x1, y2 - sy * diag)
    return [bend, p2]


# Knickwinkel-Konvention: 0 Grad = Norden (nach oben), im Uhrzeigersinn.
# Nur Vielfache von 45 Grad sind octilinear gueltig.
COMPASS: Dict[int, Pt] = {
    0: (0, -1), 45: (1, -1), 90: (1, 0), 135: (1, 1),
    180: (0, 1), 225: (-1, 1), 270: (-1, 0), 315: (-1, -1),
}


def angled_route(p1: Pt, p2: Pt, angle: Angle) -> List[Pt]:
    """Zwischenpunkte (ohne p1, mit p2) einer Verbindung, die p1 exakt in
    Richtung `angle` (Grad, 0 = Norden, im Uhrzeigersinn) verlaesst.

    Die zweite Teilstrecke (bis p2) wird automatisch so berechnet, dass
    der Gesamtweg octilinear bleibt -- es muss keine Knick-Koordinate
    mehr von Hand ausgerechnet werden. Von den mathematisch moeglichen
    Loesungen wird die gewaehlt, die in beiden Achsen monoton auf p2
    zulaeuft (kein Ueberschiessen + Umkehren). Gibt es keine solche
    Loesung, ist der Winkel fuer diese Verbindung nicht erreichbar.
    """
    key = int(round(angle)) % 360
    if key not in COMPASS:
        raise ValueError(f"Winkel {angle} ist kein Vielfaches von 45 Grad")
    ux, uy = COMPASS[key]
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return [p2]

    def ok_sign(component: float, total: float) -> bool:
        return total == 0 or component == 0 or (component > 0) == (total > 0)

    candidates: List[float] = []
    if ux == 0:                      # Erstes Bein senkrecht (N/S)
        if dx == 0:
            candidates = [dy / uy]
        else:
            for sign in (1, -1):
                candidates.append((dy - sign * dx) / uy)
    elif uy == 0:                    # Erstes Bein waagerecht (O/W)
        if dy == 0:
            candidates = [dx / ux]
        else:
            for sign in (1, -1):
                candidates.append((dx - sign * dy) / ux)
    else:                            # Erstes Bein diagonal
        candidates = [dx / ux, dy / uy]

    best = None
    for r1 in candidates:
        if r1 < -1e-6:
            continue
        r1 = max(r1, 0.0)
        bx, by = x1 + r1 * ux, y1 + r1 * uy
        dx2, dy2 = x2 - bx, y2 - by
        if not (dx2 == 0 or dy2 == 0 or abs(dx2 - dy2) < 1e-6
                or abs(dx2 + dy2) < 1e-6):
            continue
        if not (ok_sign(bx - x1, dx) and ok_sign(by - y1, dy)
                and ok_sign(dx2, dx) and ok_sign(dy2, dy)):
            continue
        best = (bx, by)
        break
    if best is None:
        raise ValueError(
            f"Winkel {angle} Grad fuehrt von {p1} nicht octilinear nach {p2}")
    if abs(best[0] - x1) < 1e-6 and abs(best[1] - y1) < 1e-6:
        return [p2]
    if abs(best[0] - x2) < 1e-6 and abs(best[1] - y2) < 1e-6:
        return [p2]
    return [best, p2]


def angled_route_chain(p1: Pt, p2: Pt, angles: List[Angle]) -> List[Pt]:
    """Wie angled_route, aber fuer MEHRERE Knickwinkel hintereinander
    (z.B. 45, 0, 45 -- "ein Stueck weiter in der alten Richtung, dann
    senkrecht, dann wieder diagonal zum Ziel"). Das macht Knicke, die
    nicht direkt an einer Station sitzen, sondern erst ein Stueck
    dahinter beginnen -- fuer ein ruhigeres Kartenbild.

    Mit n Winkeln gibt es n Teilstrecken-Laengen, aber nur 2 Gleichungen
    (Ziel-Dx/Dy) -- das ist unterbestimmt, ausser man reduziert die
    Unbekannten. Deshalb: Teilstrecken mit GLEICHEM Winkel bekommen
    automatisch dieselbe Laenge (Symmetrie). Es duerfen darum hoechstens
    zwei VERSCHIEDENE Winkel in der Kette vorkommen.
    """
    if len(angles) == 1:
        return angled_route(p1, p2, angles[0])

    dirs: List[Pt] = []
    for a in angles:
        key = int(round(a)) % 360
        if key not in COMPASS:
            raise ValueError(f"Winkel {a} ist kein Vielfaches von 45 Grad")
        dirs.append(COMPASS[key])

    groups: List[Pt] = []
    group_of: List[int] = []
    for d in dirs:
        if d in groups:
            group_of.append(groups.index(d))
        else:
            groups.append(d)
            group_of.append(len(groups) - 1)

    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    if len(groups) == 1:
        return angled_route(p1, p2, angles[0])
    if len(groups) > 2:
        raise ValueError(
            f"Knickkette {angles} von {p1} nach {p2} hat {len(groups)} "
            f"verschiedene Richtungen -- mit gleichlangen Teilstuecken pro "
            f"Winkel sind nur bis zu 2 verschiedene Winkel eindeutig loesbar")

    counts = [group_of.count(i) for i in range(len(groups))]
    (uxa, uya), (uxb, uyb) = groups
    ca, cb = counts
    a11, a12 = ca * uxa, cb * uxb
    a21, a22 = ca * uya, cb * uyb
    det = a11 * a22 - a12 * a21
    if abs(det) < 1e-9:
        raise ValueError(
            f"Knickkette {angles} von {p1} nach {p2} ist unterbestimmt "
            f"(die beiden Richtungen sind zueinander parallel)")
    ra = (dx * a22 - a12 * dy) / det
    rb = (a11 * dy - dx * a21) / det
    lengths = [ra, rb]
    for r in lengths:
        if r < -1e-6:
            raise ValueError(
                f"Knickkette {angles} von {p1} nach {p2} ergibt eine "
                f"negative Teilstrecke -- Winkel oder Reihenfolge pruefen")

    out: List[Pt] = []
    x, y = p1
    for i, (ux, uy) in enumerate(dirs):
        r = max(lengths[group_of[i]], 0.0)
        x, y = x + r * ux, y + r * uy
        out.append((x, y))
    out[-1] = p2
    cleaned: List[Pt] = []
    prev = p1
    for p in out:
        if hypot(p[0] - prev[0], p[1] - prev[1]) > 1e-6:
            cleaned.append(p)
            prev = p
    return cleaned if cleaned else [p2]


def build_path(line: Line) -> Tuple[List[Pt], Dict[int, str]]:
    """Waypoints -> vollstaendige Punktfolge + {Pfadindex: Stationsname}."""
    pts: List[Pt] = []
    st_idx: Dict[int, str] = {}
    pending_angles: List[Angle] = []
    for wp in line.waypoints:
        if isinstance(wp, (int, float)) and not isinstance(wp, bool):
            pending_angles.append(wp)
            continue
        p = resolve(wp)
        if not pts:
            pts.append(p)
        elif p != pts[-1]:
            if pending_angles:
                pts.extend(angled_route_chain(pts[-1], p, pending_angles))
            else:
                pts.extend(octilinear_route(pts[-1], p, line.elbow))
        pending_angles = []
        if isinstance(wp, str):
            st_idx[len(pts) - 1] = wp
    return pts, st_idx


def seg_key(p: Pt, q: Pt):
    a = (round(p[0], 3), round(p[1], 3))
    b = (round(q[0], 3), round(q[1], 3))
    return (a, b) if a <= b else (b, a)


def _norm(v: Pt) -> Pt:
    l = hypot(v[0], v[1])
    return (v[0] / l, v[1] / l)


def canonical_normal(key) -> Pt:
    """Einheitsnormale des Segments, unabhaengig von der Fahrtrichtung.

    Konvention: die Normale zeigt immer nach Osten bzw. (bei exakt
    senkrechten Segmenten) nach Sueden. Da der Versatz mit
    (slot - (n-1)/2) * gap berechnet wird, liegt ein NIEDRIGER rank damit
    immer auf der entgegengesetzten Seite, also weiter NORDEN bzw. WESTEN.

        waagerechter Korridor : rank klein = weiter noerdlich (oben)
        senkrechter Korridor  : rank klein = weiter westlich (links)
        diagonaler Korridor   : rank klein = nordwestliche Seite
    """
    (ax, ay), (bx, by) = key
    dx, dy = bx - ax, by - ay
    n = _norm((-dy, dx))
    if n[0] < -1e-9 or (abs(n[0]) <= 1e-9 and n[1] < 0):
        n = (-n[0], -n[1])
    return n


def _intersect(a1: Pt, b1: Pt, a2: Pt, b2: Pt) -> Optional[Pt]:
    r = (b1[0] - a1[0], b1[1] - a1[1])
    s = (b2[0] - a2[0], b2[1] - a2[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-9:
        return None
    t = ((a2[0] - a1[0]) * s[1] - (a2[1] - a1[1]) * s[0]) / den
    return (a1[0] + t * r[0], a1[1] + t * r[1])


def offset_polyline(pts: List[Pt], vecs: List[Pt], closed: bool) -> List[Pt]:
    """Verschiebt jede Kante um ihren Vektor und verbindet die Kanten.

    Zwei Faelle:
    a) Richtungswechsel (echter Knick): Miter-Join ueber den Schnittpunkt
       der beiden versetzten Geraden -> Winkel bleibt octilinear erhalten.
    b) Gleiche Richtung, aber anderer Offset (Bündelzusammensetzung aendert
       sich mitten im geraden Korridor, z.B. wenn eine Linie dazustoesst):
       die versetzten Geraden sind parallel und haben keinen Schnittpunkt.
       Statt zu mitteln (-> schraeger Versprung) wird hier ein expliziter
       45-Grad-Versatz eingefuegt: um |delta| vor dem Gelenk ausscheren,
       um |delta| dahinter wieder einschwenken.

    Rueckgabe: (versetzte Punktfolge, idx_map) mit
    idx_map[i] = Index des Rohpunkts i in der Ausgabe.
    """
    n = len(pts)
    shifted = [((pts[i][0] + vecs[i][0], pts[i][1] + vecs[i][1]),
                (pts[i + 1][0] + vecs[i][0], pts[i + 1][1] + vecs[i][1]))
               for i in range(n - 1)]
    out: List[Pt] = [shifted[0][0]]
    idx_map: List[int] = [0]
    for i in range(1, n - 1):
        d_prev = _norm((pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
        d_next = _norm((pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))
        collinear = (abs(d_prev[0] - d_next[0]) < 1e-9
                     and abs(d_prev[1] - d_next[1]) < 1e-9)

        if collinear:
            delta = hypot(vecs[i][0] - vecs[i - 1][0],
                          vecs[i][1] - vecs[i - 1][1])
            if delta > 1e-9:
                # 45-Grad-Rampe. Laengs delta/2 vor UND delta/2 hinter dem
                # Gelenk => Laengsweg delta bei Querversatz delta => exakt 45.
                # (delta statt delta/2 ergaebe 2:1, also 26,57 Grad.)
                h = delta / 2
                a, b = shifted[i - 1][1], shifted[i][0]
                out.append((a[0] - d_prev[0] * h, a[1] - d_prev[1] * h))
                idx_map.append(len(out) - 1)
                out.append((b[0] + d_next[0] * h, b[1] + d_next[1] * h))
                continue
            out.append(shifted[i][0])
            idx_map.append(len(out) - 1)
            continue

        p = _intersect(*shifted[i - 1], *shifted[i])
        if p is None or hypot(p[0] - pts[i][0], p[1] - pts[i][1]) > 60:
            p = ((shifted[i - 1][1][0] + shifted[i][0][0]) / 2,
                 (shifted[i - 1][1][1] + shifted[i][0][1]) / 2)
        out.append(p)
        idx_map.append(len(out) - 1)
    out.append(shifted[-1][1])
    idx_map.append(len(out) - 1)
    if closed:
        p = _intersect(*shifted[-1], *shifted[0])
        if p is not None:
            out[0] = out[-1] = p
    return out, idx_map


def rounded_path_d(pts: List[Pt], closed: bool, radius: float) -> str:
    """SVG-Pfaddaten mit sanft abgerundeten statt scharfen Knicken.

    An jeder Zwischenecke wird auf beiden Seiten um `radius` zurueck-
    geschnitten und die Ecke per quadratischer Bezier-Kurve (Kontroll-
    punkt = urspruengliche Ecke) verrundet. Der tatsaechlich genutzte
    Radius wird pro Ecke auf hoechstens die halbe Laenge der jeweils
    anliegenden Teilstrecke begrenzt, damit sich Rundungen an kurzen
    Buendel-Rampen nicht ueberlappen. Kollineare Punkte (kein echter
    Knick) bleiben unveraendert gerade.
    """
    def sub(a, b): return (a[0] - b[0], a[1] - b[1])
    def add(a, b): return (a[0] + b[0], a[1] + b[1])
    def scale(a, s): return (a[0] * s, a[1] * s)

    def fillet(corner: Pt, prev_p: Pt, next_p: Pt) -> Tuple[Pt, Pt]:
        len1 = hypot(*sub(corner, prev_p))
        len2 = hypot(*sub(next_p, corner))
        r = min(radius, len1 / 2, len2 / 2) if len1 > 1e-9 and len2 > 1e-9 else 0
        d1 = _norm(sub(corner, prev_p)) if len1 > 1e-9 else (0.0, 0.0)
        d2 = _norm(sub(next_p, corner)) if len2 > 1e-9 else (0.0, 0.0)
        return sub(corner, scale(d1, r)), add(corner, scale(d2, r))

    if radius <= 0:
        d = "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in pts)
        return d + (" Z" if closed else "")

    if closed:
        core = pts[:-1]
        m = len(core)
        if m < 3:
            d = "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in pts)
            return d + " Z"
        p_in, p_out = [None] * m, [None] * m
        for i in range(m):
            p_in[i], p_out[i] = fillet(core[i], core[i - 1], core[(i + 1) % m])
        start = p_out[0]
        parts = [f"M {start[0]:.3f} {start[1]:.3f}"]
        for i in range(m):
            nxt = (i + 1) % m
            parts.append(f"L {p_in[nxt][0]:.3f} {p_in[nxt][1]:.3f}")
            parts.append(f"Q {core[nxt][0]:.3f} {core[nxt][1]:.3f} "
                         f"{p_out[nxt][0]:.3f} {p_out[nxt][1]:.3f}")
        return " ".join(parts)

    m = len(pts)
    if m < 3:
        return "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in pts)
    parts = [f"M {pts[0][0]:.3f} {pts[0][1]:.3f}"]
    for i in range(1, m - 1):
        p_in, p_out = fillet(pts[i], pts[i - 1], pts[i + 1])
        parts.append(f"L {p_in[0]:.3f} {p_in[1]:.3f}")
        parts.append(f"Q {pts[i][0]:.3f} {pts[i][1]:.3f} "
                     f"{p_out[0]:.3f} {p_out[1]:.3f}")
    parts.append(f"L {pts[-1][0]:.3f} {pts[-1][1]:.3f}")
    return " ".join(parts)

# ===========================================================================
# RENDERING
# ===========================================================================

def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def auto_label(direction: Pt, clearance: float) -> Tuple[float, float, str]:
    """Label-Versatz (dx, dy, text-anchor) aus der lokalen Fahrtrichtung.

    Regel (waagerechter Text, Seite folgt der Linie):
      - waagerechte Linie  -> Label unterhalb, zentriert
      - senkrechte Linie   -> Label rechts, linksbuendig
      - diagonale Linie    -> Label auf der oberen (noerdlichen) Seite,
                              Textausrichtung waechst von der Linie weg
    `clearance` ist der Abstand vom Stationspunkt (px), typ. halbe
    Buendelbreite + etwas Luft.
    """
    ux, uy = _norm(direction) if direction != (0, 0) else (1.0, 0.0)
    if abs(uy) < 0.35:                       # waagerecht -> unten
        return (0.0, clearance + CFG.font, "middle")
    if abs(ux) < 0.35:                       # senkrecht -> rechts
        return (clearance, 0.35 * CFG.font, "start")
    nx, ny = -uy, ux                         # Normale
    if ny > 0:                               # auf die obere Seite drehen
        nx, ny = -nx, -ny
    dx, dy = nx * clearance, ny * clearance
    anchor = "start" if dx >= 0 else "end"
    return (dx, dy - 1.0, anchor)


def build_svg(lines=None, coords=None, hubs=None, hub_rects=None,
              labels=None, seg_ranks=None, paths=None, st_maps=None,
              auto_labels=False) -> str:
    # Defaults = bisherige Globals -> build_svg() verhaelt sich unveraendert.
    lines = LINES if lines is None else lines
    coords = STATIONS if coords is None else coords
    hubs = HUBS if hubs is None else hubs
    hub_rects = HUB_RECTS if hub_rects is None else hub_rects
    labels = LABEL_STYLE if labels is None else labels
    seg_ranks = SEGMENT_RANKS if seg_ranks is None else seg_ranks

    # Effektiver rank je Linie (None -> Dict-Position)
    base_rank = {name: (ln.rank if ln.rank is not None else i)
                 for i, (name, ln) in enumerate(lines.items())}

    # Pass 1: Pfade bauen (Rasterkoordinaten). Vorgebaute Pfade (z.B. aus
    # walk_turnline) koennen uebergeben werden; sonst aus build_path.
    if paths is None:
        paths, st_maps = {}, {}
        for lid, line in lines.items():
            paths[lid], st_maps[lid] = build_path(line)
            if line.closed:
                assert paths[lid][0] == paths[lid][-1], \
                    f"{lid}: Ring nicht geschlossen"

    # Pass 2: Segmentbelegung + Zuordnung Geometrie -> Stationspaar
    usage: Dict[object, List[str]] = {}
    seg_pair: Dict[object, frozenset] = {}
    for lid, line in lines.items():
        pts = paths[lid]
        rev_map = {i: n for i, n in st_maps[lid].items()}
        for i in range(len(pts) - 1):
            k = seg_key(pts[i], pts[i + 1])
            usage.setdefault(k, [])
            if lid not in usage[k]:
                usage[k].append(lid)
            a, b = rev_map.get(i), rev_map.get(i + 1)
            if a and b and k not in seg_pair:
                seg_pair[k] = frozenset((a, b))

    # Sortierung je Segment nach rank (ggf. mit Override)
    for k, group in usage.items():
        ov = seg_ranks.get(seg_pair.get(k, frozenset()), {})
        group.sort(key=lambda l: (ov.get(l, base_rank[l]), l))

    # Grid -> px
    all_pts = [p for pts in paths.values() for p in pts]
    minx = min(p[0] for p in all_pts)
    miny = min(p[1] for p in all_pts)
    maxx = max(p[0] for p in all_pts)
    maxy = max(p[1] for p in all_pts)

    def px(p: Pt) -> Pt:
        return ((p[0] - minx) * CFG.grid + CFG.margin,
                (p[1] - miny) * CFG.grid + CFG.margin)

    W = (maxx - minx) * CFG.grid + 2 * CFG.margin
    H = (maxy - miny) * CFG.grid + 2 * CFG.margin

    # Pass 3: versetzte Pfade in px
    off_paths: Dict[str, List[Pt]] = {}
    idx_maps: Dict[str, List[int]] = {}
    for lid, pts in paths.items():
        vecs: List[Pt] = []
        for i in range(len(pts) - 1):
            key = seg_key(pts[i], pts[i + 1])
            group = usage[key]
            slot = group.index(lid)
            off = (slot - (len(group) - 1) / 2) * CFG.line_gap
            nx, ny = canonical_normal(key)
            vecs.append((nx * off, ny * off))
        off_paths[lid], idx_maps[lid] = offset_polyline(
            [px(p) for p in pts], vecs, lines[lid].closed)

    svg: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" '
        f'height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}" '
        f'font-family="Helvetica, Arial, sans-serif">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]

    # Linien
    for lid, opts in off_paths.items():
        d = rounded_path_d(opts, lines[lid].closed, CFG.corner_radius)
        svg.append(f'<path d="{d}" fill="none" stroke="{lines[lid].color}" '
                   f'stroke-width="{CFG.line_width}" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')

    # Umsteige-Pills (weiss, ueber den Linien)
    hub_pts: Dict[str, List[Pt]] = {}
    hub_dir: Dict[str, Pt] = {}
    for lid, smap in st_maps.items():
        for idx, name in smap.items():
            hub_pts.setdefault(name, []).append(
                off_paths[lid][idx_maps[lid][idx]])
            if name not in hub_dir:
                p = paths[lid]
                j = idx if idx < len(p) - 1 else idx - 1
                hub_dir[name] = _norm((p[j + 1][0] - p[j][0],
                                       p[j + 1][1] - p[j][1]))

    def hub_pill(name: str):
        c = px(coords[name])
        if name in hub_rects:
            w, h = hub_rects[name]
            w *= CFG.line_scale
            h *= CFG.line_scale
            return (f'<rect x="{c[0]-w/2:.1f}" y="{c[1]-h/2:.1f}" '
                    f'width="{w:.1f}" height="{h:.1f}" '
                    f'rx="{7 * CFG.line_scale:.1f}" fill="white" '
                    f'stroke="black" stroke-width="{CFG.hub_stroke}"/>')
        pts = hub_pts.get(name, [c])
        d = hub_dir.get(name, (1, 0))
        nx, ny = -d[1], d[0]
        proj = [(p[0]-c[0]) * nx + (p[1]-c[1]) * ny for p in pts]
        length = (max(proj) - min(proj)) + CFG.line_width + 2 * CFG.hub_pad
        thick = CFG.line_width + 2 * CFG.hub_pad
        ang = degrees(atan2(ny, nx))
        return (f'<g transform="translate({c[0]:.1f},{c[1]:.1f}) '
                f'rotate({ang:.1f})"><rect x="{-length/2:.1f}" '
                f'y="{-thick/2:.1f}" width="{length:.1f}" '
                f'height="{thick:.1f}" rx="{thick/2:.1f}" fill="white" '
                f'stroke="black" stroke-width="{CFG.hub_stroke}"/></g>')

    hub_names = set(hubs) | set(hub_rects)
    for name in hub_names:
        svg.append(hub_pill(name))

    # Stationspunkte (weisse Dots pro Linie)
    seen = set()
    for lid, smap in st_maps.items():
        for idx, name in smap.items():
            if name in hub_names or (lid, name) in seen:
                continue
            seen.add((lid, name))
            x, y = off_paths[lid][idx_maps[lid][idx]]
            svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{CFG.dot_r}" '
                       f'fill="white"/>')

    # Linien-Badges an Endstationen
    if CFG.badges:
        for lid, line in lines.items():
            if line.closed:
                continue
            bs = CFG.text_scale
            for end, other in ((0, 1), (-1, -2)):
                p, q = off_paths[lid][end], off_paths[lid][other]
                d = _norm((p[0] - q[0], p[1] - q[1]))
                gap = 17 * max(CFG.line_scale, bs)
                bx, by = p[0] + d[0] * gap, p[1] + d[1] * gap
                svg.append(
                    f'<g><rect x="{bx-12*bs:.1f}" y="{by-6.5*bs:.1f}" '
                    f'width="{24*bs:.1f}" height="{13*bs:.1f}" '
                    f'rx="{6.5*bs:.1f}" fill="{line.color}"/>'
                    f'<text x="{bx:.1f}" y="{by+3*bs:.1f}" '
                    f'font-size="{8*bs:.1f}" font-weight="bold" fill="white" '
                    f'text-anchor="middle">{lid}</text></g>')

    # Beschriftung
    for name, coord in coords.items():
        if not any(name in m.values() for m in st_maps.values()):
            continue
        x, y = px(coord)
        if name in labels:                       # manueller Override
            dx, dy, anchor = labels[name]
        elif auto_labels:                        # aus der Geometrie ableiten
            clr = CFG.line_width / 2 + CFG.hub_pad + 2
            dx, dy, anchor = auto_label(hub_dir.get(name, (1, 0)), clr)
        else:                                    # bisheriger fester Default
            dx, dy, anchor = (9, -5, "start")
        big = name in hub_names
        fs = CFG.hub_font if big else CFG.font
        fw = ' font-weight="bold"' if big else ""
        svg.append(f'<text x="{x+dx:.1f}" y="{y+dy:.1f}" font-size="{fs}"'
                   f'{fw} fill="#111" text-anchor="{anchor}">'
                   f'{esc(name)}</text>')

    svg.append("</svg>")

    # Statistiken
    bundles = sorted(usage.values(), key=len)[-1]
    print(f"Linien: {len(lines)}  |  Stationen: "
          f"{len({n for m in st_maps.values() for n in m.values()})}  |  "
          f"Segmente: {len(usage)}  |  groesstes Buendel: {len(bundles)} "
          f"({', '.join(bundles)})")
    return "\n".join(svg)


def check_octilinear() -> List[str]:
    """Meldet direkte Waypoint-Verbindungen, die NICHT 0/45/90 Grad sind.

    Solche Segmente werden vom Auto-Routing zwar in zwei saubere Teilstuecke
    zerlegt, der eingefuegte Knick sitzt aber an einer willkuerlichen Stelle.
    Fuer ein ruhiges Kartenbild sollten die Stationskoordinaten so gewaehlt
    sein, dass moeglichst wenige Meldungen auftreten -- oder man setzt einen
    expliziten (x, y)-Waypoint oder Knickwinkel an die gewuenschte Stelle.

    Verbindungen mit einem Winkel-Marker dazwischen werden nicht gemeldet:
    dort ist der Knick gewollt und wird von angled_route() bereits validiert.
    """
    issues: List[str] = []
    for lid, line in LINES.items():
        wps = line.waypoints
        last = None
        angled = False
        for wp in wps:
            if isinstance(wp, (int, float)) and not isinstance(wp, bool):
                angled = True
                continue
            if last is not None and not angled:
                a, b = last, wp
                (x1, y1), (x2, y2) = resolve(a), resolve(b)
                dx, dy = x2 - x1, y2 - y1
                if not (dx == 0 and dy == 0) and not (
                        dx == 0 or dy == 0 or abs(dx) == abs(dy)):
                    na = a if isinstance(a, str) else f"({x1:g},{y1:g})"
                    nb = b if isinstance(b, str) else f"({x2:g},{y2:g})"
                    issues.append(
                        f"  {lid}: {na} -> {nb}  (dx={dx:g}, dy={dy:g})")
            last = wp
            angled = False
    return issues


def validate():
    for lid, line in LINES.items():
        for wp in line.waypoints:
            if isinstance(wp, str) and wp not in STATIONS:
                raise KeyError(f"{lid}: unbekannte Station '{wp}'")
    for name in HUBS | set(HUB_RECTS) | set(LABEL_STYLE):
        if name not in STATIONS:
            raise KeyError(f"Attribut fuer unbekannte Station '{name}'")


if __name__ == "__main__":
    validate()
    probs = check_octilinear()
    if probs:
        print(f"WARNUNG: {len(probs)} nicht-octilineare Segmente:")
        for p in probs[:25]:
            print(p)
        if len(probs) > 25:
            print(f"  ... und {len(probs) - 25} weitere")
    else:
        print("Alle Segmente sind octilinear (0/45/90 Grad).")
    out = "outputs/netzplan.svg"
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_svg())
    print(f"Geschrieben: {out}")
