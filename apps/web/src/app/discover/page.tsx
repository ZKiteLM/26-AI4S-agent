"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Found = {
  title: string;
  authors: string[];
  year: number | null;
  openalex_id: string | null;
  arxiv_id: string | null;
  pdf_url: string | null;
  doi: string | null;
  source: string;
  abstract: string;
  source_url: string | null;
  citation_count: number | null;
  github_url: string | null;
  github_stars: number | null;
};
type Rec = {
  id: string;
  candidate: Found;
  paper_id: string | null;
  score: number | null;
  scoring_factors: Record<string, number | null>;
  scoring_weights: Record<string, number>;
  feedback: number;
  topics: string[];
  summary: string;
  reason: string;
  status: string;
};
type Subscription = {
  id: string;
  topic: string;
  active: boolean;
  last_checked_at: string | null;
};
export default function Discover() {
  const [view, setView] = useState<"search" | "tracking">("tracking"),
    [query, setQuery] = useState(""),
    [found, setFound] = useState<Found[]>([]),
    [topic, setTopic] = useState(""),
    [recs, setRecs] = useState<Rec[]>([]),
    [subs, setSubs] = useState<Subscription[]>([]),
    [busy, setBusy] = useState(false),
    [busyAction, setBusyAction] = useState(""),
    [filterTopic, setFilterTopic] = useState(""),
    [status, setStatus] = useState("pending"),
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const load = useCallback(() => {
    api<{ items: Rec[]; total: number }>(
      `/recommendations?status=${status}&page=${page}&topic=${encodeURIComponent(filterTopic)}`,
    )
      .then((x) => {
        setRecs(x.items);
        setTotal(x.total);
      })
      .catch((e) => setError(String(e)));
    api<{ items: Subscription[] }>("/subscriptions")
      .then((x) => setSubs(x.items))
      .catch((e) => setError(String(e)));
  }, [status, page, filterTopic]);
  useEffect(() => {
    load();
  }, [load]);
  async function search() {
    setBusy(true);
    setError("");
    try {
      const x = await api<{ papers: Found[] }>("/discovery/search", {
        method: "POST",
        body: JSON.stringify({ query }),
      });
      setFound(x.papers);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function importOne(item: Found, recommendationId?: string) {
    setBusyAction(recommendationId || item.title);
    setMessage("");
    try {
      const x = await api<{ status: string }>(
        recommendationId
          ? `/recommendations/${recommendationId}/import`
          : "/discovery/import",
        {
          method: "POST",
          body: JSON.stringify(
            recommendationId
              ? {}
              : {
                  openalex_id: item.openalex_id,
                  arxiv_id: item.arxiv_id,
                },
          ),
        },
      );
      setMessage(
        x.status === "metadata_only"
          ? "已入库，当前只有元数据。"
          : x.status === "queued"
            ? "已入库，PDF 正在解析。"
            : "论文已在文献库中。",
      );
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction("");
    }
  }
  async function subscribe() {
    try {
      await api("/subscriptions", {
        method: "POST",
        body: JSON.stringify({ topic }),
      });
      setTopic("");
      setMessage("已订阅，正在后台发现新论文。候选论文不会自动进入文献库。");
      load();
    } catch (e) {
      setError(String(e));
    }
  }
  async function feedback(id: string, value: number) {
    setBusyAction(id);
    try {
      await api(`/recommendations/${id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ value }),
      });
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction("");
    }
  }
  async function manageSubscription(
    sub: Subscription,
    action: "toggle" | "refresh",
  ) {
    setBusyAction(sub.id);
    setError("");
    try {
      await api(
        `/subscriptions/${sub.id}${action === "refresh" ? "/refresh" : ""}`,
        {
          method: action === "refresh" ? "POST" : "PATCH",
          body: JSON.stringify(
            action === "refresh" ? {} : { active: !sub.active },
          ),
        },
      );
      setMessage(
        action === "refresh"
          ? "已排入刷新队列。"
          : sub.active
            ? "已暂停追踪。"
            : "已恢复追踪，正在刷新。",
      );
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction("");
    }
  }
  return (
    <>
      <div className="discover-tabs" role="tablist" aria-label="发现论文功能">
        <button
          type="button"
          role="tab"
          id="discover-tab-search"
          aria-controls="discover-panel-search"
          aria-selected={view === "search"}
          className={view === "search" ? "discover-tab active" : "discover-tab"}
          onClick={() => setView("search")}
        >
          检索论文
        </button>
        <button
          type="button"
          role="tab"
          id="discover-tab-tracking"
          aria-controls="discover-panel-tracking"
          aria-selected={view === "tracking"}
          className={
            view === "tracking" ? "discover-tab active" : "discover-tab"
          }
          onClick={() => setView("tracking")}
        >
          研究主题追踪
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="success" role="status">
          {message}
        </p>
      )}
      {view === "search" && (
        <section
          id="discover-panel-search"
          role="tabpanel"
          aria-labelledby="discover-tab-search"
        >
          <form
            className="panel discover-action-bar"
            onSubmit={(event) => {
              event.preventDefault();
              void search();
            }}
          >
            <h2>检索论文</h2>
            <div className="discover-action-controls">
              <input
                className="input"
                aria-label="论文检索词"
                placeholder="论文标题、作者或研究关键词"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <button
                className="button"
                type="submit"
                disabled={busy || query.length < 2}
              >
                {busy ? "检索中…" : "检索"}
              </button>
            </div>
          </form>
          <div className="list discover-search-results">
            {found.map((x) => (
              <div className="card" key={x.openalex_id || x.arxiv_id}>
                <div className="section-label">
                  {x.source} · {x.year || "年份未知"}
                </div>
                <h2>{x.title}</h2>
                <p className="muted">{x.authors.slice(0, 5).join(", ")}</p>
                <div className="row">
                  <span className="badge">
                    {x.pdf_url ? "有开放 PDF" : "仅元数据"}
                  </span>
                  <button
                    className="button secondary"
                    disabled={!!busyAction}
                    onClick={() => importOne(x)}
                  >
                    导入文献库
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      {view === "tracking" && (
        <section
          id="discover-panel-tracking"
          role="tabpanel"
          aria-labelledby="discover-tab-tracking"
        >
          <form
            className="panel discover-action-bar"
            onSubmit={(event) => {
              event.preventDefault();
              void subscribe();
            }}
          >
            <h2>研究主题追踪</h2>
            <div className="discover-action-controls">
              <input
                className="input"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                aria-label="研究主题"
                placeholder="例如 large language model agents"
              />
              <button
                className="button"
                type="submit"
                disabled={topic.length < 2}
              >
                添加订阅
              </button>
            </div>
          </form>
          <div className="panel discover-subscriptions-panel">
            <div className="discover-section-caption">
              <h3>已订阅主题</h3>
              <span>{subs.length}</span>
            </div>
            <div className="discover-subscriptions">
              {subs.length === 0 && <p className="muted">暂无订阅</p>}
              {subs.map((sub) => (
                <div className="discover-subscription" key={sub.id}>
                  <div>
                    <strong>{sub.topic}</strong>
                    <span className="muted">
                      {sub.active ? "追踪中" : "已暂停"} ·{" "}
                      {sub.last_checked_at
                        ? `上次刷新 ${new Date(sub.last_checked_at).toLocaleString("zh-CN")}`
                        : "等待首次刷新"}
                    </span>
                  </div>
                  <div className="row">
                    <button
                      className="button ghost"
                      disabled={!!busyAction || !sub.active}
                      onClick={() => manageSubscription(sub, "refresh")}
                    >
                      立即刷新
                    </button>
                    <button
                      className="button secondary"
                      disabled={!!busyAction}
                      onClick={() => manageSubscription(sub, "toggle")}
                    >
                      {sub.active ? "暂停" : "恢复"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="discover-heading">
            <h2>智能论文推荐</h2>
            <span className="discover-count">{total} 篇</span>
          </div>
          <div className="discover-filters">
            <label className="discover-topic-filter">
              <span>订阅主题</span>
              <select
                className="input"
                value={filterTopic}
                onChange={(e) => {
                  setFilterTopic(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部主题</option>
                {subs.map((s) => (
                  <option key={s.id} value={s.topic}>
                    {s.topic}
                  </option>
                ))}
              </select>
            </label>
            <div
              className="discover-status-filter"
              role="group"
              aria-label="推荐状态"
            >
              {(
                [
                  ["pending", "待处理"],
                  ["imported", "已入库"],
                  ["dismissed", "不相关"],
                  ["all", "全部"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={
                    status === value
                      ? "discover-filter-button active"
                      : "discover-filter-button"
                  }
                  aria-pressed={status === value}
                  onClick={() => {
                    setStatus(value);
                    setPage(1);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {!recs.length && (
            <p className="panel muted">
              暂无符合条件的推荐。新订阅完成后台检索后会在这里显示。
            </p>
          )}
          <div className="discover-rec-list">
            {recs.map((x) => (
              <article className="card discover-rec" key={x.id}>
                <div className="discover-rec-top">
                  <div>
                    <div className="section-label">
                      {x.candidate.source} · {x.candidate.year || "年份未知"} ·{" "}
                      {x.topics.join(" / ")}
                    </div>
                    <h2>{x.candidate.title}</h2>
                    <p className="muted">
                      {x.candidate.authors.slice(0, 6).join("、")}
                    </p>
                  </div>
                  <div className="discover-score">
                    <strong>
                      {x.score == null ? "待评分" : x.score.toFixed(0)}
                    </strong>
                    {x.score != null && <small>/ 100</small>}
                  </div>
                </div>
                {x.summary ? (
                  <p className="discover-summary">{x.summary}</p>
                ) : (
                  <p className="muted">
                    AI 分析暂不可用；已知客观指标仍可在评分依据中查看。
                  </p>
                )}
                {x.reason && (
                  <p className="discover-reason">
                    <strong>推荐理由：</strong>
                    {x.reason}
                  </p>
                )}
                <details className="discover-details">
                  <summary>完整摘要与评分依据</summary>
                  <p>{x.candidate.abstract || "暂无摘要"}</p>
                  <div className="discover-factors">
                    {Object.entries({
                      relevance: "相关性",
                      value: "研究价值",
                      citations: "引用影响",
                      github_stars: "代码热度",
                      recency: "时效性",
                    }).map(([key, label]) => (
                      <span key={key}>
                        {label}：
                        {x.scoring_factors[key] == null
                          ? "未知"
                          : `${x.scoring_factors[key].toFixed(0)}/100（权重 ${x.scoring_weights[key] ?? 0}%）`}
                      </span>
                    ))}
                  </div>
                </details>
                <div className="row discover-metrics">
                  <span>引用 {x.candidate.citation_count ?? "未知"}</span>
                  <span>GitHub Stars {x.candidate.github_stars ?? "未知"}</span>
                  {x.candidate.source_url && (
                    <a
                      href={x.candidate.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      原文 ↗
                    </a>
                  )}
                  {x.candidate.github_url && (
                    <a
                      href={x.candidate.github_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      代码 ↗
                    </a>
                  )}
                </div>
                <div className="row">
                  {x.status === "pending" && (
                    <>
                      <button
                        className="button"
                        disabled={!!busyAction}
                        onClick={() => importOne(x.candidate, x.id)}
                      >
                        入库
                      </button>
                      <button
                        className="button secondary"
                        disabled={!!busyAction || x.feedback > 0}
                        onClick={() => feedback(x.id, 1)}
                      >
                        {x.feedback > 0 ? "已标记有帮助" : "有帮助"}
                      </button>
                      <button
                        className="button ghost"
                        disabled={!!busyAction}
                        onClick={() => feedback(x.id, -1)}
                      >
                        不相关
                      </button>
                    </>
                  )}
                  {x.status === "imported" && (
                    <span className="badge">已入库</span>
                  )}
                  {x.status === "dismissed" && (
                    <span className="badge">已标记不相关</span>
                  )}
                </div>
              </article>
            ))}
          </div>
          {total > 12 && (
            <nav className="row discover-pagination" aria-label="推荐分页">
              <button
                className="button secondary"
                disabled={page === 1}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </button>
              <span>
                {page} / {Math.ceil(total / 12)}
              </span>
              <button
                className="button secondary"
                disabled={page * 12 >= total}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </button>
            </nav>
          )}
        </section>
      )}
    </>
  );
}
