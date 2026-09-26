"use client";

import { ChangeEvent, FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import { ExternalWatch, EvidenceReview } from "./external-watch";
import { STAGES, SectionId, deriveProgress, hasPatch, isPreview, runScenarioCount, sectionFromHash, stageSummary } from "./stages";

type Dict = Record<string, unknown>;
type Row = Dict & { id?: string; data?: Dict; status?: string; kind?: string; event_id?: string; scenario_id?: string; created_at?: string };

type ApiError = {
  status: number;
  message: string;
};

type Feedback = { kind: "guide" | "error"; text: string };

type ProjectState = {
  project?: Dict;
  version?: Row & { content_hash?: string };
  watch_plan?: Dict | null;
  events?: Row[];
  source_snapshots?: Array<Row & { source_id?: string; fetched_at?: string }>;
  runs?: Row[];
  actions?: Row[];
  documents?: Row[];
  notifications?: Row[];
  public_feeds?: Row[];
  mail_account?: Dict | null;
  decision_deadlines?: Row[];
  site_prep_items?: Row[];
  supplier_calendars?: Row[];
  demo_events?: Dict[];
  versions?: Row[];
  approvals?: Row[];
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
  diff?: Dict & { summary?: Dict; changed?: Dict[]; added?: unknown[]; removed?: unknown[] };
  warnings?: string[];
};

type RunResult = {
  run?: Row;
  scenarios?: Row[];
};

const apiBase = "/api/proxy";

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
  return raw.length > 10 ? raw.slice(0, 8) : raw;
}

