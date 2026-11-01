"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { MessageSquarePlus, Search, Trash2, Send } from "lucide-react";
import { api, Evidence } from "@/lib/api";
import PaperPicker from "@/components/PaperPicker";
import MathMarkdown from "@/components/MathMarkdown";

type RunEvent = {
  event: string;
  data: Record<string, unknown>;
  sequence: number;
};
type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  run_id: string | null;
  status?: string;
  error?: string | null;
  evidence: (Evidence & { number: number })[];
  events: RunEvent[];
};
type Conversation = {
  id: string;
  title: string;
  paper_ids: string[];
  updated_at: string;
  messages?: Message[];
};

function referenceHref(source: {
  paper_id: string;
  page_index: number;
  block_id: string;
  paper_version_id?: string;
}) {
  const params = new URLSearchParams({
    page: String(source.page_index + 1),
    block: source.block_id,
  });
  if (source.paper_version_id) params.set("version", source.paper_version_id);
  return `/papers/${source.paper_id}?${params}`;
}

function Trace({
  events,
  expanded = true,
  paperId,
  onReferenceClick,
}: {
  events: RunEvent[];
  expanded?: boolean;
  paperId?: string;
  onReferenceClick?: (
    page: number,
    blockId: string,
    versionId?: string,
  ) => void;
}) {
  const calls = events.filter((item) => item.event === "tool_started");
  if (!calls.length) return null;
  type Preview = Pick<
    Evidence,
    | "evidence_id"
    | "paper_id"
    | "paper_title"
    | "page_index"
    | "block_id"
    | "quote"
  > & { paper_version_id?: string };
  function Sources({ sources }: { sources: Preview[] }) {
    return (
      <div className="chat-trace-sources">
        {!sources.length && (
          <p className="chat-trace-empty">
            本步没有新增原文，或该历史记录只保存了证据数量。
          </p>
        )}
        {sources.map((source) => (
          <Link
            key={source.evidence_id}
            href={referenceHref(source)}
            className="chat-trace-source"
            onNavigate={(event) => {
              if (paperId === source.paper_id && onReferenceClick) {
                event.preventDefault();
                onReferenceClick(
                  source.page_index + 1,
                  source.block_id,
                  source.paper_version_id,
                );
              }
            }}
          >
            <b>
              {source.paper_title} · 第 {source.page_index + 1} 页
            </b>
            <span>{source.quote}</span>
          </Link>
        ))}
      </div>
    );
  }
  return (
    <details className="chat-trace" open={expanded}>
      <summary>
        <Search size={14} /> 检索过程 · {calls.length} 步
      </summary>
      <ol>
        {calls.map((call) => {
          const round = Number(call.data.round);
          const result = events.find(
            (item) =>
              item.event === "tool_finished" && item.data.round === round,
          );
          const counts = (result?.data.results || []) as {
            query: string;
            new_sources: number;
            sources?: Preview[];
          }[];
          const isRead = call.data.tool === "read";
          const isPlan = call.data.tool === "plan";
          const label =
            call.data.tool === "search_bm25"
              ? "BM25 检索"
              : call.data.tool === "search_semantic"
                ? "语义检索"
                : isRead
                  ? "阅读原文"
                  : "规划检索";
          return (
            <li key={call.sequence}>
              <strong>第 {round} 步 · {label}</strong>
              {isPlan ? (
                result && (
                  <details className="chat-trace-evidence">
                    <summary>查看检索计划</summary>
                    <p className="chat-trace-plan">
                      {String(result.data.plan || result.data.error || "规划未返回内容")}
                    </p>
                  </details>
                )
              ) : isRead ? (
                <div className="chat-trace-read">
                  <code>
                    文档位置 {Number(call.data.start)} · 连读{" "}
                    {Number(call.data.length)} 块
                  </code>
                  {result && (
                    <details className="chat-trace-evidence">
                      <summary>
                        新增 {Number(result.data.new_sources || 0)} 条证据
                      </summary>
                      <Sources
                        sources={(result.data.sources || []) as Preview[]}
                      />
                    </details>
                  )}
                </div>
              ) : (
                <ul>
                  {((call.data.queries || []) as string[]).map((query, i) => {
                    const found = counts.find((x) => x.query === query);
                    return (
                      <li key={`${query}-${i}`}>
                        <code>{query}</code>
                        {result && (
                          <details className="chat-trace-evidence">
                            <summary>
                              新增 {found?.new_sources ?? 0} 条证据
                            </summary>
                            <Sources sources={found?.sources || []} />
                          </details>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
              {result && Boolean(result.data.error) && !isPlan && (
                <p className="chat-trace-empty">{String(result.data.error)}</p>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

export default function ChatWorkspace({
  paperId,
  onReferenceClick,
}: {
  paperId?: string;
  onReferenceClick?: (
    page: number,
    blockId: string,
    versionId?: string,
  ) => void;
}) {
  const fixedPapers = paperId ? [paperId] : null;
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [session, setSession] = useState<Conversation | null>(null);
  const [selected, setSelected] = useState<string[]>(fixedPapers || []);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const messagesRef = useRef<HTMLDivElement>(null);
  const activeRun = session?.messages?.findLast(
    (item) =>
      item.role === "user" && ["queued", "running"].includes(item.status || ""),
  );
  const storageKey = `research-chat-last-${paperId || "all"}`;

  const refreshList = useCallback(async () => {
    const result = await api<{ sessions: Conversation[] }>("/chat/sessions");
    const visible = paperId
      ? result.sessions.filter((item) => item.paper_ids.includes(paperId))
      : result.sessions;
    setSessions(visible);
    return visible;
  }, [paperId]);
  const open = useCallback(
    async (id: string) => {
      const result = await api<Conversation>(`/chat/sessions/${id}`);
      setSession(result);
      if (!paperId) setSelected(result.paper_ids);
      localStorage.setItem(storageKey, id);
    },
    [paperId, storageKey],
  );
  useEffect(() => {
    let mounted = true;
    api<{ sessions: Conversation[] }>("/chat/sessions")
      .then((result) => {
        if (!mounted) return;
        const items = paperId
          ? result.sessions.filter((item) => item.paper_ids.includes(paperId))
          : result.sessions;
        setSessions(items);
        const saved = localStorage.getItem(storageKey);
        const first = items.find((item) => item.id === saved) || items[0];
        if (first) void open(first.id);
      })
      .catch((cause) => setError(String(cause)));
    return () => {
      mounted = false;
    };
  }, [paperId, open, storageKey]);
  useEffect(() => {
    if (!activeRun || !session) return;
    const timer = setInterval(() => {
      void open(session.id)
        .then(refreshList)
        .catch((cause) => setError(String(cause)));
    }, 1300);
    return () => clearInterval(timer);
  }, [activeRun, session, open, refreshList]);
  useEffect(() => {
    const container = messagesRef.current;
    if (container)
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }, [session?.messages?.length]);

  async function create() {
    setError("");
    try {
      const created = await api<Conversation>("/chat/sessions", {
        method: "POST",
        body: JSON.stringify({ paper_ids: fixedPapers || selected }),
      });
      await refreshList();
      await open(created.id);
      setDraft("");
    } catch (cause) {
      setError(String(cause));
    }
  }
  async function remove(id: string) {
    setError("");
    try {
      await api(`/chat/sessions/${id}`, { method: "DELETE" });
      const items = await refreshList();
      if (session?.id === id) {
        const next = items.find((item) => item.id !== id);
        if (next) await open(next.id);
        else {
          setSession(null);
          localStorage.removeItem(storageKey);
        }
      }
    } catch (cause) {
      setError(String(cause));
    }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim() || busy || activeRun) return;
    setBusy(true);
    setError("");
    try {
      const target =
        session ||
        (await api<Conversation>("/chat/sessions", {
          method: "POST",
          body: JSON.stringify({ paper_ids: fixedPapers || selected }),
        }));
      await api(`/chat/sessions/${target.id}/messages`, {
        method: "POST",
        body: JSON.stringify({
          content: draft.trim(),
          paper_ids: fixedPapers || selected,
        }),
      });
      setDraft("");
      await open(target.id);
      await refreshList();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`chat-workspace ${paperId ? "chat-compact" : ""}`}>
      <aside className="chat-sessions" aria-label="问答会话">
        <div className="chat-sessions-head">
          <strong>会话</strong>
          <button
            type="button"
            className="chat-icon-button"
            title="新建会话"
            aria-label="新建会话"
            onClick={create}
          >
            <MessageSquarePlus size={18} />
          </button>
        </div>
        <div className="chat-session-list">
          {sessions.map((item) => (
            <div
              className={`chat-session-item ${item.id === session?.id ? "active" : ""}`}
              key={item.id}
            >
              <button
                type="button"
                className="chat-session-select"
                onClick={() => void open(item.id)}
                aria-current={item.id === session?.id ? "page" : undefined}
              >
                <span>{item.title}</span>
                <small>
                  {new Date(item.updated_at).toLocaleDateString("zh-CN")}
                </small>
              </button>
              <button
                type="button"
                className="chat-icon-button chat-delete"
                title="删除会话"
                aria-label={`删除会话：${item.title}`}
                onClick={() => void remove(item.id)}
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
          {!sessions.length && (
            <p className="muted chat-empty-list">还没有会话</p>
          )}
        </div>
      </aside>
      <section className="chat-main" aria-label="问答内容">
        {!paperId && (
          <div className="chat-paper-picker">
            <PaperPicker
              selected={selected}
              onChange={setSelected}
              searchable
            />
          </div>
        )}
        <div className="chat-messages" aria-live="polite" ref={messagesRef}>
          {!session?.messages?.length && (
            <div className="chat-welcome">
              <Search size={24} />
              <h2>向文献提问</h2>
              <p>Agent 会检索原文，必要时继续搜索，并附上可定位的引用。</p>
            </div>
          )}
          {session?.messages?.map((message) => (
            <div className={`chat-turn ${message.role}`} key={message.id}>
              <div className="chat-role">
                {message.role === "user" ? "你" : "科研助手"}
              </div>
              {message.role === "assistant" && (
                <Trace
                  events={message.events}
                  expanded={!paperId}
                  paperId={paperId}
                  onReferenceClick={onReferenceClick}
                />
              )}
              <div className="chat-bubble">
                <MathMarkdown>{message.content}</MathMarkdown>
              </div>
              {message.role === "user" && message.status === "failed" && (
                <div className="chat-turn-error" role="alert">
                  本次回答失败：{message.error || "请稍后重试"}
                  <button
                    type="button"
                    onClick={() => setDraft(message.content)}
                  >
                    重新提问
                  </button>
                </div>
              )}
              {message.role === "assistant" && !!message.evidence?.length && (
                <div className="chat-references">
                  <strong>参考原文</strong>
                  {message.evidence.map((item) => (
                    <Link
                      key={item.evidence_id}
                      href={referenceHref(item)}
                      className="chat-reference"
                      onNavigate={(event) => {
                        if (paperId === item.paper_id && onReferenceClick) {
                          event.preventDefault();
                          onReferenceClick(
                            item.page_index + 1,
                            item.block_id,
                            item.paper_version_id,
                          );
                        }
                      }}
                    >
                      <span className="chat-ref-number">[{item.number}]</span>
                      <span>
                        <b>
                          {item.paper_title} · 第 {item.page_index + 1} 页
                        </b>
                        <small>{item.quote.slice(0, 180)}</small>
                      </span>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          ))}
          {activeRun && (
            <div className="chat-turn assistant">
              <div className="chat-role">科研助手 · 检索中</div>
              <Trace
                events={activeRun.events}
                paperId={paperId}
                onReferenceClick={onReferenceClick}
              />
              {activeRun.error && <p className="error">{activeRun.error}</p>}
            </div>
          )}
        </div>
        {error && (
          <p className="error chat-error" role="alert">
            {error}
          </p>
        )}
        <form className="chat-composer" onSubmit={send}>
          <textarea
            aria-label="发送消息"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="询问论文细节；Enter 发送，Shift+Enter 换行"
            rows={2}
          />
          <button
            className="button"
            type="submit"
            disabled={
              !draft.trim() ||
              !(fixedPapers || selected).length ||
              busy ||
              !!activeRun
            }
            aria-label="发送消息"
          >
            <Send size={17} /> 发送
          </button>
        </form>
      </section>
    </div>
  );
}
