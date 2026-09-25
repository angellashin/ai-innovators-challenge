"use client";

import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
import Image from "next/image";
import { ExternalWatch, EvidenceReview } from "./external-watch";

type Dict = Record<string, unknown>;

type ApiError = {
  status: number;
  message: string;
};

type ProjectState = {
  project?: Dict;
  version?: Dict & { id?: string; content_hash?: string; data?: Dict };
  watch_plan?: Dict | null;
  events?: Array<Dict & { id?: string; data?: Dict }>;
  source_snapshots?: Array<Dict & { id?: string; source_id?: string; status?: string; fetched_at?: string; data?: Dict }>;
  runs?: Array<Dict & { id?: string; data?: Dict; status?: string; kind?: string }>;
  actions?: Array<Dict & { id?: string; scenario_id?: string; data?: Dict }>;
  documents?: Array<Dict & { id?: string; data?: Dict }>;
  notifications?: Array<Dict & { id?: string; data?: Dict }>;
  public_feeds?: Array<Dict & { id?: string; data?: Dict }>;
  mail_account?: Dict | null;
  decision_deadlines?: Array<Dict & { id?: string }>;
  site_prep_items?: Array<Dict & { id?: string; data?: Dict }>;
  supplier_calendars?: Array<Dict & { id?: string; data?: Dict }>;
  demo_events?: Dict[];
};

type ImportPreview = Dict & {
  import_id?: string;
  import_kind?: string;
  project?: Dict;
  tasks?: Dict[];
  options?: Dict[];
  events?: Dict[];
  calendars?: Dict[];
  mapping?: Dict;
  diff?: Dict & { summary?: Dict };
  warnings?: string[];
};

type RunResult = {
  run?: Dict & { id?: string; status?: string; data?: Dict };
  scenarios?: Array<Dict & { id?: string; data?: Dict }>;
};

const defaultApiBase = "/api/proxy";

