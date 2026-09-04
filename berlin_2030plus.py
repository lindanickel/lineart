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
  * Damit gibt die S85 die Nordbahn ab und endet in Pankow -- die S15
    uebernimmt sie und faehrt von Frohnau durch.
  * S25 nach Norden bis Velten und nach Sueden bis Stahnsdorf, dazu
    Borsigwalde auf der zweigleisig ausgebauten Kremmener Bahn. Sie faehrt
    dadurch im 10-Minuten-Takt, die S26 entfaellt.
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
    REMOVE, Corridor, FixPath, FlexPath, Station, Step, TrainGroup, Turn,
    TurnLine, insert_after, planned, splice, write_map,
)

import berlin_2026 as bestand
import berlin_2030 as vorstufe

# Das Netz, auf dem dieses hier aufbaut.
VORGAENGER = vorstufe.NET


# ============================================================================
# CITY-S-BAHN BA2 -- Hauptbahnhof bis Potsdamer Platz
# ============================================================================

# Im Bestand endet der Tunnelast am Hauptbahnhof. Jetzt geht es weiter zum
# Potsdamer Platz, wo er auf den bestehenden Nord-Sued-Tunnel trifft.
_HBF_POTSDAMER: List[Step] = [
    FlexPath(), Turn(-45), FlexPath(), Turn(45, radius=1.2), FlexPath(preferred=1.0),
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
        FixPath(1.2),
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

# Wilhelmsruh bis Hohen Neuendorf lag auf starren Standardabstaenden. Hier
# elastisch und ueber `group` gekoppelt: der Loeser waehlt die Laenge, alle
# Abschnitte bleiben dabei gleich. S1 und S15 muessen dieselbe Fassung
# benutzen, sonst haetten sie auf ihren gemeinsamen Kanten unterschiedliche
# Geometrie.
_NORDBAHN_ALT: List[Step] = [
    "wilhelmsruh", "wittenau", "waidmannslust", "hermsdorf", "frohnau",
    "hohen_neuendorf",
]
_NORDBAHN_NEU: List[Step] = [
    "wilhelmsruh", FlexPath(group="nordbahn"),
    "wittenau", FlexPath(group="nordbahn"),
    "waidmannslust", FlexPath(group="nordbahn"),
    "hermsdorf", FlexPath(group="nordbahn"),
    "frohnau", FlexPath(group="nordbahn"),
    "hohen_neuendorf",
]
# Die fuenf gekoppelten Abschnitte teilen sich, was zwischen Wilhelmsruh und
# Hohen Neuendorf uebrig bleibt -- direkt kuerzer stellen laesst sich das
# nicht, weil beide Enden in Schleifen haengen. Der Hebel ist die Strecke
# SUEDLICH davon: je weiter Bornholmer, Wollankstrasse und Schoenholz
# auseinanderruecken, desto weiter wandert Wilhelmsruh nach Norden und desto
# enger wird der Rest. Diese Werte sind die Stellschrauben.
_GESUNDBRUNNEN_BORNHOLMER = FixPath(2.6)    # Bestand 2.6, S1/S15/S2/S25
_BORNHOLMER_WOLLANK = FixPath(2.8)          # Bestand 2.4
_WOLLANK_SCHOENHOLZ = FixPath(1.2)          # Bestand 1.0
_SCHOENHOLZ_WILHELMSRUH = FixPath(2.2)      # Bestand 2.0, S1 und S15
_SCHOENHOLZ_KREMMENER = (FixPath(1.0), FixPath(1.6))   # Bestand 0.8 / 1.6, S25

_NORD_BAHN_NEU: List[Step] = splice(
    splice(bestand._NORD_BAHN, _NORDBAHN_ALT, _NORDBAHN_NEU),
    ["wollankstrasse", FixPath(1.0), "schoenholz", FixPath(2.0)],
    ["wollankstrasse", _WOLLANK_SCHOENHOLZ, "schoenholz", _SCHOENHOLZ_WILHELMSRUH],
)

# Vorderes Stueck der S15: Frohnau -> ... -> Bornholmer -> Gesundbrunnen.
# Dahinter geht es mit dem bisherigen Anfang der Linie weiter.
_S15_NORDBAHN: List[Step] = [
    *bestand._reversed(bestand._slice(_NORD_BAHN_NEU, "wollankstrasse", "frohnau")),
    _BORNHOLMER_WOLLANK, "bornholmer_strasse",
    _GESUNDBRUNNEN_BORNHOLMER,
]

# Die S85 gibt auch den Hauptbahnhof-Ast wieder ab -- den befaehrt jetzt die
# durchgebundene S15 -- und faengt stattdessen in Pankow an, mit demselben
# Bogen zur Bornholmer Strasse und weiter auf den Ring, den auch die S8
# faehrt. Ab Schoenhauser Allee bleibt ihr Laufweg, wie er war.
_S85_AB_SCHOENHAUSER = list(VORGAENGER.lines["S85"].steps)
_S85_AB_SCHOENHAUSER = _S85_AB_SCHOENHAUSER[
    _S85_AB_SCHOENHAUSER.index("schoenhauser_allee"):
]

_S85 = replace(
    VORGAENGER.lines["S85"],
    start=225,                       # Pankow -> Bornholmer, erst nach Suedwesten
    steps=[
        "pankow",
        *bestand._reversed(bestand._KURVE_BORNHOLMER_PANKOW),
        "bornholmer_strasse",
        *bestand._KURVE_BORNHOLMER_SCHOENHAUSER,
        *_S85_AB_SCHOENHAUSER,
    ],
    direction="Pankow -> Flughafen BER",
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

# Die S25 faehrt jetzt im 10-Minuten-Takt, die S26 entfaellt dadurch. Ihr
# Nordast Pankow -- Blankenburg bleibt von der S2 bedient.
#
DROPPED_LINES = ("S26",)

CHANGED_LINES: Dict[str, TurnLine] = {
    "S6": S6,
    # Der Nordast der S15 wird mit dem der S1 elastisch: dieselbe Fassung wie
    # dort, sonst haetten die beiden auf ihren gemeinsamen Kanten
    # unterschiedliche Geometrie.
    "S15": replace(
        _S15_DURCHGEBUNDEN,
        steps=splice(
            _S15_DURCHGEBUNDEN.steps, vorstufe._S15_NORDBAHN, _S15_NORDBAHN,
        ),
        direction="Frohnau -> Zehlendorf",
    ),
    "S85": _S85,
    "S25": replace(
        _s25,
        steps=splice(
            insert_after(_s25.steps, "eichborndamm", "borsigwalde"),
            # Bis Schoenholz dieselben Abstaende wie S1 und S15, danach
            # zweigt die S25 auf die Kremmener Bahn ab -- auch dieses Stueck
            # etwas laenger.
            [FixPath(2.6), "bornholmer_strasse",
             FixPath(2.4), "wollankstrasse", FixPath(1.0), "schoenholz",
             FixPath(0.8), Turn(-45), FixPath(1.6)],
            [_GESUNDBRUNNEN_BORNHOLMER, "bornholmer_strasse",
             _BORNHOLMER_WOLLANK, "wollankstrasse", _WOLLANK_SCHOENHOLZ,
             "schoenholz", _SCHOENHOLZ_KREMMENER[0], Turn(-45),
             _SCHOENHOLZ_KREMMENER[1]],
        ),
    ),
    "S2": replace(
        VORGAENGER.lines["S2"],
        steps=splice(
            insert_after(
                VORGAENGER.lines["S2"].steps, "marienfelde", "kamenzer_damm"
            ),
            # Dieselbe Kante Gesundbrunnen -- Bornholmer wie bei S1/S15/S25.
            [FixPath(2.6), "bornholmer_strasse", *bestand._KURVE_BORNHOLMER_PANKOW],
            [_GESUNDBRUNNEN_BORNHOLMER, "bornholmer_strasse",
             *bestand._KURVE_BORNHOLMER_PANKOW],
        ),
    ),
    "S1": replace(
        VORGAENGER.lines["S1"],
        steps=splice(
            VORGAENGER.lines["S1"].steps,
            [FixPath(2.6), "bornholmer_strasse", FixPath(2.4),
             *bestand._NORD_BAHN],
            [_GESUNDBRUNNEN_BORNHOLMER, "bornholmer_strasse",
             _BORNHOLMER_WOLLANK, *_NORD_BAHN_NEU],
        ),
    ),
}


# ============================================================================
# UEBERNOMMENE STATIONEN, NEU BESCHRIFTET
# ============================================================================

RESTYLED_STATIONS: List[Station] = [
    # Hier halten jetzt S1/S2/S25 und zusaetzlich S6 und S15.
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


# ============================================================================
# ZUGGRUPPEN
#
# Die Legendentabelle dieser Stufe: die geerbte, mit neuen Laufwegen fuer
# die Linien, die hier anders fahren.
# ============================================================================

GROUPS: Dict[str, List[TrainGroup]] = {
    lid: list(line.groups) for lid, line in VORGAENGER.lines.items() if line.groups
}
del GROUPS["S26"]
GROUPS["S6"] = [
    TrainGroup("Stammzuggruppe", "Gartenfeld <> Potsdamer Platz", 3),
    TrainGroup("Tageszuggruppe", "Gartenfeld <> Potsdamer Platz", 3),
]
# Keine Stichfahrt mehr zum Gesundbrunnen, sondern durchgebunden von der
# Nordbahn ueber den neuen Tunnel bis Zehlendorf.
GROUPS["S15"] = [
    TrainGroup("Stammzuggruppe", "Zehlendorf <> Frohnau", 3),
    TrainGroup("Tageszuggruppe", "Zehlendorf <> Gesundbrunnen", 3),
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
# Im 10-Minuten-Takt und ueber beide Enden hinaus verlaengert -- die S26
# entfaellt dafuer.
GROUPS["S25"] = [
    TrainGroup("Stammzuggruppe", "Stahnsdorf <> Velten", 3),
    TrainGroup("Tageszuggruppe", "Stahnsdorf <> Hennigsdorf", 3),
]
# Der Nordast ging an die S15, die S85 endet in Pankow.
GROUPS["S85"] = [TrainGroup("Stammzuggruppe", "Flughafen BER <> Pankow", 3)]


NET = VORGAENGER.derive(
    stations=[*RESTYLED_STATIONS, *NEW_STATIONS],
    lines={**CHANGED_LINES, **{lid: REMOVE for lid in DROPPED_LINES}},
    groups=GROUPS,
    corridors={
        **S25_SUED_CORRIDORS,
        "hbf_potsdamer_platz": Corridor(
            # Einschwenken zum Potsdamer Platz: S15 auf dieselbe Spur wie die
            # S1 (-0.5), S6 daneben auf -1.5.
            steps=["hauptbahnhof", "potsdamer_platz"],
            offsets={"S15": 0.5, "S6": 1.5},
        ),
        # Die S85 verlaesst den Hauptbahnhof-Ast wieder -- ohne sie ist die
        # aeussere Gastspur auf dem Ring frei, und die S15 kann zurueck auf
        # ihre alte Lage. Damit fallen auch die Dreier-Spurlagen auf dem Ast
        # weg, S6 und S15 liegen wieder wie in der Stufe davor.
        "ring_gesundbrunnen_wedding": REMOVE,
        "hbf_zulauf_wedding": Corridor(
            steps=["wedding", "perlegerberger_bruecke"],
            offsets={"S1": -0.5},
        ),
        "hbf_zulauf_westhafen": Corridor(
            steps=["westhafen", "perlegerberger_bruecke", "hauptbahnhof"],
            offsets={"S6": 0.5, "S1": -0.5},
        ),
        "s8_pankow_bornholmer": replace(
            VORGAENGER.corridors["s8_pankow_bornholmer"],
            # Die S85 faengt jetzt in Pankow an, also mitten auf dieser Kante.
            # Ohne Vorgabe laege sie sofort auf 1.5 -- die S8 uebernimmt
            # diesen Versatz aber erst in der Kurve und kommt bis dahin auf
            # 0.5 aus Blankenburg. Damit beide von Pankow bis zur Kurve
            # nebeneinander liegen, startet die S85 ebenfalls auf 0.5.
            start_offsets={"S85": -0.5},
        ),
    },
    label_offsets={
        # Bisher endete die S15 hier und das Tag brauchte die vollen 18 px
        # Luft. Jetzt faehrt sie durch, es gibt kein Tag mehr -- die
        # Beschriftung darf tiefer sitzen.
        "gesundbrunnen": (0.0, -12.0),
        # Hauptbahnhof braucht gar keine Anhebung mehr: ohne Tag sitzt der
        # Name auf derselben Grundlinie wie Friedrichstrasse und
        # Alexanderplatz an derselben Stadtbahn.
        "hauptbahnhof": (0.0, 0.0),
    },
    # Die Tabelle waechst mit jeder Stufe und rueckt weiter nach oben,
    # damit sie den Gartenfelder Ast der S6 nicht beruehrt -- um wie viel,
    # rechnet der Renderer aus (Hoehe None).
    legend_at=(-60.0, None),
)

ZIEL = FilePath("outputs/berlin_sbahn_2030plus.svg")


if __name__ == "__main__":
    write_map(NET, sys.argv[1] if len(sys.argv) > 1 else ZIEL)
