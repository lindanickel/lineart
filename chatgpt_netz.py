"""
Gleiskarte-Engine
=================

Ziel
----
Diese Datei erzeugt eine schematische "Gleiskarte" / Korridorkarte:
- genau EINE graue Mittellinie pro physischem Streckenabschnitt
- gemeinsame Stationspaare werden als ein gemeinsamer Korridor behandelt
- Linien werden nur ueber Stationsfolge + relative Turns definiert
- die Engine bestimmt Startlage und elastische Laengen deterministisch
- hervorgehobene Linien werden farbig darueber gezeichnet und auf
  gemeinsamen Abschnitten parallel versetzt (gebuendelt)

Umsteige-Pills fuer Hubs fehlen noch.

Abhaengigkeit
-------------
    pip install numpy

Ausfuehren
----------
    python3 chatgpt_netz.py

Erzeugt
-------
    outputs/gleiskarte.svg

Phasen
------
Die Kartenerstellung laeuft in klar getrennten Phasen; jede hat ihr eigenes
Ergebnis und ihre eigene Konfiguration:

    1  Definition   STATIONS, Strecken-Konstanten, TRACK_LINES
                    -- von Hand geschrieben, keine Berechnung
    2  Netz         build_network()  -> Network (parsed, starts)
                    NetzConfig; prueft Definition und gemeinsame Korridore
    3  Masse        solve_measures() -> Measures (coords, leg_lengths)
                    SolverConfig; hier bekommen FlexPaths ihre Laenge
    4  Gleise       build_tracks()   -> Tracks (corridor_paths, corners)
                    ohne Konfiguration, rein rekonstruierend
    5  Darstellung  build_track_svg() -> SVG
                    StyleConfig; aendert nie die Geometrie

`solve_layout()` verkettet Phase 2-4 zu einem LayoutResult. Weil Phase 5 nur
StyleConfig bekommt, lassen sich Farben, Strichstaerken, Kurvenradien und
Buendelabstand aendern und neu rendern, ohne neu zu loesen.

Definitionssprache
------------------
Es gibt vier Bausteine:

    "Wannsee"        Station. Der Sprung von der vorherigen Station hierher
                     ist standardmaessig starr und `spacing` lang.
    Turn(45)         Richtungswechsel zwischen zwei Stationen (Vielfaches
                     von 45 Grad, positiv = im Uhrzeigersinn).
    FlexPath()       Macht das direkt angrenzende Beinstueck elastisch --
                     seine Laenge bestimmt der Solver (zwischen optionalem
                     min_length und max_length).
    FixPath(laenge)  Macht das direkt angrenzende Beinstueck starr mit einer
                     explizit angegebenen Laenge (statt spacing/radius).

FlexPath() und FixPath() sind beides Fabrikfunktionen fuer dieselbe interne
`Path`-Klasse (Felder: length, minimum, maximum, flexibility) -- sie setzen
nur unterschiedliche Standardwerte.

Beispiele:

    "A", "B"                        starrer gerader Sprung
    "A", FlexPath(), "B"            elastischer gerader Sprung
    "A", FixPath(3.0), "B"          starrer Sprung mit expliziter Laenge 3.0
    "A", Turn(45), "B"              Knick genau in der Mitte zwischen A und B
    "A", Turn(45), FlexPath(), "B"  festes Stueck bis zum Knick, danach elastisch
    "A", FlexPath(), Turn(45), "B"  Spiegelbild davon

Die Laenge gehoert immer zum Segment bzw. Bein VON der vorherigen Station
ZUR angegebenen Station.

Gemeinsame Korridore
--------------------
Fahren zwei Linien ueber dieselbe Kante, muss deren Form (Knickwinkel und
Elastizitaet jedes Beinstuecks) in beiden Linien uebereinstimmen; die
Engine prueft das und meldet Abweichungen. Die Fahrtrichtung darf sich
unterscheiden -- rueckwaerts durchlaufene Kanten werden spiegelbildlich
verglichen.

Damit das nicht von Hand synchron gehalten werden muss, werden Strecken
(Korridore) einmal weiter unten definiert -- inklusive ihrer Turns und
FlexPaths -- und in den Liniendefinitionen per `_slice()` bzw.
`_reversed()` wiederverwendet. Eine Aenderung an einer Strecke wirkt so
automatisch auf alle Linien, die sie befahren.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from math import atan2, cos, hypot, pi, radians, sin, sqrt, tan
from pathlib import Path as FilePath
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

try:
    import numpy as np
except ImportError as exc:
    raise SystemExit(
        "Diese Datei benoetigt NumPy. Installation: python3 -m pip install numpy"
    ) from exc


Pt = Tuple[float, float]


# ============================================================================
# KONFIGURATION
# ============================================================================

# Die Konfiguration ist nach Phasen getrennt, damit jede Funktion nur die
# Parameter sieht, die ihre Phase betreffen -- ein Renderparameter kann so
# nicht versehentlich die Geometrie beeinflussen (und umgekehrt).

@dataclass
class NetzConfig:
    """Phase 2 (Netz): Vorgaben beim Parsen und Ausrichten."""
    # Startwinkel fuer Linien ohne eigenen `start` und ohne Verbindung zu
    # einer bereits ausgerichteten Linie. Grad, 0 = Nord, im Uhrzeigersinn.
    default_start: int = 45
    # Untere Laengenschranke fuer FlexPath() ohne eigenes min_length.
    min_gap_default: float = 0.2

    # KREISRADIEN der Rundungen, in Gitter-Einheiten (wie spacing) -- nicht
    # in Pixeln, und nicht die Tangentenlaenge. Die Tangente wird intern
    # berechnet: t = R * tan(delta/2). Ein explizites Turn(radius=...) ist
    # ebenfalls ein Kreisradius und schlaegt diese Defaults.
    #
    # Je Knickwinkel ein eigener Wert, weil derselbe Radius bei 45 und bei
    # 135 Grad optisch sehr unterschiedlich wirkt (die Bogenlaenge ist
    # dreimal so gross). Sollen zwei Winkel gleich aussehen, gib ihnen
    # einfach denselben Wert -- das ist der Sinn echter Radien.
    #
    # Stehen hier und nicht in StyleConfig, weil eine Kurve Platz auf ihren
    # Nachbarbeinen braucht: der Radius geht als untere Laengenschranke in
    # den Solver ein (siehe _build_legs_from_modifiers). Ein Radius ist
    # damit Geometrie, keine Darstellung.
    curve_radius_45: float = 0.8
    curve_radius_90: float = 1.2
    curve_radius_135: float = 0.4

    def curve_radius(self, delta: int) -> float:
        """Default-Kreisradius fuer einen Knick um `delta` Grad."""
        angle = abs(((delta + 180) % 360) - 180)
        return {
            45: self.curve_radius_45,
            135: self.curve_radius_135,
        }.get(angle, self.curve_radius_90)


@dataclass
class SolverConfig:
    """Phase 3 (Masse): numerische Parameter des Least-Squares-Solvers."""
    # Horizontaler Abstand zwischen Netzteilen, die untereinander keine
    # gemeinsame Station haben.
    component_gap: float = 40.0
    coordinate_regularization: float = 1e-10
    constraint_tolerance: float = 1e-7


@dataclass
class StyleConfig:
    """Phase 5 (Darstellung): reine Optik. Aendert nie die Geometrie --
    diese Werte lassen sich anpassen und neu rendern, ohne neu zu loesen."""
    # Massstab: eine Gitter-Einheit in Pixeln, plus Rand um die Karte.
    grid: float = 28.0
    margin: float = 72.0

    # Korridore und hervorgehobene Linien
    track_color: str = "#444444"
    line_width: float = 1.0
    highlight_line_width: float = 8.0

    # Stationen
    station_r: float = 2.6
    station_stroke: float = 1.0
    # Hubs bekommen ein weisses Rechteck mit schwarzem Rand ueber die
    # Linien gelegt, darauf je Linie einen Punkt in ihrer Farbe.
    # `hub_pill_r` ist der Eckradius; das Rechteck wird um genau diesen Wert
    # ueber die aeussersten Stationspunkte hinaus aufgeweitet, wodurch seine
    # schmale Seite 2*r misst und die Enden zu Halbkreisen werden.
    hub_pill_r: float = 6.0
    hub_pill_stroke: float = 2.5
    hub_dot_r: float = 3.0
    # Endstationen: weisser Kreis mit schwarzem Rand und Farbpunkt darin.
    terminus_ring_r: float = 6.0
    terminus_ring_stroke: float = 2.5
    terminus_dot_r: float = 3.0

    # Buendelung: Zwischenraum zwischen zwei Linien, die sich einen Track
    # teilen -- als Vielfaches der Linienstaerke. 0.5 = eine halbe
    # Linienstaerke Luft zwischen den Linienraendern. Der Versatz je
    # Slot-Schritt ist damit Linienstaerke + Zwischenraum, ein Slot von 0.5
    # rueckt eine Linie also um die Haelfte davon aus der Mitte.
    bundle_gap_factor: float = 0.5

    @property
    def bundle_gap(self) -> float:
        """Zwischenraum zwischen zwei benachbarten Linien, in Pixeln."""
        return self.highlight_line_width * self.bundle_gap_factor

    @property
    def bundle_spacing(self) -> float:
        """Abstand zweier benachbarter Linienmitten, in Gitter-Einheiten."""
        return (self.highlight_line_width + self.bundle_gap) / self.grid

    # Labels
    label_font: float = 10.0
    label_family: str = "'Helvetica Neue', Helvetica, Arial, sans-serif"
    label_fill: str = "#111111"
    # Weisser Rand der oben liegenden Linie an Kreuzungen ohne Hub, je Seite.
    crossing_casing: float = 2.0

    # Linien-Plaketten an den Endpunkten: farbiges Oval unter dem
    # Stationsnamen, weisser fetter Liniennamen darin.
    badge_font: float = 12.0
    badge_height: float = 15.0
    badge_padding: float = 3.0
    badge_gap: float = 3.0

    label_clearance: float = 8.0
    # Hubs und Endstationen tragen ein groesseres Symbol als ein einfacher
    # Stationspunkt. Seitliche Beschriftungen ruecken dort um diesen Betrag
    # weiter nach aussen, sonst kleben sie am Marker.
    label_marker_luft: float = 2.0
    # Geklammerte Namenszusaetze ("(Eichkamp)") werden um so viele Punkte
    # kleiner gesetzt als der Stationsname selbst.
    label_zusatz_kleiner: float = 1.5


@dataclass
class Config:
    """Alle Phasen-Konfigurationen unter einem Dach.

    `solve_layout()` bekommt das ganze Config-Objekt (es durchlaeuft Phase
    2-4), `build_track_svg()` dagegen nur `.style` -- damit ist an der
    Signatur ablesbar, dass das Rendern die Geometrie nicht beeinflusst.
    """
    netz: NetzConfig = field(default_factory=NetzConfig)
    solver: SolverConfig = field(default_factory=SolverConfig)
    style: StyleConfig = field(default_factory=StyleConfig)


CFG = Config()


# Normierte 45-Grad-Richtungen
_D = 1.0 / sqrt(2.0)
COMPASS: Dict[int, Pt] = {
    0: (0.0, -1.0),
    45: (_D, -_D),
    90: (1.0, 0.0),
    135: (_D, _D),
    180: (0.0, 1.0),
    225: (-_D, _D),
    270: (-1.0, 0.0),
    315: (-_D, -_D),
}


# ============================================================================
# PHASE 1: DEFINITIONSSPRACHE
# ============================================================================

class GeometryError(ValueError):
    pass


# Die acht moeglichen Lagen einer Beschriftung, als Einheitsvektor vom
# Stationspunkt aus (y zeigt nach unten).
LABEL_RICHTUNG: Dict[str, Pt] = {
    "top": (0.0, -1.0),
    "bottom": (0.0, 1.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "top_left": (-_D, -_D),
    "top_right": (_D, -_D),
    "bottom_left": (-_D, _D),
    "bottom_right": (_D, _D),
}


@dataclass(frozen=True)
class Turn:
    """Relativer Richtungswechsel zwischen zwei Stationen.

    `radius` ist der KREISRADIUS der Rundung. Er legt zugleich die
    Standard-Beinlaenge auf JEDER Seite dieses Turns fest (vor UND nach dem
    Knick), solange dort kein FlexPath() steht -- naemlich auf die
    zugehoerige Tangentenlaenge R * tan(delta/2). None = die
    Haelfte des normalen Stationsabstands der Linie (line.spacing / 2) --
    das setzt den Knick standardmaessig genau in die Mitte des Abschnitts:
        "A", Turn(45), "B"                  -> Knick genau in der Mitte
        "A", Turn(45), FlexPath(), "B"      -> festes halbes Stueck bis zum
                                               Knick, danach elastisch
        "A", FlexPath(), Turn(45), "B"      -> Spiegelbild davon
    """
    delta: int
    radius: Optional[float] = None

    def __post_init__(self) -> None:
        if self.delta % 45 != 0:
            raise ValueError("Turns muessen Vielfache von 45 Grad sein")
        if self.radius is not None and self.radius <= 0:
            raise ValueError("radius muss positiv sein")


@dataclass(frozen=True)
class Path:
    """Markiert das direkt angrenzende Beinstueck -- egal ob es das Stueck
    vor einem Turn, das Stueck danach, oder ein ganz gerader Sprung ohne
    jeden Turn ist. Wird nicht direkt konstruiert, sondern ueber die
    Fabrikfunktionen `FlexPath()` bzw. `FixPath()`.

    length        bei flexibility=True die bevorzugte (preferred) Laenge,
                  die der Solver anpassen darf; bei flexibility=False die
                  exakte, starre Laenge.
    minimum       untere Schranke fuer den Solver. Nur bei flexibility=True
                  gesetzt, sonst None.
    maximum       obere Schranke fuer den Solver. Nur bei flexibility=True
                  gesetzt, sonst None.
    flexibility   True = elastisch (Solver bestimmt die Laenge zwischen
                  minimum und maximum), False = starr mit exakt `length`.
    flex          Steifigkeit des elastischen Beins (nur flexibility=True).
    group         koppelt mehrere elastische Beinstuecke derselben Linie auf
                  eine gemeinsame Laenge (z.B. gleichmaessige Abstaende
                  entlang einer Ringseite).
    """
    length: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    flexibility: bool = True
    flex: float = 1.0
    group: Optional[str] = None

    def __post_init__(self) -> None:
        if self.flexibility:
            if self.length is not None and self.length <= 0:
                raise ValueError("length muss positiv sein")
            if self.minimum is not None and self.minimum <= 0:
                raise ValueError("minimum muss positiv sein")
            if self.maximum is not None and self.maximum <= 0:
                raise ValueError("maximum muss positiv sein")
            if (
                self.minimum is not None
                and self.maximum is not None
                and self.minimum > self.maximum
            ):
                raise ValueError("minimum darf nicht groesser als maximum sein")
            if self.flex <= 0:
                raise ValueError("flex muss positiv sein")
        else:
            if self.length is None or self.length <= 0:
                raise ValueError("FixPath benoetigt eine positive length")
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("FixPath hat kein minimum/maximum")


def FlexPath(
    preferred: Optional[float] = None,
    min_length: Optional[float] = None,
    max_length: Optional[float] = None,
    flex: float = 1.0,
    group: Optional[str] = None,
) -> Path:
    """Elastisches Beinstueck. Ohne Angabe wird `preferred` zum normalen
    Stationsabstand der Linie (line.spacing); `min_length`/`max_length`
    fallen ohne Angabe auf die Config-Vorgabe bzw. unbeschraenkt zurueck."""
    return Path(
        length=preferred,
        minimum=min_length,
        maximum=max_length,
        flexibility=True,
        flex=flex,
        group=group,
    )


def FixPath(length: float) -> Path:
    """Starres Beinstueck mit einer explizit angegebenen Laenge (statt dem
    sonst verwendeten spacing bzw. Turn-radius)."""
    return Path(length=length, flexibility=False)


Step = Union[str, Turn, Path]


@dataclass(frozen=True)
class Corridor:
    """Ein von Hand festgelegter Streckenabschnitt mit fester Spurlage.

    steps    Stationsfolge -- wie die Strecken-Konstanten, Turn()/FlexPath()
             darin werden ignoriert, es zaehlen nur die Stationen.
    offsets  Linien-ID oder Familie -> Versatz in Slot-Einheiten. Die
             Linien-ID gewinnt, eine Familie gilt also als Vorgabe fuer alle
             ihre Linien, die keinen eigenen Eintrag haben.
             also Vielfache von StyleConfig.bundle_spacing. Gilt fuer JEDE
             Kante des Korridors -- auch dort, wo eine Linie allein faehrt.

    Das Vorzeichen bezieht sich auf die Richtung, in der `steps` geschrieben
    ist. Die Zahlen bleiben dadurch stabil, egal wie die einzelne Kante
    intern gespeichert ist.

    Eine Linie, die den Korridor befaehrt, aber weder ueber ihre Familie
    noch ueber ihre ID in `offsets` steht, ist ein Fehler -- sonst rutscht
    eine neu hinzugefuegte Linie unbemerkt an den Rand.
    """
    steps: Sequence[Step]
    offsets: Mapping[str, float]


@dataclass(frozen=True)
class Station:
    """Eine Station im Netz.

    id      eindeutiger Bezeichner, in Strecken und Linien verwendet
            (z.B. "gesundbrunnen"). Nie am Nutzer sichtbar.
    name    Anzeigename (z.B. "Gesundbrunnen").
    label   Text fuer die Karte. Standard: identisch mit `name` -- nur bei
            Bedarf abweichend definieren, z.B. fuer einen Zeilenumbruch:
            label="Berlin\\nHauptbahnhof".
    kind    "station" oder "hub" -- Hubs bekommen in der zweiten
            Rendering-Schicht ein weisses "Pill" quer zum Linienbuendel.
    label_pos
            Lage der Beschriftung, falls die automatische Regel nicht passt:
            "top", "bottom", "left", "right" sowie die vier Diagonalen
            "top_left", "top_right", "bottom_left", "bottom_right".
            None = automatisch aus der Trassenrichtung.
    """
    id: str
    name: str
    label: Optional[str] = None
    kind: str = "station"
    label_pos: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("id darf nicht leer sein")
        if not self.name.strip():
            raise ValueError("name darf nicht leer sein")
        if self.kind not in ("station", "hub"):
            raise ValueError("kind muss 'station' oder 'hub' sein")
        if self.label_pos is not None and self.label_pos not in LABEL_RICHTUNG:
            raise ValueError(
                f"label_pos '{self.label_pos}' unbekannt -- erlaubt: "
                + ", ".join(sorted(LABEL_RICHTUNG))
            )
        if self.label is None:
            object.__setattr__(self, "label", self.name)


def _station_registry(stations: Iterable[Station]) -> Dict[str, Station]:
    registry: Dict[str, Station] = {}
    for station in stations:
        if station.id in registry:
            raise ValueError(f"Station-ID '{station.id}' ist doppelt vergeben")
        registry[station.id] = station
    return registry


@dataclass
class TurnLine:
    """Eine Linie in der Definitionssprache.

    color       fuer die spaetere zweite Rendering-Schicht; die Gleiskarte
                zeichnet noch einheitlich graue Korridore.
    steps       Stationen, Turn() und FlexPath() in Fahrtreihenfolge.
    start       absoluter Startwinkel in Grad (0 = Nord, im Uhrzeigersinn).
                None = aus den gemeinsamen Korridoren herleiten.
    anchor      feste Startkoordinate; None = Solver waehlt sie.
    spacing     Standardabstand zwischen zwei Stationen.
    direction   reines Label fuer den Report, ohne Wirkung auf die Geometrie.
    closed      letzte Station == erste Station -> Ring schliessen.
    family      Linienfamilie: Varianten derselben Linie (S2/S25/S26,
                S1/S15, S8/S85, S46/S47) teilen sich EINEN Platz im
                Buendel, laufen also auf gemeinsamen Abschnitten
                uebereinander statt nebeneinander. Ueblicherweise die ID
                der Stammlinie; None = die Linie bildet ihre eigene
                Familie.
    """
    color: str
    steps: List[Step]
    start: Optional[int] = None
    anchor: Optional[Pt] = None
    spacing: float = 1.2
    direction: str = ""
    closed: bool = False
    family: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("Eine Linie benoetigt mindestens zwei Stationen")
        if self.start is not None and self.start % 45 != 0:
            raise ValueError("start muss ein Vielfaches von 45 Grad sein")
        if self.spacing <= 0:
            raise ValueError("spacing muss positiv sein")


# ============================================================================
# INTERNES MODELL
# ============================================================================

@dataclass(frozen=True)
class LengthSpec:
    preferred: float
    elastic: bool
    group: Optional[str]
    min_gap: float
    max_gap: float
    flex: float


@dataclass(frozen=True)
class Corner:
    """Ein Knick zwischen zwei Beinen, mit fertig aufgeloestem Kreisradius.

    Der Radius ist Geometrie, nicht Optik: ueber seine Tangente
    t = R * tan(delta/2) belegt der Bogen Platz auf beiden Nachbarbeinen.
    Deshalb steht er schon beim Parsen fest und geht in Phase 3 als untere
    Laengenschranke in den Solver ein.

    delta   Drehwinkel des Turns.
    radius  KREISRADIUS -- explizites Turn(radius=...) oder, falls dort
            nichts steht, NetzConfig.curve_radius(delta).
    erzwungen
            True bei explizitem Turn(radius=...). Solche Radien werden
            NICHT gedeckelt: nur so laesst sich eine Kurve bewusst so weit
            aufziehen, dass das gerade Stueck dazwischen verschwindet.
    offset  seitlicher Versatz der Linie an diesem Knick (0 = Trassenmitte),
            in Gitter-Einheiten. Eine gebuendelte Linie faehrt den Bogen
            innen enger und aussen weiter -- sonst laufen die Linien in der
            Kurve nicht mehr parallel.
    max_tangent
            obere Schranke fuer die TANGENTENLAENGE (nicht den Radius), denn
            begrenzend ist der Platz auf den Beinen. Aus den Beinlaengen der
            MITTELLINIE (freier Rest des kuerzeren Nachbarbeins, also
            abzueglich dessen, was der Knick am anderen Ende schon
            belegt). Muss aus der
            Mittellinie kommen und fuer alle Spuren gleich sein: die
            versetzten Spuren haben durch die Gehrung unterschiedlich lange
            Beine, ein je Spur eigener Deckel wuerde die Boegen wieder
            unkonzentrisch machen.
    """
    delta: int
    radius: Optional[float] = None
    offset: float = 0.0
    max_tangent: Optional[float] = None
    erzwungen: bool = False

    def tangente(self) -> float:
        """Platzbedarf des Bogens auf JEDEM der beiden Nachbarbeine."""
        return 0.0 if self.radius is None else self.radius * _corner_tangent_factor(self.delta)


@dataclass(frozen=True)
class LegSpec:
    bearing_offset: int   # relativ zum Startwinkel der Linie
    length: LengthSpec
    corner: Optional[Corner] = None  # Knick VOR diesem Bein; None = kein Knick


# Form eines Segments, unabhaengig von seinem absoluten Startwinkel:
# je Bein (Richtung relativ zum ersten Bein, elastisch, preferred, min_gap, max_gap, flex)
ShapeSignature = Tuple[Tuple[int, bool, float, float, float, float], ...]


@dataclass(frozen=True)
class SegmentSpec:
    line_id: str
    index: int
    a: str
    b: str
    legs: Tuple[LegSpec, ...]


@dataclass
class ParsedLine:
    line_id: str
    stations: List[str]
    segments: List[SegmentSpec]
    closed: bool = False   # Ringlinie: hat weder Anfangs- noch Endstation


@dataclass
class LengthVariable:
    key: Tuple[str, str]
    preferred: float
    min_gap: float
    max_gap: float
    flex: float
    occurrences: List[Tuple[str, int, int]] = field(default_factory=list)


@dataclass
class Network:
    """Ergebnis von Phase 2 (Netz): welche Stationen, welche Kanten, welche
    Form -- und in welcher absoluten Richtung jede Linie startet. Noch ohne
    jede Koordinate."""
    parsed: Dict[str, ParsedLine]
    starts: Dict[str, int]        # Linie -> absoluter Startwinkel in Grad


@dataclass
class Measures:
    """Ergebnis von Phase 3 (Masse): die geloesten Zahlen. Hier bekommen die
    FlexPaths ihre Laenge und die Stationen ihre Koordinaten."""
    coords: Dict[str, Pt]
    leg_lengths: Dict[Tuple[str, int, int], float]  # (Linie, Segment, Bein) -> Laenge


@dataclass
class Tracks:
    """Ergebnis von Phase 4 (Gleise): fertige Polylinien je physischem
    Streckenabschnitt, pro Stationspaar genau einmal (gemeinsame Korridore
    sind dedupliziert)."""
    corridor_paths: Dict[frozenset[str], List[Pt]]
    corridor_corners: Dict[frozenset[str], List[Optional[Corner]]]  # je Punkt; Enden immer None


@dataclass
class LayoutResult:
    """Die Ergebnisse der Phasen 2-4 zusammen -- das, was Phase 5 zum
    Zeichnen braucht. Die Teile sind einzeln erzeugbar (`build_network`,
    `solve_measures`, `build_tracks`); `solve_layout` verkettet sie nur."""
    network: Network
    measures: Measures
    tracks: Tracks


@dataclass
class LinePath:
    """Der durchgehende Streckenzug einer Linie in Fahrtreihenfolge,
    inklusive Buendel-Versatz. `corners[i]` gehoert zu `points[i]`.

    `stations` haelt zusaetzlich fest, wo jede Station AUF DIESER LINIE
    liegt -- also mit ihrem Versatz, nicht auf der Trassenmitte. Damit
    bekommt jede Spur eines Buendels ihren eigenen Stationspunkt.
    """
    points: List[Pt]
    corners: List[Optional[Corner]]
    stations: Dict[str, Pt] = field(default_factory=dict)


@dataclass
class LineLayout:
    """Ergebnis von Phase 4b (Linienfuehrung).

    paths  je betrachteter Linie ihr fertiger Streckenzug
    slots  je Korridorkante und Linie der vergebene Slot (0 = Mitte);
           dient der Nachvollziehbarkeit und zum Testen der Buendelung
    """
    paths: Dict[str, LinePath]
    slots: Dict[frozenset[str], Dict[str, float]]
    families: Dict[str, str]   # Linie -> Familie (Linien ohne Familie: sich selbst)


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================

def _pair_key(a: str, b: str) -> frozenset[str]:
    return frozenset((a, b))


def _norm(v: Pt) -> Pt:
    l = hypot(v[0], v[1])
    if l < 1e-12:
        return (0.0, 0.0)
    return (v[0] / l, v[1] / l)


def _fixed_length(value: float, min_gap: Optional[float] = None) -> LengthSpec:
    """Starres Beinstueck mit fester Laenge (Default oder ueber FixPath()).

    `min_gap`/`max_gap` sind fuer starre Beine keine echte Schranke (der
    Solver bindet nur elastische Laengen), gehen aber in die Korridor-
    Signatur ein -- zwei Linien ueber derselben Kante muessen hier denselben
    Wert haben. Deshalb wird `min_gap` explizit mitgefuehrt statt aus `value`
    abgeleitet; `max_gap` folgt immer `value`.
    """
    return LengthSpec(
        preferred=value,
        elastic=False,
        group=None,
        min_gap=value if min_gap is None else min_gap,
        max_gap=value,
        flex=1.0,
    )


def _flex_length(path: Path, spacing: float, netz: NetzConfig) -> LengthSpec:
    """Elastisches Beinstueck aus einem FlexPath(); offene Felder fallen auf
    den Stationsabstand der Linie bzw. die Config-Vorgabe zurueck. Ohne
    `max_length` ist die Laenge nach oben unbeschraenkt."""
    return LengthSpec(
        preferred=spacing if path.length is None else path.length,
        elastic=True,
        group=path.group,
        min_gap=netz.min_gap_default if path.minimum is None else path.minimum,
        max_gap=float("inf") if path.maximum is None else path.maximum,
        flex=path.flex,
    )


def _build_legs_from_modifiers(
    modifiers: Sequence[Union[Turn, Path]],
    start_offset: int,
    spacing: float,
    netz: NetzConfig,
    line_id: str,
    a_name: str,
    b_name: str,
) -> Tuple[Tuple[LegSpec, ...], int]:
    """Uebersetzt eine Folge aus Turn()/FlexPath()/FixPath() (in Reihenfolge,
    wie sie zwischen zwei Stationen in `steps` stehen) in konkrete Legs.

    Regel: jeder Turn schliesst das Beinstueck DAVOR ab (Standardlaenge =
    dieses Turns eigener radius, oder line.spacing/2 falls radius=None). Ein
    FlexPath()/FixPath() markiert das gerade offene (noch nicht
    abgeschlossene) Beinstueck als elastisch bzw. mit expliziter starrer
    Laenge -- das ueberschreibt die radius/spacing-Vorgabe fuer genau dieses
    eine Stueck. Das letzte Beinstueck (nach dem letzten Turn, oder das
    einzige Stueck falls gar kein Turn vorkommt) wird beim Erreichen der
    naechsten Station abgeschlossen; sein Default ist der radius des
    letzten Turns, oder line.spacing, falls kein Turn vorkam.
    """
    legs: List[LegSpec] = []
    offset = start_offset
    pending_path: Optional[Path] = None
    pending_corner: Optional[Corner] = None
    last_turn_radius: Optional[float] = None

    def close(bearing_offset: int, default_len: float, corner: Optional[Corner]) -> None:
        nonlocal pending_path
        if bearing_offset not in COMPASS:
            raise GeometryError(
                f"{line_id}: Segment {a_name} -> {b_name} fuehrt auf "
                f"ungueltige Richtung {bearing_offset} Grad"
            )
        if pending_path is not None:
            if pending_path.flexibility:
                length = _flex_length(pending_path, spacing, netz)
            else:
                length = _fixed_length(pending_path.length)
            pending_path = None
        else:
            length = _fixed_length(default_len)
        legs.append(LegSpec(bearing_offset=bearing_offset, length=length, corner=corner))

    for mod in modifiers:
        if isinstance(mod, Path):
            if pending_path is not None:
                raise GeometryError(
                    f"{line_id}: zwei FlexPath()/FixPath() direkt hintereinander "
                    f"zwischen {a_name} und {b_name} -- pro Beinstueck nur eins"
                )
            pending_path = mod
            continue
        # Turn.radius ist ein KREISRADIUS; die Beinlaenge daneben ist die
        # zugehoerige Tangentenlaenge. Dadurch fuellen bei zwei
        # aufeinanderfolgenden Turns die beiden Tangenten das Stueck
        # dazwischen genau aus -- kein gerades Reststueck.
        radius = (
            spacing / 2
            if mod.radius is None
            else mod.radius * tan(radians(abs(mod.delta)) / 2)
        )
        close(offset, radius, pending_corner)
        offset = (offset + mod.delta) % 360
        last_turn_radius = radius
        # Radius sofort aufloesen: er bestimmt ueber die Tangente, wie viel
        # Platz der Bogen auf seinen Nachbarbeinen braucht, und geht deshalb
        # noch in dieser Phase als Laengenschranke in den Solver ein.
        pending_corner = Corner(
            delta=mod.delta,
            radius=netz.curve_radius(mod.delta) if mod.radius is None else mod.radius,
            erzwungen=mod.radius is not None,
        )

    trailing_default = spacing if last_turn_radius is None else last_turn_radius
    close(offset, trailing_default, pending_corner)
    return _mit_kurvenplatz(tuple(legs)), offset


def _mit_kurvenplatz(legs: Tuple[LegSpec, ...]) -> Tuple[LegSpec, ...]:
    """Zieht die untere Laengenschranke jedes elastischen Beins so weit hoch,
    dass die Boegen an seinen beiden Enden darauf Platz haben.

    Ein Bogen verbraucht an jedem seiner Nachbarbeine seine Tangentenlaenge
    t = R * tan(delta/2). Ein Bein zwischen zwei Knicken braucht also die
    Summe beider Tangenten, sonst muesste beim Rendern einer der Radien
    gedeckelt werden. Starre Beine (FixPath) bleiben unangetastet -- dort
    ist die Laenge eine Ansage; passt der Radius nicht, greift beim Rendern
    weiterhin der Deckel.
    """
    neu: List[LegSpec] = []
    for i, leg in enumerate(legs):
        vorne = leg.corner.tangente() if leg.corner else 0.0
        nach = legs[i + 1].corner if i + 1 < len(legs) else None
        bedarf = vorne + (nach.tangente() if nach else 0.0)
        if bedarf > leg.length.min_gap and leg.length.elastic:
            neu.append(
                replace(
                    leg,
                    length=replace(
                        leg.length,
                        min_gap=bedarf,
                        preferred=max(leg.length.preferred, bedarf),
                        max_gap=max(leg.length.max_gap, bedarf),
                    ),
                )
            )
        else:
            neu.append(leg)
    return tuple(neu)


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def auto_label(direction: Pt, clearance: float) -> Tuple[float, float, str]:
    ux, uy = _norm(direction) if direction != (0.0, 0.0) else (1.0, 0.0)
    # waagerecht -> Label unten
    if abs(uy) < 0.35:
        return (0.0, clearance + 7.0, "middle")
    # senkrecht -> Label links
    if abs(ux) < 0.35:
        return (-clearance, 3.0, "end")
    # diagonal -> unter die Linie, also bottom_left bzw. bottom_right
    nx, ny = -uy, ux
    if ny < 0:
        nx, ny = -nx, -ny
    dx = nx * clearance
    anchor = "start" if dx >= 0 else "end"
    return (dx, ny * clearance + 7.0, anchor)


def _segment_trifft_box(
    a: Pt, b: Pt, box: Tuple[float, float, float, float], rand: float
) -> bool:
    """Schneidet die Strecke a-b das (um `rand` aufgeweitete) Rechteck?
    Liang-Barsky-Clipping."""
    x0, y0, x1, y1 = box
    x0 -= rand
    y0 -= rand
    x1 += rand
    y1 += rand
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(pk) < 1e-12:
            if qk < 0:
                return False
        else:
            r = qk / pk
            if pk < 0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                if r < t0:
                    return False
                t1 = min(t1, r)
    return t0 <= t1


def ist_zusatz(zeile: str) -> bool:
    """Eine geklammerte Zeile ist ein Namenszusatz (z.B. "(Eichkamp)")."""
    return zeile.startswith("(")


def zeilen_font(zeile: str, style: "StyleConfig") -> float:
    """Schriftgroesse einer einzelnen Beschriftungszeile."""
    if ist_zusatz(zeile):
        return style.label_font - style.label_zusatz_kleiner
    return style.label_font


def label_breite(text: str, font: float, zusatz_kleiner: float = 0.0) -> float:
    """Grobe Textbreite in Pixeln -- reicht fuer die Kollisionspruefung.

    Der Faktor ist die mittlere Zeichenbreite der Beschriftungsschrift,
    gemessen an Helvetica Neue Bold (0.50 bis 0.60 je nach Wortbild).
    """
    return max(
        len(z) * (font - zusatz_kleiner if ist_zusatz(z) else font)
        for z in text.split("\n")
    ) * 0.55


def label_offset(pos: str, clearance: float) -> Tuple[float, float, str]:
    """(dx, dy, text-anchor) fuer eine der acht Label-Lagen.

    `dy` beruecksichtigt, dass die y-Koordinate eines <text> auf der
    Grundlinie sitzt: unterhalb liegende Beschriftungen brauchen den vollen
    Zeilenabstand, oberhalb liegende nur einen kleinen Ausgleich.
    """
    nx, ny = LABEL_RICHTUNG[pos]
    dx = nx * clearance
    if ny < -0.3:
        dy = ny * clearance - 1.0
    elif ny > 0.3:
        dy = ny * clearance + 7.0
    else:
        dy = 3.0
    if abs(nx) < 0.3:
        anchor = "middle"
    else:
        anchor = "start" if nx > 0 else "end"
    return dx, dy, anchor


def mehrzeilen_versatz(ax: float, ay: float, versatz: float) -> Tuple[float, float]:
    """Zusatzverschiebung (dx, dy) fuer einen mehrzeiligen Beschriftungsblock.

    `versatz` ist der Abstand zwischen erster und letzter Grundlinie; der
    Block waechst also um diesen Betrag nach unten.

    Seitliche Lagen (left/right und die vier Schraegen) werden mittig zur
    Station gesetzt: halbe Blockhoehe hoch. Bei den Schraegen kommt derselbe
    Betrag waagerecht dazu, und zwar nach AUSSEN -- dann laeuft die
    Gesamtverschiebung parallel zur Trasse und der Abstand zur Linie bleibt
    exakt der einer einzeiligen Beschriftung. Ein reines `top` muss dagegen
    um die ganze Blockhoehe hoch, sonst waechst es in die Linien.
    """
    if versatz <= 0.0:
        return 0.0, 0.0
    if abs(ax) > 0.3:
        ddx = 0.0
        if abs(ay) > 0.3:
            ddx = versatz / 2 if ax > 0 else -versatz / 2
        return ddx, -versatz / 2
    if ay < -0.3:
        return 0.0, -versatz
    return 0.0, 0.0


def label_aussen(direction: Pt) -> Pt:
    """Einheitsvektor, in den auto_label() die Beschriftung schiebt.

    Spiegelt dieselben Faelle wider und dient dazu, die Beschriftung bei
    gebuendelten Stationen zusaetzlich um die Buendelbreite nach aussen zu
    ruecken, statt sie an der Trassenmitte zu lassen.
    """
    ux, uy = _norm(direction) if direction != (0.0, 0.0) else (1.0, 0.0)
    if abs(uy) < 0.35:
        return (0.0, 1.0)      # waagerechte Trasse -> Label unten
    if abs(ux) < 0.35:
        return (-1.0, 0.0)     # senkrechte Trasse  -> Label links
    nx, ny = -uy, ux
    if ny < 0:
        nx, ny = -nx, -ny      # Diagonalen: Beschriftung unterhalb
    return (nx, ny)


def polyline_path_d(points: Sequence[Pt]) -> str:
    return "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in points)


def rounded_path_d(points: Sequence[Pt], radii: Sequence[Optional[float]]) -> str:
    """SVG-Pfad durch `points`, an inneren Punkten mit radii[i] > 0 durch
    einen KREISBOGEN abgerundet (radii[0]/radii[-1] werden ignoriert -- das
    sind Stationen, keine Knicke).

    `radii[i]` ist der KREISRADIUS des Bogens. Die Tangentenlaenge -- der
    Abstand vom Knick, an dem die Rundung beginnt -- folgt daraus als
    t = R * tan(delta/2).

    Kreisboegen und keine Bezierkurven, weil nur bei ihnen die Parallelkurve
    wieder exakt ein Kreisbogen ist (konzentrisch, R -/+ Versatz) -- eine
    versetzte Bezier haette ueber den Bogen einen schwankenden Abstand."""
    if len(points) < 3:
        return polyline_path_d(points)

    parts = [f"M {points[0][0]:.3f} {points[0][1]:.3f}"]
    for i in range(1, len(points) - 1):
        radius = radii[i]
        prev_pt, cur_pt, next_pt = points[i - 1], points[i], points[i + 1]
        d_in = hypot(cur_pt[0] - prev_pt[0], cur_pt[1] - prev_pt[1])
        d_out = hypot(next_pt[0] - cur_pt[0], next_pt[1] - cur_pt[1])
        if not radius or d_in < 1e-9 or d_out < 1e-9:
            parts.append(f"L {cur_pt[0]:.3f} {cur_pt[1]:.3f}")
            continue

        ux_in, uy_in = (cur_pt[0] - prev_pt[0]) / d_in, (cur_pt[1] - prev_pt[1]) / d_in
        ux_out, uy_out = (next_pt[0] - cur_pt[0]) / d_out, (next_pt[1] - cur_pt[1]) / d_out

        # Richtungsaenderung am Knick, vorzeichenbehaftet
        cross = ux_in * uy_out - uy_in * ux_out
        dot = ux_in * ux_out + uy_in * uy_out
        delta = atan2(cross, dot)
        if abs(delta) < 1e-9 or abs(abs(delta) - pi) < 1e-9:
            # geradeaus oder Kehre -- nichts zu runden
            parts.append(f"L {cur_pt[0]:.3f} {cur_pt[1]:.3f}")
            continue

        # Tangentenlaenge aus dem Radius. Der eigentliche Deckel sitzt in
        # _corner_radius und rechnet mit der Mittellinie, damit gebuendelte
        # Spuren konzentrisch bleiben. Hier wird nur noch nachgezogen, wenn
        # die Tangente auf den TATSAECHLICHEN (versetzten) Beinen nicht
        # unterkommt -- das passiert, wo ein Versatzwechsel den Eckpunkt so
        # weit verschiebt, dass kaum Bein uebrig bleibt. Ein kleinerer Bogen
        # ist dort besser als eine scharfe Ecke.
        half = tan(abs(delta) / 2)
        t = radius * half
        fit = min(d_in, d_out)
        if t > fit:
            t = fit
        if t < 1e-9:
            parts.append(f"L {cur_pt[0]:.3f} {cur_pt[1]:.3f}")
            continue
        arc_r = t / half
        p_in = (cur_pt[0] - ux_in * t, cur_pt[1] - uy_in * t)
        p_out = (cur_pt[0] + ux_out * t, cur_pt[1] + uy_out * t)
        # y zeigt nach unten: positives Kreuzprodukt = Rechtsbogen
        sweep = 1 if cross > 0 else 0
        parts.append(f"L {p_in[0]:.3f} {p_in[1]:.3f}")
        parts.append(
            f"A {arc_r:.3f} {arc_r:.3f} 0 0 {sweep} "
            f"{p_out[0]:.3f} {p_out[1]:.3f}"
        )

    parts.append(f"L {points[-1][0]:.3f} {points[-1][1]:.3f}")
    return " ".join(parts)


def rounded_points(
    points: Sequence[Pt], radii: Sequence[Optional[float]], schritte: int = 8
) -> List[Pt]:
    """Derselbe Streckenzug wie `rounded_path_d`, aber als Polygonzug -- die
    Kreisboegen sind in `schritte` Sehnen zerlegt.

    Fuer geometrische Auswertungen am TATSAECHLICH gezeichneten Verlauf
    (z.B. Kreuzungspunkte). Der reine Stuetzpunktzug liegt in den Kurven
    daneben, weil er den Knick aussen umfaehrt statt ihn zu runden.
    """
    if len(points) < 3:
        return list(points)

    out: List[Pt] = [points[0]]
    for i in range(1, len(points) - 1):
        radius = radii[i]
        prev_pt, cur_pt, next_pt = points[i - 1], points[i], points[i + 1]
        d_in = hypot(cur_pt[0] - prev_pt[0], cur_pt[1] - prev_pt[1])
        d_out = hypot(next_pt[0] - cur_pt[0], next_pt[1] - cur_pt[1])
        if not radius or d_in < 1e-9 or d_out < 1e-9:
            out.append(cur_pt)
            continue
        ux_in, uy_in = (cur_pt[0] - prev_pt[0]) / d_in, (cur_pt[1] - prev_pt[1]) / d_in
        ux_out, uy_out = (next_pt[0] - cur_pt[0]) / d_out, (next_pt[1] - cur_pt[1]) / d_out
        cross = ux_in * uy_out - uy_in * ux_out
        dot = ux_in * ux_out + uy_in * uy_out
        delta = atan2(cross, dot)
        if abs(delta) < 1e-9 or abs(abs(delta) - pi) < 1e-9:
            out.append(cur_pt)
            continue
        half = tan(abs(delta) / 2)
        t = min(radius * half, d_in, d_out)
        if t < 1e-9:
            out.append(cur_pt)
            continue
        arc_r = t / half
        p_in = (cur_pt[0] - ux_in * t, cur_pt[1] - uy_in * t)
        p_out = (cur_pt[0] + ux_out * t, cur_pt[1] + uy_out * t)
        # Mittelpunkt liegt senkrecht zur Einfahrt, auf der Kurveninnenseite.
        # y zeigt nach unten, deshalb ist (-uy, ux) "rechts der Fahrtrichtung".
        vz = 1.0 if cross > 0 else -1.0
        mx = p_in[0] + vz * arc_r * -uy_in
        my = p_in[1] + vz * arc_r * ux_in
        a0 = atan2(p_in[1] - my, p_in[0] - mx)
        out.append(p_in)
        for k in range(1, schritte):
            a = a0 + delta * k / schritte
            out.append((mx + arc_r * cos(a), my + arc_r * sin(a)))
        out.append(p_out)

    out.append(points[-1])
    return out


# ============================================================================
# PHASE 2a: PARSEN
# ============================================================================

def parse_line(
    line_id: str,
    line: TurnLine,
    netz: NetzConfig,
    station_registry: Mapping[str, Station],
) -> ParsedLine:
    stations: List[str] = []
    segments: List[SegmentSpec] = []

    current_station: Optional[str] = None
    current_bearing_offset = 0
    pending_modifiers: List[Union[Turn, Path]] = []

    for raw in line.steps:
        if isinstance(raw, (Turn, Path)):
            if current_station is None:
                raise GeometryError(
                    f"{line_id}: {type(raw).__name__} darf erst nach der "
                    "ersten Station kommen"
                )
            pending_modifiers.append(raw)
            continue

        if not isinstance(raw, str):
            raise TypeError(f"{line_id}: Unbekannter Schritt {raw!r}")

        if raw not in station_registry:
            raise GeometryError(f"{line_id}: Unbekannte Station-ID '{raw}'")

        # Ring schliessen: bei closed=True darf die LETZTE Station exakt die
        # erste sein -- das erzeugt die schliessende Segmentkante, ohne sie
        # als neue (doppelte) Station zu fuehren.
        closing = (
            line.closed and stations and raw == stations[0]
            and current_station != stations[0]
        )
        if raw in stations and not closing:
            raise GeometryError(f"{line_id}: Station '{raw}' kommt mehrfach vor")

        if current_station is None:
            current_station = raw
            stations.append(raw)
            pending_modifiers = []
            continue

        if pending_modifiers:
            leg_specs, current_bearing_offset = _build_legs_from_modifiers(
                pending_modifiers, current_bearing_offset, line.spacing, netz,
                line_id, current_station, raw,
            )
        else:
            if current_bearing_offset not in COMPASS:
                raise GeometryError(
                    f"{line_id}: Segment {current_station} -> {raw} hat "
                    f"ungueltige Richtung {current_bearing_offset}"
                )
            leg_specs = (
                LegSpec(
                    bearing_offset=current_bearing_offset,
                    length=_fixed_length(line.spacing, min_gap=netz.min_gap_default),
                ),
            )

        segments.append(
            SegmentSpec(
                line_id=line_id,
                index=len(segments),
                a=current_station,
                b=raw,
                legs=leg_specs,
            )
        )

        current_station = raw
        if not closing:
            stations.append(raw)
        pending_modifiers = []
        if closing:
            break

    if len(stations) < 2:
        raise GeometryError(f"{line_id}: Eine Linie braucht mindestens zwei Stationen")

    return ParsedLine(
        closed=line.closed,
        line_id=line_id,
        stations=stations,
        segments=segments,
    )


# ============================================================================
# PHASE 2b: STARTWINKEL BESTIMMEN
# ============================================================================

def _segment_shape_signature(seg: SegmentSpec) -> ShapeSignature:
    """
    Signatur eines Segments relativ zum ersten Bein.
    Wird verwendet, um gemeinsame Korridore auf Konsistenz zu pruefen.
    """
    first = seg.legs[0].bearing_offset
    signature = []
    for leg in seg.legs:
        rel = (leg.bearing_offset - first) % 360
        signature.append(
            (
                rel,
                leg.length.elastic,
                round(leg.length.preferred, 8),
                round(leg.length.min_gap, 8),
                round(leg.length.max_gap, 8),
                round(leg.length.flex, 8),
            )
        )
    return tuple(signature)


def _reversed_shape_signature(seg: SegmentSpec) -> ShapeSignature:
    """Signatur desselben Segments, als wuerde man es in umgekehrter
    Stationsrichtung (b -> a) durchlaufen -- Beinreihenfolge gedreht und
    jede Richtung um 180 Grad gespiegelt."""
    legs = list(reversed(seg.legs))
    if not legs:
        return ()
    first = (legs[0].bearing_offset + 180) % 360
    signature = []
    for leg in legs:
        b = (leg.bearing_offset + 180) % 360
        rel = (b - first) % 360
        signature.append(
            (
                rel,
                leg.length.elastic,
                round(leg.length.preferred, 8),
                round(leg.length.min_gap, 8),
                round(leg.length.max_gap, 8),
                round(leg.length.flex, 8),
            )
        )
    return tuple(signature)


def infer_start_bearings(
    lines: Mapping[str, TurnLine],
    parsed: Mapping[str, ParsedLine],
    netz: NetzConfig,
) -> Dict[str, int]:
    """Bestimmt fehlende Startwinkel aus gemeinsamen Korridoren.

    Jede von zwei Linien befahrene Kante liefert eine feste Winkeldifferenz
    zwischen deren Startwinkeln. Aus explizit gesetzten `start`-Werten wird
    diese Beziehung durch das Netz propagiert; Linien ohne jede Verbindung
    fallen auf netz.default_start zurueck. Widersprueche und Kanten, deren
    Form in beiden Linien nicht uebereinstimmt, werden gemeldet.

    Die Fahrtrichtung darf sich unterscheiden -- rueckwaerts durchlaufene
    Kanten werden spiegelbildlich verglichen.
    """
    occurrences: Dict[frozenset[str], List[SegmentSpec]] = defaultdict(list)
    for pline in parsed.values():
        for seg in pline.segments:
            occurrences[_pair_key(seg.a, seg.b)].append(seg)

    graph: Dict[str, List[Tuple[str, int]]] = defaultdict(list)

    for pair, group in occurrences.items():
        if len(group) < 2:
            continue

        base = group[0]
        base_sig = _segment_shape_signature(base)

        for other in group[1:]:
            if base.a == other.a and base.b == other.b:
                reversed_edge = False
            elif base.a == other.b and base.b == other.a:
                reversed_edge = True
            else:
                # kann bei korrekter frozenset-Gruppierung nicht auftreten
                raise GeometryError(
                    f"Gemeinsamer Korridor {sorted(pair)}: unerwartete Stationspaare "
                    f"({base.a}->{base.b}) vs ({other.a}->{other.b})"
                )

            if not reversed_edge:
                if _segment_shape_signature(other) != base_sig:
                    raise GeometryError(
                        f"Gemeinsamer Korridor {base.a} -> {base.b} hat in "
                        f"{base.line_id} und {other.line_id} unterschiedliche Geometrie."
                    )
                delta = (base.legs[0].bearing_offset - other.legs[0].bearing_offset) % 360
            else:
                # other durchlaeuft denselben physischen Abschnitt rueckwaerts
                # (b -> a) -- Spiegelbild-Vergleich statt exaktem Abgleich.
                if _segment_shape_signature(other) != _reversed_shape_signature(base):
                    raise GeometryError(
                        f"Gemeinsamer Korridor {base.a} <-> {base.b} hat in "
                        f"{base.line_id} und {other.line_id} (rueckwaerts) "
                        "unterschiedliche Geometrie."
                    )
                delta = (base.legs[-1].bearing_offset - other.legs[0].bearing_offset + 180) % 360

            graph[base.line_id].append((other.line_id, delta))
            graph[other.line_id].append((base.line_id, (-delta) % 360))

    starts: Dict[str, int] = {}
    queue: deque[str] = deque()

    for lid, line in lines.items():
        if line.start is not None:
            starts[lid] = line.start % 360
            queue.append(lid)

    def propagate() -> None:
        while queue:
            lid = queue.popleft()
            for other, delta in graph.get(lid, []):
                candidate = (starts[lid] + delta) % 360
                if other in starts:
                    if starts[other] != candidate:
                        raise GeometryError(
                            f"Widerspruechliche Startwinkel fuer {other}: "
                            f"{starts[other]} Grad vs. {candidate} Grad"
                        )
                else:
                    starts[other] = candidate
                    queue.append(other)

    propagate()

    for lid in lines:
        if lid not in starts:
            starts[lid] = netz.default_start
            queue.append(lid)
            propagate()

    for lid, start in starts.items():
        if start not in COMPASS:
            raise GeometryError(
                f"{lid}: geloester Startwinkel {start} ist kein Vielfaches von 45 Grad"
            )

    return starts


# ============================================================================
# PHASE 3: LAENGENVARIABLEN
# ============================================================================

def _collect_length_variables(
    parsed: Mapping[str, ParsedLine],
) -> Tuple[
    Dict[Tuple[str, int, int], Tuple[str, str]],
    Dict[Tuple[str, str], LengthVariable],
]:
    leg_to_var: Dict[Tuple[str, int, int], Tuple[str, str]] = {}
    variables: Dict[Tuple[str, str], LengthVariable] = {}

    for lid, pline in parsed.items():
        for seg in pline.segments:
            for leg_index, leg in enumerate(seg.legs):
                spec = leg.length
                if not spec.elastic:
                    continue

                local_group = spec.group or f"__seg{seg.index}_leg{leg_index}"
                key = (lid, local_group)
                leg_to_var[(lid, seg.index, leg_index)] = key

                if key not in variables:
                    variables[key] = LengthVariable(
                        key=key,
                        preferred=spec.preferred,
                        min_gap=spec.min_gap,
                        max_gap=spec.max_gap,
                        flex=spec.flex,
                        occurrences=[(lid, seg.index, leg_index)],
                    )
                else:
                    var = variables[key]
                    for attr in ("preferred", "min_gap", "max_gap", "flex"):
                        if abs(getattr(var, attr) - getattr(spec, attr)) > 1e-9:
                            raise GeometryError(
                                f"{lid}: Gruppe '{local_group}' verwendet "
                                f"unterschiedliche {attr}-Werte"
                            )
                    var.occurrences.append((lid, seg.index, leg_index))

    return leg_to_var, variables


# ============================================================================
# PHASE 2-4: NETZ, MASSE, GLEISE
# ============================================================================

def _station_components(parsed: Mapping[str, ParsedLine]) -> List[List[str]]:
    adjacency: Dict[str, set[str]] = defaultdict(set)
    order: List[str] = []
    seen: set[str] = set()

    for pline in parsed.values():
        for name in pline.stations:
            if name not in seen:
                seen.add(name)
                order.append(name)
            adjacency.setdefault(name, set())
        for seg in pline.segments:
            adjacency[seg.a].add(seg.b)
            adjacency[seg.b].add(seg.a)

    components: List[List[str]] = []
    visited: set[str] = set()
    for root in order:
        if root in visited:
            continue
        comp: List[str] = []
        q = deque([root])
        visited.add(root)
        while q:
            node = q.popleft()
            comp.append(node)
            for nxt in sorted(adjacency[node]):
                if nxt not in visited:
                    visited.add(nxt)
                    q.append(nxt)
        components.append(comp)

    return components


def _solve_kkt(hdiag: np.ndarray, z0: np.ndarray, A: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = len(z0)
    m = len(b)
    H = np.diag(hdiag)
    K = np.block(
        [
            [H, A.T],
            [A, np.zeros((m, m), dtype=float)],
        ]
    )
    rhs = np.concatenate((hdiag * z0, b))
    solution, *_ = np.linalg.lstsq(K, rhs, rcond=None)
    return solution[:n]


def build_network(
    lines: Mapping[str, TurnLine],
    *,
    stations: Mapping[str, Station],
    netz: NetzConfig = CFG.netz,
) -> Network:
    """Phase 2 (Netz): Linien parsen und ausrichten.

    Prueft dabei die Definition (unbekannte Stationen, doppelte Stationen,
    ungueltige Richtungen) und die Konsistenz gemeinsamer Korridore.
    """
    if not lines:
        raise GeometryError("Es wurden keine Linien definiert")

    parsed = {lid: parse_line(lid, line, netz, stations) for lid, line in lines.items()}
    starts = infer_start_bearings(lines, parsed, netz)
    return Network(parsed=parsed, starts=starts)


def solve_measures(
    lines: Mapping[str, TurnLine],
    network: Network,
    *,
    solver: SolverConfig = CFG.solver,
) -> Measures:
    """Phase 3 (Masse): Koordinaten und elastische Laengen loesen.

    Stellt das Gleichungssystem aus den Segmenten auf (p_b - p_a = Summe
    der Beinvektoren), verankert die Netzteile und loest es per Least
    Squares; untere und obere Laengenschranken werden ueber ein Active-Set
    nachgezogen.
    """
    parsed = network.parsed
    starts = network.starts

    station_order: List[str] = []
    station_seen: set[str] = set()
    for pline in parsed.values():
        for name in pline.stations:
            if name not in station_seen:
                station_seen.add(name)
                station_order.append(name)

    station_index = {name: i for i, name in enumerate(station_order)}

    leg_to_var, length_vars = _collect_length_variables(parsed)
    var_order = list(length_vars)

    coord_count = 2 * len(station_order)
    var_index = {key: coord_count + i for i, key in enumerate(var_order)}
    n_unknowns = coord_count + len(var_order)

    rows: List[np.ndarray] = []
    rhs: List[float] = []

    def x_idx(name: str) -> int:
        return 2 * station_index[name]

    def y_idx(name: str) -> int:
        return 2 * station_index[name] + 1

    def add_constraint(coeffs: Mapping[int, float], value: float) -> None:
        row = np.zeros(n_unknowns, dtype=float)
        for i, c in coeffs.items():
            row[i] = c
        rows.append(row)
        rhs.append(float(value))

    # Segmentgleichungen: p_b - p_a = Summe( dir_j * laenge_j )
    for lid, pline in parsed.items():
        for seg in pline.segments:
            x_coeffs = {x_idx(seg.b): 1.0, x_idx(seg.a): -1.0}
            y_coeffs = {y_idx(seg.b): 1.0, y_idx(seg.a): -1.0}
            x_rhs = 0.0
            y_rhs = 0.0

            for leg_idx, leg in enumerate(seg.legs):
                bearing = (starts[lid] + leg.bearing_offset) % 360
                if bearing not in COMPASS:
                    raise GeometryError(
                        f"{lid}: Segment {seg.a} -> {seg.b} enthaelt ungueltige "
                        f"Beinrichtung {bearing} Grad"
                    )
                dx, dy = COMPASS[bearing]
                key = leg_to_var.get((lid, seg.index, leg_idx))
                if key is None:
                    x_rhs += dx * leg.length.preferred
                    y_rhs += dy * leg.length.preferred
                else:
                    vi = var_index[key]
                    x_coeffs[vi] = x_coeffs.get(vi, 0.0) - dx
                    y_coeffs[vi] = y_coeffs.get(vi, 0.0) - dy

            add_constraint(x_coeffs, x_rhs)
            add_constraint(y_coeffs, y_rhs)

    # Anker
    components = _station_components(parsed)
    component_of: Dict[str, int] = {}
    for ci, comp in enumerate(components):
        for station in comp:
            component_of[station] = ci

    anchored_components: set[int] = set()
    for lid, line in lines.items():
        if line.anchor is None:
            continue
        first_station = parsed[lid].stations[0]
        ax, ay = line.anchor
        add_constraint({x_idx(first_station): 1.0}, ax)
        add_constraint({y_idx(first_station): 1.0}, ay)
        anchored_components.add(component_of[first_station])

    # Nicht verankerte Komponenten deterministisch platzieren
    for ci, comp in enumerate(components):
        if ci in anchored_components:
            continue
        root = comp[0]
        add_constraint({x_idx(root): 1.0}, ci * solver.component_gap)
        add_constraint({y_idx(root): 1.0}, 0.0)

    A_base = np.vstack(rows)
    b_base = np.asarray(rhs, dtype=float)

    z0 = np.zeros(n_unknowns, dtype=float)
    hdiag = np.full(n_unknowns, solver.coordinate_regularization, dtype=float)

    for key in var_order:
        idx = var_index[key]
        var = length_vars[key]
        z0[idx] = var.preferred
        hdiag[idx] = 1.0 / (var.flex * var.flex)

    active_bounds: Dict[Tuple[str, str], float] = {}
    z = np.zeros(n_unknowns, dtype=float)

    for _ in range(len(var_order) + 1):
        if active_bounds:
            bound_rows = []
            bound_rhs = []
            for key, lower in active_bounds.items():
                row = np.zeros(n_unknowns, dtype=float)
                row[var_index[key]] = 1.0
                bound_rows.append(row)
                bound_rhs.append(lower)
            A = np.vstack((A_base, np.vstack(bound_rows)))
            b = np.concatenate((b_base, np.asarray(bound_rhs)))
        else:
            A, b = A_base, b_base

        z = _solve_kkt(hdiag, z0, A, b)
        residual = A @ z - b
        max_residual = float(np.max(np.abs(residual))) if len(residual) else 0.0
        if max_residual > solver.constraint_tolerance:
            raise GeometryError(
                "Die Linienregeln sind geometrisch widerspruechlich. "
                f"Maximaler Gleichungsfehler: {max_residual:.3g}"
            )

        violations: List[Tuple[Tuple[str, str], float, float]] = []
        for key in var_order:
            if key in active_bounds:
                continue
            var = length_vars[key]
            value = z[var_index[key]]
            if value < var.min_gap - 1e-8:
                violations.append((key, var.min_gap, var.min_gap - value))
            elif value > var.max_gap + 1e-8:
                violations.append((key, var.max_gap, value - var.max_gap))
        if not violations:
            break

        worst_key, worst_bound, _ = max(violations, key=lambda item: item[2])
        active_bounds[worst_key] = worst_bound
    else:
        raise GeometryError("Laengengrenzen konnten nicht geloest werden")

    coords = {
        name: (float(z[x_idx(name)]), float(z[y_idx(name)]))
        for name in station_order
    }

    leg_lengths: Dict[Tuple[str, int, int], float] = {}
    for lid, pline in parsed.items():
        for seg in pline.segments:
            for leg_idx, leg in enumerate(seg.legs):
                key = leg_to_var.get((lid, seg.index, leg_idx))
                leg_lengths[(lid, seg.index, leg_idx)] = (
                    leg.length.preferred if key is None else float(z[var_index[key]])
                )

    return Measures(coords=coords, leg_lengths=leg_lengths)


def build_tracks(network: Network, measures: Measures) -> Tracks:
    """Phase 4 (Gleise): aus Koordinaten und geloesten Laengen die
    Polylinien der Streckenabschnitte bauen.

    Rein rekonstruierend -- braucht keine Konfiguration. Jedes Stationspaar
    wird genau einmal erzeugt, damit ein von mehreren Linien befahrener
    Korridor auch nur eine Mittellinie hat.
    """
    parsed = network.parsed
    starts = network.starts
    coords = measures.coords

    corridor_paths: Dict[frozenset[str], List[Pt]] = {}
    corridor_corners: Dict[frozenset[str], List[Optional[Corner]]] = {}
    for lid, pline in parsed.items():
        for seg in pline.segments:
            key = _pair_key(seg.a, seg.b)
            if key in corridor_paths:
                continue

            # corners[i] beschreibt den Knick AN points[i] -- das ist der
            # Corner des Beins, das AB points[i] weiterlaeuft (also legs[i],
            # nicht legs[i-1]). Der letzte Punkt ist die Station seg.b und
            # hat nie einen Knick.
            lengths = [
                measures.leg_lengths[(lid, seg.index, k)] for k in range(len(seg.legs))
            ]
            points = [coords[seg.a]]
            corners: List[Optional[Corner]] = [None]
            pos = coords[seg.a]
            for leg_idx, leg in enumerate(seg.legs):
                length = lengths[leg_idx]
                bearing = (starts[lid] + leg.bearing_offset) % 360
                dx, dy = COMPASS[bearing]
                pos = (pos[0] + dx * length, pos[1] + dy * length)
                points.append(pos)
                next_leg = seg.legs[leg_idx + 1] if leg_idx + 1 < len(seg.legs) else None
                if next_leg is None or next_leg.corner is None:
                    corners.append(None)
                else:
                    # Freier Platz auf beiden Nachbarbeinen: von jedem Bein
                    # geht ab, was der Knick am ANDEREN Ende dieses Beins
                    # schon beansprucht. Nur wenn ein Bein an beiden Enden
                    # einen Knick hat, teilen sich die zwei Boegen es also
                    # wirklich; sonst steht die volle Beinlaenge bereit.
                    davor = seg.legs[leg_idx].corner
                    danach = (
                        seg.legs[leg_idx + 2].corner
                        if leg_idx + 2 < len(seg.legs)
                        else None
                    )
                    frei_links = lengths[leg_idx] - (davor.tangente() if davor else 0.0)
                    frei_rechts = lengths[leg_idx + 1] - (
                        danach.tangente() if danach else 0.0
                    )
                    corners.append(
                        replace(
                            next_leg.corner,
                            max_tangent=max(min(frei_links, frei_rechts), 0.0),
                        )
                    )

            end = coords[seg.b]
            if hypot(points[-1][0] - end[0], points[-1][1] - end[1]) > 1e-6:
                raise GeometryError(
                    f"Interne Rekonstruktion stimmt nicht fuer {seg.a} -> {seg.b}"
                )

            corridor_paths[key] = points
            corridor_corners[key] = corners

    return Tracks(corridor_paths=corridor_paths, corridor_corners=corridor_corners)


def solve_layout(
    lines: Mapping[str, TurnLine],
    *,
    stations: Mapping[str, Station],
    cfg: Config = CFG,
) -> LayoutResult:
    """Phase 2-4 am Stueck: Netz -> Masse -> Gleise.

    Reine Verkettung; wer eine Zwischenstufe braucht (z.B. das Netz pruefen,
    ohne zu loesen), ruft die drei Funktionen einzeln auf.
    """
    network = build_network(lines, stations=stations, netz=cfg.netz)
    measures = solve_measures(lines, network, solver=cfg.solver)
    tracks = build_tracks(network, measures)
    return LayoutResult(network=network, measures=measures, tracks=tracks)


# ============================================================================
# PHASE 4b: LINIENFUEHRUNG (BUENDELUNG)
# ============================================================================
#
# Aus den Korridoren (Phase 4) wird je Linie ein durchgehender Streckenzug.
# Wo mehrere der betrachteten Linien denselben Abschnitt befahren, werden
# sie aus der Mitte heraus parallel versetzt. Welche Linien betrachtet
# werden, ist eine Vorgabe von aussen -- deshalb eine eigene Phase und
# nicht Teil von Tracks.

def _bearing_of(p_from: Pt, p_to: Pt) -> int:
    dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
    length = hypot(dx, dy)
    ux, uy = dx / length, dy / length
    for angle, (cx, cy) in COMPASS.items():
        if abs(ux - cx) < 1e-6 and abs(uy - cy) < 1e-6:
            return angle
    raise GeometryError("Kante ist nicht oktilinear")


class _UnionFind:
    def __init__(self, items: Iterable[frozenset]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: frozenset) -> frozenset:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: frozenset, b: frozenset) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _corridor_slot_overrides(
    layout: LayoutResult,
    corridors: Mapping[str, Corridor],
    edge_lines: Mapping[frozenset, set],
    families: Mapping[str, str],
) -> Dict[frozenset, Dict[str, float]]:
    """Feste Spurlagen aus den von Hand definierten Korridoren.

    Das in `Corridor.offsets` notierte Vorzeichen gilt in Schreibrichtung
    des Korridors; hier wird es auf die gespeicherte Kantenrichtung
    umgerechnet, in der die Slots spaeter angewendet werden.
    """
    overrides: Dict[frozenset, Dict[str, float]] = {}
    for name, corridor in corridors.items():
        ids = [step for step in corridor.steps if isinstance(step, str)]
        for a, b in zip(ids, ids[1:]):
            key = _pair_key(a, b)
            if key not in layout.tracks.corridor_paths:
                raise GeometryError(
                    f"Korridor '{name}': {a} -> {b} ist keine Kante im Netz"
                )
            if key not in edge_lines:
                continue  # Kante wird von keiner der betrachteten Linien befahren

            points = layout.tracks.corridor_paths[key]
            start = layout.measures.coords[a]
            forward = hypot(points[0][0] - start[0], points[0][1] - start[1]) < 1e-6
            sign = 1 if forward else -1

            slots: Dict[str, float] = {}
            for line_id in edge_lines[key]:
                family = families.get(line_id, line_id)
                # Erst die Linien-ID, dann die Familie: das Spezielle
                # schlaegt das Allgemeine. Nur so lassen sich Linien
                # derselben Familie auf ihren GETRENNTEN Aesten
                # unterschiedlich legen (z.B. S8 und S85 im Norden), ohne
                # dass der Familienname beide erwischt.
                if line_id in corridor.offsets:
                    value = corridor.offsets[line_id]
                elif family in corridor.offsets:
                    value = corridor.offsets[family]
                else:
                    raise GeometryError(
                        f"Korridor '{name}': {line_id} faehrt {a} -> {b}, hat "
                        f"aber keinen Versatz (weder '{family}' noch '{line_id}' "
                        "in offsets)"
                    )
                slots[line_id] = value * sign
            overrides[key] = slots
    return overrides


def _compute_bundle_slots(
    layout: LayoutResult,
    line_ids: Iterable[str],
    families: Optional[Mapping[str, str]] = None,
    corridors: Optional[Mapping[str, Corridor]] = None,
) -> Dict[frozenset, Dict[str, float]]:
    """Weist jeder (Korridorkante, Linie) einen zentrierten Slot-Index zu,
    fuer alle Kanten, die mindestens eine der `line_ids` befaehrt.

    Slots werden pro FAMILIE vergeben, nicht pro Linie: Varianten derselben
    Linie (S2/S25/S26, S1/S15, ...) teilen sich einen Platz und liegen auf
    gemeinsamen Abschnitten uebereinander statt nebeneinander. Ohne
    `families` ist jede Linie ihre eigene Familie.

    Kanten werden zu "Straecken" zusammengefasst, wenn sie an einer
    gemeinsamen Station geradeaus (ohne Richtungswechsel) ineinander
    uebergehen UND von genau derselben Menge an FAMILIEN befahren werden --
    der Slot bleibt dann fuer die ganze Strecke gleich. Endet nur eine
    Variante, waehrend ihre Stammlinie weiterlaeuft, aendert sich die
    Familienmenge nicht und das Buendel bleibt unveraendert. Erst wenn eine
    ganze Familie dazukommt oder endet, beginnt eine neue Strecke und eine
    dann allein verbleibende Familie zentriert sich wieder (Slot 0).

    Der Slot gilt in der gespeicherten Richtung seiner Kante. Innerhalb
    einer Strecke sind die Kanten gleichlaeufig gespeichert (geprueft: 0 von
    133 Vereinigungen gegenlaeufig), ein Slot meint dort also durchgehend
    dieselbe Seite.
    """
    fam = (lambda lid: lid) if families is None else (lambda lid: families.get(lid, lid))

    edge_lines: Dict[frozenset, set] = defaultdict(set)
    edge_families: Dict[frozenset, set] = defaultdict(set)
    for line_id in line_ids:
        for seg in layout.network.parsed[line_id].segments:
            key = _pair_key(seg.a, seg.b)
            edge_lines[key].add(line_id)
            edge_families[key].add(fam(line_id))

    edges = list(edge_lines)

    def endpoint_bearings(key: frozenset, station_id: str) -> Tuple[int, int]:
        """(Ankunfts-, Abfahrts-)Winkel an `station_id` ueber Kante `key`."""
        points = layout.tracks.corridor_paths[key]
        start = layout.measures.coords[station_id]
        if hypot(points[0][0] - start[0], points[0][1] - start[1]) < 1e-6:
            depart = _bearing_of(points[0], points[1])
            return (depart + 180) % 360, depart
        arrive = _bearing_of(points[-2], points[-1])
        return arrive, (arrive + 180) % 360

    station_edges: Dict[str, List[frozenset]] = defaultdict(list)
    for key in edges:
        for station_id in key:
            station_edges[station_id].append(key)

    uf = _UnionFind(edges)
    for station_id, keys in station_edges.items():
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                k1, k2 = keys[i], keys[j]
                if edge_families[k1] != edge_families[k2]:
                    continue
                arrive1, _ = endpoint_bearings(k1, station_id)
                _, depart2 = endpoint_bearings(k2, station_id)
                arrive2, _ = endpoint_bearings(k2, station_id)
                _, depart1 = endpoint_bearings(k1, station_id)
                if arrive1 == depart2 or arrive2 == depart1:
                    uf.union(k1, k2)

    stretch_families: Dict[frozenset, set] = defaultdict(set)
    for key in edges:
        stretch_families[uf.find(key)] |= edge_families[key]

    edge_line_slot: Dict[frozenset, Dict[str, float]] = {}
    for key in edges:
        ordered = sorted(stretch_families[uf.find(key)])
        n = len(ordered)
        slot_of = {name: i - (n - 1) / 2 for i, name in enumerate(ordered)}
        # jede Linie erbt den Slot ihrer Familie -- Varianten liegen dadurch
        # exakt uebereinander
        edge_line_slot[key] = {
            lid: slot_of[fam(lid)] for lid in edge_lines[key]
        }

    # Von Hand definierte Korridore schlagen die automatische Vergabe.
    if corridors:
        fam_map = {lid: fam(lid) for lids in edge_lines.values() for lid in lids}
        edge_line_slot.update(
            _corridor_slot_overrides(layout, corridors, edge_lines, fam_map)
        )
    return edge_line_slot


def _offset_points(
    points: Sequence[Pt], slot: float, spacing: float
) -> List[Pt]:
    """Versetzt die Polylinie um `slot * spacing` Gitter-Einheiten seitlich.

    Innenpunkte werden auf GEHRUNG gesetzt: die versetzten Beine werden als
    Geraden geschnitten, statt jeden Punkt nur senkrecht zu einem
    Nachbarbein zu schieben. Das ist der Unterschied zwischen einer echten
    Parallelen und einer verzerrten Kopie -- nur so bleibt jedes Bein exakt
    parallel zum Original (und damit oktilinear), und der Knick wandert um
    das noetige Stueck entlang der Fahrtrichtung mit. Genau diese
    Verschiebung laesst die Linie sauber auf die versetzte Fortsetzung
    einschwenken, ohne dass ein Zusatzknick entsteht.

    Fuer den Schnittpunkt gilt mit den Normalen `nu`, `nv` der beiden Beine:
        m = d * (nu + nv) / (1 + nu . nv)
    denn dieses m hat zu beiden versetzten Geraden genau den Abstand d.
    """
    d = slot * spacing
    if d == 0 or len(points) < 2:
        return list(points)

    def normal(a: Pt, b: Pt) -> Pt:
        return COMPASS[(_bearing_of(a, b) + 90) % 360]

    first = normal(points[0], points[1])
    out: List[Pt] = [(points[0][0] + first[0] * d, points[0][1] + first[1] * d)]

    for i in range(1, len(points) - 1):
        nu = normal(points[i - 1], points[i])
        nv = normal(points[i], points[i + 1])
        denom = 1.0 + nu[0] * nv[0] + nu[1] * nv[1]
        if abs(denom) < 1e-9:
            # 180-Grad-Kehre: kein Schnittpunkt, senkrecht ausweichen
            out.append((points[i][0] + nu[0] * d, points[i][1] + nu[1] * d))
            continue
        out.append(
            (
                points[i][0] + d * (nu[0] + nv[0]) / denom,
                points[i][1] + d * (nu[1] + nv[1]) / denom,
            )
        )

    last = normal(points[-2], points[-1])
    out.append((points[-1][0] + last[0] * d, points[-1][1] + last[1] * d))
    return out


def _line_path(
    layout: LayoutResult,
    line_id: str,
    *,
    edge_slots: Optional[Mapping[frozenset, Mapping[str, float]]] = None,
    bundle_spacing: float = 0.0,
) -> LinePath:
    """Rekonstruiert den durchgehenden Streckenzug einer Linie in
    Fahrtreihenfolge samt Knicken und Stationslagen.

    Der Versatz gilt je BEIN, nicht je Kante, und ein Wechsel wird bis zur
    naechsten Kurve aufgeschoben. Der Eckpunkt ist dann der Schnittpunkt der
    beiden versetzten Beingeraden -- bei gleichem Versatz die gewohnte
    Gehrung, bei unterschiedlichem setzt die Kurve entsprechend spaeter ein
    und EIN Bogen fuehrt von der alten auf die neue Spur. Damit entfaellt
    jeder Sprung im Track.

    Nur wenn zwei Beine mit verschiedenem Versatz kollinear sind, gibt es
    keinen Schnittpunkt -- dort bleibt ein 45-Grad-Versatzknick noetig.
    """
    pline = layout.network.parsed[line_id]

    # 1) Beine der ganzen Linie in Fahrtreihenfolge, noch auf der Mittellinie
    legs: List[Tuple[Pt, Pt, float, Optional[Corner], bool]] = []
    spans: List[Tuple[str, str, int, int]] = []   # Station a, b, erstes/letztes Bein
    for seg in pline.segments:
        key = _pair_key(seg.a, seg.b)
        pts = layout.tracks.corridor_paths[key]
        cs = layout.tracks.corridor_corners[key]
        start_pt = layout.measures.coords[seg.a]
        reversed_edge = hypot(pts[0][0] - start_pt[0], pts[0][1] - start_pt[1]) > 1e-6
        if reversed_edge:
            pts = list(reversed(pts))
            cs = list(reversed(cs))
        slot = 0.0
        bundled = False
        if edge_slots is not None:
            on_edge = edge_slots.get(key, {})
            slot = on_edge.get(line_id, 0.0)
            # Die Slots sind auf die GESPEICHERTE Kantenrichtung normiert,
            # angewendet wird der Versatz hier aber entlang der FAHRTrichtung.
            # Befaehrt diese Linie die Kante rueckwaerts, muss das Vorzeichen
            # kippen -- sonst landen zwei gegenlaeufige Linien trotz
            # verschiedener Slots auf derselben Seite.
            if reversed_edge:
                slot = -slot
            # Nur wenn auf dieser Kante wirklich mehrere Spuren liegen, muss
            # der Bogen konzentrisch zu den Nachbarn sein. Fahrt die Linie
            # allein (oder nur mit ihrer eigenen Familie, also auf demselben
            # Slot), gibt es niemanden, zu dem sie parallel laufen muesste --
            # dann verkleinert die Versatzkorrektur den Bogen ohne Grund.
            bundled = len(set(round(v, 9) for v in on_edge.values())) > 1
        first = len(legs)
        for j in range(len(pts) - 1):
            legs.append((pts[j], pts[j + 1], slot, cs[j] if j > 0 else None, bundled))
        spans.append((seg.a, seg.b, first, len(legs) - 1))

    if not legs:
        return LinePath([], [])

    dirs: List[Pt] = []
    lens: List[float] = []
    for a, b, _, _, _ in legs:
        length = hypot(b[0] - a[0], b[1] - a[1])
        dirs.append(((b[0] - a[0]) / length, (b[1] - a[1]) / length))
        lens.append(length)

    def turns(k: int) -> bool:
        return (
            abs(dirs[k][0] - dirs[k - 1][0]) > 1e-9
            or abs(dirs[k][1] - dirs[k - 1][1]) > 1e-9
        )

    # 2) Versatz je Bein: Wechsel erst in der naechsten Kurve uebernehmen
    offs: List[float] = []
    current = legs[0][2]
    for k in range(len(legs)):
        if k > 0 and turns(k):
            current = legs[k][2]
        offs.append(current)

    def normal(u: Pt) -> Pt:
        return (-u[1], u[0])

    def shifted(point: Pt, k: int) -> Pt:
        nx, ny = normal(dirs[k])
        d = offs[k] * bundle_spacing
        return (point[0] + nx * d, point[1] + ny * d)

    points: List[Pt] = [shifted(legs[0][0], 0)]
    corners: List[Optional[Corner]] = [None]

    for k in range(1, len(legs)):
        if not turns(k):
            if abs(offs[k] - offs[k - 1]) < 1e-12:
                points.append(shifted(legs[k][0], k))
                corners.append(None)
                continue
            # kollinear mit Versatzwechsel: 45-Grad-Knick, seitlich wie
            # laengs genau der Versatz
            base = shifted(legs[k - 1][1], k - 1)
            delta = (offs[k] - offs[k - 1]) * bundle_spacing
            ux, uy = dirs[k]
            nx, ny = normal(dirs[k])
            points.append(base)
            corners.append(None)
            points.append(
                (
                    base[0] + ux * abs(delta) + nx * delta,
                    base[1] + uy * abs(delta) + ny * delta,
                )
            )
            corners.append(None)
            continue

        # Kurve: Schnittpunkt der beiden versetzten Beingeraden
        p = shifted(legs[k - 1][0], k - 1)
        q = shifted(legs[k][0], k)
        ux, uy = dirs[k - 1]
        vx, vy = dirs[k]
        cross = ux * vy - uy * vx
        t = ((q[0] - p[0]) * vy - (q[1] - p[1]) * vx) / cross
        corner = legs[k][3]
        if corner is not None:
            # Versatz einrechnen, sobald die Linie den Bogen ueberhaupt neben
            # Nachbarspuren durchfaehrt. Massgeblich fuer konzentrische Boegen
            # ist nicht, dass der eigene Versatz gleich BLEIBT, sondern dass
            # die ABSTAENDE innerhalb des Buendels erhalten bleiben -- und das
            # tun sie auch, wenn das ganze Buendel am Knick gemeinsam
            # umschwenkt (Adlershof -> Altglienicke: alle Spuren um -0.5).
            # Ohne die Korrektur bekaemen dort alle denselben Radius, die
            # Boegen bekaemen verschiedene Mittelpunkte und liefen auseinander.
            parallel = legs[k][4]
            corner = replace(
                corner, offset=offs[k - 1] * bundle_spacing if parallel else 0.0
            )
        points.append((p[0] + ux * t, p[1] + uy * t))
        corners.append(corner)

    points.append(shifted(legs[-1][1], len(legs) - 1))
    corners.append(None)

    # Stationslage je Linie: Anfang des ersten bzw. Ende des letzten Beins
    # des jeweiligen Segments, mit dem dort geltenden Versatz.
    stations: Dict[str, Pt] = {}
    for a, b, first, last in spans:
        stations.setdefault(a, shifted(legs[first][0], first))
        stations[b] = shifted(legs[last][1], last)

    return LinePath(points, corners, stations)


def build_line_layout(
    layout: LayoutResult,
    line_ids: Iterable[str],
    *,
    bundle_spacing: float = 0.0,
    families: Optional[Mapping[str, str]] = None,
    corridors: Optional[Mapping[str, Corridor]] = None,
) -> LineLayout:
    """Phase 4b (Linienfuehrung): Streckenzuege der betrachteten Linien
    bauen und auf gemeinsamen Abschnitten parallel versetzen.

    `line_ids` bestimmt, gegen welche Linien gebuendelt wird -- nur diese
    zaehlen bei der Slot-Vergabe. Uebergibt man alle Linien des Netzes,
    entsteht das vollstaendige Buendelbild; uebergibt man nur die farbig
    hervorgehobenen, bleiben die uebrigen Korridore mittig.

    `bundle_spacing` ist der seitliche Abstand in Gitter-Einheiten; 0
    schaltet den Versatz ab und liefert reine Mittellinien.

    `families` fasst Linienvarianten zusammen (siehe TurnLine.family): sie
    teilen sich einen Slot und laufen auf gemeinsamen Abschnitten
    uebereinander statt nebeneinander.

    `corridors` legt die Spurlage auf einzelnen Abschnitten von Hand fest
    und schlaegt dort die automatische Vergabe (siehe Corridor).
    """
    ids = list(line_ids)
    fams = {lid: (families or {}).get(lid, lid) for lid in ids}
    slots = _compute_bundle_slots(layout, ids, fams, corridors)
    paths = {
        line_id: _line_path(
            layout, line_id, edge_slots=slots, bundle_spacing=bundle_spacing
        )
        for line_id in ids
    }
    return LineLayout(paths=paths, slots=slots, families=fams)


# ============================================================================
# PHASE 5: RENDERING DER GLEISKARTE
# ============================================================================

def _corner_tangent_factor(delta: int) -> float:
    """tan(delta/2) -- der Faktor zwischen Kreisradius und Tangentenlaenge:
    t = R * tan(delta/2), R = t / tan(delta/2)."""
    return tan(radians(abs(delta)) / 2)


def _corner_radius(corner: Optional[Corner]) -> Optional[float]:
    """Loest einen Knick in seinen KREISRADIUS auf (Gitter-Einheiten).

    Der Radius steht bereits seit dem Parsen fest (explizites
    Turn(radius=...) oder NetzConfig-Default) -- er ist Geometrie, weil der
    Solver Platz dafuer reserviert hat. Hier kommen nur noch der Deckel aus
    den tatsaechlichen Beinlaengen und die Versatzkorrektur dazu.
    """
    if corner is None:
        return None
    base = corner.radius if corner.radius is not None else 0.0

    # Deckel aus der Mittellinie -- vor der Versatzkorrektur, damit alle
    # Spuren von derselben Basis ausgehen und konzentrisch bleiben. Der
    # Deckel begrenzt die Tangente (dort ist der Platz knapp), muss also in
    # einen Radius umgerechnet werden. Ein explizites Turn(radius=...) ist
    # eine Anweisung und wird NICHT gedeckelt: nur so laesst sich eine Kurve
    # bewusst so weit aufziehen, dass das gerade Stueck dazwischen
    # vollstaendig verschwindet.
    if corner.max_tangent is not None and not corner.erzwungen:
        base = min(base, corner.max_tangent / _corner_tangent_factor(corner.delta))

    # Versatz einrechnen: bei einer Rechtskurve (delta > 0) liegt die Seite
    # mit positivem Versatz innen und bekommt den kleineren Radius, aussen
    # den groesseren -- so bleiben die Boegen konzentrisch. Auf den Radius
    # wirkt der Versatz direkt, ohne Winkelfaktor.
    if corner.offset:
        base -= (1 if corner.delta > 0 else -1) * corner.offset
    return max(base, 0.0)


def build_track_svg(
    layout: LayoutResult,
    *,
    stations: Mapping[str, Station],
    highlight_lines: Optional[Mapping[str, str]] = None,
    line_layout: Optional[LineLayout] = None,
    draw_corridors: bool = True,
    style: StyleConfig = CFG.style,
    label_override: Optional[Mapping[str, Tuple[float, float, str]]] = None,
) -> str:
    """Phase 5 (Darstellung): zeichnet ein fertig geloestes `LayoutResult`.

    Erzeugt selbst keine Geometrie mehr -- `solve_layout()` (Phase 2-4) muss
    vorher aufgerufen werden. Dadurch laesst sich ein Layout einmal loesen,
    inspizieren und mehrfach unterschiedlich rendern.

    Die Streckenzuege der hervorgehobenen Linien kommen aus Phase 4b. Ohne
    `line_layout` werden sie hier bequemlichkeitshalber aus `highlight_lines`
    erzeugt; wer die Buendelung selbst steuern will (z.B. gegen ALLE Linien
    statt nur die hervorgehobenen), ruft `build_line_layout()` vorher auf und
    reicht das Ergebnis durch.

    `draw_corridors` blendet die duennen grauen Mittellinien der Korridore
    als Hilfstrassen ein. Sie werden ueber die Linien gezeichnet, damit sie
    sichtbar bleiben und man den Versatz gegen die Trassenmitte pruefen
    kann.
    """
    labels = {} if label_override is None else dict(label_override)
    highlights = {} if highlight_lines is None else dict(highlight_lines)
    if line_layout is None:
        line_layout = build_line_layout(
            layout, highlights.keys(), bundle_spacing=style.bundle_spacing
        )

    # Bounding Box ueber alle Korridorpunkte
    all_points = [p for path in layout.tracks.corridor_paths.values() for p in path]
    if not all_points:
        raise GeometryError("Keine Korridore zum Rendern gefunden")

    minx = min(x for x, _ in all_points)
    miny = min(y for _, y in all_points)
    maxx = max(x for x, _ in all_points)
    maxy = max(y for _, y in all_points)

    def px(point: Pt) -> Pt:
        return (
            (point[0] - minx) * style.grid + style.margin,
            (point[1] - miny) * style.grid + style.margin,
        )

    width = (maxx - minx) * style.grid + 2 * style.margin
    height = (maxy - miny) * style.grid + 2 * style.margin

    # Stationsrichtung fuer automatische Labels
    station_dirs: Dict[str, Pt] = {}
    for pline in layout.network.parsed.values():
        for seg in pline.segments:
            corridor = layout.tracks.corridor_paths[_pair_key(seg.a, seg.b)]
            if seg.a not in station_dirs:
                station_dirs[seg.a] = _norm(
                    (corridor[1][0] - corridor[0][0], corridor[1][1] - corridor[0][1])
                )
            if seg.b not in station_dirs:
                station_dirs[seg.b] = _norm(
                    (corridor[-1][0] - corridor[-2][0], corridor[-1][1] - corridor[-2][1])
                )

    svg: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'font-family="{esc(style.label_family)}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]

    def px_radii(corners: Sequence[Optional[Corner]]) -> List[Optional[float]]:
        """Knicke in Pixel-Rundungsradien aufloesen: Deckel und Versatz
        anwenden (Gitter-Einheiten), dann mit style.grid skalieren."""
        radii = [_corner_radius(c) for c in corners]
        return [None if r is None else r * style.grid for r in radii]

    # Hervorgehobene Linien: fertiger Streckenzug aus Phase 4b (inkl.
    # Buendel-Versatz) in Linienfarbe, oben drauf.
    # Innerhalb einer Familie die Stammlinie ZULETZT zeichnen, damit auf
    # gemeinsamen Abschnitten ihre Farbe sichtbar bleibt und nicht die der
    # darueber liegenden Variante.
    def draw_order(item: Tuple[str, str]) -> Tuple[int, str]:
        lid = item[0]
        return (1 if line_layout.families.get(lid, lid) == lid else 0, lid)

    linien_d: Dict[str, str] = {}
    linien_flach: Dict[str, List[Pt]] = {}
    for line_id, color in sorted(highlights.items(), key=draw_order):
        line = line_layout.paths[line_id]
        ecken = px_radii(line.corners)
        punkte_px = [px(p) for p in line.points]
        path_d = rounded_path_d(punkte_px, ecken)
        linien_d[line_id] = path_d
        linien_flach[line_id] = rounded_points(punkte_px, ecken, schritte=24)
        svg.append(
            f'<path d="{path_d}" fill="none" stroke="{color}" '
            f'stroke-width="{style.highlight_line_width}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
        )

    # Kreuzungen ausserhalb von Hubs: dort laufen zwei Linien ohne
    # Umsteigebeziehung uebereinander, was ohne Hinweis unuebersichtlich ist.
    # Die obenliegende bekommt deshalb genau im Kreuzungsbereich einen
    # weissen Rand -- ein kurzes, breiteres weisses Stueck, auf das ihre
    # eigene Farbe neu gezeichnet wird.
    def liniennummer(line_id: str) -> int:
        """Nummer der FAMILIE, nicht der einzelnen Linie.

        S25 und S26 fahren im Buendel der S2 und muessen sich deshalb genauso
        verhalten wie sie -- sonst gewaenne S25 (25) gegen S8 (8), waehrend
        S85 (85) gegen S2 (2) gewinnt, und dasselbe Buendel laege einmal oben
        und einmal unten."""
        stamm = line_layout.families.get(line_id, line_id)
        ziffern = "".join(c for c in stamm if c.isdigit())
        return int(ziffern) if ziffern else 0

    # Sperrbereich je Station: bis zum aeussersten dort liegenden
    # Linienpunkt, plus Pille und Strichbreite. Ein fester Radius reicht
    # nicht -- an einer Buendelkreuzung wie Schoeneberg liegen die aeusseren
    # Schnittpunkte deutlich weiter aussen als die Stationskoordinate.
    stationspunkte: List[Tuple[Pt, float]] = []
    for sid, coord in layout.measures.coords.items():
        mitte = px(coord)
        weit = max(
            (hypot(px(pt)[0] - mitte[0], px(pt)[1] - mitte[1])
             for lid in highlights
             for s2, pt in line_layout.paths[lid].stations.items() if s2 == sid),
            default=0.0,
        )
        # genau so weit, wie der Stationsmarker die Kreuzung ohnehin
        # verdeckt: die Pille ist um ihren Eckradius ueber die aeussersten
        # Linienpunkte hinaus aufgeweitet, ein normaler Punkt um station_r.
        rand = style.hub_pill_r if stations[sid].kind == "hub" else style.station_r
        stationspunkte.append((mitte, weit + rand))
    pfade_px = linien_flach

    # Ringzugehoerigkeit wird oertlich bestimmt, nicht ueber die Linien-ID:
    # auf dem Ring fahren auch Linien, die selbst nicht geschlossen sind
    # (S46/S47). Massgeblich ist, ob im selben Buendel eine geschlossene
    # Ringlinie parallel laeuft.
    ring_segmente = [
        (a, b)
        for lid in highlights
        if layout.network.parsed[lid].closed
        for a, b in zip(pfade_px[lid], pfade_px[lid][1:])
    ]
    ring_nah = style.bundle_spacing * style.grid * 4

    def am_ring(u: Pt, p: Pt) -> bool:
        for a, b in ring_segmente:
            dx_, dy_ = b[0] - a[0], b[1] - a[1]
            laenge = hypot(dx_, dy_)
            if laenge < 1e-9:
                continue
            if abs(u[0] * dy_ - u[1] * dx_) / laenge > 1e-6:
                continue                  # nicht parallel
            t_ = max(0.0, min(1.0, ((p[0] - a[0]) * dx_ + (p[1] - a[1]) * dy_) / laenge ** 2))
            if hypot(p[0] - a[0] - t_ * dx_, p[1] - a[1] - t_ * dy_) < ring_nah:
                return True
        return False

    def gekreuzte_spuren(lid: str, p: Pt) -> int:
        """Wie viele fremde Spuren schneidet `lid` in der Umgebung von `p`?

        Gezaehlt werden Familien -- eine Familie teilt sich eine Spur. Nicht
        gezaehlt wird, was nur parallel danebenlaeuft: massgeblich ist, was
        die Linie tatsaechlich ueberquert. Sonst zaehlten an einer
        Einmuendung die Nachbarspuren des eigenen Korridors mit.
        """
        reichweite = style.highlight_line_width * 3
        eigene = line_layout.families.get(lid, lid)

        def nahe_segmente(punkte: Sequence[Pt]) -> List[Tuple[Pt, Pt]]:
            return [
                (a, b)
                for a, b in zip(punkte, punkte[1:])
                if min(hypot(a[0] - p[0], a[1] - p[1]),
                       hypot(b[0] - p[0], b[1] - p[1])) < reichweite * 2
            ]

        meine = nahe_segmente(linien_flach[lid])
        familien = set()
        for lid2 in highlights:
            fam = line_layout.families.get(lid2, lid2)
            if fam == eigene or fam in familien:
                continue
            for c1, c2 in nahe_segmente(linien_flach[lid2]):
                uc = (c2[0] - c1[0], c2[1] - c1[1])
                for m1, m2 in meine:
                    um = (m2[0] - m1[0], m2[1] - m1[1])
                    nen = um[0] * uc[1] - um[1] * uc[0]
                    if abs(nen) < 1e-9:
                        continue
                    tm = ((c1[0] - m1[0]) * uc[1] - (c1[1] - m1[1]) * uc[0]) / nen
                    tc = ((c1[0] - m1[0]) * um[1] - (c1[1] - m1[1]) * um[0]) / nen
                    if not (0.0 <= tm <= 1.0 and 0.0 <= tc <= 1.0):
                        continue
                    q = (m1[0] + tm * um[0], m1[1] + tm * um[1])
                    if hypot(q[0] - p[0], q[1] - p[1]) < reichweite:
                        familien.add(fam)
                        break
                if fam in familien:
                    break
        return len(familien)

    # (Kreuzungspunkt, obenliegende Linie, untenliegende Linie)
    ueberfuehrungen: List[Tuple[Pt, str, str]] = []
    ids = sorted(highlights)
    for i, lid_a in enumerate(ids):
        for lid_b in ids[i + 1:]:
            if (line_layout.families.get(lid_a, lid_a)
                    == line_layout.families.get(lid_b, lid_b)):
                # Gleiche Familie: gleiche Farbe, gleiche Spur -- ein Rand
                # zwischen ihnen wuerde nur die eigene Linie zerschneiden.
                continue
            for a1, a2 in zip(pfade_px[lid_a], pfade_px[lid_a][1:]):
                ua = (a2[0] - a1[0], a2[1] - a1[1])
                for b1, b2 in zip(pfade_px[lid_b], pfade_px[lid_b][1:]):
                    ub = (b2[0] - b1[0], b2[1] - b1[1])
                    nenner = ua[0] * ub[1] - ua[1] * ub[0]
                    if abs(nenner) < 1e-9:
                        continue          # parallel
                    t = ((b1[0] - a1[0]) * ub[1] - (b1[1] - a1[1]) * ub[0]) / nenner
                    s = ((b1[0] - a1[0]) * ua[1] - (b1[1] - a1[1]) * ua[0]) / nenner
                    if not (0.0 < t < 1.0 and 0.0 < s < 1.0):
                        continue
                    p = (a1[0] + t * ua[0], a1[1] + t * ua[1])
                    # An einer Station kreuzen die Linien nicht, sie treffen
                    # sich dort -- Pille bzw. Stationspunkt uebernehmen.
                    if any(hypot(p[0] - q[0], p[1] - q[1]) < sperre
                           for q, sperre in stationspunkte):
                        continue
                    la, lb = hypot(*ua), hypot(*ub)
                    ea = (ua[0] / la, ua[1] / la)
                    eb = (ub[0] / lb, ub[1] / lb)
                    # Oben liegt, wer MEHR fremde Spuren ueberquert -- also
                    # die einzelne Linie ueber der Gruppe. Das liest sich am
                    # klarsten: eine Linie, die drei Spuren kreuzt, gehoert
                    # sichtbar darueber.
                    quer_a = gekreuzte_spuren(lid_a, p)
                    quer_b = gekreuzte_spuren(lid_b, p)
                    if quer_a != quer_b:
                        oben = lid_a if quer_a > quer_b else lid_b
                    else:
                        # Gleich breit: der Ring wird ueberquert, sonst
                        # entscheidet die hoehere Familiennummer.
                        a_ring, b_ring = am_ring(ea, p), am_ring(eb, p)
                        if a_ring != b_ring:
                            oben = lid_b if a_ring else lid_a
                        elif liniennummer(lid_a) >= liniennummer(lid_b):
                            oben = lid_a
                        else:
                            oben = lid_b
                    unten = lid_b if oben == lid_a else lid_a
                    ueberfuehrungen.append((p, oben, unten))

    # Ummantelt wird ein Fenster um die Kreuzung. Seine Groesse kommt nicht
    # aus dem Schnittwinkel, sondern aus der tatsaechlichen Annaeherung: so
    # weit, wie die beiden Linien einander naeher als eine Strichbreite
    # kommen. Damit passt es auch dort, wo sich zwei Linien in einer Kurve
    # flach schneiden. Gezeichnet wird der ECHTE Verlauf, ausgeschnitten auf
    # dieses Fenster -- die weissen Raender folgen den Boegen also exakt.
    def naehe_fenster(oben: str, unten: str, p: Pt) -> float:
        schwelle = style.highlight_line_width + style.crossing_casing
        grenze = style.highlight_line_width * 6
        # nur die Segmente der unteren Linie, die ueberhaupt in Frage kommen
        nah = [
            (a, b)
            for a, b in zip(linien_flach[unten], linien_flach[unten][1:])
            if min(hypot(a[0] - p[0], a[1] - p[1]),
                   hypot(b[0] - p[0], b[1] - p[1])) < grenze + schwelle
        ]

        def abstand(q: Pt) -> float:
            beste = 1e9
            for a, b in nah:
                dx_, dy_ = b[0] - a[0], b[1] - a[1]
                quad = dx_ * dx_ + dy_ * dy_
                if quad < 1e-12:
                    continue
                t_ = max(0.0, min(1.0, ((q[0] - a[0]) * dx_ + (q[1] - a[1]) * dy_) / quad))
                beste = min(beste, hypot(q[0] - a[0] - t_ * dx_, q[1] - a[1] - t_ * dy_))
            return beste

        # Den Verlauf der oberen Linie in 1-px-Schritten abtasten -- nur an
        # den Stuetzpunkten zu messen reicht nicht, die Kreuzung liegt in der
        # Regel zwischen zweien.
        weit = 0.0
        for a, b in zip(linien_flach[oben], linien_flach[oben][1:]):
            laenge = hypot(b[0] - a[0], b[1] - a[1])
            if min(hypot(a[0] - p[0], a[1] - p[1]),
                   hypot(b[0] - p[0], b[1] - p[1])) > grenze + laenge:
                continue
            for k in range(max(1, int(laenge)) + 1):
                f = k / max(1, int(laenge))
                q = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
                r_ = hypot(q[0] - p[0], q[1] - p[1])
                if r_ <= grenze and abstand(q) < schwelle:
                    weit = max(weit, r_)
        return min(weit + style.crossing_casing + 1.0, grenze)

    kreise: Dict[str, List[Tuple[Pt, float]]] = defaultdict(list)
    for p, oben, unten in ueberfuehrungen:
        kreise[oben].append((p, naehe_fenster(oben, unten, p)))

    if kreise:
        reihenfolge = sorted(kreise)
        svg.append("<defs>")
        for idx, lid in enumerate(reihenfolge):
            svg.append(f'<clipPath id="ue{idx}">')
            for pkt, radius in kreise[lid]:
                svg.append(
                    f'<circle cx="{pkt[0]:.1f}" cy="{pkt[1]:.1f}" r="{radius:.1f}"/>'
                )
            svg.append("</clipPath>")
        svg.append("</defs>")
        # erst ALLE weissen Raender, dann alle Farben -- sonst radiert der
        # Rand der einen Linie die Farbe einer anderen wieder weg.
        for idx, lid in enumerate(reihenfolge):
            svg.append(
                f'<path d="{linien_d[lid]}" fill="none" stroke="white" '
                f'stroke-width="{style.highlight_line_width + 2 * style.crossing_casing}" '
                f'stroke-linecap="round" stroke-linejoin="round" '
                f'clip-path="url(#ue{idx})"/>'
            )
        for idx, lid in enumerate(reihenfolge):
            # Nicht nur die kreuzende Linie selbst neu zeichnen, sondern ihre
            # ganze Familie: die Schwesterlinie laeuft unmittelbar daneben und
            # wuerde sonst vom eigenen weissen Rand mit abgedeckt.
            familie = line_layout.families.get(lid, lid)
            for andere in sorted(highlights):
                if line_layout.families.get(andere, andere) != familie:
                    continue
                svg.append(
                    f'<path d="{linien_d[andere]}" fill="none" '
                    f'stroke="{highlights[andere]}" '
                    f'stroke-width="{style.highlight_line_width}" '
                    f'stroke-linecap="round" stroke-linejoin="round" '
                    f'clip-path="url(#ue{idx})"/>'
                )

    # Hilfstrassen: die duennen grauen Mittellinien der Korridore, NACH den
    # Linien gezeichnet, damit sie von den breiten Linien nicht verdeckt
    # werden.
    if draw_corridors:
        for pair_key, points in layout.tracks.corridor_paths.items():
            corners = layout.tracks.corridor_corners[pair_key]
            path_d = rounded_path_d([px(p) for p in points], px_radii(corners))
            svg.append(
                f'<path d="{path_d}" fill="none" stroke="{style.track_color}" '
                f'stroke-width="{style.line_width}" stroke-linecap="round" '
                f'stroke-linejoin="round"/>'
            )

    # Stationen: ein weisser Punkt je Linie, an DEREN Lage -- auf einem
    # Buendel entstehen dadurch parallele Punktreihen statt eines einzelnen
    # Punktes auf der (womoeglich leeren) Trassenmitte. Hubs bekommen
    # stattdessen ihre Pille und deshalb hier gar keinen Punkt.
    gesetzt: set[Tuple[int, int]] = set()
    for line_id in highlights:
        for station_id, point in line_layout.paths[line_id].stations.items():
            if stations[station_id].kind == "hub":
                continue          # dort markiert die Pille die Station
            x, y = px(point)
            key = (round(x * 10), round(y * 10))
            if key in gesetzt:
                continue          # gleiche Familie, gleiche Spur
            gesetzt.add(key)
            svg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.station_r}" '
                f'fill="white"/>'
            )

    # Stationen, die von keiner gezeichneten Linie beruehrt werden, bekommen
    # ihren Punkt weiterhin auf der Trassenmitte.
    beruehrt = {
        station_id
        for line_id in highlights
        for station_id in line_layout.paths[line_id].stations
    }
    for station_id, coord in layout.measures.coords.items():
        if station_id in beruehrt:
            continue
        x, y = px(coord)
        svg.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.station_r}" '
            f'fill="white"/>'
        )

    # Hubs: weisses Rechteck mit schwarzem Rand ueber die Linien. Die Groesse
    # ergibt sich aus den Stationspunkten aller dort verkehrenden Linien,
    # aufgeweitet um den Eckradius -- so deckt es die Linien vollstaendig ab
    # und die Enden werden zu Halbkreisen.
    pillen: Dict[str, Tuple[int, float, float, float, float]] = {}
    for station_id, station in stations.items():
        if station.kind != "hub":
            continue
        punkte = [
            px(line_layout.paths[line_id].stations[station_id])
            for line_id in highlights
            if station_id in line_layout.paths[line_id].stations
        ]
        if not punkte:
            continue
        r = style.hub_pill_r
        # Ausrichtung: 0 oder 45 Grad, je nachdem was enger sitzt. Liegen die
        # Punkte auf einer Diagonalen, ist die gedrehte Box deutlich
        # schmaler; bei einer Buendelkreuzung gewinnt die achsparallele.
        # Ausrichtung nach den tatsaechlichen Gleisachsen, gewichtet mit der
        # Zahl der dort liegenden Linien.
        gewicht: Dict[int, int] = defaultdict(int)
        kurse: List[int] = []
        mitte = layout.measures.coords[station_id]
        for key, kpts in layout.tracks.corridor_paths.items():
            if station_id not in key:
                continue
            if hypot(kpts[0][0] - mitte[0], kpts[0][1] - mitte[1]) < 1e-6:
                kurs = _bearing_of(kpts[0], kpts[1])
            else:
                kurs = _bearing_of(kpts[-2], kpts[-1])
            gewicht[kurs % 90] += len(line_layout.slots.get(key, {}))
            kurse.append(kurs % 180)
        # Es gewinnt die Achse mit den MEISTEN Linien. Eine einzelne
        # kreuzende Linie soll die Pille nicht aufblaehen -- sie wird an
        # ihrem Kreuzungspunkt ohnehin mit abgedeckt.
        grad = max(sorted(gewicht), key=lambda ax: gewicht[ax]) if gewicht else 0

        # Kreuzen sich an einer Station zwei Achsen, kann eine einzige Pille
        # nicht beiden Buendeln folgen. Dann legt sie sich LAENGS der
        # Nebenachse (der kreuzenden Linie) und bleibt eine Spur dick -- die
        # Hauptachse wird dabei ueberquert. Sonst muesste sie sich zu einer
        # Raute aufblaehen, um alle Punkte zu umschliessen.
        laengs: Optional[int] = None
        if len(gewicht) > 1:
            neben = min(sorted(gewicht), key=lambda ax: gewicht[ax])
            grad = neben
            # im gedrehten System zeigt die Laengsrichtung entlang x' oder y'
            # Im um -grad gedrehten System liegt ein Kurs von `grad` auf der
            # y'-Achse und einer von grad+90 auf der x'-Achse.
            neben_kurs = next(k for k in kurse if k % 90 == neben)
            laengs = 1 if (neben_kurs - grad) % 180 == 0 else 0
        a = radians(grad)
        ca, sa = cos(a), sin(a)
        xs = [q[0] * ca + q[1] * sa for q in punkte]
        ys = [-q[0] * sa + q[1] * ca for q in punkte]

        # Streuung unterhalb einer Spurbreite kommt nicht von parallelen
        # Spuren, sondern daher, dass das Buendel als Ganzes aus der
        # Trassenmitte versetzt ist. Sie soll die Pille nicht verbreitern.
        spur = style.bundle_spacing * style.grid

        def spanne(werte: List[float]) -> Tuple[float, float]:
            lo, hi = min(werte), max(werte)
            if hi - lo < spur - 1e-6:
                # Nicht auf die Mitte zwischen den Werten legen, sondern auf
                # den aeussersten -- das ist die Lage des versetzten
                # Buendels. Sonst sitzt die Pille zwischen den Spuren und
                # trifft keine davon (Suedkreuz: S2-Familie auf +0.5).
                fest = max((lo, hi), key=abs)
                return fest, fest
            return lo, hi

        xlo, xhi = spanne(xs)
        ylo, yhi = spanne(ys)
        # An einer Achsenkreuzung bleibt die Pille quer zur Laengsrichtung
        # genau eine Spur dick. Ihre LAENGE misst sich dabei nicht an der
        # Projektion der Punkte auf die Pillenachse -- die faellt zu kurz aus
        # -- sondern an der Breite des ueberquerten Hauptbuendels, damit
        # wirklich alle dortigen Spuren ueberspannt werden.
        if laengs is not None:
            haupt = max(sorted(gewicht), key=lambda ax: gewicht[ax])
            hk = next(k for k in kurse if k % 90 == haupt)
            nx, ny = COMPASS[(hk + 90) % 360]
            quer = [q[0] * nx + q[1] * ny for q in punkte]
            # Die Pille laeuft schraeg zum ueberquerten Buendel. Um dessen
            # Breite senkrecht zu ueberbruecken, muss sie entsprechend
            # laenger sein -- bei 45 Grad um den Faktor 1/cos(45) = Wurzel 2.
            ux, uy = COMPASS[neben_kurs]
            schraeg = max(abs(ux * nx + uy * ny), 1e-6)
            halb = (max(quer) - min(quer)) / 2 / schraeg + r
            mx = (min(xs) + max(xs)) / 2
            my = (min(ys) + max(ys)) / 2
            if laengs == 0:
                xlo, xhi = mx - halb + r, mx + halb - r
                ylo = yhi = my
            else:
                xlo = xhi = mx
                ylo, yhi = my - halb + r, my + halb - r
        breite = xhi - xlo + 2 * r
        hoehe = yhi - ylo + 2 * r
        x0, y0 = xlo - r, ylo - r
        pillen[station_id] = (grad, x0, y0, breite, hoehe)
        dreh = f' transform="rotate({grad})"' if grad else ""
        svg.append(
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{breite:.1f}" '
            f'height="{hoehe:.1f}" rx="{r}" ry="{r}" fill="white" '
            f'stroke="#000000" stroke-width="{style.hub_pill_stroke}"{dreh}/>'
        )

    # darauf ein farbiger Punkt -- aber nur fuer Linien, die an diesem Hub
    # ENDEN. Durchfahrende Linien liegen unter der weissen Pille und
    # bekommen keinen Punkt.
    def richtung_an(line_id: str, punkt: Pt) -> Pt:
        """Laufrichtung der Linie an der Stelle `punkt` (Pixel)."""
        q = [px(p) for p in line_layout.paths[line_id].points]
        bestes, beste_dist = (1.0, 0.0), None
        for a, b in zip(q, q[1:]):
            dx_, dy_ = b[0] - a[0], b[1] - a[1]
            laenge = hypot(dx_, dy_)
            if laenge < 1e-9:
                continue
            t_ = max(0.0, min(1.0, ((punkt[0] - a[0]) * dx_ + (punkt[1] - a[1]) * dy_) / laenge ** 2))
            d_ = hypot(punkt[0] - a[0] - t_ * dx_, punkt[1] - a[1] - t_ * dy_)
            if beste_dist is None or d_ < beste_dist:
                bestes, beste_dist = (dx_ / laenge, dy_ / laenge), d_
        return bestes

    def erste_kreuzung(line_id: str, station_id: str, p0: Pt, u: Pt) -> Optional[Pt]:
        """Schnittpunkt mit der ZUERST gekreuzten Linie im Hub.

        `u` zeigt von aussen in den Hub hinein. Gesucht ist damit der
        Schnittpunkt mit dem kleinsten Parameter t entlang u -- also der,
        auf den die endende Linie zuerst trifft.
        """
        bestes_t: Optional[float] = None
        for lid2 in highlights:
            if lid2 == line_id:
                continue
            q2 = line_layout.paths[lid2].stations.get(station_id)
            if q2 is None:
                continue
            qq = px(q2)
            d = richtung_an(lid2, qq)
            kreuz = u[0] * d[1] - u[1] * d[0]
            if abs(kreuz) < 1e-6:
                continue                  # parallel, keine Kreuzung
            t_ = ((qq[0] - p0[0]) * d[1] - (qq[1] - p0[1]) * d[0]) / kreuz
            if t_ > 1e-6:
                # Die Kreuzung laege HINTER dem Linienende -- dort ist gar
                # keine Linie mehr, auf die der Punkt gesetzt werden koennte.
                continue
            if bestes_t is None or t_ < bestes_t:
                bestes_t = t_
        if bestes_t is None:
            return None
        return (p0[0] + bestes_t * u[0], p0[1] + bestes_t * u[1])

    for line_id, color in highlights.items():
        pline = layout.network.parsed[line_id]
        if pline.closed:
            continue                      # Ringlinie hat keine Endstation
        endet_hier = {pline.stations[0], pline.stations[-1]}
        pfad = line_layout.paths[line_id].points
        for station_id, point in line_layout.paths[line_id].stations.items():
            if stations[station_id].kind != "hub" or station_id not in endet_hier:
                continue
            x, y = px(point)
            # Auf die zuerst gekreuzte Linie setzen: der Punkt markiert, wo
            # die endende Linie im Hub das querende Buendel erreicht -- also
            # an dessen zugewandtem Rand, nicht in der Pillenmitte. `u` zeigt
            # von aussen in den Hub, bei einer Startstation entsprechend
            # entgegen der Fahrtrichtung.
            if station_id == pline.stations[0]:
                p0_, p1_ = px(pfad[0]), px(pfad[1])
            else:
                p0_, p1_ = px(pfad[-1]), px(pfad[-2])
            u_ = _norm((p0_[0] - p1_[0], p0_[1] - p1_[1]))
            treffer = erste_kreuzung(line_id, station_id, (x, y), u_)
            if treffer is not None:
                svg.append(
                    f'<circle cx="{treffer[0]:.1f}" cy="{treffer[1]:.1f}" '
                    f'r="{style.hub_dot_r}" fill="{color}"/>'
                )
                continue
            # Kein querendes Buendel: in die Pille ruecken, quer zu ihrer
            # Laengsrichtung auf die Mittellinie. Sitzt das Buendel versetzt
            # zur Trasse, laege der Punkt sonst am Pillenrand.
            if station_id in pillen:
                grad_p, px0, py0, pw, ph = pillen[station_id]
                ap_ = radians(grad_p)
                cap, sap = cos(ap_), sin(ap_)
                rx_, ry_ = x * cap + y * sap, -x * sap + y * cap
                if pw <= ph:
                    rx_ = px0 + pw / 2
                else:
                    ry_ = py0 + ph / 2
                x, y = rx_ * cap - ry_ * sap, rx_ * sap + ry_ * cap
            svg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.hub_dot_r}" '
                f'fill="{color}"/>'
            )

    # Endstationen hervorgehobener Linien: schwarzer Ring mit Farbpunkt, an
    # der tatsaechlichen (ggf. gebuendelt versetzten) Position der Linie --
    # nicht an der reinen Stationskoordinate.
    # Geschlossene Ringlinien haben keine Endstation.
    # Ist die Endstation zugleich ein Hub, hat sie bereits ihre Pille und
    # bekommt keinen zusaetzlichen Ring.
    for line_id, color in highlights.items():
        pline = layout.network.parsed[line_id]
        if pline.closed:
            continue
        line_stations = line_layout.paths[line_id].stations
        for station_id in (pline.stations[0], pline.stations[-1]):
            if stations[station_id].kind == "hub":
                continue
            point = line_stations.get(station_id)
            if point is None:
                continue
            x, y = px(point)
            svg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.terminus_ring_r}" '
                f'fill="white" stroke="#000000" '
                f'stroke-width="{style.terminus_ring_stroke}"/>'
            )
            svg.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{style.terminus_dot_r}" '
                f'fill="{color}"/>'
            )

    # Labels
    # Bei gebuendelten Stationen liegt die Beschriftung nicht an der
    # Trassenmitte, sondern am aeusseren Rand des Buendels -- sonst
    # ueberdeckt sie die aeusseren Spuren.
    station_punkte: Dict[str, List[Pt]] = defaultdict(list)
    for line_id in highlights:
        for sid, pt in line_layout.paths[line_id].stations.items():
            station_punkte[sid].append(px(pt))

    # Waagerechte Trassen: Beschriftung abwechselnd unter und ueber dem
    # Track, sonst draengen sich die Namen auf einer Seite. Gruppiert nach
    # der Hoehe der Trasse, innerhalb einer Gruppe nach x sortiert.
    wechsel: Dict[str, str] = {}
    reihen: Dict[float, List[Tuple[float, str]]] = defaultdict(list)
    for sid, koord in layout.measures.coords.items():
        ux, uy = _norm(station_dirs.get(sid, (1.0, 0.0)))
        if abs(uy) >= 0.35:
            continue                       # nicht waagerecht
        reihen[round(koord[1], 6)].append((koord[0], sid))
    for _, reihe in sorted(reihen.items()):
        # Bewusst ALLE Stationen der Reihe durchzaehlen, auch die mit
        # eigenem label_pos. Wuerde man die ueberspringen, verschoebe jede
        # festgesetzte Station die Parität aller folgenden -- ein einzelnes
        # label_pos wuerde dann den halben Rest der Strecke mitkippen.
        for i, (_, sid) in enumerate(sorted(reihe)):
            wechsel[sid] = "top" if i % 2 == 0 else "bottom"

    # Senkrechte Trassen: Beschriftung links, ausser sie liefe dort ueber
    # eine Linie -- dann nach rechts ausweichen.
    # Welche Stationen tragen ein Linien-Tag? Muss vor der Beschriftung
    # feststehen: das Tag zaehlt bei der Positionierung wie eine zusaetzliche
    # Zeile, ist aber etwas hoeher als eine Textzeile.
    endet: Dict[str, List[str]] = defaultdict(list)
    for line_id in sorted(highlights):
        pline = layout.network.parsed[line_id]
        if pline.closed:
            continue                      # Ringlinie hat keinen Endpunkt
        for sid in (pline.stations[0], pline.stations[-1]):
            if line_id not in endet[sid]:
                endet[sid].append(line_id)

    def gross_markiert(station_id: str) -> bool:
        """Traegt die Station Pille oder Endstationsring statt nur Punkt?"""
        return stations[station_id].kind == "hub" or station_id in endet

    def seit_hoehe(punkte: Sequence[Pt], ax_: float, ay_: float, y_: float) -> float:
        """Hoehe einer seitlichen Beschriftung (left/right).

        Liegt die Trasse schraeg, ist die Spur nicht nur waagerecht, sondern
        genauso weit SENKRECHT aus der Stationskoordinate versetzt -- das
        Symbol sitzt ja auf der Spur. Bisher folgte die Beschriftung nur in
        x und stand dadurch zu tief bzw. zu hoch (Koenigs Wusterhausen,
        Potsdam Hbf).

        Genommen wird die Mitte der Spurpunkte: ein gleichmaessig versetztes
        Buendel zieht die Beschriftung mit, ein zur Trassenmitte
        symmetrisches laesst sie stehen.
        """
        if abs(ax_) <= 0.3 or abs(ay_) > 0.3 or not punkte:
            return y_
        return sum(q[1] for q in punkte) / len(punkte)

    def marker_luft(station_id: str, ax_: float, ay_: float) -> float:
        """Zusatzabstand bei seitlicher Beschriftung an grossen Symbolen.

        Hubs tragen eine Pille, Endstationen einen Ring -- beide reichen
        weiter ueber die Trasse hinaus als ein Stationspunkt. Nur bei
        `left`/`right`, wo die Beschriftung direkt daneben sitzt.
        """
        if abs(ax_) <= 0.3 or abs(ay_) > 0.3:
            return 0.0
        return style.label_marker_luft if gross_markiert(station_id) else 0.0

    def badge_zusatz(station_id: str, ax_: float, ay_: float) -> float:
        """Zusatzhoehe des Linien-Tags unter dem Textblock.

        Nur bei `left` und `right`. Dort sitzt der Textblock mittig zur
        Station, das Tag gehoert also in die Blockhoehe hinein. Bei `top`,
        `bottom` und den vier Schraeglagen bleibt es aussen vor -- der
        Stationsname soll auf seiner gewohnten Hoehe neben dem Punkt stehen
        und das Tag einfach darunter haengen.

        Gezaehlt wird es dabei wie eine gewoehnliche Textzeile, NICHT mit
        seiner groesseren tatsaechlichen Hoehe: so bleibt der weisse
        Zwischenraum zwischen Name und Tag auf Hoehe der Station.
        """
        if station_id not in endet:
            return 0.0
        if abs(ax_) <= 0.3 or abs(ay_) > 0.3:
            return 0.0
        return style.label_font * 1.15

    segmente: List[Tuple[Pt, Pt]] = []
    for line_id in highlights:
        q = [px(p) for p in line_layout.paths[line_id].points]
        segmente.extend(zip(q, q[1:]))
    halbe_linie = style.highlight_line_width / 2

    def label_box(
        station_id: str, pos: str, extra_dx: float = 0.0
    ) -> Tuple[float, float, float, float]:
        """Umriss des Beschriftungsblocks, genau wie er gezeichnet wird."""
        bx, by = px(layout.measures.coords[station_id])
        ax_, ay_ = LABEL_RICHTUNG[pos]
        weit_ = max(
            ((q[0] - bx) * ax_ + (q[1] - by) * ay_
             for q in station_punkte.get(station_id, ())),
            default=0.0,
        )
        bx += ax_ * (weit_ + marker_luft(station_id, ax_, ay_))
        by = seit_hoehe(station_punkte.get(station_id, ()), ax_, ay_, by + ay_ * weit_)
        dx_, dy_, anker_ = label_offset(pos, style.label_clearance)
        zeilen = stations[station_id].label.split("\n")
        versatz_ = (len(zeilen) - 1) * style.label_font * 1.15
        ddx_, ddy_ = mehrzeilen_versatz(
            ax_, ay_, versatz_ + badge_zusatz(station_id, ax_, ay_)
        )
        breite = label_breite(stations[station_id].label, style.label_font, style.label_zusatz_kleiner)
        lx, ly = bx + dx_ + ddx_ + extra_dx, by + dy_ + ddy_
        if anker_ == "middle":
            x0 = lx - breite / 2
        elif anker_ == "end":
            x0 = lx - breite
        else:
            x0 = lx
        return (
            x0,
            ly - 0.75 * style.label_font,
            x0 + breite,
            ly + versatz_ + 0.25 * style.label_font,
        )

    seitlich: Dict[str, str] = {}
    for sid in layout.measures.coords:
        if stations[sid].label_pos is not None or sid in wechsel:
            continue
        ux_, _uy = _norm(station_dirs.get(sid, (1.0, 0.0)))
        if abs(ux_) >= 0.35:
            continue                       # nicht senkrecht
        kasten = label_box(sid, "left")
        if any(_segment_trifft_box(a, b, kasten, halbe_linie) for a, b in segmente):
            seitlich[sid] = "right"

    label_lage: Dict[str, Tuple[float, float, str, Pt]] = {}
    for station_id, coord in layout.measures.coords.items():
        x, y = px(coord)
        richtung = station_dirs.get(station_id, (1.0, 0.0))
        pos = (
            stations[station_id].label_pos
            or wechsel.get(station_id)
            or seitlich.get(station_id)
        )
        if station_id in labels:
            dx, dy, anchor = labels[station_id]
        elif pos is not None:
            dx, dy, anchor = label_offset(pos, style.label_clearance)
        else:
            dx, dy, anchor = auto_label(richtung, style.label_clearance)

        ax, ay = LABEL_RICHTUNG[pos] if pos else label_aussen(richtung)
        label_lines = stations[station_id].label.split("\n")
        versatz = (len(label_lines) - 1) * style.label_font * 1.15
        punkte = station_punkte.get(station_id, ())

        if (
            stations[station_id].kind == "hub"
            and station_id not in labels
            and abs(ax) > 0.3
            and abs(ay) > 0.3
        ):
            # Kreuzungs-Hub auf einer Schraeglage: an einem Kreuz laeuft in
            # BEIDE Achsen ein Buendel, eine Aufhaengung auf der 45-Grad-
            # Normalen landet also zwangslaeufig auf Linien. Die Ecke wird
            # deshalb aus den beiden Achsenlagen zusammengesetzt -- y wie bei
            # top/bottom, x wie bei left/right. Damit liegt die Beschriftung
            # ausserhalb beider Buendel, im freien Quadranten, und der
            # Abstand ist derselbe wie bei einer achsparallelen Lage.
            sx = 1.0 if ax > 0 else -1.0
            sy = 1.0 if ay > 0 else -1.0
            x += sx * max(((q[0] - x) * sx for q in punkte), default=0.0)
            y += sy * max(((q[1] - y) * sy for q in punkte), default=0.0)
            dx = sx * style.label_clearance
            dy = (
                style.label_clearance + 7.0
                if sy > 0
                else -style.label_clearance - 1.0
            )
            anchor = "start" if sx > 0 else "end"
            # Waagerecht steht der Block schon frei; senkrecht gilt dieselbe
            # Regel wie bei top/bottom.
            ddx, ddy = mehrzeilen_versatz(
                0.0, sy, versatz + badge_zusatz(station_id, ax, ay)
            )
        else:
            weit = max(
                ((q[0] - x) * ax + (q[1] - y) * ay for q in punkte),
                default=0.0,
            )
            # Auch negativ anwenden: liegt das ganze Buendel auf der anderen
            # Seite der Trassenmitte, laeuft auf der Label-Seite gar keine
            # Linie mehr und die Beschriftung rueckt entsprechend naeher heran.
            x += ax * (weit + marker_luft(station_id, ax, ay))
            y = seit_hoehe(punkte, ax, ay, y + ay * weit)
            ddx, ddy = mehrzeilen_versatz(
                ax, ay, versatz + badge_zusatz(station_id, ax, ay)
            )

        dx += ddx
        dy += ddy
        fein = LABEL_VERSATZ.get(station_id)
        if fein:
            dx += fein[0]
            dy += fein[1]
        zeilen_hoehe = style.label_font * 1.15
        if len(label_lines) == 1:
            text = esc(label_lines[0])
        else:
            line_height = style.label_font * 1.15

            def tspan(i: int, line: str) -> str:
                # Ein geklammerter Zusatz bekommt eine eigene, kleinere
                # Schriftgroesse; die Grundschrift steht am <text>.
                groesse = (
                    f' font-size="{zeilen_font(line, style):g}"'
                    if ist_zusatz(line)
                    else ""
                )
                dy_ = 0.0 if i == 0 else line_height
                return (
                    f'<tspan x="{x+dx:.1f}" dy="{dy_:.1f}"{groesse}>'
                    f'{esc(line)}</tspan>'
                )

            text = "".join(tspan(i, line) for i, line in enumerate(label_lines))

        svg.append(
            f'<text x="{x+dx:.1f}" y="{y+dy:.1f}" '
            f'font-size="{style.label_font}" font-weight="bold" '
            f'fill="{style.label_fill}" text-anchor="{anchor}">{text}</text>'
        )
        # Fuer die Linien-Plaketten: wo endet der Textblock?
        label_lage[station_id] = (
            x + dx,
            y + dy + (len(label_lines) - 1) * zeilen_hoehe,
            anchor,
            (ax, ay),
        )

    # Linien-Plaketten an den Endpunkten: farbiges Oval mit dem Liniennamen,
    # in einer Reihe unter den Stationsnamen gesetzt. Geschlossene Ringlinien
    # haben keinen Endpunkt und bekommen keine.
    def lage_anker(station_id: str, pos_: str) -> Tuple[float, float, str]:
        """Wo saesse die Grundlinie einer einzeiligen Beschriftung in `pos_`?"""
        ax_, ay_ = LABEL_RICHTUNG[pos_]
        punkte_ = station_punkte.get(station_id, ())
        dx_, dy_, anker_ = label_offset(pos_, style.label_clearance)
        x_, y_ = px(layout.measures.coords[station_id])
        if stations[station_id].kind == "hub" and abs(ax_) > 0.3 and abs(ay_) > 0.3:
            sx = 1.0 if ax_ > 0 else -1.0
            sy = 1.0 if ay_ > 0 else -1.0
            x_ += sx * max(((q[0] - x_) * sx for q in punkte_), default=0.0)
            y_ += sy * max(((q[1] - y_) * sy for q in punkte_), default=0.0)
            dx_ = sx * style.label_clearance
            dy_ = (
                style.label_clearance + 7.0 if sy > 0 else -style.label_clearance - 1.0
            )
            anker_ = "start" if sx > 0 else "end"
        else:
            weit_ = max(
                ((q[0] - x_) * ax_ + (q[1] - y_) * ay_ for q in punkte_), default=0.0
            )
            x_ += ax_ * (weit_ + marker_luft(station_id, ax_, ay_))
            y_ = seit_hoehe(punkte_, ax_, ay_, y_ + ay_ * weit_)
        return x_ + dx_, y_ + dy_, anker_

    # Liegen zwei Trassen auf derselben Diagonalen, steht die Beschriftung
    # schraeg -- darunter waere kein Platz mehr fuer das Tag. Es wandert dann
    # auf die GEGENUEBERLIEGENDE Ecke, die Beschriftung bleibt wo sie ist.
    gegenecke = {
        "top_left": "bottom_right", "bottom_right": "top_left",
        "top_right": "bottom_left", "bottom_left": "top_right",
    }

    def badge_text(line_id: str) -> str:
        # "S85" -> "S 85", wie auf den Liniensignets
        return line_id[0] + " " + line_id[1:] if len(line_id) > 1 else line_id

    def badge_breite(line_id: str) -> float:
        return len(badge_text(line_id)) * style.badge_font * 0.50 + 2 * style.badge_padding

    for sid, ids in sorted(endet.items()):
        if sid not in label_lage:
            continue
        lx, ly, anker, (ax_l, ay_l) = label_lage[sid]
        schraeg = (
            sid in BADGE_GEGENECKE and abs(ax_l) > 0.3 and abs(ay_l) > 0.3
        )
        if schraeg:
            pos_l = max(
                LABEL_RICHTUNG,
                key=lambda k: ax_l * LABEL_RICHTUNG[k][0] + ay_l * LABEL_RICHTUNG[k][1],
            )
            lx, ly, anker = lage_anker(sid, gegenecke[pos_l])
            # Das Tag tritt an die Stelle einer Textzeile, also mittig zu
            # deren Grundlinie statt darunter.
            oben = ly - 0.35 * style.label_font - style.badge_height / 2
        else:
            oben = ly + style.badge_gap + 1.0
        fein_b = BADGE_VERSATZ.get(sid)
        if fein_b:
            lx += fein_b[0]
            oben += fein_b[1]
        breiten = [badge_breite(l) for l in ids]
        gesamt = sum(breiten) + style.badge_gap * (len(ids) - 1)
        if anker == "middle":
            links = lx - gesamt / 2
        elif anker == "end":
            links = lx - gesamt
        else:
            links = lx
        for line_id, breite in zip(ids, breiten):
            svg.append(
                f'<rect x="{links:.1f}" y="{oben:.1f}" width="{breite:.1f}" '
                f'height="{style.badge_height:.1f}" '
                f'rx="{style.badge_height / 2:.1f}" ry="{style.badge_height / 2:.1f}" '
                f'fill="{highlights[line_id]}"/>'
            )
            svg.append(
                f'<text x="{links + breite / 2:.1f}" '
                f'y="{oben + style.badge_height / 2 + style.badge_font * 0.35:.1f}" '
                f'font-size="{style.badge_font}" font-weight="500" fill="white" '
                f'text-anchor="middle">{esc(badge_text(line_id))}</text>'
            )
            links += breite + style.badge_gap

    svg.append("</svg>")
    return "\n".join(svg)


# ============================================================================
# REPORT
# ============================================================================

def print_layout_report(
    lines: Mapping[str, TurnLine],
    layout: LayoutResult,
    stations: Mapping[str, Station],
) -> None:
    print(f"Linien: {len(lines)}")
    print(f"Stationen: {len(layout.measures.coords)}")
    print(f"Korridore: {len(layout.tracks.corridor_paths)}")
    print("Startwinkel:")
    for lid in lines:
        mode = "definiert" if lines[lid].start is not None else "automatisch"
        print(f"  {lid}: {layout.network.starts[lid]} Grad ({mode})")

    # gemeinsame Korridore
    corridor_users: Dict[frozenset[str], List[str]] = defaultdict(list)
    for lid, pline in layout.network.parsed.items():
        for seg in pline.segments:
            corridor_users[_pair_key(seg.a, seg.b)].append(lid)

    shared = {k: v for k, v in corridor_users.items() if len(v) > 1}
    if shared:
        print("Gemeinsame Korridore:")
        for pair, users in sorted(shared.items(), key=lambda item: sorted(item[0])):
            names = sorted(stations[sid].name for sid in pair)
            print(f"  {names[0]} <-> {names[1]}: {', '.join(users)}")

    elastic_groups: Dict[Tuple[str, str], float] = {}
    for (lid, seg_idx, leg_idx), value in layout.measures.leg_lengths.items():
        seg = layout.network.parsed[lid].segments[seg_idx]
        spec = seg.legs[leg_idx].length
        if spec.elastic:
            group = spec.group or f"seg{seg_idx}_leg{leg_idx}"
            elastic_groups[(lid, group)] = value

    if elastic_groups:
        print("Geloeste elastische Laengen:")
        for (lid, group), value in sorted(elastic_groups.items()):
            print(f"  {lid} / {group}: {value:.3f}")


# ============================================================================
# NETZDEFINITION
# ============================================================================

# Alle Stationen des Netzes, alphabetisch nach Anzeigename. `id` wird
# in Strecken und Linien referenziert, nie am Nutzer sichtbar.
STATIONS: Dict[str, Station] = _station_registry([
    Station("adlershof", "Adlershof", kind="hub", label_pos="top_right"),
    Station("ahrensfelde", "Ahrensfelde", label_pos="right"),
    Station("alexanderplatz", "Alexanderplatz", label="Alexander-\nplatz", label_pos="top"),
    Station("alt_reinickendorf", "Alt-Reinickendorf"),
    Station("altglienicke", "Altglienicke", label_pos="top_left"),
    Station("anhalter_bahnhof", "Anhalter Bahnhof", label_pos="right"),
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
    Station("koellnische_heide", "Köllnische Heide", label="Köllnische\nHeide"),
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
    Station("messe_nord_zob", "Messe Nord/ZOB", label_pos="left"),
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
    Station("potsdamer_platz", "Potsdamer Platz", label_pos="right", kind="hub"),
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
    Station("wilhelmsruh", "Wilhelmsruh"),
    Station("wittenau", "Wittenau"),
    Station("wollankstrasse", "Wollankstraße"),
    Station("wuhletal", "Wuhletal"),
    Station("wuhlheide", "Wuhlheide"),
    Station("yorckstrasse", "Yorckstraße"),
    Station("yorckstrasse_grossgoerschenstrasse", "Yorckstraße (Großgörschenstraße)", label="Yorckstraße\n(Großgörschenstraße)", label_pos="top_left"),
    Station("zehlendorf", "Zehlendorf"),
    Station("zepernick", "Zepernick"),
    Station("zeuthen", "Zeuthen", label_pos="top_right"),
    Station("zoologischer_garten", "Zoologischer Garten", label="Zoologischer\nGarten", label_pos="bottom"),
    Station("gartenfeld", "Gartenfeld", label_pos="top_right"),
    Station("siemensstadt", "Siemensstadt", label_pos="top_right"),
    Station("wernerwerk", "Wernerwerk", label_pos="top_right"),
    Station("perlegerberger_bruecke", "Perlegerberger Brücke", label="Perlegerberger\nBrücke"),
    Station("gleisdreieck", "Gleisdreieck"),
])

LINE_COLORS: Dict[str, str] = {
    "S1": "#DA6BA2",
    "S2": "#007734",
    "S25": "#007734",
    "S26": "#007734",
    "S3": "#0066AD",
    "S5": "#EC7405",
    "S6": "#3EA76B",   # Siemensbahn; Wert per Auge aus dem Linien-Piktogramm
    "S7": "#816DA6",
    "S75": "#816DA6",
    "S8": "#66AA22",
    "S85": "#66AA22",
    "S9": "#992746",
    "S15": "#E98ABF",
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
    FixPath(1.4), 
    "savignyplatz",
    FixPath(1.4), 
    "zoologischer_garten",
    FixPath(1.4), 
    "tiergarten",
    FixPath(1.4), 
    "bellevue",
    FixPath(2.2), 
    "hauptbahnhof",
    FixPath(1.8), 
    "friedrichstrasse",
    FixPath(2.0), 
    "hackescher_markt",
    FixPath(1.4), 
    "alexanderplatz",
    FixPath(1.4), 
    "jannowitzbruecke",
    FixPath(1.4), 
    "ostbahnhof",
    FixPath(1.4), 
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
    "perlegerberger_bruecke", FlexPath(),
    Turn(45), FlexPath(),
    "hauptbahnhof", 
    FlexPath(), Turn(-45), FlexPath(), Turn(45, radius=1.2), FlexPath(preferred=1.0),
    "potsdamer_platz",
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
    "schoeneberg", FlexPath(),
    "innsbrucker_platz", FlexPath(),
    "bundesplatz", FlexPath(), Turn(45, radius=1.2),          # Ecke Suedwest
    "heidelberger_platz", Turn(45, radius=1.2), FlexPath(),
    "hohenzollerndamm", FlexPath(),
    "halensee", FixPath(3.2),
    "westkreuz", FixPath(3.2),
    "messe_nord_zob", FlexPath(),
    "westend", FlexPath(), Turn(90), FlexPath(),              # Ecke Nordwest
    "jungfernheide", FlexPath(),
    "beusselstrasse", FlexPath(),
    "westhafen", FlexPath(),
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

_SIEMENSBAHN: List[Step] = [
    "gartenfeld", FixPath(1.8),
    "siemensstadt", FixPath(1.8),
    "wernerwerk", FixPath(2.2), 
    Turn(-45, radius=1.2),
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
_KURVE_OSTKREUZ_NOELDNERPLATZ: List[Step] = [FixPath(1.8), Turn(-45), FixPath(2.0)]

# Stadtbahn -> Schlesische Bahn (S3)
_KURVE_OSTKREUZ_RUMMELSBURG: List[Step] = [FixPath(1.8), Turn(45), FixPath(2.0)]

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
print(_ABSCHNITT_GESUNDBRUNNEN_WOLLANKSTRASSE)
print(_ABSCHNITT_GESUNDBRUNNEN_BORNHOLMER)
print(_ABSCHNITT_WOLLANKSTRASSE_BORNHOLMER)

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
# Gruenden schraeg, und an Blankenburg waere die Gegenecke von Nachbarlabels
# belegt. Dort gehoert das Tag schlicht unter den Namen.
BADGE_GEGENECKE: frozenset = frozenset({"wannsee", "wildau"})


# Manuelle Feinkorrekturen in Pixeln, wo die Automatik nicht hinkommt.
# LABEL_VERSATZ verschiebt Name UND Tag, BADGE_VERSATZ nur das Tag.
LABEL_VERSATZ: Dict[str, Pt] = {
    # Beide Kreuze tragen ihr Label als Ecke unmittelbar ueber der Stadtbahn
    # bzw. dem Ring -- das Tag darunter laege sonst auf den Linien.
    "westkreuz": (0.0, -18.0),
    "gesundbrunnen": (0.0, -18.0),
}

BADGE_VERSATZ: Dict[str, Pt] = {
    # Hier bleibt der Name, wo er ist; nur das Tag wandert unter den Ring
    # und auf die andere Seite des Nord-Sued-Tunnels.
    # Der Tunnel liegt hier um eine halbe Spur nach rechts versetzt, das Tag
    # rueckt deshalb um dieselben 6 px mit.
    "suedkreuz": (-46.0, 37.0),
    # Sonst haengt das Tag an der Siemensbahn-Diagonalen.
    "gartenfeld": (12.0, 0.0),
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
    "ring_zulauf_jungfernheide": Corridor(
        # S6 soll ab Jungfernheide aussen liegen; der Bogen davor steckt in
        # wernerwerk -> jungfernheide und muss den Versatz mitbringen.
        steps=["wernerwerk", "jungfernheide"],
        offsets={"S6": -1.0},
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
    "hbf_zulauf_westhafen": Corridor(
        # S6 und S15 laufen ab Perleberger Bruecke gemeinsam zum
        # Hauptbahnhof. Ohne Vorgabe uebernehmen sie ihre Spur erst in der
        # Kurve auf DIESER Kante -- davor liegen beide mittig, also
        # uebereinander. Die Zulaufkanten haben eigene Kurven; dort muss der
        # Versatz schon stehen.
        steps=["westhafen", "perlegerberger_bruecke", "hauptbahnhof"],
        offsets={"S6": 0.5, "S1": -0.5},
    ),
    "hbf_potsdamer_platz": Corridor(
        # Einschwenken zum Potsdamer Platz: S15 auf dieselbe Spur wie die S1
        # (-0.5), S6 daneben auf -1.5. Sonst vergibt die Automatik
        # alphabetisch und legt die S15 auf die Seite der S2.
        steps=["hauptbahnhof", "potsdamer_platz"],
        offsets={"S15": 0.5, "S6": 1.5},
    ),
    "hbf_zulauf_wedding": Corridor(
        steps=["wedding", "perlegerberger_bruecke"],
        offsets={"S1": -0.5},
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
            FixPath(1.2),
            "anhalter_bahnhof", 
            *_reversed([FixPath(1.15), Turn(-45), FixPath(0.6)]),  # SW aus dem Tunnel
            *_reversed(_slice(_WANNSEE_BAHN, "zehlendorf", "yorckstrasse_grossgoerschenstrasse")),
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
    "S6": TurnLine(
        color=LINE_COLORS["S6"],
        start=135,
        steps=[
            *_SIEMENSBAHN,
            FixPath(1.8),
            *_slice(_RING, "jungfernheide", "westhafen"),
            FixPath(1.6), Turn(45), FlexPath(),
            *_NORD_SUED_TUNNEL_HBF,
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
# MAIN
# ============================================================================

if __name__ == "__main__":
    output = FilePath("outputs/gleiskarte.svg")
    output.parent.mkdir(parents=True, exist_ok=True)

    # Phase 2-4: Netz parsen, ausrichten, Laengen loesen, Gleise bauen
    layout = solve_layout(TRACK_LINES, stations=STATIONS, cfg=CFG)

    # Phase 4b: alle Linien fuehren und gegeneinander buendeln
    line_layout = build_line_layout(
        layout,
        TRACK_LINES,
        bundle_spacing=CFG.style.bundle_spacing,
        families={lid: ln.family for lid, ln in TRACK_LINES.items() if ln.family},
        corridors=CORRIDORS,
    )

    # Phase 5: das geloeste Layout zeichnen. Alle Linien sind farbig, die
    # graue Korridorschicht darunter waere nur noch Rauschen.
    svg_text = build_track_svg(
        layout,
        stations=STATIONS,
        highlight_lines={lid: LINE_COLORS[lid] for lid in TRACK_LINES},
        line_layout=line_layout,
        # Graue Hilfstrassen sind ausgeblendet; zum Pruefen des
        # Versatzes einfach wieder auf True setzen.
        draw_corridors=False,
        style=CFG.style,
        label_override=LABEL_OVERRIDE,
    )
    output.write_text(svg_text, encoding="utf-8")

    print_layout_report(TRACK_LINES, layout, STATIONS)
    print(f"Geschrieben: {output}")
