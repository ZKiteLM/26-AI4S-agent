"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { nearestBlock, pdfPageAtAnchor, ReaderBlock } from "@/lib/reader";
import TranslationViewer from "@/components/TranslationViewer";
import OperationProgress from "@/components/OperationProgress";
import { useBilingualSync } from "@/hooks/useBilingualSync";

type CachedTranslation = { block_id: string; page_index: number; text: string };
type PdfClick = { pageIndex: number; x: number; y: number; nonce: number };

export default function BilingualPane({
  version,
  pageCount,
  page,
  pdfRef,
  pdfClick,
  onPageChange,
  onFocus,
}: {
  version: string;
  pageCount: number;
  page: number;
  pdfRef: React.RefObject<HTMLDivElement | null>;
  pdfClick: PdfClick | null;
  onPageChange: (page: number) => void;
  onFocus: (block: ReaderBlock) => void;
}) {
  const [blocksByPage, setBlocksByPage] = useState<
    Record<number, ReaderBlock[]>
  >({});
  const [translations, setTranslations] = useState<Record<string, string>>({});
  const [loadingPages, setLoadingPages] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [syncEnabled, setSyncEnabled] = useState(true);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const translationRef = useRef<HTMLDivElement>(null);
  const pageNodes = useRef<(HTMLElement | null)[]>([]);
  const cache = useRef(new Map<number, ReaderBlock[]>());
  const pending = useRef(new Map<number, Promise<ReaderBlock[] | undefined>>());
  const initialAligned = useRef(false);

  const loadPage = useCallback(
    (index: number): Promise<ReaderBlock[] | undefined> => {
      if (index < 0 || index >= pageCount) return Promise.resolve(undefined);
      if (cache.current.has(index))
        return Promise.resolve(cache.current.get(index));
      const inflight = pending.current.get(index);
      if (inflight) return inflight;
      setLoadingPages((previous) => new Set(previous).add(index));
      const request = Promise.all([
        api<{ blocks: ReaderBlock[] }>(
          `/versions/${version}/blocks?page_start=${index}&page_end=${index}`,
        ),
        api<{ translations: CachedTranslation[] }>(
          `/versions/${version}/translations?page_index=${index}&target_lang=zh`,
        ),
      ])
        .then(([parsed, cached]) => {
          cache.current.set(index, parsed.blocks);
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
        })
        .catch((cause) => {
          setError(String(cause));
          return undefined;
        })
        .finally(() => {
          pending.current.delete(index);
          setLoadingPages((previous) => {
            const next = new Set(previous);
            next.delete(index);
            return next;
          });
        });
      pending.current.set(index, request);
      return request;
    },
    [version, pageCount],
  );

  const needPage = useCallback(
    (index: number) => {
      void loadPage(index);
    },
    [loadPage],
  );
  const syncPage = useCallback(
    (index: number) => onPageChange(index + 1),
    [onPageChange],
  );
  const syncFocus = useCallback(
    (block: ReaderBlock) => {
      setFocusedId(block.id);
      onFocus(block);
    },
    [onFocus],
  );
  const { scrollToPdf, scrollToTranslation } = useBilingualSync({
    pdfRef,
    translationRef,
    blocksByPage,
    onPageChange: syncPage,
    onNeedPage: needPage,
    onFocus: syncFocus,
    enabled: syncEnabled,
  });

  useEffect(() => {
    if (initialAligned.current || !syncEnabled) return;
    const pdf = pdfRef.current;
    const translation = translationRef.current;
    if (!pdf || !translation) return;
    const anchor = pdfPageAtAnchor(pdf);
    if (!anchor) return;
    const index = Number(anchor.page.dataset.pageIndex);
    const blocks = blocksByPage[index];
    if (!blocks?.length) return;
    const block = nearestBlock(blocks, 0.25, anchor.fraction);
    if (!block || !translation.querySelector(`[data-block-id="${block.id}"]`))
      return;
    scrollToTranslation(block);
    initialAligned.current = true;
  }, [blocksByPage, pdfRef, scrollToTranslation, syncEnabled]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      needPage(page - 1);
      needPage(Math.min(page, pageCount - 1));
    });
    return () => cancelAnimationFrame(frame);
  }, [page, pageCount, needPage]);

  useEffect(() => {
    const root = translationRef.current;
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting)
            needPage(Number((entry.target as HTMLElement).dataset.pageIndex));
        }
      },
      { root, rootMargin: "700px 0px" },
    );
    pageNodes.current
      .slice(0, pageCount)
      .forEach((node) => node && observer.observe(node));
    return () => observer.disconnect();
  }, [pageCount, needPage]);

  useEffect(() => {
    if (!pdfClick) return;
    let cancelled = false;
    void loadPage(pdfClick.pageIndex).then((blocks) => {
      if (cancelled) return;
      const block = nearestBlock(blocks || [], pdfClick.x, pdfClick.y);
      if (!block) return;
      syncFocus(block);
      requestAnimationFrame(() => scrollToTranslation(block));
    });
    return () => {
      cancelled = true;
    };
  }, [pdfClick, loadPage, scrollToTranslation, syncFocus]);

  async function translatePage() {
    const index = page - 1;
    const blocks = await loadPage(index);
    if (!blocks?.length) return;
    setBusy(true);
    setError("");
    try {
      for (let start = 0; start < blocks.length; start += 60) {
        const result = await api<{ translations: CachedTranslation[] }>(
          "/translate",
          {
            method: "POST",
            progress: "inline",
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

  const current = blocksByPage[page - 1] || [];
  const translatedCount = current.filter(
    (block) => translations[block.id] !== undefined,
  ).length;
  return (
    <section className="bilingual-pane" aria-label="双语滚动">
      <div className="reader-module-head">
        <strong>双语滚动</strong>
        <label className="sync-toggle">
          <input
            type="checkbox"
            checked={syncEnabled}
            onChange={(event) => setSyncEnabled(event.target.checked)}
          />{" "}
          联动
        </label>
      </div>
      <div className="bilingual-actions">
        <span className="muted">
          本页已译 {translatedCount} / {current.length}
        </span>
        <button
          className="button secondary"
          onClick={translatePage}
          disabled={busy || loadingPages.has(page - 1) || !current.length}
        >
          <RefreshCw size={14} /> {busy ? "翻译中…" : "翻译当前页"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {busy && (
        <OperationProgress
          title="正在翻译当前页"
          detail="译文将保留公式并逐段显示。"
        />
      )}
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
                active={focusedId === block.id}
                onSelect={() => {
                  syncFocus(block);
                  onPageChange(index + 1);
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
    </section>
  );
}
