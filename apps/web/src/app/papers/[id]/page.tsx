"use client";
import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { api, Paper } from "@/lib/api";
import PdfCanvas from "@/components/PdfCanvas";
import RunPanel from "@/components/RunPanel";
import {
  BookOpenText,
  Languages,
  Maximize2,
  PanelRight,
  RefreshCw,
} from "lucide-react";

type Version = {
  id: string;
  label: string;
  parse_status: string;
  page_count: number;
};
type Block = {
  id: string;
  page_index: number;
  type: string;
  text: string;
  bbox: number[];
};
export default function Reader({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [paper, setPaper] = useState<Paper | null>(null),
    [versions, setVersions] = useState<Version[]>([]),
    [selectedVersion, setSelectedVersion] = useState<string | null>(null),
    [blocks, setBlocks] = useState<Block[]>([]),
    [page, setPage] = useState(1),
    [focus, setFocus] = useState<string | null>(null),
    [question, setQuestion] = useState(""),
    [run, setRun] = useState<string | null>(null),
    [error, setError] = useState(""),
    [selectedText, setSelectedText] = useState(""),
    [translatedText, setTranslatedText] = useState(""),
    [returnPage, setReturnPage] = useState<number | null>(null),
    [showAssistant, setShowAssistant] = useState(false),
    [focusMode, setFocusMode] = useState(false),
    [reparsing, setReparsing] = useState(false);
  useEffect(() => {
    const url = new URL(window.location.href);
    const frame = requestAnimationFrame(() => {
      setPage(Number(url.searchParams.get("page") || 1));
      setFocus(url.searchParams.get("block"));
    });
    return () => cancelAnimationFrame(frame);
  }, []);
  const refreshPaper = useCallback(() => {
    if (!id) return;
    return api<{ paper: Paper; versions: Version[] }>(`/papers/${id}`)
      .then((x) => {
        setPaper(x.paper);
        setVersions(x.versions);
      })
      .catch((e) => setError(String(e)));
  }, [id]);
  useEffect(() => {
    refreshPaper();
  }, [refreshPaper]);
  const preferred = versions.find(
    (item) => item.id === paper?.preferred_version_id,
  );
  const version =
    selectedVersion ||
    (preferred?.parse_status === "completed"
      ? preferred.id
      : versions.find((item) => item.parse_status === "completed")?.id) ||
    paper?.preferred_version_id ||
    versions[0]?.id;
  const activeVersion = versions.find((item) => item.id === version);
  useEffect(() => {
    if (
      !versions.some((item) =>
        ["pending", "running"].includes(item.parse_status),
      )
    )
      return;
    const timer = setInterval(refreshPaper, 3000);
    return () => clearInterval(timer);
  }, [versions, refreshPaper]);
  useEffect(() => {
    if (!version) return;
    let cancelled = false;
    api<{ blocks: Block[] }>(`/versions/${version}/blocks`)
      .then((x) => {
        if (!cancelled) setBlocks(x.blocks);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [version, activeVersion?.parse_status]);
  async function ask() {
    try {
      const x = await api<{ run_id: string }>("/chat/runs", {
        method: "POST",
        body: JSON.stringify({ question, paper_ids: [id] }),
      });
      setRun(x.run_id);
    } catch (e) {
      setError(String(e));
    }
  }
  async function reparse() {
    if (!version) return;
    setReparsing(true);
    setError("");
    try {
      await api(`/versions/${version}/reparse`, { method: "POST" });
      setBlocks([]);
      await refreshPaper();
    } catch (e) {
      setError(String(e));
    } finally {
      setReparsing(false);
    }
  }
  async function translateSelection() {
    try {
      const x = await api<{ text: string }>("/translate", {
        method: "POST",
        body: JSON.stringify({ text: selectedText, target_lang: "zh" }),
      });
      setTranslatedText(x.text);
    } catch (e) {
      setError(String(e));
    }
  }
  function goPage(next: number) {
    setPage(next);
    setFocus(null);
    window.history.replaceState(null, "", `?page=${next}`);
  }
  function switchVersion(next: string) {
    if (next === version) return;
    setSelectedVersion(next);
    setPage(1);
    setFocus(null);
    setBlocks([]);
    setRun(null);
    window.history.replaceState(null, "", "?page=1");
  }
  function jump(block: Block) {
    setPage(block.page_index + 1);
    setFocus(block.id);
    window.history.replaceState(
      null,
      "",
      `?page=${block.page_index + 1}&block=${block.id}`,
    );
  }
  function jumpCitation(number: number) {
    const reference = references.find((x) =>
      new RegExp(`^\\s*\\[\\s*${number}\\s*\\]`).test(x.text),
    );
    if (reference) {
      setReturnPage(page);
      jump(reference);
    } else setError(`未能定位参考文献 [${number}]`);
  }
  const focused = blocks.find((b) => b.id === focus);
  const headings = blocks.filter(
    (b) => b.type === "heading" || b.type === "title",
  );
  const references = blocks.filter((b) => b.type === "reference");
  return (
    <>
      <div className="reader-header">
        <div>
          <Link href="/library" className="back-link">
            ← 文献库
          </Link>
          <h1>{paper?.title || "论文阅读器"}</h1>
          <p className="lead">原文阅读 · 结构化目录 · 可定位证据</p>
        </div>
        <div className="mode-switch" role="group" aria-label="阅读模式">
          <button
            className={`button ${!showAssistant && !focusMode ? "active" : ""}`}
            aria-pressed={!showAssistant && !focusMode}
            onClick={() => {
              setShowAssistant(false);
              setFocusMode(false);
            }}
          >
            <BookOpenText size={15} /> 阅读
          </button>
          <button
            className={`button ${showAssistant && !focusMode ? "active" : ""}`}
            aria-pressed={showAssistant && !focusMode}
            onClick={() => {
              setShowAssistant(true);
              setFocusMode(false);
            }}
          >
            <PanelRight size={15} /> AI 辅助
          </button>
          <button
            className={`button ${focusMode ? "active" : ""}`}
            aria-pressed={focusMode}
            onClick={() => {
              setFocusMode(true);
              setShowAssistant(false);
            }}
          >
            <Maximize2 size={15} /> 专注
          </button>
          {version && (
            <Link className="button" href={`/papers/${id}/translate`}>
              <Languages size={15} /> 双语
            </Link>
          )}
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {paper && !versions.length && (
        <div className="panel reader-status">
          此记录目前只有论文元数据，尚未导入 PDF，因此没有可解析的目录。
        </div>
      )}
      {activeVersion && activeVersion.parse_status !== "completed" && (
        <div className="panel reader-status">
          {activeVersion.parse_status === "failed"
            ? "此版本解析失败。请到任务中心查看错误并重试。"
            : activeVersion.parse_status === "conflict"
              ? "此版本等待版本冲突处理。"
              : "此版本正在等待或执行解析；页面会自动更新。若长时间停留在排队状态，请到调试后台检查 parser Worker。"}
          <Link href="/debug">查看调试后台 →</Link>
        </div>
      )}
      {preferred &&
        preferred.id !== version &&
        preferred.parse_status !== "completed" && (
          <div className="panel reader-status">
            默认版本仍在解析；当前展示一个已完成解析的版本。
          </div>
        )}
      {versions.length > 0 && (
        <div className="version-bar">
          <span className="section-label">论文版本</span>
          {versions.map((v) => (
            <button
              key={v.id}
              className={v.id === version ? "button secondary" : "button ghost"}
              aria-pressed={v.id === version}
              onClick={() => switchVersion(v.id)}
            >
              {v.label} · {v.parse_status}
              {v.id === paper?.preferred_version_id ? " · 默认" : ""}
            </button>
          ))}
          {activeVersion &&
            ["completed", "failed"].includes(activeVersion.parse_status) && (
              <button
                className="button ghost"
                onClick={reparse}
                disabled={reparsing}
                title="重新提取目录与内容块"
              >
                <RefreshCw size={14} /> {reparsing ? "提交中…" : "重新解析"}
              </button>
            )}
        </div>
      )}
      {version && (
        <div
          className={`reader-workspace ${showAssistant && !focusMode ? "with-assistant" : ""} ${focusMode ? "focus-mode" : ""}`}
        >
          {!focusMode && (
            <aside className="panel reader-outline">
              <h3>论文目录</h3>
              {!headings.length && (
                <p className="muted" style={{ padding: "0 8px", fontSize: 12 }}>
                  {activeVersion?.parse_status === "completed"
                    ? "解析已完成，但没有识别出章节标题。"
                    : "目录将在解析完成后显示。"}
                </p>
              )}
              {headings.map((x) => (
                <button
                  key={x.id}
                  className={`outline-item ${focus === x.id ? "active" : ""}`}
                  onClick={() => jump(x)}
                >
                  {x.text.slice(0, 85)}
                </button>
              ))}
            </aside>
          )}
          <div className="panel reader-document">
            <div className="document-toolbar">
              <button
                className="button secondary"
                onClick={() => goPage(Math.max(1, page - 1))}
              >
                上一页
              </button>
              <span className="page-indicator">
                {page} / {activeVersion?.page_count || "—"} 页
              </span>
              <button
                className="button secondary"
                onClick={() =>
                  goPage(Math.min(activeVersion?.page_count || 999, page + 1))
                }
              >
                下一页
              </button>
              {returnPage && (
                <button
                  className="button ghost"
                  onClick={() => {
                    goPage(returnPage);
                    setReturnPage(null);
                  }}
                >
                  返回引用处
                </button>
              )}
            </div>
            <PdfCanvas
              versionId={version}
              page={page}
              bbox={focused?.bbox}
              onSelectText={(text) => {
                setSelectedText(text);
                setShowAssistant(true);
                setFocusMode(false);
              }}
              onCitationClick={jumpCitation}
            />
          </div>
          {showAssistant && !focusMode && (
            <aside className="reader-assistant">
              <div className="panel scroll">
                <h3>本页内容</h3>
                {blocks
                  .filter((x) => x.page_index === page - 1)
                  .map((x) => (
                    <button
                      key={x.id}
                      className="evidence"
                      onClick={() => jump(x)}
                    >
                      <span className="badge">{x.type}</span>{" "}
                      {x.text.slice(0, 320)}
                    </button>
                  ))}
              </div>
              {selectedText && (
                <div className="panel">
                  <h3>划词翻译</h3>
                  <p>{selectedText.slice(0, 300)}</p>
                  <button
                    className="button secondary"
                    onClick={translateSelection}
                  >
                    译为中文
                  </button>
                  {translatedText && <p>{translatedText}</p>}
                </div>
              )}
              <div className="panel">
                <h3>论文问答</h3>
                {version !== paper?.preferred_version_id && (
                  <p className="muted">问答检索默认版本的内容。</p>
                )}
                <textarea
                  className="textarea"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="询问论文细节…"
                />
                <button
                  className="button"
                  disabled={!question.trim()}
                  onClick={ask}
                >
                  检索证据并回答
                </button>
              </div>
            </aside>
          )}
        </div>
      )}
      {references.length > 0 && (
        <details className="panel references-panel">
          <summary>参考文献 · {references.length} 条</summary>
          <div className="list">
            {references.map((x) => (
              <button
                className="button ghost"
                key={x.id}
                style={{ textAlign: "left" }}
                onClick={() => jump(x)}
              >
                {x.text.slice(0, 240)}
              </button>
            ))}
          </div>
        </details>
      )}
      <RunPanel runId={run} />
    </>
  );
}
