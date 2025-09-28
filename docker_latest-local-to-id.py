#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
get_used_local_version_id.py
Ermittelt den/die lokalen Repo-Digest(s) eines Images via:
  docker image inspect <repo[:tag]>
und mappt diese Digests auf die zugehörigen Tags/Versionen auf Docker Hub.

Aufruf:
  python3 get_used_local_version_id.py <repo[:tag]> [--scan-all] [--max-pages N] [-v] [--json]

Beispiele:
  python3 get_used_local_version_id.py ollama/ollama:latest
  python3 get_used_local_version_id.py ubuntu -v --json
  python3 get_used_local_version_id.py ollama/ollama:latest --scan-all --max-pages 10
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from typing import List, Set, Tuple

LOG = logging.getLogger("local-image-revlookup")
HUB_BASE = "https://hub.docker.com/v2/repositories/{repo}/tags/?page_size=100"
SEMVER_RX = re.compile(r"^v?\d+\.\d+\.\d+(?:[.-].+)?$")


# ---------- Hilfe & Beispiele ----------

def print_help_and_examples(parser: argparse.ArgumentParser) -> None:
    parser.print_help()
    print("""
Beispielaufrufe:
  # Schnell: stoppt nach dem ersten Treffer
  python3 get_used_local_version_id.py ollama/ollama:latest

  # Vollständig: scannt alle Tag-Seiten und sammelt alle passenden Tags
  python3 get_used_local_version_id.py ollama/ollama:latest --scan-all

  # Debug-Logs + Seitenlimit
  python3 get_used_local_version_id.py ollama/ollama:latest -v --scan-all --max-pages 5

Beispielausgabe (typisch) für 'ollama/ollama:latest':
  Image: ollama/ollama:latest
  Repository: ollama/ollama
  Lokale RepoDigest(s):
    - sha256:24d41d792306fc3221de215bb6f225faf981712d1f38083d8c61301dfa2b69b3
  Zugeordnete Tags auf Docker Hub:
    - ollama/ollama:0.11.11

  => Wahrscheinlich verwendete Version/ID: 0.11.11

Hinweise:
  * Container muss NICHT laufen, aber der Docker-Daemon muss aktiv sein.
  * Wenn '.RepoDigests' leer ist (z. B. selbst gebautes Image), kann kein zuverlässiges Tag-Mapping erfolgen.
""".rstrip())


# ---------- logging ----------

def setup_logging(debug: bool):
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.CRITICAL + 1,
        format="%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------- helpers ----------

def normalize_repo(ref: str) -> Tuple[str, str]:
    """Entfernt docker.io/… Präfixe, setzt default tag=latest, fügt library/ bei offiziellen Images hinzu."""
    for p in ("docker.io/", "index.docker.io/", "registry-1.docker.io/"):
        if ref.startswith(p):
            ref = ref[len(p):]
    if ":" in ref:
        repo, tag = ref.rsplit(":", 1)
    else:
        repo, tag = ref, "latest"
    if "/" not in repo:
        repo = f"library/{repo}"
    return repo, tag


def repo_for_output(repo: str) -> str:
    return repo[8:] if repo.startswith("library/") else repo


def run(cmd: List[str]) -> subprocess.CompletedProcess:
    LOG.debug("RUN: %s", " ".join(cmd))
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def fetch_json(url: str, timeout=15, retries=5, backoff=1.5):
    att = 0
    last = None
    while att <= retries:
        att += 1
        LOG.debug("GET %s (try %d/%d)", url, att, retries + 1)
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "local-revlookup/1.1"}),
                timeout=timeout,
            ) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            last = e
            time.sleep(min(backoff**att, 8))
    raise RuntimeError(f"HTTP-Fehler bei Abruf {url}: {last}") from last


def split_latest_versions(tags: Set[str]):
    latest, versions, others = [], [], []
    for t in tags:
        if t == "latest":
            latest.append(t)
        elif SEMVER_RX.match(t or ""):
            versions.append(t)
        else:
            others.append(t)
    versions.sort()
    others.sort()
    return latest, versions, others


# ---------- core: local digest(s) ----------

