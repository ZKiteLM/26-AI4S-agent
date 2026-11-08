"use client";

import MathMarkdown from "@/components/MathMarkdown";

type FutureIdea = {
  title: string;
  status: string;
  rationale: string;
  method: string;
  evaluation: string;
  evidence: string;
  relatedTitle: string;
  related: string;
  raw: string;
};

function parseIdeas(content: string) {
  const headings = [...content.matchAll(/^###\s+(.+)$/gm)];
  if (!headings.length) return null;

  const intro = content.slice(0, headings[0].index).trim();
  const ideas: FutureIdea[] = headings.map((heading, index) => {
    const start = (heading.index ?? 0) + heading[0].length;
    const end = headings[index + 1]?.index ?? content.length;
    const raw = content.slice(start, end).trim();
    const statusMatch = raw.match(/^\*\*核查结果：\*\*(.+)$/m);
    const status = statusMatch?.[1].trim() ?? "";
    const body = statusMatch ? raw.replace(statusMatch[0], "").trim() : raw;
    const relatedMatch = body.match(
      /\*\*((?:可能)?高度相近的研究(?:（待核对）)?|检索到的论文（相关性未确认）|检索到的相关研究)：\*\*/,
    );
    const main = relatedMatch ? body.slice(0, relatedMatch.index).trim() : body;
    const related = relatedMatch
      ? body.slice((relatedMatch.index ?? 0) + relatedMatch[0].length).trim()
      : "";
    const methodAt = main.indexOf("**方法：**");
    const evaluationAt = main.indexOf("**验证：**");
    const evidenceAt = main.indexOf("**图谱依据：**");
    const structured =
      methodAt >= 0 && evaluationAt > methodAt && evidenceAt > evaluationAt;

    return {
      title: heading[1].trim(),
      status,
      rationale: structured ? main.slice(0, methodAt).trim() : "",
      method: structured
        ? main.slice(methodAt + "**方法：**".length, evaluationAt).trim()
        : "",
      evaluation: structured
        ? main.slice(evaluationAt + "**验证：**".length, evidenceAt).trim()
        : "",
      evidence: structured
        ? main.slice(evidenceAt + "**图谱依据：**".length).trim()
        : "",
      relatedTitle: relatedMatch?.[1] ?? "",
      related,
      raw: main,
    };
  });

  return { intro, ideas };
}

function statusTone(status: string) {
  if (status.includes("失败")) return "error";
  if (status.includes("未完成")) return "pending";
  if (status.includes("未确认")) return "neutral";
  if (status.includes("高度相近")) return "near";
  return "neutral";
}

export default function FutureIdeaCards({ content }: { content: string }) {
  const parsed = parseIdeas(content);
  if (!parsed) return <MathMarkdown>{content}</MathMarkdown>;

  return (
    <div className="future-ideas">
      {parsed.intro && (
        <div className="future-ideas-note">
          <MathMarkdown>{parsed.intro}</MathMarkdown>
        </div>
      )}
      <div className="future-ideas-list">
        {parsed.ideas.map((idea, index) => {
          const tone = statusTone(idea.status);
          const relatedCount = (idea.related.match(/^- /gm) ?? []).length;
          return (
            <article
              className="future-idea-card"
              key={`${idea.title}-${index}`}
            >
              <div className="future-idea-topline">
                <span className="future-idea-number">
                  研究方向 {String(index + 1).padStart(2, "0")}
                </span>
                {idea.status && (
                  <span className={`future-idea-badge ${tone}`}>
                    {tone === "near"
                      ? "相近研究待核对"
                      : tone === "error"
                        ? "检索失败"
                        : tone === "pending"
                          ? "核查未完成"
                          : "待进一步核查"}
                  </span>
                )}
              </div>
              <h4>{idea.title}</h4>
              {idea.status && (
                <p className="future-idea-status">{idea.status}</p>
              )}
              {idea.method ? (
                <>
                  {idea.rationale && (
                    <div className="future-idea-rationale">
                      <MathMarkdown>{idea.rationale}</MathMarkdown>
                    </div>
                  )}
                  <div className="future-idea-details">
                    <div className="future-idea-detail">
                      <span>研究方法</span>
                      <MathMarkdown>{idea.method}</MathMarkdown>
                    </div>
                    <div className="future-idea-detail">
                      <span>验证方式</span>
                      <MathMarkdown>{idea.evaluation}</MathMarkdown>
                    </div>
                  </div>
                  {idea.evidence && (
                    <div className="future-idea-evidence">
                      <span>图谱依据</span>
                      <MathMarkdown>{idea.evidence}</MathMarkdown>
                    </div>
                  )}
                </>
              ) : (
                <div className="future-idea-rationale">
                  <MathMarkdown>{idea.raw}</MathMarkdown>
                </div>
              )}
              {idea.related && (
                <details className="future-idea-related" open={tone === "near"}>
                  <summary>
                    <span>{idea.relatedTitle || "检索到的论文"}</span>
                    <span className="future-idea-related-count">
                      {relatedCount} 篇论文
                    </span>
                  </summary>
                  <MathMarkdown>{idea.related}</MathMarkdown>
                </details>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
