"use client";

import { useEffect, useState } from "react";

type Preview = { script: string; svg: string; error: string };
export default function DiagramPreview({
  script,
  onError,
}: {
  script: string;
  onError?: (error: string) => void;
}) {
  const [preview, setPreview] = useState<Preview>({
    script: "",
    svg: "",
    error: "",
  });
  const [exportError, setExportError] = useState("");
  const current = preview.script === script ? preview : null;
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "neutral",
          flowchart: { htmlLabels: false },
        });
        await mermaid.parse(script);
        const result = await mermaid.render(
          `research-diagram-${Date.now()}`,
          script,
        );
        if (!cancelled) setPreview({ script, svg: result.svg, error: "" });
      } catch (cause) {
        const error = String(cause);
        if (!cancelled) {
          setPreview({ script, svg: "", error });
          onError?.(error);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [script, onError]);
  async function download(kind: "svg" | "png") {
    if (!current?.svg) return;
    setExportError("");
    try {
      let blob: Blob = new Blob([current.svg], {
        type: "image/svg+xml;charset=utf-8",
      });
      if (kind === "png") {
        const image = new Image();
        // Use the same self-contained data URL as the visible preview.
        image.src = previewUrl;
        await image.decode();
        const canvas = document.createElement("canvas");
        const width = image.naturalWidth || 900;
        const height = image.naturalHeight || 600;
        const scale = Math.min(2, 4096 / Math.max(width, height));
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        const context = canvas.getContext("2d");
        if (!context) throw new Error("浏览器无法创建图片画布");
        context.fillStyle = "white";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        blob = await new Promise<Blob>((resolve, reject) =>
          canvas.toBlob(
            (x) =>
              x ? resolve(x) : reject(new Error("浏览器无法编码 PNG 图片")),
            "image/png",
          ),
        );
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `architecture.${kind}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) {
      setExportError(`导出失败：${String(cause)}`);
    }
  }
  const previewUrl = current?.svg
    ? `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(current.svg)))}`
    : "";
  return (
    <div className="stack">
      {current?.error && <p className="error">图形语法错误：{current.error}</p>}
      {exportError && (
        <p className="error" role="alert">
          {exportError}
        </p>
      )}
      {current?.svg && (
        <>
          <div
            style={{
              overflow: "auto",
              background: "white",
              borderRadius: 12,
              padding: 18,
            }}
          >
            {/* Mermaid SVG is displayed as an image, outside the document DOM. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={previewUrl}
              alt="Mermaid 架构图预览"
              style={{ maxWidth: "100%" }}
            />
          </div>
          <div className="flex">
            <button
              className="button secondary"
              onClick={() => download("svg")}
            >
              导出 SVG
            </button>
            <button
              className="button secondary"
              onClick={() => download("png")}
            >
              导出 PNG
            </button>
          </div>
        </>
      )}
    </div>
  );
}
