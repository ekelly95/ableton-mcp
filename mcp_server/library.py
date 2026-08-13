"""Read-only intelligence over Live's own library databases.

Live maintains SQLite databases of the entire library (files, tags, Live's own
64-float audio feature vector per analyzed file, installed plug-ins). This
module searches them server-side — Live is never involved, so these tools work
even while Live is closed, and nothing here touches the registry/schema hash.

Every schema fact below was verified with scripts/probe_library_db.py on the
live databases (Live 12.4.3 Trial, 2026-08-12) — the roadmap's recorded facts
had three corrections: fe_values joins on file_id (not hash), places keys on
file_id (there is no place_id column in places), and file_type/subtype are
big-endian FourCC integers ('wav-', 'adv-', ...). The search_aggregation*
tables are FTS on a custom AbletonTokenizer unavailable outside Live — any
query touching them raises, so search uses plain LIKE.

Databases are opened per call with mode=ro&immutable=1: safe while Live runs,
reading the last checkpointed snapshot. A -wal file newer than the .db means
uncheckpointed writes we cannot see — surfaced as a staleness note, never an
error.
"""

import math
import os
import re
import sqlite3
import struct
from pathlib import Path
from urllib.parse import quote

_FILES_DB_RE = re.compile(r"^Live-files-(\d+)\.db$")
_PLUGINS_DB_RE = re.compile(r"^Live-plugins-\d+\.db$")

# places.folder_kind -> source name (probed: exactly these six rows exist).
_SOURCES = {
    0: "core_library",
    1: "user_library",
    4: "current_project",
    8: "builtin",
    9: "cloud",
    10: "plugins",
}

_PLUGINS_FOLDER_KIND = 10


def _fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode("ascii"))[0]


# kind filter -> files.file_type whitelist (probed FourCC values). Folders,
# keyword rows, prefs, packs, tunings etc. are never searchable; plug-ins are
# answered from the plugins DB (their files-DB paths are virtual '<plugins>/'
# entries that don't match the real browser tree — probed: an extra 'Custom'
# level the browser doesn't show).
_KIND_TYPES = {
    "sample": tuple(_fourcc(c) for c in ("wav-", "aiff", "oggv")),
    "preset": tuple(_fourcc(c) for c in ("adv-", "adg-", "amp-")),
    "clip": (_fourcc("alc-"),),
    "midi": (_fourcc("midi"),),
    "set": (_fourcc("als-"),),
    "groove": (_fourcc("agr-"),),
}
_TYPE_TO_KIND = {t: kind for kind, types_ in _KIND_TYPES.items() for t in types_}

KINDS = (*_KIND_TYPES, "plugin", "any")
SOURCE_FILTERS = ("user_library", "core_library", "builtin", "current_project", "cloud", "any")

_SEARCH_LIMIT_DEFAULT, _SEARCH_LIMIT_MAX = 25, 100
_SIMILAR_LIMIT_DEFAULT, _SIMILAR_LIMIT_MAX = 10, 50

_FE_VERSION, _FE_COUNT = 18, 64


class LibraryError(RuntimeError):
    """The library database is missing or its schema doesn't match."""