function when(value: unknown) {
  const raw = text(value, "");
  return raw ? raw.replace("T", " ").slice(0, 16) : "-";
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

const RUN_KIND: Record<string, string> = { analysis: "영향 분석", scan: "외부 출처 확인", watch_plan_enrich: "감시 계획 보강" };
const RUN_STATUS: Record<string, string> = { queued: "대기", running: "진행 중", succeeded: "완료", failed: "실패" };
const ACTION_STATE: Record<string, string> = { OPEN: "확인 대기", ACCEPTED: "확인됨", REJECTED: "반려", DONE: "완료" };
const PATCH_KIND: Record<string, string> = { estimated_finish: "완료 예정일", not_before: "착수 가능일", blocked_dates: "작업 불가일" };

function runLabel(run?: Row) {
  if (!run) return "-";
  const kind = run.kind === "analysis" && isPreview(run) ? "통보 해석(잠정)" : RUN_KIND[text(run.kind)] || text(run.kind);
  return `${kind} · ${RUN_STATUS[text(run.status)] || text(run.status)}`;
}

// Server messages are English/internal; tell the user what to do next instead.
function friendly(error: ApiError): Feedback {
  const message = error.message || "";
  const guides: Array<[RegExp, string]> = [
    [/prepare actions before (approval|commit)/, "먼저 '확인 요청 만들기'로 이 대응안의 조건 확인 요청을 만드세요."],
    [/approval required/, "대응안을 승인한 뒤에 새 일정 버전을 확정할 수 있습니다."],
    [/unconfirmed_conditions|unaccepted_conditions|required conditions are no longer accepted/, "확인 기록이 없는 조건이 있습니다. 각 조건의 회신·확인 근거를 기록하세요."],
    [/violates hard constraints/, "이 대응안은 예산 또는 제약 조건을 넘어 승인할 수 없습니다. 다른 안을 고르거나 비용 한도를 조정하세요."],
    [/a baseline already exists/, "이미 기준 일정이 연결되어 있습니다."],
    [/confirm a baseline first/, "먼저 일정 단계에서 기준 일정을 연결하세요."],
    [/review the proposed change/, "변경 해석을 먼저 확인해야 영향 분석을 시작할 수 있습니다."],
    [/watch plan is disabled/, "감시 계획을 활성화한 뒤에 외부 변화를 확인할 수 있습니다."],
    [/schedule changed|replan required|older schedule|scan again/, "기준 일정이 바뀌었습니다. 변경 화면에서 영향 분석을 다시 시작하세요."],
    [/rejected or superseded|source has changed/, "보류되었거나 새 통보로 대체된 변경입니다. 최신 변경을 확인하세요."],
    [/task IDs changed/, "수정 Excel의 작업 ID가 기준 일정과 다릅니다. 작업 ID를 맞춘 뒤 다시 올리세요."],
  ];
  const guide = guides.find(([pattern]) => pattern.test(message));
  if (guide) return { kind: "guide", text: guide[1] };
  if (error.status >= 400 && error.status < 500) return { kind: "guide", text: message };
  return { kind: "error", text: error.status ? `서버 연결을 확인하세요 (HTTP ${error.status}). ${message}` : message };
}

function go(section: SectionId, focusId?: string) {
  window.location.hash = section;
  if (focusId) window.setTimeout(() => document.getElementById(focusId)?.scrollIntoView({ behavior: "smooth", block: "center" }), 60);
}

export default function Home({ initialProjectId = "" }: { initialProjectId?: string }) {
  const projectId = initialProjectId;
  const [project, setProject] = useState<ProjectState>({});
  const [loaded, setLoaded] = useState(false);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [changeFile, setChangeFile] = useState<File | null>(null);
  const [changePreview, setChangePreview] = useState<ImportPreview | null>(null);
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [documentStatus, setDocumentStatus] = useState<Dict | null>(null);
  const [mailForm, setMailForm] = useState({ provider: "imap", host: "", username: "", folder: "INBOX" });
  const [feedForm, setFeedForm] = useState({ label: "환경 정책 RSS", url: "https://environment.ec.europa.eu/news_en", kind: "rss" });
  const [supplierForm, setSupplierForm] = useState({ supplier_id: "", label: "", unavailable_dates: "", timezone: "Asia/Seoul" });
  const [channelForm, setChannelForm] = useState({ channel: "in_app", label: "REPLAN 인앱 알림", target: "" });
  const [projects, setProjects] = useState<Dict[]>([]);
  const [run, setRun] = useState<RunResult | null>(null);
  const [viewRunId, setViewRunId] = useState("");
  const [selectedScenarioId, setSelectedScenarioId] = useState("");
  const [conditionNotes, setConditionNotes] = useState<Record<string, string>>({});
  const [manualMessage, setManualMessage] = useState("");
  const [selectedDemoIndex, setSelectedDemoIndex] = useState(-1);
  const [budget, setBudget] = useState<number | "">("");
  const [notice, setNotice] = useState("프로젝트 작업공간을 불러오는 중입니다.");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [activeSection, setActiveSection] = useState<SectionId>("overview");
  const viewRunRef = useRef("");
  const awaitingRef = useRef<{ runId: string; kind: "analysis" | "replan" } | null>(null);
  const sectionRef = useRef<SectionId>("overview");

  useEffect(() => { viewRunRef.current = viewRunId; }, [viewRunId]);
  useEffect(() => { sectionRef.current = activeSection; }, [activeSection]);

  useEffect(() => {
    const syncSection = () => setActiveSection(sectionFromHash(window.location.hash));
    syncSection();
    window.addEventListener("hashchange", syncSection);
    return () => window.removeEventListener("hashchange", syncSection);
  }, []);

  useEffect(() => {
    if (projectId) sessionStorage.setItem("replan.projectId", projectId);
  }, [projectId]);

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

  // The run shown on the comparison screen follows the latest change unless the user picked one from history.
  function displayRunId(value: ProjectState) {
    if (viewRunRef.current) return viewRunRef.current;
    const progress = deriveProgress(value);
    const focusRuns = (value.runs || []).filter((item) => item.kind === "analysis" && item.event_id === progress.focusEvent?.id);
    return text(progress.approval?.run_id || progress.analysisRun?.id || progress.pendingAnalysis?.id || focusRuns[0]?.id, "");
  }

  async function sync(id = projectId) {
    if (!id) return null;
    const value = await callApi<ProjectState>(`/api/projects/${id}`);
    setProject(value);
    setLoaded(true);
    const runId = displayRunId(value);
    if (runId) {
      const result = await callApi<RunResult>(`/api/runs/${runId}`);
      setRun(result);
      const approved = (value.approvals || []).find((item) => result.scenarios?.some((scenario) => scenario.id === item.scenario_id));
      setSelectedScenarioId((current) => result.scenarios?.some((item) => item.id === current) ? current : text(approved?.scenario_id, ""));
      settleAwaited(result);
    } else {
      setRun(null);
    }
    return value;
  }

  function settleAwaited(result: RunResult) {
    const awaited = awaitingRef.current;
    if (!awaited || result.run?.id !== awaited.runId || !["succeeded", "failed"].includes(text(result.run?.status))) return;
    awaitingRef.current = null;
    if (result.run?.status === "failed") {
      setFeedback({ kind: "error", text: "영향 분석이 실패했습니다. 이력에서 실행 기록을 확인하세요." });
      return;
    }
    if (result.scenarios?.length) {
      setNotice(awaited.kind === "replan" ? "비용 한도를 반영한 대응안을 표시했습니다." : "분석이 끝났습니다. 대응안을 비교하고 승인할 안을 고르세요.");
      if (awaited.kind === "analysis" && sectionRef.current === "changes") go("scenarios");
    } else {
      setNotice(text(result.run?.data?.summary, "분석이 멈췄습니다. 변경 화면에서 작업과 날짜를 확인하세요."));
    }
  }

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    let inFlight = false;
    const poll = async () => {
      if (inFlight || !active) return;
      inFlight = true;
      try {
        await sync(projectId);
        setNotice((current) => current === "프로젝트 작업공간을 불러오는 중입니다." ? "프로젝트를 불러왔습니다." : current);
      } catch (caught) {
        if (active) setFeedback(friendly(caught as ApiError));
      } finally { inFlight = false; }
    };
    void poll();
    const timer = window.setInterval(poll, 4000);
    return () => { active = false; window.clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function guarded<T>(label: string, action: () => Promise<T>, done?: (value: T) => void | Promise<unknown>) {
    setBusy(true);
    setFeedback(null);
    setNotice(`${label} 처리 중…`);
    try {
      const value = await action();
      await done?.(value);
      setNotice((current) => current === `${label} 처리 중…` ? `${label} 완료` : current);
      return value;
    } catch (caught) {
      const apiError = caught as ApiError;
      const result = friendly(apiError.status !== undefined ? apiError : { status: 0, message: String(caught) });
      setFeedback(result);
      setNotice(`${label}: 진행하지 못했습니다.`);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    try { await sync(); } catch (caught) { setFeedback(friendly(caught as ApiError)); }
  }

  // ---- 2 IMPORT -------------------------------------------------------
  async function loadHeroBaseline() {
    await guarded("hero 데모 기준 일정 연결", () => callApi<Dict>(`/api/projects/${projectId}/demo/hero-baseline`, { method: "POST" }), async () => {
      await refresh();
      setNotice("기준 일정을 연결했습니다. 일정을 확인한 뒤 '변경 불러오기'로 넘어가세요.");
    });
  }

  async function uploadImport(file: File | null, asChange = false) {
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    await guarded("Excel 미리보기", () => callApi<ImportPreview>(`/api/projects/${projectId}/imports`, { method: "POST", body }), (value) => {
      if (asChange) setChangePreview(value); else setPreview(value);
      setNotice(value.import_kind === "change" ? "수정 일정의 차이를 확인한 뒤 변경으로 등록하세요." : "시트 매핑과 작업 수를 확인한 뒤 기준 일정을 확정하세요.");
    });
  }

  async function confirmImport(target: ImportPreview | null, asChange = false) {
    if (!target?.import_id) return;
    await guarded(asChange ? "수정 일정 등록" : "기준 일정 확정", () =>
      callApi<Dict>(`/api/projects/${projectId}/imports/${target.import_id}/confirm`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
      }), async (value) => {
      if (asChange) {
        setChangePreview(null); setChangeFile(null);
        await refresh();
        setNotice(value.unchanged ? "기준 일정과 달라진 작업이 없습니다." : "수정 일정을 변경으로 등록했습니다. 아래 카드에서 해석을 확인하세요.");
      } else {
        setPreview(null); setSelectedFile(null);
        await refresh();
        setNotice("기준 일정을 확정했습니다. 일정을 확인한 뒤 '변경 불러오기'로 넘어가세요.");
      }
    });
  }

  // ---- 3 DETECT -------------------------------------------------------
  async function createEvent(payload: Dict) {
    const created = await guarded("변경 등록", () =>
      callApi<{ event_id: string; duplicate?: boolean }>(`/api/projects/${projectId}/events`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }));
    if (!created) return;
    setViewRunId("");
    // A preview run interprets the message (rules, or the LLM when enabled) before a person confirms it.
    try {
      await callApi(`/api/projects/${projectId}/analyses`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: created.event_id, preview_only: true }),
      });
    } catch { /* an unreviewable event still shows its card */ }
    await refresh();
    setNotice(created.duplicate ? "이미 받은 통보입니다. 기존 변경 카드를 확인하세요." : "통보를 등록했습니다. 아래 카드에서 해석된 작업과 날짜를 확인하세요.");
    go("changes", `review-${created.event_id}`);
  }

  async function createEventFromDemo() {
    const item = project.demo_events?.[selectedDemoIndex];
    if (!item) return;
    await createEvent({
      event_id: text(item.event_id, "hero-change"),
      corrects_event_id: item.corrects_event_id ? text(item.corrects_event_id) : undefined,
      content: text(item.body || item.content),
      channel: text(item.channel, "supplier_message"),
      source_label: text(item.source_label, "가상 협력사 메시지"),
      published_at: text(item.published_at),
      mode: text(item.mode, "SYNTHETIC"),
      data_origin: "SYNTHETIC",
      simulation_as_of: text(item.published_at),
    });
    setSelectedDemoIndex(-1);
  }

  async function createManualEvent() {
    if (!manualMessage.trim()) return;
    await createEvent({
      content: manualMessage,
      channel: "supplier_message",
      source_label: "직접 입력한 메시지",
      mode: text((project.project || {}).mode, "LIVE"),
      data_origin: text((project.project || {}).data_origin, "USER"),
      ...(project.project?.status_as_of ? { simulation_as_of: `${project.project.status_as_of}T09:00:00+02:00` } : {}),
    });
    setManualMessage("");
  }

  async function startAnalysis(eventId: string) {
    setViewRunId("");
    const queued = await guarded("영향 분석 시작", () =>
      callApi<{ run_id: string }>(`/api/projects/${projectId}/analyses`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: eventId, preview_only: false }),
      }));
    if (!queued) return;
    awaitingRef.current = { runId: queued.run_id, kind: "analysis" };
    setNotice("영향을 계산하고 있습니다. 끝나면 대응안 화면으로 이동합니다.");
    await refresh();
  }

  async function confirmEvent(eventId: string, payload: Dict = {}) {
    const reviewed = await guarded("변경 해석 확인", () =>
      callApi<Dict>(`/api/projects/${projectId}/events/${eventId}/review`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true, ...payload }),
      }));
    if (reviewed) await startAnalysis(eventId);
  }

  async function reviewExternalEvent(eventId: string, payload: Dict) {
    const reviewed = await guarded("외부 근거 적용 확인", () =>
      callApi<Dict>(`/api/projects/${projectId}/events/${eventId}/review`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }));
    if (!reviewed) return;
    if (payload.confirmed) await startAnalysis(eventId);
    else { await refresh(); setNotice("이 근거는 적용하지 않도록 보류했습니다."); }
  }

  async function uploadDocument() {
    if (!documentFile) return;
    const body = new FormData();
    body.append("file", documentFile);
    const uploaded = await guarded("근거 문서 등록", () => callApi<Dict>(`/api/projects/${projectId}/documents`, { method: "POST", body }), (value) => {
      setDocumentStatus((value.document as Dict) || null);
      setDocumentFile(null);
    });
    if (uploaded?.document_id) await pollDocument(String(uploaded.document_id));
  }

  async function pollDocument(documentId: string) {
    setNotice("문서를 읽고 있습니다. 끝나면 변경 카드가 추가됩니다.");
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        const result = await callApi<Dict>(`/api/projects/${projectId}/documents/${documentId}`);
        const document = (result.document as Dict)?.data as Dict | undefined;
        setDocumentStatus(document || null);
        const status = String(document?.status || "");
        if (status === "SUCCEEDED" || status === "FAILED") {
          await refresh();
          setNotice(status === "SUCCEEDED" ? "문서를 변경 카드로 등록했습니다. 해석을 확인하세요." : "문서를 읽지 못했습니다. 파일을 확인하거나 다시 처리하세요.");
          return;
        }
      } catch (caught) {
        setFeedback(friendly(caught as ApiError));
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    setNotice("문서 처리를 기다리고 있습니다. worker가 실행 중인지 확인하세요.");
  }

  async function retryDocument() {
    const documentId = text(documentStatus?.document_id, "");
    if (!documentId) return;
    const retried = await guarded("문서 다시 처리", () => callApi<Dict>(`/api/projects/${projectId}/documents/${documentId}/retry`, { method: "POST" }));
    if (retried?.document_id) await pollDocument(String(retried.document_id));
  }

  async function saveExternalWatch(plan: Dict) {
    await callApi<Dict>(`/api/projects/${projectId}/watch-plan`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(plan),
    });
    await refresh();
  }

  async function runScan() {
    await guarded("외부 변화 확인", () =>
      callApi<{ run_id: string }>(`/api/projects/${projectId}/scan`, { method: "POST", headers: { "Idempotency-Key": `scan-${Date.now()}` } }),
    () => setNotice("등록한 출처를 확인하고 있습니다. 새 근거는 변경 카드로 추가됩니다."));
  }

  // ---- 4 COMPARE / 5 APPROVE -----------------------------------------
  async function replan() {
    const runId = text(run?.run?.id, "");
    if (!runId || budget === "") return;
    const queued = await guarded("비용 한도 반영", () =>
      callApi<{ run_id: string }>(`/api/runs/${runId}/replan`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ budget_krw: budget, unavailable_option_ids: [] }),
      }));
    if (!queued) return;
    awaitingRef.current = { runId: queued.run_id, kind: "replan" };
    setViewRunId(queued.run_id);
    viewRunRef.current = queued.run_id;
    setNotice("비용 한도를 반영해 다시 계산하고 있습니다.");
    await refresh();
  }

  async function prepareScenario(scenarioId: string) {
    await guarded("확인 요청 만들기", () => callApi<Dict>(`/api/scenarios/${scenarioId}/prepare`, { method: "POST" }), async () => {
      await refresh();
      setNotice("조건별 확인 요청을 만들었습니다. 회신·확인 근거를 기록하세요.");
    });
  }

  async function acceptCondition(actionId: string) {
    await guarded("조건 확인 기록", () => callApi<Dict>(`/api/actions/${actionId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: "ACCEPTED", note: conditionNotes[actionId] }),
    }), async () => { await refresh(); setNotice("확인 근거를 기록했습니다."); });
  }

  async function approveScenario(scenario: Row) {
    const required = (scenario.data?.required_confirmations || []) as string[];
    const approved = await guarded("대응안 승인", () =>
      callApi<Dict>(`/api/scenarios/${scenario.id}/approve`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor: "프로젝트 운영팀", decision: "APPROVED", confirmed_conditions: required }),
      }));
    if (!approved) return;
    await refresh();
    setNotice("승인을 기록했습니다. 실행 화면에서 새 일정 버전을 확정하세요.");
    go("execute");
  }

  // ---- 6 EXECUTE ------------------------------------------------------
  async function commitScenario(scenarioId: string) {
    await guarded("새 일정 버전 확정", () => callApi<Dict>(`/api/scenarios/${scenarioId}/commit`, { method: "POST" }), async () => {
      await refresh();
      setNotice("새 일정 버전을 확정했습니다. Excel로 내려받을 수 있습니다.");
    });
  }

  async function downloadExport() {
    await guarded("Excel 다운로드", async () => {
      const response = await callApi<Response>(`/api/projects/${projectId}/export`);
      const blob = await response.blob();
      const match = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") || "");
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = match?.[1] || `replan-${projectId}.xlsx`;
      anchor.click();
      URL.revokeObjectURL(url);
      return { ok: true };
    }, () => setNotice("현재 일정 버전을 Excel로 내려받았습니다."));
  }

  // ---- Advanced settings ---------------------------------------------
  async function saveMailAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await guarded("메일 연결 정보 저장", () => callApi<Dict>(`/api/projects/${projectId}/mail-account`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...mailForm, enabled: true }),
    }), async () => { await refresh(); setNotice("메일 연결 정보만 저장했습니다. 자동 수신은 연결되어 있지 않습니다."); });
  }

  async function savePublicFeed(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await guarded("공개 피드 등록", () => callApi<Dict>(`/api/projects/${projectId}/public-feeds`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...feedForm, enabled: true }),
    }), async () => { await refresh(); setNotice("공개 피드를 등록했습니다."); });
  }

  async function saveSupplierCalendar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const unavailable_dates = supplierForm.unavailable_dates.split(",").map((value) => value.trim()).filter(Boolean);
    await guarded("협력사 달력 저장", () => callApi<Dict>(`/api/projects/${projectId}/supplier-calendars`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ supplier_id: supplierForm.supplier_id, label: supplierForm.label, unavailable_dates, timezone: supplierForm.timezone }),
    }), async () => {
      setSupplierForm((current) => ({ ...current, unavailable_dates: "" }));
      await refresh();
      setNotice("협력사 휴무일을 저장했습니다. 다음 영향 분석부터 작업 가능일에서 제외됩니다.");
    });
  }

  async function saveNotificationChannel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await guarded("알림 채널 저장", () => callApi<Dict>(`/api/projects/${projectId}/notification-channels`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...channelForm, enabled: true }),
    }), async () => { await refresh(); setNotice("알림 채널 설정을 초안으로 저장했습니다. 외부로 발송하지 않습니다."); });
  }

  async function markNotification(notificationId: string) {
    await guarded("알림 확인", () => callApi<Dict>(`/api/notifications/${notificationId}`, { method: "PATCH" }), refresh);
  }

  async function createSitePrep() {
    await guarded("현장 준비 체크리스트", () => callApi<Dict>(`/api/projects/${projectId}/site-prep`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template_id: "equipment_installation_v1" }),
    }), async () => { await refresh(); setNotice("현장 준비 체크리스트를 만들었습니다. 작업·일정과는 연결되지 않습니다."); });
  }

  async function loadProjects() {
    await guarded("프로젝트 목록 조회", () => callApi<{ projects: Dict[] }>("/api/projects"), (value) => { setProjects(value.projects); setNotice("프로젝트 목록을 불러왔습니다."); });
  }

  async function openRun(runId: string) {
    setViewRunId(runId);
    viewRunRef.current = runId;
    await refresh();
    go("scenarios");
  }

  // ---- Derived view state --------------------------------------------
  const progress = useMemo(() => deriveProgress(project), [project]);
  const tasks = useMemo(() => ((project.version?.data as Dict | undefined)?.tasks || []) as Dict[], [project.version]);
  const taskNames = useMemo(() => Object.fromEntries(tasks.map((task) => [text(task.task_id), text(task.name)])), [tasks]);
  const selectedScenario = useMemo(() => run?.scenarios?.find((item) => item.id === selectedScenarioId), [run?.scenarios, selectedScenarioId]);
  const scenarioSchedule = useMemo(() => ((selectedScenario?.data?.schedule || []) as Dict[]).reduce<Record<string, Dict>>((acc, item) => {
    acc[text(item.task_id)] = item;
    return acc;
  }, {}), [selectedScenario]);

  const projectData = project.project || {};
  const projectName = text(projectData.name, loaded ? "이름 없는 프로젝트" : "불러오는 중");
  const events = project.events || [];
  const eventCount = events.length;
  const analysisRuns = (project.runs || []).filter((item) => item.kind === "analysis" && !isPreview(item));
  const approvals = project.approvals || [];
  const committedVersions = (project.versions || []).filter((item) => item.status === "committed");
  const approvedScenarioId = text(progress.approval?.scenario_id, "");
  const approvedScenario = run?.scenarios?.find((item) => item.id === approvedScenarioId);
  const scenarioActions = (scenarioId: string) => (project.actions || []).filter((item) => item.scenario_id === scenarioId);
  const openScenarioActions = (project.actions || []).filter((item) => item.scenario_id && String(item.data?.state || "OPEN") === "OPEN").length;
  const nextInThisSection = progress.next.section === activeSection;
  const runIsPreview = isPreview(run?.run);
  const runPending = run?.run && !["succeeded", "failed"].includes(text(run.run.status));
  const demoEvents = project.demo_events || [];

  // ---- Step rail -----------------------------------------------------
  const stepRail = (
    <nav className="stage-rail" aria-label="진행 단계">
      <ol>
        {STAGES.map((stage, index) => {
          const state = progress.done[index] ? "done" : index === progress.current ? "current" : "todo";
          return (
            <li key={stage.code} className={`stage-step ${state}${stage.section === activeSection ? " here" : ""}`}>
              <a href={`#${stage.section}`} aria-current={state === "current" ? "step" : undefined}>
                <span className="stage-code">{index + 1} · {stage.code}</span>
                <b>{stage.label}</b>
                <small>{state === "done" ? "완료" : state === "current" ? "지금 할 단계" : "대기"}</small>
              </a>
            </li>
          );
        })}
      </ol>
    </nav>
  );

  const nextCallout = (label: string, section: SectionId, detail: string) => (
    <div className="next-callout" role="status">
      <div><span className="eyebrow">다음 단계</span><p>{detail}</p></div>
      <button onClick={() => go(section)}>{label} →</button>
    </div>
  );

  // ---- 1 BRIEF ---------------------------------------------------------
  const overviewView = (
    <section className="workspace-view overview-view" id="overview" aria-labelledby="overview-title">
      <div className="view-heading"><div><p className="eyebrow">1 · BRIEF</p><h2 id="overview-title">프로젝트 맥락</h2><p>이 프로젝트의 기준과 지금 해야 할 한 가지를 보여줍니다.</p></div><span className="view-context">{stageSummary(progress)}</span></div>
      <div className="overview-grid">
        <article className="focus-card focus-card-primary">
          <span>지금 할 일</span>
          <strong>{progress.next.label}</strong>
          <p>{progress.next.detail}</p>
          <button className="focus-action" onClick={() => go(progress.next.section)}>{progress.next.label} →</button>
        </article>
        <article className="focus-card context-card">
          <span>프로젝트 정보</span>
          <dl className="context-list">
            <div><dt>프로젝트</dt><dd>{projectName}</dd></div>
            <div><dt>현장</dt><dd>{text(projectData.region || projectData.site_region, "기준 일정 연결 후 표시")}</dd></div>
            <div><dt>목표 완료일</dt><dd>{text(projectData.target_finish, "미설정")}</dd></div>
            <div><dt>기준 시점</dt><dd>{text(projectData.status_as_of, "미설정")}</dd></div>
            <div><dt>작업 수</dt><dd>{project.version ? `${tasks.length}개` : "-"}</dd></div>
            <div><dt>데이터 성격</dt><dd>{projectData.data_origin === "SYNTHETIC" ? "합성 데모 데이터" : projectData.data_origin === "USER" ? "사용자 데이터" : text(projectData.data_origin, "-")}</dd></div>
          </dl>
        </article>
      </div>
      <div className="overview-counts" aria-label="기록 요약">
        <div><span>변경</span><b>{eventCount}</b></div>
        <div><span>정식 분석</span><b>{analysisRuns.length}</b></div>
        <div><span>승인</span><b>{approvals.length}</b></div>
        <div><span>확정 버전</span><b>{committedVersions.length}</b></div>
        <div><span>열린 확인 요청</span><b>{openScenarioActions}</b></div>
      </div>
    </section>
  );

  // ---- 2 IMPORT --------------------------------------------------------
  const importPreviewCard = (value: ImportPreview, asChange: boolean) => {
    const mapping = (value.mapping || {}) as Dict;
    const changed = (value.diff?.changed || []) as Dict[];
    return (
      <div className="import-preview">
        <div className="import-preview-body">
          <b>{value.import_kind === "change" ? "수정 일정 미리보기" : "기준 일정 미리보기"}</b>
          <span>작업 {value.tasks?.length || 0}개 · 대응안 {value.options?.length || 0}개 · 달력 {value.calendars?.length || 0}건 · 변경 이벤트 행 {value.events?.length || 0}건</span>
          {Object.keys(mapping).length > 0 && <details><summary>시트·열 매핑 보기</summary><pre>{JSON.stringify(mapping, null, 2)}</pre></details>}
          {Array.isArray(value.warnings) && value.warnings.length > 0 && <small className="warn">확인 필요: {value.warnings.join(" · ")}</small>}
          {!asChange && (value.events?.length || 0) > 0 && <small>이 파일의 변경 이벤트 행은 자동으로 등록하지 않습니다. 기준 일정 확정 후 변경 화면에서 메시지로 붙여 넣으세요.</small>}
          {value.import_kind === "change" && <small>달라진 작업 {changed.length}개{changed.slice(0, 5).map((item) => ` · ${text(item.task_id)}`).join("")}</small>}
        </div>
        <button onClick={() => confirmImport(value, asChange)} disabled={busy || (value.import_kind === "change" && !asChange)}>{asChange ? "변경으로 등록" : "기준 일정 확정"}</button>
      </div>
    );
  };

  const visibleTaskNote = tasks.some((task) => task.task_id === "T036") && tasks.length === 64
    ? "hero 데모는 조달·반입·설치·시운전 구간(T034~T057)만 표시합니다."
    : tasks.length > 20 ? `전체 ${tasks.length}개 중 앞 20개 작업만 표시합니다.` : "";

  const scheduleView = (
    <section className="workspace-view" id="schedule" aria-labelledby="schedule-title">
      <div className="view-heading"><div><p className="eyebrow">2 · IMPORT</p><h2 id="schedule-title">기준 일정</h2><p>변경을 비교할 기준 일정을 연결하고 작업 흐름을 확인합니다.</p></div><span className="view-context">{project.version ? (project.version.status === "committed" ? "확정 버전 사용 중" : "기준 버전 연결됨") : "연결 필요"}</span></div>
      {!project.version && loaded ? <div className="setup-card">
        <div><span className="setup-index">2</span><h3>기준 일정을 연결하세요.</h3><p>데모는 hero 합성 프로젝트(64개 작업)를 바로 연결합니다. 직접 만든 일정은 Excel을 올려 미리보기로 매핑을 확인한 뒤 확정합니다.</p></div>
        <div className="setup-actions">
          <button className={preview ? "secondary" : ""} onClick={loadHeroBaseline} disabled={busy}>hero 데모 기준 일정 연결</button>
          <span className="setup-or">또는</span>
          <label className="file-input-label">{selectedFile ? selectedFile.name : "Excel 파일 선택"}<input type="file" accept=".xlsx,.csv" aria-label="기준 일정 Excel" onChange={(event: ChangeEvent<HTMLInputElement>) => setSelectedFile(event.target.files?.[0] || null)} /></label>
          <button className="secondary" onClick={() => uploadImport(selectedFile)} disabled={busy || !selectedFile}>업로드·미리보기</button>
        </div>
      </div> : null}
      {preview && !project.version && importPreviewCard(preview, false)}
      {project.version ? <>
        {progress.current === 2 && !progress.focusEvent && nextCallout("변경 불러오기", "changes", "기준 일정이 준비되었습니다. 협력사 통보를 불러와 영향을 확인하세요.")}
        {progress.current > 2 && !nextInThisSection && nextCallout(progress.next.label, progress.next.section, progress.next.detail)}
        <div className="schedule-board">
          <div className="board-meta"><div><span className="eyebrow">{project.version.status === "committed" ? "COMMITTED VERSION" : "BASELINE"}</span><h3>{project.version.status === "committed" ? "확정 버전" : "기준 버전"} {shortId(project.version.id)} · {tasks.length}개 작업</h3><p>기준 시점 {text(projectData.status_as_of, "미설정")} · 완료 {tasks.filter((task) => task.status === "completed").length} · 진행 중 {tasks.filter((task) => task.status === "in_progress").length} · 예정 {tasks.filter((task) => task.status === "planned").length}{visibleTaskNote ? ` · ${visibleTaskNote}` : ""}</p>{selectedScenario && <p className="board-overlay">색이 다른 막대: 선택한 대응안 '{text(selectedScenario.data?.label)}'의 변경 일정</p>}</div></div>
          <Gantt tasks={tasks} scenarioSchedule={scenarioSchedule} />
        </div>
      </> : null}
    </section>
  );

  // ---- 3 DETECT --------------------------------------------------------
  const intakePrimary = !progress.focusEvent || progress.allDone;
  const changesView = (
    <section className="workspace-view" id="changes" aria-labelledby="changes-title">
      <div className="view-heading"><div><p className="eyebrow">3 · DETECT</p><h2 id="changes-title">변경 감지</h2><p>협력사 통보를 불러와 해석된 작업·날짜를 확인합니다. 확인하면 영향 분석이 바로 시작됩니다.</p></div><span className="view-context">{eventCount}건 기록</span></div>
      {!project.version ? (loaded ? nextCallout("기준 일정 연결", "schedule", "변경을 계산하려면 먼저 기준 일정이 필요합니다.") : null) : <>
        {progress.current > 3 && nextCallout(progress.next.label, progress.next.section, progress.next.detail)}
        <div className="changes-layout">
          <div className="change-intake-stack">
            <article className="focus-card">
              <div className="panel-heading"><div><h3>변경 불러오기</h3><p>{demoEvents.length ? "데모는 합성 통보를 고르거나, 실제 메시지를 붙여 넣으세요." : "협력사 메시지를 붙여 넣으세요. 합성 통보 목록은 hero 데모 기준 일정에서만 제공됩니다."}</p></div></div>
              {demoEvents.length > 0 && <>
                <label>합성 통보<select aria-label="합성 통보 선택" value={selectedDemoIndex} onChange={(event) => setSelectedDemoIndex(Number(event.target.value))}>
                  <option value={-1}>통보를 고르세요</option>
                  {demoEvents.map((item, index) => <option key={`${text(item.event_id)}-${index}`} value={index}>{text(item.event_id)} · {text(item.source_label)}{demoEvents.slice(0, index).some((earlier) => earlier.event_id === item.event_id) ? " (중복 수신)" : ""}{item.corrects_event_id ? ` (${text(item.corrects_event_id)} 정정)` : ""}</option>)}
                </select></label>
                <button className={intakePrimary ? "" : "secondary"} onClick={createEventFromDemo} disabled={selectedDemoIndex < 0 || busy}>합성 통보 불러오기</button>
              </>}
              <label>변경 메시지<textarea aria-label="협력사 변경 통보" value={manualMessage} onChange={(event) => setManualMessage(event.target.value)} rows={4} placeholder="예: T045 현장 설비 반입 완료일이 2026-12-05에서 2026-12-28로 변경됩니다." /></label>
              <button className={intakePrimary && !demoEvents.length ? "" : "secondary"} onClick={createManualEvent} disabled={busy || !manualMessage.trim()}>변경 메시지 등록</button>
            </article>
            <details className="focus-card more-inputs">
              <summary>다른 입력 방식 · 수정 일정 Excel, 근거 문서</summary>
              <div className="compact-form">
                <b>수정 일정 Excel</b>
                <small>같은 작업 ID의 날짜가 바뀐 Excel을 올리면 차이를 변경으로 등록합니다.</small>
                <input type="file" accept=".xlsx,.csv" aria-label="수정 일정 Excel" onChange={(event: ChangeEvent<HTMLInputElement>) => setChangeFile(event.target.files?.[0] || null)} />
                <button className="secondary" onClick={() => uploadImport(changeFile, true)} disabled={busy || !changeFile}>차이 미리보기</button>
                {changePreview && importPreviewCard(changePreview, true)}
              </div>
              <div className="compact-form">
                <b>근거 문서(PDF·TXT·MD·EML)</b>
                <small>worker가 텍스트를 읽어 검토용 변경 카드로 등록합니다.</small>
                <input type="file" accept=".pdf,.txt,.md,.eml" aria-label="근거 문서" onChange={(event: ChangeEvent<HTMLInputElement>) => setDocumentFile(event.target.files?.[0] || null)} />
                <button className="secondary" onClick={uploadDocument} disabled={busy || !documentFile}>문서 등록</button>
                {documentStatus && <div className="inline-status"><b>{text(documentStatus.filename)} · {text(documentStatus.status)}</b>{Boolean(documentStatus.error) && <span>{text(documentStatus.error)}</span>}{documentStatus.status === "FAILED" && <button className="text-button" onClick={retryDocument} disabled={busy}>다시 처리</button>}</div>}
              </div>
            </details>
          </div>
          <div className="focus-card event-focus-panel">
            <div className="panel-heading"><div><h3>변경 카드</h3><p>최신 변경이 위에 있습니다. 확인할 해석과 질문을 카드마다 보여줍니다.</p></div></div>
            {eventCount ? <div className="focus-event-list">{events.map((event) => (
              <ChangeCard key={text(event.id)} event={event} tasks={tasks} taskNames={taskNames} busy={busy}
                isFocus={event.id === progress.focusEvent?.id}
                runs={(project.runs || []).filter((item) => item.kind === "analysis" && item.event_id === event.id)}
                onConfirm={confirmEvent} onAnalyze={startAnalysis} onReviewExternal={reviewExternalEvent}
                onOpenResult={() => go("scenarios")} />
            ))}</div> : <div className="empty focus-empty">아직 변경이 없습니다. 왼쪽에서 통보를 불러오세요.</div>}
          </div>
        </div>
        <details className="focus-card watch-section">
          <summary><b>외부 변화 감시 (선택)</b> · 공휴일·기상·공식 공지 감시 계획 {project.watch_plan?.enabled ? "· 감시 중" : "· 꺼짐"}</summary>
          <p className="muted">기준 일정에서 제안된 감시 항목을 수락·제외한 뒤 활성화합니다. 새로 감지된 근거는 위의 변경 카드로 들어옵니다.</p>
          <ExternalWatch plan={project.watch_plan || {}} tasks={tasks} disabled={busy} onSave={saveExternalWatch} onScan={runScan} />
        </details>
      </>}
    </section>
  );

  // ---- 4 COMPARE / 5 APPROVE ------------------------------------------
  const impactDetail = selectedScenario ? <ImpactDetail data={selectedScenario.data || {}} taskNames={taskNames} /> : null;

  const approvalPanel = (() => {
    if (!selectedScenario) return <div className="focus-card scenario-empty"><h3>비교할 대응안을 고르세요.</h3><p>왼쪽 목록에서 대응안을 누르면 영향 내역과 승인 조건이 표시됩니다.</p></div>;
    const data = selectedScenario.data || {};
    const scenarioId = text(selectedScenario.id);
    const required = (data.required_confirmations || []) as string[];
    const actions = scenarioActions(scenarioId);
    const accepted = new Set(actions.filter((item) => ["ACCEPTED", "DONE"].includes(text(item.data?.state))).map((item) => text(item.data?.condition, "")));
    const allAccepted = required.every((condition) => accepted.has(condition));
    const blocked = !data.budget_met || (Array.isArray(data.violations) && data.violations.length > 0);
    const approvalHere = approvals.find((item) => item.scenario_id === scenarioId);
    const otherApproved = !approvalHere && approvedScenarioId && run?.scenarios?.some((item) => item.id === approvedScenarioId);
    let step: ReactNode;
    if (runIsPreview) step = <p className="panel-note">잠정 결과입니다. 변경 화면에서 해석을 확인하면 정식 분석이 실행되고 승인할 수 있습니다.</p>;
    else if (approvalHere) step = <div className="panel-note done"><b>승인됨 · {when(approvalHere.created_at)} · {text(approvalHere.actor)}</b><p>실행 화면에서 새 일정 버전을 확정하세요.</p><button onClick={() => go("execute")}>실행으로 이동 →</button></div>;
    else if (otherApproved) step = <p className="panel-note">이 변경에는 이미 '{text(run?.scenarios?.find((item) => item.id === approvedScenarioId)?.data?.label)}' 안이 승인되어 있습니다.</p>;
    else if (blocked) step = <p className="panel-note">예산 또는 제약 조건을 넘어 승인할 수 없습니다. 다른 안을 고르거나 아래에서 비용 한도를 조정하세요.</p>;
    else if (!actions.length) step = <div className="panel-note"><p>승인 전에 조건 {required.length}건의 확인 요청을 만듭니다.{required.length ? "" : " 조건이 없으면 검토 기록 1건이 만들어집니다."}</p><button onClick={() => prepareScenario(scenarioId)} disabled={busy}>확인 요청 만들기</button></div>;
    else step = <div className="panel-note"><p>{allAccepted ? "필요한 조건을 모두 확인했습니다." : `확인할 조건 ${required.filter((condition) => !accepted.has(condition)).length}건이 남았습니다.`}</p><button onClick={() => approveScenario(selectedScenario)} disabled={busy || !allAccepted}>이 대응안 승인</button></div>;
    return (
      <div className="focus-card approval-focus" id="approval">
        <div className="panel-heading"><div><span className="eyebrow">5 · APPROVE</span><h3>{text(data.label)}</h3><p>{scenarioScore(data)} · 완료 예정 {text(data.finish_date)} · 무대응 대비 회복 {text(data.recovery_days_vs_no_response, "-")}일 · 추가 비용 {costLabel(data)}</p></div></div>
        {required.length > 0 && <ul className="condition-list">{required.map((condition) => <li key={condition} className={accepted.has(condition) ? "ok" : ""}>{accepted.has(condition) ? "확인됨 · " : "확인 필요 · "}{condition}</li>)}</ul>}
        {!runIsPreview && actions.map((action) => (
          <div key={text(action.id)} className="condition-review">
            <p><b>{ACTION_STATE[text(action.data?.state)] || text(action.data?.state)}</b> · {text(action.data?.request)}{action.data?.due_at ? ` · 기한 ${text(action.data.due_at)}` : ""}</p>
            {text(action.data?.state) === "OPEN" && !approvalHere ? <>
              <label>회신·확인 근거<input value={conditionNotes[text(action.id)] || ""} onChange={(event) => setConditionNotes({ ...conditionNotes, [text(action.id)]: event.target.value })} placeholder="예: 협력사 회신 메일(9/2)로 확인" /></label>
              <button className="secondary" disabled={busy || !conditionNotes[text(action.id)]?.trim()} onClick={() => acceptCondition(text(action.id))}>확인 기록</button>
            </> : action.data?.note ? <small>근거: {text(action.data.note)}</small> : null}
          </div>
        ))}
        {step}
      </div>
    );
  })();

  const scenariosView = (
    <section className="workspace-view" id="scenarios" aria-labelledby="scenarios-title">
      <div className="view-heading"><div><p className="eyebrow">4 · COMPARE → 5 · APPROVE</p><h2 id="scenarios-title">대응안 비교와 승인</h2><p>일정·비용·조건을 같은 기준으로 비교하고, 조건을 확인한 안을 승인합니다.</p></div><span className="view-context">{run?.run ? runLabel(run.run) : "분석 대기"}</span></div>
      {!run?.run ? nextCallout(progress.next.label, progress.next.section, progress.current <= 3 ? progress.next.detail : "변경을 확인하면 영향 분석이 시작되고 결과가 여기에 표시됩니다.") : <>
        {viewRunId && <div className="run-pin"><span>이력에서 고른 실행 {shortId(viewRunId)}을 보고 있습니다.</span><button className="text-button" onClick={() => { setViewRunId(""); viewRunRef.current = ""; void refresh(); }}>최신 결과로</button></div>}
        {runPending ? <div className="analysis-receipt pending" role="status"><span className="eyebrow">ANALYSIS</span><h3>영향을 계산하고 있습니다…</h3><p>끝나면 이 화면에 대응안이 자동으로 표시됩니다.</p></div> : <div className="analysis-receipt">
          <span className="eyebrow">{runIsPreview ? "잠정 결과" : "4 · COMPARE · 분석 결과"}</span>
          <h3>{text(run.run.data?.summary, run.run.status === "failed" ? "분석 실패: 이력에서 실행 기록을 확인하세요." : "분석 결과")}</h3>
          <p>{runLabel(run.run)} · {text(events.find((item) => item.id === run.run?.event_id)?.data?.title, "변경")}</p>
          {!run.scenarios?.length && run.run.status === "succeeded" && <button onClick={() => go("changes", `review-${text(run.run?.event_id)}`)}>변경 화면에서 작업·날짜 지정 →</button>}
        </div>}
        {Boolean(run.scenarios?.length) && <div className="scenario-layout">
          <div className="focus-card"><div className="panel-heading"><div><h3>비교할 대응안</h3><p>무대응 대비 회복 일수와 추가 비용을 비교해 한 안을 고르세요.</p></div></div><ScenarioList scenarios={run.scenarios || []} selected={selectedScenarioId} approvedId={approvedScenarioId} onSelect={setSelectedScenarioId} /></div>
          <div className="scenario-side">{approvalPanel}{impactDetail}</div>
        </div>}
        <AgentDecisionPanel agent={run.run.data?.agent as Dict | undefined} eventContent={text(events.find((item) => item.id === run.run?.event_id)?.data?.content, "")} />
        <details className="focus-card fold">
          <summary>비용 한도로 다시 계산 (선택)</summary>
          <p className="muted">최초 분석은 비용 한도 없이 계산합니다. 한도를 넣으면 같은 변경으로 다시 계산해 예산 초과 안을 표시합니다.</p>
          <div className="scenario-toolbar"><label>대응안 비용 한도(원)<input className="budget" type="number" min="0" step="100000" value={budget} onChange={(event) => setBudget(event.target.value === "" ? "" : Number(event.target.value))} /></label><button className="secondary" onClick={replan} disabled={!run.run || runIsPreview || budget === "" || busy}>비용 한도 반영</button></div>
        </details>
        <OptionCatalog options={((project.version?.data as Dict | undefined)?.options || []) as Dict[]} />
      </>}
    </section>
  );

  // ---- 6 EXECUTE -------------------------------------------------------
  const committed = progress.committedVersion;
  const executeActions = approvedScenarioId ? scenarioActions(approvedScenarioId) : [];
  const executeView = (
    <section className="workspace-view" id="execute" aria-labelledby="execute-title">
      <div className="view-heading"><div><p className="eyebrow">6 · EXECUTE</p><h2 id="execute-title">일정 반영·실행</h2><p>승인한 대응안을 새 일정 버전으로 확정하고 Excel로 내보냅니다.</p></div><span className="view-context">{committed ? "새 버전 확정됨" : progress.approval ? "확정 대기" : "승인 대기"}</span></div>
      {!progress.approval ? nextCallout(progress.next.label, progress.next.section, progress.current < 4 ? progress.next.detail : "대응안을 승인하면 여기서 새 일정 버전을 확정할 수 있습니다.") : committed ? (
        <div className="execute-card done">
          <div><span className="eyebrow">새 일정 버전</span><h3>확정 버전 {shortId(committed.id)} · {when(committed.created_at)}</h3><p>반영한 대응안: {text(approvedScenario?.data?.label, shortId(approvedScenarioId))}{approvedScenario ? ` · 완료 예정 ${text(approvedScenario.data?.finish_date)} · 추가 비용 ${costLabel(approvedScenario.data || {})}` : ""}</p><p className="muted">Excel에는 기준 일정과 변경 일정, 종료 차이가 함께 들어갑니다.</p></div>
          <div className="execute-actions"><button onClick={downloadExport} disabled={busy}>Excel 다운로드</button><button className="secondary" onClick={() => go("schedule")}>일정에서 보기</button><button className="text-button" onClick={() => go("changes")}>새 변경 불러오기</button></div>
        </div>
      ) : (
        <div className="execute-card">
          <div><span className="eyebrow">승인된 대응안</span><h3>{text(approvedScenario?.data?.label, shortId(approvedScenarioId))}</h3><p>승인 {when(progress.approval.created_at)} · {text(progress.approval.actor)}{approvedScenario ? ` · 완료 예정 ${text(approvedScenario.data?.finish_date)} · 추가 비용 ${costLabel(approvedScenario.data || {})}` : ""}</p><p className="muted">확정하면 현재 기준 일정에서 새 일정 버전이 만들어지고, 원본 기준 버전은 그대로 남습니다.</p></div>
          <div className="execute-actions"><button onClick={() => commitScenario(approvedScenarioId)} disabled={busy}>새 일정 버전 확정</button></div>
        </div>
      )}
      {executeActions.length > 0 && <div className="focus-card"><div className="panel-heading"><div><h3>실행 항목</h3><p>승인한 대응안의 조건 확인 기록입니다.</p></div></div><div className="action-list">{executeActions.map((action) => <article key={text(action.id)} className="action-item"><span className={`action-state ${String(action.data?.state || "OPEN").toLowerCase()}`}>{ACTION_STATE[text(action.data?.state, "OPEN")] || text(action.data?.state)}</span><div><b>{text(action.data?.request, "확인 요청")}</b><small>{text(action.data?.owner, "프로젝트 운영팀")}{action.data?.due_at ? ` · 기한 ${text(action.data.due_at)}` : ""}{action.data?.note ? ` · 근거 ${text(action.data.note)}` : ""}</small></div></article>)}</div></div>}
      {project.version && <p className="muted quiet-row">현재 일정({project.version.status === "committed" ? "확정 버전" : "기준 버전"})을 그대로 내려받으려면 <button className="text-button" onClick={downloadExport} disabled={busy}>현재 일정 Excel</button></p>}

      <details className="advanced-settings">
        <summary>고급 설정 · 여정에 필요 없는 운영 연동 (일부 미연동)</summary>
        <p className="muted">아래 기능은 데모 흐름(1~6단계)에 필요하지 않습니다. '미연동' 표시는 실제 외부 시스템과 연결되지 않아 정보만 저장된다는 뜻입니다.</p>
        <div className="advanced-grid">
          <form className="compact-form" onSubmit={saveSupplierCalendar}>
            <b>협력사 휴무일 <em className="tag ok">분석에 반영</em></b>
            <small>저장한 휴무일은 다음 영향 분석부터 해당 협력사 작업의 작업 가능일에서 제외됩니다. 등록 {project.supplier_calendars?.length || 0}건</small>
            <input aria-label="공급사 ID" value={supplierForm.supplier_id} onChange={(event) => setSupplierForm({ ...supplierForm, supplier_id: event.target.value })} placeholder="협력사 ID (작업의 담당 협력사)" required />
            <input aria-label="공급사 캘린더 이름" value={supplierForm.label} onChange={(event) => setSupplierForm({ ...supplierForm, label: event.target.value })} placeholder="달력 이름" required />
            <input aria-label="공급사 휴무일" value={supplierForm.unavailable_dates} onChange={(event) => setSupplierForm({ ...supplierForm, unavailable_dates: event.target.value })} placeholder="휴무일: 2026-10-03, 2026-10-04" />
            <button className="secondary" type="submit" disabled={busy}>휴무일 저장</button>
          </form>
          <form className="compact-form" onSubmit={savePublicFeed}>
            <b>공개 피드 <em className="tag">저장만</em></b>
            <small>허용된 공식 RSS·Atom 주소를 등록합니다. 감시 대상 출처는 변경 화면의 '외부 변화 감시'에서 설정합니다. 등록 {project.public_feeds?.length || 0}건</small>
            <input aria-label="피드 이름" value={feedForm.label} onChange={(event) => setFeedForm({ ...feedForm, label: event.target.value })} placeholder="피드 이름" />
            <input aria-label="피드 URL" type="url" value={feedForm.url} onChange={(event) => setFeedForm({ ...feedForm, url: event.target.value })} placeholder="https://허용된-공식-출처" />
            <button className="secondary" type="submit" disabled={busy}>피드 등록</button>
          </form>
          <form className="compact-form" onSubmit={saveMailAccount}>
            <b>메일 연결 정보 <em className="tag off">미연동</em></b>
            <small>연결 정보만 저장합니다. 메일 자동 수신은 없으며 통보는 변경 화면에 붙여 넣습니다. 현재 {project.mail_account ? "저장됨" : "미설정"}</small>
            <div className="p1-inline-fields"><select aria-label="메일 제공자" value={mailForm.provider} onChange={(event) => setMailForm({ ...mailForm, provider: event.target.value })}><option value="imap">IMAP</option><option value="gmail">Gmail</option><option value="outlook">Outlook</option></select><input aria-label="메일 호스트" value={mailForm.host} onChange={(event) => setMailForm({ ...mailForm, host: event.target.value })} placeholder="imap.example.com" required /></div>
            <input aria-label="메일 사용자" value={mailForm.username} onChange={(event) => setMailForm({ ...mailForm, username: event.target.value })} placeholder="담당자 이메일" required />
            <button className="secondary" type="submit" disabled={busy}>연결 정보 저장</button>
          </form>
          <form className="compact-form" onSubmit={saveNotificationChannel}>
            <b>외부 알림 채널 <em className="tag off">미연동</em></b>
            <small>이메일·Slack·Webhook은 초안 설정으로만 저장하고 발송하지 않습니다. 인앱 알림은 이력 화면에 기록됩니다.</small>
            <div className="p1-inline-fields"><select aria-label="알림 채널 유형" value={channelForm.channel} onChange={(event) => setChannelForm({ ...channelForm, channel: event.target.value })}><option value="in_app">인앱</option><option value="email">이메일 초안</option><option value="slack">Slack 초안</option><option value="webhook">Webhook 초안</option></select><input aria-label="알림 대상" value={channelForm.target} onChange={(event) => setChannelForm({ ...channelForm, target: event.target.value })} placeholder="대상 또는 채널" /></div>
            <button className="secondary" type="submit" disabled={busy}>채널 저장</button>
          </form>
          <div className="compact-form">
            <b>현장 준비 템플릿 <em className="tag off">미연동</em></b>
            <small>설비 설치용 체크리스트(허가·적치장·양중 장비·안전 브리핑)를 만듭니다. 작업·일정과 연결되지 않습니다. 생성 {project.site_prep_items?.length || 0}건</small>
            {(project.site_prep_items || []).slice(0, 6).map((item) => <small className="p1-record" key={text(item.id)}>{text(item.data?.title || item.data?.label || item.data?.name, "체크리스트 항목")}</small>)}
            <button className="secondary" onClick={createSitePrep} disabled={busy}>현장 준비 체크리스트 만들기</button>
          </div>
          <div className="compact-form">
            <b>다른 프로젝트 열기</b>
            <small>프로젝트 목록 화면과 같은 기능입니다.</small>
            <button className="secondary" onClick={loadProjects} disabled={busy}>프로젝트 목록 불러오기</button>
            {projects.length > 0 && <select aria-label="프로젝트 선택" defaultValue="" onChange={(event) => { if (event.target.value) window.location.href = `/projects/${event.target.value}`; }}><option value="">프로젝트 선택</option>{projects.map((item) => <option key={text(item.id)} value={text(item.id)}>{text(item.name, text(item.id))}</option>)}</select>}
          </div>
        </div>
      </details>
    </section>
  );

  // ---- History ---------------------------------------------------------
  const historyView = (
    <section className="workspace-view" id="history" aria-labelledby="history-title">
      <div className="view-heading"><div><p className="eyebrow">HISTORY</p><h2 id="history-title">이력</h2><p>일정 버전, 승인, 분석 실행, 외부 출처 수집, 알림을 시간순으로 확인합니다.</p></div><span className="view-context">분석 {analysisRuns.length}회</span></div>
      <div className="history-layout">
        <div className="focus-card"><div className="panel-heading"><div><h3>일정 버전</h3><p>기준 버전과 승인안으로 확정한 버전입니다.</p></div></div><div className="history-list">{project.versions?.length ? project.versions.map((item) => <div className="history-row static" key={text(item.id)}><span>{item.status === "committed" ? "확정 버전" : "기준 버전"}</span><b>{shortId(item.id)}{item.id === project.version?.id ? " · 현재" : ""}</b><small>{when(item.created_at)}</small></div>) : <div className="empty focus-empty">아직 일정 버전이 없습니다.</div>}</div></div>
        <div className="focus-card"><div className="panel-heading"><div><h3>승인 기록</h3><p>승인한 대응안과 승인자입니다.</p></div></div><div className="history-list">{approvals.length ? approvals.map((item) => <div className="history-row static" key={text(item.id)}><span>승인</span><b>대응안 {shortId(item.scenario_id)} · {text(item.actor)}</b><small>{when(item.created_at)}</small></div>) : <div className="empty focus-empty">아직 승인 기록이 없습니다.</div>}</div></div>
        <div className="focus-card"><div className="panel-heading"><div><h3>분석 실행</h3><p>누르면 그 실행의 대응안을 대응안 화면에서 엽니다.</p></div></div><div className="history-list">{project.runs?.length ? project.runs.map((item) => item.kind === "analysis" ? <button key={text(item.id)} className="history-row" onClick={() => openRun(text(item.id))}><span>{runLabel(item)}</span><b>{text(events.find((event) => event.id === item.event_id)?.data?.title, "변경").slice(0, 60)}</b><small>{when(item.created_at)}</small></button> : <div key={text(item.id)} className="history-row static"><span>{runLabel(item)}</span><b>{RUN_KIND[text(item.kind)] || text(item.kind)}</b><small>{when(item.created_at)}</small></div>) : <div className="empty focus-empty">아직 분석 실행 기록이 없습니다.</div>}</div></div>
        <div className="focus-card"><div className="panel-heading"><div><h3>외부 출처 수집</h3><p>마지막 수집 상태를 사실 그대로 표시합니다.</p></div></div><div className="history-list">{project.source_snapshots?.length ? project.source_snapshots.slice(0, 8).map((source) => <div className="history-row static" key={text(source.id)}><span>{text(source.source_id, "출처")}</span><b>{source.status === "ok" ? "수집 성공" : "수집 실패 · 위험 여부 확인 불가"}</b><small>{when(source.fetched_at)}</small></div>) : <div className="empty focus-empty">수집된 외부 출처 기록이 없습니다.</div>}</div></div>
        <div className="focus-card"><div className="panel-heading"><div><h3>알림 기록</h3><p>인앱 알림입니다. 외부로 발송하지 않습니다.</p></div></div><div className="history-list">{project.notifications?.length ? project.notifications.slice(0, 10).map((notification) => { const data = notification.data || {}; return <div className="history-row static" key={text(notification.id)}><span>{data.status === "UNREAD" ? "새 알림" : "확인함"}</span><b>{text(data.title, "알림")}<small>{text(data.message, text(data.body, ""))}</small></b>{data.status === "UNREAD" ? <button className="text-button" onClick={() => markNotification(text(notification.id))} disabled={busy}>확인</button> : <small>{when(notification.created_at)}</small>}</div>; }) : <div className="empty focus-empty">새 알림이 생기면 여기에 표시됩니다.</div>}</div></div>
      </div>
    </section>
  );

  const statusLine = progress.allDone ? "모든 단계 완료" : `${progress.current + 1}단계 · ${STAGES[progress.current].label}`;

  return (
    <main className="human-workspace" data-section={activeSection}>
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
      <section className="hero human-hero" id="project-header">
        <div className="hero-copy">
          <p className="eyebrow">PROJECT</p>
          <div className="project-title-row"><h1>{projectName}</h1></div>
          <p className="subtitle" role="status">{notice}</p>
          <div className="hero-context"><span className="context-marker" aria-hidden="true" /><span>프로젝트 ID {text(projectId, "미지정")}</span><span className="context-slash">/</span><span>{eventCount ? `변경 ${eventCount}건` : "변경 없음"}</span></div>
        </div>
        <div className="status-card human-status-card">
          <span className="status-kicker">WORKSPACE STATUS</span>
          <strong>{loaded ? statusLine : "불러오는 중"}</strong>
          <small>{loaded ? (progress.allDone ? "새 변경을 불러오면 3단계부터 다시 진행합니다." : `다음: ${progress.next.label}`) : ""}</small>
          {loaded && !nextInThisSection ? <a href={`#${progress.next.section}`} className="hero-action">{progress.next.label}<span aria-hidden="true">↗</span></a> : <span className="hero-here">{loaded ? "이 화면에서 진행하세요" : ""}</span>}
          <div className="hero-scene" aria-hidden="true">
            <Image className="workspace-illustration workspace-status-illustration" src="/images/workspace/workspace-status-flat.png" alt="" fill sizes="330px" priority />
          </div>
        </div>
      </section>

      {stepRail}

      {feedback && (
        <aside className={feedback.kind === "error" ? "error" : "guide-notice"} role="alert">
          <b>{feedback.kind === "error" ? "연결 확인 필요" : "안내"}</b>
          <span>{feedback.text}</span>
          <button className="text-button" onClick={() => setFeedback(null)}>닫기</button>
        </aside>
      )}

      {activeSection === "overview" && overviewView}
      {activeSection === "schedule" && scheduleView}
      {activeSection === "changes" && changesView}
      {activeSection === "scenarios" && scenariosView}
      {activeSection === "execute" && executeView}
      {activeSection === "history" && historyView}
    </main>
  );
}

