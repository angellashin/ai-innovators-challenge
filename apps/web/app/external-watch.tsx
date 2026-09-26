"use client";

import { useEffect, useState } from "react";

type Row = Record<string, any>;
type FeedRow = { id: string; title: string; published: string; source: string; synthetic: boolean; related: string };
type Props = { plan: Row; tasks: Row[]; disabled: boolean; onSave: (plan: Row) => Promise<void>; onScan: () => void; feed?: FeedRow[] };

const PROPOSAL_KIND: Record<string, string> = {
  outdoor: "야외 작업", holiday: "공휴일 달력", weather: "현장 기상", source: "공지 출처", supplier_calendar: "협력사 달력",
};

export function ExternalWatch({ plan, tasks, disabled, onSave, onScan, feed = [] }: Props) {
  const [draft, setDraft] = useState<Row>(plan);
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { if (!editing) setDraft(plan); }, [plan, editing]);
  const update = (values: Row) => { setEditing(true); setDraft((old) => ({ ...old, ...values })); };
  const site = draft.weather_site || {};
  const rules: Row[] = draft.source_rules || [];
  const holidays: Row[] = draft.holiday_calendars || [];
  const limits = draft.weather_limits || {};
  const proposals: Row[] = draft.proposal_items || [];
  const undecided = proposals.filter((item) => !item.decision || item.decision === "proposed").length;
  const outdoorCandidates = new Set(proposals.filter((item) => item.kind === "outdoor" && item.decision !== "excluded").flatMap((item) => item.task_ids || []));
  async function save(enabled: boolean) {
    setMessage("");
    try {
      await onSave({ ...draft, enabled });
      setEditing(false);
      setMessage(enabled ? "등록된 출처를 주기적으로 확인합니다." : "감시 설정을 저장했습니다.");
    } catch (error) {
      setMessage(String((error as Row).message || error));
    }
  }
  function taskSelect(value: string[], change: (ids: string[]) => void, label: string, outdoor = false) {
    return <label>{label}<select multiple aria-label={label} value={value} onChange={(event) => change(Array.from(event.target.selectedOptions, (option) => option.value))}>
      {tasks.filter((task) => !outdoor || task.outdoor || outdoorCandidates.has(task.task_id)).map((task) => <option key={task.task_id} value={task.task_id}>{task.task_id} · {task.name}</option>)}
    </select><small>Ctrl 또는 Command 키로 여러 작업을 선택할 수 있습니다.</small></label>;
  }
  return <div className="watch-card external-watch">
    <b>외부 변화 감시</b><span className={plan.enabled ? "pill ok" : "pill"}>{plan.enabled ? "주기 감시 중" : "꺼짐"}</span>
    <p>날씨·공휴일은 지정 작업에 연결해 계산하고, 공지·뉴스는 근거와 적용 후보를 검토합니다.</p>
    {feed.length > 0 && <div className="watch-feed" aria-label="감시 피드에서 들어온 신호">
      <b>감시 피드에서 들어온 신호 {feed.length}건</b>
      <ul>{feed.map((row) => <li key={row.id}>
        <span className={`status-chip${row.related ? "" : " warn"}`}>{row.related ? "관련 있음" : "검토 필요"}</span> {row.title}
        <small>{row.source}{row.synthetic ? " · 합성 공지" : ""} · 발행 {row.published} · {row.related || "연결된 협력사 통보 없음 · 변경 카드에서 검토"}</small>
      </li>)}</ul>
    </div>}
    {proposals.length > 0 && <p className="watch-progress">감시 제안 {proposals.length}개 중 {undecided ? `${undecided}개를 아직 결정하지 않았습니다. 모두 수락·수정·제외해야 활성화할 수 있습니다.` : "모두 결정했습니다."}</p>}
    <details>
      <summary>감시 제안 검토·작업 연결 설정</summary>
      {proposals.length > 0 && <fieldset disabled={disabled}>
        <legend>프로젝트 맞춤 감시 제안</legend>
        <p>각 제안의 근거를 검토하고 수락·수정·제외를 선택하세요. 아래에서 날짜·출처·임계값을 편집할 수 있습니다. 협력사 달력 제안을 수락해도 달력이 자동으로 만들어지지는 않습니다. 휴무일은 실행 화면의 고급 설정에서 입력합니다.</p>
        {undecided > 0 && <button className="secondary" onClick={() => update({ proposal_items: proposals.map((row) => !row.decision || row.decision === "proposed" ? { ...row, decision: "accepted" } : row) })}>남은 제안 {undecided}개 모두 수락</button>}
        {proposals.map((item, index) => <div className="external-config" key={item.id}>
          <b>{PROPOSAL_KIND[item.kind] || item.kind} · {(item.task_ids || []).join(", ") || item.supplier_id || "확인 요청"}</b>
          <small>{item.reason}</small>
          {item.agent_note && <small>에이전트 보강: {item.agent_note}{item.agent_keywords?.length ? ` · 검색어 후보 ${item.agent_keywords.join(", ")}` : ""}</small>}
          {item.threshold_candidates && <small>작업별 임계값 후보(현장 확인 필요): {JSON.stringify(item.threshold_candidates)}</small>}
          <label>선택<select aria-label={`감시 제안 ${item.id}`} value={item.decision || "proposed"} onChange={(event) => update({ proposal_items: proposals.map((row, rowIndex) => rowIndex === index ? { ...row, decision: event.target.value } : row) })}>
            <option value="proposed">검토 전</option><option value="accepted">수락</option><option value="modified">수정 후 수락</option><option value="excluded">제외</option>
          </select></label>
        </div>)}
      </fieldset>}
      <fieldset disabled={disabled}>
        <legend>현장 기상 예보</legend>
        <label><input type="checkbox" checked={Boolean(draft.weather_site)} onChange={(event) => update({ weather_site: event.target.checked ? { latitude: "", longitude: "", timezone: "UTC", label: "현장" } : null })} />기상 감시 사용</label>
        {draft.weather_site && <>
          <label>현장 이름<input value={site.label || ""} onChange={(event) => update({ weather_site: { ...site, label: event.target.value } })} /></label>
          <label>위도<input type="number" step="any" min="-90" max="90" value={site.latitude ?? ""} onChange={(event) => update({ weather_site: { ...site, latitude: event.target.value === "" ? "" : Number(event.target.value) } })} /></label>
          <label>경도<input type="number" step="any" min="-180" max="180" value={site.longitude ?? ""} onChange={(event) => update({ weather_site: { ...site, longitude: event.target.value === "" ? "" : Number(event.target.value) } })} /></label>
          <label>시간대<input value={site.timezone || "UTC"} onChange={(event) => update({ weather_site: { ...site, timezone: event.target.value } })} placeholder="Europe/Budapest" /></label>
          {taskSelect(draft.weather_task_ids || [], (ids) => update({ weather_task_ids: ids }), "해당 현장의 야외 작업", true)}
          <small>Excel의 야외작업 표시와 작업명·단계·위험 태그에서 제안된 후보를 표시합니다. 실제 야외 여부를 확인하세요.</small>
          {([["max_wind_speed_kmh", "중단 기준 풍속 (km/h)"], ["max_precipitation_mm", "중단 기준 일강수량 (mm)"]] as const).map(([key, label]) =>
            <label key={key}>{label}<input type="number" min="0" step="any" value={limits[key] ?? ""} onChange={(event) => {
              const next = { ...limits }; if (event.target.value === "") delete next[key]; else next[key] = Number(event.target.value);
              update({ weather_limits: next });
            }} /></label>)}
          <small>현장 승인 기준을 입력하세요. 예보 수치는 작업 중단 확정이 아닙니다.</small>
        </>}
        <label>기상 확인 간격 (시간)<input type="number" min="1" max="168" value={draft.weather_poll_hours || 6} onChange={(event) => update({ weather_poll_hours: Number(event.target.value) })} /></label>
      </fieldset>
      <fieldset disabled={disabled}>
        <legend>공휴일 달력</legend>
        {holidays.map((config, index) => <div key={index} className="external-config">
          <label>국가 코드<input aria-label={`공휴일 국가 ${index + 1}`} value={config.country_code || ""} maxLength={2} placeholder="HU" onChange={(event) => update({ holiday_calendars: holidays.map((row, i) => i === index ? { ...row, country_code: event.target.value.toUpperCase() } : row) })} /></label>
          <label>연도<input type="number" min="2000" max="2100" value={config.year} onChange={(event) => update({ holiday_calendars: holidays.map((row, i) => i === index ? { ...row, year: Number(event.target.value) } : row) })} /></label>
          <label>지역 코드 (선택)<input value={config.subdivision || ""} placeholder="예: DE-BY" onChange={(event) => update({ holiday_calendars: holidays.map((row, i) => i === index ? { ...row, subdivision: event.target.value || null } : row) })} /></label>
          {taskSelect(config.task_ids || [], (ids) => update({ holiday_calendars: holidays.map((row, i) => i === index ? { ...row, task_ids: ids } : row) }), `공휴일 적용 작업 ${index + 1}`)}
          <button className="text-button" onClick={() => update({ holiday_calendars: holidays.filter((_, i) => i !== index) })}>이 달력 제거</button>
        </div>)}
        <button className="secondary" onClick={() => update({ holiday_calendars: [...holidays, { country_code: "", year: new Date().getFullYear(), task_ids: [] }] })}>공휴일 달력 추가</button>
      </fieldset>
      <fieldset disabled={disabled}>
        <legend>공지·뉴스 출처</legend>
        <label>허용된 출처 URL (한 줄에 하나)<textarea value={(draft.source_allowlist || []).join("\n")} onChange={(event) => update({ source_allowlist: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean) })} /></label>
        <small>서버에서 허용한 HTTPS 출처만 수집합니다. 검색어 일치는 적용 후보이며 날짜·규제 적용을 확정하지 않습니다.</small>
        {rules.map((rule, index) => <div key={index} className="external-config">
          <label>출처<select value={rule.url} onChange={(event) => update({ source_rules: rules.map((row, i) => i === index ? { ...row, url: event.target.value } : row) })}><option value="">출처 선택</option>{(draft.source_allowlist || []).map((url: string) => <option key={url} value={url}>{url}</option>)}</select></label>
          <label>관련 검색어 (쉼표 구분)<input value={(rule.keywords || []).join(",")} onChange={(event) => update({ source_rules: rules.map((row, i) => i === index ? { ...row, keywords: event.target.value.split(",") } : row) })} /></label>
          {taskSelect(rule.task_ids || [], (ids) => update({ source_rules: rules.map((row, i) => i === index ? { ...row, task_ids: ids } : row) }), `공지 관련 작업 ${index + 1}`)}
          <button className="text-button" onClick={() => update({ source_rules: rules.filter((_, i) => i !== index) })}>이 연결 제거</button>
        </div>)}
        <button className="secondary" onClick={() => update({ source_rules: [...rules, { url: "", keywords: [], task_ids: [] }] })}>출처와 작업 연결</button>
        <label>공지 확인 간격 (시간)<input type="number" min="1" max="168" value={draft.notice_poll_hours || 12} onChange={(event) => update({ notice_poll_hours: Number(event.target.value) })} /></label>
      </fieldset>
      <button disabled={disabled} className="secondary" onClick={() => save(false)}>설정 저장 · 감시 중지</button>
    </details>
    <div className="button-row">
      <button disabled={disabled} className="secondary" onClick={() => save(true)}>설정 저장 · 감시 활성화</button>
      <button disabled={disabled || !plan.enabled} className="secondary" onClick={onScan}>외부 변화 지금 확인</button>
    </div>
    {message && <p className="watch-message" role="status">{message}</p>}
  </div>;
}

