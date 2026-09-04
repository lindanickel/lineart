"""
netmap -- Engine fuer schematische Liniennetzplaene.

Die Engine kennt kein bestimmtes Net. Sie bekommt eine Definition, loest
daraus die Geometrie und zeichnet sie. Ein Net lebt in einem eigenen Modul
(siehe `netze/`), das ein `Net`-Objekt bereitstellt.

    from netmap import render_svg
    from berlin_2026 import NET

    open("map.svg", "w").write(render_svg(NET))

Die Phasen dahinter, jede fuer sich aufrufbar:

    2  build_network()      Linien parsen und ausrichten
    3  solve_measures()     Koordinaten und elastische Laengen loesen
    4  build_tracks()       Korridore bauen  (2-4 zusammen: solve_layout())
    4b build_line_layout()  Linien fuehren und buendeln
    5  build_track_svg()    zeichnen
"""

from __future__ import annotations

from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Dict, FrozenSet, Mapping, Optional, Sequence, Tuple, Union

from .model import (
    CFG, Config, Corridor, FixPath, FlexPath, GeometryError, LABEL_DIRECTION,
    LayoutResult, LineLayout, LinePath, NetworkConfig, Path, Pt, SolverConfig,
    Station, Step, StyleConfig, TrainGroup, Turn, TurnLine, _station_registry,
)
from .legend import build_legend, legend_rows
from .render import build_track_svg, print_layout_report
from .solve import (
    build_line_layout, build_network, build_tracks, parse_line, solve_layout,
    solve_measures,
)

__all__ = [
    "Net", "REMOVE", "render_svg", "solve_all", "write_map",
    "splice", "insert_after", "planned",
    "Station", "TurnLine", "Turn", "Path", "FlexPath", "FixPath", "Corridor",
    "Step", "LABEL_DIRECTION", "GeometryError", "station_registry",
    "Config", "NetworkConfig", "SolverConfig", "StyleConfig", "CFG",
    "build_network", "solve_measures", "build_tracks", "solve_layout",
    "build_line_layout", "build_track_svg", "print_layout_report",
    "LayoutResult", "LineLayout", "LinePath", "parse_line",
    "TrainGroup", "build_legend", "legend_rows",
]

station_registry = _station_registry


class _Remove:
    """Sentinel fuer `Net.derive`: nimmt einen geerbten Eintrag wieder heraus."""

    __slots__ = ()

    def __repr__(self) -> str:      # nur fuer Fehlermeldungen und Debugging
        return "REMOVE"


REMOVE = _Remove()


def _merge_map(alt: Mapping, neu: Optional[Mapping], feld: str) -> Mapping:
    """Elternwerte plus Aenderungen; REMOVE nimmt einen Eintrag heraus.

    Ein REMOVE auf einen Schluessel, den es gar nicht gibt, ist ein Fehler --
    sonst verschwindet ein Tippfehler unbemerkt und man sucht spaeter, warum
    das Entfernen nichts bewirkt hat.
    """
    if not neu:
        return alt
    out = dict(alt)
    for key, wert in neu.items():
        if isinstance(wert, _Remove):
            if key not in out:
                raise KeyError(
                    f"Net.derive({feld}=...): '{key}' laesst sich nicht "
                    "entfernen, das Elternnetz kennt ihn nicht"
                )
            del out[key]
        else:
            out[key] = wert
    return out


def _merge_set(alt: FrozenSet[str], neu, feld: str) -> FrozenSet[str]:
    """Wie `_merge_map`, aber fuer die Mengenfelder.

    Ein Iterable fuegt hinzu; eine Mapping-Form erlaubt zusaetzlich REMOVE.
    """
    if neu is None:
        return alt
    if isinstance(neu, _Mapping):
        out = set(alt)
        for key, wert in neu.items():
            if isinstance(wert, _Remove):
                if key not in out:
                    raise KeyError(
                        f"Net.derive({feld}=...): '{key}' laesst sich nicht "
                        "entfernen, das Elternnetz kennt ihn nicht"
                    )
                out.discard(key)
            else:
                out.add(key)
        return frozenset(out)
    return frozenset(alt) | frozenset(neu)