function firstIsoDate(value: string) {
  return /(20\d{2}-\d{2}-\d{2})/.exec(value)?.[1] || "";
}

function ChangeCard({ event, tasks, taskNames, busy, isFocus, runs, onConfirm, onAnalyze, onReviewExternal, onOpenResult }: {
  event: Row; tasks: Dict[]; taskNames: Record<string, string>; busy: boolean; isFocus: boolean; runs: Row[];
  onConfirm: (eventId: string, payload?: Dict) => Promise<void>; onAnalyze: (eventId: string) => Promise<void>;
  onReviewExternal: (eventId: string, payload: Dict) => Promise<void>; onOpenResult: () => void;
}) {
  const data = event.data || {};
  const eventId = text(event.id, "");
  const status = text(data.review_status, "PENDING");
  const patch = (data.patch || {}) as Record<string, Record<string, unknown>>;
  const candidates = (Array.isArray(data.task_candidates) ? data.task_candidates : []) as Dict[];
  const facts = (Array.isArray(data.extracted_facts) ? data.extracted_facts : []) as Dict[];
  const [ids, setIds] = useState<string[]>(((data.related_task_ids || []) as string[]).slice(0, 3));
  const [kind, setKind] = useState("estimated_finish");
  const [day, setDay] = useState(firstIsoDate(text(data.content, "")));
  const suggested = ((data.related_task_ids || []) as string[]).join(",");
  // LLM interpretation may add task candidates after the card first renders.
  useEffect(() => { if (suggested) setIds((current) => current.length ? current : suggested.split(",").slice(0, 3)); }, [suggested]);
  const inactive = ["SUPERSEDED", "REJECTED"].includes(status);
  const interpreting = runs.some((item) => isPreview(item) && !["succeeded", "failed"].includes(text(item.status)));
  const pending = runs.some((item) => !isPreview(item) && !["succeeded", "failed"].includes(text(item.status)));
  const analysed = runs.some((item) => !isPreview(item) && item.status === "succeeded" && runScenarioCount(item) > 0);
  const noImpact = data.classification_status === "NO_SCHEDULE_IMPACT";
  const confirmed = status === "CONFIRMED" && hasPatch(data);
  const needsTask = !data.evidence && !hasPatch(data) && !noImpact && !inactive;
  const chip = inactive ? (status === "SUPERSEDED" ? "새 통보로 대체됨" : "보류됨")
    : interpreting ? "해석 중" : noImpact ? "일정 영향 없음" : needsTask ? "작업·날짜 지정 필요" : confirmed ? (analysed ? "분석 완료" : pending ? "분석 중" : "해석 확인됨") : "해석 확인 필요";
  const primary = isFocus ? "" : "secondary";

  return (
    <article id={`review-${eventId}`} className={`focus-event-card${isFocus ? " is-focus" : ""}${inactive ? " is-inactive" : ""}`}>
      <div className="focus-event-topline"><span className={`status-chip${needsTask || (!confirmed && !inactive && !noImpact) ? " warn" : ""}`}>{chip}</span><small>{text(data.source_label, "입력")} · {data.published_at ? when(data.published_at) : when(data.received_at)} · {data.data_origin === "SYNTHETIC" ? "합성" : text(data.data_origin, "")}</small></div>
      <blockquote className="event-quote">{text(data.content || data.title)}</blockquote>
      {hasPatch(data) && !data.evidence && <div className="event-interpretation"><b>해석된 변경</b><ul>{Object.entries(patch).flatMap(([patchKind, values]) => Object.entries(values || {}).map(([taskId, value]) => <li key={`${patchKind}-${taskId}`}><span>{taskId}</span> {taskNames[taskId] || ""} · {PATCH_KIND[patchKind] || patchKind} {Array.isArray(value) ? value.join(", ") : text(value)}</li>))}</ul>{facts.some((fact) => fact.confidence === "suggested") && <small>규칙이 원문에서 추출한 제안입니다. 원문과 같은지 확인하세요.</small>}</div>}
      {Array.isArray(data.verification_required) && data.verification_required.length > 0 && <p className="event-question">추가 확인: {(data.verification_required as string[]).join(" · ")}</p>}
      {Array.isArray(data.missing_fields) && data.missing_fields.length > 0 && !confirmed && <p className="event-question">확인 질문: {(data.missing_fields as string[]).join(" · ")}</p>}
      {candidates.length > 0 && <div className="event-interpretation"><b>에이전트가 찾은 작업 후보</b><ul>{candidates.map((candidate, index) => <li key={`${text(candidate.task_id)}-${index}`}><span>{text(candidate.task_id)}</span> {taskNames[text(candidate.task_id)] || ""}{candidate.quote ? <q>{text(candidate.quote)}</q> : null}{candidate.reason ? <small> {text(candidate.reason)}</small> : null}</li>)}</ul></div>}
      {Array.isArray(data.risk_signal_evidence) && data.risk_signal_evidence.length > 0 && <details className="event-evidence"><summary>유사 위험 실제 사례 {(data.risk_signal_evidence as Dict[]).length}건 · 지연 일수는 계산에 쓰지 않음</summary>{(data.risk_signal_evidence as Dict[]).map((caseRow) => <p key={text(caseRow.risk_id)}><a href={text(caseRow.source_url)} target="_blank" rel="noreferrer">{text(caseRow.title)}</a><small>발행 {text(caseRow.published_date)} · {text(caseRow.country)} · {text(caseRow.risk_type)}{caseRow.temporal_status === "POST_AS_OF_REFERENCE" ? " · 통보 이후 발행: 후향적 참고만 가능" : ""}</small></p>)}</details>}

      {data.evidence ? <EvidenceReview event={data} tasks={tasks} disabled={busy} onReview={(payload) => onReviewExternal(eventId, payload)} onAnalyze={() => onAnalyze(eventId)} />
        : inactive ? <p className="muted">{status === "SUPERSEDED" ? "정정 통보로 대체되어 계산하지 않습니다." : "보류된 변경입니다."}</p>
        : interpreting ? <p className="muted">통보를 해석하고 있습니다…</p>
        : noImpact ? <p className="muted">일정에 영향을 주는 변경이 아니어서 계산하지 않습니다.</p>
        : needsTask ? <div className="task-patch-form">
            <b>영향 작업과 날짜 지정</b>
            <small>통보에서 작업 ID를 찾지 못했습니다. 원문을 보고 기준 일정의 작업을 고르세요. 이 지정이 확인된 해석이 됩니다.</small>
            <label>영향 작업<select multiple aria-label="영향 작업 선택" value={ids} onChange={(input) => setIds(Array.from(input.target.selectedOptions, (option) => option.value))}>{tasks.filter((task) => task.status !== "completed").map((task) => <option key={text(task.task_id)} value={text(task.task_id)}>{text(task.task_id)} · {text(task.name)}</option>)}</select></label>
            <label>변경 내용<select aria-label="변경 종류" value={kind} onChange={(input) => setKind(input.target.value)}><option value="estimated_finish">완료 예정일 변경</option><option value="not_before">착수 가능일 변경</option><option value="blocked_dates">작업 불가일</option></select></label>
            <label>날짜<input type="date" aria-label="변경 날짜" value={day} onChange={(input) => setDay(input.target.value)} /></label>
            <button className={primary} disabled={busy || !ids.length || !day} onClick={() => onConfirm(eventId, { related_task_ids: ids, review_note: "작업·날짜를 사람이 지정함", patch: { [kind]: Object.fromEntries(ids.map((id) => [id, kind === "blocked_dates" ? [day] : day])) } })}>지정 후 영향 분석</button>
          </div>
        : !confirmed ? <button className={primary} onClick={() => onConfirm(eventId)} disabled={busy}>해석 확인 후 영향 분석</button>
        : analysed ? <button className={isFocus ? "secondary" : "text-button"} onClick={onOpenResult}>대응안 보기 →</button>
        : pending ? <p className="muted">영향을 계산하고 있습니다. 끝나면 대응안 화면으로 이동합니다.</p>
        : <button className={primary} onClick={() => onAnalyze(eventId)} disabled={busy}>영향 분석 시작</button>}
    </article>
  );
}

