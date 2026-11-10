"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import PaperPicker from "@/components/PaperPicker";
import CitationGraph, {
  CitationNetwork,
  FetchState,
} from "@/components/CitationGraph";
import MathMarkdown from "@/components/MathMarkdown";
import FutureIdeaCards from "@/components/FutureIdeaCards";
import OperationProgress from "@/components/OperationProgress";

type Direction = "both" | "references" | "citations";
type Mode = "internal" | "explore";
const externalIdExamples = {
  doi: "例如：10.1038/nature12373（可选）",
  arxiv: "例如：1706.03762（可选）",
  openalex: "例如：W2741809807（可选）",
};
type GraphSummary = {
  id: string;
  status: string;
  updated_at: string;
  node_count: number;
  mode: Mode;
};
type Task = {
  status: string;
  error: string | null;
  progress: number;
  result?: { phase?: string };
};
type ReviewResearch = {
  task_id: string;
  status: string;
  phase: string;
  outline: {
    sections?: {
      id: string;
      title: string;
      description: string;
      evidence_ids: string[];
    }[];
  };
  sections: { id: string; status: string }[];
  evidence_count: number;
  paper_count: number;
  evidence: {
    id: string;
    work_id: string;
    title: string;
    source_type: string;
    page_index: number | null;
    summary: string;
    quote: string;
  }[];
  coverage: { gaps?: string[] };
  error: string | null;
};
type InsightSource = {
  work_id: string;
  paper_id: string | null;
  title: string;
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  openalex_id: string | null;
  score: number;
};
type Insight = {
  content: string;
  sources: InsightSource[];
  graph_version: number;
  stale: boolean;
  updated_at: string;
};

function statusLabel(state: FetchState) {
  if (state.status === "not_fetched") return "尚未查询";
  if (state.status === "unresolved") return "身份待核验";
  if (state.status === "failed")
    return `查询失败${state.error ? `：${state.error}` : ""}`;
  if (state.status === "partial")
    return `已获取 ${state.fetched_count} 条 · 还有更多`;
  return `已获取 ${state.fetched_count} 条 · 本次查询完成`;
}

