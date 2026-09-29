"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ProjectRecords, deriveProgress, stageSummary } from "../stages";
import { authToken, withAuth } from "../auth-client";

type Dict = Record<string, unknown>;

function text(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function projectName(value: unknown) {
  const name = text(value, "이름 없는 프로젝트");
  return name === "52일 숨은 위험 데모" || name === "헝가리 배터리 공장 건설" ? "배터리 공장 건설" : name;
}

function friendlyError(value: string) {
  try {
    const parsed = JSON.parse(value) as { detail?: string };
    if (parsed.detail === "invalid bearer token") return "프로젝트 데이터 연결을 확인해주세요.";
    if (parsed.detail) return parsed.detail;
  } catch {
    // Keep a readable fallback for non-JSON API errors.
  }
  return value || "프로젝트를 불러오지 못했습니다.";
}

const TONE = ["mint", "mint", "amber", "coral", "coral", "mint"];

export default function WorkspacesPage() {
  const router = useRouter();
  const apiBase = "/api/proxy";
  const [projects, setProjects] = useState<Dict[]>([]);
  const [records, setRecords] = useState<Record<string, ProjectRecords>>({});
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [highlightedProjectId, setHighlightedProjectId] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [actionProjectId, setActionProjectId] = useState("");

  async function api<T>(path: string, init: RequestInit = {}) {
    const response = await fetch(`${apiBase}${path}`, withAuth(init));
    if (!response.ok) throw new Error(await response.text());
    return response.json() as Promise<T>;
  }

  async function loadProjects() {
    setBusy(true);
    try {
      const value = await api<{ projects: Dict[] }>(`/api/projects${includeArchived ? "?include_archived=true" : ""}`);
      setProjects(value.projects);
      setError("");
      // Status comes from each project's stored records, not from placeholders.
      const details = await Promise.all(value.projects.slice(0, 30).map((project) =>
        api<ProjectRecords>(`/api/projects/${text(project.id)}`).then((detail) => [text(project.id), detail] as const).catch(() => null)));
      setRecords(Object.fromEntries(details.filter((item): item is readonly [string, ProjectRecords] => Boolean(item))));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "프로젝트를 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!authToken()) {
      router.replace(`/auth?next=${encodeURIComponent("/workspaces")}`);
      return;
    }
    setHighlightedProjectId(new URLSearchParams(window.location.search).get("highlight") || "");
    void loadProjects();
  }, [includeArchived, router]);

  async function toggleArchive(project: Dict) {
    const id = text(project.id);
    const archived = Boolean(project.archived_at);
    setActionProjectId(id);
    setError("");
    try {
      await api(`/api/projects/${id}/archive`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: !archived }),
      });
      await loadProjects();
    } catch (caught) {
      setError(friendlyError(caught instanceof Error ? caught.message : ""));
    } finally {
      setActionProjectId("");
    }
  }

  function openProject(project: Dict, section = "overview") {
    const id = text(project.id);
    sessionStorage.setItem("replan.projectId", id);
    router.push(`/projects/${id}#${section}`);
  }

  const progressById = Object.fromEntries(Object.entries(records).map(([id, detail]) => [id, deriveProgress(detail)]));
  const known = Object.values(progressById);
  const waitingChange = known.filter((item) => item.current === 3 && item.focusEvent).length;
  const waitingApproval = known.filter((item) => item.current === 4 || item.current === 5).length;
  const committedCount = known.filter((item) => item.allDone).length;
  const queue = projects.filter((project) => progressById[text(project.id)] && !progressById[text(project.id)].allDone).slice(0, 5);

  return (
    <main className="directory-page workspace-directory">
      <header className="directory-nav">
        <Link className="brand-lockup" href="/" aria-label="REPLAN 홈"><Image src="/brand/replan-wordmark.png" alt="REPLAN" width={1500} height={350} priority /></Link>
        <div className="directory-nav-actions"><span className="demo-badge"><span className="flow-indicator" aria-hidden="true" /> 프로젝트 작업공간</span><Link className="directory-nav-link" href="/demo">대표 사례 데모</Link></div>
      </header>

      <section className="directory-head workspace-head">
        <div>
          <p className="eyebrow"><span className="flow-indicator" aria-hidden="true" /> PROJECT WORKSPACE</p>
          <h1>프로젝트</h1>
          <p>프로젝트마다 기준 일정, 외부 리스크, 대응 결정을 한 팀의 맥락으로 관리합니다.</p>
        </div>
        <div className="workspace-head-actions">
          <button className="secondary" onClick={loadProjects} disabled={busy}>새로고침</button>
          <Link className="workspace-primary-action" href="/workspaces/new">프로젝트 만들기 <span aria-hidden="true">→</span></Link>
        </div>
      </section>

      {error && <aside className="error directory-error workspace-error"><b>연결 확인</b><span>{friendlyError(error)}</span></aside>}

      <section className="workspace-stats" aria-label="워크스페이스 요약">
        <div className="workspace-stat"><span>진행 중</span><strong>{projects.length || "—"}</strong><small>현재 프로젝트</small></div>
        <div className="workspace-stat"><span>리스크 검토 대기</span><strong>{projects.length ? waitingChange : "—"}</strong><small>영향 확인이 필요한 리스크</small></div>
        <div className="workspace-stat"><span>비교·승인 대기</span><strong>{projects.length ? waitingApproval : "—"}</strong><small>분석 또는 승인 전</small></div>
        <div className="workspace-stat"><span>새 버전 확정</span><strong>{projects.length ? committedCount : "—"}</strong><small>최신 변경을 반영 완료</small></div>
      </section>

      <section className="workspace-content-grid">
        <div className="workspace-portfolio-panel">
          <div className="workspace-panel-header"><div><p className="eyebrow">PORTFOLIO</p><h2>프로젝트 포트폴리오</h2></div><label className="workspace-archive-toggle"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} /> 보관한 프로젝트 포함</label><span className="workspace-count">{projects.length}개 프로젝트</span></div>
          <div className="workspace-table" role="table" aria-label="프로젝트 포트폴리오">
            <div className="workspace-table-head" role="row"><span>프로젝트</span><span>기준 일정</span><span>목표일</span><span>다음 할 일</span><span>관리</span></div>
            {busy && <div className="workspace-empty" role="row"><div className="workspace-empty-mark">R</div><b>프로젝트 목록을 불러오는 중입니다.</b><span>연결 상태를 확인하고 있습니다.</span></div>}
            {!busy && !projects.length && <div className="workspace-empty" role="row"><div className="workspace-empty-mark">R</div><b>아직 프로젝트가 없습니다.</b><span>새 프로젝트를 만들고 기준 일정 Excel을 연결해보세요.</span><Link href="/workspaces/new">첫 프로젝트 시작 <span aria-hidden="true">→</span></Link></div>}
            {!busy && projects.map((project) => { const id = text(project.id); const detail = records[id]; const progress = progressById[id]; const isHighlighted = id === highlightedProjectId; const isDemo = id === "HERO-BAT-HU-001"; const archived = Boolean(project.archived_at); return <div className={`workspace-table-row${isHighlighted ? " is-highlighted" : ""}${archived ? " is-archived" : ""}`} role="row" key={id}><button className="workspace-row-open" onClick={() => openProject(project)}><span className="workspace-project-name"><b>{projectName(project.name)}{isDemo && <em>대표 사례</em>}{archived && <em className="archived-label">보관됨</em>}</b><small>{id}</small></span><span><i className="workspace-status-dot" />{!detail ? "확인 중" : detail.version ? (detail.version.status === "committed" ? "확정 버전" : "기준 버전") : "연결 전"}</span><span>{text(project.target_finish, "미설정")}</span><span className="workspace-impact-neutral">{progress ? progress.next.label : "-"}</span></button><button className="workspace-row-archive" disabled={isDemo || actionProjectId === id} onClick={() => void toggleArchive(project)}>{actionProjectId === id ? "처리 중" : archived ? "복원" : "보관"}</button></div>; })}
          </div>
        </div>

        <aside className="workspace-side-rail">
          <div className="workspace-decision-queue"><div className="workspace-panel-header"><div><p className="eyebrow">NEXT ACTIONS</p><h2>다음 할 일</h2></div><span className="workspace-queue-count">{queue.length}</span></div>{queue.length ? <div className="decision-list">{queue.map((project) => { const progress = progressById[text(project.id)]; return <button className="decision-item" key={text(project.id)} onClick={() => openProject(project, progress.next.section)}><span className={`decision-label ${TONE[progress.current] || "mint"}`}>{stageSummary(progress)}</span><b>{projectName(project.name)} · {progress.next.label}</b><p>{progress.next.detail}</p></button>; })}</div> : <div className="decision-empty"><span aria-hidden="true">✓</span><b>{projects.length ? "진행 중인 할 일이 없습니다." : "아직 프로젝트가 없습니다."}</b><p>{projects.length ? "새 리스크가 확인되면 여기에 다음 할 일이 표시됩니다." : "프로젝트를 만들고 기준 일정 Excel을 연결하세요."}</p></div>}</div>
          <div className="create-card workspace-create-panel" id="new-project">
            <p className="eyebrow">NEW PROJECT</p>
            <h2>새 프로젝트</h2>
            <p>프로젝트 이름만 정하고, 다음 단계에서 기준 일정을 연결합니다.</p>
            <Link className="workspace-primary-action" href="/workspaces/new">프로젝트 만들기 <span aria-hidden="true">→</span></Link>
          </div>
        </aside>
      </section>
    </main>
  );
}