function ImpactDetail({ data, taskNames }: { data: Dict; taskNames: Record<string, string> }) {
  const changed = (data.changed_tasks || []) as Dict[];
  const breakdown = ((data.delay_breakdown || []) as Dict[]).filter((row) => Number(row.supplier_delay_days) || Number(row.external_additional_days));
  const constraints = (data.external_constraints || []) as Dict[];
  const seasonal = (data.seasonal_risks || []) as Dict[];
  return (
    <div className="focus-card impact-detail" aria-live="polite">
      <span className="eyebrow">4 · COMPARE · 영향 내역</span>
      <h3>기준 완료 {text(data.baseline_finish)} → 예상 완료 {text(data.finish_date)}</h3>
      <p>완료일 변화 {text(data.finish_shift_days)}일 · 영향 작업 {changed.length}개{data.provisional ? " · 잠정 계산" : ""}</p>
      {data.supplier_finish_shift_days !== undefined && <ul className="impact-split">
        <li><b>통보에 의한 지연 {text(data.supplier_finish_shift_days)}일</b><small>통보만 적용한 완료일 {text(data.supplier_finish_date)}</small></li>
        <li><b>밀린 기간의 외부 제약 추가 {text(data.external_additional_shift_days)}일</b><small>새로 걸린 공휴일·예보 {constraints.length}건 · 현장 적용 확인 필요</small></li>
      </ul>}
      {breakdown.length > 0 && <details><summary>작업별 지연 내역 {breakdown.length}건</summary><ul>{breakdown.map((row) => <li key={text(row.task_id)}>{text(row.task_id)} {taskNames[text(row.task_id)] || ""}: 완료 {text(row.before_finish)} → 통보 {text(row.supplier_finish)} ({text(row.supplier_delay_days)}일) → 외부 제약 {text(row.final_finish)} (추가 {text(row.external_additional_days)}일)</li>)}</ul></details>}
      {constraints.length > 0 && <details><summary>새로 걸린 공휴일·예보 {constraints.length}건</summary><ul>{constraints.map((row, index) => <li key={`${text(row.task_id)}-${text(row.date)}-${index}`}>{text(row.task_id)} · {text(row.date)} · {text(row.name, row.kind === "public_holiday" ? "공휴일" : "기상 예보 위험")}</li>)}</ul></details>}
      {seasonal.length > 0 && <details><summary>조건부 계절 위험 {seasonal.length}건</summary>{seasonal.map((row, index) => <p key={`seasonal-${index}`}>{text(row.task_id)} {text(row.start)}~{text(row.finish)} · {text(row.reason)}</p>)}</details>}
      {((data.included_events || []) as Dict[]).length > 0 && <p>함께 반영한 외부 변화: {((data.included_events || []) as Dict[]).map((item) => text(item.title)).join(" · ")}</p>}
      {changed.length > 0 && <details><summary>영향 작업 {changed.length}개</summary><ul>{changed.map((task) => <li key={text(task.task_id)}>{text(task.task_id)} · {text(task.name)} · {task.direct ? "직접 영향" : "후속 영향"} · {text(task.before_finish)} → {text(task.after_finish)}</li>)}</ul></details>}
    </div>
  );
}

