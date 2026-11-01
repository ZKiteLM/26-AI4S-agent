import json
from uuid import uuid4

import httpx
from openai import OpenAI

from .config import settings


def _client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key or "local", timeout=75, max_retries=0)


def generate(system: str, prompt: str, max_tokens: int = 1200, json_mode: bool = False) -> str:
    cfg = settings()
    if not cfg.llm_base_url or not cfg.llm_model:
        raise RuntimeError("LLM endpoint is not configured")
    if cfg.llm_provider == "ollama":
        payload = {
            "model": cfg.llm_model,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0,
                "num_predict": max_tokens,
                "num_ctx": cfg.ollama_num_ctx,
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            payload["format"] = "json"
        response = httpx.post(
            cfg.llm_base_url.removesuffix("/v1").rstrip("/") + "/api/chat",
            json=payload,
            timeout=75,
        )
        if response.status_code == 400 and "exceeds the available context size" in response.text:
            raise RuntimeError(
                f"Local model context is too small (OLLAMA_NUM_CTX={cfg.ollama_num_ctx}). "
                "Reduce retrieved context or increase OLLAMA_NUM_CTX."
            )
        response.raise_for_status()
        return response.json()["message"]["content"]
    if cfg.llm_provider != "openai":
        raise RuntimeError(f"Unsupported LLM provider: {cfg.llm_provider}")
    response = _client(cfg.llm_base_url, cfg.llm_api_key).chat.completions.create(
        model=cfg.llm_model,
        temperature=0,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def chat_with_tools(
    messages: list[dict], tools: list[dict], *, thinking: bool = False, json_mode: bool = False
) -> dict:
    """Return one canonical assistant message, preserving native tool calls."""
    cfg = settings()
    if not cfg.llm_base_url or not cfg.llm_model:
        raise RuntimeError("LLM endpoint is not configured")
    if cfg.llm_provider == "ollama":
        native = []
        for message in messages:
            item = dict(message)
            if item["role"] == "tool":
                item.pop("tool_call_id", None)
            else:
                item.pop("tool_name", None)
            if item.get("tool_calls"):
                item["tool_calls"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": call["function"]["name"],
                            "arguments": json.loads(call["function"]["arguments"]),
                        },
                    }
                    for call in item["tool_calls"]
                ]
            native.append(item)
        payload = {
            "model": cfg.llm_model,
            "messages": native,
            "tools": tools,
            "stream": False,
            "think": thinking,
            "options": {
                "temperature": 0,
                "num_predict": 3500 if thinking else 900,
                "num_ctx": cfg.ollama_num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        response = httpx.post(
            cfg.llm_base_url.removesuffix("/v1").rstrip("/") + "/api/chat",
            json=payload,
            timeout=150 if thinking else 90,
        )
        response.raise_for_status()
        reply = response.json()["message"]
        calls = [
            {
                "id": call.get("id") or str(uuid4()),
                "type": "function",
                "function": {
                    "name": call["function"]["name"],
                    "arguments": json.dumps(
                        call["function"].get("arguments") or {}, ensure_ascii=False
                    ),
                },
            }
            for call in reply.get("tool_calls") or []
        ]
        return {"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls}
    kwargs = {
        "model": cfg.llm_model,
        "messages": [
            {key: value for key, value in message.items() if key != "tool_name"}
            for message in messages
        ],
        "temperature": 0,
        "max_tokens": 3500 if thinking else 900,
    }
    if tools:
        kwargs["tools"] = tools
    if thinking:
        kwargs["reasoning_effort"] = "medium"
        kwargs.pop("temperature")
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = _client(cfg.llm_base_url, cfg.llm_api_key).chat.completions.create(**kwargs)
    reply = response.choices[0].message
    return {
        "role": "assistant",
        "content": reply.content or "",
        "tool_calls": [call.model_dump(exclude_none=True) for call in reply.tool_calls or []],
    }


def embed(texts: list[str]) -> list[list[float]] | None:
    cfg = settings()
    if not cfg.embedding_base_url or not cfg.embedding_model or not texts:
        return None
    response = _client(cfg.embedding_base_url, cfg.embedding_api_key).embeddings.create(
        model=cfg.embedding_model, input=texts
    )
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
