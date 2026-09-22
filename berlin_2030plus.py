"""
Netzdefinition Berlin: Ausbaustufe 2030plus.

Ausfuehren erzeugt die Karte:

    python3 berlin_2030plus.py [ziel.svg]  ->  outputs/berlin_sbahn_2030plus.svg

Zweite Stufe. Sie beschreibt nur den UNTERSCHIED zu `berlin_2030` -- alles
Uebrige erbt `Net.derive()` am Ende der Datei.

Inhalt:

  * BA2 der City-S-Bahn: Tunnel vom Hauptbahnhof zum Potsdamer Platz. S6 und
    S15 enden nicht mehr am Hauptbahnhof, sondern fahren durch; die S15
    weiter durch den bestehenden Nord-Sued-Tunnel bis Zehlendorf.
  * Damit gibt die S85 die Nordbahn ab -- die S15 uebernimmt sie und faehrt
    von Frohnau durch. Die S85 faengt stattdessen im Norden auf der
    Stettiner Bahn an, in Buch: die S86 entfaellt, ihr Nordast Pankow --
    Buch gehoert jetzt der S85, ihr Suedast ist ohnehin deren Weg.
  * S25 nach Norden bis Velten und nach Sueden bis Stahnsdorf, dazu
    Borsigwalde auf der zweigleisig ausgebauten Kremmener Bahn. Ihre beiden
    Zuggruppen werden zu je einer Linie: die S25 faehrt ueber den neuen
    Tunnel und den Hauptbahnhof bis Velten, die S26 auf dem alten Weg durch
    den Nord-Sued-Tunnel bis Hennigsdorf. Ihren frueheren Nordast Pankow --
    Blankenburg gibt die S26 an die S2 ab.
  * Neuer Halt Kamenzer Damm auf der Dresdner Bahn (S2).

Nicht hier, sondern erst in `berlin_2040plus`: BA3 der City-S-Bahn und die
Nahverkehrstangente samt Karower Kreuz.
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
import berlin_2030 as vorstufe

# Das Netz, auf dem dieses hier aufbaut.
VORGAENGER = vorstufe.NET


# ============================================================================
# CITY-S-BAHN BA2 -- Hauptbahnhof bis Potsdamer Platz
# ============================================================================

# Die beiden kurzen Kanten um den Potsdamer Platz werden laenger als im
# Bestand (dort beide 1.2). Der Grund ist der neue Ast: die S25 faedelt hier
# vom Hauptbahnhof in den alten Tunnel ein und kreuzt dabei die S1. Mit den
# Bestandsmassen liegt diese Kreuzung mitsamt ihrem weissen Rand auf dem
# Stationspunkt des Brandenburger Tors. Die laengeren Kanten schieben sie
# nach unten, weg von der Station; gemessen bleiben zwischen Stationspunkt
# und Rand knapp 4 px. Nach oben ist dafuer mehr Platz noetig als nach
# unten, deshalb sind die beiden Werte verschieden. Spaetere Stufen brauchen
# den laengeren Tunnel nicht (die S25 faehrt dort ueber Gleisdreieck) und
# nehmen ihn wieder auf Bestandsmass.
_BRANDENBURGER_PP = 1.8   # Bestand 1.2
_PP_ANHALTER = 1.4        # Bestand 1.2

# Eine Kante muss in ALLEN Linien dieselbe Form haben. Die Dehnung wird
# deshalb ueber jede Linie gelegt, die die Stelle befaehrt -- auch ueber die
# sonst unveraenderten -- und zwar in beiden Fahrtrichtungen.
_TUNNEL_DEHNUNGEN = [
    (["anhalter_bahnhof", FixPath(bestand._PP_ANHALTER), "potsdamer_platz"],
     ["anhalter_bahnhof", FixPath(_PP_ANHALTER), "potsdamer_platz"]),
    (["potsdamer_platz", FixPath(bestand._BRANDENBURGER_PP), "brandenburger_tor"],
     ["potsdamer_platz", FixPath(_BRANDENBURGER_PP), "brandenburger_tor"]),
]


def _gedehnt(line: TurnLine) -> TurnLine:
    """Dieselbe Linie mit dem laengeren Tunnel -- unveraendert, wo sie die
    beiden Kanten gar nicht befaehrt."""
    steps = list(line.steps)
    for alt, neu in _TUNNEL_DEHNUNGEN:
        for a, n in ((alt, neu),
                     (bestand._reversed(alt), bestand._reversed(neu))):
            try:
                steps = splice(steps, a, n)
            except ValueError:
                pass
    return replace(line, steps=steps)


# Im Bestand endet der Tunnelast am Hauptbahnhof. Jetzt geht es weiter zum
# Potsdamer Platz, wo er auf den bestehenden Nord-Sued-Tunnel trifft. Beide
# Knicke mit dem Standardradius: er gilt fuer die innerste Spur des
# Buendels, die aeussere bekommt eine Spurbreite mehr -- ein Aufschlag von
# Hand ist dafuer nicht noetig.
#
# Unbeschrifteter Wegpunkt auf der Schraege. Er teilt sie in zwei Kanten,
# und nur deshalb bekommen alle drei Linien ihre Spuren an DERSELBEN Stelle:
# ein Spurwechsel wird immer in der Kurve uebernommen, die auf die neue
# Kante fuehrt, und die liegt fuer die gegenlaeufige S25 am anderen Ende der
# Schraege als fuer S6 und S15. Ohne die Teilung laege die S25 entweder am
# Hauptbahnhof zu dicht an der S15 oder im alten Tunnel neben ihrer Spur.
# Hinter dem unteren Knick steht kein Path: dann setzt der Turn selbst die
# Beinlaenge, bei einem Turn ohne eigenen Radius auf den halben
# Stationsabstand (0.6). Der Knick beginnt also gleich hinter dem Potsdamer
# Platz, und 0.6 ist reichlich Platz fuer die Tangente -- der Standardbogen
# braucht 0.8 * tan 22,5 Grad = 0.33, die aeussere Spur des Buendels
# 1.23 * tan 22,5 Grad = 0.51. Gequetscht wird hier also nichts.
_HBF_POTSDAMER: List[Step] = [
    FlexPath(), Turn(-45), FlexPath(),
    "hauptbahnhof_schraege",
    FlexPath(), Turn(45),
    "potsdamer_platz",
]

# Die S6 faehrt jetzt durch bis zum Potsdamer Platz.
S6 = replace(
    VORGAENGER.extend_line("S6", back=_HBF_POTSDAMER),
    direction="Gartenfeld -> Potsdamer Platz",
)

# Die S15 ebenso -- und von dort weiter im bestehenden Tunnel bis Zehlendorf.
# Sie faehrt seit der Vorstufe schon von Frohnau, hier kommt nur das Stueck
# hinten dran.
_S15_DURCHGEBUNDEN = VORGAENGER.extend_line(
    "S15",
    back=[
        *_HBF_POTSDAMER,
        FixPath(bestand._PP_ANHALTER),
        "anhalter_bahnhof",
        *bestand._reversed([FixPath(1.15), Turn(-45), FixPath(0.6)]),
        *bestand._reversed(
            bestand._slice(
                bestand._WANNSEE_BAHN,
                "zehlendorf", "yorckstrasse_grossgoerschenstrasse",
            )
        ),
    ],
)


# ============================================================================
# NORDBAHN -- S85 raus, S15 rein
# ============================================================================
#
# Die S85 gibt die Nordbahn ab und endet in Pankow; die S15 uebernimmt sie
# und faehrt statt bis Gesundbrunnen durch bis Frohnau. Damit liegt sie
# noerdlich von Gesundbrunnen auf der Spur ihrer Familie, also unter der S1.

# Die Nordbahn behaelt die Abstaende des Bestands -- von Gesundbrunnen bis
# Oranienburg sieht sie in dieser Stufe genauso aus wie dort. Nur der Anfang
# der S15 wird neu geschrieben: sie faehrt jetzt von Frohnau, also den
# Bestandsweg rueckwaerts.
_S15_NORDBAHN: List[Step] = [
    *bestand._reversed(
        bestand._slice(bestand._NORD_BAHN, "wollankstrasse", "frohnau")
    ),
    *bestand._reversed(bestand._ABSCHNITT_GESUNDBRUNNEN_WOLLANKSTRASSE),
]

# Die S85 gibt auch den Hauptbahnhof-Ast wieder ab -- den befaehrt jetzt die
# durchgebundene S15 -- und faengt stattdessen im Norden auf der Stettiner
# Bahn an: von Buch ueber Pankow, mit dem Bogen zur Bornholmer Strasse und
# weiter auf den Ring, den auch die S8 faehrt. Ab Schoenhauser Allee bleibt
# ihr Laufweg, wie er war.
#
# Damit entfaellt die S86: ihr Nordast Pankow -- Buch gehoert jetzt der S85,
# ihr Suedast von Gruenau herauf ist derselbe Weg, den die S85 ohnehin schon
# faehrt. Eine Linie weniger fuer dieselbe Strecke.
_PANKOW_BUCH: List[Step] = bestand._slice(
    bestand._STETTINER_BAHN, "pankow", "buch"
)

_S85_AB_SCHOENHAUSER = list(VORGAENGER.lines["S85"].steps)
_S85_AB_SCHOENHAUSER = _S85_AB_SCHOENHAUSER[
    _S85_AB_SCHOENHAUSER.index("schoenhauser_allee"):
]

_S85 = replace(
    VORGAENGER.lines["S85"],
    start=225,                       # Buch -> Karow, nach Suedwesten
    steps=[
        # Ohne das letzte Element: Pankow steht gleich darunter.
        *bestand._reversed(_PANKOW_BUCH)[:-1],
        "pankow",
        *bestand._reversed(bestand._KURVE_BORNHOLMER_PANKOW),
        "bornholmer_strasse",
        *bestand._KURVE_BORNHOLMER_SCHOENHAUSER,
        *_S85_AB_SCHOENHAUSER,
    ],
    direction="Buch -> Flughafen BER",
)


# ============================================================================
# KREMMENER BAHN -- S25 nach Norden
# ============================================================================

S25_NORD_STATIONS: List[Station] = [
    Station("hennigsdorf_nord", "Hennigsdorf Nord"),
    Station("velten", "Velten", label_pos="left"),
]

# Neue Zwischenstation auf dem bestehenden Ast, zwischen Eichborndamm und
# Tegel.
S25_BORSIGWALDE: List[Station] = [
    Station("borsigwalde", "Borsigwalde"),
]


# ============================================================================
# STAMMBAHN-AST -- S25 nach Sueden ueber Teltow hinaus
# ============================================================================

S25_SUED_STATIONS: List[Station] = [
    Station("iserstrasse", "Iserstraße"),
    Station("stahnsdorf", "Stahnsdorf"),
]

S25_SUED_CORRIDORS: Dict[str, Corridor] = {
    # Die S25 hat zwischen ihrem Suedende und dem Tunnel keine einzige Kurve
    # -- ihr Versatz stammt deshalb aus der ALLERERSTEN Kante und haelt bis
    # Gesundbrunnen durch. Durch die Verlaengerung ist diese erste Kante
    # nicht mehr Teltow Stadt, sondern Stahnsdorf; ohne diese Erweiterung
    # liefe die S25 mittig statt auf der Spur ihrer Familie und damit im
    # ganzen Nord-Sued-Tunnel eine halbe Spur neben der S2.
    # Schluessel wie im Bestand, damit er den dortigen Eintrag ersetzt.
    "nord_sued_s25_s26_zulauf": Corridor(
        steps=[
            "stahnsdorf", "iserstrasse",
            *VORGAENGER.corridors["nord_sued_s25_s26_zulauf"].steps,
        ],
        offsets={"S2": 0.5},
    ),
}


# ============================================================================
# DRESDNER BAHN -- neuer Halt Kamenzer Damm
# ============================================================================
#
# Zusaetzlicher Halt der S2 zwischen Marienfelde und Attilastrasse. Die
# Dresdner Bahn faehrt hier keine andere Linie, es aendert sich also nur die
# Stationsfolge der S2.

KAMENZER_DAMM_STATIONS: List[Station] = [
    Station("kamenzer_damm", "Kamenzer Damm", label_pos="top_right"),
]


# ============================================================================
# GEAENDERTE LINIEN
# ============================================================================

_s25 = VORGAENGER.extend_line(
    "S25",
    front=["stahnsdorf", "iserstrasse"],   # ueber Teltow Stadt hinaus
    back=["hennigsdorf_nord", "velten"],   # ueber Hennigsdorf hinaus
)

_S15 = replace(
    _S15_DURCHGEBUNDEN,
    # Der Nordast: die Vorstufe schickte die S15 nur bis Gesundbrunnen, jetzt
    # faehrt sie bis Frohnau. Beide Fassungen stehen ausgeschrieben, damit
    # sich das Stueck sauber austauschen laesst -- die Abstaende sind auf
    # beiden Seiten die des Bestands.
    steps=splice(
        _S15_DURCHGEBUNDEN.steps, vorstufe._S15_NORDBAHN, _S15_NORDBAHN,
    ),
    direction="Frohnau -> Zehlendorf",
)

# Der bisherige Laufweg der S25, mit beiden Verlaengerungen und Borsigwalde:
# Stahnsdorf -- Anhalter Bahnhof -- Nord-Sued-Tunnel -- Kremmener Bahn --
# Velten. Er ist die Grundlage fuer BEIDE Linien: die S26 faehrt ihn
# unveraendert (bis Hennigsdorf), die S25 weicht ab dem Potsdamer Platz auf
# den Hauptbahnhof aus.
_S25_KLASSISCH: List[Step] = insert_after(
    _s25.steps, "eichborndamm", "borsigwalde"
)

# Das Stueck, das die S25 kuenftig nicht mehr faehrt: vom Potsdamer Platz
# durch den alten Nord-Sued-Tunnel zum Gesundbrunnen.
_S25_DURCH_TUNNEL: List[Step] = bestand._slice(
    _S25_KLASSISCH, "potsdamer_platz", "gesundbrunnen"
)

# Und was an seine Stelle tritt: der Ast ueber den Hauptbahnhof, von dort
# ueber Perleberger Bruecke und Wedding zum Gesundbrunnen. Es ist genau der
# Weg der S15, rueckwaerts gelesen -- eine Kante muss fuer jede Linie
# dieselbe Form haben, ausschreiben liesse er sich also ohnehin nicht
# anders.
_S25_UEBER_HAUPTBAHNHOF: List[Step] = bestand._reversed(
    bestand._slice(_S15.steps, "gesundbrunnen", "potsdamer_platz")
)

CHANGED_LINES: Dict[str, TurnLine] = {
    "S6": S6,
    "S15": _S15,
    "S85": _S85,
    # Faehrt ab dem Potsdamer Platz ueber den Hauptbahnhof statt durch den
    # Nord-Sued-Tunnel -- der neue Tunnel bekommt damit seine dritte Linie.
    "S25": replace(
        _s25,
        steps=splice(
            _S25_KLASSISCH, _S25_DURCH_TUNNEL, _S25_UEBER_HAUPTBAHNHOF
        ),
        direction="Stahnsdorf -> Velten",
    ),
    # Die S26 uebernimmt den alten Weg der S25 durch den Nord-Sued-Tunnel --
    # der bleibt so bedient, wie er es war. Im Norden endet sie in
    # Hennigsdorf; auf dem Stueck darueber hinaus nach Velten bleibt die S25
    # allein. Ihren frueheren Nordast Pankow -- Blankenburg faehrt sie nicht
    # mehr, den bedient seit dieser Stufe die S2.
    "S26": replace(
        _s25,
        steps=bestand._slice(_S25_KLASSISCH, "stahnsdorf", "hennigsdorf"),
        direction="Stahnsdorf -> Hennigsdorf",
    ),
    "S2": replace(
        VORGAENGER.lines["S2"],
        steps=insert_after(
            VORGAENGER.lines["S2"].steps, "marienfelde", "kamenzer_damm"
        ),
    ),
}


# ============================================================================
# UEBERNOMMENE STATIONEN, NEU BESCHRIFTET
# ============================================================================

RESTYLED_STATIONS: List[Station] = [
    # Mit der S86 endet in Blankenburg keine Linie mehr, und in Gruenau
    # ebenso wenig -- beide waren nur ihretwegen Umsteigepunkte. An beiden
    # Stellen laufen die Linien nur noch als Familien nebeneinander her.
    VORGAENGER.station("blankenburg", kind="station"),
    VORGAENGER.station("gruenau", kind="station"),
    # Hier halten jetzt S1/S2/S25/S26 und zusaetzlich S6 und S15.
    VORGAENGER.station("potsdamer_platz", kind="hub", label_pos="right"),
    VORGAENGER.station("anhalter_bahnhof", label_pos="right"),
    # War bisher Endpunkt und stand deshalb links. Jetzt draengen Hennigsdorf
    # Nord und Velten von oben nach, die Beschriftung weicht nach unten aus.
    VORGAENGER.station("hennigsdorf", label_pos="bottom_left"),
]

# Alles, was neu dazukommt, ist geplant und noch nicht gebaut -- Klammern um
# den Namen, blasser Stationspunkt.
NEW_STATIONS: List[Station] = [
    planned(s)
    for s in (
        *S25_NORD_STATIONS,
        *S25_BORSIGWALDE,
        *S25_SUED_STATIONS,
        *KAMENZER_DAMM_STATIONS,
    )
]

# Reiner Korridor-Wegpunkt in der Schraege zwischen Hauptbahnhof und
# Potsdamer Platz, siehe `_HBF_POTSDAMER`: ohne Punkt, ohne Namen.
NEW_STATIONS.append(
    Station("hauptbahnhof_schraege", "Hauptbahnhof-Schraege", label="",
            hidden=True)
)


# ============================================================================
# ZUGGRUPPEN
#
# Die Legendentabelle dieser Stufe: die geerbte, mit neuen Laufwegen fuer
# die Linien, die hier anders fahren.
# ============================================================================

GROUPS: Dict[str, List[TrainGroup]] = {
    lid: list(line.groups) for lid, line in VORGAENGER.lines.items() if line.groups
}
GROUPS["S6"] = [
    TrainGroup("Stammzuggruppe", "Gartenfeld <> Potsdamer Platz", 3),
    TrainGroup("Tageszuggruppe", "Gartenfeld <> Potsdamer Platz", 3),
]
# Keine Stichfahrt mehr zum Gesundbrunnen, sondern durchgebunden von der
# Nordbahn ueber den neuen Tunnel bis Zehlendorf.
GROUPS["S15"] = [
    TrainGroup("Stammzuggruppe", "Zehlendorf <> Hbf <> Frohnau", 3),
    TrainGroup("Tageszuggruppe", "Zehlendorf <> Hbf <> Gesundbrunnen", 3),
]
# Den Verstaerker zum Potsdamer Platz fahren jetzt S15 und S6 durch den
# neuen Tunnel -- die S1 braucht ihre HVZ-Zuggruppen dafuer nicht mehr.
GROUPS["S1"] = [
    TrainGroup("Stammzuggruppe", "Wannsee <> Oranienburg"),
    TrainGroup("Tageszuggruppe", "Wannsee <> Frohnau"),
]
GROUPS["S2"] = [
    TrainGroup("Stammzuggruppe", "Blankenfelde <> Bernau"),
    TrainGroup("Tageszuggruppe", "Lichtenrade <> Bernau"),
]
# Ueber beide Enden hinaus verlaengert und in zwei Linien geteilt: die S25
# faehrt als Stammzuggruppe durch bis Velten, die S26 als Tageszuggruppe nur
# bis Hennigsdorf -- so weit reicht auch ihr Laufweg auf der Karte. Ihr alter
# Nordast Pankow -- Blankenburg faellt weg, den faehrt die S2.
GROUPS["S25"] = [TrainGroup("Stammzuggruppe", "Stahnsdorf <> Hbf <> Velten", 3)]
GROUPS["S26"] = [TrainGroup("Tageszuggruppe", "Stahnsdorf <> Hennigsdorf", 3)]
# Der Ast zum Hauptbahnhof ging an die S15; dafuer faehrt die S85 jetzt den
# Nordast der entfallenen S86 bis Buch.
#
# S8 und S85 fahren als Vollzug: die S86 hat den gemeinsamen Abschnitt von
# Gruenau bis Pankow bisher mitbedient, ihr Wegfall wird durch die laengeren
# Zuege aufgefangen. Der schwaechere Nordabschnitt der S8 bleibt bei zwei
# Viertelzuegen -- von vier sind damit zwei nur umrandet.
GROUPS["S8"] = [
    TrainGroup("Stammzuggruppe", "Wildau <> Birkenwerder", 4, hollow=2,
               note="Blankenburg <> Birkenwerder", note_cars=2),
]
GROUPS["S85"] = [TrainGroup("Stammzuggruppe", "Flughafen BER <> Buch", 4)]
del GROUPS["S86"]


NET = VORGAENGER.derive(
    stations=[*RESTYLED_STATIONS, *NEW_STATIONS],
    lines={
        **{
            lid: _gedehnt(line)
            for lid, line in {**VORGAENGER.lines, **CHANGED_LINES}.items()
            if lid != "S86"
        },
        "S86": REMOVE,
    },
    groups=GROUPS,
    corridors={
        **S25_SUED_CORRIDORS,
        # Oberes Stueck der Schraege, vom Hauptbahnhof bis zum Wegpunkt: hier
        # gelten fuer die S25 noch die Spuren des Astes (1.0, also aussen
        # neben der S15). Sie nimmt diesen Wert im oberen Knick an, dicht
        # unter dem Hauptbahnhof -- und liegt damit auch AM Hauptbahnhof
        # richtig. Fuer S6 und S15 stehen hier schon die Tunnelspuren: die
        # beiden fahren in der Gegenrichtung und uebernehmen sie ohnehin erst
        # in demselben Knick.
        "hbf_zulauf_schraege": Corridor(
            steps=["hauptbahnhof", "hauptbahnhof_schraege"],
            offsets={"S15": 0.5, "S6": 1.5, "S25": -1.0},
        ),
        "hbf_potsdamer_platz": Corridor(
            # Unteres Stueck der Schraege und das gerade Bein zum Potsdamer
            # Platz: die Spuren des alten Tunnels. S15 auf dieselbe Spur wie
            # die S1 (-0.5), S6 daneben auf -1.5, die S25 auf der ihrer
            # Familie (0.5). Die S25 faehrt damit von hier an bis zum
            # Anhalter Bahnhof ohne Spurwechsel durch.
            steps=["hauptbahnhof_schraege", "potsdamer_platz"],
            offsets={"S15": 0.5, "S6": 1.5, "S25": -0.5},
        ),
        # Auf dem Ringstueck Wedding -- Gesundbrunnen fahren wieder zwei
        # Gaeste nebeneinander -- statt S15 und S85 wie im 2030er Netz jetzt
        # S15 und S25. Die S25 nimmt die innere Gastspur, die S15 rueckt eine
        # weiter nach aussen.
        #
        # Wirksam ist hier nur der Wert der S15: sie kommt vom Gesundbrunnen
        # und uebernimmt ihn im Knick dort. Die S25 faehrt in der
        # Gegenrichtung und hat zwischen Perleberger Bruecke und
        # Gesundbrunnen ueberhaupt keine Kurve mehr -- eine Spur wird immer
        # erst in der Kurve angenommen, die auf die Kante fuehrt. Sie bleibt
        # deshalb auf dem, was sie im Knick vor Wedding bekommen hat (siehe
        # `hbf_zulauf_wedding`); der Eintrag hier steht nur, weil jede Linie
        # auf einer Korridorkante einen Versatz braucht.
        "ring": replace(
            VORGAENGER.corridors["ring"],
            offsets={**VORGAENGER.corridors["ring"].offsets,
                     "S25": -1.0, "S15": -2.0},
        ),
        # Die S85 verlaesst den Hauptbahnhof-Ast wieder; sie war es, fuer die
        # die Vorstufe hier einen eigenen Korridor brauchte. Die neue Paarung
        # S15/S25 steht schon in `ring` -- der Sonderfall entfaellt.
        "ring_gesundbrunnen_wedding": REMOVE,
        "hbf_zulauf_wedding": Corridor(
            # Der Wert der S25 gilt weiter als nur fuer diese Kante: sie
            # nimmt ihn im 135-Grad-Knick an und faehrt damit ohne weitere
            # Kurve durch Wedding bis zum Gesundbrunnen. Er legt also auch
            # ihre Spur auf dem Ringstueck fest (siehe `ring`), und weil sie
            # der S15 dort entgegenkommt, hat er das umgekehrte Vorzeichen.
            steps=["wedding", "perleberger_bruecke"],
            offsets={"S1": 0.0, "S25": 1.0},
        ),
        "hbf_zulauf_westhafen": Corridor(
            # Zwischen Perleberger Bruecke und Hauptbahnhof liegen die drei
            # Linien symmetrisch um die Trassenmitte: S6 im Westen (-1.0),
            # S15 auf der Mitte (0.0), S25 im Osten (1.0). Der Wert der S25
            # ist derselbe wie auf `hbf_potsdamer_platz` -- gebraucht wird er
            # von dort, hier steht er nur, weil jede Linie auf einer
            # Korridorkante einen Versatz braucht.
            steps=["westhafen", "perleberger_bruecke", "hauptbahnhof"],
            offsets={"S6": 1.0, "S1": 0.0, "S25": -1.0},
        ),

    },
    # Wo die S25 vom Ast des Hauptbahnhofs in den alten Tunnel einschwenkt,
    # kreuzt sie die S1. Sie liegt dort oben und bekommt den weissen Rand --
    # dieselbe Regel wie an der Kremmener Kurve bei Schoenholz, wo die
    # abzweigende S2-Familie ebenfalls ueber der durchgehenden S1 liegt.
    crossing_over={"S25": ("S1",)},
    # Hennigsdorf ist mit der S26 wieder Endbahnhof geworden. Sein Name steht
    # unten links, und dort laeuft die Strecke weiter nach Velten -- das Tag
    # darunter geriete zwischen Name und Gleis. Es geht deshalb auf die
    # Gegenecke nach oben rechts, waehrend der Name bleibt, wo er ist. Ein
    # Versatz von Name und Tag zusammen (wie in Buch oder Blankenburg) wurde
    # probiert und sieht hier gedraengter aus.
    badge_opposite_corner={"hennigsdorf": True},
    # Hier enden die Tageszuege der S15 (umrandetes Signet). Unter dem Namen
    # laege es auf der S15, die von Westen einbiegt; Platz dafuer gaebe es
    # nur, wenn der Name bis an Bornholmer Strasse hinaufrueckt. Das Signet
    # steht deshalb ausnahmsweise ueber dem Namen.
    badge_above=("gesundbrunnen",),
    label_offsets={
        # Bisher endete die S15 hier und das Tag brauchte die vollen 18 px
        # Luft. Jetzt faehrt sie durch, es gibt kein Tag mehr -- die
        # Beschriftung darf tiefer sitzen.
        "gesundbrunnen": (0.0, -12.0),
        # Hauptbahnhof braucht gar keine Anhebung mehr: ohne Tag sitzt der
        # Name auf derselben Grundlinie wie Friedrichstrasse und
        # Alexanderplatz an derselben Stadtbahn.
        "hauptbahnhof": (0.0, 0.0),
        # Blankenburg umgekehrt: die S26 endet dort nicht mehr, das Tag ist
        # weg, der Name steht wieder ohne Versatz.
        "blankenburg": REMOVE,
    },
    # Die Tabelle waechst mit jeder Stufe und rueckt weiter nach oben,
    # damit sie den Gartenfelder Ast der S6 nicht beruehrt -- um wie viel,
    # rechnet der Renderer aus (Hoehe None).
    legend_at=(-60.0, None),
)

ZIEL = FilePath("outputs/berlin_sbahn_2030plus.svg")


# Blendet die grauen Hilfstrassen mit ein -- die Trassenmitten, um die
# herum die Linien ihren Spurversatz bekommen.
TRASSEN = False


if __name__ == "__main__":
    write_map(
        NET,
        sys.argv[1] if len(sys.argv) > 1 else ZIEL,
        draw_corridors=TRASSEN,
    )
