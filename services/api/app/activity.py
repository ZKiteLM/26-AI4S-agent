"""Turn internal jobs into a small, user-facing activity feed."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AgentRun, BackgroundTask, Paper, RunEvent, Subscription, Version


def activity_feed(db: Session, owner_id: str) -> list[dict]:
    tasks = db.scalars(
        select(BackgroundTask)
        .where(BackgroundTask.owner_id == owner_id)
        .order_by(BackgroundTask.created_at.desc())
        .limit(80)
    ).all()
    runs = db.scalars(
        select(AgentRun)
        .where(AgentRun.owner_id == owner_id)
        .order_by(AgentRun.created_at.desc())
        .limit(40)
    ).all()
    subscriptions = db.scalars(
        select(Subscription)
        .where(
            Subscription.owner_id == owner_id,
            Subscription.deleted_at.is_(None),
            Subscription.refresh_status != "idle",
        )
        .order_by(Subscription.refresh_requested_at.desc())
        .limit(40)
    ).all()
    version_ids = [task.args.get("version_id") for task in tasks if task.args]
    versions = {v.id: v for v in db.scalars(select(Version).where(Version.id.in_(version_ids)))}
    paper_ids = {v.paper_id for v in versions.values()}
    papers = {
        p.id: p
        for p in db.scalars(
            select(Paper).where(Paper.id.in_(paper_ids), Paper.owner_id == owner_id)
        )
    }
    run_ids = [run.id for run in runs]
    latest = {}
    for event in db.scalars(
        select(RunEvent).where(RunEvent.run_id.in_(run_ids)).order_by(RunEvent.sequence)
    ):
        latest[event.run_id] = event.event

    items = []
    for task in tasks:
        kind = (task.args or {}).get("kind")
        version = versions.get((task.args or {}).get("version_id"))
        paper = papers.get(version.paper_id) if version else None
        if task.type == "parse_pdf":
            title = f"解析论文 · {paper.title if paper else 'PDF 文档'}"
            detail = "正在提取页面、章节和公式" if task.progress < 0.82 else "正在建立论文检索索引"
            href = f"/papers/{paper.id}" if paper else "/library"
        elif task.type == "citation_graph":
            title, detail, href = "构建引用图谱", "正在核对论文身份与引用关系", "/graph"
        elif task.type == "graph_insight":
            title = "生成自动综述" if kind == "review" else "探索未来研究方向"
            phase = (task.result or {}).get("phase")
            detail = {
                "planning": "正在阅读论文并规划大纲",
                "writing": "正在分章节写作",
                "verifying": "正在核对论断和引用",
            }.get(phase, "正在整理图谱中的研究资料")
            href = "/graph"
        else:
            continue
        if task.status in {"completed", "partial"}:
            detail = "已完成" if task.status == "completed" else "已完成，部分引用数据暂不可用"
        elif task.status == "failed":
            detail = "处理未完成，可以重试；如持续失败请查看调试后台"
        elif task.status == "queued":
            detail = "已排队，等待后台开始"
        items.append(
            {
                "id": task.id,
                "title": title,
                "detail": detail,
                "status": task.status,
                "progress": (
                    task.progress
                    if task.status == "running"
                    and not (task.type == "graph_insight" and kind != "review")
                    else 1
                    if task.status in {"completed", "partial"}
                    else None
                ),
                "created_at": task.created_at,
                "href": href,
                "retryable": task.type == "parse_pdf" and task.status == "failed",
            }
        )
    for run in runs:
        phase = latest.get(run.id)
        detail = (
            "正在查找和核对论文证据"
            if phase in {"tool_started", "tool_completed"}
            else "正在整理回答"
        )
        if run.status == "queued":
            detail = "已排队，等待后台开始"
        elif run.status == "completed":
            detail = "回答已生成，可在会话中查看引用"
        elif run.status == "failed":
            detail = "回答未完成，可返回会话重试"
        items.append(
            {
                "id": run.id,
                "title": f"科研问答 · {run.question[:70]}",
                "detail": detail,
                "status": run.status,
                "progress": None,
                "created_at": run.created_at,
                "href": "/research",
                "retryable": False,
            }
        )
    for sub in subscriptions:
        status = sub.refresh_status
        detail = {
            "queued": "已排队，等待检索",
            "searching": "正在从学术数据库查找论文",
            "scoring": "正在分析论文与研究主题的相关性",
            "completed": "本轮追踪已完成",
            "failed": "本轮追踪未完成，可立即刷新重试",
        }.get(status, "研究主题追踪")
        items.append(
            {
                "id": f"subscription:{sub.id}",
                "title": f"追踪主题 · {sub.topic}",
                "detail": detail,
                "status": status,
                "progress": None,
                "created_at": sub.refresh_requested_at or sub.last_checked_at,
                "href": "/discover",
                "retryable": False,
            }
        )
    return sorted(
        items,
        key=lambda item: item["created_at"].timestamp() if item["created_at"] else 0,
        reverse=True,
    )[:120]
