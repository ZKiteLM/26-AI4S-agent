"""LLM search suggestions, with only structural validation."""

import json

from ..ai import generate


def normalize_keywords(topic: str, selected: list[str] | None) -> list[str]:
    topic = " ".join(topic.split())
    if not 2 <= len(topic) <= 255:
        raise ValueError("研究主题长度应为 2–255 个字符")
    result, seen = [topic], {topic.casefold()}
    for raw in selected or []:
        if not isinstance(raw, str):
            raise TypeError("检索词必须是文本")
        word = " ".join(raw.split())
        if not word:
            continue
        if len(word) > 120 or any(ord(char) < 32 for char in word):
            raise ValueError("单个检索词不能超过 120 个字符")
        if word.casefold() not in seen:
            result.append(word)
            seen.add(word.casefold())
    if len(result) > 10:
        raise ValueError("最多保留 10 个检索词")
    return result


def generate_keywords(topic: str) -> list[str]:
    topic = normalize_keywords(topic, None)[0]
    raw = generate(
        "你是学术论文检索助手。根据用户的研究主题，给出 5–7 个简短的检索表达，"
        "优先使用缩写、全称、同义说法和词形变化。每项都应保留原查询的研究对象及限定条件；"
        "不要从主题推测常见方法、应用或相邻方向，也不要只保留原主题中的一部分。"
        "例如 large language model agents 可改写为 LLM agents 或 language-model-based agents，"
        "不可改写为 reinforcement learning agents、multi-agent systems 或 robotics。"
        "如果无法给出足够多的同主题表达，就少给几项。"
        '仅返回 JSON 对象：{"keywords":["..."]}；不要重复原始主题。',
        json.dumps({"original_topic": topic}, ensure_ascii=False),
        max_tokens=350,
        json_mode=True,
    )
    try:
        values = json.loads(raw)["keywords"]
        if not isinstance(values, list):
            raise TypeError("Invalid keyword list")
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("模型未返回有效的检索词 JSON") from exc
    result, seen = [], {topic.casefold()}
    for value in values:
        if not isinstance(value, str):
            continue
        word = " ".join(value.split())
        if (
            2 <= len(word) <= 120
            and not any(ord(char) < 32 for char in word)
            and word.casefold() not in seen
        ):
            result.append(word)
            seen.add(word.casefold())
        if len(result) == 7:
            break
    return result
