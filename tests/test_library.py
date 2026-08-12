"""Library intelligence: search/similarity over fixture copies of Live's DBs.

The fixture schema is pinned to PROBED reality — scripts/probe_library_db.py
against Live-files-12300.db / Live-plugins-1.db on Live 12.4.3 Trial
(2026-08-12). Corrections to the roadmap's recorded facts, encoded here:
fe_values joins on file_id (not hash); places keys on file_id (no place_id
column); file_type/subtype are big-endian FourCC integers. If a future Live
changes this layout, these tests keep passing — the schema-mismatch guard in
library.py and a probe re-run are the upgrade path.
"""

import json
import os
import sqlite3
import struct
import time
from pathlib import Path

import pytest

from mcp_server.library import (
    LibraryError,
    cosine_similarity,
    database_dir,
    feature_vector,
    find_files_db,
    find_plugins_db,
    find_similar,
    open_readonly,
    search_library,
)

FOURCC = {
    code: struct.unpack(">I", code.encode())[0]
    for code in ("wav-", "aiff", "adv-", "adg-", "alc-", "midi", "als-", "agr-", "fldr", "keyw")
}


def _blob(vec, version=18, count=64):
    return struct.pack(f"<III{len(vec)}f", version, count, 0, *vec)


# Distinct directions so cosine ordering is predictable: KICK_VEC is closest
# to SUB_VEC, orthogonal-ish to SNARE_VEC.
KICK_VEC = [1.0] * 32 + [0.0] * 32
SUB_VEC = [1.0] * 30 + [0.5] * 2 + [0.0] * 32
SNARE_VEC = [0.0] * 32 + [1.0] * 32


def _build_files_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    # Probed column sets (unused-by-us columns carry defaults so the fixture
    # still fails loudly if the module starts selecting something new).
    conn.executescript(
        """
        CREATE TABLE files (
            file_id INTEGER PRIMARY KEY, parent_id INTEGER, file_type INTEGER,
            subtype INTEGER DEFAULT 0, file_kind INTEGER DEFAULT 0,
            mod_date INTEGER DEFAULT 0, file_size INTEGER DEFAULT 0,
            aggr_id INTEGER DEFAULT 0, name TEXT, colors INTEGER DEFAULT 0,
            md_version INTEGER DEFAULT 0, scanner_version INTEGER DEFAULT 0,
            use_count INTEGER DEFAULT 0, place_id INTEGER DEFAULT 0,
            flags INTEGER DEFAULT 0, device_type INTEGER DEFAULT 0,
            device_arch INTEGER DEFAULT 0, device_id TEXT DEFAULT '',
            edit_source TEXT DEFAULT '', edit_date INTEGER DEFAULT 0,
            fe_version INTEGER DEFAULT 0
        );
        CREATE TABLE places (file_id INTEGER, folder_kind INTEGER, level INTEGER, name TEXT);
        CREATE TABLE keywords (file_id INTEGER, keyw_id INTEGER, is_auto BOOL);
        CREATE TABLE fe_values (file_id INTEGER, data BLOB, hash INTEGER);
        """
    )

    def add_file(file_id, parent_id, file_type, name, use_count=0, place_id=0):
        conn.execute(
            "INSERT INTO files (file_id, parent_id, file_type, name, use_count, place_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (file_id, parent_id, file_type, name, use_count, place_id),
        )

    # Folder chains. The Windows drive root row is literally 'C:\' (probed).
    add_file(1, 0, FOURCC["fldr"], "C:\\")
    add_file(2, 1, FOURCC["fldr"], "Music")
    add_file(10, 2, FOURCC["fldr"], "User Library")  # place root, kind 1
    add_file(11, 10, FOURCC["fldr"], "Drum Kits")
    add_file(12, 11, FOURCC["fldr"], "Kit A")
    add_file(20, 2, FOURCC["fldr"], "Core Library")  # place root, kind 0
    add_file(21, 20, FOURCC["fldr"], "Devices")
    conn.executemany(
        "INSERT INTO places (file_id, folder_kind, level, name) VALUES (?, ?, ?, ?)",
        [(20, 0, 0, "Core Library"), (10, 1, 0, "User Library")],
    )

    # Keyword rows are files rows of type 'keyw' (probed).
    add_file(90, 20, FOURCC["keyw"], "One Shot")
    add_file(91, 20, FOURCC["keyw"], "Punchy")

    # User-library samples.
    add_file(100, 12, FOURCC["wav-"], "808 Kick.wav", use_count=5, place_id=10)
    add_file(101, 12, FOURCC["wav-"], "808 Sub.wav", use_count=0, place_id=10)
    add_file(102, 12, FOURCC["wav-"], "Snare Tight.wav", use_count=2, place_id=10)
    add_file(103, 12, FOURCC["wav-"], "100% Wet_Clap.wav", use_count=0, place_id=10)
    # Core-library preset + other kinds.
    add_file(110, 21, FOURCC["adv-"], "Impulse 808.adv", use_count=3, place_id=20)
    add_file(111, 21, FOURCC["adg-"], "808 Core Kit.adg", use_count=1, place_id=20)
    add_file(112, 21, FOURCC["agr-"], "Swing 16.agr", place_id=20)
    add_file(113, 21, FOURCC["midi"], "Groove.mid", place_id=20)
    add_file(114, 21, FOURCC["alc-"], "Chord.alc", place_id=20)
    add_file(115, 21, FOURCC["als-"], "Template.als", place_id=20)
    # An analyzed file whose blob has an unknown version (warn-and-skip).
    add_file(116, 21, FOURCC["adv-"], "Weird Blob.adv", place_id=20)
    # A user-library file with NO feature vector (find_similar note case).
    add_file(117, 12, FOURCC["wav-"], "Unanalyzed.wav", place_id=10)

    conn.executemany(
        "INSERT INTO keywords (file_id, keyw_id, is_auto) VALUES (?, ?, 0)",
        [(100, 90), (100, 91), (102, 90)],
    )
    conn.executemany(
        "INSERT INTO fe_values (file_id, data, hash) VALUES (?, ?, 0)",
        [
            (100, _blob(KICK_VEC)),
            (101, _blob(SUB_VEC)),
            (102, _blob(SNARE_VEC)),
            (110, _blob(KICK_VEC)),
            (116, _blob(KICK_VEC, version=99)),
        ],
    )
    conn.commit()
    conn.close()


