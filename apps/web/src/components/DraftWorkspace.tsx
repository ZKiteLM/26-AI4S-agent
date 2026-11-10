"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useSessionState } from "@/lib/useSessionState";
import { BookOpenText, Network, PenLine } from "lucide-react";
import OperationProgress from "@/components/OperationProgress";

type Fields = {
  research_problem: string;
  core_motivation: string;
  solution: string;
  expected_contribution: string;
  related_work_keywords: string[];
};
type Reference = {
  number: number;
  title: string;
  authors: string[];
  year?: number;
  url: string;
  verification: string;
  verification_method: string;
};
type Candidate = {
  title: string;
  source?: string;
  verification: string;
  failure_reason?: string;
};
type DraftResult = {
  markdown: string;
  references: Reference[];
  candidates: Candidate[];
  warnings: string[];
  fields: Fields;
};
type GraphOption = {
  graph_id: string;
  updated_at: string;
  review_preview: string;
  stale: boolean;
  ideas: { index: number; title: string; description: string }[];
};
const fieldLabels: {
  key: keyof Omit<Fields, "related_work_keywords">;
  label: string;
}[] = [
  { key: "research_problem", label: "研究问题" },
  { key: "core_motivation", label: "核心思想 / 动机" },
  { key: "solution", label: "解决方案" },
  { key: "expected_contribution", label: "预期贡献" },
];
function download(markdown: string) {
  const url = URL.createObjectURL(
    new Blob([markdown], { type: "text/markdown" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = "paper-outline.md";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function DraftWorkspace() {
  const [mode, setMode] = useSessionState<"manual" | "graph">(
    "draft-mode",
    "manual",
  );
  const [idea, setIdea] = useSessionState("draft-idea", "");
  const [structured, setStructured] = useSessionState<Fields | null>(
    "draft-fields",
    null,
  );
  const [completed, setCompleted] = useSessionState(
    "draft-fields-completed",
    false,
  );
  const [graphs, setGraphs] = useState<GraphOption[]>([]);
  const [graphId, setGraphId] = useSessionState("draft-graph-id", "");
  const [ideaIndex, setIdeaIndex] = useSessionState("draft-idea-index", 0);
  const [results, setResults] = useSessionState<{
    manual: DraftResult | null;
    graph: DraftResult | null;
  }>("draft-results", { manual: null, graph: null });
  const activeResult = results[mode];
  const draft = activeResult?.markdown || "";
  const references = activeResult?.references || [];
  const candidates = activeResult?.candidates || [];
  const warnings = activeResult?.warnings || [];
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ items: GraphOption[] }>("/copilot/draft/graph-options")
      .then((x) => setGraphs(x.items))
      .catch(() => {});
  }, []);
  const graph = graphs.find((x) => x.graph_id === graphId);
  async function analyze() {
    setBusy("analyze");
    setError("");
    try {
      const result = await api<{ fields: Fields }>("/copilot/draft/analyze", {
        method: "POST",
        progress: "inline",
        body: JSON.stringify({ idea }),
      });
      setStructured(result.fields);
      setCompleted(
        Object.values(result.fields).every((value) =>
          Array.isArray(value) ? value.length > 0 : !!value,
        ),
      );
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy("");
    }
  }
  async function complete() {
    if (!structured) return;
    setBusy("complete");
    setError("");
    try {
      const result = await api<{ fields: Fields }>("/copilot/draft/complete", {
        method: "POST",
        progress: "inline",
        body: JSON.stringify({ idea, fields: structured }),
      });
      setStructured(result.fields);
      setCompleted(true);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy("");
    }
  }
  async function generate() {
    setBusy("generate");
    setError("");
    try {
      const payload =
        mode === "graph"
          ? { graph_id: graphId, idea_index: ideaIndex }
          : { idea, fields: structured };
      const result = await api<DraftResult>("/copilot/draft", {
        method: "POST",
        progress: "inline",
        body: JSON.stringify(payload),
      });
      setResults((previous) => ({ ...previous, [mode]: result }));
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="panel stack copilot-panel">
      <div className="copilot-panel-heading">
        <span className="copilot-heading-icon">
          <BookOpenText size={20} />
        </span>
        <div>
          <h2>论文框架生成</h2>
          <p>从研究 IDEA 或引用图谱出发，生成有文献依据的可编辑草稿。</p>
        </div>
      </div>
      <div
        className="copilot-source-switch"
        role="group"
        aria-label="论文框架来源"
      >
        <button
          type="button"
          aria-pressed={mode === "manual"}
          className={`copilot-source-button ${mode === "manual" ? "is-active" : ""}`}
          onClick={() => {
            setMode("manual");
            setError("");
          }}
        >
          <PenLine size={16} /> 输入研究 IDEA
        </button>
        <button
          type="button"
          aria-pressed={mode === "graph"}
          className={`copilot-source-button ${mode === "graph" ? "is-active" : ""}`}
          onClick={() => {
            setMode("graph");
            setError("");
          }}
        >
          <Network size={16} /> 从引用图谱生成
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <OperationProgress
          title={
            busy === "generate"
              ? "正在检索文献并生成论文框架"
              : busy === "analyze"
                ? "正在分析研究 IDEA"
                : "正在补全研究构想"
          }
          detail={
            busy === "generate"
              ? "会核对候选文献、撰写正文并检查引用，可能需要一些时间。"
              : "模型正在处理输入，完成后会显示可编辑结果。"
          }
        />
      )}
      {mode === "graph" ? (
        <div className="stack copilot-source-body">
          <p className="muted">
            选择已保存的自动综述和其中一个未来工作
            IDEA。系统会读取图谱论文，并补充检索相关文献。
          </p>
          {graphs.length ? (
            <>
              <label>
                引用图谱{" "}
                <select
                  className="select"
                  value={graphId}
                  onChange={(e) => {
                    setGraphId(e.target.value);
                    setIdeaIndex(0);
                  }}
                >
                  <option value="">选择图谱</option>
                  {graphs.map((item) => (
                    <option key={item.graph_id} value={item.graph_id}>
                      {new Date(item.updated_at).toLocaleString()} ·{" "}
                      {item.ideas[0]?.title}
                    </option>
                  ))}
                </select>
              </label>
              {graph && (
                <>
                  <div className="copilot-context-card">
                    <strong>自动综述</strong>
                    <p className="muted">{graph.review_preview}…</p>
                    {graph.stale && (
                      <p className="error">
                        图谱分析早于当前图谱版本，建议重新生成。
                      </p>
                    )}
                  </div>
                  <label>
                    选择 IDEA{" "}
                    <select
                      className="select"
                      value={ideaIndex}
                      onChange={(e) => setIdeaIndex(Number(e.target.value))}
                    >
                      {graph.ideas.map((item) => (
                        <option value={item.index} key={item.index}>
                          {item.title}
                        </option>
                      ))}
                    </select>
                  </label>
                  <p className="muted">{graph.ideas[ideaIndex]?.description}</p>
                </>
              )}
            </>
          ) : (
            <p>
              暂无同时具备自动综述和未来工作 IDEA 的图谱。
              <Link href="/graph">前往引用图谱</Link>
            </p>
          )}
          <button
            className="button"
            disabled={!graph || !!busy}
            onClick={generate}
          >
            {busy === "generate" ? "生成中…" : "根据综述与 IDEA 生成框架"}
          </button>
        </div>
      ) : (
        <div className="stack copilot-source-body">
          <p className="muted">
            先提取结构化字段，再根据原始 IDEA 补全缺失信息。字段可以自行修改。
          </p>
          <textarea
            className="textarea"
            value={idea}
            onChange={(e) => {
              setIdea(e.target.value);
              setStructured(null);
              setCompleted(false);
            }}
            placeholder="描述你的研究 IDEA、方法或预期贡献…"
            style={{ minHeight: 130 }}
          />
          <button
            className="button"
            disabled={idea.trim().length < 10 || !!busy}
            onClick={analyze}
          >
            {busy === "analyze" ? "分析中…" : "1. 分析 IDEA"}
          </button>
          {structured && (
            <div className="stack">
              <div
                className="two-col copilot-field-grid"
                style={{ alignItems: "start" }}
              >
                {fieldLabels.map((item) => (
                  <label key={item.key} className="stack copilot-field-card">
                    <strong>{item.label}</strong>
                    <textarea
                      className="textarea"
                      value={structured[item.key]}
                      onChange={(e) =>
                        setStructured({
                          ...structured,
                          [item.key]: e.target.value,
                        })
                      }
                      placeholder="可留空"
                      style={{ minHeight: 95 }}
                    />
                  </label>
                ))}
              </div>
              <label className="stack copilot-field-card">
                <strong>相关工作检索关键词</strong>
                <textarea
                  className="textarea"
                  value={structured.related_work_keywords.join("\n")}
                  onChange={(e) =>
                    setStructured({
                      ...structured,
                      related_work_keywords: e.target.value
                        .split(/\n|,|，/)
                        .map((word) => word.trim())
                        .filter(Boolean),
                    })
                  }
                  placeholder="每行一个简短英文关键词；可留空"
                  style={{ minHeight: 95 }}
                />
              </label>
              <div className="flex">
                <button
                  className="button secondary"
                  disabled={!!busy}
                  onClick={complete}
                >
                  {busy === "complete"
                    ? "补全中…"
                    : "2. 根据原始 IDEA 补全缺失字段"}
                </button>
                <button
                  className="button"
                  disabled={!completed || !!busy}
                  onClick={generate}
                >
                  {busy === "generate" ? "检索并生成中…" : "3. 生成论文框架"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {draft && (
        <div
          className="two-col copilot-output-grid"
          style={{ alignItems: "start" }}
        >
          <div className="stack copilot-output-pane">
            <div className="flex">
              <h3>可编辑 Markdown</h3>
              <button
                className="button secondary"
                onClick={() => download(draft)}
              >
                下载 Markdown
              </button>
            </div>
            <textarea
              aria-label="可编辑论文草稿"
              className="textarea"
              value={draft}
              onChange={(e) =>
                setResults((previous) => ({
                  ...previous,
                  [mode]: { ...previous[mode]!, markdown: e.target.value },
                }))
              }
              style={{ minHeight: 560, fontFamily: "ui-monospace, monospace" }}
            />
          </div>
          <div className="stack copilot-output-pane">
            <h3>已使用的参考文献</h3>
            {references.length ? (
              references.map((ref) => (
                <div className="copilot-reference-card" key={ref.number}>
                  <strong>
                    [{ref.number}] {ref.title}
                  </strong>
                  <p className="muted">
                    {ref.authors?.join(", ")} · {ref.year || "年份未知"}
                  </p>
                  <a href={ref.url} target="_blank" rel="noopener noreferrer">
                    查看原文 ↗
                  </a>
                  <small style={{ display: "block" }}>
                    {ref.verification === "verified_preprint"
                      ? "arXiv 预印本"
                      : "DOI"}{" "}
                    · {ref.verification_method}
                  </small>
                </div>
              ))
            ) : (
              <p>当前草稿没有可核验引用。</p>
            )}
            {warnings.length > 0 && (
              <details>
                <summary>核验提示（{warnings.length}）</summary>
                {warnings.map((item, i) => (
                  <p key={i}>{item}</p>
                ))}
              </details>
            )}
            {candidates.some((item) => item.verification === "unverified") && (
              <details>
                <summary>未纳入引用的候选文献</summary>
                {candidates
                  .filter((item) => item.verification === "unverified")
                  .map((item, i) => (
                    <p key={`${item.title}-${i}`}>
                      <strong>{item.title}</strong> ·{" "}
                      {item.source || "来源未知"} ·{" "}
                      {item.failure_reason || "未能核验身份"}
                    </p>
                  ))}
              </details>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
