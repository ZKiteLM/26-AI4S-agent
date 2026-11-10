"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { api, Paper } from "@/lib/api";
import PdfThumbnails from "@/components/PdfThumbnails";
import ReaderColumns from "@/components/ReaderColumns";
import BilingualPane from "@/components/BilingualPane";
import ContinuousPdfViewer from "@/components/ContinuousPdfViewer";
import MathMarkdown from "@/components/MathMarkdown";
import ChatWorkspace from "@/components/ChatWorkspace";
import { pdfPageAtAnchor, ReaderBlock } from "@/lib/reader";
import {
  BookOpenText,
  Languages,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRight,
  RefreshCw,
  RotateCcw,
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
type RightMode = "none" | "selection" | "bilingual";
type PdfClick = { pageIndex: number; x: number; y: number; nonce: number };

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
  const [initialPage, setInitialPage] = useState(1);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [rightMode, setRightMode] = useState<RightMode>("none");
  const [aiOpen, setAiOpen] = useState(false);
  const [outlineOpen, setOutlineOpen] = useState(true);
  const [resetToken, setResetToken] = useState(0);
  const [outlineView, setOutlineView] = useState<"outline" | "thumbnails">(
    "outline",
  );
  const [jumpPage, setJumpPage] = useState<{
    page: number;
    nonce: number;
    y?: number;
  } | null>(null);
  const [pdfClick, setPdfClick] = useState<PdfClick | null>(null);
  const [bilingualFocus, setBilingualFocus] = useState<ReaderBlock | null>(
    null,
  );
  const [error, setError] = useState("");
  const [selectedText, setSelectedText] = useState("");
  const [translatedText, setTranslatedText] = useState("");
  const [returnPage, setReturnPage] = useState<number | null>(null);
  const [reparsing, setReparsing] = useState(false);
  const [translating, setTranslating] = useState(false);
  const pdfRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const url = new URL(window.location.href);
    const initial = Math.max(1, Number(url.searchParams.get("page") || 1));
    const legacy = url.searchParams.get("mode");
    const panel = url.searchParams.get("panel");
    const frame = requestAnimationFrame(() => {
      setPage(initial);
      setInitialPage(initial);
      setFocusId(url.searchParams.get("block"));
      setSelectedVersion(url.searchParams.get("version"));
      if (panel === "selection" || panel === "bilingual") setRightMode(panel);
      else if (legacy === "bilingual") setRightMode("bilingual");
      else if (legacy === "assistant") {
        setAiOpen(true);
      }
      setAiOpen((previous) => previous || url.searchParams.get("ai") === "1");
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
    (selectedVersion && versions.some((item) => item.id === selectedVersion)
      ? selectedVersion
      : null) ||
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
    nextPage = page,
    nextFocus: string | null = focusId,
    nextPanel = rightMode,
    nextAi = aiOpen,
    nextVersion = version,
  ) {
    const url = new URL(window.location.href);
    url.searchParams.set("page", String(nextPage));
    url.searchParams.delete("mode");
    if (nextFocus) url.searchParams.set("block", nextFocus);
    else url.searchParams.delete("block");
    if (nextPanel === "none") url.searchParams.delete("panel");
    else url.searchParams.set("panel", nextPanel);
    if (nextAi) url.searchParams.set("ai", "1");
    else url.searchParams.delete("ai");
    if (nextVersion && nextVersion !== paper?.preferred_version_id)
      url.searchParams.set("version", nextVersion);
    else url.searchParams.delete("version");
    url.searchParams.delete("focus");
    window.history.replaceState(null, "", url.pathname + url.search);
  }
  function keepPdfPosition(change: () => void) {
    const container = pdfRef.current;
    const anchor = container ? pdfPageAtAnchor(container) : null;
    const pageIndex = anchor?.page.dataset.pageIndex;
    change();
    if (!container || !anchor || pageIndex === undefined) return;
    requestAnimationFrame(() => {
      const next = container.querySelector<HTMLElement>(
        `[data-page-index="${pageIndex}"]`,
      );
      if (next)
        container.scrollTop =
          next.offsetTop +
          anchor.fraction * next.clientHeight -
          container.clientHeight * 0.3;
    });
  }
  function toggleRight(next: Exclude<RightMode, "none">) {
    const value = rightMode === next ? "none" : next;
    keepPdfPosition(() => {
      setRightMode(value);
      setBilingualFocus(null);
      updateUrl(page, focusId, value);
    });
  }
  function toggleAi() {
    keepPdfPosition(() => {
      setAiOpen(!aiOpen);
      updateUrl(page, focusId, rightMode, !aiOpen);
    });
  }
  function changeOutline(open: boolean) {
    keepPdfPosition(() => {
      setOutlineOpen(open);
      window.localStorage.setItem("reader-outline-open", String(open));
    });
  }
  function goPage(next: number, blockId: string | null = null) {
    const target = Math.max(1, Math.min(activeVersion?.page_count || 1, next));
    setPage(target);
    setFocusId(blockId);
    const y = blockId
      ? blocks.find((block) => block.id === blockId)?.bbox[1]
      : undefined;
    setJumpPage((previous) => ({
      page: target,
      nonce: (previous?.nonce || 0) + 1,
      y,
    }));
    updateUrl(target, blockId);
  }
  function jumpToChatReference(
    targetPage: number,
    blockId: string,
    sourceVersionId?: string,
  ) {
    if (
      sourceVersionId &&
      sourceVersionId !== version &&
      versions.some((item) => item.id === sourceVersionId)
    ) {
      const target = Math.max(1, targetPage);
      setSelectedVersion(sourceVersionId);
      setBlocks([]);
      setInitialPage(target);
      setPage(target);
      setFocusId(blockId);
      setJumpPage((previous) => ({
        page: target,
        nonce: (previous?.nonce || 0) + 1,
      }));
      updateUrl(target, blockId, rightMode, aiOpen, sourceVersionId);
      return;
    }
    goPage(targetPage, blockId);
  }
  function switchVersion(next: string) {
    if (next === version) return;
    setSelectedVersion(next);
    setBlocks([]);
    setSelectedText("");
    setTranslatedText("");
    setBilingualFocus(null);
    setInitialPage(1);
    setPage(1);
    setFocusId(null);
    setJumpPage((previous) => ({ page: 1, nonce: (previous?.nonce || 0) + 1 }));
    updateUrl(1, null, rightMode, aiOpen, next);
  }
  const references = blocks.filter((block) => block.type === "reference");
  function jumpCitation(number: number) {
    const reference = references.find((item) =>
      new RegExp(`^\\s*\\[\\s*${number}\\s*\\]`).test(item.text),
    );
    if (reference) {
      setReturnPage(page);
      goPage(reference.page_index + 1, reference.id);
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
  const focused = blocks.find((block) => block.id === focusId);
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
              className={`outline-item ${focusId === block.id ? "active" : ""}`}
              onClick={() => goPage(block.page_index + 1, block.id)}
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
  const selectionPanel = rightMode === "selection" && (
    <section className="reader-module selection-pane">
      <div className="reader-module-head">
        <strong>划词翻译</strong>
      </div>
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
        <p className="muted">在 PDF 中选择文字，译文将在这里显示。</p>
      )}
    </section>
  );
  const aiPanel = aiOpen && (
    <section className="reader-module reader-ai-pane">
      <div className="reader-module-head">
        <strong>AI 问答</strong>
      </div>
      {version !== paper?.preferred_version_id && (
        <p className="muted">问答检索默认版本的内容。</p>
      )}
      <ChatWorkspace paperId={id} onReferenceClick={jumpToChatReference} />
    </section>
  );
  const right = (rightMode !== "none" || aiOpen) && version && (
    <aside className={`reader-side-stack ${aiOpen ? "with-ai" : ""}`}>
      {selectionPanel}
      {rightMode === "bilingual" && (
        <BilingualPane
          key={version}
          version={version}
          pageCount={activeVersion?.page_count || 1}
          page={page}
          pdfRef={pdfRef}
          pdfClick={pdfClick}
          onPageChange={setPage}
          onFocus={setBilingualFocus}
        />
      )}
      {aiPanel}
    </aside>
  );

  return (
    <div className="paper-reader-page">
      {error && <div className="error reader-inline-error">{error}</div>}
      {paper && !versions.length && (
        <div className="panel reader-status">
          此记录只有论文元数据，尚未导入 PDF。
        </div>
      )}
      {activeVersion && activeVersion.parse_status !== "completed" && (
        <div className="reader-status-inline">
          {activeVersion.parse_status === "failed"
            ? "解析失败，请到任务动态查看。"
            : activeVersion.parse_status === "conflict"
              ? "此版本等待冲突处理。"
              : "此版本正在解析；页面会自动更新。"}
          <Link href="/tasks">查看任务动态</Link>
        </div>
      )}
      {versions.length > 0 && (
        <div className="version-bar reader-version-bar">
          <span className="paper-title" title={paper?.title || ""}>
            {paper?.title || "论文"}
          </span>
          <span className="section-label">版本</span>
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
      {version && (
        <>
          <div className="reader-control-bar">
            <div className="reader-page-controls">
              <button
                className="button ghost"
                disabled={page <= 1}
                onClick={() => goPage(page - 1)}
              >
                上一页
              </button>
              <span className="page-indicator">
                {page} / {activeVersion?.page_count || "—"} 页
              </span>
              <button
                className="button ghost"
                disabled={page >= (activeVersion?.page_count || 1)}
                onClick={() => goPage(page + 1)}
              >
                下一页
              </button>
              {!outlineOpen && (
                <button
                  className="button ghost"
                  onClick={() => changeOutline(true)}
                >
                  <PanelLeftOpen size={15} /> 显示文献大纲
                </button>
              )}
              <button
                className="button ghost"
                onClick={() =>
                  keepPdfPosition(() => setResetToken((value) => value + 1))
                }
                title="恢复默认面板比例"
              >
                <RotateCcw size={14} /> 恢复默认比例
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
            <div
              className="reader-module-toggles"
              role="group"
              aria-label="阅读工具"
            >
              <button
                className={`button ${rightMode === "selection" ? "active" : "ghost"}`}
                aria-pressed={rightMode === "selection"}
                onClick={() => toggleRight("selection")}
              >
                <BookOpenText size={15} /> 划词翻译
              </button>
              <button
                className={`button ${rightMode === "bilingual" ? "active" : "ghost"}`}
                aria-pressed={rightMode === "bilingual"}
                onClick={() => toggleRight("bilingual")}
              >
                <Languages size={15} /> 双语滚动
              </button>
              <button
                className={`button ${aiOpen ? "active" : "ghost"}`}
                aria-pressed={aiOpen}
                onClick={toggleAi}
              >
                <PanelRight size={15} /> AI 问答
              </button>
            </div>
          </div>
          <ReaderColumns
            outline={outline}
            outlineOpen={outlineOpen}
            resetToken={resetToken}
            center={
              <ContinuousPdfViewer
                key={version}
                versionId={version}
                pageCount={activeVersion?.page_count || 1}
                initialPage={initialPage}
                jumpPage={jumpPage}
                scrollRef={pdfRef}
                focus={
                  bilingualFocus && rightMode === "bilingual"
                    ? {
                        pageIndex: bilingualFocus.page_index,
                        bbox: bilingualFocus.bbox,
                      }
                    : focused
                      ? { pageIndex: focused.page_index, bbox: focused.bbox }
                      : null
                }
                onPageChange={(index) => setPage(index + 1)}
                onSelectText={
                  rightMode === "selection"
                    ? (text) => {
                        setSelectedText(text);
                        setTranslatedText("");
                      }
                    : undefined
                }
                onCitationClick={jumpCitation}
                onLocationClick={(pageIndex, x, y) => {
                  if (rightMode !== "bilingual") return;
                  setPdfClick((previous) => ({
                    pageIndex,
                    x,
                    y,
                    nonce: (previous?.nonce || 0) + 1,
                  }));
                }}
              />
            }
            right={right || undefined}
          />
        </>
      )}
    </div>
  );
}
