"""
Netzdefinition Berlin: Ausbaustufe 2040plus.

Ausfuehren erzeugt die Karte:

    python3 berlin_2040plus.py [ziel.svg]  ->  outputs/berlin_sbahn_2040plus.svg

Dritte und letzte Stufe. Sie beschreibt nur den UNTERSCHIED zu
`berlin_2030plus` -- alles Uebrige erbt `Net.derive()` am Ende der Datei.

Inhalt: die Nahverkehrstangente Nord. Die S75 faehrt von Wartenberg auf
einer durchgehenden Geraden nach Nordwesten bis Birkenwerder und uebernimmt
dabei den Nordast der bisherigen S8; diese beginnt dafuer in Buch. Am
Kreuzungsbahnhof Karower Kreuz trifft die neue Gerade auf die Stettiner
Bahn, und auf dem Aussenring davor kommen zwei Halte dazu.

Im Westen dreht sich die Zuordnung von `berlin_2030` wieder um: den
Spandauer Ast faehrt wieder die S3, die S75 endet in Charlottenburg. Damit
gilt westlich des Westkreuzes auch wieder die Geometrie des Bestands.

Dazu der BA3 der City-S-Bahn: S6 und S15 fahren vom Potsdamer Platz weiter
ueber Gleisdreieck nach Yorckstrasse (Grossgoerschenstrasse) -- die S15 damit
nicht mehr ueber den Anhalter Bahnhof. Die S6 laeuft von dort weiter ueber
Julius-Leber-Bruecke und biegt in Schoeneberg ueber die Cheruskerkurve auf
den Suedring ab -- bis Koenigs Wusterhausen auf dem Weg der bisherigen S46.
Die faehrt deshalb nicht mehr dorthin: an ihre Stelle tritt die bis Westend
verlaengerte S47, die damit S46 heisst.

Im Norden faellt dafuer eine Linie weg: die S86 entfaellt, ihren Ast von
Pankow nach Buch faehrt jetzt die S85, die bisher in Pankow endete.

Der BA3 der City-S-Bahn (Verlaengerung zum Gleisdreieck und zur
Yorckstrasse, Cheruskerkurve) gehoert zeitlich ebenfalls hierher, ist aber
noch nicht gezeichnet.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path as FilePath
from typing import Dict, List

from netmap import (
    CFG, REMOVE, Corridor, FixPath, FlexPath, Station, Step, TrainGroup, Turn,
    TurnLine, insert_after, planned, splice, write_map,
)

import berlin_2026 as bestand
import berlin_2030plus as vorstufe

# Das Netz, auf dem dieses hier aufbaut.
VORGAENGER = vorstufe.NET


# ============================================================================
# AUSSENRING -- zwei neue Halte vor dem Karower Kreuz
# ============================================================================
#
# Auf dem Stueck zwischen Muehlenbeck-Moenchmuehle und dem Karower Kreuz.
# Diesen Ast befaehrt im Zielnetz die S75 (siehe unten), nicht mehr die S8.

AUSSENRING_STATIONS: List[Station] = [
    Station("schoenlinder_strasse", "Schönlinder Straße",
            label="Schönlinder Straße", label_pos="top_right"),
    Station("bucher_strasse", "Bucher Straße", label="Bucher\nStraße",
            label_pos="top_right"),
]

AUSSENRING_CORRIDORS: Dict[str, Corridor] = {
    # Wie im Bestand, nur heisst die Kante mit der Kurve jetzt
    # Bucher Strasse -- Blankenburg. Schluessel wie dort, damit er den
    # bisherigen Eintrag ersetzt.
    "s8_pankow_bornholmer": replace(
        VORGAENGER.corridors["s8_pankow_bornholmer"],
        # Der geerbte Eintrag galt der S85, solange sie in Pankow ANFING --
        # eine Startspur gilt nur fuer die erste Kante einer Linie. Sie
        # faehrt jetzt aus Buch durch und braucht ihn nicht mehr.
        start_offsets={},
    ),
    "aussenring_zulauf_hohen_neuendorf": Corridor(
        # Die S75 muss neben der S1 liegen, wenn sie auf deren Trasse
        # einschwenkt. Der Versatz steht schon auf der Kante MIT der Kurve,
        # sonst wird er nie uebernommen -- zwischen Hohen Neuendorf und
        # Birkenwerder kommt keine mehr.
        steps=["bergfelde", "hohen_neuendorf"],
        offsets={"S75": 0.5},
    ),
    "nord_sued_s1": replace(
        VORGAENGER.corridors["nord_sued_s1"],
        # Zwischen Hohen Neuendorf und Birkenwerder faehrt jetzt die S75
        # statt der S8 -- auf derselben Spur.
        offsets={**VORGAENGER.corridors["nord_sued_s1"].offsets, "S75": 0.5},
        # Anhalter Bahnhof -> Potsdamer Platz ist keine einzelne Kante mehr:
        # `_TUNNEL_FORMEN` teilt die Versatz-Schraege dort am unbeschrifteten
        # Wegpunkt `anhalter_potsdamer_schraege` (siehe `BA3_CORRIDORS`).
        steps=insert_after(
            VORGAENGER.corridors["nord_sued_s1"].steps,
            "anhalter_bahnhof", "anhalter_potsdamer_schraege",
        ),
    ),
    "stadtbahn_zulauf_messe_sued": bestand.NET.corridors[
        # Vor dem Westkreuz faehrt wieder die S3 statt der S75 -- damit gilt
        # dort auch wieder die Spurvorgabe des Bestands.
        "stadtbahn_zulauf_messe_sued"
    ],
    "s8_blankenburg_pankow": Corridor(
        # Von Buch bis Blankenburg teilen sich S2 und S8 die Trasse. Ohne
        # Vorgabe wuerde die automatische Vergabe beide symmetrisch legen
        # und damit auch die S2 von der Mitte schieben -- die soll aber
        # bleiben, wo sie ist.
        steps=["buch", "karow", "karower_kreuz", "blankenburg"],
        offsets={"S2": 0.0, "S8": -0.5},
    ),
}


# ============================================================================
# WRIEZENER BAHN -- S75 nach Osten
# ============================================================================

# Tangentenlaenge des 90-Grad-Knicks hinter Springpfuhl: gleich dem
# Standardradius (t = R * tan(45) = R), damit die Kurve denselben Radius
# bekommt wie die 90-Grad-Kurven der uebrigen Linien.
_SPRINGPFUHL_KNICK = 1.2

# Verlaengerung ueber Wartenberg hinaus nach Nordwesten. Die Reihenfolge
# folgt der Lage: Sellheimbruecke liegt dicht westlich von Wartenberg,
# danach kommt das Karower Kreuz und zuletzt die Parkstadt Pankow.
S75_STATIONS: List[Station] = [
    Station("sellheimbruecke", "Sellheimbrücke", label="Sellheimbrücke",
            label_pos="top_right"),
    # Umsteigepunkt zwischen S75, S2 und S8. Einzige Station der Geraden,
    # die nicht nach oben rechts beschriftet ist -- dort liegt Karow.
    # Der Text steht rechts, das S8-Signet ueber badge_offsets unter der Pille.
    Station("karower_kreuz", "Karower Kreuz", kind="hub", label_pos="right"),
    Station("parkstadt_pankow", "Parkstadt Pankow", label="Parkstadt Pankow",
            label_pos="top_right"),
]

# Am Karower Kreuz trifft die S75 auf die Stettiner Bahn. Damit schliesst
# sich ein Ring durch das halbe Netz -- Karower Kreuz haengt jetzt sowohl an
# der S2 als auch, ueber Wartenberg und Ostkreuz, an der S75. Die Laengen
# muessen deshalb elastisch sein, sonst laesst sich die Schleife nicht
# schliessen; `preferred` gibt dem Loeser nur noch die Wunschform vor.
#
# Zwei Knicke von Nord ueber Nordwest nach West: das Karower Kreuz liegt
# deutlich weiter westlich als noerdlich von Wartenberg, mit einer reinen
# Diagonalen waere es nicht erreichbar.
# Von Gehrenseestrasse bis Bergfelde faehrt die S75 im Zielnetz eine einzige
# Gerade nach Nordwesten -- ohne einen einzigen Knick. Der Ast, den bisher die
# S8 befahren hat, haengt hinten dran.
#
# Die Laengen sind durchweg elastisch: das Karower Kreuz haengt starr an der
# Trasse der S2, Birkenwerder an der der S1, und die Gerade muss beide
# treffen. `preferred` gibt nur die Wunschabstaende vor.
_S75_GERADE: List[Step] = [
    # Das Bein von Springpfuhl bis zum Knick. Ohne Angabe waere es der halbe
    # Stationsabstand (0.6) -- zu kurz fuer die Tangente des vollen
    # 90-Grad-Radius (1.2), der Knick fiele damit auf den halben Radius
    # zusammen. Zusammen mit dem verkuerzten Stueck davor (siehe
    # `_FFO_SPRINGPFUHL` in `_FORMEN`) bleibt der Knick, wo er war, und nur
    # die Station rueckt an Friedrichsfelde Ost heran.
    FixPath(_SPRINGPFUHL_KNICK),
    Turn(-90),                      # NO -> Nordwest, statt bisher nach Nord
    FlexPath(preferred=2.6),
    "gehrenseestrasse", FlexPath(preferred=1.4),
    "hohenschoenhausen", FlexPath(preferred=1.4),
    "wartenberg", FlexPath(preferred=1.4),
    "parkstadt_pankow", FlexPath(preferred=1.6),
    "sellheimbruecke", FlexPath(preferred=2.0),
    "karower_kreuz", FlexPath(preferred=2.0),
    # Bucher Strasse bis Bergfelde: gleich lange Abschnitte, Laenge frei.
    "bucher_strasse", FlexPath(group="aussenring"),
    "schoenlinder_strasse", FlexPath(group="aussenring"),
    "muehlenbeck_moenchmuehle", FlexPath(group="aussenring"),
    "schoenfliess", FlexPath(group="aussenring"),
    "bergfelde",
    # ... erst hier wieder ein Knick, nach Norden auf die Trasse der S1.
    # Kurz vor Hohen Neuendorf zurueck auf Nord, auf die Trasse der S1.
    FixPath(1.0), Turn(45), FixPath(0.6),
    # Ohne eigene Laengenangabe -- diese Kante gehoert der S1, ihre
    # Geometrie muss hier unveraendert uebernommen werden.
    "hohen_neuendorf",
    "birkenwerder",
]

# ============================================================================
# CITY-S-BAHN BA3 -- S6 vom Potsdamer Platz auf den Suedring
# ============================================================================
#
# Der Tunnel geht ueber den Potsdamer Platz hinaus: Gleisdreieck, Yorckstrasse
# (Grossgoerschenstrasse), Julius-Leber-Bruecke -- bis hierher die Trasse der
# S1 -- und dann ueber die Cheruskerkurve auf den Suedring. Ab Schoeneberg
# faehrt die S6 den Weg der S46 bis Koenigs Wusterhausen.
#
# Der Knick auf den Ring sitzt GENAU im Bahnhof Schoeneberg: `FixPath(0.0)`
# laesst dem Bein davor keine Laenge. Nur so laesst sich hier abbiegen,
# obwohl beide Nachbarkanten geradeaus fahrenden Linien gehoeren -- der S1
# auf der Wannseebahn und den Ringlinien nach Suedkreuz.
# ============================================================================
# NORD-SUED-TUNNEL -- symmetrisch in den Potsdamer Platz
# ============================================================================
#
# Der Tunnel bekommt denselben Versatz wie der Ast vom Hauptbahnhof: ein
# 45-Grad-Knick vor und einer hinter dem Brandenburger Tor, beide biegen
# spiegelbildlich in Potsdamer Platz ein. Suedlich davon (zwischen Potsdamer
# Platz und Anhalter Bahnhof) geht der Versatz mit zwei weiteren Knicken
# wieder zurueck, damit sich an der Trasse ab Yorckstrasse nichts aendert.


# ============================================================================
# STELLSCHRAUBEN -- Hauptbahnhof bis Schoeneberg
# ============================================================================
#   Stadtbahn ---- _AUSSEN_GERADE ---\
#                                     \ Schraege (aus _STADTBAHN_HBF_FS)
#                  _PP_GERADE --------/
#   Potsdamer Platz
#                  _ANHALTER_GERADE + _STADTBAHN_HBF_FS + _PP_GERADE (fest,
#                  gleiche Hoehe wie Anhalter Bahnhof)
#   Gleisdreieck --- elastisch (FlexPath), dann Turn(45), dann fix 1.2 ------
#   Yorckstrasse (Grossgoerschenstrasse)
#                  _YORCK_JLB
#   Julius-Leber-Bruecke
#                  _JLB_SCHOENEBERG_ABSTAND
#   Schoeneberg (auf dem Ring, in der Hoehe fest)

_STADTBAHN_HBF_FS = 1.8   # Breite des Knotens: Hauptbahnhof <-> Friedrichstrasse
_AUSSEN_GERADE = 1.4      # Gerade von der Stadtbahn Richtung Süden alter Tunnel
_PP_GERADE = 0.3          # Gerade am Potsdamer Platz, auf allen vier Aesten
_ANHALTER_GERADE = 0.4    # Gerade an Gleisdreieck / Anhalter Bahnhof
_DEHNUNG_YORCK = -0.3     # kommt auf allen drei Aesten oben drauf
_YORCK_JLB = 1.0          # Yorckstrasse (Grossgoerschenstrasse) <-> Julius-Leber-Bruecke

# Radius der Cheruskerkurve -- je spitzer (kleiner), desto knapper der Bogen.
_CHERUSKER_RADIUS = 0.4

# Mindestabstand Julius-Leber-Bruecke <-> Schoeneberg: das erste Bein der
# Cheruskerkurve (vor dem Turn) bekommt diese explizite Mindestlaenge, statt
# nur die vom Radius erzwungene Tangente (R * tan 67.5). Ohne das wuerde ein
# spitzerer Radius die beiden Stationen automatisch naeher zusammenziehen --
# mit fester Mindestlaenge bleibt der Abstand unabhaengig vom Radius.
_JLB_SCHOENEBERG_ABSTAND = 1.9

# 45-Grad-Schraege fuer den Versatz (Kartenwert fuer eine horizontale
# Verschiebung um _STADTBAHN_HBF_FS).
_SCHRAEGE = _STADTBAHN_HBF_FS * 2 ** 0.5

# Hoehe des Asts Hauptbahnhof -> Potsdamer Platz: Gerade + Schraege +
# Gerade, aber als ein einziges Bein gezeichnet (keine Station noetig, an
# der er knickt).
_STADTBAHN_PP = _AUSSEN_GERADE + _STADTBAHN_HBF_FS + _PP_GERADE


_TUNNEL_FORMEN = [
    (["potsdamer_platz", FixPath(1.2), "brandenburger_tor",
      FixPath(2.4), "friedrichstrasse"],
     ["potsdamer_platz", FixPath(_PP_GERADE),
      Turn(45), FixPath(_SCHRAEGE * 0.7),
      "brandenburger_tor", FixPath(_SCHRAEGE * 0.3), Turn(-45),
      FixPath(_AUSSEN_GERADE), "friedrichstrasse"]),
    (["anhalter_bahnhof", FixPath(1.2), "potsdamer_platz"],
     ["anhalter_bahnhof", FixPath(_ANHALTER_GERADE),
      # Elastisch statt _SCHRAEGE fest: der S2-Zulauf (unten) zwingt Anhalter
      # Bahnhof ohne eigene Kurve starr auf die x-Position von S2 -- diese
      # Schraege gleicht die Differenz zur Nordschraege deshalb aus, statt
      # exakt gleich lang zu sein.
      Turn(-45), FixPath(_SCHRAEGE / 2),
      # Unbeschrifteter Wegpunkt: eigene Kante fuer den Spurwechsel der S1
      # im zweiten Knick statt schon am Anhalter Bahnhof.
      "anhalter_potsdamer_schraege", FixPath(_SCHRAEGE / 2),
      Turn(45), FixPath(_PP_GERADE),
      "potsdamer_platz"]),
    (["yorckstrasse_grossgoerschenstrasse", FixPath(1.15), Turn(-45),
      FixPath(0.6)],
     ["yorckstrasse_grossgoerschenstrasse",
      FlexPath(2 * _SCHRAEGE), Turn(-45),
      FlexPath(_ANHALTER_GERADE + _DEHNUNG_YORCK)]),
    # Die Kante gehoert der S1; die S6 schreibt sie unten noch einmal mit
    # derselben Laenge (`_YORCK_JLB`).
    (["yorckstrasse_grossgoerschenstrasse", FixPath(1.2),
      "julius_leber_bruecke"],
     ["yorckstrasse_grossgoerschenstrasse", FixPath(_YORCK_JLB),
      "julius_leber_bruecke"]),
]


# Suedlich des Potsdamer Platzes laufen die beiden Aeste wieder auseinander
# und in Yorckstrasse (Grossgoerschenstrasse) wieder zusammen -- die S6 als
# Spiegelbild des Tunnels. Die gemeinsame Spur mit der S1 dort regeln die
# Korridore `potsdamer_platz_*` unten ueber den Spurversatz, nicht die Trasse.

# Potsdamer Platz -> Yorckstrasse (Grossgoerschenstrasse) ueber Gleisdreieck,
# gemeinsam fuer S6 und S15.
_POTSDAMER_YORCK: List[Step] = [
    # Dieselbe Hoehe wie Potsdamer Platz -> Anhalter Bahnhof (Suedschraege),
    # damit Gleisdreieck auf gleicher Hoehe wie Anhalter Bahnhof liegt.
    FixPath(_ANHALTER_GERADE + _STADTBAHN_HBF_FS + _PP_GERADE),
    "gleisdreieck",
    FlexPath(), Turn(45),
    FixPath(1.4),
    "yorckstrasse_grossgoerschenstrasse",
]

# Geerbter (Vorstufen-)Weg der S15 ueber den Anhalter Bahnhof, zum Splicen
# auf `_POTSDAMER_YORCK`.
_S15_UEBER_ANHALTER: List[Step] = [
    FixPath(1.2),
    "anhalter_bahnhof",
    FixPath(0.6), Turn(45), FixPath(1.15),
    "yorckstrasse_grossgoerschenstrasse",
]

# Geerbter (Bestands-)Weg der S25 ueber den Anhalter Bahnhof, zum Splicen
# auf `_YORCK_GLEISDREIECK`. Die S26 (unten) behaelt diesen Weg unveraendert.
_S25_UEBER_ANHALTER: List[Step] = [
    "yorckstrasse", FixPath(1.4),
    "anhalter_bahnhof", FixPath(1.2),
    "potsdamer_platz",
]

# Die S25 wechselt von ihrer alten Kante ueber den Anhalter Bahnhof auf die
# Spur der S15/S6: von "yorckstrasse" (S2) mit derselben Schraege wie am
# Anhalter Bahnhof (Turn(-45), _SCHRAEGE, Turn(45)) direkt auf Gleisdreieck,
# von dort weiter mit der schon vorhandenen Kante Gleisdreieck ->
# Potsdamer Platz. Beide Geraden sind elastisch: "yorckstrasse" und
# Gleisdreieck haben nicht denselben Abstand wie Anhalter Bahnhof und
# Potsdamer Platz, und diese Kante schliesst ausserdem einen Kreis mit der
# Cheruskerkurve (ueber Potsdamer Platz, Gleisdreieck, Julius-Leber-Bruecke,
# Suedring, Gesundbrunnen, zurueck zu Hauptbahnhof/Perleberger Bruecke) --
# mit nur einer elastischen Gerade hier ist der Kreis nicht in beiden
# Richtungen schliessbar, sobald sich `_CHERUSKER_RADIUS` aendert.
_YORCK_GLEISDREIECK: List[Step] = [
    "yorckstrasse",
    FlexPath(), Turn(-45), FixPath(_SCHRAEGE), Turn(45),
    FlexPath(),
    "gleisdreieck",
    FixPath(_ANHALTER_GERADE + _STADTBAHN_HBF_FS + _PP_GERADE),
    "potsdamer_platz",
]

# Ab Potsdamer Platz faehrt die S25 nicht mehr durch den Nord-Sued-Tunnel
# (Brandenburger Tor, Friedrichstrasse, ...), sondern ueber den Ast zum
# Hauptbahnhof und von dort ueber Perleberger Bruecke und Wedding zum
# Gesundbrunnen -- derselbe Weg, den S15 vor der BA3-Verlaengerung nach
# Gleisdreieck schon gefahren ist.
_S25_DURCH_TUNNEL: List[Step] = [
    "potsdamer_platz", FixPath(1.2),
    "brandenburger_tor", FixPath(2.4),
    "friedrichstrasse", FixPath(2.8),
    "oranienburger_strasse", FixPath(1.4),
    "nordbahnhof", FixPath(1.4),
    "humboldthain", FixPath(2.4),
    "gesundbrunnen",
]
_S25_UEBER_HAUPTBAHNHOF: List[Step] = [
    "potsdamer_platz", FixPath(_STADTBAHN_PP),
    *bestand._reversed(
        bestand._slice(
            VORGAENGER.lines["S15"].steps, "gesundbrunnen", "hauptbahnhof"
        )
    ),
]

_BA3: List[Step] = [
    *_POTSDAMER_YORCK,
    FixPath(_YORCK_JLB),
    "julius_leber_bruecke",
    # Cheruskerkurve: ein Bogen von der Wannseebahn auf den Suedring,
    # trifft ihn zwischen Schoeneberg und Suedkreuz -- Schoeneberg selbst
    # liegt nicht daran.
    FlexPath(min_length=_JLB_SCHOENEBERG_ABSTAND),
    Turn(-135, radius=_CHERUSKER_RADIUS), FlexPath(),
    # Ab Suedkreuz der Laufweg der bisherigen S46 (aus der Vorstufe, dort
    # bereits gedehnt), rueckwaerts gelesen.
    *bestand._reversed(
        bestand._slice(
            VORGAENGER.lines["S46"].steps, "koenigs_wusterhausen", "suedkreuz"
        )
    ),
]

# Abstand Priesterweg -- Suedkreuz (Bestand: 1.8) und Friedrichsfelde Ost --
# Springpfuhl (Bestand: 2.2), siehe die Begruendungen in `_FORMEN`.
_PRIESTERWEG_SUEDKREUZ = 2.6
_FFO_SPRINGPFUHL = 1.6

# Zwei Formaenderungen im Suedosten, fuer jede Linie an der Stelle und in
# beiden Fahrtrichtungen (eine Kante muss ueberall dieselbe Form haben).
_FORMEN = [
    *_TUNNEL_FORMEN,
    (["hauptbahnhof", FixPath(1.8), "friedrichstrasse"],
     ["hauptbahnhof", FixPath(_STADTBAHN_HBF_FS), "friedrichstrasse"]),
    # Ast Hauptbahnhof -> Potsdamer Platz: derselbe Kartenradius (0.8) wie
    # der Tunnel statt der bisherigen 1.2.
    ([FlexPath(), Turn(-45), FlexPath(), Turn(45, radius=1.2),
      FlexPath(preferred=1.0)],
     [FixPath(_STADTBAHN_PP)]),
    # Anhalter Bahn: S2/S25 von Yorckstrasse in den Tunnel -- elastisch,
    # damit sie sich an die Lage des Anhalter Bahnhofs anpasst.
    (["yorckstrasse", FixPath(1.4), "anhalter_bahnhof"],
     ["yorckstrasse", FlexPath(), "anhalter_bahnhof"]),
    # Laengeres erstes Bein vor Baumschulenweg, Platz fuer alle vier Spuren.
    (bestand._KURVE_BAUMSCHULENWEG_NEUKOELLN,
     [FixPath(2.4), *bestand._KURVE_BAUMSCHULENWEG_NEUKOELLN[1:]]),
    # Plaenterwald soll stehen bleiben -- die Dehnung faellt ganz auf das
    # Stueck nach Baumschulenweg.
    (bestand._KURVE_TREPTOW_PLAENTERWALD,
     [*bestand._KURVE_TREPTOW_PLAENTERWALD[:-1], FixPath(4.336)]),
    # Priesterweg rueckt von Suedkreuz ab: die Pille ist dort mit der S6 auf
    # vier Ringspuren gewachsen und reicht so weit nach Sueden, dass der
    # naechste Halt fast an ihr klebt.
    (["priesterweg", FixPath(1.8), "suedkreuz"],
     ["priesterweg", FixPath(_PRIESTERWEG_SUEDKREUZ), "suedkreuz"]),
    # Springpfuhl rueckt an Friedrichsfelde Ost heran. Die Ecke dahinter
    # liegt fest -- dort trifft die Wriezener Bahn auf die neue Gerade der
    # S75 -- der gewonnene Weg faellt also ganz auf das Stueck zwischen
    # Station und Knick. Erst damit ist Platz fuer die Tangente des vollen
    # 90-Grad-Radius; vorher deckelte das kurze Bein die Kurve auf die Haelfte.
    (["friedrichsfelde_ost", FixPath(2.2), "springpfuhl"],
     ["friedrichsfelde_ost", FixPath(_FFO_SPRINGPFUHL), "springpfuhl"]),
]


def _umgeformt(line: TurnLine) -> TurnLine:
    steps = list(line.steps)
    for alt, neu in _FORMEN:
        for a, n in ((alt, neu),
                     (bestand._reversed(alt), bestand._reversed(neu))):
            try:
                steps = splice(steps, a, n)
            except ValueError:
                pass
    return replace(line, steps=steps)


BA3_CORRIDORS: Dict[str, Corridor] = {
    # Spiegelung am Potsdamer Platz: S1/S15 auf der Trassenmitte, S6/S2
    # spiegelbildlich daneben (+-1), jede Linie durchgehend auf ihrer Spur
    # vom Zulauf bis zum Ablauf -- sonst faellt der Spurwechsel (immer erst
    # an der naechsten Kurve in Fahrtrichtung) fuer S1 und S15 an
    # verschiedene Stellen, weil beide den Knoten gegenlaeufig durchfahren.
    "potsdamer_platz_tunnel_s2_zulauf": Corridor(
        # S25 faehrt diese Kante nicht mehr (siehe Gleisdreieck-Umleitung
        # unten) -- nur noch die S2, S26 uebernimmt den Wert ueber ihre
        # Familie "S2".
        steps=["yorckstrasse", "anhalter_bahnhof"],
        offsets={"S2": 1.0},
    ),
    "potsdamer_platz_tunnel": Corridor(
        # S1 bleibt bis zum Wegpunkt auf -0.5; S2 (und darueber die S26)
        # weicht hier schon auf ihre Bestandsspur 0.5 aus, bevor sie am
        # Wegpunkt zurueckschwenkt.
        steps=["anhalter_bahnhof", "anhalter_potsdamer_schraege"],
        offsets={"S1": -0.5, "S2": 0.5},
    ),
    "potsdamer_platz_tunnel_s1": Corridor(
        steps=["anhalter_potsdamer_schraege", "potsdamer_platz"],
        offsets={"S1": 0.0, "S2": 1.0},
    ),
    "nord_sued_s1_gesundbrunnen": Corridor(
        # Ab Potsdamer Platz wieder die Bestandsspur, bis Gesundbrunnen.
        # Die S25 faehrt hier nicht mehr mit (sie zweigt am Potsdamer Platz
        # zum Hauptbahnhof ab) -- nur noch die S26 auf ihrer alten Trasse,
        # die den Wert 0.5 automatisch ueber ihre Familie "S2" bekommt.
        steps=["potsdamer_platz", "brandenburger_tor", "friedrichstrasse",
               "oranienburger_strasse", "nordbahnhof", "humboldthain",
               "gesundbrunnen"],
        offsets={"S1": -0.5, "S2": 0.5},
    ),
    "potsdamer_platz_ast": Corridor(
        # Die S25 kommt hier neu dazu (von "yorckstrasse" ueber die neue
        # Schraege) und liegt links von der S15, direkt daneben; die S6
        # bleibt rechts auf ihrer alten Spur.
        steps=["perlegerberger_bruecke", "hauptbahnhof", "potsdamer_platz",
               "gleisdreieck", "yorckstrasse_grossgoerschenstrasse"],
        offsets={"S15": 0.0, "S25": -1.0, "S6": 1.0},
    ),
    # Die neue Schraege "yorckstrasse" -> Gleisdreieck hat sonst KEINEN
    # Korridor (die S25 faehrt dort allein) und bekommt deshalb automatisch
    # die Mittelspur (0.0) -- exakt die der S15 auf `potsdamer_platz_ast`
    # gleich danach. Da zwischen der Schraege und Hauptbahnhof kein Knick
    # mehr liegt (alles eine gerade Strecke), wird `potsdamer_platz_ast`s
    # Wert dort nie uebernommen: die S25 muss ihre Spur schon hier bekommen.
    "yorckstrasse_gleisdreieck_zulauf": Corridor(
        steps=["yorckstrasse", "gleisdreieck"],
        offsets={"S25": 1.0},
    ),
    "wannseebahn_s6": Corridor(
        # S6 laeuft hinter Gleisdreieck neben die S1, die mittig bleibt --
        # auf derselben Seite (+1.0), auf der sie schon vor Gleisdreieck
        # liegt (siehe "potsdamer_platz_ast"). S6 und S1 kreuzen sich hier
        # nicht (S6 kreuzt erst weiter suedlich, an der Cheruskerkurve, den
        # Ring) und muessen es deshalb auch nicht: bei -1.0 (Gegenseite)
        # wechselt S6 am Knick bei Gleisdreieck unnoetig die Seite UND
        # geraet dabei in die Gehrung, mit der die S1 in derselben Kurve
        # ihren eigenen Versatz Richtung Anhalter Bahnhof wechselt (0 ->
        # -0.5, aus dem Bestand) -- die Spitze dieser Gehrung reicht dann in
        # die S6-Spur hinein, ohne dass es eine echte Kreuzung waere, die
        # der Renderer mit einem weissen Rand freistellen koennte.
        steps=["gleisdreieck", "yorckstrasse_grossgoerschenstrasse",
               "julius_leber_bruecke"],
        offsets={"S1": 0.0, "S6": -1.0},
    ),
    "cheruskerkurve": Corridor(
        steps=["julius_leber_bruecke", "suedkreuz"],
        offsets={"S6": 2.0},
    ),
    "suedring_s6": Corridor(
        steps=["neukoelln", "hermannstrasse", "tempelhof", "suedkreuz",
               "schoeneberg"],
        offsets={"S42": 0.0, "S41": 1.0, "S4": -1.0, "S6": 2.0},
    ),
    "ring_zulauf_koellnische_heide": replace(
        VORGAENGER.corridors["ring_zulauf_koellnische_heide"],
        offsets={"S4": -1.0, "S6": 1.0},
    ),
    # Dasselbe Muster wie im 2030er Netz an genau dieser Kurve (dort S15 und
    # S85, die S85-Rolle spielt hier die S25): S15 wechselt ihre Spur schon
    # im Knick bei Wedding (2.0 -> 0.0, siehe `hbf_zulauf_wedding`), die S25
    # bleibt dort unveraendert und wechselt stattdessen erst im Knick bei
    # Perleberger Bruecke (siehe `hbf_zulauf_westhafen`/`potsdamer_platz_ast`,
    # 1.0 -> -1.0). Jede Linie wechselt so an nur einer Kurve, nicht an beiden.
    "ring": replace(
        VORGAENGER.corridors["ring"],
        offsets={**VORGAENGER.corridors["ring"].offsets, "S25": 1.0, "S15": -2.0},
    ),
    "hbf_zulauf_wedding": replace(
        VORGAENGER.corridors["hbf_zulauf_wedding"],
        offsets={"S15": 0.0, "S25": 1.0},
    ),
    # Auf "perlegerberger_bruecke" -> "hauptbahnhof" gewinnt spaeter
    # `potsdamer_platz_ast` (S25 hier also ohne Wirkung); auf "westhafen" ->
    # "perlegerberger_bruecke" gilt dieser Korridor aber wirklich -- S6 bekommt
    # deshalb schon hier ihren Wert 1.0, sonst haelt der Wechsel bei
    # Perleberger Bruecke nur zur Haelfte.
    "hbf_zulauf_westhafen": replace(
        VORGAENGER.corridors["hbf_zulauf_westhafen"],
        offsets={**VORGAENGER.corridors["hbf_zulauf_westhafen"].offsets,
                 "S6": 1.0, "S25": -1.0},
    ),
    "hbf_potsdamer_platz": replace(
        VORGAENGER.corridors["hbf_potsdamer_platz"],
        offsets={**VORGAENGER.corridors["hbf_potsdamer_platz"].offsets, "S25": -1.0},
    ),
    "goerlitzer_zulauf_spindlersfeld": replace(
        VORGAENGER.corridors["goerlitzer_zulauf_spindlersfeld"],
        offsets={"S4": 2.0},
    ),
    "goerlitzer_spindlersfelder_ast": Corridor(
        steps=["baumschulenweg", "schoeneweide"],
        offsets={"S8": 0.0, "S9": 1.0, "S6": 1.0, "S4": -2.0},
    ),
    "goerlitzer_bahn": replace(
        VORGAENGER.corridors["goerlitzer_bahn"],
        offsets={**VORGAENGER.corridors["goerlitzer_bahn"].offsets, "S6": 1.0},
    ),
}


# ============================================================================
# GEAENDERTE LINIEN
# ============================================================================

# Die S8 gibt ihren Nordast an die S75 ab und beginnt jetzt am Karower
# Kreuz. Alles ab Blankenburg bleibt, wie es war.
_S8_AB_BLANKENBURG = list(VORGAENGER.lines["S8"].steps)
_S8_AB_BLANKENBURG = _S8_AB_BLANKENBURG[_S8_AB_BLANKENBURG.index("blankenburg"):]

# Die Ostseite bleibt, wie sie ist: erst die neue Gerade einsetzen, dann
# unten den Westen abschneiden.
_S75_MIT_GERADE = VORGAENGER.splice_line(
    "S75",
    [Turn(-45), FixPath(1.6),
     "gehrenseestrasse", FixPath(1.4),
     "hohenschoenhausen", FixPath(1.4),
     "wartenberg"],
    _S75_GERADE,
)

# Das Stueck Blankenburg -- Buch bekommt mit dem Karower Kreuz einen
# Zwischenhalt. Es steht hier einmal, weil zwei Linien es brauchen.
# Die neue Schleife Yorckstrasse(S2)->Gleisdreieck->Cheruskerkurve->Suedkreuz
# ->Yorckstrasse(S2) (durch die BA3-Verlaengerung der S25 entstanden, siehe
# `_YORCK_GLEISDREIECK`) hat ausser den Cherusker-Flankenbeinen nur noch
# diese eine elastische Kante als Spiel: die Mindestlaenge aus dem Bestand
# (2.4, fuer S2 und S26) laesst bei kleinem `_CHERUSKER_RADIUS` keinen Schluss
# der Schleife mehr zu. Gilt nur hier, nicht im Bestand selbst.
_SUEDKREUZ_YORCK_ALT: List[Step] = ["suedkreuz", FlexPath(min_length=2.4), "yorckstrasse"]
_SUEDKREUZ_YORCK_NEU: List[Step] = ["suedkreuz", FlexPath(min_length=1.6), "yorckstrasse"]

_BLANKENBURG_BUCH_ALT: List[Step] = ["blankenburg", FixPath(2.4), "karow", "buch"]
_BLANKENBURG_BUCH_NEU: List[Step] = [
    "blankenburg", FixPath(2.4), "karower_kreuz",
    FixPath(1.8), "karow", FixPath(1.6), "buch",
]

# Pankow -> Buch, der Nordast der bisherigen S86.
_PANKOW_BUCH: List[Step] = [
    *bestand._slice(bestand._STETTINER_BAHN, "pankow", "blankenburg"),
    *_BLANKENBURG_BUCH_NEU[1:],
]

_S2_BLANKENBURG_BUCH = VORGAENGER.splice_line(
    "S2", _BLANKENBURG_BUCH_ALT, _BLANKENBURG_BUCH_NEU,
)

CHANGED_LINES: Dict[str, TurnLine] = {
    "S8": replace(
        VORGAENGER.lines["S8"],
        start=225,                       # Buch -> Karow, nach Suedwesten
        steps=[
            "buch", FixPath(1.6),
            "karow", FixPath(1.8),
            "karower_kreuz", FixPath(2.4),
            *_S8_AB_BLANKENBURG,
        ],
        direction="Buch -> Wildau",
    ),
    # Am Karower Kreuz trifft die S75 auf die Stettiner Bahn -- die Station
    # gehoert deshalb beiden Linien. Dasselbe Stueck faehrt die S2, sie
    # bekommt also denselben Zwischenhalt; die S8 faehrt es ab hier ohnehin
    # selbst (siehe oben).
    "S2": replace(
        _S2_BLANKENBURG_BUCH,
        steps=splice(
            _S2_BLANKENBURG_BUCH.steps, _SUEDKREUZ_YORCK_ALT, _SUEDKREUZ_YORCK_NEU
        ),
    ),
    # Die S86 entfaellt. Ihren Nordast von Pankow nach Buch uebernimmt die
    # S85, die bisher in Pankow endete -- eine Linie weniger fuer denselben
    # Weg.
    "S86": REMOVE,
    "S85": replace(
        VORGAENGER.lines["S85"],
        start=225,                       # Buch -> Karow, nach Suedwesten
        steps=[
            *bestand._reversed(_PANKOW_BUCH)[:-1],
            *VORGAENGER.lines["S85"].steps,
        ],
        direction="Buch -> Flughafen BER",
    ),
    # Die neue Gerade der S75 laeuft von Gehrenseestrasse bis Bergfelde
    # durch und muss dabei sowohl das Karower Kreuz (starr an der S2) als
    # auch Hohen Neuendorf (starr an der S1) treffen.
    "S75": replace(
        _S75_MIT_GERADE,
        start=90,                        # Charlottenburg -> Savignyplatz
        # Im Westen andersherum als in `berlin_2030`: den Spandauer Ast
        # faehrt wieder die S3, die S75 endet in Charlottenburg.
        steps=bestand._slice(
            _S75_MIT_GERADE.steps, "charlottenburg", "birkenwerder"
        ),
        direction="Charlottenburg -> Birkenwerder",
    ),
    # Damit geht der Spandauer Ast an die S3 zurueck -- unveraendert so, wie
    # er im Bestand steht.
    "S3": bestand.NET.lines["S3"],
    # Westlich des Westkreuzes gilt damit wieder die Bestandsgeometrie: die
    # in `berlin_2030` gekuerzten Geraden und das laengere Bein der S7, mit
    # dem dort die Gabel zusammengelegt wurde, gehoeren zu einem Netz, in
    # dem die S75 dort ueberhaupt faehrt. Beide Linien kommen deshalb
    # ebenfalls unveraendert aus dem Bestand.
    "S7": bestand.NET.lines["S7"],
    "S9": bestand.NET.lines["S9"],
    # Der Suedosten wird neu aufgeteilt: die Goerlitzer Bahn nach Koenigs
    # Wusterhausen faehrt jetzt die S6, den Ring dorthin braucht es nicht
    # mehr doppelt. Die bisherige S47 faehrt stattdessen ueber Suedkreuz
    # hinaus bis Westend -- und heisst damit S46; die alte S46 entfaellt.
    "S46": VORGAENGER.splice_line(
        "S47",
        bestand._slice(bestand._RING, "neukoelln", "suedkreuz"),
        bestand._slice(bestand._RING, "neukoelln", "westend"),
        direction="Spindlersfeld -> Westend",
    ),
    "S47": REMOVE,
    # Die S15 wechselt mit auf den neuen Ast -- ueber Gleisdreieck statt
    # ueber den Anhalter Bahnhof.
    "S15": VORGAENGER.splice_line(
        "S15", _S15_UEBER_ANHALTER, _POTSDAMER_YORCK,
    ),
    # BA3: weiter vom Potsdamer Platz auf den Suedring (siehe unten).
    "S6": replace(
        VORGAENGER.extend_line("S6", back=_BA3),
        direction="Gartenfeld -> Königs Wusterhausen",
    ),
    # Die S25 wechselt vom Anhalter Bahnhof auf die Spur der S15/S6 ueber
    # Gleisdreieck und faehrt ab Potsdamer Platz ueber Hauptbahnhof statt
    # durch den Nord-Sued-Tunnel; die S26 (neu, direkt darunter) uebernimmt
    # dafuer die alte Fassung unveraendert.
    "S25": replace(
        VORGAENGER.lines["S25"],
        steps=splice(
            splice(
                splice(
                    VORGAENGER.lines["S25"].steps,
                    _SUEDKREUZ_YORCK_ALT, _SUEDKREUZ_YORCK_NEU,
                ),
                _S25_UEBER_ANHALTER, _YORCK_GLEISDREIECK,
            ),
            _S25_DURCH_TUNNEL, _S25_UEBER_HAUPTBAHNHOF,
        ),
    ),
    # Neue Linie: der Weg der bisherigen S25 (Anhalter Bahnhof, alter
    # Tunnel, Kremmener Bahn) -- deckt ab, was die S25 jetzt ueber
    # Gleisdreieck umfaehrt. Im Norden endet sie schon in Hennigsdorf; auf
    # dem Stueck darueber hinaus nach Velten bleibt die S25 allein.
    "S26": replace(
        VORGAENGER.lines["S25"],
        steps=bestand._slice(
            splice(
                VORGAENGER.lines["S25"].steps,
                _SUEDKREUZ_YORCK_ALT, _SUEDKREUZ_YORCK_NEU,
            ),
            "stahnsdorf", "hennigsdorf",
        ),
    ),
}


# ============================================================================
# UEBERNOMMENE STATIONEN, NEU BESCHRIFTET
# ============================================================================

RESTYLED_STATIONS: List[Station] = [
    VORGAENGER.station("blankenburg", kind="station"),
    # Endpunkt der S8 und Durchfahrt der S2 -- damit ein Umsteigepunkt.
    # Beschriftung nach rechts, die Signetreihe darunter (siehe
    # `badge_opposite_corner` unten): nordwestlich der Station stehen die
    # Namen der neuen Aussenring-Halte, dort ist kein Platz mehr.
    VORGAENGER.station("buch", kind="hub", label_pos="bottom_right"),
    # Nordwestlich stoesst die Beschriftung an Muehlenbeck-Moenchmuehle,
    # suedwestlich liegt jetzt die S8 mit ihren neuen Halten -- bleibt die
    # Suedostseite.
    VORGAENGER.station("karow", label_pos="bottom_right"),
    # Alle Stationen auf der Geraden der S75 werden nach oben rechts
    # beschriftet -- einheitlich, statt je nach Nachbar mal so, mal so.
    *(VORGAENGER.station(sid, label_pos="top_right") for sid in (
        "gehrenseestrasse", "hohenschoenhausen", "wartenberg",
        "muehlenbeck_moenchmuehle", "schoenfliess", "bergfelde",
    )),
    # An beiden Stellen laufen die Linien nur noch parallel nebeneinander --
    # umsteigen muss hier niemand mehr, also ein gewoehnlicher Halt statt
    # der Umsteigepille.
    VORGAENGER.station("pankow", kind="station"),
    VORGAENGER.station("gruenau", kind="station"),
]

# Neu und noch nicht gebaut: Klammern um den Namen, blasser Stationspunkt.
# Die beiden Formaenderungen gelten fuer jede Linie, die die Stellen
# befaehrt -- auch fuer die sonst unveraenderten.
LINIEN: Dict[str, TurnLine] = {
    lid: _umgeformt(line)
    for lid, line in {**VORGAENGER.lines, **CHANGED_LINES}.items()
    # Die beiden gestrichenen Linien stehen unten in `derive`.
    if lid not in ("S86", "S47")
}


NEW_STATIONS: List[Station] = [
    planned(s) for s in (*AUSSENRING_STATIONS, *S75_STATIONS)
]
# Gleisdreieck steht schon im Bestand, faehrt aber erst der BA3 an.
NEW_STATIONS.append(planned(VORGAENGER.stations["gleisdreieck"]))
# Reiner Korridor-Wegpunkt ohne Beschriftung, siehe `_TUNNEL_FORMEN` und
# `BA3_CORRIDORS`: teilt die Versatz-Schraege Anhalter Bahnhof -> Potsdamer
# Platz in zwei Kanten, damit die S1 dort erst am zweiten Knick statt gleich
# am ersten die Spur wechselt.
NEW_STATIONS.append(
    Station("anhalter_potsdamer_schraege", "Anhalter-Potsdamer-Schraege",
            label="", hidden=True)
)


# ============================================================================
# ZUGGRUPPEN
#
# Die Legendentabelle dieser Stufe: die geerbte, mit neuen Laufwegen fuer
# S8 und S75.
# ============================================================================

GROUPS: Dict[str, List[TrainGroup]] = {
    lid: list(line.groups) for lid, line in VORGAENGER.lines.items() if line.groups
}
# Uebernimmt den Nordast der bisherigen S8 und faehrt bis Birkenwerder; im
# Westen gibt sie den Spandauer Ast an die S3 zurueck und endet in
# Charlottenburg. Als Vollzug -- nur auf dem Aussenring-Stueck am Nordende
# faehrt die Haelfte davon, siehe die Fussnote unter der Tabelle.
GROUPS["S75"] = [
    TrainGroup("Stammzuggruppe", "Birkenwerder <> Charlottenburg", 4,
               hollow=2, note="Bucher Straße <> Hohen Neuendorf",
               note_cars=2),
    TrainGroup("Tageszuggruppe", "Wartenberg <> Warschauer Straße", 2),
]
# ... und faehrt dafuer wieder bis Spandau.
GROUPS["S3"] = [
    TrainGroup("Stammzuggruppe", "Erkner <> Spandau", 4),
    TrainGroup("Tageszuggruppe", "Erkner <> Charlottenburg", 4),
    TrainGroup("HVZ-Verstärker", "Friedrichshagen <> Ostbahnhof", 2),
]
# Beginnt jetzt am Karower Kreuz bzw. in Buch. Die Fussnote aus dem Bestand
# faellt hier weg: der schwaechere Abschnitt lag auf der Nordbahn, und die
# faehrt jetzt die S75 -- dort steht die Fussnote nun auch.
GROUPS["S8"] = [TrainGroup("Stammzuggruppe", "Wildau <> Buch", 3)]
# Aus der verlaengerten S47 geworden, die alte S46 entfaellt.
GROUPS["S46"] = [TrainGroup("Stammzuggruppe", "Spindlersfeld <> Westend", 3)]
del GROUPS["S47"]
# Die S86 entfaellt; ihren Nordast faehrt jetzt die S85.
GROUPS["S85"] = [TrainGroup("Stammzuggruppe", "Flughafen BER <> Buch", 3)]
del GROUPS["S86"]
# Faehrt mit dem BA3 durch bis Koenigs Wusterhausen.
GROUPS["S6"] = [
    TrainGroup("Stammzuggruppe", "Gartenfeld <> Königs Wusterhausen", 4),
    TrainGroup("Tageszuggruppe", "Gartenfeld <> Grünau", 4),
]
# Die beiden Zuggruppen der bisherigen S25 werden zu je einer Linie: die
# S25 faehrt als Stammzuggruppe durch bis Velten, die neue S26 als
# Tageszuggruppe nur bis Hennigsdorf -- so weit reicht auch ihr Laufweg auf
# der Karte.
GROUPS["S25"] = [TrainGroup("Stammzuggruppe", "Stahnsdorf <> Velten", 3)]
GROUPS["S26"] = [TrainGroup("Tageszuggruppe", "Stahnsdorf <> Hennigsdorf", 3)]


NET = VORGAENGER.derive(
    stations=[*RESTYLED_STATIONS, *NEW_STATIONS],
    lines={**LINIEN, "S86": REMOVE, "S47": REMOVE},
    colors={"S26": bestand.LINE_COLORS["S25"]},
    groups=GROUPS,
    corridors={**AUSSENRING_CORRIDORS, **BA3_CORRIDORS},
    badge_offsets={
        # Das S8-Signet sitzt unter dem Namen, muss dort aber nach rechts
        # ausweichen: unter dem Kreuz laufen S2 und S8 als Paar, dazu die S75
        # diagonal durch -- an der Standardstelle liegt es auf der S75.
        "karower_kreuz": (14.0, 0.0),
    },
    # Buch traegt seine Signete jetzt wieder ganz normal unter dem Namen --
    # der steht rechts der Station, und dort ist darunter Platz. Auf der
    # Gegenecke (aus der Vorstufe geerbt) laeuft in dieser Stufe die
    # Beschriftung der neuen Aussenring-Halte durch.
    badge_opposite_corner={
        "buch": REMOVE,
        # Hennigsdorf ist mit der S26 zum Endbahnhof geworden. Sein Name
        # steht unten links (aus der Vorstufe), dort laeuft die Strecke aber
        # weiter nach Velten -- das Signet geht deshalb auf die Gegenecke
        # nach oben rechts, waehrend der Name bleibt, wo er ist.
        "hennigsdorf": True,
    },
    # Die beiden Linien der City-S-Bahn kreuzen den Suedosten als
    # durchgehendes Paar -- sie bleiben dabei sichtbar oben, statt unter
    # jeder einzelnen Diagonalen zu verschwinden.
    crossing_over={"S6": ("S8", "S9", "S85"), "S46": ("S8", "S9", "S85")},
    # Nur die linke Kante ist gesetzt; die Hoehe rechnet der Renderer aus
    # (siehe `legend_at` in `Net`): die Tabelle haengt so tief, dass unter
    # ihr genau so viel Luft bleibt wie ueber und neben ihr.
    legend_at=(-60.0, None),
)

ZIEL = FilePath("outputs/berlin_sbahn_2040plus.svg")


# Blendet die grauen Hilfstrassen mit ein -- die Trassenmitten, um die
# herum die Linien ihren Spurversatz bekommen.
TRASSEN = False


if __name__ == "__main__":
    write_map(
        NET,
        sys.argv[1] if len(sys.argv) > 1 else ZIEL,
        draw_corridors=TRASSEN,
    )
