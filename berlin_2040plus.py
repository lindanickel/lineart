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
# S1 -- und dann ueber die Cheruskerkurve auf den Suedring. Die Kurve trifft
# den Ring zwischen Schoeneberg und Suedkreuz, Schoeneberg selbst liegt nicht
# daran (siehe `_BA3`). Ab Suedkreuz faehrt die S6 den Weg der S46 bis Koenigs
# Wusterhausen.
# ============================================================================
# NORD-SUED-TUNNEL -- schnurgerade durch den Potsdamer Platz
# ============================================================================
#
# Beide Trassen laufen hier ohne einen einzigen Knick durch: der alte
# Tunnel von Anhalter Bahnhof bis Friedrichstrasse, und die City-S-Bahn vom
# Hauptbahnhof bis Gleisdreieck. Zwei Geraden nebeneinander koennen sich
# aber keinen Knoten teilen -- der Potsdamer Platz hat deshalb ZWEI: den
# alten auf der Tunnelgeraden und `potsdamer_platz_city` auf der der
# City-S-Bahn, eine halbe Trassenbreite (1.8) daneben. Gezeichnet wird
# trotzdem ein Bahnhof: die Pille des alten Knotens spannt ueber beide
# (`pill_with`), der zweite Knoten bleibt ohne eigenen Marker.
#
# Die Hoehen sind genau die der frueheren Schraegen-Fassung, damit sich an
# Gleisdreieck, Anhalter Bahnhof und allem weiter suedlich nichts
# verschiebt.


# ============================================================================
# STELLSCHRAUBEN -- Hauptbahnhof bis Schoeneberg
# ============================================================================
#   Friedrichstrasse (Stadtbahn)
#                  _FS_BRANDENBURGER
#   Brandenburger Tor
#                  _BRANDENBURGER_PP
#   Potsdamer Platz          (der Ast vom Hauptbahnhof kommt hier schraeg an)
#                  _PP_ANHALTER (fest, gleiche Hoehe wie Anhalter Bahnhof)
#   Gleisdreieck --- elastisch (FlexPath), dann Turn(45), dann fix 1.2 ------
#   Yorckstrasse (Grossgoerschenstrasse)
#                  _YORCK_JLB
#   Julius-Leber-Bruecke
#                  _JLB_SCHOENEBERG_ABSTAND
#   Schoeneberg (auf dem Ring, in der Hoehe fest)

_STADTBAHN_HBF_FS = 1.8   # Breite des Knotens: Hauptbahnhof <-> Friedrichstrasse
_ANHALTER_GERADE = 0.4    # Gerade an Gleisdreieck / Anhalter Bahnhof
_DEHNUNG_YORCK = -0.3     # kommt auf allen drei Aesten oben drauf
_YORCK_JLB = 1.0          # Yorckstrasse (Grossgoerschenstrasse) <-> Julius-Leber-Bruecke
# Knick hinter Gleisdreieck <-> Yorckstrasse (Grossgoerschenstrasse)
_YORCK_GD_ABSTAND = 1.0
_ANHALTER_YORCK = 2.8     # Hoechstabstand Anhalter Bahnhof <-> Yorckstrasse
# Der 135-Grad-Knick zwischen Wedding und Perleberger Bruecke. Der erste
# Wert gehoert der Trassenmitte, die beiden anderen sind die Boegen, die S15
# und S25 dort wirklich zeichnen (siehe `hbf_zulauf_wedding`).
_WEDDING_RADIUS = 0.4     # Trassenmitte
_WEDDING_RADIUS_S15 = 0.9 # aussen: weiter Bogen
_WEDDING_RADIUS_S25 = 0.4 # innen: Standard fuer 135 Grad

# Radius der Cheruskerkurve -- je spitzer (kleiner), desto knapper der Bogen.
# Wie ueberall bei 135 Grad der Standard (`CFG.netz.curve_radius_135`).
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

