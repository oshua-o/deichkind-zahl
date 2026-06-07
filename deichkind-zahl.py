import time, sys
from pathlib import Path
from collections import deque
import musicbrainzngs as mb

mb.set_useragent("KollaborationsZahl", "0.3", "kontakt@beispiel.de")

MAX_DEPTH = 7
CACHE_FILE = Path("collab_cache.csv")


# ── Cache ──────────────────────────────────────────────────────────────────────
#
# Format: eine Zeile pro Kante, vier Felder:
#   mbid_a,mbid_b,name_a,name_b
# Beispiel:
#   abc-123,def-456,Deichkind,Fettes Brot
#
# Sonderzeilen: "KNOWN:<mbid>" markieren Kuenstler*innen, deren Kollaborationen
# vollstaendig abgerufen wurden.
# Alte 2-Spalten-Zeilen werden beim Laden toleriert (Rueckwaertskompatibilitaet).
#
# Im Speicher:
#   edges         : set[frozenset[str, str]]  - alle bekannten Kanten
#   neighbors     : dict[str, set[str]]       - Nachbarn je Knoten
#   known_artists : set[str]                  - vollstaendig abgefragte MBIDs
#   name_map      : dict[str, str]            - mbid -> Kuenstler*innen-Name

def load_cache() -> tuple[set, dict, set, dict]:
    edges: set[frozenset] = set()
    neighbors: dict[str, set[str]] = {}
    known_artists: set[str] = set()
    name_map: dict[str, str] = {}

    if not CACHE_FILE.exists():
        return edges, neighbors, known_artists, name_map

    try:
        with CACHE_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("KNOWN:"):
                    known_artists.add(line[6:])
                    continue
                parts = line.split(",", 3)   # maxsplit=3: Name darf Komma enthalten
                if len(parts) < 2:
                    continue
                a, b = parts[0].strip(), parts[1].strip()
                if len(parts) >= 4:           # 4-Spalten-Format mit Namen
                    name_map.setdefault(a, parts[2].strip())
                    name_map.setdefault(b, parts[3].strip())
                edge = frozenset({a, b})
                if edge not in edges:
                    edges.add(edge)
                    neighbors.setdefault(a, set()).add(b)
                    neighbors.setdefault(b, set()).add(a)
    except IOError as e:
        print(f"[Cache] Fehler beim Laden: {e} - starte mit leerem Cache.")

    print(f"[Cache] {len(edges)} Kanten, {len(known_artists)} vollstaendig bekannte Kuenstler*innen.")
    return edges, neighbors, known_artists, name_map


def append_to_cache(new_edges: set, new_known_id: str, name_map: dict) -> None:
    """Haengt neue Kanten (4 Spalten mit Namen) + KNOWN-Marker ans Ende der CSV."""
    try:
        with CACHE_FILE.open("a", encoding="utf-8") as f:
            for edge in new_edges:
                a, b = tuple(edge)
                na = name_map.get(a, "")
                nb = name_map.get(b, "")
                f.write(f"{a},{b},{na},{nb}\n")
            f.write(f"KNOWN:{new_known_id}\n")
    except IOError as e:
        print(f"[Cache] Fehler beim Schreiben: {e}")


# ── MusicBrainz ────────────────────────────────────────────────────────────────

def search_artist(name: str) -> dict | None:
    """Sucht Kuenstler*in interaktiv, gibt gewahltes Artist-Dict zurueck."""
    try:
        results = mb.search_artists(artist=name, limit=5).get("artist-list", [])
    except Exception as e:
        print(f"Fehler: {e}"); return None

    if not results:
        print(f"Niemanden namens '{name}' gefunden."); return None

    if len(results) == 1:
        return results[0]

    print()
    for i, a in enumerate(results[:5]):
        area = a.get("area", {}).get("name", "")
        print(f"  {i+1}. {a['name']}" + (f"  [{area}]" if area else ""))
    while True:
        try:
            c = int(input("Nummer waehlen: ")) - 1
            if 0 <= c < len(results):
                return results[c]
        except ValueError:
            pass


def fetch_collaborators_from_api(artist_mbid: str) -> dict[str, str]:
    """
    Holt alle Co-Artist-MBIDs direkt von MusicBrainz.
    Gibt dict[mbid -> name] zurueck (Songs werden nicht gespeichert).
    """
    collabs: dict[str, str] = {}
    offset, limit = 0, 100

    while True:
        try:
            res = mb.browse_recordings(
                artist=artist_mbid, limit=limit,
                offset=offset, includes=["artist-credits"]
            )
        except Exception as e:
            print(f"  API-Fehler: {e}"); break

        for rec in res.get("recording-list", []):
            credits = rec.get("artist-credit", [])
            pairs = [(c["artist"]["id"], c["artist"]["name"])
                     for c in credits if isinstance(c, dict) and "artist" in c]
            if artist_mbid not in [aid for aid, _ in pairs]:
                continue
            for aid, aname in pairs:
                if aid != artist_mbid and aid not in collabs:
                    collabs[aid] = aname

        offset += limit
        if offset >= res.get("recording-count", 0):
            break
        time.sleep(1.1)

    return collabs


