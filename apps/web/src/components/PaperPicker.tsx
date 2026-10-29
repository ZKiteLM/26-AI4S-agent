"use client";
import { useEffect, useState } from "react";
import { api, Paper } from "@/lib/api";

export default function PaperPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [papers, setPapers] = useState<Paper[]>([]);
  useEffect(() => {
    api<{ items: Paper[] }>("/papers")
      .then((x) => setPapers(x.items))
      .catch(() => {});
  }, []);
  return (
    <div className="panel">
      <h3>选择文献</h3>
      <div className="list scroll" style={{ maxHeight: 230 }}>
        {papers.map((p) => (
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
      </div>
    </div>
  );
}
