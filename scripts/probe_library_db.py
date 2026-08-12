"""Probe Live's library SQLite databases before mcp_server/library.py exists.

Read-only, stdlib-only. Run with Live open AND closed if possible — the
mode=ro&immutable=1 safety claim only means something while Live holds the DB.

Run:  python scripts/probe_library_db.py [--db-dir PATH] [--ref-query 808]

Every fact mcp_server/library.py relies on must appear in this output first
(roadmap ground rule 5: measure, don't assume). Sections are independent —
a failure prints and the next section still runs.
"""

import argparse
import io
import os
import re
import sqlite3
import struct
import sys
import time
import traceback
from pathlib import Path

# cp1252 consoles crash on fancy characters in DB content (lesson from the
# live_checkpoint scripts) — replace, never die.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, errors="replace")

FILES_DB_RE = re.compile(r"^Live-files-(\d+)\.db$")
PLUGINS_DB_RE = re.compile(r"^Live-plugins-(\d+)\.db$")


def default_db_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "Ableton" / "Live Database"
    return Path.home() / "Library" / "Application Support" / "Ableton" / "Live Database"


def section(title):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
            try:
                return fn(*args, **kwargs)
            except Exception:
                traceback.print_exc()
                print(f"[SECTION FAILED: {title}]")
                return None

        return wrapper

    return decorator


def to_uri(path: Path, *, immutable: bool) -> str:
    # Forward slashes + percent-quoting; sqlite URIs choke on backslashes and
    # need %-escapes for spaces/%/# in path segments.
    from urllib.parse import quote

    quoted = quote(str(path).replace("\\", "/"), safe="/:")
    suffix = "&immutable=1" if immutable else ""
    return f"file:{quoted}?mode=ro{suffix}"


def open_ro(path: Path, *, immutable: bool = True) -> sqlite3.Connection:
    return sqlite3.connect(to_uri(path, immutable=immutable), uri=True)


def join_parts(parts: list[str]) -> str:
    # Run 1: the Windows drive root row is literally 'C:\' — a naive
    # '/'.join produced 'C:\/Users/...'. Strip trailing separators per part.
    cleaned = [p.rstrip("\\/") for p in parts]
    return "/".join(p for p in cleaned if p) if cleaned[0] else "/" + "/".join(cleaned[1:])


@section("1. DB enumeration + selection rules")
def probe_selection(db_dir: Path) -> tuple[Path, Path]:
    entries = sorted(db_dir.iterdir())
    for p in entries:
        print(
            f"  {p.name:32s} {p.stat().st_size:>12,} bytes  mtime {time.ctime(p.stat().st_mtime)}"
        )

    files_candidates = [
        (int(FILES_DB_RE.match(p.name).group(1)), p.stat().st_mtime, p)
        for p in entries
        if FILES_DB_RE.match(p.name)
    ]
    plugins_candidates = [(p.stat().st_mtime, p) for p in entries if PLUGINS_DB_RE.match(p.name)]
    files_db = max(files_candidates)[2]  # highest schema number, mtime tie-break
    plugins_db = max(plugins_candidates)[1]  # newest mtime
    print(f"\nSelected files DB:   {files_db.name}")
    print(f"Selected plugins DB: {plugins_db.name}")
    return files_db, plugins_db


@section("2. Read-only + immutable open; write must fail; URI shown")
def probe_ro_open(files_db: Path):
    uri = to_uri(files_db, immutable=True)
    print(f"URI: {uri}")
    conn = open_ro(files_db)
    (n,) = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    print(f"Opened OK, sqlite_master rows: {n}")
    try:
        conn.execute("CREATE TABLE _probe_should_fail (x)")
        print("!! WRITE SUCCEEDED — mode=ro NOT effective !!")
    except sqlite3.OperationalError as e:
        print(f"Write correctly refused: {e}")
    conn.close()