# Die Hoehen im alten Tunnel. Die Vorstufe hat die beiden kurzen Kanten am
# Potsdamer Platz gedehnt, um dort Platz fuer die einfaedelnde S25 zu
# schaffen; die faehrt hier ueber Gleisdreieck, der Tunnel geht deshalb
# zurueck auf Bestandsmass (siehe `_TUNNEL_FORMEN`). Die Werte kommen direkt
# aus dem Bestand bzw. der Vorstufe, damit sie nicht auseinanderlaufen.
_FS_BRANDENBURGER = 2.4   # Friedrichstrasse <-> Brandenburger Tor
_BRANDENBURGER_PP = bestand._BRANDENBURGER_PP
_PP_ANHALTER = bestand._PP_ANHALTER

# Hoehe des Asts Hauptbahnhof -> Potsdamer Platz (City), ein einziges
# gerades Bein. Sie MUSS die Summe der beiden Tunnelkanten darueber sein:
# Hauptbahnhof und Friedrichstrasse liegen auf derselben Hoehe (Stadtbahn),
# also liegen auch die beiden Potsdamer Plaetze nur dann nebeneinander --
# und nur dann deckt eine Pille beide sauber ab.
_STADTBAHN_PP = _FS_BRANDENBURGER + _BRANDENBURGER_PP


_TUNNEL_FORMEN = [
    # Der alte Tunnel behaelt Form und Laengen des BESTANDS: von der
    # Friedrichstrasse bis zum Anhalter Bahnhof eine einzige Gerade. Die
    # Kante Friedrichstrasse <-> Brandenburger Tor kommt unveraendert
    # durch; die beiden kurzen am Potsdamer Platz nimmt die Vorstufe laenger
    # und werden hier zurueckgesetzt.
    (["anhalter_bahnhof", FixPath(vorstufe._PP_ANHALTER), "potsdamer_platz"],
     ["anhalter_bahnhof", FixPath(_PP_ANHALTER), "potsdamer_platz"]),
    (["potsdamer_platz", FixPath(vorstufe._BRANDENBURGER_PP),
      "brandenburger_tor"],
     ["potsdamer_platz", FixPath(_BRANDENBURGER_PP), "brandenburger_tor"]),
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
    # Gerade weiter nach Sueden, dieselbe Hoehe wie Potsdamer Platz ->
    # Anhalter Bahnhof im alten Tunnel: so liegt Gleisdreieck auf der Hoehe
    # des Anhalter Bahnhofs.
    FixPath(_PP_ANHALTER),
    "gleisdreieck",
    FlexPath(), Turn(45),
    # So dicht an den Knick, wie es geht. Kuerzer als 1.0 beschneidet der
    # Zeichner die Boegen: das Buendel wechselt in diesem Knick zugleich die
    # Spur (von `potsdamer_platz_ast` auf `wannseebahn_s6`), und der Versatz
    # verschiebt den Scheitel so weit auf das Bein, dass fuer die Tangente
    # nichts mehr bleibt. Gemessen faellt die S15 bei 0.9 auf Radius 0.71,
    # bei 0.6 auf eine scharfe Ecke.
    FixPath(_YORCK_GD_ABSTAND),
    "yorckstrasse_grossgoerschenstrasse",
]

# Geerbter (Vorstufen-)Weg der S15 ueber den Anhalter Bahnhof, zum Splicen
# auf `_POTSDAMER_YORCK`.
_S15_UEBER_ANHALTER: List[Step] = [
    FixPath(vorstufe._PP_ANHALTER),
    "anhalter_bahnhof",
    FixPath(0.6), Turn(45), FixPath(1.15),
    "yorckstrasse_grossgoerschenstrasse",
]

