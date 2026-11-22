"""Tests for the local pending submission queue layer."""

import task_queue
import model_identity
import datadir

AUTH = {"Authorization": "Bearer test-session-token"}


def _api_workflow():
    return {
        "1": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "positive", "clip": ["5", 0]},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "negative", "clip": ["5", 0]},
        },
        "5": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "x.safetensors"},
        },
    }


def _ui_workflow():
    return {"nodes": [{"id": 1, "type": "CLIPTextEncode"}], "links": []}


def _import(test_client, workflow):
    response = test_client.post(
        "/api/workflows/import",
        headers=AUTH,
        json={"filename": "sample.json", "workflow": workflow},
    )
    assert response.status_code == 200
    return response.json()["id"]


def _confirm(test_client, workflow_id):
    response = test_client.post(
        f"/api/workflows/{workflow_id}/confirm",
        headers=AUTH,
        json={"positive_node_ids": ["1"], "negative_node_ids": ["2"]},
    )
    assert response.status_code == 200


def _enqueue(test_client, workflow_id, note=""):
    return test_client.post(
        "/api/queue",
        headers=AUTH,
        json={"workflow_id": workflow_id, "note": note},
    )


def test_enqueue_confirmed_workflow_listed_pending(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)

    response = _enqueue(test_client, workflow_id, "first")
    assert response.status_code == 200
    item = response.json()
    assert item["workflow_id"] == workflow_id
    assert item["status"] == "pending"
    assert item["note"] == "first"

    listed = test_client.get("/api/queue", headers=AUTH).json()
    assert listed["global_paused"] is False
    assert len(listed["items"]) == 1
    assert listed["items"][0]["queue_id"] == item["queue_id"]


def test_enqueue_rejects_unconfirmed_ui_and_missing(client):
    test_client, _ = client

    # Unconfirmed API workflow.
    unconfirmed = _import(test_client, _api_workflow())
    assert _enqueue(test_client, unconfirmed).status_code == 400

    # UI-format workflow.
    ui_id = _import(test_client, _ui_workflow())
    assert _enqueue(test_client, ui_id).status_code == 400

    # Missing workflow.
    missing = test_client.post(
        "/api/queue", headers=AUTH, json={"workflow_id": "no-such-id"}
    )
    assert missing.status_code == 404


def test_enqueue_freezes_working_snapshot(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)

    response = _enqueue(test_client, workflow_id, "frozen")
    assert response.status_code == 200
    body = response.json()
    # The API exposes only a snapshot flag, not the frozen snapshot body.
    assert body["has_snapshot"] is True
    assert "prompt_snapshot" not in body

    listing = test_client.get("/api/queue", headers=AUTH)
    listed = listing.json()["items"][0]
    assert listed["has_snapshot"] is True
    assert "prompt_snapshot" not in listed


def test_item_pause_and_resume(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    queue_id = _enqueue(test_client, workflow_id).json()["queue_id"]

    paused = test_client.post(
        f"/api/queue/{queue_id}/pause", headers=AUTH, json={"paused": True}
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    resumed = test_client.post(
        f"/api/queue/{queue_id}/pause", headers=AUTH, json={"paused": False}
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "pending"


def test_cancel_pending_item_hides_it(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    queue_id = _enqueue(test_client, workflow_id).json()["queue_id"]

    cancel = test_client.delete(f"/api/queue/{queue_id}", headers=AUTH)
    assert cancel.status_code == 200

    listed = test_client.get("/api/queue", headers=AUTH).json()
    assert listed["items"] == []

    missing = test_client.delete(f"/api/queue/{queue_id}", headers=AUTH)
    assert missing.status_code == 404


def test_move_up_swaps_with_previous(client):
    test_client, _ = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)

    a = _enqueue(test_client, workflow_id, "a").json()["queue_id"]
    b = _enqueue(test_client, workflow_id, "b").json()["queue_id"]
    c = _enqueue(test_client, workflow_id, "c").json()["queue_id"]

    moved = test_client.post(
        f"/api/queue/{b}/move", headers=AUTH, json={"direction": "up"}
    )
    assert moved.status_code == 200
    order = [item["queue_id"] for item in moved.json()["items"]]
    assert order == [b, a, c]


def test_global_pause_toggle_roundtrip(client):
    test_client, _ = client
    on = test_client.post("/api/queue/pause-all", headers=AUTH, json={"paused": True})
    assert on.status_code == 200
    assert on.json()["global_paused"] is True

    listed = test_client.get("/api/queue", headers=AUTH).json()
    assert listed["global_paused"] is True

    off = test_client.post("/api/queue/pause-all", headers=AUTH, json={"paused": False})
    assert off.status_code == 200
    assert off.json()["global_paused"] is False


def test_settled_item_cannot_be_cancelled_or_paused(client):
    test_client, main = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    queue_id = _enqueue(test_client, workflow_id).json()["queue_id"]

    with model_identity.connect(datadir.database_path(main.DATA_ROOT)) as connection:
        task_queue.ensure_schema(connection)
        task_queue.mark_submitted(connection, queue_id, "fake-prompt-id")

    cancel = test_client.delete(f"/api/queue/{queue_id}", headers=AUTH)
    assert cancel.status_code == 409

    paused = test_client.post(
        f"/api/queue/{queue_id}/pause", headers=AUTH, json={"paused": True}
    )
    assert paused.status_code == 409

    moved = test_client.post(
        f"/api/queue/{queue_id}/move", headers=AUTH, json={"direction": "up"}
    )
    assert moved.status_code == 409


def test_requeue_stale_submitting_item_on_startup(client):
    """A crash between mark_submitting and mark_submitted must not wedge the queue."""
    test_client, main = client
    workflow_id = _import(test_client, _api_workflow())
    _confirm(test_client, workflow_id)
    queue_id = _enqueue(test_client, workflow_id).json()["queue_id"]

    with model_identity.connect(datadir.database_path(main.DATA_ROOT)) as connection:
        task_queue.ensure_schema(connection)
        task_queue.mark_submitting(connection, queue_id)
        assert task_queue.requeue_stale_submitting(connection) == 1

    listed = test_client.get("/api/queue", headers=AUTH).json()
    assert [item["queue_id"] for item in listed["items"]] == [queue_id]
    assert listed["items"][0]["status"] == "pending"

    # Startup runs this once; a second run must be a no-op.
    with model_identity.connect(datadir.database_path(main.DATA_ROOT)) as connection:
        assert task_queue.requeue_stale_submitting(connection) == 0
