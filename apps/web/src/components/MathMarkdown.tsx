"use client";

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

export default function MathMarkdown({ children }: { children: string }) {
  const normalized = children
    .replace(
      /\\\[([\s\S]*?)\\\]/g,
      (_, formula: string) => `\n$$\n${formula}\n$$\n`,
    )
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, formula: string) => `$${formula}$`)
    .replace(/\$\$([\s\S]*?)\$\$|(?<!\\)\$([^$\n]+)\$/g, (match) =>
      match.replace(/\\\\(?=[A-Za-z])/g, "\\"),
    );
  return (
    <Markdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex]}
    >
      {normalized}
    </Markdown>
  );
}