def insert_after(steps: Sequence[Any], anchor: Any, *neu: Any) -> list:
    """Fuegt `neu` direkt hinter der ersten Fundstelle von `anchor` ein."""
    out = list(steps)
    i = out.index(anchor)
    return out[: i + 1] + list(neu) + out[i + 1 :]


def splice(steps: Sequence[Any], alt: Sequence[Any], neu: Sequence[Any]) -> list:
    """Ersetzt die erste Fundstelle der Teilfolge `alt` durch `neu`.

    Turn und Path sind frozen dataclasses, lassen sich also direkt
    vergleichen -- damit kann eine Stelle mitten in einer Linie ueber ihre
    Modifier angesteuert werden, nicht nur ueber Stationsnamen.
    """
    out, alt = list(steps), list(alt)
    for i in range(len(out) - len(alt) + 1):
        if out[i : i + len(alt)] == alt:
            return out[:i] + list(neu) + out[i + len(alt):]
    raise ValueError(f"Teilfolge nicht gefunden: {alt}")


def planned(station: Station) -> Station:
    """Dieselbe Station als GEPLANT: Name in Klammern, blasser Punkt.

    Die Klammern stehen hier und nicht im Renderer, weil sie eine Frage der
    Beschriftung sind -- ein mehrzeiliges Label wird als Ganzes geklammert,
    nicht Zeile fuer Zeile.
    """
    return replace(station, label=f"({station.label})", planned=True)


def _station_map(neu) -> Optional[Mapping[str, Any]]:
    """Stationen duerfen als Liste kommen -- die ID steht ja in der Station."""
    if neu is None or isinstance(neu, _Mapping):
        return neu
    if isinstance(neu, _Iterable):
        return {s.id: s for s in neu}
    raise TypeError("Net.derive(stations=...): Mapping oder Liste von Station")


