"use client";

import { useCallback, useEffect, useRef } from "react";
import { nearestBlock, ReaderBlock } from "@/lib/reader";

type Options = {
  pdfRef: React.RefObject<HTMLDivElement | null>;
  translationRef: React.RefObject<HTMLDivElement | null>;
  blocksByPage: Record<number, ReaderBlock[]>;
  onPageChange: (pageIndex: number) => void;
  onNeedPage: (pageIndex: number) => void;
  onFocus: (block: ReaderBlock) => void;
  enabled: boolean;
};

const ANCHOR = 0.3;
const MANUAL_PAUSE = 1500;

export function useBilingualSync({
  pdfRef,
  translationRef,
  blocksByPage,
  onPageChange,
  onNeedPage,
  onFocus,
  enabled,
}: Options) {
  const pointerX = useRef<number | null>(null);
  const pausePdfSyncUntil = useRef(0);
  const programmaticUntil = useRef(0);
  const frame = useRef(0);

  const scrollToTranslation = useCallback(
    (block: ReaderBlock, fraction = 0) => {
      const container = translationRef.current;
      const target = container?.querySelector<HTMLElement>(
        `[data-block-id="${block.id}"]`,
      );
      if (!container || !target) return;
      const root = container.getBoundingClientRect();
      const rect = target.getBoundingClientRect();
      const delta =
        rect.top + fraction * rect.height - root.top - root.height * ANCHOR;
      if (Math.abs(delta) > 2) {
        programmaticUntil.current = Date.now() + 300;
        container.scrollTop += delta;
      }
    },
    [translationRef],
  );

  const scrollToPdf = useCallback(
    (block: ReaderBlock, fraction = 0) => {
      const container = pdfRef.current;
      const target = container?.querySelector<HTMLElement>(
        `[data-page-index="${block.page_index}"]`,
      );
      if (!container || !target || block.bbox.length !== 4) return;
      const root = container.getBoundingClientRect();
      const rect = target.getBoundingClientRect();
      const y = block.bbox[1] + fraction * (block.bbox[3] - block.bbox[1]);
      const delta =
        rect.top + y * rect.height - root.top - root.height * ANCHOR;
      if (Math.abs(delta) > 2) {
        programmaticUntil.current = Date.now() + 300;
        pausePdfSyncUntil.current = Date.now() + MANUAL_PAUSE;
        container.scrollTop += delta;
      }
    },
    [pdfRef],
  );

  useEffect(() => {
    const pdf = pdfRef.current;
    const translation = translationRef.current;
    if (!pdf || !translation || !enabled) return;
    const markManual = () => {
      pausePdfSyncUntil.current = Date.now() + MANUAL_PAUSE;
    };
    const pointer = (event: PointerEvent) => {
      pointerX.current = event.clientX;
    };
    const leave = () => {
      pointerX.current = null;
    };

    const syncFromPdf = () => {
      frame.current = 0;
      if (Date.now() < pausePdfSyncUntil.current) return;
      const root = pdf.getBoundingClientRect();
      const anchorY = root.top + root.height * ANCHOR;
      const element = document.elementFromPoint(
        root.left + root.width / 2,
        anchorY,
      );
      const page = element?.closest<HTMLElement>("[data-page-index]");
      if (!page) return;
      const pageIndex = Number(page.dataset.pageIndex);
      onPageChange(pageIndex);
      onNeedPage(pageIndex);
      const blocks = blocksByPage[pageIndex];
      if (!blocks?.length) return;
      const rect = page.getBoundingClientRect();
      const y = Math.max(0, Math.min(1, (anchorY - rect.top) / rect.height));
      const x =
        pointerX.current === null
          ? 0.25
          : Math.max(
              0,
              Math.min(1, (pointerX.current - rect.left) / rect.width),
            );
      const block = nearestBlock(blocks, x, y);
      if (!block) return;
      const fraction = Math.max(
        0,
        Math.min(
          1,
          (y - block.bbox[1]) / Math.max(0.01, block.bbox[3] - block.bbox[1]),
        ),
      );
      scrollToTranslation(block, fraction);
    };
    const onPdfScroll = () => {
      if (frame.current) return;
      frame.current = requestAnimationFrame(syncFromPdf);
    };
    const onTranslationScroll = () => {
      if (Date.now() < programmaticUntil.current) return;
      if (Date.now() >= pausePdfSyncUntil.current) return;
      const root = translation.getBoundingClientRect();
      const anchorY = root.top + root.height * ANCHOR;
      const elements =
        translation.querySelectorAll<HTMLElement>("[data-block-id]");
      let nearest: HTMLElement | null = null;
      let distance = Infinity;
      for (const element of elements) {
        const rect = element.getBoundingClientRect();
        const next = Math.max(rect.top - anchorY, 0, anchorY - rect.bottom);
        if (next < distance) {
          nearest = element;
          distance = next;
        }
      }
      if (!nearest) return;
      const page = nearest.closest<HTMLElement>("[data-page-index]");
      const block = blocksByPage[Number(page?.dataset.pageIndex)]?.find(
        (item) => item.id === nearest?.dataset.blockId,
      );
      if (!block) return;
      onFocus(block);
      onPageChange(block.page_index);
      const rect = nearest.getBoundingClientRect();
      const fraction = Math.max(
        0,
        Math.min(1, (anchorY - rect.top) / Math.max(rect.height, 1)),
      );
      scrollToPdf(block, fraction);
    };
    pdf.addEventListener("scroll", onPdfScroll, { passive: true });
    pdf.addEventListener("pointermove", pointer);
    pdf.addEventListener("pointerleave", leave);
    translation.addEventListener("wheel", markManual, { passive: true });
    translation.addEventListener("touchstart", markManual, { passive: true });
    translation.addEventListener("pointerdown", markManual);
    translation.addEventListener("keydown", markManual);
    translation.addEventListener("scroll", onTranslationScroll, {
      passive: true,
    });
    onPdfScroll();
    return () => {
      pdf.removeEventListener("scroll", onPdfScroll);
      pdf.removeEventListener("pointermove", pointer);
      pdf.removeEventListener("pointerleave", leave);
      translation.removeEventListener("wheel", markManual);
      translation.removeEventListener("touchstart", markManual);
      translation.removeEventListener("pointerdown", markManual);
      translation.removeEventListener("keydown", markManual);
      translation.removeEventListener("scroll", onTranslationScroll);
      if (frame.current) cancelAnimationFrame(frame.current);
    };
  }, [
    pdfRef,
    translationRef,
    blocksByPage,
    onPageChange,
    onNeedPage,
    onFocus,
    enabled,
    scrollToPdf,
    scrollToTranslation,
  ]);

  return { scrollToPdf, scrollToTranslation };
}