def get_local_repo_digests_via_docker_inspect(user_ref: str, hub_repo: str) -> Tuple[Set[str], str | None]:
    """
    Holt .RepoDigests via docker image inspect. Gibt ein Set von sha256:* (Manifest-Digests) zurück.
    Zusätzlich wird .Id zurückgegeben (Config-Digest), falls RepoDigests leer sind.
    """
    # 1) RepoDigests
    cp = run(["docker", "image", "inspect", user_ref, "--format", "{{json .RepoDigests}}"])
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip())

    try:
        repo_digests = json.loads(cp.stdout.strip() or "[]")
    except json.JSONDecodeError:
        raise RuntimeError(f"Unerwartete Ausgabe von docker inspect: {cp.stdout!r}")

    LOG.debug("RepoDigests raw: %s", repo_digests)

    # Repo-Varianten, wie sie in RepoDigests vorkommen können
    short = repo_for_output(hub_repo)
    variants = {
        short,
        f"docker.io/{short}",
        f"index.docker.io/{short}",
        f"registry-1.docker.io/{short}",
    }

    matched: Set[str] = set()
    for entry in repo_digests or []:
        # Beispiel: "ollama/ollama@sha256:..."
        if "@sha256:" not in entry:
            continue
        left, digest = entry.split("@", 1)
        if any(left.endswith(v) for v in variants):
            matched.add(digest)

    # 2) .Id als Fallback merken (Config-Digest)
    cp_id = run(["docker", "image", "inspect", user_ref, "--format", "{{.Id}}"])
    local_id = cp_id.stdout.strip() if cp_id.returncode == 0 else None
    if local_id and local_id.startswith("sha256:"):
        LOG.debug(".Id (config) = %s", local_id)

    return matched, local_id


# ---------- docker hub mapping ----------