@dataclass(frozen=True)
class Net:
    """Eine vollstaendige Netzdefinition -- alles, was eine Karte ausmacht.

    stations, lines, colors  das Net selbst
    corridors                von Hand festgelegte Spurlagen
    label_override           Beschriftung an einer Station voll manuell
    label_offsets            Feinkorrektur in Pixeln, verschiebt Name UND Tag
    badge_offsets            Feinkorrektur, verschiebt nur das Linien-Tag
    badge_opposite_corner          Stationen, an denen das Tag auf die
                             gegenueberliegende Ecke der Beschriftung gehoert
    badge_order              Stationen -> Reihenfolge der Signete; ohne
                             Eintrag stehen sie alphabetisch
    crossing_over            Linie -> Linien, ueber denen sie liegt, wo sie
                             sich kreuzen. Setzt die automatische Wahl der
                             obenliegenden Linie ausser Kraft; gefragt wird
                             nach Familien (S8 meint auch die S85)
    badge_above              Stationen, an denen die Signetreihe UEBER dem
                             Namen steht statt darunter -- fuer Bahnhoefe,
                             an denen so viele Linien enden, dass die Reihe
                             breiter wird als der Name
    legend_at                linke obere Ecke der Zuggruppen-Tabelle, in
                             Kartenkoordinaten (Pixel). None = keine Legende.
                             Die Hoehe darf selbst None sein -- (-60.0, None)
                             --, dann sucht der Renderer sie: die Tabelle
                             haengt so tief, dass unter ihr genau so viel
                             Luft bleibt wie der Rahmen ringsum laesst
                             (`style.margin`). Weil sie meist ueber den
                             Nordrand der Karte hinausragt, ist das
                             zugleich die kuerzeste Karte.
    """

    stations: Mapping[str, Station]
    lines: Mapping[str, TurnLine]
    colors: Mapping[str, str]
    corridors: Mapping[str, Corridor] = field(default_factory=dict)
    label_override: Mapping[str, Tuple[float, float, str]] = field(default_factory=dict)
    label_offsets: Mapping[str, Pt] = field(default_factory=dict)
    badge_offsets: Mapping[str, Pt] = field(default_factory=dict)
    badge_opposite_corner: FrozenSet[str] = frozenset()
    badge_above: FrozenSet[str] = frozenset()
    badge_order: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    crossing_over: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    legend_at: Optional[Tuple[float, Optional[float]]] = None

    def __post_init__(self) -> None:
        fehlend = sorted(set(self.lines) - set(self.colors))
        if fehlend:
            raise ValueError("Linien ohne Farbe: " + ", ".join(fehlend))

    @property
    def families(self) -> Dict[str, str]:
        """Linien-ID -> Familie, nur fuer Linien mit eigener Familie."""
        return {lid: ln.family for lid, ln in self.lines.items() if ln.family}

    # ---- Bausteine fuer ein abgeleitetes Netz -------------------------
    # Alle vier lesen aus DIESEM Netz und liefern eine geaenderte Kopie --
    # gedacht fuer die naechste Ausbaustufe, die "dasselbe wie vorher, nur
    # ..." ausdruecken will.

    def line(self, line_id: str, **changes) -> TurnLine:
        """Dieselbe Linie wie hier, mit geaenderten Feldern."""
        return replace(self.lines[line_id], **changes)

    def station(self, station_id: str, **changes) -> Station:
        """Dieselbe Station wie hier, mit geaenderter Darstellung.

        Der Name bleibt; gedacht fuer `label`, `label_pos` oder `kind`, wenn
        eine uebernommene Station im neuen Netz anders dasteht.
        """
        return replace(self.stations[station_id], **changes)

    def extend_line(
        self, line_id: str, *, front: Sequence = (), back: Sequence = (),
    ) -> TurnLine:
        """Dieselbe Linie, vorn und/oder hinten verlaengert."""
        alt = self.lines[line_id]
        return replace(alt, steps=[*front, *alt.steps, *back])

    def splice_line(self, line_id: str, alt: Sequence, neu: Sequence, **changes) -> TurnLine:
        """Dieselbe Linie, mit der Teilfolge `alt` durch `neu` ersetzt."""
        line = self.lines[line_id]
        return replace(line, steps=splice(line.steps, alt, neu), **changes)

    def derive(
        self,
        *,
        stations: Union[Mapping[str, Any], Sequence[Station], None] = None,
        lines: Optional[Mapping[str, Any]] = None,
        colors: Optional[Mapping[str, Any]] = None,
        corridors: Optional[Mapping[str, Any]] = None,
        label_override: Optional[Mapping[str, Any]] = None,
        label_offsets: Optional[Mapping[str, Any]] = None,
        badge_offsets: Optional[Mapping[str, Any]] = None,
        badge_opposite_corner=None,
        badge_above=None,
        badge_order: Optional[Mapping[str, Any]] = None,
        crossing_over: Optional[Mapping[str, Any]] = None,
        groups: Optional[Mapping[str, Sequence[TrainGroup]]] = None,
        legend_at=None,
    ) -> "Net":
        """Ein Netz, das auf diesem aufbaut -- ein spaeterer Ausbaustand.

        Der Sinn ist, dass ein abgeleitetes Netz nur den UNTERSCHIED
        beschreibt. Alles, was hier nicht genannt wird, kommt unveraendert
        aus dem Elternnetz -- auch Felder, die es zum Zeitpunkt der Ableitung
        noch gar nicht gab. Genau das ist der Unterschied zum Zusammenbauen
        von Hand: dort faellt ein vergessenes Feld still auf seinen Default
        zurueck, statt geerbt zu werden.

        Jedes Abbildungsfeld nimmt dieselben drei Faelle entgegen:

            "S6": S6            neu -- die ID gibt es im Elternnetz nicht
            "S15": geaendert    ersetzt den geerbten Eintrag
            "S26": REMOVE       nimmt den geerbten Eintrag heraus

        `stations` darf statt einer Abbildung auch eine Liste von `Station`
        sein; die ID steht ja in der Station. `badge_opposite_corner` ist eine
        Menge: ein Iterable fuegt hinzu, die Abbildungsform erlaubt REMOVE.
        `legend_at` uebernimmt ohne Angabe die Ecke des Elternnetzes, REMOVE
        schaltet die Legende ab.

        `groups` ist der eine Sonderfall: es setzt die Zuggruppen ALLER
        Linien neu, weil die Legendentabelle zum Ausbaustand gehoert und
        nicht zur geerbten Linie. Wer dort fehlt, taucht in der Tabelle nicht
        auf. Ohne Angabe bleiben die geerbten Zuggruppen stehen.

        Ableitungen lassen sich ketten: Stufe 3 leitet von Stufe 2 ab, nicht
        vom Bestand.
        """
        neue_linien = _merge_map(self.lines, lines, "lines")
        if groups is not None:
            neue_linien = {
                lid: replace(line, groups=groups.get(lid, ()))
                for lid, line in neue_linien.items()
            }
        return replace(
            self,
            stations=_merge_map(self.stations, _station_map(stations), "stations"),
            lines=neue_linien,
            colors=_merge_map(self.colors, colors, "colors"),
            corridors=_merge_map(self.corridors, corridors, "corridors"),
            label_override=_merge_map(
                self.label_override, label_override, "label_override"
            ),
            label_offsets=_merge_map(self.label_offsets, label_offsets, "label_offsets"),
            badge_offsets=_merge_map(self.badge_offsets, badge_offsets, "badge_offsets"),
            badge_opposite_corner=_merge_set(
                self.badge_opposite_corner, badge_opposite_corner,
                "badge_opposite_corner",
            ),
            badge_above=_merge_set(self.badge_above, badge_above, "badge_above"),
            badge_order=_merge_map(self.badge_order, badge_order, "badge_order"),
            crossing_over=_merge_map(
                self.crossing_over, crossing_over, "crossing_over"
            ),
            legend_at=(
                self.legend_at if legend_at is None
                else None if isinstance(legend_at, _Remove)
                else legend_at
            ),
        )


