"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ChartAssemblyInput } from "flint-chart";
import { validateChart } from "flint-chart/validate";
import { assembleECharts } from "flint-chart/echarts";
import * as echarts from "echarts";

type Config = {
  type: string;
  x: string;
  y: string | null;
  group: string | null;
  title: string;
  unit: string;
};
const names: Record<string, string> = {
  line: "Line Chart",
  bar: "Bar Chart",
  scatter: "Scatter Plot",
  radar: "Radar Chart",
};

export default function FlintChartPreview({
  rows,
  config,
}: {
  rows: Record<string, unknown>[];
  config: Config;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [renderError, setRenderError] = useState("");
  const spec = useMemo<ChartAssemblyInput>(
    () => ({
      data: { values: rows },
      chart_spec: {
        chartType: names[config.type] || "Line Chart",
        title: config.title,
        encodings: {
          x: { field: config.x },
          y: { field: config.y || "" },
          ...(config.group ? { color: { field: config.group } } : {}),
        },
        baseSize: { width: 760, height: 420 },
      },
    }),
    [rows, config],
  );
  const checked = useMemo(
    () => (config.type === "table" ? null : validateChart(spec, "echarts")),
    [spec, config.type],
  );
  const validationError = checked?.valid
    ? ""
    : checked?.errors.map((x) => x.message).join("; ") || "";
  useEffect(() => {
    if (!host.current || validationError || config.type === "table") return;
    try {
      const instance = echarts.init(host.current, undefined, {
        renderer: "svg",
      });
      instance.setOption({
        ...assembleECharts(spec),
        title: { text: config.title, left: "center" },
      });
      const observer = new ResizeObserver(() => instance.resize());
      observer.observe(host.current);
      return () => {
        observer.disconnect();
        instance.dispose();
      };
    } catch (cause) {
      const message = String(cause);
      queueMicrotask(() => setRenderError(message));
    }
  }, [spec, config.title, config.type, validationError]);
  async function exportImage(kind: "svg" | "png") {
    const svg = host.current?.querySelector("svg");
    if (!svg) return;
    const source = new XMLSerializer().serializeToString(svg);
    const svgBlob = new Blob([source], { type: "image/svg+xml;charset=utf-8" });
    let blob: Blob = svgBlob;
    if (kind === "png") {
      const url = URL.createObjectURL(svgBlob);
      try {
        const image = new Image();
        image.src = url;
        await image.decode();
        const canvas = document.createElement("canvas");
        canvas.width = image.naturalWidth || 760;
        canvas.height = image.naturalHeight || 420;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("Canvas unavailable");
        context.fillStyle = "white";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0);
        blob = await new Promise<Blob>((resolve, reject) =>
          canvas.toBlob((value) =>
            value ? resolve(value) : reject(new Error("PNG export failed")),
          ),
        );
      } finally {
        URL.revokeObjectURL(url);
      }
    }
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `figure.${kind}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  if (config.type === "table") return null;
  return (
    <div className="stack">
      {(validationError || renderError) && (
        <p className="error">{validationError || renderError}</p>
      )}
      <div
        ref={host}
        style={{ width: "100%", height: 440, background: "white" }}
        role="img"
        aria-label={config.title}
      />
      <div className="flex">
        <button
          className="button secondary"
          disabled={!!validationError}
          onClick={() => exportImage("svg")}
        >
          导出 SVG
        </button>
        <button
          className="button secondary"
          disabled={!!validationError}
          onClick={() => exportImage("png")}
        >
          导出 PNG
        </button>
      </div>
    </div>
  );
}
