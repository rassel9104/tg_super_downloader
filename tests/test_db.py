from datetime import datetime, timedelta, timezone
from tgdl.core import db as DB  # :contentReference[oaicite:4]{index=4}

def test_db_lifecycle_basic(temp_db_path):
    # init
    DB.db_init(temp_db_path)
    # flags
    DB.db_set_flag("PAUSED", "0")
    assert DB.is_paused() is False
    DB.db_set_flag("PAUSED", "1")
    assert DB.is_paused() is True

    # enqueue 2 items
    now = datetime.now(timezone.utc)
    q1 = DB.db_add("url", {"url":"http://example.com/a"}, now - timedelta(minutes=1))
    q2 = DB.db_add("url", {"url":"http://example.com/b"}, now + timedelta(hours=1))
    assert isinstance(q1, int) and isinstance(q2, int)

    # due vs queued
    due = DB.db_get_due(now)
    assert {r[0] for r in due} == {q1}
    allq = DB.db_get_all_queued()
    assert {r[0] for r in allq} == {q1, q2}

    # status & list
    DB.db_update_status(q1, "running")
    lst = DB.db_list()
    ids = {r[0] for r in lst}
    assert q1 in ids and q2 in ids

    # progress upsert
    DB.db_update_progress(q1, total=1000, downloaded=100)
    DB.db_update_progress(q1, total=1000, downloaded=500)
    DB.db_clear_progress(q1)

    # retry/purge
    DB.db_update_status(q1, "error")
    assert DB.db_retry_errors() >= 1
    DB.db_update_status(q1, "done")
    DB.db_update_status(q2, "error")
    deleted = DB.db_purge_finished()
    assert deleted >= 2

def test_migration_and_clear(temp_db_path):
    DB.db_init(temp_db_path)
    DB.db_migrate_add_ext_id()  # crea columna ext_id si no existe
    # insertar y setear ext_id
    now = datetime.now(timezone.utc)
    qid = DB.db_add("url", {"url": "http://x"}, now)
    DB.db_set_ext_id(qid, "gid-123")
    rows = DB.db_get_queue()
    assert rows[0][5] == "gid-123"  # ext_id en la 6a columna
    # clear all
    DB.db_clear_all()
    assert DB.db_list() == []