function text(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function money(value: unknown) {
  const amount = Number(value || 0);
  return `${amount.toLocaleString("ko-KR")}원`;
}

function costLabel(data: Dict) {
  return data.extra_cost_krw === null || data.extra_cost_krw === undefined ? "비용 미정" : money(data.extra_cost_krw);
}

function shortId(value: unknown, fallback = "-") {
  const raw = text(value, fallback);
  return raw.length > 12 ? `${raw.slice(0, 12)}...` : raw;
}

function taskDates(task: Dict) {
  return {
    start: text(task.baseline_start ?? task.planned_start, ""),
    finish: text(task.baseline_finish ?? task.planned_finish, ""),
  };
}

function scenarioScore(data: Dict) {
  if (data.budget_status === "UNSET") return data.target_met ? "조건부" : "목표일 미달";
  if (data.target_met && data.budget_met && Array.isArray(data.violations) && data.violations.length === 0) {
    if (Array.isArray(data.required_confirmations) && data.required_confirmations.length > 0) return "조건부";
    return "실행 후보";
  }
  if (!data.target_met && data.budget_met) return "목표일 미달";
  if (data.target_met && !data.budget_met) return "예산 초과";
  return "제약 확인";
}

export default function Home({ initialProjectId = "" }: { initialProjectId?: string }) {
  const apiBase = defaultApiBase;
  const [projectId, setProjectId] = useState("");
  const [project, setProject] = useState<ProjectState>({});
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [documentStatus, setDocumentStatus] = useState<Dict | null>(null);
  const [mailForm, setMailForm] = useState({ provider: "imap", host: "", username: "", folder: "INBOX" });
  const [feedForm, setFeedForm] = useState({ label: "환경 정책 RSS", url: "https://environment.ec.europa.eu/news_en", kind: "rss" });
  const [supplierForm, setSupplierForm] = useState({ supplier_id: "", label: "", unavailable_dates: "", timezone: "Asia/Seoul" });
  const [channelForm, setChannelForm] = useState({ channel: "in_app", label: "REPLAN 인앱 알림", target: "" });
  const [projects, setProjects] = useState<Dict[]>([]);
  const [run, setRun] = useState<RunResult | null>(null);
  const [selectedScenarioId, setSelectedScenarioId] = useState("");
  const [selectedEventId, setSelectedEventId] = useState("");
  const [pendingRunId, setPendingRunId] = useState("");
  const [conditionNotes, setConditionNotes] = useState<Record<string, string>>({});
  const [manualMessage, setManualMessage] = useState("");
  const [budget, setBudget] = useState<number | "">("");
  const [notice, setNotice] = useState("프로젝트를 생성하거나 불러오세요.");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    setProjectId(initialProjectId || sessionStorage.getItem("replan.projectId") || "");
  }, [initialProjectId]);

  useEffect(() => {
    if (!initialProjectId) return;
    let active = true;
    setNotice("프로젝트 작업공간 불러오는 중...");
    callApi<ProjectState>(`/api/projects/${initialProjectId}`)
      .then((value) => {
        if (active) {
          setProject(value);
          setNotice("프로젝트 작업공간 준비됨");
        }
      })
      .catch((caught) => {
        if (!active) return;
        const apiError = caught as ApiError;
        setError(apiError.status ? apiError : { status: 0, message: String(caught) });
        setNotice("프로젝트 작업공간을 불러오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, [initialProjectId]);

  useEffect(() => {
    if (projectId) sessionStorage.setItem("replan.projectId", projectId);
  }, [projectId]);

  useEffect(() => {
    setRun(null); setSelectedScenarioId(""); setSelectedEventId(""); setPendingRunId("");
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    let inFlight = false;
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const value = await callApi<ProjectState>(`/api/projects/${projectId}`);
        if (!active) return;
        setProject(value);
        const latest = value.runs?.find((item) => item.kind === "analysis" &&
          (!selectedEventId || item.event_id === selectedEventId));
        const id = pendingRunId || latest?.id;
        if (id) {
          const result = await callApi<RunResult>(`/api/runs/${id}`);
          if (!active) return;
          setRun(result);
          setSelectedScenarioId((current) => result.scenarios?.some((item) => item.id === current) ? current : result.scenarios?.[0]?.id || "");
        }
      } catch (caught) {
        if (active) setError(caught as ApiError);
      } finally { inFlight = false; }
    };
    void poll();
    const timer = window.setInterval(poll, 4000);
    return () => { active = false; window.clearInterval(timer); };
  }, [projectId, selectedEventId, pendingRunId]);

  async function callApi<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    const response = await fetch(`${apiBase}${path}`, { ...init, headers });
    if (!response.ok) {
      let message = response.statusText;
      const rawBody = await response.text();
      try {
        const body = JSON.parse(rawBody) as Dict;
        message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
      } catch {
        message = rawBody || response.statusText;
      }
      throw { status: response.status, message } satisfies ApiError;
    }
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) return response.json() as Promise<T>;
    return response as T;
  }

  async function guarded<T>(label: string, action: () => Promise<T>, done?: (value: T) => void | Promise<unknown>) {
    setBusy(true);
    setError(null);
    setNotice(`${label} 처리 중...`);
    try {
      const value = await action();
      await done?.(value);
      setNotice(`${label} 완료`);
      return value;
    } catch (caught) {
      const apiError = caught as ApiError;
      setError(apiError.status ? apiError : { status: 0, message: String(caught) });
      setNotice(`${label} 실패`);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function refreshProject(id = projectId) {
    if (!id) return null;
    return guarded("프로젝트 새로고침", () => callApi<ProjectState>(`/api/projects/${id}`), (value) => setProject(value));
  }

  async function loadProjects() {
    await guarded("프로젝트 목록 조회", () => callApi<{ projects: Dict[] }>("/api/projects"), (value) => setProjects(value.projects));
  }

  async function uploadDocument() {
    if (!projectId || !documentFile) return;
    const body = new FormData();
    body.append("file", documentFile);
    const uploaded = await guarded("문서·메일 입력", () => callApi<Dict>(`/api/projects/${projectId}/documents`, { method: "POST", body }), (value) => {
      setDocumentStatus((value.document as Dict) || null);
      setDocumentFile(null);
    });
    if (uploaded?.document_id) await pollDocument(String(uploaded.document_id));
  }

  async function pollDocument(documentId: string) {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        const result = await callApi<Dict>(`/api/projects/${projectId}/documents/${documentId}`);
        const document = (result.document as Dict)?.data as Dict | undefined;
        setDocumentStatus(document || null);
        const status = String(document?.status || "");
        if (status === "SUCCEEDED" || status === "FAILED") {
          await refreshProject(projectId);
          setNotice(status === "SUCCEEDED" ? "문서 처리가 완료되었습니다." : "문서 처리에 실패했습니다.");
          return;
        }
      } catch (caught) {
        const apiError = caught as ApiError;
        setError(apiError.status ? apiError : { status: 0, message: String(caught) });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    setNotice("문서 처리 대기 중입니다. worker 상태를 확인하세요.");
  }

  async function retryDocument() {
    const documentId = text(documentStatus?.document_id, "");
    if (!documentId) return;
    const retried = await guarded("문서 재처리", () => callApi<Dict>(`/api/projects/${projectId}/documents/${documentId}/retry`, { method: "POST" }), (value) => {
      setDocumentStatus((value.document as Dict) || null);
    });
    if (retried?.document_id) await pollDocument(String(retried.document_id));
  }

  async function saveMailAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await guarded("메일 연결 메타데이터 저장", () => callApi<Dict>(`/api/projects/${projectId}/mail-account`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...mailForm, enabled: true }),
    }), async () => refreshProject(projectId));
  }

  async function savePublicFeed(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await guarded("공개 피드 등록", () => callApi<Dict>(`/api/projects/${projectId}/public-feeds`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...feedForm, enabled: true }),
    }), async () => refreshProject(projectId));
  }

  async function saveSupplierCalendar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const unavailable_dates = supplierForm.unavailable_dates.split(",").map((value) => value.trim()).filter(Boolean);
    await guarded("공급사 일정 저장", () => callApi<Dict>(`/api/projects/${projectId}/supplier-calendars`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ supplier_id: supplierForm.supplier_id, label: supplierForm.label, unavailable_dates, timezone: supplierForm.timezone }),
    }), async () => {
      setSupplierForm((current) => ({ ...current, unavailable_dates: "" }));
      await refreshProject(projectId);
    });
  }

  async function saveNotificationChannel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await guarded("알림 채널 저장", () => callApi<Dict>(`/api/projects/${projectId}/notification-channels`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...channelForm, enabled: true }),
    }), async () => refreshProject(projectId));
  }

  async function markNotification(notificationId: string) {
    await guarded("알림 확인", () => callApi<Dict>(`/api/notifications/${notificationId}`, { method: "PATCH" }), async () => refreshProject(projectId));
  }

  async function createSitePrep() {
    await guarded("현장 준비 체크리스트", () => callApi<Dict>(`/api/projects/${projectId}/site-prep`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template_id: "equipment_installation_v1" }) }), async () => refreshProject(projectId));
  }

  async function uploadImport() {
    if (!projectId || !selectedFile) {
      setError({ status: 0, message: "프로젝트와 Excel 파일이 필요합니다." });
      return;
    }
    const body = new FormData();
    body.append("file", selectedFile);
    await guarded("Excel 미리보기", () =>
      callApi<ImportPreview>(`/api/projects/${projectId}/imports`, { method: "POST", body }),
    (value) => setPreview(value));
  }

  async function confirmImport() {
    if (!projectId || !preview?.import_id) return;
    await guarded("Import 확인", () =>
      callApi<Dict>(`/api/projects/${projectId}/imports/${preview.import_id}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }),
    async () => {
      setPreview(null);
      await refreshProject(projectId);
    });
  }

  async function saveExternalWatch(plan: Dict) {
    await callApi<Dict>(`/api/projects/${projectId}/watch-plan`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(plan),
    });
    await refreshProject(projectId);
  }

  async function runScan() {
    await guarded("등록 소스 조회", () =>
      callApi<{ run_id: string; status: string }>(`/api/projects/${projectId}/scan`, {
        method: "POST",
        headers: { "Idempotency-Key": `scan-${Date.now()}` },
      }),
    (value) => setNotice("외부 출처를 확인하고 있습니다. 새 근거와 분석 결과가 자동으로 표시됩니다."));
  }

  async function createEventFromDemo() {
    const first = project.demo_events?.[0];
    if (!first) {
      setError({ status: 0, message: "hero 기준 일정을 업로드하고 확정하세요." });
      return;
    }
    await createEvent({
      event_id: text(first.event_id, "hero-change"),
      content: text(first.body || first.content),
      channel: text(first.channel, "supplier_message"),
      source_label: text(first.source_label, "가상 협력사 메시지"),
      published_at: text(first.published_at),
      mode: text(first.mode, "SYNTHETIC"),
      data_origin: "SYNTHETIC",
      simulation_as_of: text(first.published_at),
    }, true);
  }

  async function loadHeroBaseline() {
    if (!projectId) return;
    await guarded("hero 데모 일정 연결", () => callApi<Dict>(`/api/projects/${projectId}/demo/hero-baseline`, { method: "POST" }),
      async () => { await refreshProject(projectId); });
  }

  async function createEvent(payload: Dict, autoAnalyze = false) {
    const created = await guarded("이벤트 등록", () =>
      callApi<{ event_id: string; event: Dict; duplicate?: boolean }>(`/api/projects/${projectId}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    async (value) => {
      setNotice(value.duplicate ? `중복 이벤트 사용: ${value.event_id}` : `이벤트 등록: ${value.event_id}`);
      await refreshProject(projectId);
    });
    if (created && autoAnalyze) await analyzeEvent(created.event_id, true);
  }

  async function reviewEvent(eventId: string) {
    await guarded("변경 해석 확인", () =>
      callApi<Dict>(`/api/projects/${projectId}/events/${eventId}/review`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true }),
      }),
    async () => { await refreshProject(projectId); await analyzeEvent(eventId); });
  }

  async function createManualEvent() {
    await createEvent({
      content: manualMessage,
      channel: "supplier_message",
      source_label: "수동 메시지",
      mode: text((project.project || {}).mode, "LIVE"),
      data_origin: text((project.project || {}).data_origin, "USER"),
      ...(project.project?.status_as_of ? { simulation_as_of: `${project.project.status_as_of}T09:00:00+02:00` } : {}),
    }, true);
  }

  async function analyzeEvent(eventId: string, previewOnly = false) {
    setSelectedEventId(eventId); setPendingRunId(""); setRun(null); setSelectedScenarioId("");
    await guarded("영향 분석 시작", () =>
      callApi<{ run_id: string; status: string }>(`/api/projects/${projectId}/analyses`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: eventId, preview_only: previewOnly }),
      }), (value) => {
        setPendingRunId(value.run_id);
        setNotice("영향을 계산하고 있습니다. 완료되면 결과를 자동으로 표시합니다.");
      });
  }

  async function analyzeLatestEvent() {
    const id = selectedEventId || project.events?.[0]?.id;
    if (id) await analyzeEvent(id);
  }

  async function reviewExternalEvent(eventId: string, payload: Dict) {
    const reviewed = await guarded("외부 근거 적용 확인", () =>
      callApi<Dict>(`/api/projects/${projectId}/events/${eventId}/review`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }));
    if (reviewed) {
      await refreshProject(projectId);
      if (payload.confirmed) await analyzeEvent(eventId);
    }
  }

  async function fetchRun(runId?: string) {
    const id = runId || pendingRunId || text(project.runs?.find((item) => item.kind === "analysis" && (!selectedEventId || item.event_id === selectedEventId))?.id, "");
    if (!id) return;
    await guarded("분석 결과 조회", () => callApi<RunResult>(`/api/runs/${id}`), (value) => {
      if (value.run?.kind === "analysis") {
        setSelectedEventId(text(value.run.event_id, ""));
        setPendingRunId(text(value.run.id, ""));
      }
      setRun(value);
      const firstScenario = value.scenarios?.[0]?.id;
      if (firstScenario) setSelectedScenarioId(firstScenario);
    });
  }

  async function replan() {
    const runId = text(run?.run?.id || project.runs?.[0]?.id, "");
    if (!runId || budget === "") return;
    await guarded("비용 한도 반영", () =>
      callApi<{ run_id: string; status: string }>(`/api/runs/${runId}/replan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ budget_krw: budget, unavailable_option_ids: [] }),
      }),
    (value) => (setPendingRunId(value.run_id), setNotice("비용 한도를 반영한 결과를 계산하고 있습니다.")));
  }

  async function prepareScenario() {
    if (!selectedScenarioId) return;
    await guarded("대응 업무 준비", () =>
      callApi<Dict>(`/api/scenarios/${selectedScenarioId}/prepare`, { method: "POST" }),
    async () => refreshProject(projectId));
  }

  async function acceptCondition(actionId: string) {
    await guarded("조건 확인 기록", () => callApi<Dict>(`/api/actions/${actionId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: "ACCEPTED", note: conditionNotes[actionId] }),
    }), async () => refreshProject(projectId));
  }

  async function approveScenario() {
    const scenario = selectedScenario;
    if (!scenario?.id) return;
    const required = (scenario.data?.required_confirmations || []) as string[];
    await guarded("시나리오 승인", () =>
      callApi<Dict>(`/api/scenarios/${scenario.id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor: "프로젝트 운영팀", decision: "APPROVED", confirmed_conditions: required }),
      }));
  }

  async function commitScenario() {
    if (!selectedScenarioId) return;
    await guarded("일정 확정", () =>
      callApi<Dict>(`/api/scenarios/${selectedScenarioId}/commit`, { method: "POST" }),
    async () => refreshProject(projectId));
  }

  async function downloadExport() {
    if (!projectId) return;
    await guarded("Excel 다운로드", async () => {
      const response = await callApi<Response>(`/api/projects/${projectId}/export`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `replan-${projectId}.xlsx`;
      anchor.click();
      URL.revokeObjectURL(url);
      return { ok: true };
    });
  }

  const tasks = useMemo(() => {
    const snapshot = project.version?.data as Dict | undefined;
    return (snapshot?.tasks || []) as Dict[];
  }, [project.version]);

  const selectedScenario = useMemo(() => {
    return run?.scenarios?.find((item) => item.id === selectedScenarioId);
  }, [run?.scenarios, selectedScenarioId]);

  const scenarioSchedule = useMemo(() => {
    return ((selectedScenario?.data?.schedule || []) as Dict[]).reduce<Record<string, Dict>>((acc, item) => {
      acc[text(item.task_id)] = item;
      return acc;
    }, {});
  }, [selectedScenario]);

  const pendingReviews = project.events?.filter((item) => item.data?.review_status === "PENDING").length || 0;
  const openActions = project.actions?.filter((item) => String(item.data?.state || "OPEN") === "OPEN").length || 0;
  const eventCount = project.events?.length || 0;
  const runCount = project.runs?.length || 0;
  const currentStep = !project.version ? 1 : project.version.status === "committed" ? 6 : selectedScenarioId ? 5 : run?.scenarios?.length ? 4 : eventCount ? 3 : 2;
  const projectName = text((project.project || {}).name, "프로젝트 없음");

  return (
    <main className="human-workspace">
      <header className="brand-bar human-nav" aria-label="REPLAN workspace">
        <a className="brand-lockup" href="/" aria-label="REPLAN 홈">
          <Image src="/brand/replan-wordmark.png" alt="REPLAN" width={1500} height={350} priority />
        </a>
        <div className="brand-context">
          <span className="brand-context-dot" aria-hidden="true" />
          <span>프로젝트 운영팀</span>
          <span className="brand-divider" aria-hidden="true" />
          <span>공용 계정</span>
        </div>
        <div className="human-nav-meta"><span className="nav-live-dot" /> <span>TEAM WORKSPACE</span><span className="nav-meta-divider" /> <span>{shortId(projectId, "NEW")}</span></div>
      </header>
      <section className="hero human-hero" id="overview">
        <div className="hero-copy">
          <p className="eyebrow">PROJECT OVERVIEW</p>
          <div className="project-title-row"><h1>{projectName}</h1></div>
          <p className="subtitle">{notice}</p>
          <div className="hero-context"><span className="context-marker" aria-hidden="true" /><span>프로젝트 ID {shortId(projectId, "미지정")}</span><span className="context-slash">/</span><span>{eventCount ? `변경 ${eventCount}건` : "변경 없음"}</span></div>
        </div>
        <div className="status-card human-status-card">
          <span className="status-kicker">WORKSPACE STATUS</span>
          <strong>{error ? "연결 확인 필요" : project.version ? "기준 일정 연결됨" : "기준 일정 대기"}</strong>
          <small>{notice}</small>
          <a href={currentStep < 3 ? "#onboarding" : currentStep < 5 ? "#changes" : "#scenarios"} className="hero-action">{currentStep < 3 ? "기준 일정 연결" : currentStep < 5 ? "변경 영향 확인" : "승인 큐 열기"}<span aria-hidden="true">↗</span></a>
          <div className="hero-scene" aria-hidden="true">
            <Image
              className="workspace-illustration workspace-status-illustration"
              src="/images/workspace/workspace-status-flat.png"
              alt=""
              fill
              sizes="330px"
              priority
            />
          </div>
        </div>
      </section>

      <nav className="workspace-stepper" aria-label="프로젝트 진행 단계">
        {[
          ["01", "Brief", "프로젝트 맥락"],
          ["02", "Import", "기준 일정"],
          ["03", "Detect", "변경 감지"],
          ["04", "Compare", "대응안 비교"],
          ["05", "Approve", "조건 승인"],
          ["06", "Execute", "일정 반영·실행 추적"],
        ].map(([number, label, description], index) => {
          const step = index + 1;
          return <a className={`step-item ${step === currentStep ? "current" : ""} ${step < currentStep ? "complete" : ""}`} href={step <= 2 ? "#onboarding" : step <= 4 ? "#changes" : "#scenarios"} key={number}><span className="step-number">{step < currentStep ? "✓" : number}</span><span><b>{label}</b><small>{description}</small></span></a>;
        })}
      </nav>

      {error && (
        <aside className="error">
          <b>{error.status ? `HTTP ${error.status}` : "UI"}</b>
          <span>{error.message}</span>
        </aside>
      )}

      <section className="workspace-summary human-summary" aria-label="프로젝트 요약">
        <article className="summary-card summary-card-primary"><span>DECISION QUEUE</span><strong>{pendingReviews ? `${pendingReviews}건 근거 확인 필요` : "근거 확인 대기 없음"}</strong><small>{!project.version ? "기준 Excel을 업로드하세요." : pendingReviews ? "근거와 적용 조건을 확인하세요." : "분석 결과와 감시 상태를 확인하세요."}</small></article>
        <article className="summary-card"><span>최근 변경</span><strong>{eventCount || "—"}</strong><small>{eventCount ? "저장된 이벤트" : "아직 변경 없음"}</small></article>
        <article className="summary-card"><span>근거 확인 대기</span><strong>{pendingReviews || "—"}</strong><small>{pendingReviews ? "적용 여부 확인 필요" : "미확인 근거 없음"}</small></article>
        <article className="summary-card"><span>실행 항목</span><strong>{openActions || "—"}</strong><small>{openActions ? "열린 업무" : `${runCount || 0}개 분석 실행`}</small></article>
      </section>

      <section className="workspace human-grid">
        <aside className="panel sidebar context-rail" id="onboarding">
          <div className="rail-heading"><div><p className="eyebrow">CONTEXT RAIL</p><h2>프로젝트 맥락</h2></div><span className="rail-index">01</span></div>
          <p className="rail-intro">기준 Excel과 프로젝트 운영 입력을 관리합니다.</p>
          <small className="muted">프로젝트 운영팀 공용 계정이 기준 일정과 프로젝트 결정을 관리합니다.</small>
          <div className="preview workspace-create-note">
            <b>새 프로젝트</b>
            <small>프로젝트 이름을 정한 뒤 기준 Excel을 연결합니다.</small>
            <a href="/workspaces/new">프로젝트 만들기 <span aria-hidden="true">↗</span></a>
          </div>
          <label>
            Project ID
            <input value={projectId} onChange={(event) => setProjectId(event.target.value)} placeholder="기존 프로젝트 ID" />
          </label>
          <button className="secondary" onClick={() => refreshProject()} disabled={busy || !projectId}>프로젝트 불러오기</button>
          <button className="secondary" onClick={loadProjects} disabled={busy}>프로젝트 목록</button>
          {projects.length > 0 && <select value={projectId} onChange={(event) => { setProjectId(event.target.value); refreshProject(event.target.value); }}><option value="">프로젝트 선택</option>{projects.map((item) => <option key={text(item.id)} value={text(item.id)}>{text(item.name, text(item.id))}</option>)}</select>}

          <div className="divider" />
          <h2>2. Excel import</h2>
          <button className="secondary" onClick={loadHeroBaseline} disabled={busy || !projectId || Boolean(project.version)}>hero 데모 기준 일정 사용 (64개 작업)</button>
          <input type="file" accept=".xlsx,.csv" onChange={(event: ChangeEvent<HTMLInputElement>) => setSelectedFile(event.target.files?.[0] || null)} />
          <button onClick={uploadImport} disabled={busy || !selectedFile || !projectId}>Excel 업로드·미리보기</button>
          {preview && (
            <div className="preview">
              <b>{preview.import_kind === "change" ? "수정 Excel" : "Baseline Excel"}</b>
              <span>{preview.tasks?.length || 0} tasks · {preview.options?.length || 0} options · {preview.events?.length || 0} demo events</span>
              {preview.diff?.summary && <small>Diff {JSON.stringify(preview.diff.summary)}</small>}
              <button onClick={confirmImport} disabled={busy}>확인 후 저장</button>
            </div>
          )}
          <div className="divider" />
          <p className="eyebrow">EVIDENCE / 02</p>
          <h2>보조 근거 연결</h2>
          <input type="file" accept=".pdf,.txt,.md,.eml" onChange={(event: ChangeEvent<HTMLInputElement>) => setDocumentFile(event.target.files?.[0] || null)} />
          <button onClick={uploadDocument} disabled={busy || !documentFile || !projectId}>PDF·메일 텍스트 입력</button>
          <small className="muted">업로드 후 worker가 파싱하고, 완료되면 검토 이벤트를 생성합니다.</small>
          {documentStatus && (
            <div className="preview">
              <b>{text(documentStatus.filename)} · {text(documentStatus.status)}</b>
              {Boolean(documentStatus.error) && <small className="muted">{text(documentStatus.error)}</small>}
              {Boolean(documentStatus.event_id) && <small className="muted">이벤트 {shortId(documentStatus.event_id)}</small>}
              {documentStatus.status === "FAILED" && <button className="secondary" onClick={retryDocument} disabled={busy}>다시 처리</button>}
            </div>
          )}
        </aside>

        <section className="panel main-panel decision-canvas" id="schedule">
          <div className="canvas-intro"><div><p className="eyebrow">SCHEDULE / 02</p><h2>일정 변경</h2><p>기준 일정과 변경 이벤트를 확인합니다.</p></div><span className="canvas-state"><i />{!project.version ? "BASELINE REQUIRED" : eventCount ? "CHANGE DETECTED" : "BASELINE READY"}</span></div>
          <div className="workspace-scene-panel" aria-hidden="true">
            <div className="scene-panel-copy"><span className="scene-kicker">CURRENT STATE</span><strong>{eventCount ? "변경 이벤트가 있습니다" : "기준 일정이 없습니다"}</strong><span>{eventCount ? "이벤트 타임라인에서 영향 범위를 확인하세요." : "작업·선후행·자원 조건이 포함된 Excel을 업로드하세요."}</span></div>
            <div className="scene-panel-art">
              <Image
                className="workspace-illustration workspace-schedule-illustration"
                src="/images/workspace/workspace-schedule-flat.png"
                alt=""
                fill
                sizes="(max-width: 860px) 45vw, 360px"
              />
            </div>
          </div>
          <div className="section-head">
            <div id="changes">
              <p className="eyebrow">PROJECT PULSE</p>
              <h2>기준 일정과 변경 영향</h2>
              <p>기준 버전 {shortId(project.version?.id)} · hash {shortId(project.version?.content_hash)}</p>
              {Boolean(project.project?.status_as_of) && <p>합성 데모 기준일 {text(project.project?.status_as_of)} · 전체 {tasks.length}개 작업 · 완료 {tasks.filter((task) => task.status === "completed").length} · 진행 중 {tasks.filter((task) => task.status === "in_progress").length} · 예정 {tasks.filter((task) => task.status === "planned").length}</p>}
            </div>
            <button className="secondary" onClick={downloadExport} disabled={!project.version}>Excel-out</button>
          </div>
          {!tasks.length ? (
            <div className="workspace-empty-state">
              <div className="workspace-empty-index">01</div>
              <div><p className="eyebrow">BASELINE REQUIRED</p><h3>기준 일정이 없습니다.</h3><p>작업·선후행·자원 조건이 포함된 Excel을 업로드하면 일정 변경을 비교할 수 있습니다.</p><a href="#onboarding">Excel 업로드 <span aria-hidden="true">↗</span></a></div>
            </div>
          ) : <Gantt tasks={tasks} scenarioSchedule={scenarioSchedule} />}

          <div className="timeline-grid">
            <div>
              <h3>이벤트 타임라인</h3>
              <div className="event-list">
                {(project.events || []).map((event) => {
                  const data = event.data || event;
                  return (
                    <article key={text(event.id || data.id)} className="event-card">
                      <b>{text(data.title, "변경 메시지")}</b>
                      <p>{text(data.content || data.title)}</p>
                      <small>{text(data.source_label)} · {data.review_status === "CONFIRMED" ? "변경 해석 확인됨" : "변경 해석 확인 필요"}</small>
                      <small>{text(data.mode)} · {text(data.data_origin)}</small>
                      {Array.isArray(data.verification_required) && data.verification_required.length > 0 &&
                        <small>추가 확인: {(data.verification_required as string[]).join(" · ")}</small>}
                      {Array.isArray(data.extracted_facts) && data.extracted_facts.length > 0 && (
                        <ul className="event-facts">
                          {(data.extracted_facts as Dict[]).slice(0, 3).map((fact, index) => (
                            <li key={`${text(fact.kind, "fact")}-${index}`}>
                              <span>{text(fact.kind, "변경")}</span> {text(fact.value)}
                            </li>
                          ))}
                        </ul>
                      )}
                      {data.evidence ? <EvidenceReview event={data} tasks={tasks} disabled={busy}
                        onReview={(payload) => reviewExternalEvent(text(event.id || data.id, ""), payload)}
                        onAnalyze={() => analyzeEvent(text(event.id || data.id, ""))} /> : null}
                      {!data.evidence && Object.keys((data.patch || {}) as Dict).length > 0 && data.review_status !== "CONFIRMED" && (
                        <button className="text-button event-review-button" onClick={() => reviewEvent(text(event.id || data.id, ""))} disabled={busy}>변경 해석 확인</button>
                      )}
                    </article>
                  );
                })}
              </div>
            </div>
            <div id="history">
              <h3>Run 상태</h3>
              <div className="event-list">
                {(project.runs || []).map((item) => (
                  <button key={text(item.id)} className="run-row" onClick={() => fetchRun(text(item.id))}>
                    <span>{text(item.kind)}</span>
                    <b>{text(item.status)}</b>
                    <small>{shortId(item.id)}</small>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        <aside className="panel decision notes-rail">
          <div className="rail-heading"><div><p className="eyebrow">NOTES & QUEUE</p><h2>감시·분석·승인</h2></div><span className="rail-index">03</span></div>
          <p className="rail-intro">알림, 실행 항목, 연결 상태를 관리합니다.</p>
          <div className="decision-lead">
            <p className="eyebrow">NEXT DECISION</p>
            <h3>{!project.version ? "기준 일정 연결 필요" : eventCount ? "외부 근거·영향 확인" : "외부 변화 감시 설정"}</h3>
            <p>{!project.version ? "기준 Excel을 연결하세요." : eventCount ? "변경별 근거와 계산 결과를 확인하세요. 적용 조건이 확인되어야 승인할 수 있습니다." : "현장·공휴일·공식 출처를 작업에 연결하면 주기적으로 확인합니다."}</p>
            <span>{eventCount ? `${eventCount}건의 변경 기록` : project.version ? "기준 일정 준비됨" : "아직 기준 버전 없음"}</span>
          </div>
          <ExternalWatch plan={project.watch_plan || {}} tasks={tasks} disabled={!project.version || busy}
            onSave={saveExternalWatch} onScan={runScan} />
          {(project.source_snapshots || []).slice(0, 5).map((source) => (
            <small className="external-source-state" key={text(source.id)}>
              {text(source.data?.url, text(source.source_id))} · {source.status === "ok" ? "수집 성공" : "수집 실패 · 위험 여부 확인 불가"} · {text(source.fetched_at)}
            </small>
          ))}
          <div className="p1-card" id="scenarios">
            <b>실행 준비</b>
            <span>미확인 알림 {project.notifications?.filter((item) => item.data?.status === "UNREAD").length || 0}건</span>
            <span>공급사 캘린더 {project.supplier_calendars?.length || 0}건 · 현장 준비 {project.site_prep_items?.length || 0}건</span>
            <button className="secondary" onClick={createSitePrep} disabled={!projectId || busy}>현장 준비 템플릿 적용</button>
          </div>

          <section className="p1-operations" aria-labelledby="p1-operations-title">
            <div className="section-head compact"><div><h3 id="p1-operations-title">운영 입력</h3><p>외부 근거를 연결하고 적용 조건을 확인합니다.</p></div></div>

            <form className="p1-form" onSubmit={savePublicFeed}>
              <div className="p1-form-title"><b>공개 피드</b><span>{project.public_feeds?.length || 0}개 등록</span></div>
              <input aria-label="피드 이름" value={feedForm.label} onChange={(event) => setFeedForm({ ...feedForm, label: event.target.value })} placeholder="피드 이름" />
              <input aria-label="피드 URL" type="url" value={feedForm.url} onChange={(event) => setFeedForm({ ...feedForm, url: event.target.value })} placeholder="https://허용된-공식-출처" />
              <button className="secondary" type="submit" disabled={!projectId || busy}>피드 등록</button>
              {(project.public_feeds || []).slice(0, 2).map((item) => <small className="p1-record" key={text(item.id || item.data?.feed_id)}>{text(item.data?.label)} · {text(item.data?.url)}</small>)}
            </form>

            <form className="p1-form" onSubmit={saveSupplierCalendar}>
              <div className="p1-form-title"><b>공급사 캘린더</b><span>{project.supplier_calendars?.length || 0}개 등록</span></div>
              <input aria-label="공급사 ID" value={supplierForm.supplier_id} onChange={(event) => setSupplierForm({ ...supplierForm, supplier_id: event.target.value })} placeholder="공급사 ID" required />
              <input aria-label="공급사 캘린더 이름" value={supplierForm.label} onChange={(event) => setSupplierForm({ ...supplierForm, label: event.target.value })} placeholder="캘린더 이름" required />
              <input aria-label="공급사 휴무일" value={supplierForm.unavailable_dates} onChange={(event) => setSupplierForm({ ...supplierForm, unavailable_dates: event.target.value })} placeholder="휴무일: 2026-10-03, 2026-10-04" />
              <button className="secondary" type="submit" disabled={!projectId || busy}>일정 저장</button>
            </form>

            <form className="p1-form" onSubmit={saveMailAccount}>
              <div className="p1-form-title"><b>메일 연결 메타데이터</b><span>{text(project.mail_account?.status, "미설정")}</span></div>
              <div className="p1-inline-fields"><select aria-label="메일 제공자" value={mailForm.provider} onChange={(event) => setMailForm({ ...mailForm, provider: event.target.value })}><option value="imap">IMAP</option><option value="gmail">Gmail</option><option value="outlook">Outlook</option></select><input aria-label="메일 호스트" value={mailForm.host} onChange={(event) => setMailForm({ ...mailForm, host: event.target.value })} placeholder="imap.example.com" required /></div>
              <input aria-label="메일 사용자" value={mailForm.username} onChange={(event) => setMailForm({ ...mailForm, username: event.target.value })} placeholder="담당자 이메일" required />
              <button className="secondary" type="submit" disabled={!projectId || busy}>연결 정보 저장</button>
              <small className="muted">자동 메일 수신은 미연결입니다. 현재는 연결 정보만 저장합니다.</small>
            </form>

            <form className="p1-form" onSubmit={saveNotificationChannel}>
              <div className="p1-form-title"><b>알림 채널</b><span>외부 발송은 초안</span></div>
              <div className="p1-inline-fields"><select aria-label="알림 채널 유형" value={channelForm.channel} onChange={(event) => setChannelForm({ ...channelForm, channel: event.target.value })}><option value="in_app">인앱</option><option value="email">이메일 초안</option><option value="slack">Slack 초안</option><option value="webhook">Webhook 초안</option></select><input aria-label="알림 대상" value={channelForm.target} onChange={(event) => setChannelForm({ ...channelForm, target: event.target.value })} placeholder="대상 또는 채널" /></div>
              <button className="secondary" type="submit" disabled={!projectId || busy}>채널 저장</button>
            </form>

            <div className="p1-notifications">
              <div className="p1-form-title"><b>알림 기록</b><span>{project.notifications?.length || 0}건</span></div>
              {(project.notifications || []).slice(0, 4).map((notification) => {
                const data = notification.data || notification;
                const notificationId = text(notification.id || data.id, "");
                return <div className="p1-notification" key={notificationId}><div><b>{text(data.title, "알림")}</b><small>{text(data.message, text(data.body))}</small></div>{data.status === "UNREAD" && <button className="text-button" onClick={() => markNotification(notificationId)} disabled={busy}>확인</button>}</div>;
              })}
              {!project.notifications?.length && <small className="muted">새 알림이 생기면 여기에 표시됩니다.</small>}
            </div>
          </section>

          <div className="button-row">
            <button onClick={createEventFromDemo} disabled={!project.demo_events?.length || busy}>hero 합성 통보 불러오기</button>
            <button className="secondary" onClick={analyzeLatestEvent} disabled={!project.events?.length || busy}>영향 분석 시작</button>
          </div>
          <small>보조 입력: 협력사 메일 내용 또는 변경 통보 (샘플은 합성 데이터). 입력 후 영향 미리보기가 자동으로 표시됩니다.</small>
          <textarea aria-label="협력사 변경 통보" value={manualMessage} onChange={(event) => setManualMessage(event.target.value)} rows={4} />
          <button className="secondary" onClick={createManualEvent} disabled={!project.version || busy}>변경 메시지 등록</button>

          <div className="button-row">
            <button onClick={() => fetchRun()} disabled={!project.runs?.length || busy}>분석 결과 보기</button>
            <label className="replan-budget-field">
              대응안 비교용 비용 한도 (선택)
              <input className="budget" type="number" min="0" step="100000" value={budget} onChange={(event) => setBudget(event.target.value === "" ? "" : Number(event.target.value))} />
            </label>
            <button className="secondary" onClick={replan} disabled={!run?.run || budget === "" || busy}>비용 한도 반영</button>
          </div>

          {run?.run && <div className="impact-result" aria-live="polite">
            <h3>영향 분석 결과</h3>
            <small>{text(run.run.status)} · {shortId(run.run.event_id)}</small>
            <p>{text(run.run.data?.summary, run.run.status === "failed" ? "분석 실패: 다시 시도하거나 입력을 확인하세요." : "계산 중입니다.")}</p>
            {selectedScenario && <>
              <p>기준 완료 {text(selectedScenario.data?.baseline_finish)} → 예상 완료 {text(selectedScenario.data?.finish_date)}</p>
              <b>완료일 변화 {text(selectedScenario.data?.finish_shift_days)}일 · 영향 작업 {((selectedScenario.data?.changed_tasks || []) as Dict[]).length}개</b>
              {selectedScenario.data?.provisional ? <p>잠정 계산입니다. 변경 해석과 필요한 조건을 확인한 뒤 승인하세요.</p> : null}
              {((selectedScenario.data?.included_events || []) as Dict[]).length > 0 && <p>함께 반영한 외부 변화: {((selectedScenario.data?.included_events || []) as Dict[]).map((item) => text(item.title)).join(" · ")}</p>}
              <ul>{((selectedScenario.data?.changed_tasks || []) as Dict[]).map((task) => <li key={text(task.task_id)}>{text(task.task_id)} · {text(task.name)} · {task.direct ? "직접 영향" : "후속 영향"}<br />{text(task.before_finish)} → {text(task.after_finish)}</li>)}</ul>
            </>}
          </div>}

          <ScenarioList scenarios={run?.scenarios || []} selected={selectedScenarioId} onSelect={setSelectedScenarioId} />

          {selectedScenario && (
            <div className="approval-card">
              <b>{text(selectedScenario.data?.label)} · {scenarioScore(selectedScenario.data || {})}</b>
              <small>완료 예정 {text(selectedScenario.data?.finish_date)} · 추가 비용 {costLabel(selectedScenario.data || {})}</small>
              <small>확인이 필요한 조건 {((selectedScenario.data?.required_confirmations as unknown[]) || []).length}건</small>
              {(project.actions || []).filter((item) => item.scenario_id === selectedScenarioId).map((action) => (
                <div key={text(action.id)} className="condition-review">
                  <p>{text(action.data?.request)} · {text(action.data?.state)}</p>
                  <label>회신·확인 근거<input value={conditionNotes[text(action.id)] || ""} onChange={(event) => setConditionNotes({ ...conditionNotes, [text(action.id)]: event.target.value })} /></label>
                  <button className="secondary" disabled={busy || !conditionNotes[text(action.id)]?.trim()} onClick={() => acceptCondition(text(action.id))}>이 조건 확인 기록</button>
                </div>
              ))}
              <div className="button-row wrap">
                <button onClick={prepareScenario} disabled={busy}>확인 요청 만들기</button>

                <button className="secondary" onClick={approveScenario} disabled={busy}>승인</button>
                <button onClick={commitScenario} disabled={busy}>일정 확정</button>
              </div>
            </div>
          )}

          <h3 id="actions">Action items</h3>
          <div className="event-list">
            {(project.actions || []).map((action) => (
              <article key={text(action.id)} className="action-row">
                <b>{text(action.data?.state)}</b>
                <span>{text(action.data?.request)}</span>
                <small>{shortId(action.data?.scenario_id)}</small>
              </article>
            ))}
          </div>
        </aside>
      </section>
    </main>
  );
}

function ScenarioList({ scenarios, selected, onSelect }: { scenarios: Array<Dict & { id?: string; data?: Dict }>; selected: string; onSelect: (id: string) => void }) {
  if (!scenarios.length) {
    return <div className="empty">worker가 analysis run을 처리하면 시나리오가 여기에 표시됩니다.</div>;
  }
  return (
    <div className="scenario-list">
      {scenarios.map((scenario) => {
        const data = scenario.data || {};
        return (
          <button key={text(scenario.id)} className={selected === scenario.id ? "scenario active" : "scenario"} onClick={() => onSelect(text(scenario.id))}>
            <span>{text(data.label)}</span>
            <b>{text(data.finish_date)}</b>
            <small>{scenarioScore(data)} · {costLabel(data)}</small>
          </button>
        );
      })}
    </div>
  );
}

function Gantt({ tasks, scenarioSchedule }: { tasks: Dict[]; scenarioSchedule: Record<string, Dict> }) {
  const visibleTasks = tasks.some((task) => task.task_id === "T036") && tasks.length === 64
    ? tasks.filter((task) => /^T0(3[4-9]|4[0-9]|5[0-7])$/.test(text(task.task_id)))
    : tasks.slice(0, 20);
  const dates = visibleTasks.flatMap((task) => {
    const original = taskDates(task);
    const scenario = scenarioSchedule[text(task.task_id)];
    return [original.start, original.finish, text(scenario?.planned_start, ""), text(scenario?.planned_finish, "")].filter(Boolean);
  });
  const min = dates.length ? new Date(dates.sort()[0]) : null;
  const max = dates.length ? new Date(dates.sort()[dates.length - 1]) : null;
  const total = min && max ? Math.max(1, (max.getTime() - min.getTime()) / 86400000 + 1) : 1;

  if (!tasks.length) return <div className="empty">Excel 기준 일정을 확정하면 작업 일정이 표시됩니다.</div>;

  return (
    <div className="gantt">
      {visibleTasks.map((task) => {
        const id = text(task.task_id);
        const original = taskDates(task);
        const scenario = scenarioSchedule[id];
        const start = new Date(text(scenario?.planned_start, original.start));
        const finish = new Date(text(scenario?.planned_finish, original.finish));
        const left = min ? ((start.getTime() - min.getTime()) / 86400000 / total) * 100 : 0;
        const width = Math.max(4, ((finish.getTime() - start.getTime()) / 86400000 + 1) / total * 100);
        const changed = Boolean(scenario);
        const finishLabel = text(scenario?.planned_finish, original.finish);
        return (
          <div className="gantt-row" key={id}>
            <div className="task-meta">
              <b>{id}</b>
              <span>{text(task.name)}{task.status === "in_progress" ? " · 진행 중" : task.status === "completed" ? " · 완료" : ""}</span>
              <time className="task-date" dateTime={finishLabel}>{finishLabel.slice(5)}</time>
            </div>
            <div className="bar-track">
              <div className={changed ? "bar changed" : "bar"} style={{ left: `${left}%`, width: `${width}%` }} title={`${id} 완료 ${finishLabel}`} aria-label={`${id} 완료 ${finishLabel}`} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