export function EvidenceReview({ event, tasks, disabled, onReview, onAnalyze }: {
  event: Row; tasks: Row[]; disabled: boolean; onReview: (payload: Row) => Promise<void>; onAnalyze: () => void;
}) {
  const [ids, setIds] = useState<string[]>(event.related_task_ids || []);
  const [day, setDay] = useState("");
  const [operation, setOperation] = useState("not_before");
  const [note, setNote] = useState("");
  const [allTasks, setAllTasks] = useState(false);
  const hasPatch = Object.keys(event.patch || {}).length > 0;
  const candidates = (event.candidates || []) as Row[];
  const related = new Set<string>([...(event.related_task_ids || []), ...candidates.map((row) => row.task_id)]);
  const choices = allTasks || !related.size ? tasks : tasks.filter((task) => related.has(task.task_id));
  const day10 = (value: unknown) => (typeof value === "string" && value ? value.slice(0, 10) : "");
  const reviewLabel: Record<string, string> = { PENDING: "확인 대기", CONFIRMED: "확인됨", REJECTED: "보류", SUPERSEDED: "대체됨" };
  const candidateRow = (candidate: Row) => <p key={candidate.task_id}>{candidate.task_id}: {(candidate.reasons || []).join(" · ")}{candidate.quote && <><br /><small>원문 인용: <q>{candidate.quote}</q></small></>}</p>;
  const invalid = ["SUPERSEDED", "REJECTED"].includes(event.review_status);
  const proof = event.evidence || {};
  const url = /^https?:\/\//.test(proof.source_url || "") ? proof.source_url : null;
  return <div className="evidence-review">
    <b>{proof.kind === "FORECAST" ? "예보 기반 · 조건부 계산" : proof.kind === "CALENDAR" ? "공개 달력 · 실제 휴무 확인 필요" : "공지·뉴스 · 적용 여부 확인 필요"}</b>
    {url && <a href={url} target="_blank" rel="noreferrer">원문 근거 열기 ↗</a>}
    <small>수집 {day10(proof.fetched_at) || "미확인"} · 발행 {day10(proof.published_at) || day10(event.published_at) || "미확인"} · {reviewLabel[event.review_status] || event.review_status}</small>
    {candidates.slice(0, 5).map(candidateRow)}
    {candidates.length > 5 && <details><summary>관련 작업 후보 {candidates.length}개 모두 보기</summary>{candidates.slice(5).map(candidateRow)}</details>}
    {event.missing_fields?.length > 0 && <p>확인할 내용: {event.missing_fields.join(", ")}</p>}
    {hasPatch && <ul>{Object.entries(event.patch).flatMap(([kind, values]) => Object.entries(values as Row).map(([id, value]) => <li key={`${kind}-${id}`}>{id} · {kind === "blocked_dates" ? "작업 제외일" : kind === "not_before" ? "착수 가능일" : "완료 예정일"}: {Array.isArray(value) ? value.join(", ") : String(value)}</li>))}</ul>}
    {!hasPatch && !invalid && <details><summary>적용 작업·날짜 확인</summary>
      <label>영향 작업<select multiple value={ids} onChange={(input) => setIds(Array.from(input.target.selectedOptions, (option) => option.value))}>{choices.map((task) => <option value={task.task_id} key={task.task_id}>{task.task_id} · {task.name}</option>)}</select></label>
      {related.size > 0 && <label className="inline-check"><input type="checkbox" checked={allTasks} onChange={(input) => setAllTasks(input.target.checked)} /> 관련 후보 밖의 작업도 보기</label>}
      <label>변경 내용<select value={operation} onChange={(input) => setOperation(input.target.value)}><option value="not_before">이 날짜부터 착수 가능</option><option value="blocked_dates">이 날짜는 작업 불가</option><option value="estimated_finish">변경된 완료 예정일</option></select></label>
      <label>적용 날짜<input type="date" value={day} onChange={(input) => setDay(input.target.value)} /></label>
      <label>적용 근거·확인 내용<textarea value={note} onChange={(input) => setNote(input.target.value)} placeholder="원문의 효력일, 적용 설비·지역과 담당자 확인 내용을 기록하세요." /></label>
      <button disabled={disabled || !ids.length || !day || !note.trim()} onClick={() => onReview({ confirmed: true, related_task_ids: ids, review_note: note, patch: { [operation]: Object.fromEntries(ids.map((id) => [id, operation === "blocked_dates" ? [day] : day])) } })}>근거 확인 후 계산</button>
    </details>}
    {hasPatch && !invalid && event.review_status !== "CONFIRMED" && <button disabled={disabled} onClick={() => onReview({ confirmed: true })}>이 근거의 작업·날짜 적용 확인</button>}
    {!invalid && <button className="secondary" disabled={disabled} onClick={onAnalyze}>{hasPatch ? "영향 분석 시작" : "근거·작업 후보 분석"}</button>}
    {!invalid && <button className="text-button" disabled={disabled} onClick={() => onReview({ confirmed: false })}>적용 보류</button>}
    {invalid && <p>보류되었거나 최신 근거로 대체되어 승인할 수 없습니다.</p>}
  </div>;
}

