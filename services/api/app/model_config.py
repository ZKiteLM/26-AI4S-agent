"""User-selected LLM endpoints shared by API requests and Celery workers."""

import base64
import hashlib
import ipaddress
import socket
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from .config import settings
from .db import SessionLocal
from .models import UserModelConfig, now

_owner: ContextVar[str | None] = ContextVar("llm_owner", default=None)
_cached: ContextVar[object | None] = ContextVar("llm_config", default=None)


@contextmanager
def model_for_user(owner_id: str | None):
    owner_token = _owner.set(owner_id)
    cache_token = _cached.set(None)
    try:
        yield
    finally:
        _cached.reset(cache_token)
        _owner.reset(owner_token)


def _cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings().app_secret.encode()).digest())
    return Fernet(key)


def encrypt_key(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode()


def decrypt_key(value: str | None) -> str:
    return _cipher().decrypt(value.encode()).decode() if value else ""


def validate_endpoint(provider: str, base_url: str, model: str) -> str:
    if provider not in {"openai", "ohmygpt", "ollama"}:
        raise ValueError("请选择支持的接口类型")
    if not model.strip() or len(model) > 255 or not base_url or len(base_url) > 500:
        raise ValueError("请填写模型名称和接口地址")
    try:
        parsed = urlsplit(base_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("接口地址格式不正确") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("接口地址必须是完整的 HTTP(S) URL，且不能包含账号或查询参数")
    local = parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    if local:
        if settings().deployment_mode != "local":
            raise ValueError("公网部署不能连接本机模型地址")
    else:
        if parsed.scheme != "https":
            raise ValueError("远程模型接口必须使用 HTTPS")
        try:
            addresses = socket.getaddrinfo(parsed.hostname, port or 443)
        except OSError as exc:
            raise ValueError("模型接口域名无法解析") from exc
        fake_ip_range = ipaddress.ip_network("198.18.0.0/15")
        if not addresses or any(
            not (
                address.is_global
                or settings().deployment_mode == "local"
                and address in fake_ip_range
            )
            for item in addresses
            for address in [ipaddress.ip_address(item[4][0])]
        ):
            raise ValueError("远程模型接口不能指向内网地址")
    if provider == "ollama" and not local:
        raise ValueError("本地 Ollama 仅支持本机地址")
    return base_url.strip().rstrip("/")


def effective_settings(default=None):
    owner_id = _owner.get()
    if not owner_id:
        return default or settings()
    cached = _cached.get()
    if cached is not None:
        return cached
    with SessionLocal() as db:
        row = db.get(UserModelConfig, owner_id)
        if row:
            key = decrypt_key(row.api_key_ciphertext)
            result = settings().model_copy(
                update={
                    "llm_provider": row.provider,
                    "llm_base_url": row.base_url,
                    "llm_model": row.model,
                    "llm_api_key": key,
                    "ohmygpt_api_key": key,
                }
            )
        else:
            result = default or settings()
    _cached.set(result)
    return result


def save_user_config(owner_id: str, provider: str, base_url: str, model: str, api_key: str) -> None:
    with SessionLocal() as db:
        row = db.get(UserModelConfig, owner_id)
        same_endpoint = bool(row and row.provider == provider and row.base_url == base_url)
        if row is None:
            row = UserModelConfig(owner_id=owner_id)
            db.add(row)
        row.provider = provider
        row.base_url = base_url
        row.model = model
        if api_key:
            row.api_key_ciphertext = encrypt_key(api_key)
        elif not same_endpoint or provider == "ollama":
            row.api_key_ciphertext = None
        row.updated_at = now()
        db.commit()