function agentSummary(agent: Dict) {
  const value = agent.summary;
  if (typeof value === "string" && !value.trim().startsWith("{'")) return value || "판단 기록이 없습니다.";
  const result = ((Array.isArray(agent.tool_log) ? agent.tool_log : []) as Dict[])
    .slice().reverse().find((entry) => entry.tool === "recheck_shifted_schedule" && entry.status === "ok")?.result as Dict | undefined;
  if (result?.finish_date) return `통보의 일정 영향을 검토했습니다. 계산된 완료 예정일은 ${text(result.finish_date)}이며, ${result.target_met ? "목표일을 충족합니다." : "목표일을 충족하지 못합니다."}`;
  return "통보 내용을 검토했습니다. 일정 계산에 필요한 조건을 추가로 확인해야 합니다.";
}

function days(value: unknown, label: string) {
  return value === undefined || value === null || value === "" ? "" : `${label} ${text(value)}일`;
}

function toolResultSummary(entry: Dict) {
  if (entry.status === "error") return `도구 오류: ${text(entry.error)}`;
  const result = (entry.result || {}) as Dict;
  const tool = text(entry.tool);
  if (result.status === "NEEDS_INPUT") return `추가 확인 필요: ${text(result.reason)}`;
  if (tool === "get_project_context") return `프로젝트 작업 ${Array.isArray(result.tasks) ? result.tasks.length : 0}개를 확인했습니다.`;
  if (tool === "find_task_candidates") return `관련 작업 후보: ${Array.isArray(result.task_ids) ? result.task_ids.join(", ") : text(result.status)}`;
  if (tool === "list_response_options") return `등록된 대응안 ${Array.isArray(result.options) ? result.options.length : 0}개를 확인했습니다.`;
  if (tool === "simulate_schedule") return `계산 완료일 ${text(result.finish_date)} · 추가 비용 ${money(result.extra_cost_krw)}`;
  if (tool === "recheck_shifted_schedule") {
    const constraints = (Array.isArray(result.external_constraints) ? result.external_constraints : []) as Dict[];
    const holidays = constraints.filter((item) => item.kind === "public_holiday").map((item) => `${text(item.date)} (${text(item.task_id)})`);
    return [days(result.supplier_finish_shift_days, "통보 지연"), days(result.external_additional_shift_days, "외부 제약 추가"), `계산 완료일 ${text(result.finish_date)}`, holidays.length ? `새로 걸린 공휴일: ${holidays.join(", ")}` : ""].filter(Boolean).join(" · ");
  }
  if (tool === "simulate_regulatory_condition") return result.finish_date ? `규제 적용 시 조건부 완료일 ${text(result.finish_date)}` : `조건부 계산 보류: ${text(result.reason)}`;
  if (tool === "search_risk_signals" || tool === "search_public_sources") return `찾은 근거 ${Array.isArray(result.results) ? result.results.length : 0}건`;
  if (tool === "prepare_change_package") return "검토용 초안만 준비했습니다. 확정·발송은 하지 않았습니다.";
  if (result.status) return `도구 상태: ${text(result.status)}`;
  return "도구 결과를 받았습니다. 세부 응답을 펼쳐 확인할 수 있습니다.";
}

