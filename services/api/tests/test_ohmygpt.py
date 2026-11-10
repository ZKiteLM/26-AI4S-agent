import json
from types import SimpleNamespace

import pytest

from scripts import local_services
from services.api.app import ai


@pytest.fixture
def client(monkeypatch):
    config = SimpleNamespace(
        llm_provider="ohmygpt",
        llm_base_url="https://api.ohmygpt.com/v1",
        llm_model="deepseek/deepseek-v4.1-flash-1017",
        ohmygpt_api_key="test-key",
    )
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            output_text='{"ok": true}',
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call-1",
                    name="search_bm25",
                    arguments='{"query":"transformer"}',
                )
            ],
        )

    monkeypatch.setattr(ai, "settings", lambda: config)
    monkeypatch.setattr(
        ai,
        "_ohmygpt_client",
        lambda _cfg: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    return requests


def test_generate_uses_responses_and_json_mode(client):
    assert json.loads(ai.generate("Return JSON", "Hello", json_mode=True)) == {"ok": True}
    assert client[0]["model"] == "deepseek/deepseek-v4.1-flash-1017"
    assert client[0]["input"][-1] == {"role": "user", "content": "Hello"}
    assert client[0]["text"] == {"format": {"type": "json_object"}}
    assert client[0]["reasoning"] == {"effort": "none"}
    assert client[0]["store"] is False


def test_tool_history_round_trips_through_responses(client):
    messages = [
        {"role": "system", "content": "Use tools"},
        {"role": "user", "content": "Find the architecture"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-0",
                    "type": "function",
                    "function": {"name": "search_bm25", "arguments": '{"query":"encoder"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-0", "tool_name": "search_bm25", "content": "result"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_bm25",
                "description": "Search papers",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ]
    answer = ai.chat_with_tools(messages, tools, thinking=True)
    assert answer["tool_calls"][0]["id"] == "call-1"
    assert answer["tool_calls"][0]["function"]["name"] == "search_bm25"
    assert client[0]["input"][-2:] == [
        {
            "type": "function_call",
            "call_id": "call-0",
            "name": "search_bm25",
            "arguments": '{"query":"encoder"}',
        },
        {"type": "function_call_output", "call_id": "call-0", "output": "result"},
    ]
    assert client[0]["tools"][0]["name"] == "search_bm25"
    assert client[0]["reasoning"] == {"effort": "medium"}


def test_missing_key_fails_before_request():
    with pytest.raises(RuntimeError, match="OHMYGPT_API_KEY"):
        ai._ohmygpt_client(SimpleNamespace(ohmygpt_api_key=""))


def test_local_services_keep_ollama_for_embeddings(tmp_path, monkeypatch):
    monkeypatch.setattr(local_services, "ROOT", tmp_path)
    env = tmp_path / ".env"
    env.write_text(
        "LLM_PROVIDER=ohmygpt\nLLM_BASE_URL=https://api.ohmygpt.com/v1\n"
        "EMBEDDING_BASE_URL=http://localhost:11434/v1\n"
    )
    assert local_services.ollama_needed()
    env.write_text("LLM_PROVIDER=ohmygpt\nLLM_BASE_URL=https://api.ohmygpt.com/v1\n")
    assert not local_services.ollama_needed()