def solve_all(net: Net, cfg: Config = CFG) -> Tuple[LayoutResult, LineLayout]:
    """Phasen 2 bis 4b: aus der Definition wird fertige Geometrie."""
    layout = solve_layout(net.lines, stations=net.stations, cfg=cfg)
    line_layout = build_line_layout(
        layout,
        net.lines,
        bundle_spacing=cfg.style.bundle_spacing,
        families=net.families,
        corridors=net.corridors,
        bezier_shift=cfg.netz.shift_bezier,
        bezier_span=cfg.netz.shift_bezier_span,
    )
    return layout, line_layout


def render_svg(
    net: Net,
    cfg: Config = CFG,
    *,
    draw_corridors: bool = False,
    layout: Optional[LayoutResult] = None,
    line_layout: Optional[LineLayout] = None,
) -> str:
    """Alle Phasen am Stueck: aus einer Netzdefinition wird ein SVG.

    `draw_corridors` blendet die grauen Hilfstrassen ein -- nuetzlich, um den
    Spurversatz gegen die Trassenmitte zu pruefen.
    """
    if layout is None or line_layout is None:
        layout, line_layout = solve_all(net, cfg)
    return build_track_svg(
        layout,
        stations=net.stations,
        highlight_lines={lid: net.colors[lid] for lid in net.lines},
        line_layout=line_layout,
        draw_corridors=draw_corridors,
        style=cfg.style,
        label_override=net.label_override,
        badge_opposite_corner=net.badge_opposite_corner,
        badge_above=net.badge_above,
        badge_order=net.badge_order,
        crossing_over=net.crossing_over,
        label_offsets=net.label_offsets,
        badge_offsets=net.badge_offsets,
        legend_at=net.legend_at,
        legend_lines=net.lines,
    )


def write_map(
    net: Net,
    target,
    cfg: Config = CFG,
    *,
    draw_corridors: bool = False,
    report: bool = True,
) -> None:
    """Karte erzeugen und schreiben -- der uebliche Weg fuer eine Netzdatei.

    Liegt hier und nicht in den Netzdateien, damit jede Karte denselben Weg
    durch die Engine nimmt: eine Aenderung am Renderer wirkt damit auf alle.
    """
    from pathlib import Path as _FilePath

    ziel = _FilePath(target)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    layout, line_layout = solve_all(net, cfg)
    ziel.write_text(
        render_svg(
            net, cfg,
            draw_corridors=draw_corridors,
            layout=layout,
            line_layout=line_layout,
        ),
        encoding="utf-8",
    )
    if report:
        print_layout_report(net.lines, layout, net.stations)
    print(f"Geschrieben: {ziel}")
