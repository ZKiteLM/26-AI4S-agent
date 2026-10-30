"use client";
import { useEffect, useRef, useState } from "react";
import { apiFile } from "@/lib/api";

export default function PdfCanvas({
  versionId,
  page,
  bbox,
  onSelectText,
  onCitationClick,
  onLocationClick,
}: {
  versionId: string;
  page: number;
  bbox?: number[];
  onSelectText?: (text: string) => void;
  onCitationClick?: (number: number) => void;
  onLocationClick?: (x: number, y: number) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const textLayerContainer = useRef<HTMLDivElement>(null);
  const [document, setDocument] = useState<
    import("pdfjs-dist").PDFDocumentProxy | null
  >(null);
  const [count, setCount] = useState(0),
    [error, setError] = useState("");
  const [width, setWidth] = useState(700);
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver((entries) =>
      setWidth(entries[0].contentRect.width),
    );
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let cancelled = false;
    let loadingTask: import("pdfjs-dist").PDFDocumentLoadingTask | null = null;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.min.mjs",
          import.meta.url,
        ).toString();
        const blob = await apiFile(`/versions/${versionId}/file`);
        loadingTask = pdfjs.getDocument({
          data: new Uint8Array(await blob.arrayBuffer()),
          wasmUrl: "/pdfjs/wasm/",
        });
        const pdf = await loadingTask.promise;
        if (!cancelled) {
          setDocument(pdf);
          setCount(pdf.numPages);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
      loadingTask?.destroy();
    };
  }, [versionId]);
  useEffect(() => {
    if (!document || !canvas || !page) return;
    let cancelled = false;
    let task: import("pdfjs-dist").RenderTask | null = null;
    (async () => {
      try {
        const pdfPage = await document.getPage(
          Math.min(Math.max(page, 1), document.numPages),
        );
        if (cancelled) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const viewport = pdfPage.getViewport({
          scale: Math.min(1.8, Math.max(0.4, (width - 40) / base.width)),
        });
        const element = canvas.current!;
        element.width = viewport.width;
        element.height = viewport.height;
        task = pdfPage.render({
          canvas: element,
          canvasContext: element.getContext("2d")!,
          viewport,
        });
        await task.promise;
        if (textLayerContainer.current) {
          const layer = textLayerContainer.current;
          layer.innerHTML = "";
          layer.style.width = `${viewport.width}px`;
          layer.style.height = `${viewport.height}px`;
          layer.style.setProperty(
            "--total-scale-factor",
            String(viewport.scale),
          );
          const pdfjs = await import("pdfjs-dist");
          await new pdfjs.TextLayer({
            textContentSource: await pdfPage.getTextContent(),
            container: layer,
            viewport,
          }).render();
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [document, page, width]);
  return (
    <div>
      <div className="section-label">
        PDF · {page} / {count || "…"}
      </div>
      {error && <div className="error">{error}</div>}
      <div className="reader-canvas" ref={container}>
        <div
          style={{ position: "relative", display: "inline-block" }}
          onMouseUp={() => {
            const text = window.getSelection()?.toString().trim();
            if (text) onSelectText?.(text);
          }}
          onClick={(event) => {
            const match = (event.target as HTMLElement).textContent?.match(
              /\[\s*(\d+)\s*\]/,
            );
            if (match) onCitationClick?.(Number(match[1]));
            const rect = canvas.current?.getBoundingClientRect();
            if (rect)
              onLocationClick?.(
                (event.clientX - rect.left) / rect.width,
                (event.clientY - rect.top) / rect.height,
              );
          }}
        >
          <canvas ref={canvas} />
          <div ref={textLayerContainer} className="textLayer" />
          {bbox && bbox.length === 4 && (
            <div
              aria-label="证据位置"
              style={{
                position: "absolute",
                left: `${bbox[0] * 100}%`,
                top: `${bbox[1] * 100}%`,
                width: `${(bbox[2] - bbox[0]) * 100}%`,
                height: `${(bbox[3] - bbox[1]) * 100}%`,
                border: "2px solid #f2a93b",
                background: "#f2a93b33",
                pointerEvents: "none",
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
