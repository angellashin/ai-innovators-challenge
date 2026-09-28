"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

const defaultApiBase = "/api/proxy";

export default function DemoEntryPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [createdProjectId, setCreatedProjectId] = useState("");

  async function post<T>(path: string, body?: Record<string, string>): Promise<T> {
    const response = await fetch(`${defaultApiBase}${path}`, {
      method: "POST",
      ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      const value = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(value.detail || `서버 응답 ${response.status}`);
    }
    return response.json() as Promise<T>;
  }

  async function startExample() {
    if (busy) return;
    setBusy(true);
    setError("");
    sessionStorage.setItem("replan.apiBase", defaultApiBase);
    try {
      const created = await post<{ project_id: string }>("/api/projects", { name: "52일 숨은 위험 데모", mode: "LIVE" });
      setCreatedProjectId(created.project_id);
      sessionStorage.setItem("replan.projectId", created.project_id);
      await post(`/api/projects/${created.project_id}/demo/hero-baseline`);
      await post(`/api/projects/${created.project_id}/watch/start`);
      router.push(`/projects/${created.project_id}#changes`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "데모를 준비하지 못했습니다.");
      setBusy(false);
    }
  }

  function continueDemo() {
    sessionStorage.setItem("replan.apiBase", defaultApiBase);
    router.push("/workspaces");
  }

  return (
    <main className="entry-page">
      <header className="entry-nav">
        <Link className="brand-lockup" href="/" aria-label="REPLAN 홈">
          <Image src="/brand/replan-wordmark.png" alt="REPLAN" width={1500} height={350} priority />
        </Link>
        <Link className="entry-back-link" href="/">← 소개로 돌아가기</Link>
      </header>
      <section className="entry-card demo-entry-card">
        <p className="eyebrow"><span className="flow-indicator" aria-hidden="true" /> REPLAN 대표 사례</p>
        <h1>메일에는 0일,<br />실제로는 52일 위험.</h1>
        <p>협력사 메일에 적힌 부품의 지연은 일정에 흡수됩니다. REPLAN은 같은 수출 허가가 필요한 다른 부품을 찾아 담당자가 확인할 위험을 보여줍니다.</p>
        <div className="demo-comparison" aria-label="대표 사례의 분석 결과">
          <div><span>메일 내용만 계산</span><strong>0일</strong><small>완료일 변화 없음</small></div>
          <div><span>숨은 품목 조사 후</span><strong>+52일</strong><small>협력사 확인이 필요한 최악 조건</small></div>
        </div>
        <p className="demo-entry-note">합성 프로젝트와 녹화된 AI 응답을 쓰는 시연입니다. 실제 메일은 연결되지 않으며, 담당자 확인 전에는 일정을 바꾸지 않습니다.</p>
        <div className="entry-form">
          <button type="button" onClick={startExample} disabled={busy}>{busy ? "대표 사례를 준비하는 중…" : "대표 사례 바로 시작"}<span aria-hidden="true">→</span></button>
          <button className="entry-secondary-button" type="button" onClick={continueDemo} disabled={busy}>기존 프로젝트 열기</button>
        </div>
        {error && <div className="entry-error" role="alert">데모를 준비하지 못했습니다: {error}{createdProjectId && <> · <Link href={`/projects/${createdProjectId}#schedule`}>만든 프로젝트 열기</Link></>}</div>}
        <small className="muted">새 프로젝트에는 예시 일정과 외부 공지가 자동으로 연결됩니다. 다음 화면에서 받은편지함의 대표 통보를 선택하세요.</small>
      </section>
    </main>
  );
}
