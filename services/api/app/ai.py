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
            "options": {"temperature": 0, "num_predict": max_tokens},
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


def embed(texts: list[str]) -> list[list[float]] | None:
    cfg = settings()
    if not cfg.embedding_base_url or not cfg.embedding_model or not texts:
        return None
    response = _client(cfg.embedding_base_url, cfg.embedding_api_key).embeddings.create(
        model=cfg.embedding_model, input=texts
    )
    return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


def rerank(query: str, texts: list[str]) -> list[int]:
    cfg = settings()
    if not cfg.rerank_base_url or not cfg.rerank_model:
        return list(range(len(texts)))
    response = httpx.post(
        cfg.rerank_base_url.rstrip("/") + "/rerank",
        headers={"Authorization": f"Bearer {cfg.rerank_api_key or 'local'}"},
        json={"model": cfg.rerank_model, "query": query, "documents": texts},
        timeout=90,
    )
    response.raise_for_status()
    return [x["index"] for x in response.json()["results"]]
