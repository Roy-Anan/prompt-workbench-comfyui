"""Tests for the vision / writing history tables and aggregated archive API."""

import llm_history

AUTH = {"Authorization": "Bearer test-session-token"}


def _connect(main):
    return main.model_identity.connect(
        main.datadir.database_path(main.DATA_ROOT)
    )


def test_vision_record_list_delete_and_keyword():
    import sqlite3
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "t.sqlite3")
        llm_history.ensure_schema(conn)

        record = llm_history.record_vision(
            conn,
            model="qwen3-vl:latest",
            prompt="描述服装",
            description="盘发女性，白衬衫黑裙",
            asset_ids=[7, 9],
            image_names=["a.png", "b.png"],
        )
        assert record["kind"] == "vision"
        assert record["assets"] == [
            {"asset_id": 7, "name": "a.png"},
            {"asset_id": 9, "name": "b.png"},
        ]

        listed = llm_history.list_vision(conn)
        assert len(listed) == 1
        assert llm_history.list_vision(conn, keyword="盘发")[0]["id"] == record["id"]
        assert llm_history.list_vision(conn, keyword="nope") == []

        assert llm_history.delete_vision(conn, int(record["id"])) is True
        assert llm_history.list_vision(conn) == []
        conn.close()


def test_vision_rejects_empty_description():
    import sqlite3
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "t.sqlite3")
        llm_history.ensure_schema(conn)
        try:
            try:
                llm_history.record_vision(
                    conn,
                    model=None,
                    prompt=None,
                    description="   ",
                    asset_ids=[],
                    image_names=[],
                )
                raise AssertionError("空描述应被拒绝")
            except ValueError:
                pass
        finally:
            conn.close()


def test_writing_record_list_delete_and_keyword():
    import sqlite3
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "t.sqlite3")
        llm_history.ensure_schema(conn)

        record = llm_history.record_writing(
            conn,
            model="qwen3:latest",
            persona_id="p1",
            persona_name="插画师",
            genre="二次元",
            style="清新",
            mode="draft",
            task="写一段角色出场",
            output_text="她推开门走进来。",
        )
        assert record["kind"] == "writing"
        assert record["persona_name"] == "插画师"

        assert len(llm_history.list_writing(conn)) == 1
        assert llm_history.list_writing(conn, keyword="出场")[0]["id"] == record["id"]
        assert llm_history.list_writing(conn, keyword="missing") == []

        assert llm_history.delete_writing(conn, int(record["id"])) is True
        assert llm_history.list_writing(conn) == []
        conn.close()


def test_aggregated_history_sections_and_counts(client):
    test_client, main = client
    with _connect(main) as conn:
        llm_history.ensure_schema(conn)
        v = llm_history.record_vision(
            conn,
            model="vl",
            prompt=None,
            description="识别到一名剑士",
            asset_ids=[3],
            image_names=["sword.png"],
        )
        w = llm_history.record_writing(
            conn,
            model="llm",
            persona_id=None,
            persona_name="裸模型",
            genre=None,
            style=None,
            mode="polish",
            task="润色一句",
            output_text="润色后的成文内容",
        )

    all_body = test_client.get("/api/history/all", headers=AUTH).json()
    assert all_body["counts"] == {"prompt": 0, "vision": 1, "writing": 1}
    kinds = {item["kind"] for item in all_body["items"]}
    assert kinds == {"vision", "writing"}

    vision_body = test_client.get(
        "/api/history/all?kind=vision", headers=AUTH
    ).json()
    assert [item["id"] for item in vision_body["items"]] == [v["id"]]
    assert vision_body["items"][0]["assets"][0]["asset_id"] == 3

    writing_body = test_client.get(
        "/api/history/all?kind=writing", headers=AUTH
    ).json()
    assert [item["id"] for item in writing_body["items"]] == [w["id"]]


def test_save_writing_endpoint_persists_and_rejects_empty(client):
    test_client, _ = client

    ok = test_client.post(
        "/api/history/writing",
        headers=AUTH,
        json={
            "model": "llm",
            "persona_name": "作者",
            "genre": "散文",
            "style": "白描",
            "mode": "draft",
            "task": "写开头",
            "output_text": "一段成文。",
        },
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["kind"] == "writing"
    assert body["output_text"] == "一段成文。"

    empty = test_client.post(
        "/api/history/writing",
        headers=AUTH,
        json={"output_text": "   "},
    )
    assert empty.status_code == 400


def test_save_vision_requires_asset_reference(client):
    test_client, _ = client

    no_asset = test_client.post(
        "/api/history/vision",
        headers=AUTH,
        json={"model": "vl", "description": "临时图描述", "assets": []},
    )
    assert no_asset.status_code == 400

    missing_asset = test_client.post(
        "/api/history/vision",
        headers=AUTH,
        json={
            "model": "vl",
            "description": "描述",
            "assets": [{"asset_id": 999999, "name": "x.png"}],
        },
    )
    assert missing_asset.status_code == 404


def test_delete_history_records_permanently(client):
    test_client, main = client
    with _connect(main) as conn:
        llm_history.ensure_schema(conn)
        v = llm_history.record_vision(
            conn,
            model="vl",
            prompt=None,
            description="待删识别",
            asset_ids=[1],
            image_names=["a.png"],
        )
        w = llm_history.record_writing(
            conn,
            model="llm",
            persona_id=None,
            persona_name=None,
            genre=None,
            style=None,
            mode="draft",
            task=None,
            output_text="待删成文",
        )

    removed_v = test_client.delete(
        f"/api/history/vision/{v['id']}", headers=AUTH
    )
    assert removed_v.status_code == 200
    assert removed_v.json()["removed"] is True

    removed_w = test_client.delete(
        f"/api/history/writing/{w['id']}", headers=AUTH
    )
    assert removed_w.status_code == 200

    missing = test_client.delete(
        f"/api/history/writing/{w['id']}", headers=AUTH
    )
    assert missing.status_code == 404

    body = test_client.get("/api/history/all", headers=AUTH).json()
    assert body["counts"] == {"prompt": 0, "vision": 0, "writing": 0}


def test_save_vision_references_existing_asset(client, tmp_path):
    test_client, main = client
    image = tmp_path / "a.png"
    image.write_bytes(b"fake-png")

    with _connect(main) as conn:
        main.asset_library.ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO asset_item
                (name, kind, source_path, ext, note, created_at, updated_at)
            VALUES (?, 'reference', ?, '.png', '', ?, ?)
            """,
            ("a.png", str(image), "2026-11-20T00:00:00+00:00", "2026-11-20T00:00:00+00:00"),
        )
        asset_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    resp = test_client.post(
        "/api/history/vision",
        headers=AUTH,
        json={
            "model": "vl",
            "prompt": "看服装",
            "description": "白衬衫黑裙",
            "assets": [{"asset_id": asset_id, "name": "a.png"}],
        },
    )
    assert resp.status_code == 200
    record = resp.json()
    assert record["kind"] == "vision"
    assert record["assets"] == [{"asset_id": asset_id, "name": "a.png"}]

    listed = test_client.get(
        "/api/history/all?kind=vision", headers=AUTH
    ).json()
    assert listed["counts"]["vision"] == 1
    assert listed["items"][0]["assets"][0]["exists"] is True


def test_delete_unknown_kind_rejected(client):
    test_client, _ = client
    resp = test_client.delete("/api/history/bogus/1", headers=AUTH)
    assert resp.status_code == 400
