"""
Die Grundkarte zeichnen: Ebene fuer Ebene holen, projizieren, vereinfachen
und als SVG schreiben.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List

import numpy as np

from .geometry import bbox_of, overlaps, path_d, ring_area, simplify
from .model import Basemap, Layer
from .osm import build_query, element_areas, element_lines, overpass


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _project_all(bm: Basemap, zuege) -> List[np.ndarray]:
    """Alle Linienzuege auf einmal projizieren -- einzeln kostet der
    numpy-Aufruf pro Weg mehr als die Rechnung selbst."""
    if not zuege:
        return []
    a = np.asarray([p for z in zuege for p in z], dtype=float)
    x, y = bm.view.project(a[:, 0], a[:, 1])
    grenzen = np.cumsum([len(z) for z in zuege])[:-1]
    return np.split(np.column_stack([x, y]), grenzen)


def layer_path(bm: Basemap, layer: Layer) -> str:
    """Eine Ebene als SVG-Pfaddaten -- geladen, projiziert, vereinfacht.

    Was ganz ausserhalb des Ausschnitts liegt, faellt schon hier weg; den
    Rest schneidet der clipPath. Eine Flaeche wird nur als Ganzes verworfen:
    fehlte ihr ein Innenring, waere aus dem See eine Insel geworden.
    """
    w, h = bm.view.width_px, bm.view.height_px
    pad = 4 * layer.width + 2
    query = build_query(
        layer.selectors, bm.data_bbox or bm.view.bbox(), tags=layer.tags
    )
    elements = overpass(query, bm.cache_dir, layer.name)["elements"]

    if layer.kind == "line":
        zuege = [
            simplify(p, layer.simplify)
            for p in _project_all(bm, element_lines(elements))
            if overlaps(bbox_of(p), w, h, pad)
        ]
        return path_d(zuege, closed=False)

    ringe: List[np.ndarray] = []
    flaechen = element_areas(elements)
    alle = iter(_project_all(bm, [r for f in flaechen for r in f]))
    for flaeche in flaechen:
        proj = [next(alle) for _ in flaeche]
        box = bbox_of(np.vstack(proj))
        if not overlaps(box, w, h, pad):
            continue
        if layer.min_area and ring_area(proj[0]) < layer.min_area and len(proj) == 1:
            continue
        ringe.extend(simplify(r, layer.simplify) for r in proj)
    return path_d(ringe, closed=True)


def _layer_svg(layer: Layer, d: str) -> str:
    attrs = [f'id="{layer.name}"']
    if layer.kind == "area":
        attrs.append(f'fill="{layer.fill or "none"}" fill-rule="evenodd"')
    else:
        attrs.append('fill="none"')
    if layer.stroke:
        attrs.append(
            f'stroke="{layer.stroke}" stroke-width="{layer.width:g}"'
            ' stroke-linecap="round" stroke-linejoin="round"'
        )
        if layer.dash:
            attrs.append(f'stroke-dasharray="{layer.dash}"')
    if layer.opacity < 1:
        attrs.append(f'opacity="{layer.opacity:g}"')
    return f'<path {" ".join(attrs)} d="{d}"/>'


def render_svg(bm: Basemap, *, overlay: str = "", verbose: bool = True) -> str:
    """Die Grundkarte als SVG; `overlay` kommt darueber, unter den
    OSM-Hinweis -- etwa die Linien aus `lines_svg`."""
    w, h = bm.view.width_px, bm.view.height_px
    teile = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}"'
        f' viewBox="0 0 {w:.1f} {h:.1f}">',
        '<defs><clipPath id="ausschnitt">'
        f'<rect width="{w:.1f}" height="{h:.1f}"/></clipPath></defs>',
        f'<rect width="{w:.1f}" height="{h:.1f}" fill="{bm.background}"/>',
        '<g id="grundkarte" clip-path="url(#ausschnitt)">',
    ]
    for layer in bm.layers:
        t0 = time.time()
        d = layer_path(bm, layer)
        teile.append(_layer_svg(layer, d))
        if verbose:
            print(
                f"  {layer.name:<18} {len(d) / 1e6:6.2f} MB Pfad,"
                f" {time.time() - t0:5.1f} s",
                flush=True,
            )
    teile.append("</g>")
    if overlay:
        teile.append(f'<g clip-path="url(#ausschnitt)">\n{overlay}\n</g>')
    if bm.attribution:
        teile.append(
            f'<text x="{w - 8:.1f}" y="{h - 8:.1f}" text-anchor="end"'
            f' font-family="{bm.font_family}" font-size="11" fill="#777">'
            f"{_esc(bm.attribution)}</text>"
        )
    teile.append("</svg>")
    return "\n".join(teile)


def write_basemap(
    bm: Basemap, target, *, overlay: str = "", png: bool = True, png_scale: float = 1.0,
) -> Path:
    """SVG schreiben, auf Wunsch daneben ein PNG -- in der Groesse der Karte
    oder um `png_scale` vergroessert."""
    ziel = Path(target)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    svg = render_svg(bm, overlay=overlay)
    ziel.write_text(svg, encoding="utf-8")
    print(f"Geschrieben: {ziel} ({len(svg) / 1e6:.1f} MB)")
    if png:
        import cairosvg

        png_ziel = ziel.with_suffix(".png")
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(png_ziel), scale=png_scale)
        print(f"Geschrieben: {png_ziel}")
    return ziel
