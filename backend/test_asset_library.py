import base64
import sqlite3
from pathlib import Path

import asset_library


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = asset_library.connect(tmp_path / "workbench.sqlite3")
    asset_library.ensure_schema(conn)
    return conn


def _png_bytes() -> bytes:
    # Minimal valid-looking PNG payload (bytes are not decoded as an image here).
    return b"\x89PNG\r\n\x1a\n" + b"asset-bytes"


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _write_reference(tmp_path: Path, name: str = "ref.png") -> Path:
    directory = tmp_path / "external"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(_png_bytes())
    return path


def test_default_group_created(tmp_path):
    conn = _db(tmp_path)
    groups = asset_library.list_groups(conn)
    assert len(groups) == 1
    assert groups[0]["name"] == "未分组"
    assert groups[0]["is_default"] is True
    conn.close()


def test_import_copies_writes_unique_files_and_records(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    created = asset_library.import_copies(
        conn,
        assets,
        [{"name": "a.png", "data": _b64(_png_bytes())}],
        tag_names=["人物", "参考"],
    )
    assert len(created) == 1
    item = created[0]
    assert item["kind"] == "copy"
    assert item["exists"] is True
    assert item["tags"] == ["人物", "参考"]
    assert item["group_name"] == "未分组"

    copies_dir = assets / "copies"
    copy_path = copies_dir / item["storage_rel"]
    assert copy_path.is_file()
    assert copy_path.suffix == ".png"
    # Stored name is a unique token, not the original display name.
    assert copy_path.name != "a.png"
    conn.close()


def test_import_copy_rejects_non_image(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    try:
        asset_library.import_copies(
            conn, assets, [{"name": "notes.txt", "data": _b64(b"hello")}]
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "格式" in str(exc)
    conn.close()


def test_import_reference_records_path_without_copy(tmp_path):
    conn = _db(tmp_path)
    source = _write_reference(tmp_path)
    created = asset_library.import_references(conn, [str(source)], tag_names=["外景"])
    assert len(created) == 1
    item = created[0]
    assert item["kind"] == "reference"
    assert item["source_path"] == str(source.resolve())
    assert item["exists"] is True
    assert item["tags"] == ["外景"]
    # No managed copy directory created for references.
    assert not (tmp_path / "assets").exists()
    conn.close()


def test_import_reference_rejects_missing_file(tmp_path):
    conn = _db(tmp_path)
    try:
        asset_library.import_references(conn, [str(tmp_path / "nope.png")])
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "不存在" in str(exc)
    conn.close()


def test_import_reference_rejects_non_image(tmp_path):
    conn = _db(tmp_path)
    text_file = tmp_path / "x.txt"
    text_file.write_text("hi", encoding="utf-8")
    try:
        asset_library.import_references(conn, [str(text_file)])
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "格式" in str(exc)
    conn.close()


def test_group_create_rename_delete_moves_items_to_default(tmp_path):
    conn = _db(tmp_path)
    group = asset_library.create_group(conn, "角色")
    created = asset_library.import_references(
        conn, [str(_write_reference(tmp_path, "one.png"))], group_id=group["id"]
    )
    item_id = created[0]["id"]

    asset_library.rename_group(conn, group["id"], "角色卡")
    groups = asset_library.list_groups(conn)
    assert any(g["name"] == "角色卡" and g["count"] == 1 for g in groups)

    asset_library.delete_group(conn, group["id"])
    item = asset_library.get_item(conn, item_id, tmp_path / "assets")
    default = next(g for g in asset_library.list_groups(conn) if g["is_default"])
    assert item["group_id"] == default["id"]
    assert item["group_name"] == "未分组"
    conn.close()


def test_default_group_cannot_be_renamed_or_deleted(tmp_path):
    conn = _db(tmp_path)
    default = next(g for g in asset_library.list_groups(conn) if g["is_default"])
    try:
        asset_library.rename_group(conn, default["id"], "别的")
        assert False, "expected ValueError"
    except ValueError:
        pass
    try:
        asset_library.delete_group(conn, default["id"])
        assert False, "expected ValueError"
    except ValueError:
        pass
    conn.close()


def test_delete_tag_keeps_item(tmp_path):
    conn = _db(tmp_path)
    created = asset_library.import_references(
        conn, [str(_write_reference(tmp_path, "t.png"))], tag_names=["临时"]
    )
    item_id = created[0]["id"]
    tag = next(t for t in asset_library.list_tags(conn) if t["name"] == "临时")

    asset_library.delete_tag(conn, tag["id"])
    item = asset_library.get_item(conn, item_id, tmp_path / "assets")
    assert item["tags"] == []
    assert item["exists"] is True
    conn.close()


def test_filter_by_group_tag_and_keyword(tmp_path):
    conn = _db(tmp_path)
    group_a = asset_library.create_group(conn, "A组")
    group_b = asset_library.create_group(conn, "B组")

    p1 = _write_reference(tmp_path, "apple.png")
    p2 = _write_reference(tmp_path, "banana.png")
    p3 = _write_reference(tmp_path, "apricot.png")
    asset_library.import_references(conn, [str(p1)], group_id=group_a["id"], tag_names=["红"])
    asset_library.import_references(conn, [str(p2)], group_id=group_b["id"], tag_names=["黄"])
    asset_library.import_references(conn, [str(p3)], group_id=group_a["id"], tag_names=["红", "小"])

    group_items = asset_library.list_items(conn, None, group_id=group_a["id"])
    assert {i["name"] for i in group_items} == {"apple.png", "apricot.png"}

    # AND logic: must carry every selected tag.
    and_items = asset_library.list_items(conn, None, tag_names=["红", "小"])
    assert [i["name"] for i in and_items] == ["apricot.png"]

    keyword_items = asset_library.list_items(conn, None, keyword="app")
    assert [i["name"] for i in keyword_items] == ["apple.png"]
    conn.close()


def test_update_item_name_group_tags_note(tmp_path):
    conn = _db(tmp_path)
    created = asset_library.import_references(
        conn, [str(_write_reference(tmp_path, "old.png"))], tag_names=["旧"]
    )
    item_id = created[0]["id"]
    group = asset_library.create_group(conn, "新组")

    updated = asset_library.update_item(
        conn,
        item_id,
        tmp_path / "assets",
        name="改名.png",
        group_id=group["id"],
        tag_names=["新"],
        note="备注",
    )
    assert updated["name"] == "改名.png"
    assert updated["group_name"] == "新组"
    assert updated["tags"] == ["新"]
    assert updated["note"] == "备注"
    conn.close()


def test_delete_reference_never_touches_original(tmp_path):
    conn = _db(tmp_path)
    source = _write_reference(tmp_path, "keep.png")
    created = asset_library.import_references(conn, [str(source)])
    item_id = created[0]["id"]

    asset_library.delete_item(conn, tmp_path / "assets", item_id, delete_copy=True)
    assert source.is_file(), "original reference file must remain"
    assert asset_library.get_item(conn, item_id, tmp_path / "assets") is None
    conn.close()


def test_delete_copy_keeps_or_removes_managed_copy(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    created = asset_library.import_copies(
        conn, assets, [{"name": "copy.png", "data": _b64(_png_bytes())}]
    )
    item_id = created[0]["id"]
    copies_dir = assets / "copies"
    copy_file = copies_dir / created[0]["storage_rel"]

    # First delete without removing the copy: record gone, managed file kept.
    asset_library.delete_item(conn, assets, item_id, delete_copy=False)
    assert copy_file.is_file()

    # Re-import and delete with copy removal: managed file removed.
    again = asset_library.import_copies(
        conn, assets, [{"name": "copy2.png", "data": _b64(_png_bytes())}]
    )
    second = next((copies_dir / r["storage_rel"] for r in again), None)
    asset_library.delete_item(conn, assets, again[0]["id"], delete_copy=True)
    assert second is not None and not second.exists()
    conn.close()


def test_missing_reference_marked_not_exists(tmp_path):
    conn = _db(tmp_path)
    source = _write_reference(tmp_path, "later.png")
    created = asset_library.import_references(conn, [str(source)])
    source.unlink()
    item = asset_library.get_item(conn, created[0]["id"], tmp_path / "assets")
    assert item["exists"] is False
    assert asset_library.resolve_image_path(conn, tmp_path / "assets", item["id"]) is None
    conn.close()


def test_copy_imported_into_group_subfolder(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    group = asset_library.create_group(conn, "角色 卡")
    created = asset_library.import_copies(
        conn,
        assets,
        [{"name": "a.png", "data": _b64(_png_bytes())}],
        group_id=group["id"],
    )
    item = created[0]
    rel = Path(item["storage_rel"])
    # Folder carries the group id and a cleaned slug; file keeps the token name.
    assert rel.parent.parts[0].startswith(f"{group['id']}-")
    assert " " not in rel.parent.name
    copy_path = assets / "copies" / rel
    assert copy_path.is_file()
    assert copy_path.read_bytes() == _png_bytes()
    conn.close()


def test_regroup_copy_moves_file_between_folders(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    group = asset_library.create_group(conn, "A组")
    created = asset_library.import_copies(
        conn,
        assets,
        [{"name": "a.png", "data": _b64(_png_bytes())}],
        group_id=group["id"],
    )
    item_id = created[0]["id"]
    old_path = assets / "copies" / created[0]["storage_rel"]
    assert old_path.is_file()

    default = next(g for g in asset_library.list_groups(conn) if g["is_default"])
    updated = asset_library.update_item(
        conn, item_id, assets, group_id=default["id"]
    )
    new_path = assets / "copies" / updated["storage_rel"]
    assert not old_path.exists()
    assert new_path.is_file()
    assert new_path.read_bytes() == _png_bytes()
    assert updated["group_name"] == "未分组"
    conn.close()


def test_legacy_flat_copy_filename_still_resolves(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    copies_dir = assets / "copies"
    copies_dir.mkdir(parents=True, exist_ok=True)
    legacy = copies_dir / "legacyabc123.png"
    legacy.write_bytes(_png_bytes())
    now = asset_library._now()
    default_id = next(g["id"] for g in asset_library.list_groups(conn) if g["is_default"])
    conn.execute(
        "INSERT INTO asset_item(name, kind, storage_rel, group_id, created_at, updated_at) "
        "VALUES (?, 'copy', ?, ?, ?, ?)",
        ("legacy.png", legacy.name, default_id, now, now),
    )
    conn.commit()
    item = asset_library.list_items(conn, assets)[0]
    assert item["exists"] is True
    resolved = asset_library.resolve_image_path(conn, assets, item["id"])
    assert resolved == legacy.resolve()
    conn.close()


def test_migrate_legacy_flat_copies_into_group_folders(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    copies_dir = assets / "copies"
    copies_dir.mkdir(parents=True, exist_ok=True)

    group = asset_library.create_group(conn, "苏悦希")
    default_id = next(
        g["id"] for g in asset_library.list_groups(conn) if g["is_default"]
    )
    now = asset_library._now()

    legacy_a = copies_dir / "aaa111.png"
    legacy_b = copies_dir / "bbb222.png"
    legacy_a.write_bytes(_png_bytes())
    legacy_b.write_bytes(_png_bytes())
    conn.execute(
        "INSERT INTO asset_item(name, kind, storage_rel, group_id, created_at, updated_at) "
        "VALUES (?, 'copy', ?, ?, ?, ?)",
        ("a.png", legacy_a.name, group["id"], now, now),
    )
    conn.execute(
        "INSERT INTO asset_item(name, kind, storage_rel, group_id, created_at, updated_at) "
        "VALUES (?, 'copy', ?, ?, ?, ?)",
        ("b.png", legacy_b.name, default_id, now, now),
    )
    conn.commit()

    moved = asset_library.migrate_copy_group_folders(conn, assets)
    assert moved == 2

    items = asset_library.list_items(conn, assets)
    by_group = {item["group_id"]: item for item in items}
    custom = by_group[group["id"]]
    default = by_group[default_id]
    custom_folder = asset_library._group_folder(conn, group["id"])
    assert custom["storage_rel"].startswith(f"{custom_folder}/")
    assert default["storage_rel"].startswith("_default/")
    assert (assets / "copies" / custom["storage_rel"]).read_bytes() == _png_bytes()
    assert (assets / "copies" / default["storage_rel"]).read_bytes() == _png_bytes()
    # No flat bare-name file remains in the copies root.
    assert list(copies_dir.glob("*.png")) == []

    # Second run is a no-op: records already carry a subfolder.
    assert asset_library.migrate_copy_group_folders(conn, assets) == 0
    conn.close()


def test_migrate_legacy_missing_file_rewrites_rel(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    now = asset_library._now()
    default_id = next(
        g["id"] for g in asset_library.list_groups(conn) if g["is_default"]
    )
    conn.execute(
        "INSERT INTO asset_item(name, kind, storage_rel, group_id, created_at, updated_at) "
        "VALUES (?, 'copy', ?, ?, ?, ?)",
        ("ghost.png", "ghost.png", default_id, now, now),
    )
    conn.commit()

    asset_library.migrate_copy_group_folders(conn, assets)
    item = asset_library.list_items(conn, assets)[0]
    assert item["storage_rel"] == "_default/ghost.png"
    assert item["exists"] is False
    conn.close()


def test_rename_group_renames_physical_folder(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    group = asset_library.create_group(conn, "旧名")
    created = asset_library.import_copies(
        conn,
        assets,
        [{"name": "a.png", "data": _b64(_png_bytes())}],
        group_id=group["id"],
    )
    old_rel = created[0]["storage_rel"]
    old_dir = (assets / "copies" / old_rel).parent
    assert old_dir.is_dir()

    asset_library.rename_group(conn, group["id"], "新名", assets)
    item = asset_library.get_item(conn, created[0]["id"], assets)
    new_dir = (assets / "copies" / item["storage_rel"]).parent
    assert new_dir.is_dir()
    assert not old_dir.exists()
    assert (new_dir / Path(old_rel).name).is_file()
    conn.close()


def test_delete_group_moves_copies_to_default_folder(tmp_path):
    conn = _db(tmp_path)
    assets = tmp_path / "assets"
    group = asset_library.create_group(conn, "临时组")
    created = asset_library.import_copies(
        conn,
        assets,
        [{"name": "a.png", "data": _b64(_png_bytes())}],
        group_id=group["id"],
    )
    old_path = assets / "copies" / created[0]["storage_rel"]
    assert old_path.is_file()

    asset_library.delete_group(conn, group["id"], assets)
    item = asset_library.get_item(conn, created[0]["id"], assets)
    default = next(g for g in asset_library.list_groups(conn) if g["is_default"])
    assert item["group_id"] == default["id"]
    new_path = assets / "copies" / item["storage_rel"]
    assert new_path.is_file()
    assert not old_path.exists()
    conn.close()
