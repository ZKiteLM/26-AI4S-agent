"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { api, Paper } from "@/lib/api";
import PdfCanvas from "@/components/PdfCanvas";
import PdfThumbnails from "@/components/PdfThumbnails";
import ReaderColumns from "@/components/ReaderColumns";
import BilingualReader from "@/components/BilingualReader";
import MathMarkdown from "@/components/MathMarkdown";
import RunPanel from "@/components/RunPanel";
import {
  BookOpenText,
  Languages,
  Maximize2,
  PanelLeftClose,
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
type Mode = "read" | "assistant" | "focus" | "bilingual";

export default function Reader({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [paper, setPaper] = useState<Paper | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [page, setPage] = useState(1);
  const [focus, setFocus] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("read");
  const [outlineOpen, setOutlineOpen] = useState(true);
  const [outlineView, setOutlineView] = useState<"outline" | "thumbnails">(
    "outline",
  );
  const [jumpPage, setJumpPage] = useState<{
    page: number;
    nonce: number;
  } | null>(null);
  const [question, setQuestion] = useState("");
  const [run, setRun] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [selectedText, setSelectedText] = useState("");
  const [translatedText, setTranslatedText] = useState("");
  const [returnPage, setReturnPage] = useState<number | null>(null);
  const [reparsing, setReparsing] = useState(false);
  const [translating, setTranslating] = useState(false);

  useEffect(() => {
    const url = new URL(window.location.href);
    const next = url.searchParams.get("mode");
    const frame = requestAnimationFrame(() => {
      setPage(Math.max(1, Number(url.searchParams.get("page") || 1)));
      setFocus(url.searchParams.get("block"));
      if (["read", "assistant", "focus", "bilingual"].includes(next || ""))
        setMode(next as Mode);
      setOutlineOpen(
        window.localStorage.getItem("reader-outline-open") !== "false",
      );
    });
    return () => cancelAnimationFrame(frame);
  }, []);

  const refreshPaper = useCallback(() => {
    return api<{ paper: Paper; versions: Version[] }>(`/papers/${id}`)
      .then((result) => {
        setPaper(result.paper);
        setVersions(result.versions);
      })
      .catch((cause) => setError(String(cause)));
  }, [id]);
  useEffect(() => {
    void refreshPaper();
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
      .then((result) => {
        if (!cancelled) setBlocks(result.blocks);
      })
      .catch((cause) => {
        if (!cancelled) setError(String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [version, activeVersion?.parse_status]);

  function updateUrl(
    nextMode = mode,
    nextPage = page,
    nextFocus: string | null = focus,
  ) {
    const url = new URL(window.location.href);
    url.searchParams.set("page", String(nextPage));
    if (nextMode === "read") url.searchParams.delete("mode");
    else url.searchParams.set("mode", nextMode);
    if (nextFocus) url.searchParams.set("block", nextFocus);
    else url.searchParams.delete("block");
    window.history.replaceState(null, "", url.pathname + url.search);
  }
  function changeMode(next: Mode) {
    setMode(next);
    updateUrl(next);
  }
  function changeOutline(open: boolean) {
    setOutlineOpen(open);
    window.localStorage.setItem("reader-outline-open", String(open));
  }
  function goPage(next: number) {
    const target = Math.max(1, Math.min(activeVersion?.page_count || 1, next));
    setPage(target);
    setFocus(null);
    setJumpPage((previous) => ({
      page: target,
      nonce: (previous?.nonce || 0) + 1,
    }));
    updateUrl(mode, target, null);
  }
  function switchVersion(next: string) {
    if (next === version) return;
    setSelectedVersion(next);
    setPage(1);
    setFocus(null);
    setBlocks([]);
    setRun(null);
    setSelectedText("");
    setTranslatedText("");
    setJumpPage((previous) => ({ page: 1, nonce: (previous?.nonce || 0) + 1 }));
    updateUrl(mode, 1, null);
  }
  function jump(block: Block) {
    setPage(block.page_index + 1);
    setFocus(block.id);
    setJumpPage((previous) => ({
      page: block.page_index + 1,
      nonce: (previous?.nonce || 0) + 1,
    }));
    updateUrl(mode, block.page_index + 1, block.id);
  }
  const references = blocks.filter((block) => block.type === "reference");
  function jumpCitation(number: number) {
    const reference = references.find((item) =>
      new RegExp(`^\\s*\\[\\s*${number}\\s*\\]`).test(item.text),
    );
    if (reference) {
      setReturnPage(page);
      jump(reference);
    } else setError(`未能定位参考文献 [${number}]`);
  }
  async function reparse() {
    if (!version) return;
    setReparsing(true);
    setError("");
    try {
      await api(`/versions/${version}/reparse`, { method: "POST" });
      setBlocks([]);
      await refreshPaper();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setReparsing(false);
    }
  }
  async function translateSelection() {
    if (!selectedText.trim()) return;
    setTranslating(true);
    setError("");
    try {
      const result = await api<{ text: string }>("/translate", {
        method: "POST",
        body: JSON.stringify({ text: selectedText, target_lang: "zh" }),
      });
      setTranslatedText(result.text);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setTranslating(false);
    }
  }
  async function ask() {
    try {
      const result = await api<{ run_id: string }>("/chat/runs", {
        method: "POST",
        body: JSON.stringify({ question, paper_ids: [id] }),
      });
      setRun(result.run_id);
    } catch (cause) {
      setError(String(cause));
    }
  }

  const focused = blocks.find((block) => block.id === focus);
  const headings = blocks.filter(
    (block) => block.type === "heading" || block.type === "title",
  );
  const outline = version && (
    <aside className="panel reader-outline">
      <div className="outline-heading">
        <h3>文献大纲</h3>
        <button
          className="icon-button"
          aria-label="收起文献大纲"
          onClick={() => changeOutline(false)}
        >
          <PanelLeftClose size={17} />
        </button>
      </div>
      <div className="outline-tabs" role="group" aria-label="文献大纲视图">
        <button
          className={outlineView === "outline" ? "active" : ""}
          aria-pressed={outlineView === "outline"}
          onClick={() => setOutlineView("outline")}
        >
          大纲
        </button>
        <button
          className={outlineView === "thumbnails" ? "active" : ""}
          aria-pressed={outlineView === "thumbnails"}
          onClick={() => setOutlineView("thumbnails")}
        >
          缩略图
        </button>
      </div>
      {outlineView === "outline" ? (
        headings.length ? (
          headings.map((block) => (
            <button
              key={block.id}
              className={`outline-item ${focus === block.id ? "active" : ""}`}
              onClick={() => jump(block)}
            >
              {block.text.slice(0, 85)}
            </button>
          ))
        ) : (
          <p className="muted outline-empty">
            {activeVersion?.parse_status === "completed"
              ? "解析已完成，但没有识别出章节标题。"
              : "目录将在解析完成后显示。"}
          </p>
        )
      ) : (
        <PdfThumbnails
          versionId={version}
          count={activeVersion?.page_count || 1}
          page={page}
          onPage={goPage}
        />
      )}
    </aside>
  );
  const selectionPanel = (
    <aside className="panel reader-side-panel">
      <h3>划词翻译</h3>
      {selectedText ? (
        <>
          <p className="selected-source">{selectedText.slice(0, 1200)}</p>
          <button
            className="button secondary"
            disabled={translating}
            onClick={translateSelection}
          >
            {translating ? "翻译中…" : "译为中文"}
          </button>
          {translatedText && (
            <div className="selection-translation translation-markdown">
              <MathMarkdown>{translatedText}</MathMarkdown>
            </div>
          )}
        </>
      ) : (
        <p className="muted">在左侧 PDF 中选择文字，译文将在这里显示。</p>
      )}
      {mode === "assistant" && (
        <div className="reader-qa">
          <h3>论文问答</h3>
          {version !== paper?.preferred_version_id && (
            <p className="muted">问答检索默认版本的内容。</p>
          )}
          <textarea
            className="textarea"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="询问论文细节…"
          />
          <button className="button" disabled={!question.trim()} onClick={ask}>
            检索证据并回答
          </button>
          <div className="reader-run">
            <RunPanel runId={run} />
          </div>
        </div>
      )}
    </aside>
  );
  const documentPanel = version && (
    <div className="panel reader-document">
      <div className="document-toolbar">
        <button
          className="button secondary"
          disabled={page <= 1}
          onClick={() => goPage(page - 1)}
        >
          上一页
        </button>
        <span className="page-indicator">
          {page} / {activeVersion?.page_count || "—"} 页
        </span>
        <button
          className="button secondary"
          disabled={page >= (activeVersion?.page_count || 1)}
          onClick={() => goPage(page + 1)}
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
          setTranslatedText("");
          if (mode === "focus") changeMode("read");
        }}
        onCitationClick={jumpCitation}
      />
    </div>
  );
  return (
    <>
      <div className="reader-header">
        <div>
          <Link href="/library" className="back-link">
            ← 文献库
          </Link>
          <h1>{paper?.title || "论文阅读器"}</h1>
          <p className="lead">原文阅读 · 结构化大纲 · 可定位证据</p>
        </div>
        <div className="mode-switch" role="group" aria-label="阅读模式">
          {(
            [
              ["read", BookOpenText, "阅读"],
              ["assistant", PanelRight, "AI 辅助"],
              ["focus", Maximize2, "专注"],
              ["bilingual", Languages, "双语"],
            ] as const
          ).map(([value, Icon, label]) => (
            <button
              key={value}
              className={`button ${mode === value ? "active" : ""}`}
              aria-pressed={mode === value}
              onClick={() => changeMode(value)}
            >
              <Icon size={15} /> {label}
            </button>
          ))}
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
          {versions.map((item) => (
            <button
              key={item.id}
              className={
                item.id === version ? "button secondary" : "button ghost"
              }
              aria-pressed={item.id === version}
              onClick={() => switchVersion(item.id)}
            >
              {item.label} · {item.parse_status}
              {item.id === paper?.preferred_version_id ? " · 默认" : ""}
            </button>
          ))}
          {activeVersion &&
            ["completed", "failed"].includes(activeVersion.parse_status) && (
              <button
                className="button ghost"
                onClick={reparse}
                disabled={reparsing}
                title="重新提取目录、公式与内容块；未变化的译文保留"
              >
                <RefreshCw size={14} /> {reparsing ? "提交中…" : "重新解析"}
              </button>
            )}
        </div>
      )}
      {version &&
        (mode === "bilingual" ? (
          <BilingualReader
            key={version}
            version={version}
            pageCount={activeVersion?.page_count || 1}
            initialPage={page}
            outline={outline}
            outlineOpen={outlineOpen}
            onOutlineOpen={() => changeOutline(true)}
            jumpPage={jumpPage}
            onPageChange={setPage}
          />
        ) : mode === "focus" ? (
          <div className="reader-focus-document">{documentPanel}</div>
        ) : (
          <ReaderColumns
            outline={outline}
            center={documentPanel}
            right={selectionPanel}
            outlineOpen={outlineOpen}
            onOutlineOpen={() => changeOutline(true)}
          />
        ))}
      {references.length > 0 && mode !== "focus" && (
        <details className="panel references-panel">
          <summary>参考文献 · {references.length} 条</summary>
          <div className="list">
            {references.map((item) => (
              <button
                className="button ghost"
                key={item.id}
                onClick={() => jump(item)}
              >
                {item.text.slice(0, 240)}
              </button>
            ))}
          </div>
        </details>
      )}
    </>
  );
}
