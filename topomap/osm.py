"""
OpenStreetMap-Daten ueber die Overpass-API holen und zwischenspeichern.

Jede Abfrage landet gzip-komprimiert im Cache, benannt nach einem Hash ihres
Textes. Aendert sich die Abfrage (anderer Ausschnitt, anderer Filter), ist
das eine neue Datei; bleibt sie gleich, geht kein Byte ueber das Netz. Wer
frische Daten will, loescht den Cache.

Die Daten stehen unter der ODbL: (c) OpenStreetMap-Mitwirkende.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
USER_AGENT = "lineart-topomap/0.1 (Grundkarte fuer einen S-Bahn-Plan)"

LonLat = List[Tuple[float, float]]


def build_query(
    selectors: Sequence[str],
    bbox: Tuple[float, float, float, float],
    *,
    tags: bool,
    timeout: int = 300,
    maxsize: int = 0,
) -> str:
    """Eine Overpass-Abfrage aus Selektoren wie 'way["highway"="primary"]'.

    Der Ausschnitt haengt an jedem Selektor und nicht global: ein globales
    [bbox] schneidet bei `out geom` auch die Geometrie ab, und eine
    Landesgrenze soll ganz ankommen, nicht nur ihr Stueck im Ausschnitt.
    `tags=False` laesst die Attribute weg (`out skel`) -- bei den Strassen
    spart das gut die Haelfte, und der Stil haengt ohnehin an der Ebene.
    """
    box = ",".join(f"{v:.5f}" for v in bbox)
    body = "".join(f"  {sel}({box});\n" for sel in selectors)
    out = "out geom qt;" if tags else "out skel geom qt;"
    # Ein grosses maxsize reserviert den Speicher vorab; Overpass stellt
    # solche Abfragen hintan, bis ein Slot frei ist -- mitunter ewig. Ohne
    # Angabe gilt der Standard von 512 MB, der fuer eine Stadt reicht.
    groesse = f"[maxsize:{maxsize}]" if maxsize else ""
    return (
        f"[out:json][timeout:{timeout}]{groesse};\n"
        f"(\n{body});\n{out}"
    )


def _download(query: str, retries: int = 3) -> bytes:
    data = urllib.parse.urlencode({"data": query}).encode()
    fehler = None
    for versuch in range(retries):
        for url in ENDPOINTS:
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": USER_AGENT, "Accept": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=420) as resp:
                    return resp.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                # 429/504 heisst bei Overpass: Server voll, spaeter nochmal
                fehler = exc
                print(f"  Overpass {url}: {exc}")
        time.sleep(15 * (versuch + 1))
    raise RuntimeError(f"Overpass nicht erreichbar: {fehler}")


def overpass(query: str, cache_dir: Path, label: str = "") -> Dict:
    """Abfrage ausfuehren oder aus dem Cache lesen."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(query.encode()).hexdigest()[:12]
    # Der Name vorn ist nur zum Wiederfinden; gesucht wird ueber den Hash --
    # zwei Ebenen mit derselben Abfrage teilen sich so eine Datei
    for datei in cache_dir.glob(f"*-{key}.json.gz"):
        with gzip.open(datei, "rb") as fh:
            return json.load(fh)
    datei = cache_dir / f"{label or 'query'}-{key}.json.gz"
    print(f"  lade {label or 'Abfrage'} von Overpass ...", flush=True)
    t0 = time.time()
    roh = _download(query)
    antwort = json.loads(roh)
    if "remark" in antwort and not antwort.get("elements"):
        # Overpass meldet Timeouts und Speicherlimits im Text, nicht im Status
        raise RuntimeError(f"Overpass: {antwort['remark']}")
    with gzip.open(datei, "wb") as fh:
        fh.write(roh)
    print(
        f"  {len(antwort['elements'])} Elemente, {len(roh) / 1e6:.1f} MB,"
        f" {time.time() - t0:.0f} s",
        flush=True,
    )
    return antwort


