"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Database, RefreshCw, Server, Terminal } from "lucide-react";
import { api } from "@/lib/api";

type Event = {
  id: string;
  time: string;
  source: string;
  level: string;
  message: string;
  details: string;
};
type Task = {
  id: string;
  type: string;
  status: string;
  progress: number;
  error: string | null;
  paper_id?: string;
  paper_title?: string;
  version_status?: string;
};
type Overview = {
  database: {
    backend: string;
    available: boolean;
    papers?: number;
    versions?: number;
    blocks?: number;
    postgres?: Record<string, number>;
    error?: string;
  };
  broker: {
    available: boolean;
    parser_queue: number | null;
    default_queue: number | null;
    workers: { name: string; queues: string[] }[];
    error?: string;
  };
  tasks: Task[];
  events: Event[];
};

const time = (value: string) =>
  new Date(value).toLocaleTimeString("zh-CN", { hour12: false });

export default function Debug() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [key, setKey] = useState(() =>
    typeof window === "undefined"
      ? ""
      : sessionStorage.getItem("research-debug-key") || "",
  );
  const [source, setSource] = useState("all");
  const [query, setQuery] = useState("");
  const [auto, setAuto] = useState(true);
  const [updated, setUpdated] = useState("");
  const load = useCallback(async () => {
    try {
      const result = await api<Overview>("/debug/overview", {
        headers: key ? { "X-Debug-Key": key } : {},
      });
      setOverview(result);
      setUpdated(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
      setError("");
    } catch (cause) {
      setError(String(cause));
    }
  }, [key]);
  useEffect(() => {
    const first = setTimeout(load, 0);
    if (!auto) return () => clearTimeout(first);
    const timer = setInterval(load, 4000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [auto, load]);
  const events = useMemo(
    () =>
      (overview?.events || []).filter(
        (entry) =>
          (source === "all" || entry.source === source) &&
          (!query ||
            `${entry.message} ${entry.details}`
              .toLowerCase()
              .includes(query.toLowerCase())),
      ),
    [overview, source, query],
  );
  const parserWorkers =
    overview?.broker.workers.filter((worker) =>
      worker.queues.includes("parser"),
    ) || [];
  return (
    <>
      <div className="workspace-hero compact-actions">
        <div className="row">
          <span className="muted">{updated && `更新于 ${updated}`}</span>
          <button className="button secondary" onClick={load}>
            <RefreshCw size={16} /> 刷新
          </button>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {error.includes("Debug console key") && (
        <div className="panel row">
          <label htmlFor="debug-key">调试密钥</label>
          <input
            id="debug-key"
            className="input"
            type="password"
            value={key}
            onChange={(event) => setKey(event.target.value)}
          />
          <button
            className="button"
            onClick={() => {
              sessionStorage.setItem("research-debug-key", key);
              load();
            }}
          >
            连接
          </button>
        </div>
      )}
      <div className="debug-stats">
        <div className="panel debug-stat">
          <Database size={19} />
          <span>数据库</span>
          <strong>
            {overview?.database.available ? "运行正常" : "不可用"}
          </strong>
          <small>
            {overview?.database.backend || "等待数据"} ·{" "}
            {overview?.database.blocks ?? "—"} 个内容块
          </small>
        </div>
        <div className="panel debug-stat">
          <Server size={19} />
          <span>解析 Worker</span>
          <strong>
            {parserWorkers.length
              ? `${parserWorkers.length} 个在线`
              : "未检测到"}
          </strong>
          <small>
            parser 队列 {overview?.broker.parser_queue ?? "—"} 个待处理
          </small>
        </div>
        <div className="panel debug-stat">
          <Activity size={19} />
          <span>任务</span>
          <strong>
            {overview?.tasks.filter((task) => task.status === "running")
              .length ?? "—"}{" "}
            个运行中
          </strong>
          <small>
            {overview?.tasks.filter((task) => task.status === "failed")
              .length ?? "—"}{" "}
            个近期失败
          </small>
        </div>
        <div className="panel debug-stat">
          <Terminal size={19} />
          <span>事件</span>
          <strong>{overview?.events.length ?? "—"} 条</strong>
          <small>保留最近 3000 条</small>
        </div>
      </div>
      {!parserWorkers.length && overview?.broker.available && (
        <div className="error">
          parser 队列没有在线 Worker。新上传 PDF
          的目录会持续处于等待状态。请启动解析 Worker。
        </div>
      )}
      <div className="debug-grid">
        <section className="panel debug-log-panel">
          <div className="toolbar">
            <div>
              <div className="section-label">LIVE ACTIVITY</div>
              <h2>事件日志</h2>
            </div>
            <label className="row muted">
              <input
                type="checkbox"
                checked={auto}
                onChange={(event) => setAuto(event.target.checked)}
              />{" "}
              自动刷新
            </label>
          </div>
          <div className="debug-filters">
            <select
              className="select"
              value={source}
              onChange={(event) => setSource(event.target.value)}
              aria-label="日志来源"
            >
              <option value="all">全部来源</option>
              <option value="api">API</option>
              <option value="backend">后台日志</option>
              <option value="parser">解析 Worker</option>
              <option value="worker">科研 Worker</option>
              <option value="database">数据库</option>
            </select>
            <input
              className="input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索日志内容"
              aria-label="搜索日志"
            />
          </div>
          <div className="debug-logs" role="log" aria-live="off">
            {events.map((entry) => (
              <div className="debug-log" key={entry.id}>
                <time>{time(entry.time)}</time>
                <span className={`debug-source ${entry.source}`}>
                  {entry.source}
                </span>
                <span className={`debug-level ${entry.level.toLowerCase()}`}>
                  {entry.level}
                </span>
                <div>
                  <strong>{entry.message}</strong>
                  {entry.details !== "{}" && <code>{entry.details}</code>}
                </div>
              </div>
            ))}
            {!events.length && <p className="muted">暂无匹配的日志。</p>}
          </div>
        </section>
        <div className="debug-side">
          <section className="panel">
            <div className="section-label">QUEUE & WORKERS</div>
            <h2>服务状态</h2>
            <div className="debug-kv">
              <span>Redis</span>
              <strong>
                {overview?.broker.available ? "已连接" : "不可用"}
              </strong>
              <span>解析队列</span>
              <strong>{overview?.broker.parser_queue ?? "—"}</strong>
              <span>默认队列</span>
              <strong>{overview?.broker.default_queue ?? "—"}</strong>
              <span>数据库论文 / 版本</span>
              <strong>
                {overview?.database.papers ?? "—"} /{" "}
                {overview?.database.versions ?? "—"}
              </strong>
            </div>
            {overview?.broker.workers.map((worker) => (
              <p className="debug-worker" key={worker.name}>
                {worker.name}
                <small>{worker.queues.join(", ")}</small>
              </p>
            ))}
            {overview?.database.postgres && (
              <div className="debug-db-metrics">
                <h3>PostgreSQL 统计</h3>
                {Object.entries(overview.database.postgres).map(
                  ([name, value]) => (
                    <div className="debug-kv" key={name}>
                      <span>{name}</span>
                      <strong>{value}</strong>
                    </div>
                  ),
                )}
              </div>
            )}
          </section>
          <section className="panel">
            <div className="section-label">RECENT JOBS</div>
            <h2>后台任务</h2>
            <div className="debug-tasks">
              {overview?.tasks.map((task) => (
                <div className="debug-task" key={task.id}>
                  <div className="row">
                    <span className="badge">{task.status}</span>
                    <small>{Math.round(task.progress * 100)}%</small>
                  </div>
                  <strong>{task.paper_title || task.type}</strong>
                  {task.paper_id && (
                    <Link href={`/papers/${task.paper_id}`}>查看论文 →</Link>
                  )}
                  {task.error && <p className="error">{task.error}</p>}
                </div>
              ))}
              {!overview?.tasks.length && <p className="muted">暂无任务。</p>}
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
