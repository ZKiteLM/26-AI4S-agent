"use client";

import { useEffect, useState } from "react";

export default function OperationProgress({
  title,
  detail,
  progress,
  startedAt,
}: {
  title: string;
  detail?: string;
  progress?: number | null;
  startedAt?: string | null;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const start = startedAt ? new Date(startedAt).getTime() : Date.now();
    const update = () =>
      setElapsed(Math.max(0, Math.floor((Date.now() - start) / 1000)));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  const percentage =
    progress == null
      ? null
      : Math.round(Math.min(1, Math.max(0, progress)) * 100);
  return (
    <div className="operation-progress" role="status" aria-live="polite">
      <div className="operation-progress-header">
        <strong>{title}</strong>
        <span>
          {percentage == null
            ? `已等待 ${elapsed} 秒`
            : `${percentage}% · 已用 ${elapsed} 秒`}
        </span>
      </div>
      <progress max={100} value={percentage ?? undefined} aria-label={title} />
      {detail && <p>{detail}</p>}
    </div>
  );
}
