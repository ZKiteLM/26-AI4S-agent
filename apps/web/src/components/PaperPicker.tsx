"use client";
import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { api, Paper } from "@/lib/api";

export default function PaperPicker({
  selected,
  onChange,
  searchable = false,
  bulkSelect = false,
}: {
  selected: string[];
  onChange: (ids: string[]) => void;
  searchable?: boolean;
  bulkSelect?: boolean;
}) {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [query, setQuery] = useState("");
  useEffect(() => {
    api<{ items: Paper[] }>("/papers")
      .then((x) => setPapers(x.items))
      .catch(() => {});
  }, []);
  const filtered = papers.filter((paper) =>
    paper.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );
  const visibleIds = filtered.map((paper) => paper.id);
  const allVisibleSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selected.includes(id));
  return (
    <div className="panel">
      <div className="paper-picker-heading">
        <h3>选择文献</h3>
        {(searchable || bulkSelect) && (
          <div className="paper-picker-actions">
            <span>{selected.length} 篇已选</span>
            {bulkSelect && (
              <button
                type="button"
                className="paper-picker-select-all"
                disabled={!visibleIds.length}
                onClick={() =>
                  onChange(
                    allVisibleSelected
                      ? selected.filter((id) => !visibleIds.includes(id))
                      : [...new Set([...selected, ...visibleIds])],
                  )
                }
              >
                {allVisibleSelected
                  ? query.trim()
                    ? "取消选中搜索结果"
                    : "取消全选"
                  : query.trim()
                    ? "全选搜索结果"
                    : "全选"}
              </button>
            )}
          </div>
        )}
      </div>
      {searchable && (
        <label className="paper-picker-search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索论文标题"
            aria-label="搜索论文标题"
          />
        </label>
      )}
      <div className="list scroll" style={{ maxHeight: 230 }}>
        {filtered.map((p) => (
          <label key={p.id} className="row list-item">
            <input
              type="checkbox"
              checked={selected.includes(p.id)}
              onChange={(e) =>
                onChange(
                  e.target.checked
                    ? [...selected, p.id]
                    : selected.filter((x) => x !== p.id),
                )
              }
            />
            <span>{p.title}</span>
          </label>
        ))}
        {query && !filtered.length && (
          <p className="muted paper-picker-empty">没有匹配的论文</p>
        )}
      </div>
    </div>
  );
}
