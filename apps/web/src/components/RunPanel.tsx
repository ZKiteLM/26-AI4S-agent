"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Evidence } from "@/lib/api";

type Run = {
  status: string;
  answer: string;
  evidence: Evidence[];
  error?: string;
  events: { event: string; data: Record<string, unknown>; sequence: number }[];
};
export default function RunPanel({ runId }: { runId: string | null }) {
  const [run, setRun] = useState<Run | null>(null);
  useEffect(() => {
    if (!runId) return;
    let active = true;
    const poll = async () => {
      try {
        const data = await api<Run>(`/chat/runs/${runId}`);
        if (active) setRun(data);
        if (active && !["completed", "failed"].includes(data.status))
          setTimeout(poll, 1000);
      } catch {
        if (active) setTimeout(poll, 2000);
      }
    };
    poll();
    return () => {
      active = false;
    };
  }, [runId]);
  if (!runId) return null;
  const cited =
    run?.evidence?.filter((item) =>
      run.answer?.includes(`[${item.evidence_id}]`),
    ) || [];
  return (
    <section className="panel">
      <div className="section-label">AGENT RUN · {run?.status || "queued"}</div>
      <h2>执行过程与结果</h2>
      {run?.events?.length ? (
        <div className="list">
          {run.events
            .filter((x) => x.event !== "evidence")
            .map((x) => (
              <div className="list-item muted" key={x.sequence}>
                {x.event} · {JSON.stringify(x.data)}
              </div>
            ))}
        </div>
      ) : (
        <p className="muted">正在规划任务…</p>
      )}
      {run?.error && <p className="error">{run.error}</p>}
      {run?.answer && (
        <>
          <div className="divider" />
          <div className="answer">{run.answer}</div>
          <h3 style={{ marginTop: 18 }}>原文证据</h3>
          {(cited.length ? cited : run.evidence).map((x) => (
            <Link
              key={x.evidence_id}
              className="evidence"
              style={{ display: "block" }}
              href={`/papers/${x.paper_id}?page=${x.page_index + 1}&block=${x.block_id}`}
            >
              <strong>
                {x.paper_title} · 第 {x.page_index + 1} 页
              </strong>
              <br />
              {x.quote.slice(0, 320)}
            </Link>
          ))}
        </>
      )}
    </section>
  );
}
