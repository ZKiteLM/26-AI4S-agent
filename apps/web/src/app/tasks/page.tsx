"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
type Task = {
  id: string;
  type: string;
  status: string;
  progress: number;
  error: string | null;
  result: Record<string, unknown>;
};
export default function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]),
    [error, setError] = useState("");
  const load = () =>
    api<{ items: Task[] }>("/tasks")
      .then((x) => setTasks(x.items))
      .catch((e) => setError(String(e)));
  useEffect(() => {
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, []);
  async function retry(id: string) {
    try {
      await api(`/tasks/${id}/retry`, { method: "POST" });
      load();
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <>
      {error && <p className="error">{error}</p>}
      <div className="list">
        {tasks.map((t) => (
          <div className="card" key={t.id}>
            <div className="toolbar">
              <div>
                <div className="section-label">{t.type}</div>
                <h2>{t.status}</h2>
              </div>
              <span className="badge">{Math.round(t.progress * 100)}%</span>
            </div>
            <progress value={t.progress} max={1} style={{ width: "100%" }} />
            {t.error && <p className="error">{t.error}</p>}
            {t.status === "failed" && (
              <button className="button secondary" onClick={() => retry(t.id)}>
                重试
              </button>
            )}
            {t.status === "completed" && (
              <p className="muted">{JSON.stringify(t.result)}</p>
            )}
          </div>
        ))}
      </div>
      {!tasks.length && <div className="panel muted">暂无后台任务。</div>}
    </>
  );
}