@section("3. Schema dump vs roadmap facts")
def probe_schema(files_db: Path):
    conn = open_ro(files_db)
    tables = [
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]
    print(f"Tables ({len(tables)}): {', '.join(tables)}")
    for table in tables:
        # search_aggregation* are FTS tables on a custom AbletonTokenizer we
        # don't have — any query against them raises. Confirmed run 1.
        if table.startswith("search_aggregation"):
            print(f"\n  {table}: SKIPPED (FTS, AbletonTokenizer unavailable)")
            continue
        try:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            (count,) = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        except sqlite3.OperationalError as e:
            print(f"\n  {table}: UNQUERYABLE ({e})")
            continue
        print(f"\n  {table} ({count} rows):")
        for _cid, name, ctype, _notnull, _default, _pk in cols:
            print(f"    {name:24s} {ctype}")
    conn.close()
    return tables


@section("4. Path reconstruction via recursive CTE, verified on disk")
def probe_paths(files_db: Path):
    conn = open_ro(files_db)
    # Roadmap fact: full paths reconstruct by walking parent_id upward; a root
    # named like "X:" is a Windows drive.
    cte = """
    WITH RECURSIVE chain(file_id, name, parent_id, depth) AS (
        SELECT file_id, name, parent_id, 0 FROM files WHERE file_id = ?
        UNION ALL
        SELECT f.file_id, f.name, f.parent_id, chain.depth + 1
        FROM files f JOIN chain ON f.file_id = chain.parent_id
    )
    SELECT name FROM chain ORDER BY depth DESC
    """
    rows = conn.execute(
        "SELECT file_id, name FROM files WHERE name LIKE '%.wav' AND use_count > 0 LIMIT 5"
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT file_id, name FROM files WHERE name LIKE '%.wav' LIMIT 5"
        ).fetchall()
    ok = 0
    for file_id, _name in rows:
        parts = [r[0] for r in conn.execute(cte, (file_id,))]
        path = join_parts(parts)
        exists = os.path.exists(path)
        ok += exists
        print(f"  [{'OK ' if exists else 'MISS'}] {path}")
    print(f"\n{ok}/{len(rows)} reconstructed paths exist on disk")
    conn.close()


def fourcc(value: int) -> str:
    """Render an integer as a four-char code both ways (run 1: file_type is
    FourCC-style, e.g. 2002875949)."""
    try:
        le = struct.pack("<I", value).decode("ascii", errors="replace")
        be = struct.pack(">I", value).decode("ascii", errors="replace")
        return f"le={le!r} be={be!r}"
    except struct.error:
        return "(not uint32)"