# Geerbter (Bestands-)Weg der S25 ueber den Anhalter Bahnhof, zum Splicen
# auf `_YORCK_GLEISDREIECK`. Die S26 (unten) behaelt diesen Weg unveraendert.
_S25_UEBER_ANHALTER: List[Step] = [
    "yorckstrasse", FixPath(1.4),
    "anhalter_bahnhof", FixPath(vorstufe._PP_ANHALTER),
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
    # Beide Knicke mit dem Standardradius. Damit seine Tangente (0.33)
    # zwischen ihnen unterkommt, braucht die Schraege ein Stueck Laenge --
    # siehe `_ANHALTER_YORCK`.
    FlexPath(), Turn(-45),
    FixPath(_SCHRAEGE),
    # Unbeschrifteter Wegpunkt im Scheitel des zweiten Knicks. Er teilt die
    # Schraege in zwei Kanten, und nur deshalb kann die S25 GENAU in diesem
    # Bogen von der halben auf die ganze Spur einschwenken: ein Spurwechsel
    # wird in der Kurve uebernommen, die auf die neue Kante fuehrt.
    "gleisdreieck_schraege",
    # Ohne Laenge: der Knick sitzt genau auf dem Wegpunkt, die Schraege wird
    # durch ihn nicht laenger. Der Radius steht hier ausgeschrieben, obwohl
    # es der Standardwert ist: neben einem Bein der Laenge 0 rechnet der
    # Loeser null Platz fuer die Tangente aus und deckelte den Bogen sonst
    # auf eine scharfe Ecke. Ein ausdruecklich gesetzter Radius wird nicht
    # gedeckelt -- und Platz ist in Wahrheit da, das Bein davor ist die
    # ganze Schraege.
    FixPath(0.0), Turn(45, radius=CFG.netz.curve_radius_45),
    FlexPath(),
    "gleisdreieck",
    FixPath(_PP_ANHALTER),
    "potsdamer_platz",
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

# Abstand Priesterweg -- Suedkreuz (Bestand: 2.0) und Friedrichsfelde Ost --
# Springpfuhl (Bestand: 2.2), siehe die Begruendungen in `_FORMEN`.
_PRIESTERWEG_SUEDKREUZ = 2.4
_FFO_SPRINGPFUHL = 1.6

# Zwei Formaenderungen im Suedosten, fuer jede Linie an der Stelle und in
# beiden Fahrtrichtungen (eine Kante muss ueberall dieselbe Form haben).
# Die Nordbahn zwischen Wilhelmsruh und Hohen Neuendorf wird elastisch. In
# allen Stufen davor liegt sie auf dem starren Standardabstand; hier geht das
# nicht mehr auf, weil der Aussenring mit Bucher Strasse und Schoenlinder
# Strasse und die Tangente ueber das Karower Kreuz eine weitere Schleife um
# Hohen Neuendorf schliessen (ohne die Dehnung bleibt ein Restfehler von
# 0.043 stehen). `group` koppelt die fuenf Abschnitte, sie bleiben also
# untereinander gleich lang -- der Loeser waehlt nur, wie lang.
#
# Je Kante ein eigenes Paar statt eines Musters ueber die ganze Strecke: die
# S15 endet in Frohnau und enthaelt das lange Muster deshalb gar nicht, die
# S1 faehrt durch. Mit Einzelkanten trifft die Umformung beide.
_NORDBAHN_STATIONEN = [
    "wilhelmsruh", "wittenau", "waidmannslust", "hermsdorf", "frohnau",
    "hohen_neuendorf",
]
_NORDBAHN_ELASTISCH = [
    ([a, b], [a, FlexPath(group="nordbahn"), b])
    for a, b in zip(_NORDBAHN_STATIONEN, _NORDBAHN_STATIONEN[1:])
]

# Die drei Stellschrauben suedlich davon. Sie legen fest, wie weit
# Wilhelmsruh nach Norden rueckt -- und damit, was den fuenf gekoppelten
# Abschnitten darueber noch bleibt. Ohne sie fallen die zu lang aus (1.55
# statt 1.39) und die Nordbahn rutscht als Ganzes nach Sueden. Die Werte
# gelten nur hier; bis zur Vorstufe hat die Nordbahn ueberall die Abstaende
# des Bestands.
_NORDBAHN_STELLSCHRAUBEN = [
    (["bornholmer_strasse", FixPath(2.4), "wollankstrasse"],
     ["bornholmer_strasse", FixPath(2.8), "wollankstrasse"]),
    (["wollankstrasse", FixPath(1.0), "schoenholz"],
     ["wollankstrasse", FixPath(1.2), "schoenholz"]),
    (["schoenholz", FixPath(2.0), "wilhelmsruh"],
     ["schoenholz", FixPath(2.2), "wilhelmsruh"]),
    # Der Abzweig der S25 auf die Kremmener Bahn haengt an derselben Ecke.
    (["schoenholz", FixPath(0.8), Turn(-45)],
     ["schoenholz", FixPath(1.0), Turn(-45)]),
]

_FORMEN = [
    *_NORDBAHN_ELASTISCH,
    *_NORDBAHN_STELLSCHRAUBEN,
    *_TUNNEL_FORMEN,
    (["hauptbahnhof", FixPath(1.8), "friedrichstrasse"],
     ["hauptbahnhof", FixPath(_STADTBAHN_HBF_FS), "friedrichstrasse"]),
    # Ast Hauptbahnhof -> Potsdamer Platz (City): ein einziges gerades Bein
    # statt der geerbten Schraege. Die Trasse laeuft vom Hauptbahnhof bis
    # Gleisdreieck senkrecht durch, deshalb liegt ihr Potsdamer Platz
    # westlich des alten.
    ([FlexPath(), Turn(-45), FlexPath(), "hauptbahnhof_schraege",
      FlexPath(), Turn(45)],
     [FixPath(_STADTBAHN_PP)]),
    # Anhalter Bahn: S2/S25 von Yorckstrasse in den Tunnel -- elastisch,
    # damit sie sich an die Lage des Anhalter Bahnhofs anpasst, aber nach
    # oben gedeckelt: seit der Tunnel kuerzer ist, liegt der Anhalter
    # Bahnhof weiter noerdlich, und ohne Deckel zoege die Kante die
    # Yorckstrasse einfach mit nach unten.
    (["yorckstrasse", FixPath(1.4), "anhalter_bahnhof"],
     ["yorckstrasse", FlexPath(max_length=_ANHALTER_YORCK), "anhalter_bahnhof"]),
    # Laengeres erstes Bein vor Baumschulenweg, Platz fuer alle vier Spuren.
    (bestand._KURVE_BAUMSCHULENWEG_NEUKOELLN,
     [FixPath(2.4), *bestand._KURVE_BAUMSCHULENWEG_NEUKOELLN[1:]]),
    # Der 135-Grad-Knick zwischen Wedding und Perleberger Bruecke. Der
    # Radius gehoert zur Trasse: S15 und S25 fahren denselben Bogen, der
    # Spurversatz macht daraus zwei konzentrische Kurven. Ein eigener Wert je
    # Linie ist nicht moeglich -- der Loeser weist zwei Geometrien auf einer
    # gemeinsamen Kante ab.
    (["wedding", FixPath(2.8), Turn(-135, radius=0.4)],
     ["wedding", FixPath(2.8), Turn(-135, radius=_WEDDING_RADIUS)]),
    # Plaenterwald soll stehen bleiben -- die Dehnung faellt ganz auf das
    # Stueck nach Baumschulenweg.
    (bestand._KURVE_TREPTOW_PLAENTERWALD,
     [*bestand._KURVE_TREPTOW_PLAENTERWALD[:-1], FixPath(4.336)]),
    # Priesterweg rueckt von Suedkreuz ab: die Pille ist dort mit der S6 auf
    # vier Ringspuren gewachsen und reicht so weit nach Sueden, dass der
    # naechste Halt fast an ihr klebt.
    (["priesterweg", FixPath(bestand._PRIESTERWEG_SUEDKREUZ), "suedkreuz"],
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
    # Zwischen Anhalter Bahnhof und Friedrichstrasse steht keine eigene
    # Spurvorgabe mehr: dort gilt durchgehend `nord_sued_s1` (S1 auf -0.5,
    # S2 auf 0.5) -- beide Linien laufen also ohne Spurwechsel durch den
    # Potsdamer Platz.
    "nord_sued_s1_gesundbrunnen": Corridor(
        # Ab Potsdamer Platz wieder die Bestandsspur, bis Gesundbrunnen.
        # Die S25 zweigt schon seit der Vorstufe zum Hauptbahnhof ab -- hier
        # faehrt nur noch die S26 auf ihrer alten Trasse, die den Wert 0.5
        # automatisch ueber ihre Familie "S2" bekommt.
        steps=["potsdamer_platz", "brandenburger_tor", "friedrichstrasse",
               "oranienburger_strasse", "nordbahnhof", "humboldthain",
               "gesundbrunnen"],
        offsets={"S1": -0.5, "S2": 0.5},
    ),
    "potsdamer_platz_ast": Corridor(
        # S25 aussen, daneben die S6 auf der Mitte, aussen die S15 -- die
        # beiden haben gegenueber der Vorstufe die Plaetze getauscht. Der
        # Tausch steht schon in den Zulaeufen von Wedding und Westhafen
        # (`hbf_zulauf_*`), damit er nicht erst im Knick an der Perleberger
        # Bruecke stattfindet: eine Spur wechselt immer erst in der naechsten
        # Kurve, und dort waeren S6 und S15 sichtbar uebereinander gestiegen.
        steps=["perleberger_bruecke", "hauptbahnhof", "potsdamer_platz_city",
               "gleisdreieck", "yorckstrasse_grossgoerschenstrasse"],
        offsets={"S6": 0.0, "S25": -1.0, "S15": 1.0},
    ),
    # Die neue Schraege "yorckstrasse" -> Gleisdreieck hat sonst KEINEN
    # Korridor (die S25 faehrt dort allein) und bekommt deshalb automatisch
    # die Mittelspur (0.0) -- exakt die der S15 auf `potsdamer_platz_ast`
    # gleich danach. Da zwischen der Schraege und Hauptbahnhof kein Knick
    # mehr liegt (alles eine gerade Strecke), wird `potsdamer_platz_ast`s
    # Wert dort nie uebernommen: die S25 muss ihre Spur schon hier bekommen.
    "yorckstrasse_gleisdreieck_zulauf": Corridor(
        # Auf der Schraege liegt die S25 noch auf der halben Spur ihrer
        # Familie, wie suedlich der Yorckstrasse auch.
        steps=["yorckstrasse", "gleisdreieck_schraege"],
        offsets={"S25": 0.5},
    ),
    "gleisdreieck_zulauf_schraege": Corridor(
        # Ab dem Scheitel des Knicks die ganze Spur: die S25 schwenkt im
        # Bogen selbst ein und liegt am Gleisdreieck schon richtig.
        steps=["gleisdreieck_schraege", "gleisdreieck"],
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
    "hbf_zulauf_wedding": replace(
        VORGAENGER.corridors["hbf_zulauf_wedding"],
        # Der 135-Grad-Knick liegt auf dieser Kante, und beide Linien
        # WECHSELN genau in ihm die Spur -- die S15 von der Ringspur, die
        # S25 von der des Hauptbahnhof-Astes. Konzentrisch ist dort nicht
        # definiert (die beiden laufen vor und hinter dem Bogen verschieden
        # weit auseinander), deshalb hier der Notausgang `radii` statt
        # `radius_at`: jede Linie bekommt ihren Bogen ausgeschrieben. Hinter
        # dem Bogen trennen sie sich ohnehin. Werte wie bei S15 und S85 an
        # derselben Stelle im 2030er Netz.
        radii={"S15": _WEDDING_RADIUS_S15, "S25": _WEDDING_RADIUS_S25},
        # S15 neben der mittig laufenden S6, wie suedlich davon auch: so
        # behaelt das Buendel vom Ring bis zum Gleisdreieck dieselbe
        # Reihenfolge. Vorzeichen: S15 und S25 befahren diese Kante
        # GEGENLAEUFIG (die eine nach Sueden, die andere nach Norden) --
        # gleiches Vorzeichen hiesse deshalb dieselbe Seite. Damit sie
        # links und rechts der S6 liegen, brauchen beide +1.0.
        offsets={"S15": 1.0, "S25": 1.0},
    ),
    # Auf "perleberger_bruecke" -> "hauptbahnhof" gewinnt spaeter
    # `potsdamer_platz_ast` (S25 hier also ohne Wirkung); auf "westhafen" ->
    # "perleberger_bruecke" gilt dieser Korridor aber wirklich -- die S6
    # bekommt deshalb schon hier ihren Wert 0.0 (seit dem Tausch mit der S15
    # die Mitte), sonst haelt der Wechsel bei Perleberger Bruecke nur zur
    # Haelfte.
    "hbf_zulauf_westhafen": replace(
        VORGAENGER.corridors["hbf_zulauf_westhafen"],
        offsets={**VORGAENGER.corridors["hbf_zulauf_westhafen"].offsets,
                 "S6": 0.0, "S25": -1.0},
    ),
    # Die Schraege der Vorstufe ist hier einer Geraden gewichen; der
    # Korridor auf ihrem oberen Stueck entfaellt mit ihr (der Wegpunkt
    # selbst weiter unten in `derive`).
    "hbf_zulauf_schraege": REMOVE,
    "hbf_potsdamer_platz": replace(
        VORGAENGER.corridors["hbf_potsdamer_platz"],
        # Fuehrt jetzt zum eigenen Knoten der City-S-Bahn. S6 und S15 haben
        # ihre Spuren getauscht (geerbt war S15 0.5, S6 1.5).
        steps=["hauptbahnhof", "potsdamer_platz_city"],
        offsets={"S6": 0.5, "S15": 1.5, "S25": -1.0},
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
    # Die S85 faehrt seit der Vorstufe von Buch herunter; auf diesem Stueck
    # kommt hier nur das Karower Kreuz dazu. Sie befaehrt es von Norden,
    # deshalb das gespiegelte Muster.
    "S85": VORGAENGER.splice_line(
        "S85",
        bestand._reversed(_BLANKENBURG_BUCH_ALT),
        bestand._reversed(_BLANKENBURG_BUCH_NEU),
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
    # Die S25 faehrt seit der Vorstufe ueber den Hauptbahnhof; hier wechselt
    # sie im Sueden zusaetzlich vom Anhalter Bahnhof auf die Spur der S15/S6
    # ueber Gleisdreieck. Die S26 behaelt den alten Weg durch den
    # Nord-Sued-Tunnel und bekommt nur den kuerzeren Zulauf zur Yorckstrasse.
    "S25": replace(
        VORGAENGER.lines["S25"],
        steps=splice(
            splice(
                VORGAENGER.lines["S25"].steps,
                _SUEDKREUZ_YORCK_ALT, _SUEDKREUZ_YORCK_NEU,
            ),
            _S25_UEBER_ANHALTER, _YORCK_GLEISDREIECK,
        ),
    ),
    "S26": VORGAENGER.splice_line(
        "S26", _SUEDKREUZ_YORCK_ALT, _SUEDKREUZ_YORCK_NEU,
    ),
}


# ============================================================================
# UEBERNOMMENE STATIONEN, NEU BESCHRIFTET
# ============================================================================

RESTYLED_STATIONS: List[Station] = [
    # Wie im Bestand einzeilig -- der Umbruch gilt nur in 2030 und 2030plus.
    VORGAENGER.station("messe_nord_zob", label=None),
    # Endpunkt der S8 und Durchfahrt der S2 -- damit ein Umsteigepunkt.
    # Beschriftung nach rechts, die Signetreihe wie seit der 2030er Stufe
    # darunter: nordwestlich der Station stehen die Namen der neuen
    # Aussenring-Halte, dort ist kein Platz mehr.
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
]

def _city_potsdamer(line: TurnLine) -> TurnLine:
    """Setzt den Potsdamer Platz der City-S-Bahn ein.

    S6, S15 und S25 halten dort auf ihrer eigenen Geraden, nicht auf der des
    alten Tunnels -- fuer sie heisst der Bahnhof deshalb
    `potsdamer_platz_city`. Jede der drei beruehrt ihn genau einmal (die S25
    faehrt seit dieser Stufe nicht mehr durch den alten Tunnel), ein
    einfaches Ersetzen genuegt also.
    """
    return replace(line, steps=[
        "potsdamer_platz_city" if s == "potsdamer_platz" else s
        for s in line.steps
    ])


# Neu und noch nicht gebaut: Klammern um den Namen, blasser Stationspunkt.
# Die beiden Formaenderungen gelten fuer jede Linie, die die Stellen
# befaehrt -- auch fuer die sonst unveraenderten.
LINIEN: Dict[str, TurnLine] = {
    lid: (_city_potsdamer if lid in ("S6", "S15", "S25") else lambda l: l)(
        _umgeformt(line)
    )
    for lid, line in {**VORGAENGER.lines, **CHANGED_LINES}.items()
    # Die beiden gestrichenen Linien stehen unten in `derive`.
    if lid != "S47"
}


NEW_STATIONS: List[Station] = [
    planned(s) for s in (*AUSSENRING_STATIONS, *S75_STATIONS)
]
# Der Potsdamer Platz der City-S-Bahn: eigener Knoten auf deren Geraden,
# 1.8 westlich des alten. Ohne eigenen Marker und ohne Beschriftung -- beides
# traegt der alte Knoten, dessen Pille ueber beide spannt (`pill_with`).
NEW_STATIONS.append(
    Station("potsdamer_platz_city", "Potsdamer Platz", label="",
            hidden=True, pill_with="potsdamer_platz")
)
# Reiner Korridor-Wegpunkt im Knick der S25-Schraege, siehe
# `_YORCK_GLEISDREIECK`: ohne Punkt, ohne Namen.
NEW_STATIONS.append(
    Station("gleisdreieck_schraege", "Gleisdreieck-Schraege", label="",
            hidden=True)
)
# Gleisdreieck steht schon im Bestand, faehrt aber erst der BA3 an.
NEW_STATIONS.append(planned(VORGAENGER.stations["gleisdreieck"]))


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
               hollow=2, note="Bucher Straße <> Birkenwerder",
               note_cars=2),
    TrainGroup("Tageszuggruppe", "Wartenberg <> Ostbahnhof", 2),
]
# ... und faehrt dafuer wieder bis Spandau.
GROUPS["S3"] = [
    TrainGroup("Stammzuggruppe", "Erkner <> Spandau", 4),
    TrainGroup("Tageszuggruppe", "Erkner <> Charlottenburg", 4),
    TrainGroup("HVZ-Verstärker", "Friedrichshagen <> Ostbahnhof", 2),
]
# Beginnt jetzt am Karower Kreuz bzw. in Buch. Die Fussnote aus der Vorstufe
# faellt hier weg: der schwaechere Abschnitt lag auf der Nordbahn, und die
# faehrt jetzt die S75 -- dort steht die Fussnote nun auch. Damit faehrt die
# S8 auf ihrer ganzen Laenge den Vollzug, den sie seit der Vorstufe hat.
GROUPS["S8"] = [TrainGroup("Stammzuggruppe", "Wildau <> Buch", 4)]
# Aus der verlaengerten S47 geworden, die alte S46 entfaellt.
GROUPS["S46"] = [TrainGroup("Stammzuggruppe", "Spindlersfeld <> Westend", 3)]
del GROUPS["S47"]
# Faehrt mit dem BA3 durch bis Koenigs Wusterhausen.
GROUPS["S6"] = [
    TrainGroup("Stammzuggruppe", "Gartenfeld <> Königs Wusterhausen", 4),
    TrainGroup("Tageszuggruppe", "Gartenfeld <> Grünau", 4),
]

NET = VORGAENGER.derive(
    stations={
        **{st.id: st for st in (*RESTYLED_STATIONS, *NEW_STATIONS)},
        # Der Wegpunkt in der Schraege der Vorstufe -- die gibt es hier nicht
        # mehr, siehe `_FORMEN`.
        "hauptbahnhof_schraege": REMOVE,
    },
    lines={**LINIEN, "S47": REMOVE},
    groups=GROUPS,
    corridors={**AUSSENRING_CORRIDORS, **BA3_CORRIDORS},
    # Die S85 faehrt von Pankow durch bis Buch -- ohne Tag braucht der Name
    # dort auch keinen Versatz mehr. Julius-Leber-Bruecke steht mit der S6
    # und der gedehnten Kante nach Yorckstrasse anders; dort bleibt der
    # Name auf seiner Standardstelle.
    label_offsets={"julius_leber_bruecke": REMOVE},
    badge_offsets={
        # Das S8-Signet sitzt unter dem Namen, muss dort aber nach rechts
        # ausweichen: unter dem Kreuz laufen S2 und S8 als Paar, dazu die S75
        # diagonal durch -- an der Standardstelle liegt es auf der S75.
        "karower_kreuz": (14.0, 0.0),
    },
    # Zwei Beschriftungen von Hand, gemessen ab der Stationsmitte (x nach
    # rechts, y nach unten). Beide Stellen sind Sonderfaelle, an denen die
    # automatische Lage nichts Besseres finden kann.
    label_override={
        # Karower Kreuz liegt in einem Andreaskreuz: die Stettiner Bahn von
        # Suedwest nach Nordost, die Gerade der S75 von Nordwest nach
        # Suedost. Der Name steht im oestlichen Zwickel, dicht hinter der
        # hellgruenen S8 (die reicht auf dieser Hoehe bis 16) und mit der
        # MITTE der Zeile auf Stationshoehe -- daher die 7 statt 3.5, die
        # Grundlinie liegt eine halbe Versalhoehe tiefer.
        "karower_kreuz": (18.5, 7.0, "start"),
        # Zwei Zeilen ueber dem Knick, und darunter folgt gleich
        # "Julius-Leber-Bruecke". Weiter nach oben und nach rechts, damit
        # zwischen den beiden Bloecken Luft bleibt.
        "yorckstrasse_grossgoerschenstrasse": (-6.0, -20.0, "end"),
    },
    # Die beiden Linien der City-S-Bahn kreuzen den Suedosten als
    # durchgehendes Paar -- sie bleiben dabei sichtbar oben, statt unter
    # jeder einzelnen Diagonalen zu verschwinden.
    crossing_over={
        # Die S6 liegt oben, wo sie andere Linien kreuzt: im Suedosten die
        # Diagonalen von S8/S85 und S9, bei Westhafen die S15, mit der sie
        # dort die Plaetze tauscht. Sie faehrt in beiden Faellen ueber die
        # andere hinweg und bekommt deshalb dort den weissen Rand.
        "S6": ("S1", "S8", "S9", "S85"),
        "S46": ("S8", "S9", "S85"),
    },
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