// Older runs stored rule calculations as "rules_fallback" tool calls; they are not agent reasoning either.
function isRulesOnly(agent: Dict) {
  const log = (Array.isArray(agent.tool_log) ? agent.tool_log : []) as Dict[];
  return agent.mode === "rules_only" || (log.length > 0 && log.every((entry) => entry.source === "rules_fallback"))
    || agent.summary === "규칙 기반 분석";
}

function explanationText(item: unknown) {
  if (typeof item === "string") return item;
  const row = (item || {}) as Dict;
  const ids = Array.isArray(row.option_ids) ? (row.option_ids as unknown[]).map((id) => text(id)) : [];
  const body = text(row.text ?? row.explanation ?? row.tradeoff, "");
  return ids.length ? `${ids.join(" + ")} · ${body}` : body;
}

function AgentDecisionPanel({ agent, eventContent }: { agent?: Dict; eventContent: string }) {
  if (!agent) return null;
  if (isRulesOnly(agent)) {
    return <section className="agent-decision rules-only" aria-label="분석 방식">
      <span className="eyebrow">RULES · 규칙과 계산기</span>
      <p>{text(agent.summary, "에이전트 없이 규칙과 계산기로만 분석했습니다.")}</p>
    </section>;
  }
  const interpretation = agent.mode === "llm_interpretation";
  const log = (Array.isArray(agent.tool_log) ? agent.tool_log : []) as Dict[];
  const regulation = agent.regulatory_assessment as Dict | undefined;
  const conditional = agent.conditional_scenario as Dict | undefined;
  const email = agent.email_draft as Dict | undefined;
  const explanations = (Array.isArray(agent.option_explanations) ? agent.option_explanations : []) as unknown[];
  return <section className="agent-decision" aria-label="에이전트 판단 과정">
    <span className="eyebrow">{interpretation ? "LLM INTERPRETATION · 원문 해석" : "AGENT REASONING · 설명과 초안"}</span>
    <h3>{interpretation ? "에이전트 해석" : "에이전트 판단 과정"}</h3>
    <p>{agentSummary(agent)}</p>
    {agent.stop_reason ? <p className="agent-stop">멈춘 이유: {text(agent.stop_reason)}</p> : null}
    {Array.isArray(agent.unresolved_items) && agent.unresolved_items.length ? <p className="agent-stop">확인 질문: {(agent.unresolved_items as unknown[]).map((item) => text(item)).join(" · ")}</p> : null}
    {regulation && /(규제|인허가|허가|법령|법규|규정|regulation|regulatory|permit|license)/i.test(eventContent) ? <div className="agent-note"><h4>규제·인허가 적용 가능성: {text(regulation.likelihood, "불확실")}</h4>{regulation.reason ? <p>{text(regulation.reason)}</p> : null}{regulation.human_check ? <p>사람 확인: {text(regulation.human_check)}</p> : null}{Array.isArray(regulation.evidence_risk_ids) && regulation.evidence_risk_ids.length ? <small>당시 이용 가능한 L2 근거: {(regulation.evidence_risk_ids as unknown[]).join(", ")}</small> : null}{Array.isArray(regulation.reference_only_risk_ids) && regulation.reference_only_risk_ids.length ? <small>통보 이후 발행된 참고 사례: {(regulation.reference_only_risk_ids as unknown[]).join(", ")}</small> : null}</div> : null}
    {conditional ? <div className="agent-note"><h4>규제 적용 시 조건부 일정 · 계산 도구</h4><p>{conditional.finish_date ? `계산된 완료 예정일 ${text(conditional.finish_date)}` : text(conditional.reason, "추가 일정 입력이 필요합니다.")}</p><small>적용 확인 전에는 확정 일정에 반영되지 않습니다.</small></div> : null}
    {explanations.length ? <div className="agent-note"><h4>대응안별 설명</h4>{explanations.map((item, index) => <p key={index}>{explanationText(item)}</p>)}</div> : null}
    {email ? <div className="agent-note email-draft"><h4>협력사 협의 메일 · 발송 전 초안</h4><p><b>{text(email.subject, "협의 요청")}</b></p><p className="draft-body">{text(email.body)}</p></div> : null}
    {log.length > 0 && <details><summary>도구 호출 기록 {log.length}건</summary><ol className="agent-tool-list">{log.map((entry, index) => <li key={`${text(entry.tool)}-${index}`}><b>{text(entry.tool)}</b> · {text(entry.status)}<p className="agent-tool-result">{toolResultSummary(entry)}</p><details><summary>전체 도구 응답 보기</summary><pre>{JSON.stringify(entry.result ?? entry.error ?? {}, null, 2)}</pre></details></li>)}</ol></details>}
  </section>;
}