@section("5. Value distributions (tool enums get written FROM this)")
def probe_distributions(files_db: Path):
    conn = open_ro(files_db)
    for table, col in [
        ("places", "folder_kind"),
        ("files", "file_type"),
        ("files", "subtype"),
        ("files", "device_type"),
        ("files", "file_kind"),
    ]:
        print(f"\n  {table}.{col}:")
        for value, count in conn.execute(
            f"SELECT {col}, count(*) FROM {table} GROUP BY {col} ORDER BY count(*) DESC"
        ):
            decoded = (
                f"  {fourcc(value)}"
                if table == "files" and col in ("file_type", "subtype") and value
                else ""
            )
            print(f"    {value!r:30} {count:>8,}{decoded}")
    # For each file_type, show two sample names — the FourCC alone may not
    # be self-explanatory.
    print("\n  file_type -> sample names:")
    for (value,) in conn.execute("SELECT DISTINCT file_type FROM files"):
        names = [
            r[0] for r in conn.execute("SELECT name FROM files WHERE file_type=? LIMIT 2", (value,))
        ]
        print(f"    {fourcc(value)}: {names}")
    # What do places actually look like?
    print("\n  places sample (all columns):")
    cols = [c[1] for c in conn.execute("PRAGMA table_info(places)")]
    print(f"    columns: {cols}")
    for row in conn.execute("SELECT * FROM places LIMIT 12"):
        print(f"    {row}")
    # keywords: roadmap says it joins file_id to tag rows living in files.
    # Run 1 showed keyw_id values (~9090+) inside the files id range — resolve
    # them against files.name to confirm the claim.
    print("\n  keywords resolved (keyw_id -> files.name?):")
    for file_id, keyw_id, is_auto in conn.execute("SELECT * FROM keywords LIMIT 10"):
        fname = conn.execute("SELECT name FROM files WHERE file_id=?", (file_id,)).fetchone()
        kname = conn.execute("SELECT name FROM files WHERE file_id=?", (keyw_id,)).fetchone()
        print(f"    file {fname and fname[0]!r} tagged {kname and kname[0]!r} (auto={is_auto})")
    print("\n  most-used keywords:")
    for kname, count in conn.execute(
        "SELECT kf.name, count(*) FROM keywords k JOIN files kf ON kf.file_id = k.keyw_id "
        "GROUP BY k.keyw_id ORDER BY count(*) DESC LIMIT 15"
    ):
        print(f"    {kname!r:36} {count:>6,}")
    # metadata/metadata_values: 42k rows in run 1 — what lives there?
    print("\n  metadata_values sample:")
    for row in conn.execute("SELECT * FROM metadata_values LIMIT 15"):
        print(f"    {row}")
    print("\n  metadata keys distribution:")
    for key, count in conn.execute(
        "SELECT key, count(*) FROM metadata GROUP BY key ORDER BY count(*) DESC LIMIT 10"
    ):
        print(f"    key={key}: {count:,}")
    conn.close()


@section("6. fe_values blob decode + cosine ranking")
def probe_features(files_db: Path, ref_query: str):
    conn = open_ro(files_db)
    print("  blob length distribution:")
    for length, count in conn.execute(
        "SELECT length(data), count(*) FROM fe_values GROUP BY length(data) ORDER BY count(*) DESC LIMIT 5"
    ):
        print(f"    {length} bytes: {count:,} rows")

    # Run 1: fe_values columns are (file_id, data, hash) — the join key is
    # file_id, NOT hash as the roadmap recorded. hash stays an opaque value.
    row = conn.execute(
        "SELECT f.file_id, f.name, v.data FROM files f "
        "JOIN fe_values v ON v.file_id = f.file_id "
        "WHERE f.name LIKE ? LIMIT 1",
        (f"%{ref_query}%",),
    ).fetchone()
    if row is None:
        print(f"  no analyzed file matching {ref_query!r}")
        return None
    file_id, name, blob = row
    version, count, reserved = struct.unpack_from("<III", blob)
    print(f"\n  reference: {name!r} (file_id {file_id})")
    print(f"  header: version={version} count={count} reserved={reserved} bloblen={len(blob)}")
    vec = struct.unpack_from(f"<{count}f", blob, 12)
    print(f"  first 8 floats: {[round(x, 4) for x in vec[:8]]}")
    conn.close()
    return file_id, name, vec


@section("7. Timed full cosine scan (pure Python feasibility)")
def probe_scan(files_db: Path, ref):
    import math

    file_id, ref_name, ref_vec = ref
    conn = open_ro(files_db)
    t0 = time.perf_counter()
    rows = conn.execute(
        "SELECT f.file_id, f.name, v.data FROM files f JOIN fe_values v ON v.file_id = f.file_id"
    ).fetchall()
    t1 = time.perf_counter()
    ref_norm = math.sqrt(sum(x * x for x in ref_vec))
    scored = []
    for fid, name, blob in rows:
        if len(blob) < 12:
            continue
        version, count, _ = struct.unpack_from("<III", blob)
        if version != 18 or count != 64 or len(blob) < 12 + 4 * count:
            continue
        vec = struct.unpack_from("<64f", blob, 12)
        dot = sum(a * b for a, b in zip(ref_vec, vec, strict=False))
        norm = math.sqrt(sum(x * x for x in vec))
        if norm and ref_norm:
            scored.append((dot / (ref_norm * norm), name, fid))
    t2 = time.perf_counter()
    scored.sort(reverse=True)
    print(f"  fetch: {t1 - t0:.3f}s, score {len(scored):,} vectors: {t2 - t1:.3f}s")
    print(f"\n  top 10 similar to {ref_name!r} (eyeball: are these plausible?):")
    for sim, name, _fid in scored[:10]:
        print(f"    {sim:.4f}  {name}")
    conn.close()


