"use client";

import { useEffect, useRef } from "react";
import cytoscape, { Core } from "cytoscape";

export type FetchState = {
  status: string;
  has_more: boolean;
  fetched_count: number;
  total_count?: number | null;
  provider?: string;
  error?: string | null;
};
export type CitationNode = {
  id: string;
  title: string;
  authors: string[];
  year: number | null;
  abstract: string;
  doi: string | null;
  arxiv_id: string | null;
  openalex_id: string | null;
  citation_count: number | null;
  citation_source: string | null;
  pagerank: number;
  in_degree: number;
  out_degree: number;
  role: "foundation" | "derivative" | "connected" | "isolated" | null;
  in_library: boolean;
  paper_id: string | null;
  is_seed: boolean;
  references: FetchState;
  citations: FetchState;
};
export type CitationNetwork = {
  graph_id: string;
  mode: "internal" | "explore";
  status: string;
  version: number;
  error: string | null;
  truncated: boolean;
  nodes: CitationNode[];
  edges: { source: string; target: string; provenance: string[] }[];
  metrics: {
    verified_papers?: number | null;
    total_papers?: number | null;
    interpretation?: string | null;
  };
};

export default function CitationGraph({
  graph,
  selectedId,
  onSelect,
  resetLayout,
}: {
  graph: CitationNetwork;
  selectedId: string | null;
  onSelect: (id: string) => void;
  resetLayout: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<Core | null>(null);
  const callbacks = useRef({ onSelect });
  useEffect(() => {
    callbacks.current = { onSelect };
  }, [onSelect]);

  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({
      container: container.current,
      elements: [],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            label: "data(label)",
            color: "#17233c",
            "font-size": 11,
            "text-valign": "bottom",
            "text-margin-y": 7,
            width: "data(size)",
            height: "data(size)",
            "border-width": 2,
            "border-color": "data(borderColor)",
          },
        },
        {
          selector: "node:selected",
          style: { "border-color": "#17233c", "border-width": 3 },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#a9bad0",
            "target-arrow-color": "#a9bad0",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
      ],
      layout: { name: "preset" },
    });
    instance.current = cy;
    cy.on("tap", "node", (event) => {
      callbacks.current.onSelect(event.target.id());
    });
    return () => {
      cy.destroy();
      instance.current = null;
    };
  }, []);

  useEffect(() => {
    const cy = instance.current;
    if (!cy) return;
    const first = cy.nodes().length === 0;
    let added = 0;
    for (const node of graph.nodes) {
      const data = {
        id: node.id,
        label:
          node.title.length > 25 ? `${node.title.slice(0, 25)}…` : node.title,
        color: node.in_library ? "#245fbd" : "#e59a44",
        borderColor: node.role === "foundation" ? "#b7791f" : "#ffffff",
        size: Math.min(60, 25 + Math.log1p(node.citation_count ?? 0) * 4),
      };
      if (cy.getElementById(node.id).empty()) {
        const anchor = graph.edges.find(
          (edge) => edge.source === node.id || edge.target === node.id,
        );
        const parentId =
          anchor?.source === node.id ? anchor.target : anchor?.source;
        const parent = parentId ? cy.getElementById(parentId) : null;
        const position =
          parent && !parent.empty() ? parent.position() : { x: 0, y: 0 };
        const angle = added++ * 2.399;
        cy.add({
          group: "nodes",
          data,
          position: {
            x: position.x + 100 * Math.cos(angle),
            y: position.y + 100 * Math.sin(angle),
          },
        });
      } else cy.getElementById(node.id).data(data);
    }
    for (const edge of graph.edges) {
      const id = `${edge.source}:${edge.target}`;
      if (cy.getElementById(id).empty())
        cy.add({
          group: "edges",
          data: { id, source: edge.source, target: edge.target },
        });
    }
    if (first && added)
      cy.layout({ name: "cose", animate: false, fit: true }).run();
  }, [graph]);

  useEffect(() => {
    const cy = instance.current;
    if (!cy) return;
    cy.nodes().unselect();
    if (selectedId) cy.getElementById(selectedId).select();
  }, [selectedId, graph]);

  useEffect(() => {
    if (resetLayout)
      instance.current
        ?.layout({ name: "cose", animate: false, fit: true })
        .run();
  }, [resetLayout]);

  return (
    <div
      ref={container}
      className="graph citation-network"
      role="img"
      aria-label={`引用图谱：${graph.nodes.length} 篇论文，${graph.edges.length} 条引用关系。引用者指向被引用者。点击节点查看详情。`}
    />
  );
}
