"use client";
import { useEffect, useState } from "react";
import { api, Paper } from "@/lib/api";

type Found = {
  title: string;
  authors: string[];
  year: number | null;
  openalex_id: string | null;
  arxiv_id: string | null;
  pdf_url: string | null;
  doi: string | null;
  source: string;
};
type Rec = {
  id: string;
  paper: Paper;
  score: number;
  scoring_factors: Record<string, unknown>;
  feedback: number;
};
export default function Discover() {
  const [query, setQuery] = useState(""),
    [found, setFound] = useState<Found[]>([]),
    [topic, setTopic] = useState(""),
    [recs, setRecs] = useState<Rec[]>([]),
    [subs, setSubs] = useState<{ id: string; topic: string }[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const load = () => {
    api<{ items: Rec[] }>("/recommendations")
      .then((x) => setRecs(x.items))
      .catch(() => {});
    api<{ items: { id: string; topic: string }[] }>("/subscriptions")
      .then((x) => setSubs(x.items))
      .catch(() => {});
  };
  useEffect(() => {
    load();
  }, []);
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
  async function importOne(item: Found) {
    setMessage("");
    try {
      const x = await api<{ status: string }>("/discovery/import", {
        method: "POST",
        body: JSON.stringify({
          openalex_id: item.openalex_id,
          arxiv_id: item.arxiv_id,
        }),
      });
      setMessage(`导入结果：${x.status}`);
    } catch (e) {
      setError(String(e));
    }
  }
  async function subscribe() {
    try {
      await api("/subscriptions", {
        method: "POST",
        body: JSON.stringify({ topic }),
      });
      setTopic("");
      setMessage("已订阅，推荐将在后台更新。");
      load();
    } catch (e) {
      setError(String(e));
    }
  }
  async function feedback(id: string, value: number) {
    try {
      await api(`/recommendations/${id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ value }),
      });
      load();
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <>
      {error && <p className="error">{error}</p>}
      {message && <p className="success">{message}</p>}
      <div className="panel">
        <h2>检索论文</h2>
        <div className="row">
          <input
            className="input"
            placeholder="论文标题、作者或研究关键词"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
          <button
            className="button"
            disabled={busy || query.length < 2}
            onClick={search}
          >
            {busy ? "检索中…" : "检索 OpenAlex"}
          </button>
        </div>
      </div>
      <div className="list">
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
              <button className="button secondary" onClick={() => importOne(x)}>
                导入文献库
              </button>
            </div>
          </div>
        ))}
      </div>
      <div className="panel">
        <h2>研究主题追踪</h2>
        <div className="row">
          <input
            className="input"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="例如 large language model agents"
          />
          <button
            className="button"
            disabled={topic.length < 2}
            onClick={subscribe}
          >
            添加订阅
          </button>
        </div>
        <p className="muted">
          已订阅：{subs.map((x) => x.topic).join("、") || "无"}
        </p>
      </div>
      <h2>个性化推荐</h2>
      <div className="grid">
        {recs.map((x) => (
          <div className="card" key={x.id}>
            <div className="section-label">推荐分数 {x.score.toFixed(2)}</div>
            <h2>{x.paper.title}</h2>
            <p className="muted">{JSON.stringify(x.scoring_factors)}</p>
            <div className="row">
              <button
                className="button secondary"
                onClick={() => feedback(x.id, 1)}
              >
                有帮助 ↑
              </button>
              <button
                className="button ghost"
                onClick={() => feedback(x.id, -1)}
              >
                不相关 ↓
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
