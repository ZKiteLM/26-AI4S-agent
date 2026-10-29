"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import PaperPicker from "@/components/PaperPicker";
import RunPanel from "@/components/RunPanel";

export default function Research() {
  const [selected, setSelected] = useState<string[]>([]),
    [question, setQuestion] = useState(""),
    [run, setRun] = useState<string | null>(null),
    [error, setError] = useState("");
  async function submit() {
    setError("");
    try {
      const x = await api<{ run_id: string }>("/chat/runs", {
        method: "POST",
        body: JSON.stringify({ question, paper_ids: selected }),
      });
      setRun(x.run_id);
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <>
      <div className="section-label">EVIDENCE FIRST RESEARCH</div>
      <h1>科研问答</h1>
      <p className="lead">
        选择一篇或多篇论文，查看 Agent 的检索、证据和回答。
      </p>
      <PaperPicker selected={selected} onChange={setSelected} />
      <div className="panel stack">
        <h2>向文献提问</h2>
        <textarea
          className="textarea"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="例如：比较 Transformer 与 BERT 的编码器结构，分别引用原文证据。"
        />
        <div className="row">
          <button
            className="button"
            disabled={!question.trim() || !selected.length}
            onClick={submit}
          >
            开始分析
          </button>
          <span className="muted">已选 {selected.length} 篇</span>
        </div>
        {error && <p className="error">{error}</p>}
      </div>
      <RunPanel runId={run} />
    </>
  );
}
