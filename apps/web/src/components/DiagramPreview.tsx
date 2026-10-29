"use client";

import { useEffect, useState } from "react";

export default function DiagramPreview({ script }: { script: string }) {
  const [preview, setPreview] = useState({ script: "", image: "", error: "" });
  const image = preview.script === script ? preview.image : "";
  const error = preview.script === script ? preview.error : "";

  useEffect(() => {
    let cancelled = false;
    let url = "";
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: "neutral",
        });
        await mermaid.parse(script);
        const result = await mermaid.render(
          `research-diagram-${Date.now()}`,
          script,
        );
        if (cancelled) return;
        url = URL.createObjectURL(
          new Blob([result.svg], { type: "image/svg+xml" }),
        );
        setPreview({ script, image: url, error: "" });
      } catch (cause) {
        if (!cancelled)
          setPreview({
            script,
            image: "",
            error: `图形语法错误：${String(cause)}`,
          });
      }
    })();
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [script]);

  return (
    <>
      {error && <p className="error">{error}</p>}
      {image && (
        // Mermaid produces a local Blob URL, which does not use image optimization.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={image}
          alt="Mermaid 架构图预览"
          style={{ maxWidth: "100%" }}
        />
      )}
    </>
  );
}
