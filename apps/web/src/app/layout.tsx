import type { Metadata } from "next";
import { Suspense } from "react";
import AppShell from "@/components/AppShell";
import "katex/dist/katex.min.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "科研小助手",
  description: "可溯源的 AI 科研工作台",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <Suspense fallback={<main className="main">{children}</main>}>
          <AppShell>{children}</AppShell>
        </Suspense>
      </body>
    </html>
  );
}
