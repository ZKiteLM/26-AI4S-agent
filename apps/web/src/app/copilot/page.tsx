"use client";

import { useCallback, useEffect, useState } from "react";
import { BarChart3, GitBranch, Languages, PenLine, Save } from "lucide-react";
import { useSessionState } from "@/lib/useSessionState";
import { api, apiFile } from "@/lib/api";
import DiagramPreview from "@/components/DiagramPreview";
import FlintChartPreview from "@/components/FlintChartPreview";
import DraftWorkspace from "@/components/DraftWorkspace";

type Chart = {
  rows: Record<string, unknown>[];
  overview: {
    row_count: number;
    columns: {
      name: string;
      type: string;
      nulls: number;
      examples: string[];
    }[];
  };
  spec: {
    type: string;
    x: string;
    y: string | null;
    group: string | null;
    title: string;
    unit: string;
    transform: string;
  };
  caption: string;
  data_artifact_id: string;
};
type ChartCache = Omit<Chart, "rows"> & { appliedSpec: Chart["spec"] };
const tabs = [
  { id: "draft", label: "论文框架", subtitle: "文献支撑的写作", icon: PenLine },
  {
    id: "diagram",
    label: "架构图",
    subtitle: "方法结构可视化",
    icon: GitBranch,
  },
  {
    id: "chart",
    label: "数据图表",
    subtitle: "CSV 与学术图注",
    icon: BarChart3,
  },
  {
    id: "translate",
    label: "学术翻译",
    subtitle: "中英表达转换",
    icon: Languages,
  },
] as const;
type Tab = (typeof tabs)[number]["id"];
const chartTypes = [
  { id: "auto", name: "自动选择" },
  { id: "line", name: "折线图" },
  { id: "bar", name: "柱状图" },
  { id: "scatter", name: "散点图" },
  { id: "radar", name: "二维雷达图" },
  { id: "table", name: "表格" },
];
function downloadText(name: string, value: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([value], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function tableText(
  rows: Record<string, unknown>[],
  style: "markdown" | "latex",
) {
  const names = Object.keys(rows[0] || {});
  const safe = (value: unknown) =>
    String(value ?? "")
      .replaceAll("|", "\\|")
      .replaceAll("\n", " ");
  if (style === "markdown")
    return `| ${names.join(" | ")} |\n| ${names.map(() => "---").join(" | ")} |\n${rows.map((r) => `| ${names.map((n) => safe(r[n])).join(" | ")} |`).join("\n")}`;
  const latex = (value: unknown) =>
    String(value ?? "")
      .replaceAll("\\", "__BACKSLASH__")
      .replace(/[&%$#_{}]/g, (c) => `\\${c}`)
      .replaceAll("__BACKSLASH__", "\\textbackslash{}");
  return `\\begin{tabular}{${"l".repeat(names.length)}}\n${names.map(latex).join(" & ")} \\\\\n\\hline\n${rows.map((r) => `${names.map((n) => latex(r[n])).join(" & ")} \\\\`).join("\n")}\n\\end{tabular}`;
}

export default function Copilot() {
  const [tab, setTab] = useSessionState<Tab>("active-tab", "draft");
  const [description, setDescription] = useSessionState(
    "diagram-description",
    "",
  );
  const [diagram, setDiagram] = useSessionState("diagram-source", "");
  const [diagramError, setDiagramError] = useState("");
  const [repairCount, setRepairCount] = useSessionState("diagram-repairs", 0);
  const [csv, setCsv] = useState<File | null>(null);
  const [csvFileName, setCsvFileName] = useSessionState("chart-filename", "");
  const [requestedType, setRequestedType] = useSessionState(
    "chart-requested-type",
    "auto",
  );
  const [chart, setChart] = useState<Chart | null>(null);
  const [chartCache, setChartCache] = useSessionState<ChartCache | null>(
    "chart-result",
    null,
  );
  const [caption, setCaption] = useSessionState("chart-caption", "");
  const [translationInput, setTranslationInput] = useSessionState(
    "translation-input",
    "",
  );
  const [translation, setTranslation] = useSessionState(
    "translation-output",
    "",
  );
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const onDiagramError = useCallback(
    (value: string) => setDiagramError(value),
    [],
  );
  useEffect(() => {
    if (tab !== "chart" || !chartCache || chart) return;
    let cancelled = false;
    api<Pick<Chart, "rows" | "spec" | "caption">>("/copilot/charts/caption", {
      method: "POST",
      body: JSON.stringify({
        data_artifact_id: chartCache.data_artifact_id,
        spec: chartCache.appliedSpec || chartCache.spec,
      }),
    })
      .then((restored) => {
        if (!cancelled) setChart({ ...chartCache, rows: restored.rows });
      })
      .catch(() => {
        if (!cancelled) setError("上次图表暂时无法恢复，请重新选择 CSV 分析。");
      });
    return () => {
      cancelled = true;
    };
  }, [chart, chartCache, tab]);
  async function makeDiagram(repair = false) {
    setBusy("diagram");
    setError("");
    try {
      const x = await api<{ script: string }>("/copilot/diagrams", {
        method: "POST",
        body: JSON.stringify({
          description,
          previous: repair ? diagram : "",
          error: repair ? diagramError : "",
        }),
      });
      setDiagram(x.script);
      setDiagramError("");
      setRepairCount(repair ? repairCount + 1 : 0);
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
      const x = await api<Chart>(
        `/copilot/charts?chart_type=${requestedType}`,
        { method: "POST", body },
      );
      setChart(x);
      setChartCache({
        overview: x.overview,
        spec: x.spec,
        appliedSpec: x.spec,
        caption: x.caption,
        data_artifact_id: x.data_artifact_id,
      });
      setCaption(x.caption);
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
  async function downloadCsv() {
    if (!chart) return;
    const blob = await apiFile(`/artifacts/${chart.data_artifact_id}`);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = csv?.name || csvFileName || "data.csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function applyChartConfig() {
    if (!chart) return;
    setBusy("chart-config");
    setError("");
    try {
      const update = await api<Pick<Chart, "rows" | "spec" | "caption">>(
        "/copilot/charts/caption",
        {
          method: "POST",
          body: JSON.stringify({
            data_artifact_id: chart.data_artifact_id,
            spec: chart.spec,
          }),
        },
      );
      const updated = { ...chart, ...update };
      setChart(updated);
      setChartCache({
        overview: updated.overview,
        spec: updated.spec,
        appliedSpec: updated.spec,
        caption: updated.caption,
        data_artifact_id: updated.data_artifact_id,
      });
      setCaption(update.caption);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy("");
    }
  }
  const setSpec = (change: Partial<Chart["spec"]>) => {
    setChart((old) =>
      old ? { ...old, spec: { ...old.spec, ...change } } : null,
    );
    setChartCache((old) =>
      old ? { ...old, spec: { ...old.spec, ...change } } : null,
    );
  };
  return (
    <div className="copilot-workspace">
      <div className="copilot-toolbar">
        <nav className="copilot-tabs" aria-label="写作与绘图功能">
          {tabs.map(({ id, label, subtitle, icon: Icon }) => (
            <button
              key={id}
              type="button"
              className={`copilot-tab ${tab === id ? "is-active" : ""}`}
              aria-pressed={tab === id}
              onClick={() => {
                setTab(id);
                setError("");
              }}
            >
              <span className="copilot-tab-icon">
                <Icon size={18} strokeWidth={1.9} />
              </span>
              <span>
                <strong>{label}</strong>
                <small>{subtitle}</small>
              </span>
            </button>
          ))}
        </nav>
        <div className="copilot-save-note">
          <Save size={14} /> 本次会话自动保存
        </div>
      </div>
      <div className="copilot-stage">
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div hidden={tab !== "draft"}>
          <DraftWorkspace />
        </div>
        {tab === "diagram" && (
          <section className="panel stack copilot-panel">
            <div className="copilot-panel-heading">
              <span className="copilot-heading-icon">
                <GitBranch size={20} />
              </span>
              <div>
                <h2>论文架构拓扑图</h2>
                <p>描述模块与数据流，编辑 Mermaid 代码并实时预览。</p>
              </div>
            </div>
            <textarea
              className="textarea"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="描述模块、数据流和模块之间的连接…"
              style={{ minHeight: 120 }}
            />
            <div className="flex">
              <button
                className="button"
                disabled={description.trim().length < 5 || !!busy}
                onClick={() => makeDiagram(false)}
              >
                {busy === "diagram" ? "生成中…" : "生成 Mermaid"}
              </button>
              {diagramError && repairCount < 2 && (
                <button
                  className="button secondary"
                  disabled={!!busy}
                  onClick={() => makeDiagram(true)}
                >
                  自动修复（剩余 {2 - repairCount} 次）
                </button>
              )}
            </div>
            {diagram && (
              <div
                className="two-col copilot-output-grid"
                style={{ alignItems: "start" }}
              >
                <div className="stack copilot-output-pane">
                  <h3>Mermaid 代码</h3>
                  <textarea
                    aria-label="Mermaid 代码"
                    className="textarea"
                    value={diagram}
                    onChange={(e) => {
                      setDiagram(e.target.value);
                      setRepairCount(0);
                    }}
                    style={{
                      minHeight: 430,
                      fontFamily: "ui-monospace, monospace",
                    }}
                  />
                </div>
                <div className="stack copilot-output-pane">
                  <h3>实时预览</h3>
                  <DiagramPreview script={diagram} onError={onDiagramError} />
                </div>
              </div>
            )}
          </section>
        )}
        {tab === "chart" && (
          <section className="panel stack copilot-panel">
            <div className="copilot-panel-heading">
              <span className="copilot-heading-icon">
                <BarChart3 size={20} />
              </span>
              <div>
                <h2>实验数据可视化</h2>
                <p>上传 CSV，检查字段，并用实际统计结果生成图表与图注。</p>
              </div>
            </div>
            <div className="flex copilot-upload-row">
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => {
                  const file = e.target.files?.[0] || null;
                  setCsv(file);
                  if (file) setCsvFileName(file.name);
                }}
              />
              <select
                className="select"
                value={requestedType}
                onChange={(e) => setRequestedType(e.target.value)}
              >
                {chartTypes.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              <button
                className="button"
                disabled={!csv || !!busy}
                onClick={makeChart}
              >
                {busy === "chart" ? "分析中…" : "分析并生成"}
              </button>
            </div>
            {chart && !csv && (
              <p className="copilot-inline-note">
                已恢复 {csvFileName || "上次数据"} 的图表。重新分析时请再次选择
                CSV。
              </p>
            )}
            {chart && (
              <>
                <div className="flex">
                  <strong>
                    {chart.overview.row_count} 行 ·{" "}
                    {chart.overview.columns.length} 列
                  </strong>
                  <button className="button secondary" onClick={downloadCsv}>
                    下载原始 CSV
                  </button>
                </div>
                <div
                  className="scroll"
                  style={{ display: "flex", gap: 8, overflowX: "auto" }}
                >
                  {chart.overview.columns.map((c) => (
                    <div
                      key={c.name}
                      className="copilot-data-column"
                      style={{ minWidth: 160, padding: 12 }}
                    >
                      <strong>{c.name}</strong>
                      <small style={{ display: "block" }}>
                        {c.type} · 缺失 {c.nulls}
                      </small>
                      <small>{c.examples.join(" / ")}</small>
                    </div>
                  ))}
                </div>
                <div className="flex">
                  <label>
                    图形{" "}
                    <select
                      className="select"
                      value={chart.spec.type}
                      onChange={(e) => setSpec({ type: e.target.value })}
                    >
                      {chartTypes
                        .filter((t) => t.id !== "auto")
                        .map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    X{" "}
                    <select
                      className="select"
                      value={chart.spec.x}
                      onChange={(e) => setSpec({ x: e.target.value })}
                    >
                      {chart.overview.columns.map((c) => (
                        <option key={c.name}>{c.name}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Y{" "}
                    <select
                      className="select"
                      value={chart.spec.y || ""}
                      onChange={(e) => setSpec({ y: e.target.value })}
                    >
                      {chart.overview.columns
                        .filter((c) => c.type === "numeric")
                        .map((c) => (
                          <option key={c.name}>{c.name}</option>
                        ))}
                    </select>
                  </label>
                  <label>
                    分组{" "}
                    <select
                      className="select"
                      value={chart.spec.group || ""}
                      onChange={(e) =>
                        setSpec({ group: e.target.value || null })
                      }
                    >
                      <option value="">无</option>
                      {chart.overview.columns.map((c) => (
                        <option key={c.name}>{c.name}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <input
                  className="input"
                  aria-label="图表标题"
                  value={chart.spec.title}
                  onChange={(e) => setSpec({ title: e.target.value })}
                />
                <button
                  className="button secondary"
                  disabled={!!busy}
                  onClick={applyChartConfig}
                >
                  应用配置并更新图注
                </button>
                {chart.spec.type === "table" ? (
                  <>
                    <div className="scroll">
                      <table>
                        <thead>
                          <tr>
                            {Object.keys(chart.rows[0] || {}).map((x) => (
                              <th key={x}>{x}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {chart.rows.map((r, i) => (
                            <tr key={i}>
                              {Object.values(r).map((v, j) => (
                                <td key={j}>{String(v ?? "")}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="flex">
                      <button
                        className="button secondary"
                        onClick={() =>
                          downloadText(
                            "table.md",
                            tableText(chart.rows, "markdown"),
                          )
                        }
                      >
                        导出 Markdown 表格
                      </button>
                      <button
                        className="button secondary"
                        onClick={() =>
                          downloadText(
                            "table.tex",
                            tableText(chart.rows, "latex"),
                          )
                        }
                      >
                        导出 LaTeX 表格
                      </button>
                    </div>
                  </>
                ) : (
                  <FlintChartPreview rows={chart.rows} config={chart.spec} />
                )}
                <label>
                  Figure Caption
                  <textarea
                    className="textarea"
                    value={caption}
                    onChange={(e) => setCaption(e.target.value)}
                    style={{ minHeight: 92 }}
                  />
                </label>
              </>
            )}
          </section>
        )}
        {tab === "translate" && (
          <section className="panel stack copilot-panel">
            <div className="copilot-panel-heading">
              <span className="copilot-heading-icon">
                <Languages size={20} />
              </span>
              <div>
                <h2>中文学术翻译</h2>
                <p>将摘要或正文转换成学术英文，保留公式与符号。</p>
              </div>
            </div>
            <div className="two-col copilot-translation-grid">
              <label className="stack">
                <strong>中文原文</strong>
                <textarea
                  aria-label="中文原文"
                  className="textarea"
                  value={translationInput}
                  onChange={(e) => setTranslationInput(e.target.value)}
                  placeholder="输入中文摘要或正文…"
                  style={{ minHeight: 340 }}
                />
              </label>
              <label className="stack">
                <strong>英文译文 · 可编辑</strong>
                <textarea
                  aria-label="可编辑英文译文"
                  className="textarea"
                  value={translation}
                  onChange={(e) => setTranslation(e.target.value)}
                  placeholder="英文译文可在这里编辑"
                  style={{ minHeight: 340 }}
                />
              </label>
            </div>
            <div className="flex">
              <button
                className="button"
                disabled={!translationInput.trim() || !!busy}
                onClick={translate}
              >
                {busy === "translate" ? "翻译中…" : "翻译为学术英文"}
              </button>
              {translation && (
                <button
                  className="button secondary"
                  onClick={() => navigator.clipboard.writeText(translation)}
                >
                  复制译文
                </button>
              )}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
