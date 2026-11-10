"use client";

import { useEffect, useState } from "react";
import OperationProgress from "@/components/OperationProgress";

type Request = { id: string; path: string };
function label(path: string) {
  if (path.includes("/file")) return "正在读取论文 PDF";
  if (path.includes("model-config/test")) return "正在测试模型接口";
  if (path.includes("draft/analyze")) return "正在分析研究 IDEA";
  if (path.includes("draft/complete")) return "正在补全论文构想";
  if (path.includes("draft")) return "正在检索资料并生成论文框架";
  if (path.includes("diagrams")) return "正在生成论文架构图";
  if (path.includes("charts")) return "正在分析实验数据和图注";
  if (path.includes("translate")) return "正在翻译论文内容";
  if (path.includes("subscriptions/keywords")) return "正在生成检索关键词";
  if (path.includes("discovery/search")) return "正在检索学术论文";
  if (
    path.includes("discovery/import") ||
    (path.includes("recommendations/") && path.includes("import"))
  )
    return "正在导入论文";
  if (path.includes("graphs") && path.includes("insights"))
    return "正在启动图谱研究任务";
  if (path.includes("graphs")) return "正在准备引用图谱";
  if (path.includes("upload")) return "正在上传论文";
  return "正在处理请求";
}
export default function RequestProgress() {
  const [requests, setRequests] = useState<Request[]>([]);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const onRequest = (event: Event) => {
      const { phase, id, path } = (
        event as CustomEvent<{ phase: string; id: string; path: string }>
      ).detail;
      if (phase === "end") setVisible(false);
      setRequests((previous) =>
        phase === "start"
          ? [...previous, { id, path }]
          : previous.filter((item) => item.id !== id),
      );
    };
    window.addEventListener("research-request", onRequest);
    return () => window.removeEventListener("research-request", onRequest);
  }, []);
  useEffect(() => {
    if (!requests.length) return;
    const timer = window.setTimeout(() => setVisible(true), 700);
    return () => window.clearTimeout(timer);
  }, [requests.length]);
  if (!requests.length || !visible) return null;
  return (
    <div className="request-progress-banner">
      <OperationProgress
        title={
          requests.length > 1
            ? `正在同时处理 ${requests.length} 项请求`
            : label(requests[0].path)
        }
        detail="请求仍在进行，完成后页面会自动更新。"
      />
    </div>
  );
}
