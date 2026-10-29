"use client";
import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api, Paper } from "@/lib/api";
import PdfCanvas from "@/components/PdfCanvas";
import RunPanel from "@/components/RunPanel";

type Version = {
  id: string;
  label: string;
  parse_status: string;
  page_count: number;
};
type Block = {
  id: string;
  page_index: number;
  type: string;
  text: string;
  bbox: number[];
};
export default function Reader({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [paper, setPaper] = useState<Paper | null>(null),
    [versions, setVersions] = useState<Version[]>([]),
    [selectedVersion, setSelectedVersion] = useState<string | null>(null),
    [blocks, setBlocks] = useState<Block[]>([]),
    [page, setPage] = useState(1),
    [focus, setFocus] = useState<string | null>(null),
    [question, setQuestion] = useState(""),
    [run, setRun] = useState<string | null>(null),
    [error, setError] = useState(""),
    [selectedText, setSelectedText] = useState(""),
    [translatedText, setTranslatedText] = useState(""),
    [returnPage, setReturnPage] = useState<number | null>(null),
    [showAssistant, setShowAssistant] = useState(false);
  useEffect(() => {
    const url = new URL(window.location.href);
    const frame = requestAnimationFrame(() => {
      setPage(Number(url.searchParams.get("page") || 1));
      setFocus(url.searchParams.get("block"));
    });
    return () => cancelAnimationFrame(frame);
  }, []);
  useEffect(() => {
    if (!id) return;
    api<{ paper: Paper; versions: Version[] }>(`/papers/${id}`)
      .then((x) => {
        setPaper(x.paper);
        setVersions(x.versions);
      })
      .catch((e) => setError(String(e)));
  }, [id]);
  const version =
    selectedVersion || paper?.preferred_version_id || versions[0]?.id;
  const activeVersion = versions.find((item) => item.id === version);
  useEffect(() => {
    if (!version) return;
    api<{ blocks: Block[] }>(`/versions/${version}/blocks`)
      .then((x) => setBlocks(x.blocks))
      .catch((e) => setError(String(e)));
  }, [version]);
  async function ask() {
    try {
      const x = await api<{ run_id: string }>("/chat/runs", {
        method: "POST",
        body: JSON.stringify({ question, paper_ids: [id] }),
      });
      setRun(x.run_id);
    } catch (e) {
      setError(String(e));
    }
  }
  async function translateSelection() {
    try {
      const x = await api<{ text: string }>("/translate", {
        method: "POST",
        body: JSON.stringify({ text: selectedText, target_lang: "zh" }),
      });
      setTranslatedText(x.text);
    } catch (e) {
      setError(String(e));
    }
  }
  function goPage(next: number) {
    setPage(next);
    setFocus(null);
    window.history.replaceState(null, "", `?page=${next}`);
  }
  function switchVersion(next: string) {
    setSelectedVersion(next);
    setPage(1);
    setFocus(null);
    setBlocks([]);
    setRun(null);
    window.history.replaceState(null, "", "?page=1");
  }
  function jump(block: Block) {
    setPage(block.page_index + 1);
    setFocus(block.id);
    window.history.replaceState(
      null,
      "",
      `?page=${block.page_index + 1}&block=${block.id}`,
    );
  }
  function jumpCitation(number: number) {
    const reference = references.find((x) =>
      new RegExp(`^\\s*\\[\\s*${number}\\s*\\]`).test(x.text),
    );
    if (reference) {
      setReturnPage(page);
      jump(reference);
    } else setError(`未能定位参考文献 [${number}]`);
  }
  const focused = blocks.find((b) => b.id === focus);
  const headings = blocks.filter(
    (b) => b.type === "heading" || b.type === "title",
  );
  const references = blocks.filter((b) => b.type === "reference");
  return (
    <>
      <div className="toolbar">
        <div>
          <Link href="/library" className="muted">
            ← 文献库
          </Link>
          <h1>{paper?.title || "论文阅读器"}</h1>
          <p className="lead">原始 PDF、结构化内容与可定位证据</p>
        </div>
        <div className="row">
          <button
            className="button secondary"
            onClick={() => setShowAssistant(!showAssistant)}
          >
            {showAssistant ? "收起侧栏" : "内容与问答"}
          </button>
          {id && (
            <Link className="button secondary" href={`/papers/${id}/translate`}>
              双语阅读 →
            </Link>
          )}
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {versions.length > 0 && (
        <div className="panel row">
          <span>版本</span>
          {versions.map((v) => (
            <button
              key={v.id}
              className={v.id === version ? "button secondary" : "button ghost"}
              aria-pressed={v.id === version}
              onClick={() => switchVersion(v.id)}
            >
              {v.label} · {v.parse_status}
              {v.id === paper?.preferred_version_id ? " · 默认" : ""}
            </button>
          ))}
        </div>
      )}
      {version && (
        <div className={showAssistant ? "three-col" : "reader-layout"}>
          <aside className="panel scroll">
            <h3>目录</h3>
            {headings.map((x) => (
              <button
                key={x.id}
                className="button ghost"
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  marginBottom: 5,
                }}
                onClick={() => jump(x)}
              >
                {x.text.slice(0, 85)}
              </button>
            ))}
          </aside>
          <div className="panel">
            <div className="toolbar">
              <button
                className="button secondary"
                onClick={() => goPage(Math.max(1, page - 1))}
              >
                上一页
              </button>
              <span>第 {page} 页</span>
              <button
                className="button secondary"
                onClick={() =>
                  goPage(Math.min(activeVersion?.page_count || 999, page + 1))
                }
              >
                下一页
              </button>
              {returnPage && (
                <button
                  className="button ghost"
                  onClick={() => {
                    goPage(returnPage);
                    setReturnPage(null);
                  }}
                >
                  返回引用处
                </button>
              )}
            </div>
            <PdfCanvas
              versionId={version}
              page={page}
              bbox={focused?.bbox}
              onSelectText={(text) => {
                setSelectedText(text);
                setShowAssistant(true);
              }}
              onCitationClick={jumpCitation}
            />
          </div>
          {showAssistant && (
            <aside className="stack">
              <div className="panel scroll">
                <h3>本页内容</h3>
                {blocks
                  .filter((x) => x.page_index === page - 1)
                  .map((x) => (
                    <div
                      key={x.id}
                      className="evidence"
                      onClick={() => jump(x)}
                    >
                      <span className="badge">{x.type}</span>{" "}
                      {x.text.slice(0, 320)}
                    </div>
                  ))}
              </div>
              {selectedText && (
                <div className="panel">
                  <h3>划词翻译</h3>
                  <p>{selectedText.slice(0, 300)}</p>
                  <button
                    className="button secondary"
                    onClick={translateSelection}
                  >
                    译为中文
                  </button>
                  {translatedText && <p>{translatedText}</p>}
                </div>
              )}
              <div className="panel">
                <h3>论文问答</h3>
                {version !== paper?.preferred_version_id && (
                  <p className="muted">问答检索默认版本的内容。</p>
                )}
                <textarea
                  className="textarea"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  placeholder="询问论文细节…"
                />
                <button
                  className="button"
                  disabled={!question.trim()}
                  onClick={ask}
                >
                  检索证据并回答
                </button>
              </div>
            </aside>
          )}
        </div>
      )}
      {references.length > 0 && (
        <div className="panel">
          <h2>参考文献</h2>
          <div className="list">
            {references.map((x) => (
              <button
                className="button ghost"
                key={x.id}
                style={{ textAlign: "left" }}
                onClick={() => jump(x)}
              >
                {x.text.slice(0, 240)}
              </button>
            ))}
          </div>
        </div>
      )}
      <RunPanel runId={run} />
    </>
  );
}
