"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import OperationProgress from "@/components/OperationProgress";

type Activity = {
  id: string;
  title: string;
  detail: string;
  status: string;
  progress: number | null;
  created_at: string | null;
  href: string;
  retryable: boolean;
};
const active = new Set(["queued", "running", "searching", "scoring"]);
const labels: Record<string, string> = {
  queued: "等待中",
  running: "进行中",
  searching: "检索中",
  scoring: "分析中",
  completed: "已完成",
  partial: "部分完成",
  failed: "未完成",
};

export default function Tasks() {
  const [items, setItems] = useState<Activity[]>([]);
  const [filter, setFilter] = useState<"active" | "all">("active");
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<{ items: Activity[] }>("/activity")
      .then((data) => {
        setItems(data.items);
        setError("");
      })
      .catch((cause) => setError(String(cause)));
  }, []);
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [load]);
  async function retry(id: string) {
    try {
      await api(`/tasks/${id}/retry`, { method: "POST" });
      load();
    } catch (cause) {
      setError(String(cause));
    }
  }
  const visible =
    filter === "active"
      ? items.filter((item) => active.has(item.status))
      : items;
  return (
    <section className="task-activity">
      <div className="task-activity-top">
        <div>
          <h2>任务动态</h2>
          <p className="muted">
            论文解析、科研问答、主题追踪、引用图谱和论文框架的处理进度会自动更新。
          </p>
        </div>
        <span className="badge">
          进行中 {items.filter((item) => active.has(item.status)).length}
        </span>
      </div>
      <div className="citation-mode-tabs" role="tablist" aria-label="任务筛选">
        <button
          type="button"
          role="tab"
          aria-selected={filter === "active"}
          className={filter === "active" ? "active" : ""}
          onClick={() => setFilter("active")}
        >
          进行中
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={filter === "all"}
          className={filter === "all" ? "active" : ""}
          onClick={() => setFilter("all")}
        >
          全部任务
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="task-activity-list">
        {visible.map((item) => (
          <article className="panel task-activity-card" key={item.id}>
            <div className="task-activity-top">
              <div>
                <span className={`task-state task-state-${item.status}`}>
                  {labels[item.status] || "处理中"}
                </span>
                <h3>{item.title}</h3>
              </div>
              {item.created_at && (
                <time dateTime={item.created_at}>
                  {new Date(item.created_at).toLocaleString("zh-CN")}
                </time>
              )}
            </div>
            {active.has(item.status) ? (
              <OperationProgress
                title={item.detail}
                progress={item.progress}
                startedAt={item.created_at}
              />
            ) : (
              <p className="muted">{item.detail}</p>
            )}
            <div className="task-activity-actions">
              <Link href={item.href} className="button secondary">
                查看相关页面
              </Link>
              {item.retryable && (
                <button
                  type="button"
                  className="button"
                  onClick={() => retry(item.id)}
                >
                  重新解析
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      {!visible.length && (
        <div className="panel muted">
          {filter === "active"
            ? "当前没有正在处理的任务。可切换到“全部任务”查看历史。"
            : "还没有后台任务。"}
        </div>
      )}
    </section>
  );
}
