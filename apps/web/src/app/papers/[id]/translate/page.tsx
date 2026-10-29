"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, Paper } from "@/lib/api";
import PdfCanvas from "@/components/PdfCanvas";

type Block = {
  id: string;
  page_index: number;
  type: string;
  text: string;
  bbox: number[];
};
export default function Translate({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const [id, setId] = useState(""),
    [paper, setPaper] = useState<Paper | null>(null),
    [blocks, setBlocks] = useState<Block[]>([]),
    [page, setPage] = useState(1),
    [pageCount, setPageCount] = useState(1),
    [translations, setTranslations] = useState<Record<string, string>>({}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [focus, setFocus] = useState<string | null>(null);
  const translationItems = useRef<Record<string, HTMLDivElement | null>>({});
  useEffect(() => {
    params.then((x) => setId(x.id));
  }, [params]);
  const version = paper?.preferred_version_id;
  useEffect(() => {
    if (!id) return;
    api<{ paper: Paper; versions: { page_count: number }[] }>(`/papers/${id}`)
      .then((x) => {
        setPaper(x.paper);
        setPageCount(x.versions[0]?.page_count || 1);
      })
      .catch((e) => setError(String(e)));
  }, [id]);
  useEffect(() => {
    if (!version) return;
    api<{ blocks: Block[] }>(`/versions/${version}/blocks`)
      .then((x) => setBlocks(x.blocks))
      .catch((e) => setError(String(e)));
  }, [version]);
  async function translatePage() {
    if (!version) return;
    setBusy(true);
    setError("");
    try {
      const ids = blocks
        .filter((x) => x.page_index === page - 1)
        .map((x) => x.id);
      const x = await api<{
        translations: { block_id: string; text: string }[];
      }>("/translate", {
        method: "POST",
        body: JSON.stringify({
          version_id: version,
          block_ids: ids,
          target_lang: "zh",
        }),
      });
      setTranslations({
        ...translations,
        ...Object.fromEntries(x.translations.map((t) => [t.block_id, t.text])),
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const current = blocks.filter((x) => x.page_index === page - 1);
  function jumpToBlock(block: Block) {
    setFocus(block.id);
    translationItems.current[block.id]?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }
  function selectPdfLocation(x: number, y: number) {
    const containing = current.filter((block) => {
      const [x0, y0, x1, y1] = block.bbox;
      return x >= x0 && x <= x1 && y >= y0 && y <= y1;
    });
    const nearest =
      containing[0] ||
      current.reduce<Block | null>((best, block) => {
        if (!best) return block;
        const distance = (value: Block) => {
          const [x0, y0, x1, y1] = value.bbox;
          return Math.hypot(x - (x0 + x1) / 2, y - (y0 + y1) / 2);
        };
        return distance(block) < distance(best) ? block : best;
      }, null);
    if (nearest) jumpToBlock(nearest);
  }
  return (
    <>
      <Link href={`/papers/${id}`} className="muted">
        ← 返回论文
      </Link>
      <h1>双语对照阅读</h1>
      <p className="lead">{paper?.title}</p>
      {error && <p className="error">{error}</p>}
      <div className="panel row">
        <button
          className="button secondary"
          onClick={() => {
            setPage(Math.max(1, page - 1));
            setFocus(null);
          }}
        >
          上一页
        </button>
        <span>
          第 {page} / {pageCount} 页
        </span>
        <button
          className="button secondary"
          onClick={() => {
            setPage(Math.min(pageCount, page + 1));
            setFocus(null);
          }}
        >
          下一页
        </button>
        <button
          className="button"
          onClick={translatePage}
          disabled={busy || current.length === 0}
        >
          {busy ? "翻译中…" : "翻译当前页"}
        </button>
      </div>
      {version && (
        <div className="two-col">
          <PdfCanvas
            versionId={version}
            page={page}
            bbox={current.find((x) => x.id === focus)?.bbox}
            onLocationClick={selectPdfLocation}
          />
          <div className="panel scroll">
            <h2>中文译文</h2>
            {current.map((x) => (
              <div
                key={x.id}
                ref={(node) => {
                  translationItems.current[x.id] = node;
                }}
                className="list-item"
                style={{
                  marginBottom: 10,
                  borderColor: focus === x.id ? "#245fbd" : undefined,
                  cursor: "pointer",
                }}
                onClick={() => setFocus(x.id)}
              >
                <div className="section-label">{x.type}</div>
                <div>{translations[x.id] || "点击“翻译当前页”获取译文"}</div>
                <details>
                  <summary className="muted">查看原文</summary>
                  {x.text}
                </details>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
