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
  project_page_url: string | null;
  github_verification: string | null;
  citation_source: string | null;
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
  matched_keywords: Record<string, string[]>;
  summary: string;
  reason: string;
  status: string;
};
type Subscription = {
  id: string;
  topic: string;
  active: boolean;
  last_checked_at: string | null;
  next_refresh_at: string | null;
  keywords: string[];
  refresh_status: string;
  refresh_mode: string | null;
  refresh_error: string | null;
};
type KeywordChoice = { text: string; selected: boolean };
const runningStatuses = new Set(["queued", "searching", "scoring"]);
const statusLabels: Record<string, string> = {
  idle: "空闲",
  queued: "排队中",
  searching: "检索中",
  scoring: "评分中",
  completed: "已完成",
  failed: "失败",
};

function KeywordEditor({
  topic,
  choices,
  onChange,
}: {
  topic: string;
  choices: KeywordChoice[];
  onChange: (choices: KeywordChoice[]) => void;
}) {
  const [extra, setExtra] = useState("");
  function addExtra() {
    if (!extra.trim() || choices.length >= 9) return;
    onChange([...choices, { text: extra.trim(), selected: true }]);
    setExtra("");
  }
  return (
    <div className="discover-keyword-editor">
      <div className="discover-keyword-heading">
        最终检索词 <span className="muted">原始主题始终保留</span>
      </div>
      <label className="discover-keyword-item original">
        <input type="checkbox" checked readOnly disabled />
        <span>{topic}</span>
        <small>原始主题</small>
      </label>
      {choices.map((choice, index) => (
        <div className="discover-keyword-item" key={index}>
          <input
            type="checkbox"
            aria-label={`选择检索词 ${choice.text}`}
            checked={choice.selected}
            onChange={(event) =>
              onChange(
                choices.map((x, i) =>
                  i === index ? { ...x, selected: event.target.checked } : x,
                ),
              )
            }
          />
          <input
            className="input"
            aria-label={`编辑检索词 ${index + 1}`}
            value={choice.text}
            onChange={(event) =>
              onChange(
                choices.map((x, i) =>
                  i === index ? { ...x, text: event.target.value } : x,
                ),
              )
            }
          />
          <button
            className="button ghost"
            type="button"
            aria-label={`删除检索词 ${choice.text}`}
            onClick={() => onChange(choices.filter((_, i) => i !== index))}
          >
            删除
          </button>
        </div>
      ))}
      <div className="discover-keyword-add">
        <input
          className="input"
          aria-label="手动添加检索词"
          placeholder="补充等价检索表达"
          value={extra}
          onChange={(event) => setExtra(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addExtra();
            }
          }}
        />
        <button
          className="button secondary"
          type="button"
          disabled={!extra.trim() || choices.length >= 9}
          onClick={addExtra}
        >
          添加检索词
        </button>
      </div>
    </div>
  );
}
export default function Discover() {
  const [view, setView] = useState<"search" | "tracking">("tracking"),
    [query, setQuery] = useState(""),
    [found, setFound] = useState<Found[]>([]),
    [topic, setTopic] = useState(""),
    [choices, setChoices] = useState<KeywordChoice[]>([]),
    [generatedFor, setGeneratedFor] = useState(""),
    [generating, setGenerating] = useState(false),
    [editingId, setEditingId] = useState(""),
    [editChoices, setEditChoices] = useState<KeywordChoice[]>([]),
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
  useEffect(() => {
    if (
      view !== "tracking" ||
      !subs.some((sub) => runningStatuses.has(sub.refresh_status))
    )
      return;
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [load, subs, view]);
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
    setBusyAction("subscribe");
    setError("");
    try {
      await api("/subscriptions", {
        method: "POST",
        body: JSON.stringify({
          topic,
          keywords: choices.filter((x) => x.selected).map((x) => x.text),
        }),
      });
      setTopic("");
      setChoices([]);
      setGeneratedFor("");
      setMessage("已订阅，正在后台发现新论文。候选论文不会自动进入文献库。");
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction("");
    }
  }
  async function suggestKeywords() {
    setGenerating(true);
    setError("");
    try {
      const result = await api<{ keywords: string[] }>(
        "/subscriptions/keywords",
        {
          method: "POST",
          body: JSON.stringify({ topic }),
        },
      );
      setChoices(result.keywords.map((text) => ({ text, selected: true })));
      setGeneratedFor(topic.trim());
      if (!result.keywords.length)
        setMessage("模型没有返回可用的检索词，可手动补充或直接使用原始主题。");
    } catch (e) {
      setError(`生成检索词失败：${String(e)}。可直接使用原始主题创建订阅。`);
    } finally {
      setGenerating(false);
    }
  }
  function startEditing(sub: Subscription) {
    setEditingId(sub.id);
    setEditChoices(
      (sub.keywords || [])
        .filter((word) => word !== sub.topic)
        .map((text) => ({ text, selected: true })),
    );
  }
  async function saveKeywords(sub: Subscription) {
    setBusyAction(sub.id);
    setError("");
    try {
      await api(`/subscriptions/${sub.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          keywords: editChoices.filter((x) => x.selected).map((x) => x.text),
        }),
      });
      setEditingId("");
      setMessage("检索词已保存；下次刷新时生效。");
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction("");
    }
  }
  async function deleteSubscription(sub: Subscription) {
    if (!window.confirm(`删除订阅“${sub.topic}”？未入库的专属推荐将一并移除。`))
      return;
    setBusyAction(sub.id);
    setError("");
    try {
      await api(`/subscriptions/${sub.id}`, { method: "DELETE" });
      if (filterTopic === sub.topic) setFilterTopic("");
      setEditingId("");
      setMessage("订阅已删除；已入库论文不受影响。");
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyAction("");
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
      const result = await api<{ queued?: boolean }>(
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
          ? result.queued
            ? "已排入刷新队列。"
            : "刷新任务已在排队或执行中。"
          : sub.active
            ? "已暂停自动追踪；仍可手动刷新。"
            : "已恢复自动追踪。",
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
            className="panel discover-action-bar discover-tracking-form"
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
                onChange={(e) => {
                  setTopic(e.target.value);
                  setChoices([]);
                  setGeneratedFor("");
                }}
                aria-label="研究主题"
                placeholder="例如 large language model agents"
              />
              <button
                className="button secondary"
                type="button"
                disabled={topic.trim().length < 2 || generating || !!busyAction}
                onClick={() => void suggestKeywords()}
              >
                {generating ? "生成中…" : "生成检索词"}
              </button>
              <button
                className="button"
                type="submit"
                disabled={topic.trim().length < 2 || generating || !!busyAction}
              >
                创建订阅
              </button>
            </div>
            {topic.trim() && (
              <KeywordEditor
                topic={topic.trim()}
                choices={choices}
                onChange={setChoices}
              />
            )}
            {generatedFor && (
              <p className="muted discover-keyword-note">
                已生成候选检索词，请检查是否符合你的研究主题，可取消、修改或补充。
              </p>
            )}
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
                  <div className="discover-subscription-main">
                    <strong>{sub.topic}</strong>
                    <span className="muted">
                      {sub.active ? "自动追踪中" : "自动追踪已暂停"} ·{" "}
                      {sub.last_checked_at
                        ? `上次成功刷新 ${new Date(sub.last_checked_at).toLocaleString("zh-CN")}`
                        : "等待首次刷新"}
                    </span>
                    {sub.active && sub.next_refresh_at && (
                      <span className="muted">
                        {sub.last_checked_at &&
                        new Date(sub.next_refresh_at).getTime() -
                          new Date(sub.last_checked_at).getTime() <
                          6 * 24 * 60 * 60 * 1000
                          ? "下次来源补全重试 "
                          : "下次预计自动刷新 "}
                        {new Date(sub.next_refresh_at).toLocaleString("zh-CN")}
                      </span>
                    )}
                    <div
                      className="discover-keyword-chips"
                      aria-label="已选检索词"
                    >
                      {(sub.keywords || [sub.topic]).map((word) => (
                        <span key={word}>{word}</span>
                      ))}
                    </div>
                    <span
                      className={`discover-task-status ${sub.refresh_status === "failed" ? "failed" : ""}`}
                      role="status"
                    >
                      当前任务：
                      {statusLabels[sub.refresh_status] || sub.refresh_status}
                    </span>
                    {sub.refresh_error && (
                      <span className="error" role="alert">
                        {sub.refresh_error}
                      </span>
                    )}
                    {editingId === sub.id && (
                      <div className="discover-subscription-edit">
                        <KeywordEditor
                          topic={sub.topic}
                          choices={editChoices}
                          onChange={setEditChoices}
                        />
                        <div className="row">
                          <button
                            className="button"
                            type="button"
                            disabled={!!busyAction}
                            onClick={() => void saveKeywords(sub)}
                          >
                            保存检索词
                          </button>
                          <button
                            className="button ghost"
                            type="button"
                            onClick={() => setEditingId("")}
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="row discover-subscription-actions">
                    <button
                      className="button ghost"
                      type="button"
                      disabled={!!busyAction}
                      onClick={() =>
                        editingId === sub.id
                          ? setEditingId("")
                          : startEditing(sub)
                      }
                    >
                      编辑关键词
                    </button>
                    <button
                      className="button ghost"
                      disabled={
                        !!busyAction ||
                        (runningStatuses.has(sub.refresh_status) &&
                          !(
                            sub.refresh_status === "queued" &&
                            sub.refresh_mode === "auto" &&
                            !sub.active
                          ))
                      }
                      onClick={() => manageSubscription(sub, "refresh")}
                    >
                      立即刷新
                    </button>
                    <button
                      className="button secondary"
                      disabled={!!busyAction}
                      onClick={() => manageSubscription(sub, "toggle")}
                    >
                      {sub.active ? "暂停自动追踪" : "恢复自动追踪"}
                    </button>
                    <button
                      className="button ghost"
                      type="button"
                      disabled={!!busyAction}
                      onClick={() => void deleteSubscription(sub)}
                    >
                      删除订阅
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
                    {Object.entries(x.matched_keywords || {}).length > 0 && (
                      <p className="muted discover-match-keywords">
                        命中检索词：
                        {[
                          ...new Set(Object.values(x.matched_keywords).flat()),
                        ].join("、")}
                      </p>
                    )}
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
                  <span>
                    官方 GitHub Stars{" "}
                    {x.candidate.github_verification === "verified"
                      ? (x.candidate.github_stars ?? "未知")
                      : "未知"}
                  </span>
                  {x.candidate.source_url && (
                    <a
                      href={x.candidate.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      原文 ↗
                    </a>
                  )}
                  {x.candidate.project_page_url && (
                    <a
                      href={x.candidate.project_page_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      项目主页 ↗
                    </a>
                  )}
                  {x.candidate.github_url &&
                    x.candidate.github_verification === "verified" && (
                      <a
                        href={x.candidate.github_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        官方代码 ↗
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
