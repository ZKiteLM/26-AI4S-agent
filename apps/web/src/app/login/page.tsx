"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { API } from "@/lib/api";
import { BookOpenText, FileSearch, Languages, Sparkles } from "lucide-react";

export default function Login() {
  const router = useRouter();
  const [email, setEmail] = useState("demo@example.com");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/v1/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) throw new Error("邮箱或密码不正确");
      const data = await response.json();
      localStorage.setItem("research-token", data.access_token);
      router.push("/library");
    } catch (cause) {
      setError(String(cause));
      setBusy(false);
    }
  }
  return (
    <div className="login-layout">
      <section className="login-showcase">
        <div className="brand">
          <span className="brand-mark">
            <BookOpenText size={21} />
          </span>
          <span className="brand-copy">
            科研小助手<small>RESEARCH WORKSPACE</small>
          </span>
        </div>
        <div className="section-label">YOUR RESEARCH, CONNECTED</div>
        <h1>把阅读、证据和灵感放在一起。</h1>
        <p className="lead">
          围绕真实文献建立研究工作流。从原文出发，获得可追溯的回答与创作素材。
        </p>
        <div className="login-feature-list">
          <div className="login-feature">
            <FileSearch size={19} /> 阅读与定位原文
          </div>
          <div className="login-feature">
            <Sparkles size={19} /> 有出处的科研问答
          </div>
          <div className="login-feature">
            <Languages size={19} /> 中英双语对照
          </div>
        </div>
      </section>
      <div className="login-form-wrap">
        <form className="stack login-form" onSubmit={submit}>
          <div className="section-label">WELCOME BACK</div>
          <h2>登录工作台</h2>
          <p className="muted">继续你的研究。</p>
          <label className="form-field">
            邮箱
            <input
              className="input"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label className="form-field">
            密码
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="button" disabled={busy}>
            {busy ? "登录中…" : "登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