export default function GraphPage() {
  const [mode, setMode] = useState<Mode>("internal");
  const [selected, setSelected] = useState<string[]>([]);
  const [externalType, setExternalType] = useState<
    "doi" | "arxiv" | "openalex"
  >("doi");
  const [externalValue, setExternalValue] = useState("");
  const [direction, setDirection] = useState<Direction>("both");
  const [limit, setLimit] = useState(20);
  const [graph, setGraph] = useState<CitationNetwork | null>(null);
  const [graphs, setGraphs] = useState<GraphSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [graphProgress, setGraphProgress] = useState<number | null>(null);
  const [resetLayout, setResetLayout] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [insights, setInsights] = useState<{
    review?: Insight;
    future?: Insight;
  }>({});
  const [insightTaskId, setInsightTaskId] = useState<string | null>(null);
  const [insightProgress, setInsightProgress] = useState<number | null>(null);
  const [insightKind, setInsightKind] = useState<"review" | "future" | null>(
    null,
  );
  const [insightError, setInsightError] = useState("");
  const [reviewResearch, setReviewResearch] = useState<ReviewResearch | null>(
    null,
  );
  const [notice, setNotice] = useState("");

  const loadGraphs = useCallback(() => {
    api<{ items: GraphSummary[] }>("/graphs")
      .then((response) => setGraphs(response.items))
      .catch(() => {});
  }, []);
  useEffect(() => {
    loadGraphs();
  }, [loadGraphs]);

  const loadInsights = useCallback((id: string) => {
    api<{ review?: Insight; future?: Insight }>(`/graphs/${id}/insights`)
      .then(setInsights)
      .catch((cause) => setInsightError(String(cause)));
    api<{ run: ReviewResearch | null }>(
      `/graphs/${id}/insights/review/research`,
    )
      .then((response) => {
        setReviewResearch(response.run);
        if (
          response.run &&
          ["queued", "running"].includes(response.run.status)
        ) {
          setInsightTaskId(response.run.task_id);
          setInsightKind("review");
        }
      })
      .catch(() => setReviewResearch(null));
  }, []);

  const loadGraph = useCallback(
    (id: string) => {
      api<CitationNetwork>(`/graphs/${id}`)
        .then((response) => {
          setGraph(response);
          setMode(response.mode);
          loadInsights(id);
          setSelectedId((current) =>
            current && response.nodes.some((n) => n.id === current)
              ? current
              : (response.nodes[0]?.id ?? null),
          );
        })
        .catch((cause) => setError(String(cause)));
    },
    [loadInsights],
  );

  const graphId = graph?.graph_id;
  useEffect(() => {
    if (!taskId || !graphId) return;
    const timer = window.setInterval(async () => {
      try {
        const task = await api<Task>(`/tasks/${taskId}`);
        setGraphProgress(task.status === "running" ? task.progress : null);
        if (["completed", "partial", "failed"].includes(task.status)) {
          window.clearInterval(timer);
          if (task.error) setError(task.error);
          setTaskId(null);
          setGraphProgress(null);
          setBusy(false);
          loadGraph(graphId);
          loadGraphs();
        }
      } catch (cause) {
        window.clearInterval(timer);
        setTaskId(null);
        setBusy(false);
        setError(String(cause));
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, [taskId, graphId, loadGraph, loadGraphs]);

  useEffect(() => {
    if (!insightTaskId || !graphId) return;
    const timer = window.setInterval(async () => {
      try {
        const task = await api<Task>(`/tasks/${insightTaskId}`);
        setInsightProgress(
          task.status === "running" && insightKind === "review"
            ? task.progress
            : null,
        );
        if (insightKind === "review") {
          api<{ run: ReviewResearch | null }>(
            `/graphs/${graphId}/insights/review/research`,
          )
            .then((response) => setReviewResearch(response.run))
            .catch(() => {});
        }
        if (["completed", "failed"].includes(task.status)) {
          window.clearInterval(timer);
          setInsightTaskId(null);
          setInsightProgress(null);
          setInsightKind(null);
          if (task.error) setInsightError(task.error);
          else loadInsights(graphId);
        }
      } catch (cause) {
        window.clearInterval(timer);
        setInsightTaskId(null);
        setInsightKind(null);
        setInsightError(String(cause));
      }
    }, 1800);
    return () => window.clearInterval(timer);
  }, [insightTaskId, graphId, insightKind, loadInsights]);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const response = await api<{ graph_id: string; task_id: string }>(
        "/graphs",
        {
          method: "POST",
          body: JSON.stringify({
            mode,
            paper_ids: selected,
            external_seeds:
              mode === "explore" && externalValue.trim()
                ? [{ type: externalType, value: externalValue.trim() }]
                : [],
            direction,
            per_direction_limit: limit,
          }),
        },
      );
      setSelectedId(null);
      setGraph({
        graph_id: response.graph_id,
        mode,
        status: "queued",
        version: 0,
        error: null,
        truncated: false,
        nodes: [],
        edges: [],
        metrics: {},
      });
      setTaskId(response.task_id);
      setGraphProgress(null);
      loadGraph(response.graph_id);
      loadGraphs();
    } catch (cause) {
      setError(String(cause));
      setBusy(false);
    }
  }

  async function expand(
    workId: string,
    force = false,
    chosen: Direction = direction,
  ) {
    if (!graph || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await api<{ task_id: string }>(
        `/graphs/${graph.graph_id}/${force ? "refresh" : "expand"}`,
        {
          method: "POST",
          body: JSON.stringify({
            work_id: workId,
            direction: chosen,
            per_direction_limit: limit,
          }),
        },
      );
      setTaskId(response.task_id);
      setGraphProgress(null);
    } catch (cause) {
      setError(String(cause));
      setBusy(false);
    }
  }

  async function importNode(workId: string) {
    if (!graph || busy) return;
    setBusy(true);
    setError("");
    try {
      await api(`/graphs/${graph.graph_id}/nodes/${workId}/import`, {
        method: "POST",
      });
      loadGraph(graph.graph_id);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function createInsight(kind: "review" | "future") {
    if (!graph || insightTaskId) return;
    setInsightKind(kind);
    setInsightError("");
    try {
      const response = await api<{ task_id: string }>(
        `/graphs/${graph.graph_id}/insights/${kind}`,
        { method: "POST", progress: "inline" },
      );
      setInsightTaskId(response.task_id);
      setInsightProgress(null);
    } catch (cause) {
      setInsightError(String(cause));
      setInsightKind(null);
    }
  }

  async function removeGraph() {
    if (!graph || busy || insightTaskId) return;
    if (
      !window.confirm(
        "删除当前图谱及其自动综述、未来方向？文献库中的论文不会删除。",
      )
    )
      return;
    setError("");
    try {
      await api(`/graphs/${graph.graph_id}`, { method: "DELETE" });
      setGraph(null);
      setSelectedId(null);
      setInsights({});
      setTaskId(null);
      loadGraphs();
      setNotice("图谱已删除");
    } catch (cause) {
      setError(String(cause));
    }
  }

  const node = graph?.nodes.find((item) => item.id === selectedId);
  const externalLink = node?.doi
    ? `https://doi.org/${node.doi}`
    : node?.arxiv_id
      ? `https://arxiv.org/abs/${node.arxiv_id}`
      : node?.openalex_id
        ? `https://openalex.org/${node.openalex_id}`
        : null;

  return (
    <div className="citation-page">
      <div className="citation-mode-tabs" role="tablist" aria-label="图谱类型">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "internal"}
          className={mode === "internal" ? "active" : ""}
          onClick={() => setMode("internal")}
        >
          库内关系分析
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "explore"}
          className={mode === "explore" ? "active" : ""}
          onClick={() => setMode("explore")}
        >
          扩展引用探索
        </button>
      </div>
      <p className="muted citation-mode-description">
        {mode === "internal"
          ? "只显示所选文献之间可核验的引用关系，比较这批论文在内部网络中的位置。"
          : "从库内论文或外部标识出发，继续查找参考文献和后续引用论文。"}
      </p>
      <div className="citation-seeds">
        <PaperPicker
          selected={selected}
          onChange={setSelected}
          searchable
          bulkSelect
        />
        <div className="panel citation-setup">
          {mode === "explore" && (
            <label>
              外部种子
              <div className="citation-external-row">
                <select
                  value={externalType}
                  onChange={(event) =>
                    setExternalType(event.target.value as typeof externalType)
                  }
                >
                  <option value="doi">DOI</option>
                  <option value="arxiv">arXiv</option>
                  <option value="openalex">OpenAlex</option>
                </select>
                <input
                  value={externalValue}
                  onChange={(event) => setExternalValue(event.target.value)}
                  placeholder={externalIdExamples[externalType]}
                />
              </div>
            </label>
          )}
          {mode === "explore" && (
            <div className="citation-options">
              <label>
                探索方向
                <select
                  value={direction}
                  onChange={(event) =>
                    setDirection(event.target.value as Direction)
                  }
                >
                  <option value="both">引用与被引</option>
                  <option value="references">该论文引用了谁</option>
                  <option value="citations">谁引用了该论文</option>
                </select>
              </label>
              <label>
                每个方向新增上限
                <select
                  value={limit}
                  onChange={(event) => setLimit(Number(event.target.value))}
                >
                  <option value={10}>10 篇</option>
                  <option value={20}>20 篇</option>
                  <option value={50}>50 篇</option>
                </select>
              </label>
            </div>
          )}
          <button
            className="button"
            disabled={
              busy ||
              (mode === "internal"
                ? selected.length < 2
                : !selected.length && !externalValue.trim())
            }
            onClick={create}
          >
            {busy && !graph?.nodes.length
              ? "构建中…"
              : mode === "internal"
                ? "分析库内引用关系"
                : "构建探索图谱"}
          </button>
          <p className="muted">
            {mode === "internal"
              ? "选择至少 2 篇库内论文；只分析这些论文之间的引用，不加入外部节点。"
              : "至少选一篇库内论文，或输入外部论文标识。外部节点不会自动入库。"}
          </p>
          {taskId && (
            <OperationProgress
              title="正在构建引用图谱"
              detail="正在核对论文身份、获取引用数据并计算图谱。"
              progress={graphProgress}
            />
          )}
        </div>
      </div>

      {graphs.length > 0 && (
        <div className="citation-history">
          <label>
            已有图谱{" "}
            <select
              value={graph?.graph_id ?? ""}
              onChange={(event) => {
                setTaskId(null);
                setBusy(false);
                setInsightTaskId(null);
                setInsightKind(null);
                setInsightError("");
                if (event.target.value) loadGraph(event.target.value);
                else {
                  setGraph(null);
                  setInsights({});
                }
              }}
            >
              <option value="">选择已保存的图谱</option>
              {graphs.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.mode === "internal" ? "库内分析" : "扩展探索"} ·{" "}
                  {new Date(item.updated_at).toLocaleString()} ·{" "}
                  {item.node_count} 篇 · {item.status}
                </option>
              ))}
            </select>
          </label>
          {graph && (
            <button
              className="button ghost citation-delete"
              disabled={busy || Boolean(insightTaskId)}
              onClick={removeGraph}
            >
              删除当前图谱
            </button>
          )}
        </div>
      )}

      {notice && (
        <p className="muted" role="status">
          {notice}
        </p>
      )}

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {graph && (
        <>
          <div className="citation-toolbar">
            <span>
              {graph.mode === "internal" ? "库内关系" : "扩展探索"} ·{" "}
              {graph.nodes.length} 篇论文 · {graph.edges.length} 条引用 ·
              {busy ? " 正在获取引用…" : ` ${graph.status}`}
            </span>
            <span>箭头表示引用者 → 被引用者</span>
            <button
              className="button ghost"
              onClick={() => setResetLayout((value) => value + 1)}
            >
              重置布局
            </button>
          </div>
          {graph.error && <p className="error">{graph.error}</p>}
          <div className="citation-workspace">
            <CitationGraph
              key={graph.graph_id}
              graph={graph}
              selectedId={selectedId}
              onSelect={setSelectedId}
              resetLayout={resetLayout}
            />
            <aside className="panel citation-details" aria-label="论文详情">
              {node ? (
                <>
                  <span
                    className={`citation-kind ${node.in_library ? "local" : "external"}`}
                  >
                    {node.in_library ? "已入库" : "外部论文"}
                  </span>
                  <h3>{node.title}</h3>
                  {graph.mode === "internal" && node.role && (
                    <span className={`citation-role ${node.role}`}>
                      {node.role === "foundation"
                        ? "这批论文的基石候选"
                        : node.role === "derivative"
                          ? "边缘衍生"
                          : node.role === "isolated"
                            ? "暂无已核验的内部关联"
                            : "网络中的关联论文"}
                    </span>
                  )}
                  <p className="muted">
                    {node.authors.slice(0, 5).join("、") || "作者未知"} ·{" "}
                    {node.year ?? "年份未知"}
                  </p>
                  <div className="citation-stats">
                    {graph.mode === "explore" && (
                      <span>全局被引 {node.citation_count ?? "未知"}</span>
                    )}
                    <span>内部被引 {node.in_degree}</span>
                    <span>内部引用 {node.out_degree}</span>
                    <span>内部 PageRank {node.pagerank.toFixed(3)}</span>
                  </div>
                  {node.abstract && (
                    <details>
                      <summary>摘要</summary>
                      <p>{node.abstract}</p>
                    </details>
                  )}
                  {graph.mode === "explore" && (
                    <>
                      <p>它引用的论文：{statusLabel(node.references)}</p>
                      <p>引用它的论文：{statusLabel(node.citations)}</p>
                    </>
                  )}
                  <div className="citation-actions">
                    {graph.mode === "explore" && (
                      <>
                        <button
                          className="button secondary"
                          disabled={
                            busy ||
                            (node.references.status === "complete" &&
                              !node.references.has_more)
                          }
                          onClick={() => expand(node.id, false, "references")}
                        >
                          {node.references.has_more
                            ? "加载更多参考文献"
                            : "查找它引用的论文"}
                        </button>
                        <button
                          className="button secondary"
                          disabled={
                            busy ||
                            (node.citations.status === "complete" &&
                              !node.citations.has_more)
                          }
                          onClick={() => expand(node.id, false, "citations")}
                        >
                          {node.citations.has_more
                            ? "加载更多后续引用"
                            : "查找引用它的论文"}
                        </button>
                        <details className="citation-maintenance">
                          <summary>数据维护</summary>
                          <p className="muted">
                            忽略当前缓存，从第一页重新查询两个方向，不清空已显示的节点。
                          </p>
                          <button
                            className="button ghost"
                            disabled={busy}
                            onClick={() => expand(node.id, true, "both")}
                          >
                            重新查询此论文的引用
                          </button>
                        </details>
                      </>
                    )}
                    {node.paper_id ? (
                      <Link
                        className="button secondary"
                        href={`/papers/${node.paper_id}`}
                      >
                        打开论文
                      </Link>
                    ) : (
                      <button
                        className="button secondary"
                        disabled={busy}
                        onClick={() => importNode(node.id)}
                      >
                        加入文献库
                      </button>
                    )}
                    {externalLink && (
                      <a
                        href={externalLink}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        查看原文 ↗
                      </a>
                    )}
                  </div>
                </>
              ) : (
                <p className="muted">点击节点查看论文详情。</p>
              )}
            </aside>
          </div>
          {graph.mode === "internal" && (
            <div className="panel citation-analysis-summary">
              <strong>这批论文的内部引用网络</strong>
              <span>
                已核验 {graph.metrics.verified_papers ?? 0}/
                {graph.metrics.total_papers ?? graph.nodes.length} 篇 ·{" "}
                {graph.edges.length} 条内部引用
              </span>
              <p className="muted">
                “基石”与“边缘衍生”是依据所选论文之间的入度和 PageRank
                得出的相对位置；不是对整个领域的绝对评价。未核验的论文可能使关系不完整。
              </p>
              <div className="citation-role-lists">
                <div>
                  <b>基石候选</b>
                  {graph.nodes.filter((item) => item.role === "foundation")
                    .length ? (
                    graph.nodes
                      .filter((item) => item.role === "foundation")
                      .map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => setSelectedId(item.id)}
                        >
                          {item.title}
                        </button>
                      ))
                  ) : (
                    <span className="muted">暂无足够的内部引用证据</span>
                  )}
                </div>
                <div>
                  <b>边缘衍生</b>
                  {graph.nodes.filter((item) => item.role === "derivative")
                    .length ? (
                    graph.nodes
                      .filter((item) => item.role === "derivative")
                      .map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => setSelectedId(item.id)}
                        >
                          {item.title}
                        </button>
                      ))
                  ) : (
                    <span className="muted">暂无</span>
                  )}
                </div>
              </div>
            </div>
          )}
          <p className="muted citation-legend">
            {graph.mode === "internal"
              ? "只显示所选库内论文。箭头从引用者指向被引用者；缺少数据不等于不存在引用。"
              : "蓝色：已入库 · 橙色：外部论文。引用列表来自学术数据源，空结果不保证论文真实没有引用。"}
          </p>
        </>
      )}

      {graph && (
        <div className="citation-synthesis">
          {insightError && (
            <p className="error" role="alert">
              {insightError}
            </p>
          )}
          <InsightPanel
            kind="review"
            title="自动综述"
            action="生成综述"
            description="围绕当前图谱逐步研究、调整大纲，再按章节写作并核验引用。"
            insight={insights.review}
            progress={insightProgress}
            research={reviewResearch}
            loading={insightKind === "review"}
            disabled={busy || Boolean(insightTaskId) || !graph.nodes.length}
            onGenerate={() => createInsight("review")}
          />
          <InsightPanel
            kind="future"
            title="未来工作探索"
            action="探索未来方向"
            description="结合当前图谱的摘要、结论、局限及已有综述生成候选方向，逐项展示相近论文与核查状态。相近不等于方法相同，新颖性仍需核对全文。"
            insight={insights.future}
            progress={insightProgress}
            loading={insightKind === "future"}
            disabled={busy || Boolean(insightTaskId) || !graph.nodes.length}
            onGenerate={() => createInsight("future")}
          />
        </div>
      )}
    </div>
  );
}

