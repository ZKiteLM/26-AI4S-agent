"use client";

import { ReaderBlock } from "@/lib/reader";
import MathMarkdown from "@/components/MathMarkdown";

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
      {translation === undefined && (
        <small className="untranslated-label">待翻译</small>
      )}
      <div className="translation-markdown">
        <MathMarkdown>{content}</MathMarkdown>
      </div>
    </div>
  );
}
