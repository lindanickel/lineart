# S-Bahn Berlin Netz

Das Projekt ist ein kleines Experiment, wie gut Claude Code beim Erstellen von
komplexen Plots funktioniert. Der Großteil des Codes wurde mit Claude Code
erstellt.

Der Plan wird nicht aus Koordinaten gezeichnet, sondern aus einer **Linie als
Folge von Fahrbefehlen**: Startrichtung, Stationen, relative Kurven, starre
(`FixPath`) und elastische (`FlexPath`) Abschnitte. Wo mehrere Linien dieselbe
Strecke benutzen, liegt darunter ein gemeinsamer **Korridor**, der den
Spurversatz vergibt. Die Stationspositionen fallen aus dem Lösen dieses
Systems heraus — verschiebt sich eine Kante, wandert der Rest mit.

Engine-Erklärung: [`engine_description.md`](engine_description.md)

## Erzeugen

```bash
conda env create -f environment.yml && conda activate lineart
```

```bash
python berlin_2026.py && python berlin_2030.py && python berlin_2030plus.py && python berlin_2040plus.py
```

Jede Stufe beschreibt nur den Unterschied zur vorherigen und leitet mit
`Net.derive()` von ihr ab:

```
berlin_2026  →  berlin_2030  →  berlin_2030plus  →  berlin_2040plus
   Bestand      Siemensbahn      City-S-Bahn BA2      Nahverkehrs-
                                 S25, Kamenzer Damm   tangente Nord
```

## Alte Dateien

Drei eigenständige Vorgänger-Engines, jede mit eigenem `__main__`. Keine davon
wird vom aktuellen Stand importiert; sie liegen nur noch als
Entwicklungsgeschichte herum.

- `sbahnberlin_plot.py` — der erste Generator. Netz über feste
  Rasterkoordinaten (`STATIONS: Dict[str, Pt]`). → `outputs/netzplan.svg`
- `turn_engine.py` — erstmals turn-basiert statt koordinatenbasiert, komplett
  eigenständig. → `outputs/netzplan_turns.svg`
- `chatgpt_netz.py` — die monolithische „Gleiskarte-Engine“ mit
  `FixPath`/`FlexPath`, `Config`, `StyleConfig` und Korridoren. Der direkte
  Vorfahr von `netmap/`, das daraus in Module zerlegt wurde. →
  `outputs/gleiskarte.svg`

## Das aktuelle Berliner S-Bahn Netz, Stand 2026

![S-Bahn-Netz 2026](outputs/berlin_sbahn_2026.png)

Vektorfassung: [`outputs/berlin_sbahn_2026.svg`](outputs/berlin_sbahn_2026.svg)

## Zukünftige Stadien des Berliner S-Bahn Netzes

Die Maßnahmen sind in drei Stufen sortiert. Maßgeblich für die Einordnung ist
weniger das einzelne Bauwerk als die **Abhängigkeit**: die City-S-Bahn kann nur
Abschnitt für Abschnitt wachsen, und die Nordast-Umbauten hängen alle am
Karower Kreuz.

### Stufe 1 — Ende der 2020er (`berlin_2030.py`)

- Fertigstellung der Siemensbahn: S6 von Gartenfeld über Siemensstadt und
  Wernerwerk zum Ring und weiter zum Hauptbahnhof
- Fertigstellung der endgültigen Station Hbf tief und Ablösung der
  Interimsstation mit ihrem einen Bahnsteig für Halbzüge
- Teilweise Zweigleisiger Ausbau der Strecke Hoppegarten – Strausberg (S5)
  für einen 10-Minutentakt bis Strausberg
- S3 endet in Charlottenburg, die S75 übernimmt dafür den Spandauer Ast
  und fährt von Wartenberg durch bis Spandau
- Neue Linie S86 Grünau – Buch: sie fährt auf vorhandener Strecke, bis
  Blankenburg auf dem Weg der S8, danach neben der S2. Beide Endpunkte
  werden Umsteigebahnhöfe.

S6 und S15 enden hier noch am Hauptbahnhof; der Tunnel nach Süden kommt erst
in Stufe 2.

![S-Bahn-Netz 2030](outputs/berlin_sbahn_2030.png)

### Stufe 2 — 2030er Jahre (`berlin_2030plus.py`)

- Fertigstellung des BA2 der City-S-Bahn: Tunnel von Hbf tief bis Potsdamer
  Platz
