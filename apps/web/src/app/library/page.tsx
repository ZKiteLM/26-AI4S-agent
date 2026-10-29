"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { UploadCloud, Search } from "lucide-react";
import { api, Paper } from "@/lib/api";

type Conflict = {
  id: string;
  existing_version: string;
  incoming_version: string;
  recommendation: string;
  reason: string;
};
export default function Library() {
  const [papers, setPapers] = useState<Paper[]>([]),
    [query, setQuery] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [conflicts, setConflicts] = useState<Conflict[]>([]);
  const load = useCallback(
    (search = "") =>
      api<{ items: Paper[] }>(`/papers?q=${encodeURIComponent(search)}`)
        .then((x) => setPapers(x.items))
        .catch((e) => setError(String(e))),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);
  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      const body = new FormData();
      Array.from(files).forEach((f) => body.append("files", f));
      const result = await api<{ conflicts: Conflict[] }>("/papers/upload", {
        method: "POST",
        body,
      });
      setConflicts(result.conflicts);
      load(query);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function resolve(id: string, resolution: string) {
    try {
      await api(`/versions/conflicts/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ resolution }),
      });
      setConflicts(conflicts.filter((x) => x.id !== id));
      load(query);
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <>
      <div className="toolbar">
        <div>
          <div className="section-label">LITERATURE LIBRARY</div>
          <h1>文献库</h1>
          <p className="lead">导入、解析和组织可追溯的科研资料。</p>
        </div>
        <label className="button">
          <UploadCloud size={18} />
          {busy ? "上传中…" : "上传 PDF"}
          <input
            hidden
            type="file"
            accept="application/pdf"
            multiple
            onChange={(e) => upload(e.target.files)}
            disabled={busy}
          />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      {conflicts.map((c) => (
        <div className="panel" key={c.id}>
          <h3>发现同一论文的不同版本</h3>
          <p>
            已有 {c.existing_version}，新上传 {c.incoming_version}。
            {c.recommendation === "keep_existing"
              ? "建议保留现有版本。"
              : "建议使用新上传版本。"}
          </p>
          <div className="row">
            <button
              className="button secondary"
              onClick={() => resolve(c.id, "keep")}
            >
              保留两个版本
            </button>
            <button className="button" onClick={() => resolve(c.id, "replace")}>
              设新版本为默认
            </button>
            <button
              className="button ghost"
              onClick={() => resolve(c.id, "discard")}
            >
              取消导入
            </button>
          </div>
        </div>
      ))}
      <div className="panel row">
        <Search size={18} />
        <input
          className="input"
          placeholder="搜索论文标题"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load(query)}
        />
        <button className="button secondary" onClick={() => load(query)}>
          搜索
        </button>
        <span className="muted">{papers.length} 篇论文</span>
      </div>
      <div className="grid">
        {papers.map((p) => (
          <Link href={`/papers/${p.id}`} className="card" key={p.id}>
            <div className="section-label">
              {p.source} · {p.year || "年份未识别"}
            </div>
            <h2>{p.title}</h2>
            <div className="row">
              <span className="badge">
                {p.arxiv_id ? `arXiv ${p.arxiv_id}` : "本地论文"}
              </span>
              <span className="muted">打开阅读器 →</span>
            </div>
          </Link>
        ))}
      </div>
      {!papers.length && (
        <div className="panel muted">暂无论文。上传官方 PDF 开始测试。</div>
      )}
    </>
  );
}
