"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Languages, RefreshCw } from "lucide-react";
import { api, Paper } from "@/lib/api";
import { nearestBlock, ReaderBlock } from "@/lib/reader";
import ContinuousPdfViewer from "@/components/ContinuousPdfViewer";
import TranslationViewer from "@/components/TranslationViewer";
import { useBilingualSync } from "@/hooks/useBilingualSync";

type CachedTranslation = { block_id: string; page_index: number; text: string };

export default function Translate({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [paper, setPaper] = useState<Paper | null>(null);
  const [pageCount, setPageCount] = useState(1);
  const [activePage, setActivePage] = useState(0);
  const [blocksByPage, setBlocksByPage] = useState<
    Record<number, ReaderBlock[]>
  >({});
  const [translations, setTranslations] = useState<Record<string, string>>({});
  const [loadingPages, setLoadingPages] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [syncEnabled, setSyncEnabled] = useState(true);
  const [focus, setFocus] = useState<ReaderBlock | null>(null);
  const [error, setError] = useState("");
  const pdfRef = useRef<HTMLDivElement>(null);
  const translationRef = useRef<HTMLDivElement>(null);
  const pageNodes = useRef<(HTMLElement | null)[]>([]);
  const loaded = useRef(new Set<number>());
  const pending = useRef(new Set<number>());
  const version = paper?.preferred_version_id;

  useEffect(() => {
    api<{ paper: Paper; versions: { id: string; page_count: number }[] }>(
      `/papers/${id}`,
    )
      .then((result) => {
        setPaper(result.paper);
        setPageCount(
          result.versions.find(
            (item) => item.id === result.paper.preferred_version_id,
          )?.page_count || 1,
        );
      })
      .catch((cause) => setError(String(cause)));
  }, [id]);

  const loadPage = useCallback(
    async (index: number): Promise<ReaderBlock[] | undefined> => {
      if (
        !version ||
        index < 0 ||
        index >= pageCount ||
        loaded.current.has(index) ||
        pending.current.has(index)
      )
        return;
      pending.current.add(index);
      setLoadingPages((previous) => new Set(previous).add(index));
      try {
        const [parsed, cached] = await Promise.all([
          api<{ blocks: ReaderBlock[] }>(
            `/versions/${version}/blocks?page_start=${index}&page_end=${index}`,
          ),
          api<{ translations: CachedTranslation[] }>(
            `/versions/${version}/translations?page_index=${index}&target_lang=zh`,
          ),
        ]);
        loaded.current.add(index);
        setBlocksByPage((previous) => ({
          ...previous,
          [index]: parsed.blocks,
        }));
        setTranslations((previous) => ({
          ...previous,
          ...Object.fromEntries(
            cached.translations.map((item) => [item.block_id, item.text]),
          ),
        }));
        return parsed.blocks;
      } catch (cause) {
        setError(String(cause));
      } finally {
        pending.current.delete(index);
        setLoadingPages((previous) => {
          const next = new Set(previous);
          next.delete(index);
          return next;
        });
      }
    },
    [version, pageCount],
  );

  const needPage = useCallback(
    (index: number) => {
      void loadPage(index);
    },
    [loadPage],
  );
  const onPageChange = useCallback((index: number) => setActivePage(index), []);
  const onFocus = useCallback((block: ReaderBlock) => setFocus(block), []);
  const { scrollToPdf, scrollToTranslation } = useBilingualSync({
    pdfRef,
    translationRef,
    blocksByPage,
    onPageChange,
    onNeedPage: needPage,
    onFocus,
    enabled: syncEnabled && Boolean(version),
  });

  useEffect(() => {
    if (!version) return;
    const frame = requestAnimationFrame(() => {
      [0, 1].filter((index) => index < pageCount).forEach(needPage);
    });
    return () => cancelAnimationFrame(frame);
  }, [version, pageCount, needPage]);

  useEffect(() => {
    if (!version || !translationRef.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting)
            needPage(Number((entry.target as HTMLElement).dataset.pageIndex));
        }
      },
      { root: translationRef.current, rootMargin: "700px 0px" },
    );
    pageNodes.current
      .slice(0, pageCount)
      .forEach((node) => node && observer.observe(node));
    return () => observer.disconnect();
  }, [version, pageCount, needPage]);

  async function translatePage() {
    if (!version) return;
    const blocks = blocksByPage[activePage] || (await loadPage(activePage));
    if (!blocks?.length) return;
    setBusy(true);
    setError("");
    try {
      for (let start = 0; start < blocks.length; start += 60) {
        const result = await api<{ translations: CachedTranslation[] }>(
          "/translate",
          {
            method: "POST",
            body: JSON.stringify({
              version_id: version,
              block_ids: blocks
                .slice(start, start + 60)
                .map((block) => block.id),
              target_lang: "zh",
            }),
          },
        );
        setTranslations((previous) => ({
          ...previous,
          ...Object.fromEntries(
            result.translations.map((item) => [item.block_id, item.text]),
          ),
        }));
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  function goPage(index: number) {
    const target = pdfRef.current?.querySelector<HTMLElement>(
      `[data-page-index="${index}"]`,
    );
    const root = pdfRef.current;
    if (!target || !root) return;
    root.scrollTop +=
      target.getBoundingClientRect().top - root.getBoundingClientRect().top;
    setActivePage(index);
    needPage(index);
  }

  async function selectPdfLocation(index: number, x: number, y: number) {
    const blocks = blocksByPage[index] || (await loadPage(index));
    const block = nearestBlock(blocks || [], x, y);
    if (!block) return;
    setFocus(block);
    setActivePage(index);
    requestAnimationFrame(() => scrollToTranslation(block));
  }

  const current = blocksByPage[activePage] || [];
  const translatedCount = current.filter(
    (block) => translations[block.id] !== undefined,
  ).length;
  return (
    <>
      <div className="reader-header">
        <div>
          <Link href={`/papers/${id}`} className="back-link">
            ← 返回论文
          </Link>
          <h1>双语连续阅读</h1>
          <p className="lead">{paper?.title}</p>
        </div>
        <span className="badge">
          <Languages size={15} /> 原文与译文联动
        </span>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="version-bar bilingual-toolbar">
        <button
          className="button secondary"
          disabled={activePage === 0}
          onClick={() => goPage(activePage - 1)}
        >
          <ArrowLeft size={15} /> 上一页
        </button>
        <span className="page-indicator">
          第 {activePage + 1} / {pageCount} 页
        </span>
        <button
          className="button secondary"
          disabled={activePage >= pageCount - 1}
          onClick={() => goPage(activePage + 1)}
        >
          下一页 <ArrowRight size={15} />
        </button>
        <span className="toolbar-spacer" />
        <label className="sync-toggle">
          <input
            type="checkbox"
            checked={syncEnabled}
            onChange={(event) => setSyncEnabled(event.target.checked)}
          />{" "}
          双向滚动联动
        </label>
        <span className="muted">
          本页已译 {translatedCount} / {current.length}
        </span>
        <button
          className="button"
          onClick={translatePage}
          disabled={
            !version || busy || loadingPages.has(activePage) || !current.length
          }
        >
          <RefreshCw size={15} /> {busy ? "翻译中…" : "翻译当前页"}
        </button>
      </div>
      {version ? (
        <div className="translation-workspace continuous-layout">
          <ContinuousPdfViewer
            versionId={version}
            pageCount={pageCount}
            scrollRef={pdfRef}
            focus={
              focus ? { pageIndex: focus.page_index, bbox: focus.bbox } : null
            }
            onPageChange={onPageChange}
            onLocationClick={selectPdfLocation}
          />
          <div
            className="continuous-translation-scroll"
            ref={translationRef}
            aria-label="连续中文译文"
          >
            {Array.from({ length: pageCount }, (_, index) => (
              <section
                className="translation-page"
                data-page-index={index}
                key={index}
                ref={(node) => {
                  pageNodes.current[index] = node;
                }}
              >
                <div className="translation-page-head">
                  <span>第 {index + 1} 页</span>
                  <small>{blocksByPage[index]?.length ?? "…"} 个内容块</small>
                </div>
                {loadingPages.has(index) && (
                  <p className="muted">正在加载此页内容…</p>
                )}
                {blocksByPage[index]?.map((block) => (
                  <TranslationViewer
                    key={block.id}
                    block={block}
                    translation={translations[block.id]}
                    active={focus?.id === block.id}
                    onSelect={() => {
                      setFocus(block);
                      setActivePage(index);
                      scrollToPdf(block);
                    }}
                  />
                ))}
                {!blocksByPage[index] && !loadingPages.has(index) && (
                  <p className="muted">滚动到此页加载内容</p>
                )}
                {blocksByPage[index]?.length === 0 && (
                  <p className="muted">此页没有可提取的文本块。</p>
                )}
              </section>
            ))}
          </div>
        </div>
      ) : (
        <div className="panel muted">这篇论文尚无可阅读的 PDF 版本。</div>
      )}
    </>
  );
}