- Ausbau der S25 nach Süden um Iserstraße und Stahnsdorf
- Zweigleisiger Ausbau der Kremmener Bahn (S25) bis Hennigsdorf, mit der neuen
  Station Borsigwalde
- Verlängerung der S25 nach Norden bis Velten, mit Hennigsdorf Nord und Velten
- Neue Station Kamenzer Damm auf der S2
- Zweigleisiger Wiederaufbau der Strecke Buch – Bernau (S2)

Mit dem durchgebundenen Tunnel gibt die S85 die Nordbahn ab und endet in
Pankow; die S15 übernimmt sie und fährt von Frohnau bis Zehlendorf durch.
Aus den beiden Zuggruppen der S25 werden zwei Linien: die S25 fährt über den
neuen Tunnel und den Hauptbahnhof bis Velten, die S26 auf dem alten Weg durch
den Nord-Süd-Tunnel bis Hennigsdorf. Ihren bisherigen Nordast Pankow –
Blankenburg gibt die S26 an die S2 ab.

![S-Bahn-Netz 2030plus](outputs/berlin_sbahn_2030plus.png)

### Stufe 3 — 2040er Jahre (`berlin_2040plus.py`)

- Fertigstellung des BA3a und 3b der City-S-Bahn: Verlängerung von Potsdamer
  Platz bis Yorckstraße und Yorckstraße (Großgörschenstraße), mit der neuen
  Station Gleisdreieck. Errichtung der Cheruskerkurve und Verbindung der
  Stammbahn Richtung Osten mit dem Südring
- Bau der Nahverkehrstangente Nord und Errichtung des Kreuzungsbahnhofs
  Karower Kreuz, Verlängerung der S75 bis Birkenwerder
- Zwei neue Stationen Bucher Straße und Schönlinder Straße auf dem Berliner
  Außenring
- Die S6 fährt über den BA3 vom Potsdamer Platz weiter und biegt in
  Schöneberg auf den Südring ab — von dort bis Königs Wusterhausen. Die S46
  fährt dorthin deshalb nicht mehr; an ihre Stelle tritt die bis Westend
  verlängerte S47, die damit S46 heißt

Die S86 entfällt; ihren Ast von Pankow nach Buch fährt jetzt die S85, die
bisher in Pankow endete. Die S75 übernimmt den Nordast der bisherigen S8,
die dafür in Buch beginnt. Im Westen geht der Spandauer Ast zurück an die S3; die S75 endet
dort in Charlottenburg.

![S-Bahn-Netz 2040plus](outputs/berlin_sbahn_2040plus.png)

Vektorfassung: [`outputs/berlin_sbahn_2040plus.svg`](outputs/berlin_sbahn_2040plus.svg)

### Unrealistische Erweiterungen

Weitere angedachte Erweiterungen sind nicht abgebildet, da die Realisierung nach
heutigem Stand sehr unrealistisch ist:

- Nahverkehrstangente Süd von Altglienicke oder Grünau über den Berliner
  Außenring bis Springpfuhl und weiter auf der geplanten S75 bis Birkenwerder,
  als neue Linie S95 von Flughafen BER bis Birkenwerder. Neue Stationen
  *Glienicker Straße*, *Dörpfeldstraße* (Spindlersfeld),
  *FEZ (Str. An der Wuhlheide)*, *Wuhlheider Kreuz* (Übergang zur S3),
  *Karlshorst Nord*, *Biesdorf Süd*, *Biesdorfer Kreuz* (Übergang zur S5)
- Verlängerung der S-Bahn von Spandau bis Finkenkrug mit den Stationen
  *Nauener Straße*, *Klosterbuschweg*, *Albrechtshof*, *Seegefeld*,
  *Falkensee*, *Finkenkrug*
- Verlängerung der Siemensbahn bis Hakenfelde mit den Stationen
  *Insel Gartenfeld*, *Wasserstadt Oberhavel* und *Hakenfelde*
- Verlängerung der S2 von Blankenfelde bis Rangsdorf mit den Stationen
  *Dahlewitz*, *Dahlewitz-Rolls-Royce*, *Rangsdorf*
- Wiederaufbau der Stammbahn von Zehlendorf bis Griebnitzsee als S-Bahn mit den
  Stationen *Zehlendorf-Süd*, *Düppel-Kleinmachnow*, *Europarc-Dreilinden*

Zweigleisiger Ausbau:

- Frohnau – Oranienburg (S1)
- Wannsee – Potsdam (S7)
- Wildau – Königs Wusterhausen (S46)
