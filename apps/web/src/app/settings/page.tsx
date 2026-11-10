"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import OperationProgress from "@/components/OperationProgress";

type Provider = "openai" | "ohmygpt" | "ollama";
type ModelConfig = {
  source: "default" | "custom";
  provider: Provider;
  base_url: string;
  model: string;
  has_api_key: boolean;
};
const cloudExample = {
  url: "https://api.ohmygpt.com/v1",
  model: "deepseek/deepseek-v4.1-flash-1017",
};
const examples: Record<Provider, { url: string; model: string }> = {
  openai: cloudExample,
  ohmygpt: cloudExample,
  ollama: { url: "http://localhost:11434/v1", model: "qwen3.5:9b" },
};

export default function ModelSettings() {
  const [saved, setSaved] = useState<ModelConfig | null>(null);
  const [provider, setProvider] = useState<Provider>("openai");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [tested, setTested] = useState(false);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState<"test" | "save" | "reset" | "">("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function fill(config: ModelConfig) {
    setSaved(config);
    setProvider(config.provider);
    setBaseUrl(config.base_url);
    setModel(config.model);
    setApiKey("");
    setTested(false);
    setReply("");
  }
  useEffect(() => {
    api<ModelConfig>("/model-config")
      .then(fill)
      .catch((cause) => setError(String(cause)));
  }, []);
  function changed() {
    setTested(false);
    setReply("");
    setNotice("");
  }
  function chooseProvider(value: Provider) {
    setProvider(value);
    setBaseUrl(examples[value].url);
    setModel(examples[value].model);
    setApiKey("");
    changed();
  }
  const payload = {
    provider,
    base_url: baseUrl.trim(),
    model: model.trim(),
    api_key: apiKey.trim(),
  };
  async function test() {
    setBusy("test");
    setError("");
    setNotice("");
    try {
      const result = await api<{ reply: string }>("/model-config/test", {
        method: "POST",
        progress: "inline",
        body: JSON.stringify(payload),
      });
      setReply(result.reply);
      setTested(true);
    } catch (cause) {
      setTested(false);
      setError(String(cause));
    } finally {
      setBusy("");
    }
  }
  async function save() {
    if (!tested) return;
    setBusy("save");
    setError("");
    try {
      await api("/model-config", {
        method: "PUT",
        progress: "inline",
        body: JSON.stringify(payload),
      });
      fill(await api<ModelConfig>("/model-config"));
      setNotice(
        "模型接口已切换。之后开始的任务会使用新配置；运行中的任务继续使用启动时的配置。",
      );
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy("");
    }
  }
  async function reset() {
    setBusy("reset");
    setError("");
    try {
      await api("/model-config", { method: "DELETE" });
      fill(await api<ModelConfig>("/model-config"));
      setNotice("已恢复使用服务器默认模型配置。");
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="panel model-settings">
      <div className="task-activity-top">
        <div>
          <h2>模型接口</h2>
          <p className="muted">
            配置用于科研问答、综述、写作和翻译。Embedding 与 PDF
            解析服务仍独立配置。
          </p>
          <p className="muted">
            本机 Ollama 指运行 API
            服务的机器；部署到云端后，请使用服务器可访问的外部接口。
          </p>
        </div>
        <span className="badge">
          {saved?.source === "custom" ? "自定义模型" : "服务器默认模型"}
        </span>
      </div>
      <div className="model-settings-fields">
        <label>
          接口类型
          <select
            value={provider}
            onChange={(event) => chooseProvider(event.target.value as Provider)}
          >
            <option value="openai">OpenAI 兼容接口</option>
            <option value="ohmygpt">OpenAI Responses 接口</option>
            <option value="ollama">本机 Ollama</option>
          </select>
        </label>
        <label>
          Base URL
          <input
            value={baseUrl}
            onChange={(event) => {
              setBaseUrl(event.target.value);
              changed();
            }}
            placeholder={examples[provider].url}
            autoComplete="url"
          />
        </label>
        <label>
          模型名称
          <input
            value={model}
            onChange={(event) => {
              setModel(event.target.value);
              changed();
            }}
            placeholder={examples[provider].model || "输入模型 ID"}
          />
        </label>
        {provider !== "ollama" && (
          <label>
            API Key
            <input
              type="password"
              value={apiKey}
              onChange={(event) => {
                setApiKey(event.target.value);
                changed();
              }}
              placeholder={
                saved?.has_api_key &&
                saved.provider !== "ollama" &&
                saved.base_url === baseUrl.trim()
                  ? "同一接口可留空，沿用现有 Key"
                  : "输入当前接口的 API Key"
              }
              autoComplete="off"
            />
            <small>
              保存后不会再次显示密钥。切换到新接口时需要输入该接口的密钥。
            </small>
          </label>
        )}
      </div>
      {busy === "test" && (
        <OperationProgress
          title="正在测试模型接口"
          detail="向模型发送一条简短消息，等待真实回复。"
        />
      )}
      {busy === "save" && <OperationProgress title="正在保存模型配置" />}
      {reply && (
        <p className="model-test-reply">
          <strong>连接成功，模型回复：</strong>
          {reply}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {notice && <p className="muted">{notice}</p>}
      <div className="task-activity-actions">
        <button
          className="button secondary"
          type="button"
          onClick={test}
          disabled={!!busy || !baseUrl.trim() || !model.trim()}
        >
          测试连接
        </button>
        <button
          className="button"
          type="button"
          onClick={save}
          disabled={!!busy || !tested}
        >
          确认并切换
        </button>
        {saved?.source === "custom" && (
          <button
            className="button ghost"
            type="button"
            onClick={reset}
            disabled={!!busy}
          >
            恢复服务器默认
          </button>
        )}
      </div>
    </section>
  );
}
