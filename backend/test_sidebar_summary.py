"""Tests for the desensitized sidebar summary endpoint and projection."""

from __future__ import annotations

import json

import datadir
import generation_history
import model_identity
import rule_store


def _node_headers(main) -> dict[str, str]:
    return {"Authorization": f"Bearer {main.NODE_TOKEN}"}


def test_summary_requires_authentication(client):
    test_client, _ = client

    assert test_client.get("/api/sidebar/summary").status_code == 401


def test_summary_allows_node_token_read_only(client):
    test_client, main = client

    resp = test_client.get("/api/sidebar/summary", headers=_node_headers(main))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert set(body["sections"]) == {
        "rules",
        "workflows",
        "drafts",
        "assets",
        "loras",
        "history",
    }


def test_empty_sections_report_zero_totals(client):
    test_client, main = client

    body = test_client.get("/api/sidebar/summary", headers=_node_headers(main)).json()
    for section in body["sections"].values():
        assert section["total"] == 0
        assert section["items"] == []


def test_summary_redacts_prompt_text_and_notes(client):
    test_client, main = client

    db_path = datadir.database_path(main.DATA_ROOT)
    with model_identity.connect(db_path) as connection:
        rule_store.ensure_schema(connection)
        rule_store.upsert_rule(
            connection,
            level="checkpoint",
            scope_key="pony-v6.safetensors",
            rule_key="Resolution",
            value="1024x1024",
            note="SECRET_NOTE_456",
        )
        generation_history.ensure_schema(connection)
        generation_history.record_snapshot(
            connection,
            prompt_id="prompt-abcdef123456",
            workflow_id="wf-1",
            working_prompts=[
                {"role": "positive", "text": "SECRET_HISTORY_789 positive"},
                {"role": "negative", "text": "negative SECRET_HISTORY_789"},
            ],
            report={"model_nodes": [{"inputs": {"ckpt_name": "pony-v6.safetensors"}}]},
            status="succeeded",
        )

    resp = test_client.get("/api/sidebar/summary", headers=_node_headers(main))
    assert resp.status_code == 200
    body = resp.json()
    raw = json.dumps(body, ensure_ascii=False)

    # Sensitive bodies and notes must not leak into the projection.
    assert "SECRET_NOTE_456" not in raw
    assert "SECRET_HISTORY_789" not in raw

    rule = body["sections"]["rules"]["items"][0]
    assert rule["level"] == "checkpoint"
    assert rule["rule_key"] == "Resolution"
    assert rule["value"] == "1024x1024"
    assert "note" not in rule
    assert "structured" not in rule

    history = body["sections"]["history"]["items"][0]
    assert history["status"] == "succeeded"
    assert history["model"] == "pony-v6.safetensors"
    assert "positive_prompt" not in history
    assert "images" not in history


def test_summary_caps_items_but_keeps_total(client):
    test_client, main = client

    db_path = datadir.database_path(main.DATA_ROOT)
    with model_identity.connect(db_path) as connection:
        rule_store.ensure_schema(connection)
        for index in range(21):
            rule_store.upsert_rule(
                connection,
                level="global",
                scope_key="",
                rule_key=f"Rule{index}",
                value=f"value-{index}",
            )

    body = test_client.get("/api/sidebar/summary", headers=_node_headers(main)).json()
    rules = body["sections"]["rules"]
    assert rules["total"] == 21
    assert len(rules["items"]) == 20


def test_node_token_cannot_post_summary(client):
    test_client, main = client

    resp = test_client.post("/api/sidebar/summary", headers=_node_headers(main))
    assert resp.status_code == 403