def get_neighbors(artist_mbid: str, artist_name: str,
                  edges: set, neighbors: dict, known_artists: set,
                  name_map: dict) -> set[str]:
    """
    Gibt die Menge aller bekannten Nachbar-MBIDs zurueck.
    Nutzt Cache wenn vollstaendig bekannt, fragt sonst die API ab.
    Befuellt name_map mit neu entdeckten Namen.
    """
    if artist_mbid in known_artists:
        print(f"  [Cache] Nachbarn von {artist_name} bereits vollstaendig bekannt.")
        return neighbors.get(artist_mbid, set())

    print(f"  [API]   Lade Kollaborationen von {artist_name}...")
    collabs = fetch_collaborators_from_api(artist_mbid)  # dict[mbid -> name]

    new_edges: set[frozenset] = set()
    for cid, cname in collabs.items():
        name_map.setdefault(cid, cname)      # Namen fuer spaetere Pfad-Ausgabe merken
        edge = frozenset({artist_mbid, cid})
        if edge not in edges:
            edges.add(edge)
            new_edges.add(edge)
            neighbors.setdefault(artist_mbid, set()).add(cid)
            neighbors.setdefault(cid, set()).add(artist_mbid)

    known_artists.add(artist_mbid)
    append_to_cache(new_edges, artist_mbid, name_map)   # name_map weitergeben
    print(f"          -> {len(collabs)} Kollaborateur*innen, "
          f"{len(new_edges)} neue Kanten gespeichert.")

    return neighbors.get(artist_mbid, set())


# ── Bidirektionale BFS ─────────────────────────────────────────────────────────
#
# Zwei Fronten: "forward" (von Start) und "backward" (von Ziel).
# In jeder Runde wird die kleinere Front um eine volle Ebene erweitert.
# Sobald ein Knoten der erweiterten Front bereits in der anderen Front
# besucht wurde, ist ein Pfad gefunden.
#
# visited_fwd / visited_bwd: dict[mbid -> (depth, [name, ...])]

def bidirectional_bfs(root_id: str, root_name: str,
                      target_id: str, target_name: str,
                      edges: set, neighbors: dict,
                      known_artists: set, name_map: dict) -> dict:

    visited_fwd: dict[str, tuple[int, list[str]]] = {root_id:   (0, [root_name])}
    visited_bwd: dict[str, tuple[int, list[str]]] = {target_id: (0, [target_name])}

    queue_fwd: deque = deque([(root_id,   root_name,   0)])
    queue_bwd: deque = deque([(target_id, target_name, 0)])

    # name_map kommt bereits vorbelegt aus main (Start+Ziel+Cache-Namen)
    name_map[root_id]   = root_name
    name_map[target_id] = target_name

    def expand(queue: deque, visited_this: dict, visited_other: dict) -> dict | None:
        """Erweitert eine volle Ebene. Gibt Ergebnis-Dict zurueck oder None."""
        if not queue:
            return None

        current_depth = queue[0][2]
        while queue and queue[0][2] == current_depth:
            cur_id, cur_name, depth = queue.popleft()
            if depth >= MAX_DEPTH:
                continue

            for nid in get_neighbors(cur_id, cur_name, edges, neighbors,
                                     known_artists, name_map):
                if nid in visited_this:
                    continue

                # name_map ist jetzt immer aktuell bevor wir den Namen brauchen
                nname = name_map.get(nid, f"[{nid[:8]}]")
                visited_this[nid] = (depth + 1, visited_this[cur_id][1] + [nname])

                if nid in visited_other:
                    path_fwd = visited_fwd[nid][1]
                    path_bwd = visited_bwd[nid][1]
                    full_path = path_fwd + list(reversed(path_bwd))[1:]
                    depth_total = visited_fwd[nid][0] + visited_bwd[nid][0]
                    return {"found": True, "number": depth_total, "path": full_path}

                if depth + 1 < MAX_DEPTH:
                    queue.append((nid, nname, depth + 1))

        return None

    while queue_fwd or queue_bwd:
        if len(queue_fwd) <= len(queue_bwd):
            result = expand(queue_fwd, visited_fwd, visited_bwd)
        else:
            result = expand(queue_bwd, visited_bwd, visited_fwd)
        if result:
            return result

    return {"found": False}


# ── Hauptprogramm ──────────────────────────────────────────────────────────────

def main():
    print("\n-- Deichkind-Zahl Rechner --\n")
    print(f"[Cache] Lade '{CACHE_FILE}' ...")
    edges, neighbors, known_artists, name_map = load_cache()   # 4 Rueckgabewerte
    print()

    # Startkuenstler*in
    start_input = input("Start-Kuenstler*in [Enter fuer Deichkind]: ").strip()
    if start_input:
        start = search_artist(start_input)
        if not start: sys.exit(1)
    else:
        print("Suche Deichkind...")
        start = search_artist("Deichkind")
        if not start: sys.exit(1)
    print(f"Start: {start['name']}\n")

    # Ziel-Kuenstler*in
    target_input = input("Ziel-Kuenstler*in: ").strip()
    if not target_input: sys.exit(0)
    target = search_artist(target_input)
    if not target: sys.exit(1)
    print(f"Ziel:  {target['name']}\n")

    if start["id"] == target["id"]:
        print(f"{start['name']}-Zahl von {target['name']}: 0")
        sys.exit(0)

    print(f"Suche Pfad von {start['name']} zu {target['name']} "
          f"(bidirektional, max. Tiefe {MAX_DEPTH})...\n")

    try:
        result = bidirectional_bfs(
            start["id"],  start["name"],
            target["id"], target["name"],
            edges, neighbors, known_artists, name_map   # name_map mitgeben
        )
    except KeyboardInterrupt:
        print("\nAbgebrochen."); sys.exit(0)

    print()
    if result["found"]:
        n = result["number"]
        path = result["path"]
        print(f"{start['name']}-Zahl von {target['name']}: {n}\n")
        print("Weg:")
        for i, step in enumerate(path):
            print(f"  {'-> ' if i > 0 else '   '}{step}")
    else:
        print(f"Kein Pfad gefunden innerhalb Tiefe {MAX_DEPTH}.")
        print("Tipp: MAX_DEPTH im Script erhoehen.")
    print()


if __name__ == "__main__":
    main()