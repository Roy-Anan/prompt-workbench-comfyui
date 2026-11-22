import base64
from pathlib import Path

import asset_api
import datadir
import pytest


def _auth():
    return {"Authorization": "Bearer test-session-token"}


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"asset-api-bytes"


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def test_assets_require_session(client):
    test_client, _ = client
    response = test_client.get("/api/assets/items")
    assert response.status_code == 401


def test_groups_default_and_crud(client):
    test_client, _ = client
    headers = _auth()

    groups = test_client.get("/api/assets/groups", headers=headers).json()["groups"]
    assert len(groups) == 1
    default = groups[0]
    assert default["name"] == "未分组"

    created = test_client.post(
        "/api/assets/groups", json={"name": "角色"}, headers=headers
    ).json()
    gid = created["id"]

    renamed = test_client.post(
        f"/api/assets/groups/{gid}/rename", json={"name": "角色卡"}, headers=headers
    )
    assert renamed.status_code == 200

    test_client.delete(f"/api/assets/groups/{gid}", headers=headers)
    groups = test_client.get("/api/assets/groups", headers=headers).json()["groups"]
    assert all(g["name"] != "角色卡" for g in groups)


def test_import_copy_reference_list_and_proxy(client, tmp_path):
    test_client, main = client
    headers = _auth()

    # Copy import via base64 JSON.
    copy_resp = test_client.post(
        "/api/assets/import-copy",
        json={
            "files": [{"name": "shot.png", "data": _b64(_png())}],
            "tags": ["参考"],
        },
        headers=headers,
    )
    assert copy_resp.status_code == 200, copy_resp.text
    copy_items = copy_resp.json()["items"]
    assert len(copy_items) == 1
    copy_id = copy_items[0]["id"]
    assert copy_items[0]["kind"] == "copy"
    assert copy_items[0]["exists"] is True

    # Reference import from a real external file.
    external = tmp_path / "external.png"
    external.write_bytes(_png())
    ref_resp = test_client.post(
        "/api/assets/import-reference",
        json={"paths": [str(external)], "tags": ["外景"]},
        headers=headers,
    )
    assert ref_resp.status_code == 200, ref_resp.text
    ref_id = ref_resp.json()["items"][0]["id"]

    items = test_client.get("/api/assets/items", headers=headers).json()["items"]
    assert len(items) == 2

    # Tag AND filter.
    filtered = test_client.get(
        "/api/assets/items", params={"tags": "参考"}, headers=headers
    ).json()["items"]
    assert [i["id"] for i in filtered] == [copy_id]

    # Protected proxy serves the copy bytes.
    image = test_client.get(f"/api/assets/image?asset_id={copy_id}", headers=headers)
    assert image.status_code == 200
    assert image.content == _png()

    # Reference proxy serves the original; original is not copied into userdata.
    ref_image = test_client.get(f"/api/assets/image?asset_id={ref_id}", headers=headers)
    assert ref_image.status_code == 200
    assert ref_image.content == _png()

    # Update metadata.
    update = test_client.post(
        f"/api/assets/items/{copy_id}",
        json={"name": "改图.png", "tags": ["新标签"], "note": "备注"},
        headers=headers,
    )
    assert update.status_code == 200
    body = update.json()
    assert body["name"] == "改图.png"
    assert body["tags"] == ["新标签"]
    assert body["note"] == "备注"

    # Delete reference: original file stays.
    test_client.request(
        "DELETE",
        f"/api/assets/items/{ref_id}",
        json={"delete_copy": True},
        headers=headers,
    )
    assert external.is_file()

    # Delete copy without removing the managed copy.
    test_client.request(
        "DELETE",
        f"/api/assets/items/{copy_id}",
        json={"delete_copy": False},
        headers=headers,
    )
    items = test_client.get("/api/assets/items", headers=headers).json()["items"]
    assert items == []


def test_invalid_imports_rejected(client, tmp_path):
    test_client, _ = client
    headers = _auth()

    bad_copy = test_client.post(
        "/api/assets/import-copy",
        json={"files": [{"name": "x.txt", "data": _b64(b"hi")}]},
        headers=headers,
    )
    assert bad_copy.status_code == 400

    missing = test_client.post(
        "/api/assets/import-reference",
        json={"paths": [str(tmp_path / "missing.png")]},
        headers=headers,
    )
    assert missing.status_code == 400


def test_import_history_image_as_reference(client, tmp_path):
    test_client, main = client
    headers = _auth()

    output_dir = tmp_path / "comfy-output"
    output_dir.mkdir()
    shot = output_dir / "grid_001.png"
    shot.write_bytes(_png())
    datadir.save_settings_file(
        main.DATA_ROOT, {"storage": {"output": str(output_dir)}}
    )

    resp = test_client.post(
        "/api/assets/import-history",
        json={"filename": "grid_001.png", "subfolder": ""},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["kind"] == "reference"
    assert item["exists"] is True

    image = test_client.get(
        f"/api/assets/image?asset_id={item['id']}", headers=headers
    )
    assert image.status_code == 200
    assert image.content == _png()

    missing = test_client.post(
        "/api/assets/import-history",
        json={"filename": "nope.png"},
        headers=headers,
    )
    assert missing.status_code == 404


def test_missing_image_proxy_404(client):
    test_client, _ = client
    headers = _auth()
    response = test_client.get("/api/assets/image?asset_id=999999", headers=headers)
    assert response.status_code == 404


def test_open_assets_dir_calls_fixed_opener(client, monkeypatch):
    test_client, _ = client
    captured: dict[str, str] = {}

    def fake_open(directory):
        captured["dir"] = str(directory)
        return {"path": str(directory)}

    monkeypatch.setattr(asset_api, "_open_fixed_dir", fake_open)
    response = test_client.post("/api/assets/open-dir", headers=_auth())
    assert response.status_code == 200
    assert response.json()["path"] == captured["dir"]


def test_open_assets_dir_os_error_returns_500(client, monkeypatch):
    test_client, _ = client

    def fake_open(directory):
        raise OSError("boom")

    monkeypatch.setattr(asset_api, "_open_fixed_dir", fake_open)
    response = test_client.post("/api/assets/open-dir", headers=_auth())
    assert response.status_code == 500
