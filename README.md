# docker_latest-to-id
Ein kleines Python-Tool, das nicht die aktuelle latest-Version aus der Registry, sondern die lokal zuletzt heruntergeladene LATEST Image-Version bestimmt **um folgendes Problem zu lösen**:

> [!TIP]
> Einen docker compose Dienst von `latest` auf eine fixe Versionsnummer umstellen, ohne auf die aktuell neueste `latest` Version aktualisieren zu müssen.

Dazu muss natürlich die aktuell verwendete Versionsnummer bekannt sein, um diese entsprechend in der docker compose Datei anzugeben.


Gerade wenn ein Image mit dem Tag `latest` verwendet wird, ist oft unklar, auf welche konkrete Version (z. B. `v0.11.11`) dieses lokal tatsächlich verweist und ob zwischen eine neuere latest Version existiert.

Das Script liest die **lokalen Image-Digests** via `docker image inspect` aus und ermittelt anschließend über die **Docker Hub API**, welche offiziellen Tags/Versionen auf diesen Digest zeigen.  




---

## Features

- **Bestimmt die lokalen Repo-Digests** (`sha256:…`) eines Images mit `docker image inspect`.
- **Mappt diese Digests auf Docker Hub Tags**, sodass man die echte Version kennt (z. B. `0.11.11`).
- **Container muss nicht laufen** – nur der Docker-Daemon muss erreichbar sein.
- Unterstützt **JSON-Ausgabe** für Automatisierung und CI/CD.
- **Schnelle Suche**: Standardmäßig wird nach dem ersten Treffer abgebrochen.
- Optional: `--scan-all`, um alle Tag-Seiten auf Docker Hub zu durchsuchen (falls ein Digest mehreren Tags zugeordnet ist).
- Option `--max-pages`, um die Suche auf eine bestimmte Anzahl Seiten zu begrenzen.
- **Debug-Modus** (`-v`), um jeden Schritt nachzuvollziehen.

---

## Voraussetzungen

- Docker ist installiert und der **Docker-Daemon läuft**.
- Python 3.7+  
- Internetzugang zu [hub.docker.com](https://hub.docker.com), um die Mapping-Daten abzufragen.

---

## Installation

Einfach das Skript auf den Computer übertragen, wo Docker läuft und ausführbar machen:

```bash
chmod +x docker_latest-local-to-id.py
```
Optional in $PATH aufnehmen oder via python3 docker_latest-local-to-id.py … aufrufen.


## Verwendung
```bash
./docker_latest-local-to-id.py <repository[:tag]> [Optionen]
```


### Beispiele:
```
# Standard (schnell, stoppt nach erstem Treffer)
./docker_latest-local-to-id.py ollama/ollama:latest
```

```bash
# Debug-Ausgabe aktivieren
./docker_latest-local-to-id.py ollama/ollama:latest -v
```

```bash
# Alle Seiten durchsuchen
./docker_latest-local-to-id.py ollama/ollama:latest --scan-all
```

```bash
# Suche auf maximal 5 Seiten beschränken
./docker_latest-local-to-id.py ollama/ollama:latest --scan-all --max-pages 5
```

```bash
# JSON-Ausgabe (z. B. für CI/CD)
./docker_latest-local-to-id.py ollama/ollama:latest --json
```


## Optionen

|Option          | Beschreibung  |
|----------------|---------------|
| -v             | --debug	Debug-Logs aktivieren  |
| --json         | JSON-Ausgabe statt menschenlesbarer Text  |
|  --scan-all    |  Alle Tag-Seiten durchsuchen (langsamer). Standard: Stoppt nach erstem Treffer |
| --max-pages N  | Maximale Anzahl von Seiten, die von der Hub-API geladen werden (Default: 10)  |


## Beispielausgabe

### Standardausgabe

```bash
$ python3 docker_latest-local-to-id.py ollama/ollama:latest

Image: ollama/ollama:latest
Repository: ollama/ollama
Lokale RepoDigest(s):
  - sha256:24d41d792306fc3221de215bb6f225faf981712d1f38083d8c61301dfa2b69b3
Zugeordnete Tags auf Docker Hub:
  - ollama/ollama:0.11.11

=> Wahrscheinlich verwendete Version/ID: 0.11.11
```


### JSON-Ausgabe
```bash
$ python3 docker_latest-local-to-id.py ollama/ollama:latest
{
  "input": "ollama/ollama:latest",
  "repository": "ollama/ollama",
  "local_repo_digests": [
    "sha256:24d41d792306fc3221de215bb6f225faf981712d1f38083d8c61301dfa2b69b3"
  ],
  "local_id": "sha256:6682ce39a34f9d92fb2ea8ac528d203d7e5eeecc7c9bcfff609681fcd92a56d6",
  "tags": {
    "versions": [
      "0.11.11"
    ],
    "latest": [],
    "others": []
  },
  "all_tags_sorted": [
    "0.11.11"
  ],
  "scan_all": false,
  "max_pages": 10
}
```




## Hinweise & Einschränkungen
* Das Skript nutzt .RepoDigests aus docker image inspect.
Wenn ein Image lokal gebaut oder aus einer anderen Registry geladen wurde, enthält .RepoDigests u. U. keine Werte.
In diesem Fall wird nur die .Id angezeigt, aber es kann kein zuverlässiges Mapping auf Hub-Tags erfolgen.

* Das Mapping funktioniert nur für öffentliche Images auf Docker Hub.
Für private Registries oder andere Anbieter (GHCR, Quay.io) müsste die API-Abfrage angepasst werden.



