"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import PaperPicker from "@/components/PaperPicker";
import CitationGraph from "@/components/CitationGraph";
import RunPanel from "@/components/RunPanel";

type GraphData = {
  nodes: { id: string; title: string; pagerank: number; in_degree: number }[];
  edges: { source: string; target: string }[];
  metrics: Record<string, string>;
};
export default function GraphPage() {
  const [selected, setSelected] = useState<string[]>([]),
    [graph, setGraph] = useState<GraphData | null>(null),
    [run, setRun] = useState<string | null>(null),
    [future, setFuture] = useState<
      { quote: string; page_index: number; paper_id: string }[]
    >([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function build() {
    setBusy(true);
    setError("");
    try {
      const x = await api<{ graph_id: string }>("/graphs", {
        method: "POST",
        body: JSON.stringify({ paper_ids: selected }),
      });
      setGraph(await api<GraphData>(`/graphs/${x.graph_id}`));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function synthesize() {
    try {
      const x = await api<{
        run_id: string;
        future_work: { quote: string; page_index: number; paper_id: string }[];
      }>("/research/synthesize", {
        method: "POST",
        body: JSON.stringify({
          question: "综述这些论文的技术演进、差异和有证据支持的未来研究方向",
          paper_ids: selected,
        }),
      });
      setRun(x.run_id);
      setFuture(x.future_work);
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <>
      <PaperPicker selected={selected} onChange={setSelected} />
      <div className="row">
        <button
          className="button"
          disabled={selected.length < 2 || busy}
          onClick={build}
        >
          {busy ? "构建中…" : "构建引用图谱"}
        </button>
        <button
          className="button secondary"
          disabled={selected.length < 2}
          onClick={synthesize}
        >
          生成综述与 Future Work
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {graph && (
        <div className="panel">
          <CitationGraph graph={graph} />
          <h3 style={{ marginTop: 18 }}>重要节点 · PageRank</h3>
          <ul className="plain-list">
            {[...graph.nodes]
              .sort((a, b) => b.pagerank - a.pagerank)
              .map((n) => (
                <li key={n.id}>
                  {n.title}{" "}
                  <span className="muted">
                    PR {n.pagerank.toFixed(3)} · 入度 {n.in_degree}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      )}
      <RunPanel runId={run} />
      {future.length > 0 && (
        <div className="panel">
          <h2>Future Work 原文候选</h2>
          {future.map((x, i) => (
            <div className="evidence" key={i}>
              {x.quote}
              <div className="muted">
                第 {x.page_index + 1} 页 · 后续研究状态：证据不足，需进一步核查
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
