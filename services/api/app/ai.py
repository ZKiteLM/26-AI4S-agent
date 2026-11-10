import json
from uuid import uuid4

import httpx
from openai import OpenAI

from .config import settings
from .model_config import effective_settings


def _client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=base_url, api_key=api_key or "local", timeout=75, max_retries=0)


def _ohmygpt_client(cfg) -> OpenAI:
    if not cfg.ohmygpt_api_key:
        raise RuntimeError("OHMYGPT_API_KEY is not configured")
    return OpenAI(
        base_url=cfg.llm_base_url,
        api_key=cfg.ohmygpt_api_key,
        timeout=75,
        max_retries=2,
    )


def _responses_input(messages: list[dict]) -> list[dict]:
    items = []
    for message in messages:
        if message["role"] == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
            )
            continue
        if message.get("content"):
            items.append({"role": message["role"], "content": message["content"]})
        for call in message.get("tool_calls") or []:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": call["function"]["name"],
                    "arguments": call["function"]["arguments"],
                }
            )
    return items


def _responses_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", **tool["function"]} for tool in tools]


def _ollama_chat(base_url: str, payload: dict, timeout: int) -> dict:
    url = base_url.removesuffix("/v1").rstrip("/") + "/api/chat"
    response = httpx.post(url, json=payload, timeout=timeout)
    if (
        payload.get("format") == "json"
        and response.status_code == 501
        and "structured output is unavailable" in response.text
    ):
        # Ollama's MLX runner supports this model but not constrained JSON output.
        messages = [dict(message) for message in payload["messages"]]
        instruction = "Return only one valid JSON object. Do not use Markdown fences or extra text."
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += "\n" + instruction
        else:
            messages.insert(0, {"role": "system", "content": instruction})
        fallback = {key: value for key, value in payload.items() if key != "format"}
        fallback["messages"] = messages
        response = httpx.post(url, json=fallback, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        content = data["message"].get("content") or ""
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise RuntimeError("Local model returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Local model returned a non-object JSON value")
        data["message"]["content"] = json.dumps(parsed, ensure_ascii=False)
        return data
    response.raise_for_status()
    return response.json()


def generate(
    system: str, prompt: str, max_tokens: int = 1200, json_mode: bool = False, *, llm_config=None
) -> str:
    cfg = llm_config or effective_settings(settings())
    if not cfg.llm_base_url or not cfg.llm_model:
        raise RuntimeError("LLM endpoint is not configured")
    if cfg.llm_provider == "ohmygpt":
        kwargs = {
            "model": cfg.llm_model,
            "input": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "max_output_tokens": max_tokens,
            "reasoning": {"effort": "none"},
            "store": False,
        }
        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}
        return _ohmygpt_client(cfg).responses.create(**kwargs).output_text
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
        try:
            return _ollama_chat(cfg.llm_base_url, payload, 75)["message"]["content"]
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code == 400
                and "exceeds the available context size" in exc.response.text
            ):
                raise RuntimeError(
                    f"Local model context is too small (OLLAMA_NUM_CTX={cfg.ollama_num_ctx}). "
                    "Reduce retrieved context or increase OLLAMA_NUM_CTX."
                ) from exc
            raise
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
    messages: list[dict],
    tools: list[dict],
    *,
    thinking: bool = False,
    json_mode: bool = False,
    max_tokens: int | None = None,
) -> dict:
    """Return one canonical assistant message, preserving native tool calls."""
    cfg = effective_settings(settings())
    if not cfg.llm_base_url or not cfg.llm_model:
        raise RuntimeError("LLM endpoint is not configured")
    if cfg.llm_provider == "ohmygpt":
        kwargs = {
            "model": cfg.llm_model,
            "input": _responses_input(messages),
            "max_output_tokens": max_tokens or (3500 if thinking else 900),
            "reasoning": {"effort": "medium" if thinking else "none"},
            "store": False,
        }
        if tools:
            kwargs["tools"] = _responses_tools(tools)
        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}
        response = _ohmygpt_client(cfg).responses.create(**kwargs)
        calls = [
            {
                "id": item.call_id,
                "type": "function",
                "function": {"name": item.name, "arguments": item.arguments},
            }
            for item in response.output
            if item.type == "function_call"
        ]
        return {"role": "assistant", "content": response.output_text, "tool_calls": calls}
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
                "num_predict": max_tokens or (3500 if thinking else 900),
                "num_ctx": cfg.ollama_num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        reply = _ollama_chat(cfg.llm_base_url, payload, 150 if thinking else 90)["message"]
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
        "max_tokens": max_tokens or (3500 if thinking else 900),
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
