"use client";

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { ReaderBlock } from "@/lib/reader";

function sourceFor(block: ReaderBlock): string {
  const content = block.markdown || block.text;
  if (block.type === "equation") {
    const formula = block.latex || content;
    return formula.includes("$$") ? formula : `$$\n${formula}\n$$`;
  }
  if (block.type === "code")
    return content.startsWith("```") ? content : `\`\`\`\n${content}\n\`\`\``;
  return content;
}

export default function TranslationViewer({
  block,
  translation,
  active,
  onSelect,
}: {
  block: ReaderBlock;
  translation?: string;
  active: boolean;
  onSelect: () => void;
}) {
  const content = translation ?? sourceFor(block);
  return (
    <div
      data-block-id={block.id}
      className={`translation-block ${active ? "active" : ""} ${translation === undefined ? "untranslated" : ""}`}
      onClick={onSelect}
    >
      <div className="translation-block-meta">
        <span>
          {block.type === "heading"
            ? "章节"
            : block.type === "equation"
              ? "公式"
              : block.type === "table"
                ? "表格"
                : "原文段落"}
        </span>
        {translation === undefined && <small>待翻译</small>}
      </div>
      <div className="translation-markdown">
        <Markdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
        >
          {content}
        </Markdown>
      </div>
    </div>
  );
}
