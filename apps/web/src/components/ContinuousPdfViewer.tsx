"use client";

import { useEffect, useRef, useState } from "react";
import { apiFile } from "@/lib/api";

type PdfDocument = import("pdfjs-dist").PDFDocumentProxy;
type PdfPage = import("pdfjs-dist").PDFPageProxy;

type Props = {
  versionId: string;
  pageCount: number;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  focus: { pageIndex: number; bbox: number[] } | null;
  onPageChange: (pageIndex: number) => void;
  onLocationClick: (pageIndex: number, x: number, y: number) => void;
  initialPage?: number;
};

function Page({
  document,
  pageIndex,
  width,
  visible,
  focus,
  register,
  onLocationClick,
}: {
  document: PdfDocument;
  pageIndex: number;
  width: number;
  visible: boolean;
  focus: number[] | null;
  register: (element: HTMLDivElement | null) => void;
  onLocationClick: Props["onLocationClick"];
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const layerRef = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState<{
    width: number;
    height: number;
  } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!visible || !width) return;
    let cancelled = false;
    let renderTask: import("pdfjs-dist").RenderTask | null = null;
    (async () => {
      try {
        const page: PdfPage = await document.getPage(pageIndex + 1);
        if (cancelled) return;
        const base = page.getViewport({ scale: 1 });
        const view = page.getViewport({
          scale: Math.max(0.2, (width - 28) / base.width),
        });
        const canvas = canvasRef.current;
        if (!canvas) return;
        const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = Math.ceil(view.width * pixelRatio);
        canvas.height = Math.ceil(view.height * pixelRatio);
        canvas.style.width = `${view.width}px`;
        canvas.style.height = `${view.height}px`;
        setViewport({ width: view.width, height: view.height });
        renderTask = page.render({
          canvas,
          canvasContext: canvas.getContext("2d")!,
          viewport: view,
          transform: [pixelRatio, 0, 0, pixelRatio, 0, 0],
        });
        await renderTask.promise;
        if (cancelled || !layerRef.current) return;
        const layer = layerRef.current;
        layer.innerHTML = "";
        layer.style.width = `${view.width}px`;
        layer.style.height = `${view.height}px`;
        layer.style.setProperty("--total-scale-factor", String(view.scale));
        const pdfjs = await import("pdfjs-dist");
        await new pdfjs.TextLayer({
          textContentSource: await page.getTextContent(),
          container: layer,
          viewport: view,
        }).render();
      } catch (cause) {
        if (!cancelled && String(cause) !== "RenderingCancelledException")
          setError(String(cause));
      }
    })();
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [document, pageIndex, visible, width]);

  return (
    <div
      className="continuous-pdf-page"
      data-page-index={pageIndex}
      ref={register}
    >
      <div className="pdf-page-number">{pageIndex + 1}</div>
      {error && <div className="error">{error}</div>}
      {visible && (
        <div
          className="pdf-page-content"
          style={
            viewport
              ? { width: viewport.width, height: viewport.height }
              : undefined
          }
          onClick={(event) => {
            const canvas = canvasRef.current;
            if (!canvas) return;
            const rect = canvas.getBoundingClientRect();
            onLocationClick(
              pageIndex,
              Math.max(
                0,
                Math.min(1, (event.clientX - rect.left) / rect.width),
              ),
              Math.max(
                0,
                Math.min(1, (event.clientY - rect.top) / rect.height),
              ),
            );
          }}
        >
          <canvas ref={canvasRef} />
          <div ref={layerRef} className="textLayer" />
          {focus && focus.length === 4 && (
            <div
              className="pdf-focus-box"
              style={{
                left: `${focus[0] * 100}%`,
                top: `${focus[1] * 100}%`,
                width: `${(focus[2] - focus[0]) * 100}%`,
                height: `${(focus[3] - focus[1]) * 100}%`,
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

export default function ContinuousPdfViewer({
  versionId,
  pageCount,
  scrollRef,
  focus,
  onPageChange,
  onLocationClick,
  initialPage = 1,
}: Props) {
  const [document, setDocument] = useState<PdfDocument | null>(null);
  const [count, setCount] = useState(pageCount);
  const [width, setWidth] = useState(700);
  const [visible, setVisible] = useState(() => new Set([0, 1]));
  const [error, setError] = useState("");
  const nodes = useRef<(HTMLDivElement | null)[]>([]);
  const currentPage = useRef(-1);

  useEffect(() => {
    let cancelled = false;
    let loading: import("pdfjs-dist").PDFDocumentLoadingTask | null = null;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.min.mjs",
          import.meta.url,
        ).toString();
        const file = await apiFile(`/versions/${versionId}/file`);
        loading = pdfjs.getDocument({
          data: new Uint8Array(await file.arrayBuffer()),
          wasmUrl: "/pdfjs/wasm/",
        });
        const pdf = await loading.promise;
        if (!cancelled) {
          setDocument(pdf);
          setCount(pdf.numPages);
        }
      } catch (cause) {
        if (!cancelled) setError(String(cause));
      }
    })();
    return () => {
      cancelled = true;
      loading?.destroy();
    };
  }, [versionId]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(entry.contentRect.width),
    );
    observer.observe(container);
    return () => observer.disconnect();
  }, [scrollRef]);

  useEffect(() => {
    if (!document || initialPage <= 1) return;
    const target = nodes.current[initialPage - 1];
    const container = scrollRef.current;
    if (target && container) container.scrollTop = target.offsetTop;
  }, [document, initialPage, scrollRef]);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container || !document) return;
    const observer = new IntersectionObserver(
      (entries) => {
        setVisible((previous) => {
          const next = new Set(previous);
          for (const entry of entries) {
            const index = Number(
              (entry.target as HTMLElement).dataset.pageIndex,
            );
            if (entry.isIntersecting) next.add(index);
            else next.delete(index);
          }
          return next;
        });
      },
      { root: container, rootMargin: "900px 0px" },
    );
    nodes.current
      .slice(0, count)
      .forEach((node) => node && observer.observe(node));
    return () => observer.disconnect();
  }, [document, count, scrollRef]);

  function updatePage() {
    const container = scrollRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const element = window.document.elementFromPoint(
      rect.left + rect.width / 2,
      rect.top + rect.height * 0.3,
    );
    const page = element?.closest<HTMLElement>("[data-page-index]");
    if (!page) return;
    const index = Number(page.dataset.pageIndex);
    if (index !== currentPage.current) {
      currentPage.current = index;
      onPageChange(index);
    }
  }

  return (
    <div
      ref={scrollRef}
      className="continuous-pdf-scroll"
      onScroll={updatePage}
      aria-label="连续 PDF 阅读器"
    >
      {error && <div className="error">{error}</div>}
      {!document && !error && (
        <div className="muted pdf-loading">正在加载 PDF…</div>
      )}
      {document &&
        Array.from({ length: count }, (_, index) => (
          <Page
            key={index}
            document={document}
            pageIndex={index}
            width={width}
            visible={visible.has(index)}
            focus={focus?.pageIndex === index ? focus.bbox : null}
            register={(element) => {
              nodes.current[index] = element;
            }}
            onLocationClick={onLocationClick}
          />
        ))}
    </div>
  );
}
