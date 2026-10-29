"use client";
import { useState } from "react";
import { api, apiFile } from "@/lib/api";
import DiagramPreview from "@/components/DiagramPreview";

export default function Copilot() {
  const [idea, setIdea] = useState(""),
    [draft, setDraft] = useState(""),
    [description, setDescription] = useState(""),
    [diagram, setDiagram] = useState(""),
    [format, setFormat] = useState("mermaid"),
    [chartType, setChartType] = useState("line"),
    [chart, setChart] = useState<{
      artifact_id: string;
      data_artifact_id: string;
      caption: string;
      script: string;
      spec: Record<string, unknown>;
    } | null>(null),
    [image, setImage] = useState(""),
    [csv, setCsv] = useState<File | null>(null),
    [translationInput, setTranslationInput] = useState(""),
    [translation, setTranslation] = useState(""),
    [busy, setBusy] = useState(""),
    [error, setError] = useState("");
  async function makeDraft() {
    setBusy("draft");
    setError("");
    try {
      const x = await api<{ markdown: string }>("/copilot/draft", {
        method: "POST",
        body: JSON.stringify({ idea }),
      });
      setDraft(x.markdown);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
  async function makeDiagram() {
    setBusy("diagram");
    setError("");
    try {
      const x = await api<{ script: string }>("/copilot/diagrams", {
        method: "POST",
        body: JSON.stringify({ description, format }),
      });
      setDiagram(x.script);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
  async function makeChart() {
    if (!csv) return;
    setBusy("chart");
    setError("");
    try {
      const body = new FormData();
      body.append("file", csv);
      const x = await api<{
        artifact_id: string;
        data_artifact_id: string;
        caption: string;
        script: string;
        spec: Record<string, unknown>;
      }>(`/copilot/charts?chart_type=${chartType}`, { method: "POST", body });
      setChart(x);
      const blob = await apiFile(`/artifacts/${x.artifact_id}`);
      setImage(URL.createObjectURL(blob));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
  async function translate() {
    setBusy("translate");
    setError("");
    try {
      const x = await api<{ text: string }>("/translate", {
        method: "POST",
        body: JSON.stringify({ text: translationInput, target_lang: "en" }),
      });
      setTranslation(x.text);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
  return (
    <>
      <div className="section-label">ACADEMIC COPILOT</div>
      <h1>写作与数据可视化</h1>
      <p className="lead">生成可编辑草稿、可核查参考文献、图表与脚本。</p>
      {error && <p className="error">{error}</p>}
      <section className="panel stack">
        <h2>Idea → 论文框架</h2>
        <textarea
          className="textarea"
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          placeholder="描述研究问题、方法和预期贡献…"
        />
        <button
          className="button"
          disabled={idea.length < 10 || !!busy}
          onClick={makeDraft}
        >
          {busy === "draft" ? "核验文献中…" : "生成框架"}
        </button>
        {draft && (
          <textarea
            className="textarea"
            style={{ minHeight: 350 }}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="可编辑论文草稿"
          />
        )}
      </section>
      <div className="two-col">
        <section className="panel stack">
          <h2>CSV → 图表与图注</h2>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setCsv(e.target.files?.[0] || null)}
          />
          <select
            className="select"
            value={chartType}
            onChange={(e) => setChartType(e.target.value)}
          >
            <option value="line">折线图</option>
            <option value="bar">柱状图</option>
            <option value="scatter">散点图</option>
            <option value="radar">雷达图</option>
            <option value="3d">三维散点图</option>
            <option value="table">表格</option>
          </select>
          <button
            className="button"
            disabled={!csv || !!busy}
            onClick={makeChart}
          >
            生成图表
          </button>
          {image && (
            // The protected chart is loaded as a local Blob URL.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={image}
              alt="由上传 CSV 生成的科研图表"
              style={{ maxWidth: "100%" }}
            />
          )}
          {chart && (
            <>
              <p>{chart.caption}</p>
              <a
                className="button secondary"
                href="#"
                onClick={async (e) => {
                  e.preventDefault();
                  const blob = await apiFile(
                    `/artifacts/${chart.data_artifact_id}`,
                  );
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = "data.csv";
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                下载原始数据
              </a>
              <pre className="scroll">{chart.script}</pre>
            </>
          )}
        </section>
        <section className="panel stack">
          <h2>学术架构图脚本</h2>
          <textarea
            className="textarea"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="描述节点和连接关系…"
          />
          <select
            className="select"
            value={format}
            onChange={(e) => setFormat(e.target.value)}
          >
            <option value="mermaid">Mermaid</option>
            <option value="graphviz">Graphviz</option>
            <option value="tikz">TikZ</option>
            <option value="matplotlib">Matplotlib</option>
          </select>
          <button
            className="button"
            disabled={description.length < 5 || !!busy}
            onClick={makeDiagram}
          >
            生成脚本
          </button>
          {diagram && (
            <>
              <textarea
                className="textarea"
                value={diagram}
                onChange={(e) => setDiagram(e.target.value)}
                aria-label="可编辑架构图脚本"
                style={{ minHeight: 180, fontFamily: "monospace" }}
              />
              {format === "mermaid" && <DiagramPreview script={diagram} />}
            </>
          )}
        </section>
      </div>
      <section className="panel stack">
        <h2>中文学术文本 → 英文</h2>
        <textarea
          className="textarea"
          value={translationInput}
          onChange={(e) => setTranslationInput(e.target.value)}
          placeholder="粘贴摘要或论文正文…"
        />
        <button
          className="button"
          disabled={!translationInput.trim() || !!busy}
          onClick={translate}
        >
          翻译为学术英文
        </button>
        {translation && (
          <textarea
            className="textarea"
            value={translation}
            onChange={(e) => setTranslation(e.target.value)}
          />
        )}
      </section>
    </>
  );
}
