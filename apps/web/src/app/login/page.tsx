"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { API } from "@/lib/api";

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
    <div style={{ maxWidth: 460, margin: "12vh auto" }} className="panel">
      <div className="section-label">WELCOME BACK</div>
      <h1>进入科研工作台</h1>
      <p className="lead">登录后管理论文、检索证据与生成科研素材。</p>
      <form className="stack" onSubmit={submit}>
        <label>
          邮箱
          <br />
          <input
            className="input"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </label>
        <label>
          密码
          <br />
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
  );
}