def _build_plugins_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE plugins (plugin_id INTEGER PRIMARY KEY, module_id INTEGER, "
        "dev_identifier TEXT, name TEXT, vendor TEXT, version TEXT, sdk_version TEXT, "
        "flags INTEGER, scanstate INTEGER, subcategories TEXT, enabled INTEGER)"
    )
    conn.executemany(
        "INSERT INTO plugins (dev_identifier, name, vendor, subcategories, enabled) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            # Probed shapes: VST2 numeric id + ?n=, VST3 uuid.
            ("device:vst:instr:1280852818?n=Purity_x64", "Purity_x64", "SonicCat", "", 1),
            (
                "device:vst3:instr:84e8de5f-9255-2222-96fa-e4133c935a18",
                "Omnisphere",
                "Spectrasonics",
                "Instrument|Synth",
                1,
            ),
            ("device:vst3:audiofx:feedbeef", "Broken Verb", "Gone Inc", "Fx", 0),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def db_dir(tmp_path, monkeypatch):
    _build_files_db(tmp_path / "Live-files-12300.db")
    _build_plugins_db(tmp_path / "Live-plugins-1.db")
    monkeypatch.setenv("ABLETON_MCP_LIVE_DB_DIR", str(tmp_path))
    return tmp_path


class TestDatabaseSelection:
    def test_env_override_wins(self, db_dir):
        assert database_dir() == db_dir

    def test_files_db_highest_schema_number_wins(self, tmp_path):
        old = tmp_path / "Live-files-999.db"
        new = tmp_path / "Live-files-12300.db"
        old.touch()
        new.touch()
        # Make the LOWER-numbered file newer: number must still win over mtime.
        now = time.time()
        os.utime(old, (now, now))
        os.utime(new, (now - 3600, now - 3600))
        assert find_files_db(tmp_path) == new

    def test_plugins_db_newest_mtime_wins(self, tmp_path):
        # Version numbers in plugin DB filenames are NOT monotonic.
        a = tmp_path / "Live-plugins-5.db"
        b = tmp_path / "Live-plugins-1.db"
        a.touch()
        b.touch()
        now = time.time()
        os.utime(a, (now - 3600, now - 3600))
        os.utime(b, (now, now))
        assert find_plugins_db(tmp_path) == b

    def test_missing_dir_is_a_helpful_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ABLETON_MCP_LIVE_DB_DIR", str(tmp_path / "nope"))
        with pytest.raises(LibraryError, match="Has Live run"):
            search_library({"query": "808"})

    def test_open_readonly_rejects_writes_and_hostile_paths(self, tmp_path):
        # Space and % in the directory name exercise the URI quoting.
        weird = tmp_path / "we ird%dir"
        weird.mkdir()
        db = weird / "Live-files-1.db"
        _build_files_db(db)
        conn = open_readonly(db)
        try:
            assert conn.execute("SELECT count(*) FROM files").fetchone()[0] > 0
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("CREATE TABLE nope (x)")
        finally:
            conn.close()


class TestSearchLibrary:
    def test_query_reconstructs_path_and_guess(self, db_dir):
        result = search_library({"query": "808 Kick"})
        (match,) = result["matches"]
        assert match["name"] == "808 Kick.wav"
        assert match["path"] == "C:/Music/User Library/Drum Kits/Kit A/808 Kick.wav"
        assert match["kind"] == "sample"
        assert match["source"] == "user_library"
        assert match["browser_path_guess"] == [
            "user_library",
            "Drum Kits",
            "Kit A",
            "808 Kick.wav",
        ]
        assert match["tags"] == ["One Shot", "Punchy"]
        assert match["use_count"] == 5

    def test_absent_equals_default(self, db_dir):
        result = search_library({"query": "808 Sub"})
        (match,) = result["matches"]
        # use_count 0, no tags -> the fields are simply absent.
        assert "use_count" not in match
        assert "tags" not in match
        assert "truncated" not in result
        assert "staleness" not in result

    def test_core_library_gets_no_browser_guess(self, db_dir):
        result = search_library({"query": "Impulse 808"})
        (match,) = result["matches"]
        assert match["source"] == "core_library"
        assert "browser_path_guess" not in match
        assert match["path"].startswith("C:/Music/Core Library/")

    def test_kind_filter(self, db_dir):
        names = {m["name"] for m in search_library({"query": "808", "kind": "preset"})["matches"]}
        assert names == {"Impulse 808.adv", "808 Core Kit.adg"}

    def test_source_filter(self, db_dir):
        names = {
            m["name"] for m in search_library({"query": "808", "source": "user_library"})["matches"]
        }
        assert names == {"808 Kick.wav", "808 Sub.wav"}

    def test_tags_all_must_match_case_insensitive(self, db_dir):
        names = {m["name"] for m in search_library({"tags": "one shot, PUNCHY"})["matches"]}
        assert names == {"808 Kick.wav"}
        names = {m["name"] for m in search_library({"tags": "One Shot"})["matches"]}
        assert names == {"808 Kick.wav", "Snare Tight.wav"}

    def test_like_wildcards_are_escaped(self, db_dir):
        # '100%' and '_' must match literally, not as SQL wildcards.
        assert [m["name"] for m in search_library({"query": "100% Wet_Clap"})["matches"]] == [
            "100% Wet_Clap.wav"
        ]
        assert search_library({"query": "100%X"})["matches"] == []

    def test_sorted_by_use_count_and_truncated(self, db_dir):
        result = search_library({"kind": "sample", "limit": 2})
        assert [m["name"] for m in result["matches"]] == ["808 Kick.wav", "Snare Tight.wav"]
        assert result["truncated"] is True

    def test_no_filters_rejected(self, db_dir):
        with pytest.raises(ValueError, match="at least one filter"):
            search_library({})

    def test_bad_enum_values_rejected(self, db_dir):
        with pytest.raises(ValueError, match="'kind'"):
            search_library({"query": "x", "kind": "sausage"})
        with pytest.raises(ValueError, match="'source'"):
            search_library({"query": "x", "source": "attic"})
        with pytest.raises(ValueError, match="'limit'"):
            search_library({"query": "x", "limit": 0})

    def test_plugin_kind_reads_plugins_db(self, db_dir):
        result = search_library({"kind": "plugin"})
        by_name = {m["name"]: m for m in result["matches"]}
        assert set(by_name) == {"Purity_x64", "Omnisphere"}  # disabled row excluded
        assert by_name["Purity_x64"]["browser_path_guess"] == [
            "plugins",
            "VST",
            "SonicCat",
            "Purity_x64",
        ]
        assert by_name["Omnisphere"]["browser_path_guess"] == [
            "plugins",
            "VST3",
            "Spectrasonics",
            "Omnisphere",
        ]
        assert by_name["Omnisphere"]["tags"] == ["Instrument", "Synth"]
        assert "tags" not in by_name["Purity_x64"]

    def test_kind_any_includes_plugins(self, db_dir):
        names = {m["name"] for m in search_library({"query": "Omni"})["matches"]}
        assert names == {"Omnisphere"}

    def test_staleness_note_when_wal_is_newer(self, db_dir):
        wal = db_dir / "Live-files-12300.db-wal"
        wal.write_bytes(b"x" * 32)
        now = time.time()
        os.utime(db_dir / "Live-files-12300.db", (now - 60, now - 60))
        os.utime(wal, (now, now))
        result = search_library({"query": "808 Kick"})
        assert "snapshot" in result["staleness"]


class TestFindSimilar:
    def test_ranks_by_cosine_and_excludes_reference(self, db_dir):
        result = find_similar({"query": "808 Kick"})
        assert result["reference"]["name"] == "808 Kick.wav"
        names = [m["name"] for m in result["matches"]]
        # Impulse 808.adv shares KICK_VEC exactly; the sub is nearly parallel;
        # the snare is orthogonal and must come last.
        assert names[0] == "Impulse 808.adv"
        assert names[1] == "808 Sub.wav"
        assert names[-1] == "Snare Tight.wav"
        assert "808 Kick.wav" not in names
        assert result["matches"][0]["similarity"] == 1.0
        # The version-99 blob is skipped with a note, never an error.
        assert "Weird Blob.adv" not in names
        assert "skipped" in result["note"]

    def test_reference_by_path(self, db_dir):
        result = find_similar({"path": "C:\\Music\\User Library\\Drum Kits\\Kit A\\808 Kick.wav"})
        assert result["reference"]["name"] == "808 Kick.wav"
        assert result["matches"]

    def test_path_xor_query_enforced(self, db_dir):
        with pytest.raises(ValueError, match="exactly one"):
            find_similar({})
        with pytest.raises(ValueError, match="exactly one"):
            find_similar({"path": "x", "query": "y"})

    def test_unknown_reference_errors(self, db_dir):
        with pytest.raises(ValueError, match="No library file"):
            find_similar({"path": "C:/nowhere/nothing.wav"})
        with pytest.raises(ValueError, match="No analyzed library file"):
            find_similar({"query": "zzz-no-such-file"})

    def test_unanalyzed_reference_gets_note_not_error(self, db_dir):
        result = find_similar({"path": "C:/Music/User Library/Drum Kits/Kit A/Unanalyzed.wav"})
        assert result["matches"] == []
        assert "not analyzed" in result["note"]


class TestFeatureDecoding:
    def test_decode_round_trip(self):
        vec = feature_vector(_blob([0.5] * 64))
        assert len(vec) == 64
        assert vec[0] == 0.5

    def test_unknown_layouts_return_none(self):
        assert feature_vector(b"short") is None
        assert feature_vector(_blob([0.5] * 64, version=17)) is None
        assert feature_vector(_blob([0.5] * 32, count=32)) is None

    def test_cosine(self):
        assert cosine_similarity([1, 0], [1, 0]) == 1.0
        assert cosine_similarity([1, 0], [0, 1]) == 0.0
        assert cosine_similarity([1, 0], [0, 0]) == 0.0  # zero norm, no crash


@pytest.mark.anyio
class TestLibraryToolsOverProtocol:
    async def _session(self, client):
        from mcp.shared.memory import create_connected_server_and_client_session

        from mcp_server.server import build_server

        return create_connected_server_and_client_session(build_server(client))

    async def test_search_never_touches_live(self, db_dir):
        from tests.helpers import FakeAbletonClient

        fake = FakeAbletonClient(connected=False)  # Live is unreachable...
        async with await self._session(fake) as session:
            result = await session.call_tool("search_library", {"query": "808 Kick"})
            assert result.isError is False  # ...and search works anyway
            payload = json.loads(result.content[0].text)
            assert payload["matches"][0]["name"] == "808 Kick.wav"
            assert "\n" not in result.content[0].text  # compact emission
        assert fake.sent == []

    async def test_find_similar_over_protocol(self, db_dir):
        from tests.helpers import FakeAbletonClient

        async with await self._session(FakeAbletonClient()) as session:
            result = await session.call_tool("find_similar", {"query": "808 Kick"})
            assert result.isError is False
            assert result.structuredContent["reference"]["name"] == "808 Kick.wav"

    async def test_errors_surface_as_tool_errors(self, db_dir):
        from tests.helpers import FakeAbletonClient

        async with await self._session(FakeAbletonClient()) as session:
            result = await session.call_tool("search_library", {})
            assert result.isError is True
            assert "at least one filter" in result.content[0].text
