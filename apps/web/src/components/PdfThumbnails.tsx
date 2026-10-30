"use client";

import { useEffect, useRef, useState } from "react";
import { apiFile } from "@/lib/api";

type Document = import("pdfjs-dist").PDFDocumentProxy;

function Thumbnail({
  document,
  index,
  selected,
  onSelect,
}: {
  document: Document;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const root = useRef<HTMLButtonElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { root: node.closest(".reader-outline"), rootMargin: "400px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    let task: import("pdfjs-dist").RenderTask | null = null;
    (async () => {
      try {
        const page = await document.getPage(index + 1);
        if (cancelled || !canvas.current) return;
        const base = page.getViewport({ scale: 1 });
        const view = page.getViewport({ scale: 122 / base.width });
        const target = canvas.current;
        target.width = Math.ceil(view.width);
        target.height = Math.ceil(view.height);
        task = page.render({
          canvas: target,
          canvasContext: target.getContext("2d")!,
          viewport: view,
        });
        await task.promise;
      } catch {
        /* The page or render task may be cancelled while scrolling. */
      }
    })();
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [document, index, visible]);
  return (
    <button
      ref={root}
      className={`thumbnail-item ${selected ? "active" : ""}`}
      onClick={onSelect}
    >
      <canvas ref={canvas} aria-hidden="true" />
      <span>第 {index + 1} 页</span>
    </button>
  );
}

export default function PdfThumbnails({
  versionId,
  count,
  page,
  onPage,
}: {
  versionId: string;
  count: number;
  page: number;
  onPage: (page: number) => void;
}) {
  const [document, setDocument] = useState<Document | null>(null);
  const [error, setError] = useState("");
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
        const result = await loading.promise;
        if (!cancelled) setDocument(result);
      } catch (cause) {
        if (!cancelled) setError(String(cause));
      }
    })();
    return () => {
      cancelled = true;
      loading?.destroy();
    };
  }, [versionId]);
  if (error) return <p className="error">缩略图加载失败：{error}</p>;
  if (!document) return <p className="muted">正在加载缩略图…</p>;
  return (
    <div className="thumbnail-list">
      {Array.from(
        { length: Math.min(count, document.numPages) },
        (_, index) => (
          <Thumbnail
            key={index}
            document={document}
            index={index}
            selected={page === index + 1}
            onSelect={() => onPage(index + 1)}
          />
        ),
      )}
    </div>
  );
}
