import type { Metadata } from "next";
import Link from "next/link";
import {
  BookOpenText,
  ChartNoAxesCombined,
  Compass,
  Files,
  FlaskConical,
  Network,
  ListTodo,
} from "lucide-react";
import "./globals.css";

export const metadata: Metadata = {
  title: "科研小助手",
  description: "可溯源的 AI 科研工作台",
};

const links = [
  ["/library", "文献库", Files],
  ["/research", "科研问答", FlaskConical],
  ["/discover", "发现论文", Compass],
  ["/graph", "引用图谱", Network],
  ["/copilot", "写作与绘图", ChartNoAxesCombined],
  ["/tasks", "任务", ListTodo],
] as const;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <div className="app-shell">
          <aside className="sidebar">
            <Link href="/" className="brand">
              <BookOpenText size={26} />
              <span>
                科研小助手<small>AI RESEARCH AGENT</small>
              </span>
            </Link>
            <nav aria-label="主导航">
              {links.map(([href, label, Icon]) => (
                <Link key={href} href={href}>
                  <Icon size={18} />
                  {label}
                </Link>
              ))}
            </nav>
            <div className="sidebar-foot">
              以原文证据为依据
              <br />
              让研究过程可核查
            </div>
          </aside>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
