"""
Netzdefinition Berlin: Ausbaustufe 2030.

Ausfuehren erzeugt die Karte:

    python3 berlin_2030.py [ziel.svg]   ->  outputs/berlin_sbahn_2030.svg

Erste Stufe nach dem Bestand. Sie beschreibt nicht das ganze Netz noch
einmal, sondern nur den UNTERSCHIED zu `berlin_2026` -- alles Uebrige erbt
`Net.derive()` am Ende der Datei.

Inhalt:

  * Die wiederaufgebaute Siemensbahn: die einzige Massnahme mit konkretem
    Bauzeitplan und die einzige, die keine andere voraussetzt. Der Tunnel
    nach Sueden (BA2 der City-S-Bahn) kommt erst in `berlin_2030plus`; bis
    dahin enden S6 und S15 am Hauptbahnhof.
  * Die neue S86 von Gruenau nach Buch -- kein Neubau, sondern eine
    zusaetzliche Linie auf vorhandener Strecke.
  * Der Spandauer Ast wechselt die Linie: die S3 endet in Charlottenburg,
    die S75 faehrt statt am Ostbahnhof erst in Spandau zu Ende.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path as FilePath
from typing import Dict, List

from netmap import (
    CFG, REMOVE, Corridor, FixPath, FlexPath, Station, Step, TrainGroup, Turn,
    TurnLine, planned, splice, write_map,
)

import berlin_2026 as bestand

# Das Netz, auf dem dieses hier aufbaut. Alles, was unten nicht ausdruecklich
# geaendert wird, kommt von hier.
#
# Zur Abgrenzung: was ueber VORGAENGER geht, sind NETZDATEN -- Linien,
# Stationen, Korridore, Farben des Vorgaengers. Was direkt aus `bestand`
# kommt (`_RING`, `_slice`, `_NORD_SUED_TUNNEL_HBF`), sind BAUSTEINE: die
# Strecken-Konstanten des Berliner Netzes, die fuer jede Stufe dieselben
# sind und nicht mitwandern.
VORGAENGER = bestand.NET


# ============================================================================
# SIEMENSBAHN -- S6 und Perleberger Bruecke
# ============================================================================

SIEMENSBAHN_STATIONS: List[Station] = [
    Station("gartenfeld", "Gartenfeld", label_pos="left"),
    Station("siemensstadt", "Siemensstadt"),
    Station("wernerwerk", "Wernerwerk"),
    # Liegt auf dem gemeinsamen Stueck von S6 und S15 zum Hauptbahnhof.
    # Klammern setzt planned(), hier steht nur der Umbruch.
    Station("perlegerberger_bruecke", "Perlegerberger Brücke",
            label="Perlegerberger\nBrücke"),
]

_SIEMENSBAHN: List[Step] = [
    "gartenfeld", FixPath(1.8),
    "siemensstadt", FixPath(1.8),
    "wernerwerk", FixPath(2.2),
    Turn(-45, radius=1.2),
]

# Auf beiden Kurven zum Hauptbahnhof fahren jetzt drei Linien nebeneinander
# statt einer. Der gezeichnete Bogen wird je Spur um deren Versatz gegen die
# Trassenmitte korrigiert, damit die Spuren konzentrisch bleiben.
#
# Bei Wedding wechselt das ganze Buendel im Bogen die Spur; sein Mittelwert
# liegt dort auf der Trassenmitte, die innerste Spur faehrt also genau den
# Grundradius -- der bleibt deshalb der des Bestands.
_R_WEDDING = 0.4                              # Ring -> Perleberger Bruecke
# Vor dem Hauptbahnhof behaelt jede Linie ihre Spur, die innerste liegt eine
# Spurbreite innen. Um genau die ist der Grundradius groesser als im Bestand,
# damit sie denselben Bogen faehrt wie die S15 dort 2026 als einzige Linie.
_R_HBF = 0.8 + CFG.style.bundle_spacing       # Perleberger Bruecke -> Hbf

# Zulauf zum Hauptbahnhof: ab der Perleberger Bruecke auf die Trasse, die im
# Bestand schon die S15 benutzt. Hier endet die S6 -- der Tunnel weiter zum
# Potsdamer Platz gehoert in die naechste Stufe.
_HBF_ZULAUF: List[Step] = [
    "perlegerberger_bruecke", FlexPath(),
    *splice(bestand._NORD_SUED_TUNNEL_HBF, [Turn(45)], [Turn(45, radius=_R_HBF)]),
]

S6 = TurnLine(
    color=VORGAENGER.colors["S6"],
    start=135,
    steps=[
        *_SIEMENSBAHN,
        FixPath(1.4),
        *bestand._slice(bestand._RING, "jungfernheide", "westhafen"),
        FixPath(1.6), Turn(45), FlexPath(),
        *_HBF_ZULAUF,
    ],
    direction="Gartenfeld -> Hauptbahnhof",
)

# Die S15 faehrt dieselbe Kurve und haelt ebenfalls an der Perleberger
# Bruecke. Angesteuert wird die Stelle ueber ihre Modifier, weil dort keine
# Station steht, an der man sich festhalten koennte.
_S15_MIT_BRUECKE = VORGAENGER.splice_line(
    "S15",
    [Turn(-135, radius=0.4), FlexPath(), Turn(45), FlexPath()],
    [Turn(-135, radius=_R_WEDDING), FlexPath(),
     "perlegerberger_bruecke", FlexPath(), Turn(45, radius=_R_HBF), FlexPath()],
)


# ============================================================================
# NORDBAHN -- S15 bis Frohnau, S85 zum Hauptbahnhof
# ============================================================================
#
# Die S15 wird ueber den Gesundbrunnen hinaus auf die Nordbahn verlaengert
# und faehrt bis Frohnau. Den Ast dort gibt die S85 dafuer ab -- sie bleibt
# im Sueden auf dem Ring, faehrt an Gesundbrunnen und Wedding vorbei und
# endet mit S15 und S6 am Hauptbahnhof.
#
# Die Nordbahn selbst bleibt in dieser Stufe, wie sie im Bestand steht; erst
# `berlin_2030plus` macht sie elastisch, wenn die S15 ganz durchgebunden wird.

# Frohnau -> ... -> Wollankstrasse -> Bornholmer -> Gesundbrunnen, also der
# Nordast rueckwaerts. Die Abstaende sind dieselben wie bei der S1, die
# dieselben Kanten befaehrt.
_S15_NORDBAHN: List[Step] = [
    *bestand._reversed(bestand._slice(bestand._NORD_BAHN, "wollankstrasse", "frohnau")),
    FixPath(2.4), "bornholmer_strasse",
    FixPath(2.6),
]

S15 = replace(
    _S15_MIT_BRUECKE,
    start=None,                      # ergibt sich aus der S1 auf der Nordbahn
    steps=[
        *_S15_NORDBAHN,
        "gesundbrunnen",
        # Knick exakt im Bahnhof: FixPath(0.0) laesst dem Bein vor dem Turn
        # keine Laenge, damit Wedding nicht mitwandert. Nur so kann die S15
        # hier abbiegen, obwohl sie beide Nachbarkanten mit geradeaus
        # fahrenden Linien teilt (S1/S2/S25 bzw. S41/S42).
        FixPath(0.0), Turn(90),
        *_S15_MIT_BRUECKE.steps[1:],
    ],
    direction="Frohnau -> Hauptbahnhof",
)

# Die S85 gibt die Nordbahn ab. Statt am Gesundbrunnen auf den Ring zu
# wechseln, bleibt sie darauf bis Wedding und biegt dort mit der S15 zum
# Hauptbahnhof ab -- dieselbe Kurve, dieselben Halte, nur in Gegenrichtung
# geschrieben.
_S85_AB_TREPTOWER = list(VORGAENGER.lines["S85"].steps)
_S85_AB_TREPTOWER = _S85_AB_TREPTOWER[_S85_AB_TREPTOWER.index("treptower_park") + 1:]

S85 = replace(
    VORGAENGER.lines["S85"],
    start=None,                      # ergibt sich aus S15/S6 am Hauptbahnhof
    steps=[
        *bestand._reversed(_S15_MIT_BRUECKE.steps),
        # ... und ab Gesundbrunnen im Uhrzeigersinn ueber den Ring wie bisher.
        *bestand._slice(bestand._RING, "gesundbrunnen", "treptower_park")[1:],
        *_S85_AB_TREPTOWER,
    ],
    direction="Hauptbahnhof -> Flughafen BER",
)

# Im Bestandsnetz steht die Ringkante Westhafen -- Wedding auf FixPath(4.4).
# Das geht dort, weil der Tunnelast am Hauptbahnhof endet und damit eine
# Sackgasse ist. Ab hier schliessen S6 (ueber Westhafen) und S15 (ueber
# Wedding) an der Perleberger Bruecke ein Dreieck mit genau dieser Kante --
# und eine starre Seite laesst es sich nicht mehr schliessen. Deshalb hier
# wieder elastisch; der Solver findet dann rund 4.4 statt 5.0.
RING_LINES: Dict[str, TurnLine] = {
    lid: VORGAENGER.splice_line(
        lid,
        ["westhafen", FixPath(4.4), "wedding"],
        ["westhafen", FlexPath(), "wedding"],
    )
    for lid in ("S41", "S42")
}


SIEMENSBAHN_CORRIDORS: Dict[str, Corridor] = {
    "ring": replace(
        VORGAENGER.corridors["ring"],
        # Die S6 bleibt noerdlich des Rings, auf der Seite, von der sie
        # kommt. Ihre beiden Ringkanten sind unten einzeln geregelt.
        offsets={**VORGAENGER.corridors["ring"].offsets, "S6": -1.0},
    ),
    "ring_zulauf_jungfernheide": Corridor(
        # Die S46 endet in Westend, oestlich davon ist die aeussere Spur
        # (-1.0) durchgehend frei. Die S6 nimmt sie schon im Bogen und
        # braucht deshalb keinen Schwenk mehr.
        steps=["wernerwerk", "jungfernheide"],
        offsets={"S6": -1.0},
    ),
    "ring_jungfernheide_westhafen": Corridor(
        steps=["jungfernheide", "beusselstrasse", "westhafen"],
        offsets={"S42": 0.0, "S41": 1.0, "S6": -1.0},
    ),
    "hbf_zulauf_westhafen": Corridor(
        # S6 und S15 laufen ab Perleberger Bruecke gemeinsam zum
        # Hauptbahnhof. Ohne Vorgabe uebernehmen sie ihre Spur erst in der
        # Kurve auf DIESER Kante -- davor liegen beide mittig, also
        # uebereinander.
        # Am Hauptbahnhof liegen die drei nebeneinander: S6 links, S15 in
        # der Mitte, S85 rechts.
        steps=["westhafen", "perlegerberger_bruecke", "hauptbahnhof"],
        offsets={"S6": 1.0, "S1": 0.0, "S8": -1.0},
    ),
    "hbf_zulauf_wedding": Corridor(
        # Zwischen Wedding und der Perleberger Bruecke liegt die S85 noch
        # aussen -- sie kommt von dort auf dem Ring und hat bis Gesundbrunnen
        # keine Kurve mehr, an der sie die Spur wechseln koennte. Auf die
        # rechte Spur schwenkt sie erst im Bogen vor dem Hauptbahnhof.
        steps=["wedding", "perlegerberger_bruecke"],
        offsets={"S8": 1.0, "S1": 0.0},
    ),
    "ring_gesundbrunnen_wedding": Corridor(
        # Auf diesem einen Ringstueck fahren S15 und S85 nebeneinander: die
        # S85 aussen neben der S42, die S15 noch eine Spur weiter aussen.
        # Ohne diese Vorgabe laegen beide auf der gemeinsamen Gastspur des
        # Ring-Korridors uebereinander.
        steps=["wedding", "gesundbrunnen"],
        offsets={"S42": 0.0, "S41": 1.0, "S8": -1.0, "S1": -2.0},
    ),
}


# ============================================================================
# S86 -- Gruenau <> Buch
# ============================================================================
#
# Eine neue Linie auf vorhandener Strecke: sie faehrt von Gruenau die
# Goerlitzer Bahn hinauf, ueber den Ostring und die Bornholmer Strasse auf
# die Stettiner Bahn und weiter bis Buch. Bis Blankenburg ist das Meter fuer
# Meter der Weg der S8 -- `family="S8"` legt sie dort exakt auf deren Spur,
# statt sie als eigene Familie danebenzuschieben, und sie erbt damit auch
# alle Korridorvorgaben der S8.
#
# Noerdlich von Blankenburg endet die Gemeinsamkeit: die S8 kommt dort vom
# Aussenring herein, waehrend die S86 der Stettiner Bahn folgt. Auf diesem
# letzten Stueck bis Buch faehrt sie neben der S2.
_S86_AUF_DER_S8: List[Step] = bestand._reversed(
    bestand._slice(VORGAENGER.lines["S8"].steps, "blankenburg", "gruenau")
)

S86 = TurnLine(
    color=bestand.LINE_COLORS["S8"],
    family="S8",
    # Gruenau -> Adlershof, die Goerlitzer Bahn nach Nordwesten.
    start=315,
    steps=[
        *_S86_AUF_DER_S8,
        # Blankenburg steht schon am Ende des S8-Stuecks.
        *bestand._slice(bestand._STETTINER_BAHN, "blankenburg", "buch")[1:],
    ],
    direction="Grünau -> Buch",
)

# Zwei Stellen, an denen die S86 nicht von allein auf der Spur der S8
# landet. Der Grund ist beide Male derselbe: ein Spurwechsel wird erst in
# der naechsten KURVE uebernommen, und die S86 befaehrt diese Abschnitte in
# der Gegenrichtung zur S8 -- die passende Kurve liegt fuer sie deshalb
# woanders.
S86_CORRIDORS: Dict[str, Corridor] = {
    "ring_zulauf_schoenhauser": replace(
        VORGAENGER.corridors["ring_zulauf_schoenhauser"],
        # Auf dieser Kante liegt der 90-Grad-Bogen an der Bornholmer
        # Strasse. Die S8 faehrt ihn nach Sueden und wechselt dort von der
        # Stettiner Spur auf die aeussere Ringspur (-1.0). Die S86 faehrt
        # ihn andersherum, fuer sie ist es derselbe Wechsel rueckwaerts:
        # bis in den Bogen bleibt sie auf der Ringspur, die sie schon
        # mitbringt, und ab dem Bogen liegt sie auf der Spur, die die S8
        # dort gerade verlassen hat -- in Schreibrichtung dieses Korridors
        # ist das -1.5. Ohne den Wert bliebe sie durch die Bornholmer
        # Strasse hindurch auf der Ringspur, eine halbe Spur neben der S8.
        offsets={
            **VORGAENGER.corridors["ring_zulauf_schoenhauser"].offsets,
            "S86": -1.5,
        },
    ),
    "s8_pankow_bornholmer": replace(
        VORGAENGER.corridors["s8_pankow_bornholmer"],
        # Die S8 faehrt hier nach Sueden: sie liegt von Blankenburg bis in
        # die Kurve vor Bornholmer auf der Stettiner Spur und schwenkt erst
        # dort nach aussen. Die S86 kommt von der anderen Seite -- fuer sie
        # faellt derselbe Wechsel in dieselbe Kurve, wenn sie auf DIESER
        # Kante schon die Stettiner Spur hat. Ohne das bliebe sie bis
        # Blankenburg eine Spur neben der S8, weil danach keine Kurve mehr
        # kommt.
        offsets={
            **VORGAENGER.corridors["s8_pankow_bornholmer"].offsets,
            "S86": -0.5,
        },
    ),
    "s86_zulauf_treptower": Corridor(
        # Der Wechsel von der Goerlitzer Bahn auf den Ring passiert in der
        # Kurve VOR Treptower Park -- fuer die S8 (aus dem Ring kommend)
        # genau richtig, fuer die S86 zu frueh: sie faehrt danach bis
        # Landsberger Allee ohne weitere Kurve und laege bis dorthin mittig
        # auf dem Ring statt aussen. Mit der Ringspur schon auf DIESER Kante
        # faellt ihr Schwenk in dieselbe Kurve wie der der S8.
        # In derselben Richtung geschrieben wie `goerlitzer_bahn`, sonst
        # kippt das Vorzeichen -- und mit ihm die Seite von S9 und S46/S47.
        steps=["treptower_park", "plaenterwald"],
        offsets={"S8": 0.0, "S9": 1.0, "S4": -1.0, "S86": -1.0},
    ),
}


# Beide Endpunkte werden Umsteigebahnhoefe.
S86_ENDPUNKTE: List[Station] = [
    replace(VORGAENGER.stations["gruenau"], kind="hub"),
    replace(VORGAENGER.stations["buch"], kind="hub"),
]

# Charlottenburg wird Endbahnhof der S3 und damit Umsteigepunkt; an der
# Warschauer Strasse endet dafuer keine Linie mehr -- die S75 faehrt jetzt
# durch bis Spandau.
UMGEWIDMETE_STATIONEN: List[Station] = [
    replace(VORGAENGER.stations["charlottenburg"], kind="hub"),
    replace(VORGAENGER.stations["warschauer_strasse"], kind="station"),
]


# ============================================================================
# STADTBAHN WEST -- S3 bis Charlottenburg, S75 bis Spandau
# ============================================================================
#
# Die S3 gibt den Spandauer Ast ab und endet in Charlottenburg; die S75
# uebernimmt ihn und faehrt statt am Ostbahnhof erst in Spandau zu Ende.
# Auf der Stadtbahn liegt sie dabei wie ueberall auf der Spur ihrer Familie,
# also auf der S7.

S3 = replace(
    VORGAENGER.lines["S3"],
    start=90,                        # Charlottenburg -> Savignyplatz, nach Osten
    steps=bestand._slice(VORGAENGER.lines["S3"].steps, "charlottenburg", "erkner"),
    direction="Charlottenburg -> Erkner",
)

# Das gerade Stueck zwischen Kurve und Westkreuz ist auf allen drei Aesten
# um 0.6 kuerzer als im Bestand -- die Gabel rueckt damit naeher an die
# Station. S9 und S75 teilen sich die Kante zur Messe Sued und muessen
# deshalb dieselbe Form haben.
_KURVE_MESSE_SUED_WESTKREUZ: List[Step] = [FixPath(2.2), Turn(-45), FixPath(1.6)]

S9 = VORGAENGER.splice_line(
    "S9",
    bestand._KURVE_MESSE_SUED_WESTKREUZ,
    _KURVE_MESSE_SUED_WESTKREUZ,
)

# Hinter dem Westkreuz gabelt sich das Buendel: die S75 nach Nordwesten zur
# Messe Sued, die S7 nach Suedwesten um den Grunewald. Auf der Mittellinie
# liegen beide Kurven gleich weit von der Station entfernt (2.2) -- gezeichnet
# aber nicht: beide Linien fahren seitlich versetzt auf der Spur ihrer
# Familie, und der Eckpunkt einer versetzten Linie wandert je nach
# Knickrichtung vor oder zurueck. Die beiden Gabelpunkte lagen dadurch fast
# eine Gitterlaenge auseinander.
#
# Der Wert ist gemessen, nicht gerechnet: er ist die Beinlaenge, bei der die
# Ecke der S7 auf der der S75 liegt und die Gabel EIN Punkt wird -- und wie
# dort um 0.6 gekuerzt.
_S7_KURVE_WESTKREUZ_LAENGE = 2.58

S7 = VORGAENGER.splice_line(
    "S7",
    [FlexPath(), Turn(45), FixPath(2.2)],
    [FlexPath(), Turn(45), FixPath(_S7_KURVE_WESTKREUZ_LAENGE)],
)

S75 = replace(
    VORGAENGER.lines["S75"],
    start=135,                       # Spandau -> Stresow, nach Suedosten
    steps=[
        # Derselbe Weg, den bisher die S3 nahm -- und den die S9 weiter
        # nimmt: Spandauer Ast, Bogen vor Westkreuz, Stadtbahn.
        *bestand._SPANDAU,
        *_KURVE_MESSE_SUED_WESTKREUZ,
        *bestand._slice(bestand._STADTBAHN, "westkreuz", "warschauer_strasse"),
        # Der geerbte Streckenzug faengt an der Warschauer Strasse an; die
        # steht jetzt schon am Ende der Stadtbahn oben.
        *VORGAENGER.lines["S75"].steps[1:],
    ],
    direction="Spandau -> Wartenberg",
)

STADTBAHN_WEST_CORRIDORS: Dict[str, Corridor] = {
    "stadtbahn_zulauf_messe_sued": replace(
        VORGAENGER.corridors["stadtbahn_zulauf_messe_sued"],
        # Die S3 faehrt den Bogen nicht mehr, dafuer die S75: sie muss ihre
        # Stadtbahnspur wie alle anderen schon hier einnehmen, danach kommt
        # bis Ostkreuz keine Kurve mehr.
        offsets={"S7": -1.5, "S9": 1.5},
    ),
}


# ============================================================================
# GEDEHNTE GERADEN
# ============================================================================
#
# Zwei Geraden werden laenger: Adlershof -- Gruenau um 0.6, die hinter
# Muehlenbeck-Moenchmuehle um 1.0. Beides sind Bildkorrekturen, keine
# Netzaenderungen: zwischen Adlershof und Gruenau braucht die Gabel zum
# Flughafenast Platz, und hinter Muehlenbeck-Moenchmuehle rueckt die
# 90-Grad-Kurve von der Beschriftung ab.
#
# Eine Kante muss in ALLEN Linien dieselbe Form haben. Die Dehnung wird
# deshalb ueber jede Linie gelegt, die die Stelle befaehrt -- auch ueber die
# sonst unveraenderten -- und zwar in beiden Fahrtrichtungen.
_DEHNUNGEN = [
    (["adlershof", FixPath(2.4), "gruenau"],
     ["adlershof", FixPath(3.0), "gruenau"]),
    (["muehlenbeck_moenchmuehle", FixPath(2.6)],
     ["muehlenbeck_moenchmuehle", FixPath(3.6)]),
]


def _enthaelt(steps: List[Step], folge: List[Step]) -> bool:
    return any(
        list(steps[i:i + len(folge)]) == folge
        for i in range(len(steps) - len(folge) + 1)
    )


def _dehne(line: TurnLine) -> TurnLine:
    """Dieselbe Linie mit den gedehnten Geraden -- unveraendert, wo sie die
    Stellen gar nicht befaehrt."""
    steps = list(line.steps)
    for alt, neu in _DEHNUNGEN:
        for a, n in ((alt, neu),
                     (bestand._reversed(alt), bestand._reversed(neu))):
            if _enthaelt(steps, a):
                steps = splice(steps, a, n)
    return replace(line, steps=steps)


LINIEN: Dict[str, TurnLine] = {
    lid: _dehne(line)
    for lid, line in {
        **VORGAENGER.lines,
        "S3": S3, "S6": S6, "S7": S7, "S9": S9, "S15": S15, "S75": S75,
        "S85": S85, "S86": S86, **RING_LINES,
    }.items()
    # Der HVZ-Ast der S85 nach Pankow faellt in dieser Stufe weg (siehe
    # `derive` unten) -- er darf hier nicht wieder hereinkommen.
    if lid != "S85_pankow"
}


# ============================================================================
# ZUGGRUPPEN
#
# Die Legendentabelle dieser Stufe. Uebernommen aus dem Bestand, ergaenzt um
# die S6; `derive(groups=...)` setzt sie fuer alle Linien neu.
# ============================================================================

GROUPS: Dict[str, List[TrainGroup]] = {
    lid: list(line.groups) for lid, line in VORGAENGER.lines.items() if line.groups
}
GROUPS["S6"] = [
    TrainGroup("Stammzuggruppe", "Gartenfeld <> Hauptbahnhof", 3),
    TrainGroup("Tageszuggruppe", "Gartenfeld <> Hauptbahnhof", 3),
]
# Ueber den Gesundbrunnen hinaus auf die Nordbahn verlaengert.
GROUPS["S15"] = [
    TrainGroup("Stammzuggruppe", "Hauptbahnhof <> Frohnau", 3),
]
GROUPS["S3"] = [
    TrainGroup("Stammzuggruppe", "Erkner <> Charlottenburg", 4),
    TrainGroup("Tageszuggruppe", "Erkner <> Charlottenburg", 4),
    TrainGroup("HVZ-Verstärker", "Friedrichshagen <> Ostbahnhof", 2),
]
GROUPS["S5"] = [
    TrainGroup("Stammzuggruppe", "Strausberg Nord <> Westkreuz", 4),
    TrainGroup("Tageszuggruppe", "Strausberg <> Westkreuz", 4),
    TrainGroup("HVZ-Verstärker", "Mahlsdorf <> Warschauer Straße", 2),
    TrainGroup("HVZ-Verstärker", "Mahlsdorf <> Warschauer Straße", 2),
]
GROUPS["S75"] = [
    TrainGroup("Stammzuggruppe", "Wartenberg <> Spandau", 4),
    TrainGroup("Tageszuggruppe", "Wartenberg <> Ostbahnhof", 2),
]
# Gibt die Nordbahn an die S15 ab und endet am Hauptbahnhof.
GROUPS["S85"] = [
    TrainGroup("Stammzuggruppe", "Flughafen BER <> Hauptbahnhof", 3),
]
GROUPS["S86"] = [
    TrainGroup("Stammzuggruppe", "Grünau <> Buch", 4),
]


NET = VORGAENGER.derive(
    stations=[
        *(planned(s) for s in SIEMENSBAHN_STATIONS),
        *S86_ENDPUNKTE, *UMGEWIDMETE_STATIONEN,
    ],
    lines={
        **LINIEN,
        # Der HVZ-Ast der S85 nach Pankow faellt mit ihrem alten Nordast weg:
        # sie faehrt die Nordbahn nicht mehr und beruehrt die Bornholmer
        # Strasse gar nicht.
        "S85_pankow": REMOVE,
    },
    colors={"S86": bestand.LINE_COLORS["S8"]},
    groups=GROUPS,
    corridors={
        **SIEMENSBAHN_CORRIDORS, **S86_CORRIDORS, **STADTBAHN_WEST_CORRIDORS,
    },
    # Am Hauptbahnhof enden jetzt drei Linien statt einer, die Signetreihe ist
    # damit breiter als der Name. Unter ihm stiesse sie auf "Tiergarten" --
    # sie steht deshalb DARUEBER; so muss auch bei weiteren Linien nichts
    # mehr nachgeschoben werden.
    badge_above=("hauptbahnhof",),
    # An beiden Enden der S86 laeuft die Strecke schraeg unter der
    # Beschriftung durch -- das Tag unter dem Namen laege auf den Linien. Es
    # geht deshalb auf die gegenueberliegende Ecke, wie in Wannsee und
    # Wildau.
    badge_opposite_corner=("gruenau", "buch"),
    # Alphabetisch stuende die S15 vor der S6. Die Reihe folgt stattdessen
    # den Spuren, die von oben in die Station laufen: S6, S15, S85.
    badge_order={"hauptbahnhof": ("S6", "S15", "S85")},
    # Beide Namen erben aus dem Bestand einen Lift von 18 px, der dort das
    # Tag UNTER dem Namen von den Linien freihielt. Hier traegt der
    # Hauptbahnhof sein Tag oberhalb und der Gesundbrunnen als
    # Durchgangsstation gar keins mehr. Der Gesundbrunnen behaelt einen
    # kleinen Lift: seine Pille ist mit der zweiten Westlinie auf 2x4
    # gewachsen und reicht weiter nach oben als frueher.
    label_offsets={"hauptbahnhof": REMOVE, "gesundbrunnen": (0.0, -12.0)},
    # Die Tabelle ist um S6 und S86 gewachsen, und unter ihr liegt jetzt
    # der Gartenfelder Ast. Wie weit sie deshalb nach oben rueckt, rechnet
    # der Renderer aus (Hoehe None): sie haelt zu "(Gartenfeld)" denselben
    # Abstand wie der Rahmen ringsum.
    legend_at=(-60.0, None),
)

ZIEL = FilePath("outputs/berlin_sbahn_2030.svg")


if __name__ == "__main__":
    write_map(NET, sys.argv[1] if len(sys.argv) > 1 else ZIEL)
