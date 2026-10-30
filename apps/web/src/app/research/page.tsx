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
      <div className="research-layout">
        <PaperPicker selected={selected} onChange={setSelected} />
        <div className="panel stack">
          <h2>向文献提问</h2>
          <textarea
            className="textarea"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="例如：比较 Transformer 与 BERT 的编码器结构，分别引用原文证据。"
          />
          <div className="prompt-suggestions" aria-label="示例问题">
            {[
              "这篇论文的核心贡献是什么？",
              "作者如何设计实验与评价指标？",
              "哪些结论有直接证据支持？",
            ].map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => setQuestion(prompt)}
              >
                {prompt}
              </button>
            ))}
          </div>
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
      </div>
      <RunPanel runId={run} />
    </>
  );
}