function InsightPanel({
  kind,
  title,
  action,
  description,
  insight,
  progress,
  research,
  loading,
  disabled,
  onGenerate,
}: {
  kind: "review" | "future";
  title: string;
  action: string;
  description: string;
  insight?: Insight;
  progress?: number | null;
  research?: ReviewResearch | null;
  loading: boolean;
  disabled: boolean;
  onGenerate: () => void;
}) {
  return (
    <section className="panel citation-insight">
      <div className="citation-insight-header">
        <div>
          <h3>{title}</h3>
          <p className="muted">{description}</p>
        </div>
        <button
          className="button secondary"
          disabled={disabled}
          onClick={onGenerate}
        >
          {loading ? "生成中…" : insight ? `重新${action}` : action}
        </button>
      </div>
      {loading && (
        <OperationProgress
          title={kind === "review" ? "正在生成自动综述" : "正在探索未来方向"}
          detail={
            kind === "review"
              ? "依次研究论文、撰写章节并核对引用。"
              : "正在整理未来工作并对照已有研究。"
          }
          progress={progress}
        />
      )}
      {insight?.stale && (
        <p className="muted">图谱已更新，此结果基于较早版本；可重新生成。</p>
      )}
      {kind === "review" && research && (
        <div className="graph-review-research">
          <p>
            <strong>研究进度：</strong>
            {(
              {
                planning: "研究论文与规划大纲",
                writing: "按章节撰写",
                verifying: "核验引用",
                completed: "已完成",
                failed: "未完成",
              } as Record<string, string>
            )[research.phase] || research.phase}{" "}
            · 证据 {research.evidence_count} 条 · 涉及论文{" "}
            {research.paper_count} 篇
          </p>
          {research.error && <p className="error">{research.error}</p>}
          <details>
            <summary>研究大纲与证据来源</summary>
            <ol>
              {(research.outline.sections || []).map((section) => (
                <li key={section.id}>
                  <strong>{section.title}</strong> ·{" "}
                  {research.sections.some((item) => item.id === section.id)
                    ? "已撰写"
                    : "待撰写"}{" "}
                  · {section.evidence_ids.length} 条证据
                  <p className="muted">{section.description}</p>
                </li>
              ))}
            </ol>
            {research.coverage.gaps?.length ? (
              <p className="muted">
                待补足：{research.coverage.gaps.join("；")}
              </p>
            ) : null}
            <details>
              <summary>证据清单 · {research.evidence_count} 条</summary>
              {research.evidence.map((item) => (
                <p key={item.id} className="graph-review-evidence">
                  <strong>
                    {item.source_type === "abstract"
                      ? "摘要"
                      : `PDF 第 ${(item.page_index ?? 0) + 1} 页`}
                  </strong>{" "}
                  · {item.title}
                  <br />
                  {item.summary}
                  <br />
                  <small>{item.quote}</small>
                </p>
              ))}
            </details>
          </details>
        </div>
      )}
      {insight && (
        <>
          <div className="citation-insight-body">
            {kind === "future" ? (
              <FutureIdeaCards content={insight.content} />
            ) : (
              <MathMarkdown>{insight.content}</MathMarkdown>
            )}
          </div>
          <details className="citation-insight-sources">
            <summary>
              使用的论文 · {insight.sources.length} 篇（
              {kind === "review" ? "按首次引用排序" : "按综合评分排序"}）
            </summary>
            <ol>
              {insight.sources.map((source) => {
                const href = source.paper_id
                  ? `/papers/${source.paper_id}`
                  : source.doi
                    ? `https://doi.org/${source.doi}`
                    : source.arxiv_id
                      ? `https://arxiv.org/abs/${source.arxiv_id}`
                      : source.openalex_id
                        ? `https://openalex.org/${source.openalex_id}`
                        : null;
                return (
                  <li key={source.work_id}>
                    {href ? (
                      <a
                        href={href}
                        target={source.paper_id ? undefined : "_blank"}
                        rel={
                          source.paper_id ? undefined : "noopener noreferrer"
                        }
                      >
                        {source.title}
                      </a>
                    ) : (
                      source.title
                    )}
                    {source.year ? ` · ${source.year}` : ""}
                  </li>
                );
              })}
            </ol>
          </details>
        </>
      )}
    </section>
  );
}
