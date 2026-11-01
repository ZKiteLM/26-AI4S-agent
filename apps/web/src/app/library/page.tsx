"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  FileText,
  Search,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { api, Paper } from "@/lib/api";

type Conflict = {
  id: string;
  existing_version: string;
  incoming_version: string;
  recommendation: string;
  reason: string;
};
export default function Library() {
  const fileInput = useRef<HTMLInputElement>(null);
  const [papers, setPapers] = useState<Paper[]>([]),
    [query, setQuery] = useState(""),
    [busy, setBusy] = useState(false),
    [deletingId, setDeletingId] = useState<string | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
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
  async function removePaper(paper: Paper) {
    if (
      !window.confirm(
        `确定删除《${paper.title}》吗？\n该论文的所有版本、PDF、解析内容及向量索引将一并删除。`,
      )
    )
      return;
    setDeletingId(paper.id);
    setError("");
    setNotice("");
    try {
      await api(`/papers/${paper.id}`, { method: "DELETE" });
      setPapers((current) => current.filter((item) => item.id !== paper.id));
      setNotice(`已删除《${paper.title}》。`);
      await load(query);
    } catch (cause) {
      setError(`删除失败：${String(cause)}`);
    } finally {
      setDeletingId(null);
    }
  }
  return (
    <>
      <div className="workspace-hero compact-actions">
        <button
          className="button"
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={busy}
        >
          <UploadCloud size={18} />
          {busy ? "上传中…" : "上传 PDF"}
        </button>
        <input
          ref={fileInput}
          hidden
          type="file"
          accept="application/pdf"
          multiple
          onChange={(e) => {
            upload(e.target.files);
            e.target.value = "";
          }}
          disabled={busy}
        />
      </div>
      {error && <p className="error">{error}</p>}
      {notice && (
        <p className="success" role="status">
          {notice}
        </p>
      )}
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
      <div className="panel search-panel">
        <Search size={18} />
        <input
          className="input"
          aria-label="搜索论文标题"
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
          <article className="card paper-card" key={p.id}>
            <Link href={`/papers/${p.id}`} className="paper-card-main">
              <div className="paper-card-top">
                <FileText size={21} color="#6e86b5" aria-hidden="true" />
                <ArrowUpRight
                  className="card-arrow"
                  size={18}
                  aria-hidden="true"
                />
              </div>
              <div className="section-label">
                {p.source} · {p.year || "年份未识别"}
              </div>
              <h2>{p.title}</h2>
            </Link>
            <div className="card-bottom">
              <span className="badge">
                {p.arxiv_id ? `arXiv ${p.arxiv_id}` : "本地论文"}
              </span>
              <button
                className="button ghost paper-delete-button"
                type="button"
                onClick={() => void removePaper(p)}
                disabled={deletingId !== null}
                aria-label={`删除文献：${p.title}`}
              >
                <Trash2 size={16} aria-hidden="true" />
                {deletingId === p.id ? "删除中…" : "删除"}
              </button>
            </div>
          </article>
        ))}
      </div>
      {!papers.length && (
        <div className="panel empty-state">
          <FileText size={34} aria-hidden="true" />
          <h2>从第一篇论文开始</h2>
          <p>上传 PDF 后，即可阅读原文、追踪证据并提出问题。</p>
        </div>
      )}
    </>
  );
}
