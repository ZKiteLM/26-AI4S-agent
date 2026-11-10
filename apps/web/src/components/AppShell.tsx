"use client";

import Link from "next/link";
import RequestProgress from "@/components/RequestProgress";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  BookOpenText,
  ChartNoAxesCombined,
  Compass,
  Files,
  FlaskConical,
  ListTodo,
  Menu,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  Settings2,
  Sparkles,
  X,
} from "lucide-react";

const groups = [
  {
    label: "研究空间",
    links: [
      { href: "/library", label: "文献库", icon: Files },
      { href: "/research", label: "科研问答", icon: FlaskConical },
      { href: "/discover", label: "发现论文", icon: Compass },
      { href: "/graph", label: "引用图谱", icon: Network },
    ],
  },
  {
    label: "创作",
    links: [
      { href: "/copilot", label: "写作与绘图", icon: ChartNoAxesCombined },
    ],
  },
  {
    label: "系统",
    links: [
      { href: "/tasks", label: "任务动态", icon: ListTodo },
      { href: "/settings", label: "模型接口", icon: Settings2 },
    ],
  },
] as const;

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() =>
      setCollapsed(
        window.localStorage.getItem("app-sidebar-collapsed") === "true",
      ),
    );
    return () => cancelAnimationFrame(frame);
  }, []);
  function toggleSidebar() {
    setCollapsed((previous) => {
      window.localStorage.setItem("app-sidebar-collapsed", String(!previous));
      return !previous;
    });
  }

  if (pathname === "/login")
    return <main className="auth-main">{children}</main>;

  const current = pathname.includes("/translate")
    ? "双语阅读"
    : pathname.startsWith("/papers/")
      ? "论文阅读"
      : groups
          .flatMap((group) =>
            group.links.map((link) => ({
              href: String(link.href),
              label: String(link.label),
            })),
          )
          .find((link) => link.href === pathname)?.label || "研究工作台";

  return (
    <div className="app-shell">
      {menuOpen && (
        <button
          className="sidebar-scrim"
          aria-label="关闭导航菜单"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        className={`sidebar ${menuOpen ? "sidebar-open" : ""} ${collapsed ? "sidebar-collapsed" : ""}`}
      >
        <div className="sidebar-head">
          <Link
            href="/library"
            className="brand"
            onClick={() => setMenuOpen(false)}
          >
            <span className="brand-mark">
              <BookOpenText size={21} strokeWidth={1.8} />
            </span>
            <span className="brand-copy">
              科研小助手
              <small>RESEARCH WORKSPACE</small>
            </span>
          </Link>
          <button
            className="icon-button sidebar-close"
            aria-label="关闭导航菜单"
            onClick={() => setMenuOpen(false)}
          >
            <X size={19} />
          </button>
          <button
            className="icon-button sidebar-toggle"
            aria-label={collapsed ? "展开导航栏" : "收起导航栏"}
            title={collapsed ? "展开导航栏" : "收起导航栏"}
            aria-expanded={!collapsed}
            onClick={toggleSidebar}
          >
            {collapsed ? (
              <PanelLeftOpen size={18} />
            ) : (
              <PanelLeftClose size={18} />
            )}
          </button>
        </div>

        <nav aria-label="主导航" className="sidebar-nav">
          {groups.map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-group-label">{group.label}</div>
              {group.links.map(({ href, label, icon: Icon }) => {
                const active =
                  pathname === href ||
                  (href === "/library" && pathname.startsWith("/papers/"));
                return (
                  <Link
                    key={href}
                    href={href}
                    className={`nav-link ${active ? "active" : ""}`}
                    aria-current={active ? "page" : undefined}
                    title={label}
                    onClick={() => setMenuOpen(false)}
                  >
                    <Icon size={18} strokeWidth={1.8} aria-hidden="true" />
                    <span>{label}</span>
                    {active && (
                      <span className="nav-active-dot" aria-hidden="true" />
                    )}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="sidebar-foot-icon">
            <Sparkles size={17} aria-hidden="true" />
          </div>
          <div>
            <strong>让每个结论都有出处</strong>
            <p>阅读、检索与写作，始终回到原文。</p>
          </div>
        </div>
      </aside>

      <div className="app-frame">
        <RequestProgress />
        <header className="topbar">
          <div className="topbar-location">
            <button
              className="icon-button mobile-menu"
              aria-label="打开导航菜单"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <Menu size={20} />
            </button>
            <span className="topbar-root">工作台</span>
            <span className="topbar-separator">/</span>
            <span className="topbar-current">{current}</span>
          </div>
          <div className="topbar-actions">
            <span className="topbar-status">
              <span className="status-dot" /> 证据优先
            </span>
            <Link
              href="/library"
              className="topbar-shortcut"
              aria-label="前往文献库"
            >
              <Files size={17} aria-hidden="true" /> 文献库
            </Link>
          </div>
        </header>
        <main
          className={`main ${pathname.startsWith("/papers/") ? "main-reader" : pathname === "/research" ? "main-research" : ""}`}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
