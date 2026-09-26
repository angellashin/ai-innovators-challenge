// One ordered model for the sidebar, the workspace sections and the step rail.
// Step state is derived only from stored records returned by GET /api/projects/{id}.

type Dict = Record<string, unknown>;
type Row = Dict & { id?: string; data?: Dict; status?: string; kind?: string; event_id?: string; created_at?: string };

export const SECTIONS = [
  { id: "overview", label: "개요" },
  { id: "schedule", label: "일정" },
  { id: "changes", label: "변경" },
  { id: "scenarios", label: "대응안" },
  { id: "execute", label: "실행" },
  { id: "history", label: "이력" },
] as const;

export type SectionId = (typeof SECTIONS)[number]["id"];

export const STAGES = [
  { code: "BRIEF", label: "프로젝트 맥락", section: "overview" },
  { code: "IMPORT", label: "기준 일정", section: "schedule" },
  { code: "DETECT", label: "변경 감지", section: "changes" },
  { code: "COMPARE", label: "대응안 비교", section: "scenarios" },
  { code: "APPROVE", label: "조건 승인", section: "scenarios" },
  { code: "EXECUTE", label: "일정 반영·실행", section: "execute" },
] as const satisfies ReadonlyArray<{ code: string; label: string; section: SectionId }>;

// Older links used #actions for the execution screen.
const SECTION_ALIASES: Record<string, SectionId> = { actions: "execute" };

export function sectionFromHash(hash: string): SectionId {
  const raw = hash.replace("#", "");
  const id = SECTION_ALIASES[raw] || raw;
  return SECTIONS.some((item) => item.id === id) ? (id as SectionId) : "overview";
}

export type ProjectRecords = {
  project?: Dict;
  version?: Row | null;
  events?: Row[];
  runs?: Row[];
  approvals?: Row[];
  versions?: Row[];
};

export type NextAction = { label: string; section: SectionId; detail: string };

export type Progress = {
  done: boolean[];
  current: number; // index into STAGES; STAGES.length when every step is done
  allDone: boolean;
  focusEvent?: Row;
  analysisRun?: Row;
  pendingAnalysis?: Row;
  interpreting?: Row;
  approval?: Row;
  committedVersion?: Row;
  next: NextAction;
};

const INACTIVE = new Set(["SUPERSEDED", "REJECTED"]);
const FINAL = new Set(["succeeded", "failed"]);

export function hasPatch(data?: Dict) {
  return Object.keys((data?.patch || {}) as Dict).length > 0;
}

export function isPreview(run?: Row) {
  return Boolean((run?.data as Dict | undefined)?.preview_only);
}

export function runScenarioCount(run?: Row) {
  const ids = (run?.data as Dict | undefined)?.scenario_ids;
  return Array.isArray(ids) ? ids.length : 0;
}

export function deriveProgress(state: ProjectRecords): Progress {
  const events = (state.events || []).filter((item) => !INACTIVE.has(String(item.data?.review_status)));
  // Follow the change the person last analysed, unless a newer change arrived since.
  const userRuns = (state.runs || []).filter((item) => item.kind === "analysis" && !isPreview(item)
    && !(item.data as Dict | undefined)?.auto_detected)
    .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  const actedEvent = userRuns.length ? events.find((item) => item.id === userRuns[0].event_id) : undefined;
  const newestEvent = events[0];
  const focusEvent = actedEvent && (!newestEvent || String(userRuns[0].created_at || "") >= String(newestEvent.created_at || ""))
    ? actedEvent : newestEvent;
  const focusData = focusEvent?.data || {};
  const runs = (state.runs || []).filter((item) => item.kind === "analysis" && focusEvent && item.event_id === focusEvent.id)
    .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  const analysisRun = runs.find((item) => item.status === "succeeded" && !isPreview(item) && runScenarioCount(item) > 0);
  const pendingAnalysis = runs.find((item) => !FINAL.has(String(item.status)) && !isPreview(item));
  const interpreting = runs.find((item) => !FINAL.has(String(item.status)) && isPreview(item));
  const approvals = (state.approvals || []).filter((item) => focusEvent && item.event_id === focusEvent.id);
  const approval = approvals[0];
  const committedVersion = (state.versions || []).find((item) => item.status === "committed"
    && approvals.some((row) => row.scenario_id === (item as Dict).scenario_id));

  const raw = [
    Boolean(state.project),
    Boolean(state.version),
    Boolean(focusEvent && focusData.review_status === "CONFIRMED" && hasPatch(focusData)),
    Boolean(analysisRun),
    Boolean(approval),
    Boolean(committedVersion),
  ];
  const done = raw.map((value, index) => value && raw.slice(0, index).every(Boolean));
  const firstOpen = done.indexOf(false);
  const current = firstOpen === -1 ? STAGES.length : firstOpen;
  const allDone = current === STAGES.length;

  let next: NextAction;
  if (current <= 1) {
    next = { label: "기준 일정 연결", section: "schedule", detail: "hero 데모 일정 또는 Excel로 기준 일정을 연결하세요." };
  } else if (current === 2) {
    if (!focusEvent) next = { label: "변경 불러오기", section: "changes", detail: "합성 통보를 불러오거나 협력사 메시지를 붙여 넣으세요." };
    else if (interpreting) next = { label: "변경 해석 확인", section: "changes", detail: "통보를 해석하고 있습니다. 끝나면 작업·날짜를 확인하세요." };
    else if (!hasPatch(focusData) && !focusData.evidence) next = { label: "작업·날짜 지정", section: "changes", detail: "통보에서 영향 작업을 찾지 못했습니다. 작업과 날짜를 지정하세요." };
    else next = { label: "변경 해석 확인", section: "changes", detail: "추출된 작업과 날짜가 맞는지 확인하면 영향 분석이 시작됩니다." };
  } else if (current === 3) {
    next = pendingAnalysis
      ? { label: "분석 결과 보기", section: "scenarios", detail: "영향을 계산하고 있습니다. 끝나면 대응안이 표시됩니다." }
      : { label: "영향 분석 시작", section: "changes", detail: "확인한 변경으로 영향 분석을 시작하세요." };
  } else if (current === 4) {
    next = { label: "대응안 비교·승인", section: "scenarios", detail: "대응안을 고르고 조건을 확인한 뒤 승인하세요." };
  } else if (current === 5) {
    next = { label: "새 일정 버전 확정", section: "execute", detail: "승인한 대응안을 새 일정 버전으로 확정하세요." };
  } else {
    next = { label: "Excel 다운로드", section: "execute", detail: "확정한 일정 버전을 Excel로 내려받거나 새 변경을 불러오세요." };
  }
  return { done, current, allDone, focusEvent, analysisRun, pendingAnalysis, interpreting, approval, committedVersion, next };
}

export function stageSummary(progress: Progress) {
  if (progress.allDone) return "6단계 완료 · 새 일정 버전 확정됨";
  const stage = STAGES[progress.current];
  return `${progress.current + 1}단계 · ${stage.label}`;
}
