"use client";

import { useEffect, useRef, useState } from "react";
import { PanelLeftOpen, RotateCcw } from "lucide-react";

type Props = {
  outline: React.ReactNode;
  center: React.ReactNode;
  right?: React.ReactNode;
  outlineOpen: boolean;
  onOutlineOpen: () => void;
};

const DEFAULT_OUTLINE = 220;
const DEFAULT_RIGHT = 390;

export default function ReaderColumns({
  outline,
  center,
  right,
  outlineOpen,
  onOutlineOpen,
}: Props) {
  const root = useRef<HTMLDivElement>(null);
  const [outlineWidth, setOutlineWidth] = useState(DEFAULT_OUTLINE);
  const [rightWidth, setRightWidth] = useState(DEFAULT_RIGHT);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const saved = window.localStorage.getItem("reader-panel-widths");
      if (!saved) return;
      try {
        const widths = JSON.parse(saved);
        const total = root.current?.clientWidth || 1200;
        const outline = Number.isFinite(widths.outline)
          ? Math.max(160, Math.min(400, widths.outline))
          : DEFAULT_OUTLINE;
        const right = Number.isFinite(widths.right)
          ? Math.max(260, Math.min(750, widths.right))
          : DEFAULT_RIGHT;
        setOutlineWidth(Math.min(outline, Math.max(160, total - right - 376)));
        setRightWidth(Math.min(right, Math.max(260, total - outline - 376)));
      } catch {
        window.localStorage.removeItem("reader-panel-widths");
      }
    });
    return () => cancelAnimationFrame(frame);
  }, []);

  function update(which: "outline" | "right", width: number) {
    const total = root.current?.clientWidth || 1200;
    const remaining =
      total -
      (which === "outline"
        ? right
          ? rightWidth
          : 0
        : outlineOpen
          ? outlineWidth
          : 0) -
      376;
    const max =
      which === "outline"
        ? Math.min(400, total * 0.35, remaining)
        : Math.min(750, total * 0.55, remaining);
    const next = Math.round(
      Math.max(which === "outline" ? 160 : 260, Math.min(max, width)),
    );
    const nextOutline = which === "outline" ? next : outlineWidth;
    const nextRight = which === "right" ? next : rightWidth;
    setOutlineWidth(nextOutline);
    setRightWidth(nextRight);
    window.localStorage.setItem(
      "reader-panel-widths",
      JSON.stringify({ outline: nextOutline, right: nextRight }),
    );
  }

  function startResize(
    which: "outline" | "right",
    event: React.PointerEvent<HTMLDivElement>,
  ) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = which === "outline" ? outlineWidth : rightWidth;
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    const move = (next: PointerEvent) =>
      update(
        which,
        startWidth + (next.clientX - startX) * (which === "outline" ? 1 : -1),
      );
    const stop = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", stop);
      handle.removeEventListener("pointercancel", stop);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
  }

  function reset() {
    setOutlineWidth(DEFAULT_OUTLINE);
    setRightWidth(DEFAULT_RIGHT);
    window.localStorage.removeItem("reader-panel-widths");
  }

  const hasRight = Boolean(right);
  const columns = [
    ...(outlineOpen ? [`${outlineWidth}px`, "8px"] : []),
    "minmax(0, 1fr)",
    ...(hasRight ? ["8px", `${rightWidth}px`] : []),
  ].join(" ");

  return (
    <div className="reader-columns-wrap">
      <div className="reader-columns-tools">
        {!outlineOpen && (
          <button className="button ghost" onClick={onOutlineOpen}>
            <PanelLeftOpen size={15} /> 显示文献大纲
          </button>
        )}
        <button
          className="button ghost"
          onClick={reset}
          title="恢复默认面板比例"
        >
          <RotateCcw size={14} /> 恢复默认比例
        </button>
      </div>
      <div
        className={`reader-columns ${hasRight ? "has-right" : ""} ${outlineOpen ? "has-outline" : ""}`}
        ref={root}
        style={{ gridTemplateColumns: columns }}
      >
        {outlineOpen && (
          <>
            <div className="reader-column">{outline}</div>
            <div
              className="reader-splitter"
              role="separator"
              aria-label="调整文献大纲宽度"
              aria-orientation="vertical"
              aria-valuemin={160}
              aria-valuemax={400}
              aria-valuenow={outlineWidth}
              tabIndex={0}
              onPointerDown={(event) => startResize("outline", event)}
              onKeyDown={(event) => {
                if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                  event.preventDefault();
                  update(
                    "outline",
                    outlineWidth + (event.key === "ArrowRight" ? 20 : -20),
                  );
                }
              }}
            />
          </>
        )}
        <div className="reader-column reader-center">{center}</div>
        {hasRight && (
          <>
            <div
              className="reader-splitter"
              role="separator"
              aria-label="调整右侧面板宽度"
              aria-orientation="vertical"
              aria-valuemin={260}
              aria-valuemax={750}
              aria-valuenow={rightWidth}
              tabIndex={0}
              onPointerDown={(event) => startResize("right", event)}
              onKeyDown={(event) => {
                if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                  event.preventDefault();
                  update(
                    "right",
                    rightWidth + (event.key === "ArrowLeft" ? 20 : -20),
                  );
                }
              }}
            />
            <div className="reader-column reader-right">{right}</div>
          </>
        )}
      </div>
    </div>
  );
}
