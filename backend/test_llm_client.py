import json

import llm_client


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_urlopen(captured: dict):
    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(json.dumps({"message": {"content": "candidate"}}).encode())

    return fake_urlopen


def test_chat_injects_default_num_predict(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(llm_client, "urlopen", _capture_urlopen(captured))

    result = llm_client.chat("http://127.0.0.1:11434", "m", [{"role": "user", "content": "x"}])

    assert result["ok"] is True
    assert captured["body"]["options"]["num_predict"] == llm_client.MAX_PREDICT_TOKENS


def test_chat_respects_explicit_num_predict(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(llm_client, "urlopen", _capture_urlopen(captured))

    llm_client.chat(
        "http://127.0.0.1:11434", "m",
        [{"role": "user", "content": "x"}],
        options={"num_predict": 128},
    )

    assert captured["body"]["options"]["num_predict"] == 128