@section("8. WAL / staleness state")
def probe_wal(files_db: Path):
    wal = files_db.with_name(files_db.name + "-wal")
    db_m = files_db.stat().st_mtime
    if wal.exists():
        wal_m = wal.stat().st_mtime
        print(f"  db mtime:  {time.ctime(db_m)}")
        print(f"  wal mtime: {time.ctime(wal_m)}  size {wal.stat().st_size:,}")
        print(f"  wal newer than db: {wal_m > db_m}  (True + nonzero size = stale snapshot note)")
    else:
        print("  no -wal file (checkpointed / Live closed)")


@section("9. Plugins DB: schema + dev_identifier parse")
def probe_plugins(plugins_db: Path):
    conn = open_ro(plugins_db)
    tables = [
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]
    print(f"  tables: {tables}")
    for table in tables:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
        (count,) = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        print(f"    {table} ({count} rows): {cols}")
    print("\n  plugins rows:")
    cols = [c[1] for c in conn.execute("PRAGMA table_info(plugins)")]
    for row in conn.execute("SELECT * FROM plugins"):
        print(f"    {dict(zip(cols, row, strict=False))}")
    conn.close()


@section("10. Browser-path seam worksheet (disk path vs browse path guess)")
def probe_seam(files_db: Path):
    conn = open_ro(files_db)
    # Print a few user-library and pack files with reconstructed paths; the
    # live round trip decides which folder kinds get browser guesses at all.
    cte = """
    WITH RECURSIVE chain(file_id, name, parent_id, depth) AS (
        SELECT file_id, name, parent_id, 0 FROM files WHERE file_id = ?
        UNION ALL
        SELECT f.file_id, f.name, f.parent_id, chain.depth + 1
        FROM files f JOIN chain ON f.file_id = chain.parent_id
    )
    SELECT name FROM chain ORDER BY depth DESC
    """
    # Run 1: places has NO place_id column — it keys on file_id (each place IS
    # a files row; files.place_id points at that row).
    rows = conn.execute(
        "SELECT f.file_id, f.name, p.name, p.folder_kind "
        "FROM files f JOIN places p ON p.file_id = f.place_id "
        "WHERE f.use_count > 0 ORDER BY f.use_count DESC LIMIT 15"
    ).fetchall()
    for file_id, _name, place_name, folder_kind in rows:
        parts = [r[0] for r in conn.execute(cte, (file_id,))]
        print(f"  place={place_name!r:18} kind={folder_kind} {join_parts(parts)}")
    conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-dir", type=Path, default=default_db_dir())
    ap.add_argument(
        "--ref-query", default="808", help="substring for the similarity reference file"
    )
    args = ap.parse_args()

    print(f"DB dir: {args.db_dir}")
    if not args.db_dir.is_dir():
        print("FAIL: directory does not exist (has Live run on this machine?)")
        sys.exit(1)

    picked = probe_selection(args.db_dir)
    if picked is None:
        sys.exit(1)
    files_db, plugins_db = picked
    probe_ro_open(files_db)
    probe_schema(files_db)
    probe_paths(files_db)
    probe_distributions(files_db)
    ref = probe_features(files_db, args.ref_query)
    if ref is not None:
        probe_scan(files_db, ref)
    probe_wal(files_db)
    probe_plugins(plugins_db)
    probe_seam(files_db)
    print("\nDone. Every fact library.py relies on must be visible above.")


if __name__ == "__main__":
    main()