def collect_hub_tags_for_digests(
    hub_repo: str,
    target_digests: Set[str],
    *,
    scan_all: bool = False,
    max_pages: int = 200
) -> Set[str]:
    """
    Sucht auf Docker Hub nach Tags, die auf einen der target_digests zeigen.
    - scan_all=False (Default): stoppt nach dem ersten Treffer (schnell)
    - scan_all=True: scannt alle Seiten (vollständige Liste)
    - max_pages: harte Obergrenze für Anzahl Seiten (Sicherheitsnetz)
    """
    base = HUB_BASE.format(repo=urllib.parse.quote(hub_repo, safe=""))
    url = base
    hits: Set[str] = set()
    visited = set()
    page = 0

    while url:
        if url in visited:
            LOG.error("Pagination-Loop bei Hub-/tags/. Abbruch.")
            break
        visited.add(url)
        page += 1
        if page > max_pages:
            LOG.warning("max_pages=%d erreicht. Breche Suche ab.", max_pages)
            break

        data = fetch_json(url)
        res = data.get("results", [])
        page_hits = 0

        for obj in res:
            name = obj.get("name")
            dtop = obj.get("digest") or ""
            matched = False

            if dtop in target_digests:
                matched = True
            else:
                for img in obj.get("images") or []:
                    d = img.get("digest") or ""
                    if d in target_digests:
                        matched = True
                        break

            if matched:
                hits.add(name)
                page_hits += 1
                if not scan_all:
                    LOG.debug("Erster Treffer '%s' auf Seite %d – breche Suche ab (scan_all=False).", name, page)
                    return hits  # sofort zurück

        LOG.debug("Hub Seite %d: %d Treffer", page, page_hits)
        next_url = data.get("next")
        if not next_url:
            break
        url = next_url

    return hits


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser(
        description="Lokalen Image-Digest via docker inspect ermitteln und auf Docker-Hub-Tags mappen.",
        add_help=True,
    )
    parser.add_argument("ref", nargs="?", help="Repository[:tag], z. B. ollama/ollama:latest (Tag optional: latest)")
    parser.add_argument("-v", "--debug", action="store_true", help="Debug-Logs")
    parser.add_argument("--json", action="store_true", help="JSON-Ausgabe")
    parser.add_argument("--scan-all", action="store_true",
                        help="Alle Tag-Seiten scannen (langsamer). Standard: stoppt nach erstem Treffer.")
    parser.add_argument("--max-pages", type=int, default=200,
                        help="Maximale Anzahl Tag-Seiten, die abgerufen werden (Default: 200).")

    # Wenn ohne Parameter aufgerufen → Hilfe + Beispiele ausgeben und beenden
    if len(sys.argv) == 1:
        print_help_and_examples(parser)
        sys.exit(2)

    args = parser.parse_args()
    setup_logging(args.debug)

    try:
        hub_repo, tag = normalize_repo(args.ref)
        out_repo = repo_for_output(hub_repo)
        LOG.info("Input: %s | Hub-Repo: %s | Tag: %s", args.ref, hub_repo, tag)

        # 1) Lokale Digests (Manifest) via docker inspect
        repo_digests, local_id = get_local_repo_digests_via_docker_inspect(args.ref, hub_repo)

        if not repo_digests:
            # Kein zuverlässiges Mapping möglich – freundlich erklären:
            msg = (
                "Achtung: '.RepoDigests' ist leer. Häufige Gründe: Image lokal gebaut "
                "oder aus einer anderen Registry geladen. Die '.Id' (Config-Digest) ist "
                "i. d. R. nicht eindeutig einem Registry-Tag zuordenbar."
            )
            if args.json:
                payload = {
                    "input": args.ref,
                    "repository": out_repo,
                    "local_repo_digests": [],
                    "local_id": local_id,
                    "mapping_possible": False,
                    "note": msg,
                }
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                sys.exit(0)
            else:
                print(f"Image: {args.ref}")
                print(f"Repository: {out_repo}")
                if local_id:
                    print(f"Lokale .Id: {local_id}")
                print(msg)
                sys.exit(0)

        LOG.info("Lokale RepoDigest(s): %s", ", ".join(sorted(repo_digests)))

        # 2) Tags auf Docker Hub dazu finden (mit Early-Exit/Scan-All und max-pages)
        tags = collect_hub_tags_for_digests(
            hub_repo,
            repo_digests,
            scan_all=args.scan_all,
            max_pages=args.max_pages
        )

        latest, versions, others = split_latest_versions(tags)

        if args.json:
            payload = {
                "input": args.ref,
                "repository": out_repo,
                "local_repo_digests": sorted(repo_digests),
                "local_id": local_id,
                "tags": {
                    "versions": versions,
                    "latest": latest,
                    "others": others,
                },
                "all_tags_sorted": sorted(tags),
                "scan_all": args.scan_all,
                "max_pages": args.max_pages,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        # Menschliche Ausgabe
        print(f"Image: {args.ref}")
        print(f"Repository: {out_repo}")
        print("Lokale RepoDigest(s):")
        for d in sorted(repo_digests):
            print(f"  - {d}")

        print("Zugeordnete Tags auf Docker Hub:")
        for t in versions:
            print(f"  - {out_repo}:{t}")
        for t in latest:
            print(f"  - {out_repo}:{t}")
        for t in others:
            print(f"  - {out_repo}:{t}")

        preferred = versions[-1] if versions else (latest[0] if latest else (others[0] if others else None))
        if preferred:
            print(f"\n=> Wahrscheinlich verwendete Version/ID: {preferred}")

    except FileNotFoundError:
        print("Fehler: 'docker' CLI nicht gefunden. Bitte Docker installieren/ PATH prüfen.", file=sys.stderr)
        sys.exit(127)
    except subprocess.SubprocessError as e:
        print(f"Fehler: docker inspect fehlgeschlagen: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        err = str(e)
        if "Cannot connect to the Docker daemon" in err:
            print(
                "Fehler: Docker-Daemon nicht erreichbar. Bitte Docker starten.\n"
                "Hinweis: Container muss NICHT laufen – aber der Daemon muss aktiv sein.",
                file=sys.stderr,
            )
        else:
            print(f"Fehler: {err}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if args.debug:
            LOG.exception("Unerwarteter Fehler: %s", e)
        else:
            print(f"Unerwarteter Fehler: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