def database_dir() -> Path:
    override = os.environ.get("ABLETON_MCP_LIVE_DB_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "Ableton" / "Live Database"
    return Path.home() / "Library" / "Application Support" / "Ableton" / "Live Database"


def _missing_dir_error(directory: Path) -> LibraryError:
    return LibraryError(
        f"Live database directory not found: {directory}. Has Live run at least "
        f"once on this machine? (Windows: %LOCALAPPDATA%\\Ableton\\Live Database, "
        f"macOS: ~/Library/Application Support/Ableton/Live Database; override "
        f"with ABLETON_MCP_LIVE_DB_DIR.)"
    )


def find_files_db(directory: Path) -> Path:
    # Highest schema number in the filename wins, mtime as tie-break.
    candidates = [
        (int(m.group(1)), p.stat().st_mtime, p)
        for p in directory.iterdir()
        if (m := _FILES_DB_RE.match(p.name))
    ]
    if not candidates:
        raise LibraryError(f"No Live-files-*.db in {directory}")
    return max(candidates)[2]


def find_plugins_db(directory: Path) -> Path | None:
    # Newest mtime wins — the number in the filename is not monotonic.
    candidates = [p for p in directory.iterdir() if _PLUGINS_DB_RE.match(p.name)]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def open_readonly(path: Path) -> sqlite3.Connection:
    # Backslashes and unquoted %/space/# break sqlite URIs — probed quoting
    # works on the real path (spaces in 'Live Database').
    quoted = quote(str(path).replace("\\", "/"), safe="/:")
    return sqlite3.connect(f"file:{quoted}?mode=ro&immutable=1", uri=True)


def staleness_note(db_path: Path) -> str | None:
    wal = db_path.with_name(db_path.name + "-wal")
    try:
        if (
            wal.exists()
            and wal.stat().st_size > 0
            and wal.stat().st_mtime > db_path.stat().st_mtime
        ):
            return (
                "Live has unsaved database changes; results reflect the last "
                "snapshot and may miss very recent files"
            )
    except OSError:
        pass
    return None


def feature_vector(blob: bytes) -> tuple[float, ...] | None:
    """Live's per-file audio feature vector; None for any layout we don't know."""
    if not isinstance(blob, bytes) or len(blob) < 12:
        return None
    version, count, _reserved = struct.unpack_from("<III", blob)
    if version != _FE_VERSION or count != _FE_COUNT or len(blob) < 12 + 4 * count:
        return None
    return struct.unpack_from(f"<{count}f", blob, 12)


def cosine_similarity(a, b) -> float:
    # feature_vector() guarantees both are 64 floats — a mismatch is a bug.
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


# Root-first name chain for one file. The Windows drive root row is literally
# 'C:\' (probed) — parts are cleaned of trailing separators before joining.
_CHAIN_SQL = """
WITH RECURSIVE chain(file_id, name, parent_id, depth) AS (
    SELECT file_id, name, parent_id, 0 FROM files WHERE file_id = ?
    UNION ALL
    SELECT f.file_id, f.name, f.parent_id, chain.depth + 1
    FROM files f JOIN chain ON f.file_id = chain.parent_id
)
SELECT file_id, name FROM chain ORDER BY depth DESC
"""


def _resolve_path(conn: sqlite3.Connection, file_id: int, place_root_id: int, source: str):
    """Absolute path + browser-path guess for one files row.

    The guess only exists for user_library items: their path relative to the
    User Library root mirrors the browser tree ('user_library' root confirmed
    in real-Live use). Core Library items appear in the browser by category
    (sounds/drums/...), not by disk layout — no confident mapping, so no guess.
    """
    chain = conn.execute(_CHAIN_SQL, (file_id,)).fetchall()
    parts = [name.rstrip("\\/") for _, name in chain]
    # A Unix root row ('/' or '') cleans to empty — dropping it would make an
    # absolute /Users/... path relative. Windows drive roots ('C:\' -> 'C:')
    # take the plain-join branch. Mirrors join_parts in
    # scripts/probe_library_db.py, which stays deliberately separate (the
    # probe is the independent measurement oracle) — fix both or neither.
    if parts and not parts[0]:
        path = "/" + "/".join(p for p in parts[1:] if p)
    else:
        path = "/".join(p for p in parts if p)
    guess = None
    if source == "user_library":
        root_pos = next((i for i, (fid, _) in enumerate(chain) if fid == place_root_id), None)
        if root_pos is not None and root_pos < len(chain) - 1:
            guess = ["user_library", *(name for _, name in chain[root_pos + 1 :])]
    return path, guess


def _tags_for(conn: sqlite3.Connection, file_id: int) -> list[str]:
    # Tag rows ARE files rows (file_type 'keyw'); keywords is the join table.
    return [
        row[0]
        for row in conn.execute(
            "SELECT kf.name FROM keywords k JOIN files kf ON kf.file_id = k.keyw_id "
            "WHERE k.file_id = ? ORDER BY kf.name",
            (file_id,),
        )
    ]


def _int_arg(arguments: dict, key: str, default: int, maximum: int) -> int:
    try:
        value = int(arguments.get(key, default))
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be an integer") from None
    if not 1 <= value <= maximum:
        raise ValueError(f"'{key}' must be between 1 and {maximum}")
    return value


def _wrap_schema_errors(db: Path, err: sqlite3.Error) -> LibraryError:
    return LibraryError(
        f"Live database schema mismatch in {db.name}: {err}. Live may have "
        f"changed its library format — re-verify with scripts/probe_library_db.py."
    )


def _search_files(conn, query, tag_list, kind, source, limit):
    types_ = _KIND_TYPES[kind] if kind in _KIND_TYPES else tuple(_TYPE_TO_KIND)
    sql = [
        "SELECT f.file_id, f.name, f.file_type, f.use_count, f.place_id, p.folder_kind",
        "FROM files f JOIN places p ON p.file_id = f.place_id",
        f"WHERE f.file_type IN ({','.join('?' * len(types_))})",
        "AND p.folder_kind != ?",
    ]
    params: list = [*types_, _PLUGINS_FOLDER_KIND]
    if query:
        sql.append("AND f.name LIKE ? ESCAPE '\\'")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    if source != "any":
        kind_num = next(k for k, v in _SOURCES.items() if v == source)
        sql.append("AND p.folder_kind = ?")
        params.append(kind_num)
    for tag in tag_list:
        sql.append(
            "AND EXISTS (SELECT 1 FROM keywords k JOIN files kf ON kf.file_id = k.keyw_id "
            "WHERE k.file_id = f.file_id AND kf.name = ? COLLATE NOCASE)"
        )
        params.append(tag)
    sql.append("ORDER BY f.use_count DESC, f.name LIMIT ?")
    params.append(limit + 1)

    rows = conn.execute(" ".join(sql), params).fetchall()
    truncated = len(rows) > limit
    matches = []
    for file_id, name, file_type, use_count, place_id, folder_kind in rows[:limit]:
        source_name = _SOURCES.get(folder_kind, f"unknown_{folder_kind}")
        path, guess = _resolve_path(conn, file_id, place_id, source_name)
        match = {
            "name": name,
            "path": path,
            "kind": _TYPE_TO_KIND[file_type],
            "source": source_name,
        }
        if guess:
            match["browser_path_guess"] = guess
        tags = _tags_for(conn, file_id)
        if tags:
            match["tags"] = tags
        if use_count:
            match["use_count"] = use_count
        matches.append(match)
    return matches, truncated


def _plugin_format(dev_identifier: str) -> str:
    # Probed shapes: 'device:vst:instr:1280852818?n=Purity_x64',
    # 'device:vst3:instr:<uuid>'.
    parts = dev_identifier.split(":")
    return {"vst": "VST", "vst3": "VST3"}.get(parts[1] if len(parts) > 2 else "", "unknown")


def _search_plugins(conn, query, tag_list, limit):
    matches = []
    rows = conn.execute(
        "SELECT dev_identifier, name, vendor, subcategories FROM plugins "
        "WHERE enabled = 1 ORDER BY name"
    ).fetchall()
    for dev_identifier, name, vendor, subcategories in rows:
        tags = [s for s in (subcategories or "").split("|") if s]
        if query and query.lower() not in f"{name} {vendor}".lower():
            continue
        if any(t.lower() not in (x.lower() for x in tags) for t in tag_list):
            continue
        fmt = _plugin_format(dev_identifier)
        match = {
            "name": name,
            "vendor": vendor,
            "kind": "plugin",
            "source": "plugins",
            "browser_path_guess": ["plugins", fmt, vendor, name],
        }
        if tags:
            match["tags"] = tags
        matches.append(match)
    return matches[:limit], len(matches) > limit


def search_library(arguments: dict) -> dict:
    """Handler for the search_library tool (server-only, never touches Live)."""
    query = (arguments.get("query") or "").strip()
    tag_list = [t.strip() for t in (arguments.get("tags") or "").split(",") if t.strip()]
    kind = arguments.get("kind", "any")
    source = arguments.get("source", "any")
    limit = _int_arg(arguments, "limit", _SEARCH_LIMIT_DEFAULT, _SEARCH_LIMIT_MAX)
    if kind not in KINDS:
        raise ValueError(f"'kind' must be one of {', '.join(KINDS)}")
    if source not in SOURCE_FILTERS:
        raise ValueError(f"'source' must be one of {', '.join(SOURCE_FILTERS)}")
    if not query and not tag_list and kind == "any" and source == "any":
        raise ValueError("Give at least one filter: query, tags, kind, or source")

    directory = database_dir()
    if not directory.is_dir():
        raise _missing_dir_error(directory)
    files_db = find_files_db(directory)

    matches: list[dict] = []
    truncated = False
    if kind != "plugin":
        conn = open_readonly(files_db)
        try:
            matches, truncated = _search_files(conn, query, tag_list, kind, source, limit)
        except sqlite3.Error as e:
            raise _wrap_schema_errors(files_db, e) from e
        finally:
            conn.close()

    if kind in ("plugin", "any") and source in ("any",) and len(matches) < limit:
        plugins_db = find_plugins_db(directory)
        if plugins_db is not None:
            conn = open_readonly(plugins_db)
            try:
                plugin_matches, plugin_truncated = _search_plugins(
                    conn, query, tag_list, limit - len(matches)
                )
            except sqlite3.Error as e:
                raise _wrap_schema_errors(plugins_db, e) from e
            finally:
                conn.close()
            matches.extend(plugin_matches)
            truncated = truncated or plugin_truncated
        elif kind == "plugin":
            raise LibraryError(f"No Live-plugins-*.db in {directory}")

    result: dict = {"matches": matches}
    if truncated:
        result["truncated"] = True
    note = staleness_note(files_db)
    if note:
        result["staleness"] = note
    return result


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


def _resolve_reference(conn, path_arg: str | None, query_arg: str | None):
    """The reference must have an fe_values row — that IS the reference sound."""
    if path_arg:
        basename = _norm_path(path_arg).rsplit("/", 1)[-1]
        rows = conn.execute(
            "SELECT f.file_id, f.name, f.place_id, p.folder_kind, v.data "
            "FROM files f JOIN places p ON p.file_id = f.place_id "
            "LEFT JOIN fe_values v ON v.file_id = f.file_id "
            "WHERE f.name = ? COLLATE NOCASE",
            (basename,),
        ).fetchall()
        for file_id, name, place_id, folder_kind, blob in rows:
            source = _SOURCES.get(folder_kind, "")
            full_path, _ = _resolve_path(conn, file_id, place_id, source)
            if _norm_path(full_path) == _norm_path(path_arg):
                return file_id, name, full_path, blob
        raise ValueError(f"No library file with path {path_arg!r} (is it indexed by Live?)")
    row = conn.execute(
        "SELECT f.file_id, f.name, f.place_id, p.folder_kind, v.data "
        "FROM files f JOIN places p ON p.file_id = f.place_id "
        "JOIN fe_values v ON v.file_id = f.file_id "
        "WHERE f.name LIKE ? ORDER BY f.use_count DESC, f.name LIMIT 1",
        (f"%{query_arg}%",),
    ).fetchone()
    if row is None:
        raise ValueError(f"No analyzed library file matches {query_arg!r}")
    file_id, name, place_id, folder_kind, blob = row
    source = _SOURCES.get(folder_kind, "")
    full_path, _ = _resolve_path(conn, file_id, place_id, source)
    return file_id, name, full_path, blob


def find_similar(arguments: dict) -> dict:
    """Handler for the find_similar tool: rank by Live's own audio features."""
    path_arg = (arguments.get("path") or "").strip() or None
    query_arg = (arguments.get("query") or "").strip() or None
    if bool(path_arg) == bool(query_arg):
        raise ValueError("Give exactly one of 'path' or 'query'")
    limit = _int_arg(arguments, "limit", _SIMILAR_LIMIT_DEFAULT, _SIMILAR_LIMIT_MAX)

    directory = database_dir()
    if not directory.is_dir():
        raise _missing_dir_error(directory)
    files_db = find_files_db(directory)
    conn = open_readonly(files_db)
    try:
        ref_id, ref_name, ref_path, ref_blob = _resolve_reference(conn, path_arg, query_arg)
        result: dict = {"reference": {"name": ref_name, "path": ref_path}}
        ref_vec = feature_vector(ref_blob) if ref_blob is not None else None
        if ref_vec is None:
            result["matches"] = []
            result["note"] = (
                "Live has not analyzed this file (no feature vector) — no "
                "similarity ranking possible"
            )
            return result

        scored = []
        skipped = 0
        for file_id, name, place_id, folder_kind, use_count, blob in conn.execute(
            "SELECT f.file_id, f.name, f.place_id, p.folder_kind, f.use_count, v.data "
            "FROM files f JOIN places p ON p.file_id = f.place_id "
            "JOIN fe_values v ON v.file_id = f.file_id WHERE f.file_id != ?",
            (ref_id,),
        ):
            vec = feature_vector(blob)
            if vec is None:
                skipped += 1
                continue
            scored.append(
                (cosine_similarity(ref_vec, vec), file_id, name, place_id, folder_kind, use_count)
            )
        scored.sort(key=lambda item: (-item[0], item[2]))

        matches = []
        for similarity, file_id, name, place_id, folder_kind, use_count in scored[:limit]:
            source = _SOURCES.get(folder_kind, f"unknown_{folder_kind}")
            path, guess = _resolve_path(conn, file_id, place_id, source)
            match = {"name": name, "path": path, "similarity": round(similarity, 3)}
            if guess:
                match["browser_path_guess"] = guess
            if use_count:
                match["use_count"] = use_count
            matches.append(match)
        result["matches"] = matches
        if skipped:
            result["note"] = f"{skipped} analyzed files skipped (unknown feature format)"
    except sqlite3.Error as e:
        raise _wrap_schema_errors(files_db, e) from e
    finally:
        conn.close()

    note = staleness_note(files_db)
    if note:
        result["staleness"] = note
    return result