function OptionCatalog({ options }: { options: Dict[] }) {
  if (!options.length) return null;
  return <details className="focus-card option-catalog fold"><summary>등록된 대응안 카탈로그 {options.length}개</summary><p>합성 가정의 단축 일수는 보장된 회복 일수가 아닙니다. 실제 회복 일수는 위 계산 결과로 비교하세요.</p><div className="option-catalog-grid">{options.map((option) => <article key={text(option.option_id)}><b>{text(option.name)}</b><small>적용 작업 {(option.target_ids as string[] || []).join(", ")} · 최대 가정 {text(option.reduction_workdays)}작업일 단축 · 추가 비용 {money(option.extra_cost_krw)} ({text(option.currency, "KRW")})</small><p>{text(option.conditions)}</p><small>결정 기한 {text(option.decision_deadline)} · {text(option.approval_state)} · {text(option.data_origin)}</small></article>)}</div></details>;
}

function ScenarioList({ scenarios, selected, approvedId, onSelect }: { scenarios: Row[]; selected: string; approvedId: string; onSelect: (id: string) => void }) {
  return (
    <div className="scenario-list">
      {scenarios.map((scenario) => {
        const data = scenario.data || {};
        return (
          <button key={text(scenario.id)} className={selected === scenario.id ? "scenario active" : "scenario"} onClick={() => onSelect(text(scenario.id))} aria-pressed={selected === scenario.id}>
            <span>{text(data.label)}{scenario.id === approvedId ? " · 승인됨" : ""}</span>
            <b>{text(data.finish_date)}</b>
            <small>{scenarioScore(data)} · 무대응 대비 회복 {text(data.recovery_days_vs_no_response, "-")}일 · {costLabel(data)}</small>
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

  if (!tasks.length) return <div className="empty">기준 일정을 확정하면 작업 일정이 표시됩니다.</div>;

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
        const changed = Boolean(scenario) && (text(scenario?.planned_finish) !== original.finish || text(scenario?.planned_start) !== original.start);
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
