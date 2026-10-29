"use client";
import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";

type Graph = {
  nodes: { id: string; title: string; pagerank: number; in_degree: number }[];
  edges: { source: string; target: string }[];
};
export default function CitationGraph({ graph }: { graph: Graph }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({
      container: container.current,
      elements: [
        ...graph.nodes.map((n) => ({
          data: { id: n.id, label: n.title.slice(0, 24), rank: n.pagerank },
        })),
        ...graph.edges.map((e, i) => ({
          data: { id: `e${i}`, source: e.source, target: e.target },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#245fbd",
            label: "data(label)",
            color: "#17233c",
            "font-size": 11,
            "text-valign": "bottom",
            "text-margin-y": 8,
            width: "mapData(rank, 0, 1, 26, 65)",
            height: "mapData(rank, 0, 1, 26, 65)",
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.8,
            "line-color": "#a9bad0",
            "target-arrow-color": "#a9bad0",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
      ],
      layout: { name: "cose", animate: false },
    });
    return () => cy.destroy();
  }, [graph]);
  return <div ref={container} className="graph" aria-label="论文引用关系图" />;
}
