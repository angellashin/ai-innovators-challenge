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
  const [projectId, setProjectId] = useState("");

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
      const demo = await post<{ project_id: string }>("/api/demo/hero-project");
      setProjectId(demo.project_id);
      sessionStorage.setItem("replan.projectId", demo.project_id);
      router.push(`/workspaces?highlight=${demo.project_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "데모를 준비하지 못했습니다.");
      setBusy(false);
    }
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
        <h1>하나의 프로젝트 안에서,<br />숨은 리스크까지 검토하세요.</h1>
        <p>배터리 공장 건설 프로젝트를 예시로, 기준 일정 연결부터 외부 리스크 검토와 대응안 승인까지의 흐름을 살펴봅니다.</p>
        <div className="demo-comparison" aria-label="대표 사례의 분석 결과">
          <div><span>기준 일정</span><strong>1개</strong><small>공장 건설 전체 작업을 연결</small></div>
          <div><span>리스크 검토</span><strong>3종</strong><small>협력사·외부 출처·일정 영향</small></div>
        </div>
        <p className="demo-entry-note">대표 사례의 분석 흐름을 확인하는 화면입니다. 담당자 검토와 승인 전에는 일정이 바뀌지 않습니다.</p>
        <div className="entry-form">
          <button type="button" onClick={startExample} disabled={busy}>{busy ? "대표 사례를 준비하는 중…" : "대표 사례 데모 보기"}<span aria-hidden="true">→</span></button>
          <Link className="entry-secondary-button" href="/workspaces/new">새 프로젝트 시작<span aria-hidden="true">→</span></Link>
        </div>
        {error && <div className="entry-error" role="alert">데모를 준비하지 못했습니다: {error}{projectId && <> · <Link href={`/projects/${projectId}#overview`}>대표 사례 열기</Link></>}</div>}
      </section>
    </main>
  );
}