# ==========================================================================
# ELEMENTE -> GEOMETRIE
# ==========================================================================


def _coords(geometry: Iterable) -> LonLat:
    # Luecken (null) kommen bei Relationen vor, deren Mitglieder teils
    # ausserhalb der Datenbank-Extrakte liegen -- einfach auslassen
    return [(p["lon"], p["lat"]) for p in geometry if p]


def element_lines(elements: Iterable[Dict]) -> List[LonLat]:
    """Alle Wege als Linienzuege; Relationen tragen ihre Mitgliedswege bei.

    Ein Weg, der zu mehreren Relationen gehoert (eine Gemeindegrenze, die
    zugleich Landesgrenze ist), kommt nur einmal heraus -- doppelt
    gezeichnet wuerde er an den Kanten dunkler.
    """
    gesehen = set()
    out: List[LonLat] = []
    for el in elements:
        if el["type"] == "way":
            teile = [(el["id"], el.get("geometry", ()))]
        elif el["type"] == "relation":
            teile = [
                (m["ref"], m.get("geometry", ()))
                for m in el.get("members", ())
                if m["type"] == "way"
            ]
        else:
            continue
        for wid, geom in teile:
            if wid in gesehen:
                continue
            gesehen.add(wid)
            pts = _coords(geom)
            if len(pts) >= 2:
                out.append(pts)
    return out


def element_areas(elements: Iterable[Dict]) -> List[List[LonLat]]:
    """Flaechen als Listen von Ringen.

    Ein geschlossener Weg ist ein Ring. Eine Multipolygon-Relation wird aus
    ihren Wegen zu Ringen verkettet, getrennt nach Rolle, damit ein Aussen-
    nie mit einem Innenring verschmilzt. Welcher Ring Loch ist, muss danach
    niemand mehr wissen: gezeichnet wird mit fill-rule="evenodd".
    """
    out: List[List[LonLat]] = []
    for el in elements:
        if el["type"] == "way":
            pts = _coords(el.get("geometry", ()))
            if len(pts) >= 4 and pts[0] == pts[-1]:
                out.append([pts])
        elif el["type"] == "relation":
            ringe: List[LonLat] = []
            for rolle in ("outer", "inner"):
                wege = [
                    _coords(m.get("geometry", ()))
                    for m in el.get("members", ())
                    if m["type"] == "way" and (m.get("role") or "outer") == rolle
                ]
                ringe.extend(stitch_rings([w for w in wege if len(w) >= 2]))
            if ringe:
                out.append(ringe)
    return out


def stitch_rings(ways: List[LonLat]) -> List[LonLat]:
    """Wegstuecke an gemeinsamen Endpunkten zu Ringen verketten.

    OSM legt einen Multipolygon-Ring oft aus vielen Wegen zusammen, in
    beliebiger Reihenfolge und Richtung. Verkettet wird gierig: an das
    offene Ende passt das naechste Stueck, das dort beginnt oder endet.
    Bleibt ein Ring offen (unvollstaendige Daten), wird er trotzdem
    geschlossen -- eine gerade Kante ist besser als eine fehlende Flaeche.
    """
    offen = [list(w) for w in ways]
    by_end: Dict[Tuple[float, float], List[int]] = {}
    for i, w in enumerate(offen):
        by_end.setdefault(w[0], []).append(i)
        by_end.setdefault(w[-1], []).append(i)
    benutzt = [False] * len(offen)
    ringe: List[LonLat] = []
    for start in range(len(offen)):
        if benutzt[start]:
            continue
        benutzt[start] = True
        ring = list(offen[start])
        while ring[0] != ring[-1]:
            ende = ring[-1]
            weiter = next(
                (j for j in by_end.get(ende, ()) if not benutzt[j]), None
            )
            if weiter is None:
                break
            benutzt[weiter] = True
            stueck = offen[weiter]
            if stueck[0] != ende:
                stueck = stueck[::-1]
            ring.extend(stueck[1:])
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) >= 4:
            ringe.append(ring)
    return ringe
