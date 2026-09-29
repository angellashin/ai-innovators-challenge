"use client";

import { FormEvent, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";

type AuthResponse = { token?: string; user?: { username?: string } };

export default function AuthPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/proxy/api/auth/${mode === "login" ? "login" : "register"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const value = await response.json().catch(() => ({})) as AuthResponse & { detail?: string };
      if (!response.ok || !value.token) throw new Error(value.detail || "인증을 완료하지 못했습니다.");
      sessionStorage.setItem("replan.authToken", value.token);
      const next = new URLSearchParams(window.location.search).get("next") || "/workspaces";
      router.push(next.startsWith("/") ? next : "/workspaces");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "인증을 완료하지 못했습니다.");
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <header className="auth-nav">
        <Link className="brand-lockup" href="/" aria-label="REPLAN 홈"><Image src="/brand/replan-wordmark.png" alt="REPLAN" width={1500} height={350} priority /></Link>
        <Link href="/demo">대표 사례 데모</Link>
      </header>
      <section className="auth-card">
        <p className="eyebrow"><span className="flow-indicator" aria-hidden="true" /> PROJECT WORKSPACE</p>
        <h1>{mode === "login" ? "프로젝트 작업공간에 들어가기" : "새 작업공간 만들기"}</h1>
        <p>대표 사례는 로그인 없이 볼 수 있습니다. 직접 프로젝트를 만들고 관리하려면 계정이 필요합니다.</p>
        <div className="auth-tabs" role="tablist" aria-label="계정 메뉴">
          <button type="button" className={mode === "login" ? "is-active" : ""} onClick={() => { setMode("login"); setError(""); }}>로그인</button>
          <button type="button" className={mode === "register" ? "is-active" : ""} onClick={() => { setMode("register"); setError(""); }}>계정 만들기</button>
        </div>
        <form onSubmit={submit} className="auth-form">
          <label><span>아이디</span><input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="영문, 숫자, ., _, -" required /></label>
          <label><span>비밀번호</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} placeholder="8자 이상" minLength={8} required /></label>
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="auth-submit" type="submit" disabled={busy}>{busy ? "확인 중…" : mode === "login" ? "로그인" : "계정 만들고 시작"}<span aria-hidden="true">→</span></button>
        </form>
        <Link className="auth-demo-link" href="/demo">로그인 없이 대표 사례 보기 →</Link>
      </section>
    </main>
  );
}
