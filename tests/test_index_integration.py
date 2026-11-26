"""Runs only when Postgres with pgvector is available. GitHub Actions provides it."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("psycopg")
import psycopg

MIGRATION = Path("supabase/migrations/001_policy_chunks.sql")


@pytest.fixture(scope="module")
def database():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip("DATABASE_URL not set")
    if shutil.which("psql") is None:
        pytest.skip("psql not installed")
    subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-f", str(MIGRATION)],
        check=True,
        capture_output=True,
        text=True,
    )
    with psycopg.connect(url) as conn:
        yield conn


def _vector(position: int) -> str:
    values = ["0"] * 1536
    values[position] = "1"
    return "[" + ",".join(values) + "]"


def test_full_text_vector_and_row_level_security(database):
    with database.cursor() as cursor:
        cursor.execute("delete from policy_chunks")
        cursor.execute(
            """
            insert into policy_chunks (
                page_id, title, section_heading, chunk_index, chunk_text, content_hash,
                last_edited_time, is_live, embedding, embedding_model, embedding_dimensions
            ) values
            ('live', 'Cold chain', 'Fridge temperatures', 0, 'record the fridge minimum',
             'hash-live', '2026-01-01', true, %s, 'text-embedding-3-small', 1536),
            ('old', 'Old policy', 'Archive', 0, 'record the fridge minimum too',
             'hash-old', '2020-01-01', false, %s, 'text-embedding-3-small', 1536)
            """,
            (_vector(0), _vector(1)),
        )
        cursor.execute("select page_id from search_policy_chunks_fts('fridge minimum', 10, true)")
        assert [row[0] for row in cursor.fetchall()] == ["live"]
        cursor.execute(
            "select page_id from search_policy_chunks_vector(%s::vector, 10, true)",
            (_vector(0),),
        )
        assert [row[0] for row in cursor.fetchall()] == ["live"]
        cursor.execute(
            """
            insert into policy_chunks (
                page_id, title, section_heading, chunk_index, chunk_text, content_hash,
                last_edited_time, is_live, embedding, embedding_model, embedding_dimensions
            ) values
            ('other', 'Other model', 'Fridge temperatures', 0, 'record the fridge minimum',
             'hash-other', '2026-01-01', true, %s, 'other/model', 1536)
            """,
            (_vector(0),),
        )
        cursor.execute(
            """
            select page_id from search_policy_chunks_vector(
                %s::vector, 10, true, 'text-embedding-3-small'
            )
            """,
            (_vector(0),),
        )
        assert [row[0] for row in cursor.fetchall()] == ["live"]
        cursor.execute(
            """
            select chunk_index from neighbour_policy_chunks('live', 0)
            """
        )
        assert cursor.fetchall() == [(0,)]
        cursor.execute("select nathan_try_index_lock('tester', 30)")
        assert cursor.fetchone()[0] is True
        cursor.execute("select nathan_try_index_lock('other', 30)")
        assert cursor.fetchone()[0] is False
        cursor.execute("select nathan_index_unlock('tester')")

        cursor.execute("drop role if exists nathan_reader")
        cursor.execute("create role nathan_reader nologin")
        cursor.execute("grant usage on schema public to nathan_reader")
        cursor.execute("grant select on policy_chunks to nathan_reader")
        cursor.execute("set role nathan_reader")
        cursor.execute("select count(*) from policy_chunks")
        assert cursor.fetchone()[0] == 0
        cursor.execute("reset role")
        cursor.execute("select count(*) from policy_chunks")
        assert cursor.fetchone()[0] == 3
        cursor.execute("drop owned by nathan_reader")
        cursor.execute("drop role nathan_reader")
    database.commit()
