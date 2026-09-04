"""
Legende: die Tabelle der Zuggruppen.

Eine eigene Schicht neben der Karte. Sie liest nur, was in den Linien selbst
steht (`TurnLine.groups`), und liefert fertige SVG-Elemente plus den Umriss,
den sie belegt -- der Renderer haengt beides an und laesst die Zeichenflaeche
entsprechend mitwachsen.

Aufbau der Tabelle wie auf dem Aushang:

    Linie | Zuggruppe | Laufweg | Zugstaerke

Die Zugstaerke steht als Viertelzuege in der letzten Spalte, gefuellt oder
nur umrandet. Fussnoten sammeln sich unter der Tabelle.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Sequence, Tuple

from .model import (
    Pt, StyleConfig, TrainGroup, TurnLine, badge_text, badge_width, text_width,
)

__all__ = ["legend_rows", "build_legend"]

Box = Tuple[float, float, float, float]


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def legend_rows(
    lines: Mapping[str, TurnLine],
) -> List[Tuple[List[str], Sequence[TrainGroup]]]:
    """Die Zeilenbloecke der Tabelle: (Linien-IDs, Zuggruppen).

    Sortiert wird nach der Liniennummer als Zeichenkette -- das ergibt genau
    die Reihenfolge des Aushangs: S1, S15, S2, S25, S3, S41, S42, ...

    Zwei aufeinanderfolgende Linien mit identischen Zuggruppen teilen sich
    einen Block; so stehen S41 und S42 mit ihrer gemeinsamen Zeile
    "Ringbahn in beide Richtungen" untereinander vor EINEM Eintrag.
    """
    ids = sorted((lid for lid, ln in lines.items() if ln.groups),
                 key=lambda lid: lid.lstrip("S"))
    bloecke: List[Tuple[List[str], Sequence[TrainGroup]]] = []
    for lid in ids:
        gruppen = tuple(lines[lid].groups)
        if bloecke and tuple(bloecke[-1][1]) == gruppen:
            bloecke[-1][0].append(lid)
        else:
            bloecke.append(([lid], gruppen))
    return bloecke


def build_legend(
    lines: Mapping[str, TurnLine],
    colors: Mapping[str, str],
    style: StyleConfig,
    x0: float,
    y0: float,
) -> Tuple[List[str], Box]:
    """Zeichnet die Tabelle mit der linken oberen Ecke bei (x0, y0)."""
    bloecke = legend_rows(lines)
    if not bloecke:
        return [], (x0, y0, x0, y0)

    # Schrift und Signets kommen aus derselben Quelle wie in der Karte --
    # gleiche Groesse wie die Stationsnamen, nur nicht fett, und die Signets
    # Zeichen fuer Zeichen dieselben wie an den Endpunkten.
    font = style.label_font
    zeile = style.legend_row_height
    pad = style.legend_padding

    # Spaltenbreiten: die beiden Textspalten so breit wie ihr laengster
    # Eintrag, damit nichts umbricht und nichts unnoetig leer bleibt.
    def breiteste(werte: Sequence[str], f: float) -> float:
        return max((text_width(w, f) for w in werte), default=0.0)

    badge_b = max(badge_width(lid, style)
                  for ids, _ in bloecke for lid in ids)
    spalte_linie = max(badge_b, text_width("Linie", font)) + 2 * pad
    spalte_gruppe = max(
        breiteste([g.kind for _, gs in bloecke for g in gs], font),
        text_width("Zuggruppe", font),
    ) + 2 * pad
    spalte_weg = max(
        breiteste([g.route for _, gs in bloecke for g in gs], font),
        text_width("Laufweg", font),
    ) + 2 * pad
    wagen_b = 4 * style.legend_car_width + 3 * style.legend_car_gap
    # Platz fuer das Fussnotenzeichen hinter der Zugstaerke, falls es eines
    # gibt -- sonst stiesse es an die Spaltenlinie.
    if any(g.note for _, gs in bloecke for g in gs):
        wagen_b += text_width(" *", font)
    spalte_staerke = max(wagen_b, text_width("Zugstärke", font)) + 2 * pad

    xs = [x0]
    for b in (spalte_linie, spalte_gruppe, spalte_weg, spalte_staerke):
        xs.append(xs[-1] + b)
    breite = xs[-1] - x0

    def block_masse(ids: List[str], gs: Sequence[TrainGroup]
                    ) -> Tuple[float, float, float]:
        """(Blockhoehe, Zeilenpitch im Block, Hoehe des Textsatzes).

        Der Abstand von der Trennlinie zur ersten und zur letzten Textzeile
        ist in JEDEM Block derselbe wie bei einer einzeiligen Linie -- die
        Zeile bringt oben und unten je `zeile / 2` um ihre Mitte mit. Nur der
        Abstand ZWISCHEN den Zeilen eines mehrzeiligen Blocks ist enger
        (`legend_row_height_multi`), damit die Tabelle kuerzer bleibt.
        """
        pitch = zeile if len(gs) <= 1 else style.legend_row_height_multi
        text_h = len(gs) * pitch
        # zeile + (n-1) * pitch: erste Textmitte bei zeile/2 unter der
        # oberen Trennlinie, letzte bei zeile/2 ueber der unteren.
        noetig = zeile + max(len(gs) - 1, 0) * pitch
        # Gestapelte Signets (S41/S42) brauchen weiterhin ihre volle Hoehe.
        return max(noetig, len(ids) * zeile), pitch, text_h

    fussnoten = [(g.note, g.note_cars)
                 for _, gs in bloecke for g in gs if g.note]
    hoehe = (
        zeile                                   # Kopfzeile
        + sum(block_masse(ids, gs)[0] for ids, gs in bloecke)
        + (len(fussnoten) * zeile if fussnoten else 0)
    )

    svg: List[str] = [f'<g font-size="{font:g}">']

    def text(tx: float, ty: float, s: str, *, bold: bool = False,
             anchor: str = "start", fill: str = "#111111",
             size: float = font, weight: str = "") -> None:
        fett = (f' font-weight="{weight}"' if weight
                else ' font-weight="bold"' if bold else "")
        svg.append(
            f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="{size:g}"{fett} '
            f'fill="{fill}" text-anchor="{anchor}">{_esc(s)}</text>'
        )

    def linie(y: float, von: float, bis: float, dick: float) -> None:
        svg.append(
            f'<line x1="{von:.1f}" y1="{y:.1f}" x2="{bis:.1f}" y2="{y:.1f}" '
            f'stroke="#111111" stroke-width="{dick:g}"/>'
        )

    # Kopfzeile
    grund = y0 + zeile * 0.72
    for i, titel in enumerate(("Linie", "Zuggruppe", "Laufweg", "Zugstärke")):
        text(xs[i] + pad, grund, titel, bold=True)
    y = y0 + zeile
    linie(y, x0, xs[-1], 0.8)

    for ids, gruppen in bloecke:
        # Faehrt eine Linie mit mehreren Zuggruppen, ruecken deren Zeilen
        # untereinander enger zusammen -- der Rand zur Trennlinie bleibt
        # aber genau der einer einzeiligen Linie.
        block_h, zeile_block, text_h = block_masse(ids, gruppen)

        # Liniensignets, mittig zum Block
        hoch = style.badge_height
        oben = y + (block_h - len(ids) * hoch - (len(ids) - 1) * 2.0) / 2
        for lid in ids:
            b = badge_width(lid, style)
            svg.append(
                f'<rect x="{xs[0] + pad:.1f}" y="{oben:.1f}" '
                f'width="{b:.1f}" height="{hoch:.1f}" '
                f'rx="{hoch / 2:.1f}" ry="{hoch / 2:.1f}" '
                f'fill="{colors.get(lid, "#888888")}"/>'
            )
            text(xs[0] + pad + b / 2,
                 oben + hoch / 2 + style.badge_font * 0.35,
                 badge_text(lid), anchor="middle", fill="white",
                 size=style.badge_font, weight="500")
            oben += hoch + 2.0

        # Text und Zugstaerke stehen mittig in ihrer Zeile -- fuellt der
        # Text nicht den ganzen Block (z.B. eine einzelne Zuggruppe neben
        # zwei uebereinander gestapelten Signets wie bei S41/S42), ruecken
        # sie zusaetzlich auf die Blockmitte.
        text_oben = y + (block_h - text_h) / 2
        for i, g in enumerate(gruppen):
            mitte = text_oben + i * zeile_block + zeile_block / 2
            gy = mitte + font * 0.35
            beschriftung = g.kind if len(gruppen) > 1 or len(ids) == 1 else g.kind
            text(xs[1] + pad, gy, beschriftung)
            text(xs[2] + pad, gy, g.route)
            # Zugstaerke: Viertelzuege als schraege Balken
            cx = xs[3] + pad
            cy = mitte - style.legend_car_height / 2
            for k in range(g.cars):
                voll = k < g.cars - g.hollow
                svg.append(_wagen(cx, cy, style, voll))
                cx += style.legend_car_width + style.legend_car_gap
            if g.note:
                text(cx + 2.0, gy, "*", size=font * 0.9)

        y += block_h
        linie(y, x0, xs[-1], 0.8)

    # Senkrechte Trennlinien zwischen den Spalten -- so duenn wie die
    # waagerechten. Sie reichen nur ueber die Tabelle selbst, nicht ueber die
    # Fussnoten darunter.
    for sx in xs[1:-1]:
        svg.append(
            f'<line x1="{sx:.1f}" y1="{y0:.1f}" x2="{sx:.1f}" y2="{y:.1f}" '
            f'stroke="#111111" stroke-width="0.8"/>'
        )

    for note, note_cars in fussnoten:
        gy = y + zeile * 0.72
        text(xs[1] + pad, gy, "* " + note, size=font * 0.9)
        cx = xs[3] + pad
        cy = y + (zeile - style.legend_car_height) / 2
        for _ in range(note_cars):
            svg.append(_wagen(cx, cy, style, True))
            cx += style.legend_car_width + style.legend_car_gap
        y += zeile

    svg.append("</g>")
    return svg, (x0, y0, x0 + breite, y0 + hoehe)


def _wagen(x: float, y: float, style: StyleConfig, voll: bool) -> str:
    """Ein Viertelzug: laenglicher Balken, nur vorn schraeg angeschnitten."""
    b, h = style.legend_car_width, style.legend_car_height
    # Versatz gleich der Hoehe: die Schnittkante steht damit unter 45 Grad.
    schraeg = h * 0.9
    punkte = [
        (x + schraeg, y), (x + b, y), (x + b, y + h), (x, y + h),
    ]
    d = " ".join(f"{px:.1f},{py:.1f}" for px, py in punkte)
    if voll:
        return f'<polygon points="{d}" fill="#111111"/>'
    return (f'<polygon points="{d}" fill="white" stroke="#111111" '
            f'stroke-width="1.0"/>')
