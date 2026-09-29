"use client";

import { useState } from "react";

type Dict = Record<string, unknown>;

function text(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value).replace(/\[합성\]\s*/g, "");
}

function bufferText(value: unknown, fallback = "-") {
  return text(value, fallback)
    .replaceAll("여유 0일이라 취약", "일정 여유 없음 · 지연 시 완료일 영향")
    .replace(/여유 (\d+)일이라 취약/g, "일정 여유 $1일 · 영향 가능성 높음")
    .replaceAll("여유 ", "일정 여유 ")
    .replaceAll("흡수 여력 있음", "일정 내 흡수 가능")
    .replaceAll("여유로", "일정 완충 기간으로");
}

function list(value: unknown) {
  return (Array.isArray(value) ? value : []) as Dict[];
}

export const RISK_STATUS: Record<string, string> = {
  EXPECTED: "예상됨", SIGNAL_DETECTED: "신호 감지", OCCURRED: "발생", RESPONDING: "대응 중", CLOSED: "종결",
};
const STATUS_ORDER = ["EXPECTED", "SIGNAL_DETECTED", "OCCURRED", "RESPONDING", "CLOSED"];
const ACTOR: Record<string, string> = {
  briefing: "일정 사전 점검", triage: "자동 분류", investigation: "AI 추가 확인", person: "사람",
};
const LEVEL_CLASS: Record<string, string> = { critical: "danger", high: "warn", medium: "", low: "ok", unknown: "" };
const TOOL: Record<string, string> = {
  find_procurement_items: "같은 원인 품목 찾기", get_task_facts: "작업 속성 확인", check_schedule_slack: "일정 완충 기간 계산",
  search_risk_cases: "유사 사례 검색",
};
const ATTRIBUTE_LABEL: Record<string, string> = {
  origin_country: "원산지", customs_required: "통관", permit_required: "인허가", outdoor: "야외 작업",
};

function yesNo(value: unknown) {
  if (value === "속성 없음") return <em className="no-attribute">속성 없음</em>;
  if (value === true) return "예";
  if (value === false) return "아니오";
  return text(value);
}

function CaseList({ cases, label }: { cases: Dict[]; label?: unknown }) {
  if (!cases.length) return <small className="muted">저장된 L2 사례 없음</small>;
  return <p className="risk-cases"><small>L2 실제 사례{label ? ` · ${text(label)}` : ""}:</small>{cases.map((row, index) => <span key={text(row.risk_id)}>
    {index ? " · " : " "}<a href={text(row.source_url)} target="_blank" rel="noreferrer">{text(row.risk_id)} {text(row.title)}</a>
    <small> ({text(row.published_date)}{row.temporal_status === "POST_AS_OF_REFERENCE" ? " · 근거 기준일 이후 발행: 참고만" : ""})</small>
  </span>)}</p>;
}

/** Top risks of the baseline: the calculator ranks, the agent may add a same-cause item or drop a candidate. */
export function BriefingPanel({ briefing, llmMode }: { briefing?: Dict | null; llmMode: string }) {
  if (!briefing) return null;
  const status = text(briefing.run_status, "");
  if (status === "queued" || status === "running") {
    return <section className="focus-card briefing" aria-label="일정 사전 점검"><span className="eyebrow">일정 사전 점검</span>
      <h3>먼저 확인할 위험을 찾고 있습니다…</h3><p className="muted">작업 특성, 선후행 관계, 일정 여유를 바탕으로 우선순위를 정합니다.</p></section>;
  }
  const result = (briefing.briefing || {}) as Dict;
  const agent = (briefing.agent || {}) as Dict;
  const risks = list(result.risks);
  const excluded = list(result.excluded);
  const coverage = (result.coverage || {}) as Dict;
  const missing = (coverage.missing || {}) as Record<string, number>;
  const log = list(agent.tool_log);
  const usage = (agent.usage || {}) as Dict;
  const agentReviewed = agent.mode === "agent_review";
  return <section className="focus-card briefing" aria-label="일정 사전 점검">
    <div className="panel-heading"><div>
      <span className="eyebrow">일정 사전 점검 · 기준 {text(result.as_of)}</span>
      <h3>먼저 확인할 위험 {risks.length}개</h3>
      <p>작업 특성과 일정 여유를 바탕으로 우선순위를 정했습니다. 각 항목을 펼쳐 대상 작업과 근거를 확인하세요.</p>
    </div><span className={`status-chip${agentReviewed ? " confirm" : ""}`}>{agentReviewed ? "AI 검토 완료" : "일정 기준 점검"}</span></div>
    {!agentReviewed && <p className="muted">{text(agent.summary, "현재는 일정 정보만으로 우선순위를 정했습니다.").replaceAll("에이전트가 꺼져 있어", "AI 분석이 연결되지 않아")}</p>}
    <ol className="briefing-list">{risks.map((risk) => {
      const vulnerability = (risk.vulnerability || {}) as Dict;
      const score = (risk.score || {}) as Dict;
      const items = list(risk.items);
      const tasks = list(risk.tasks);
      return <li key={text(risk.risk_id)} className="briefing-risk">
        <div className="briefing-head">
          <span className="rank">{text(risk.rank)}</span>
          <div><b>{text(risk.title)}</b><small>{bufferText(score.label)} · 관련 품목 {items.length}개 / 작업 {text(risk.task_count, String(tasks.length))}개</small></div>
          <span className={`status-chip ${LEVEL_CLASS[text(vulnerability.level, "")] || ""}`}>{bufferText(vulnerability.label)}</span>
        </div>
        {list(risk.actions).slice(0,1).map((action,index) => <p key={index}><b>다음 행동</b> · {text(action.what)} {action.by ? `(${text(action.by)}까지)` : ""}</p>)}
        <details><summary>왜 위험한가요? · 품목·근거·계산 내역</summary>
        <p>{text(risk.basis)}</p>
        <LinkedCause cause={risk.linked_cause as Dict | undefined} />
        {list(risk.critical_warnings).map((warning, index) => <p key={index} className="critical-warning">{bufferText(warning)}</p>)}
        {items.length > 0 && <table className="risk-table"><thead><tr><th>품목</th><th>협력사</th><th>원산지</th><th>통관</th><th>허가·인증</th><th>도착 예정</th><th>필요 작업</th><th>일정 완충</th></tr></thead>
          <tbody>{items.map((row) => <tr key={text(row.item_id)} className={row.float_days === 0 ? "critical" : ""}>
            <td><b>{text(row.item_id)}</b> <small>{text(row.item_name)}</small>{list(risk.added_item_ids).some((id) => text(id) === text(row.item_id)) ? <em className="added"> 추가 확인</em> : null}</td>
            <td>{yesNo(row.supplier_id)}</td><td>{yesNo(row.origin_country)}</td><td>{yesNo(row.customs_required)}</td><td>{yesNo(row.requirement)}</td>
            <td>{yesNo(row.planned_arrival)}</td><td>{text(row.task_id)}</td><td>{bufferText(((row.vulnerability || {}) as Dict).label)}</td>
          </tr>)}</tbody></table>}
        {tasks.length > 0 && <table className="risk-table"><thead><tr><th>작업</th><th>착수 예정</th>{Object.values(ATTRIBUTE_LABEL).map((label) => <th key={label}>{label}</th>)}<th>일정 완충</th></tr></thead>
          <tbody>{tasks.map((row) => <tr key={text(row.task_id)} className={row.float_days === 0 ? "critical" : ""}>
            <td><b>{text(row.task_id)}</b> <small>{text(row.name)}</small></td><td>{text(row.baseline_start)}</td>
            {Object.keys(ATTRIBUTE_LABEL).map((key) => <td key={key}>{yesNo(row[key])}</td>)}
            <td>{bufferText(((row.vulnerability || {}) as Dict).label)}</td>
          </tr>)}</tbody></table>}
        {Number(risk.task_count) > tasks.length && <small className="muted">작업 {text(risk.task_count)}개 중 {tasks.length}개 표시</small>}
        <div className="risk-actions"><small>선제 행동</small><ul>{list(risk.actions).map((action, index) => <li key={index}>
          {text(action.what)} · <b>{action.by ? `${text(action.by)}까지` : ""}</b> <small>({text(action.basis)})</small></li>)}</ul></div>
        <CaseList cases={list(risk.cases)} label={risk.case_label} />
        {risk.agent_note ? <p className="agent-note-line">AI 검토 메모: {text(risk.agent_note)}</p> : null}
        </details>
      </li>;
    })}</ol>
    {Number(result.more_count) > 0 && <small className="muted">순위 밖 후보 {text(result.more_count)}개는 표시하지 않았습니다.</small>}
    <details><summary>점검 범위와 누락 정보</summary><p className="coverage-line"><b>속성 없음</b> · 진행 전 작업 {text(coverage.open_task_count)}개 중 {Object.entries(ATTRIBUTE_LABEL).map(([key, label]) => `${label} ${missing[key] ?? 0}개`).join(" · ")}. 속성이 비어 있는 작업은 해당 위험 판단에서 빠집니다.</p></details>
    {excluded.length > 0 && <details className="evidence-block"><summary>우선순위에서 제외한 후보 {excluded.length}개 · 이유 보기</summary>
      <ul>{excluded.map((row) => <li key={text(row.risk_key)}><b>{text(row.title)}</b> — {text(row.reason)}</li>)}</ul></details>}
    {agentReviewed && <details className="investigation-log"><summary>AI가 확인한 근거 {log.length}개 보기</summary>
      {Number(usage.llm_calls) > 0 && <p className="usage-line">AI 검토 {text(usage.llm_calls)}회{llmMode === "replay" ? " · 검증용 응답 재생" : ""}</p>}
      <p>{text(agent.summary, "")}</p>
      <ol className="investigation-checks">{log.map((entry, index) => <li key={index}><b>{TOOL[text(entry.tool)] || text(entry.tool)}</b>
        {(entry.args as Dict | undefined)?.reason ? <span className="why">왜: {text((entry.args as Dict).reason)}</span> : null}
        <small>→ {briefingCheck(entry)}</small></li>)}</ol>
    </details>}
  </section>;
}

/** A cause the workbook does not state, named by the agent only with an L2 case it read. */
function LinkedCause({ cause }: { cause?: Dict }) {
  if (!cause) return null;
  return <p className="linked-cause">AI가 외부 근거와 연결한 원인: <b>{text(cause.text)}</b>{list(cause.cases).map((row) => <span key={text(row.risk_id)}>
    {" · "}<a href={text(row.source_url)} target="_blank" rel="noreferrer">{text(row.risk_id)} {text(row.title)}</a> <small>({text(row.published_date)}{row.temporal_status === "POST_AS_OF_REFERENCE" ? " · 근거 기준일 이후: 참고만" : ""})</small></span>)}
    <small> · 엑셀에는 위험이 적혀 있지 않고, 품목 사실(원산지·통관·품목명)과 실제 사례를 AI가 연결했습니다.</small></p>;
}

function briefingCheck(entry: Dict) {
  const result = (entry.result || {}) as Dict;
  if (result.status === "limit_reached") return "확인 한도(3회)를 넘어 실행하지 않음";
  if (entry.tool === "find_procurement_items") return list(result.items).map((row) => `${text(row.item_id)}(${text(row.needed_for_task_id)})`).join(" · ") || "해당 품목 없음";
  if (entry.tool === "check_schedule_slack") return list(result.tasks).map((row) => `${text(row.task_id)} 일정 완충 ${text(row.float_calendar_days)}일`).join(" · ");
  if (entry.tool === "get_task_facts") return list(result.tasks).map((row) => `${text(row.task_id)} 원산지 ${text(row.origin_country, "속성 없음")}${row.customs_required ? " · 통관" : ""}`).join(" · ");
  if (entry.tool === "search_risk_cases") return `${text(result.label, "")} 사례 ${list(result.results).length}건`;
  return "";
}

/** One register the briefing, the triage and the investigation read and write; a person can set any status. */
export function RiskRegister({ risks, busy, onStatus }: { risks: Dict[]; busy: boolean; onStatus: (riskId: string, status: string, note: string) => void }) {
  const [editing, setEditing] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("CLOSED");
  if (!risks.length) return null;
  return <section className="focus-card risk-register" aria-label="리스크 대장">
    <div className="panel-heading"><div><span className="eyebrow">예상 리스크</span><h3>사전 점검 결과 {risks.length}건</h3>
      <p>새 외부 정보가 들어오면 관련 항목을 찾아 검토 대상으로 알려드립니다.</p></div></div>
    <ul className="register-list">{risks.map((risk) => {
      const riskId = text(risk.risk_id);
      const current = STATUS_ORDER.indexOf(text(risk.status));
      return <li key={riskId} className={`register-row status-${text(risk.status).toLowerCase()}`}>
        <div className="register-head"><span className="status-chip">{RISK_STATUS[text(risk.status)]}</span><b>{text(risk.title)}</b></div>
        <details><summary>{riskId} · 연결 근거와 대응 이력</summary>
        {risk.linked_cause ? <small className="linked-cause-line">AI가 외부 근거와 연결한 원인: {text((risk.linked_cause as Dict).text)} ({((risk.linked_cause as Dict).case_ids as string[] || []).join(", ")})</small> : null}
        <ol className="status-track" aria-label={`${riskId} 상태`}>{STATUS_ORDER.map((name, index) => <li key={name} className={index < current ? "past" : index === current ? "now" : ""}>{RISK_STATUS[name]}</li>)}</ol>
        <small>관련 {list(risk.items).length ? `품목 ${((risk.item_ids || []) as string[]).join("·")} · ` : ""}작업 {((risk.task_ids || []) as string[]).slice(0, 6).join("·")}{((risk.task_ids || []) as string[]).length > 6 ? " 외" : ""}</small>
        <details><summary>근거와 변경 이력 {list(risk.history).length}건</summary>
          <p><small>근거: {text(risk.basis)}</small></p>
          <ul className="history-list-plain">{list(risk.history).map((row, index) => <li key={index}>
            <b>{RISK_STATUS[text(row.status)] || text(row.status)}</b>{row.moved === false ? " (상태 유지)" : ""} · {ACTOR[text(row.actor)] || text(row.actor)} · {text(row.at).replace("T", " ").slice(0, 16)}
            <small> {text(row.note, "")}</small>{row.quote ? <q>{text(row.quote)}</q> : null}
          </li>)}</ul>
          {editing === riskId ? <div className="register-edit">
            <label>상태<select value={status} onChange={(event) => setStatus(event.target.value)}>{STATUS_ORDER.map((name) => <option key={name} value={name}>{RISK_STATUS[name]}</option>)}</select></label>
            <label>메모<input value={note} onChange={(event) => setNote(event.target.value)} placeholder="예: 대응안 확정으로 종결" /></label>
            <button className="secondary" disabled={busy} onClick={() => { onStatus(riskId, status, note); setEditing(""); setNote(""); }}>상태 기록</button>
          </div> : <button className="text-button" onClick={() => { setEditing(riskId); setStatus(text(risk.status) === "CLOSED" ? "RESPONDING" : "CLOSED"); }}>사람이 상태 바꾸기</button>}
        </details></details>
      </li>;
    })}</ul>
  </section>;
}

const TRIAGE_STATUS: Record<string, string> = {
  interpreted: "자동 추리 완료", no_candidates: "규칙 후보 0건 · LLM 없이 무관", agent_off: "AI 조사 미연결 · 규칙 후보만",
  budget_stopped: "자동 추리기 한도 초과 · 규칙 결과만", interpretation_failed: "자동 추리 실패 · 규칙 결과만",
  already_attempted: "이미 시도함 · 규칙 결과만", ai_running: "AI 분석 실행 중… · 규칙 후보 먼저 표시",
};

/** The one automatic LLM call on a detected change: related / needs check / unrelated, folded. */
export function TriagePanel({ triage, taskNames, risks, picked = [], onPick }: {
  triage?: Dict; taskNames: Record<string, string>; risks: Dict[]; picked?: string[]; onPick?: (ids: string[]) => void;
}) {
  if (!triage) return null;
  const status = text(triage.status, "");
  const related = list(triage.related);
  const check = list(triage.needs_check);
  const unrelated = list(triage.unrelated);
  const ruleCandidates = list(triage.rule_candidates);
  const links = list(triage.risk_links);
  const titles = Object.fromEntries(risks.map((risk) => [text(risk.risk_id), text(risk.title)]));
  const toggle = (id: string) => onPick?.(picked.includes(id) ? picked.filter((value) => value !== id) : [...picked, id]);
  const row = (item: Dict) => <li key={text(item.task_id)}><span>{text(item.task_id)}</span> {taskNames[text(item.task_id)] || ""}
    {list(item.reasons).length ? <small> {text(list(item.reasons)[0])}</small> : null}
    {item.quote ? <q>{text(item.quote)}</q> : null}</li>;
  const selectable = (item: Dict) => onPick ? <li key={text(item.task_id)} className="pickable"><label>
    <input type="checkbox" checked={picked.includes(text(item.task_id))} onChange={() => toggle(text(item.task_id))} />
    <span>{text(item.task_id)}</span> {taskNames[text(item.task_id)] || ""}</label>
    {list(item.reasons).length ? <small>{text(list(item.reasons)[0])}</small> : null}</li> : row(item);
  return <div className="triage" aria-label="리스크 후보 정리">
    <div className="triage-head"><b>리스크 후보</b><span className={`status-chip${status === "interpreted" ? " confirm" : " warn"}`}>{TRIAGE_STATUS[status] || status}</span>
      <small>{status === "interpreted" ? `후보 ${related.length + check.length}개를 관련성과 확인 필요로 나눴습니다. 선택 전에는 일정이 바뀌지 않습니다.` : text(triage.summary, "").replace("에이전트가 꺼져 있어", "AI 분석이 연결되지 않아")}</small></div>
    {status === "ai_running" && <div className="triage-progress" role="status"><b>규칙 결과</b><span>{ruleCandidates.length}건 먼저 표시됨</span><em>AI 분석 실행 중…</em></div>}
    {status !== "interpreted" && status !== "ai_running" && ruleCandidates.length > 0 && <details className="triage-rule-candidates"><summary>규칙으로 찾은 후보 {ruleCandidates.length}개</summary><ul className="triage-candidate-list">{ruleCandidates.map(row)}</ul></details>}
    {status === "interpreted" && <>
      <div className="triage-buckets">
        <div><b>관련 있음 {related.length}</b><small>일정 영향 분석에 자동 포함됩니다.</small>{related.length ? <ul className="triage-candidate-list">{related.map(row)}</ul> : <small className="muted">없음</small>}</div>
        <div><b>확인 필요 {check.length}</b><small>필요한 작업만 선택해 함께 분석하세요.</small>{check.length ? <ul className="triage-candidate-list">{check.map(selectable)}</ul> : <small className="muted">없음</small>}</div>
      </div>
      {unrelated.length > 0 && <details className="evidence-block"><summary>무관 {unrelated.length}건 제외 · 이유 보기</summary><ul>{unrelated.map(row)}</ul></details>}
      {links.length > 0 && <p className="risk-link-line">리스크 대장 연결: {links.map((link) => `${text(link.risk_id)} ${titles[text(link.risk_id)] || ""}`).join(" · ")} → 신호 감지</p>}
    </>}
  </div>;
}

/** What the investigation tied this notice to in the register. */
export function RiskLinkNote({ link, compact = false }: { link?: Dict | null; compact?: boolean }) {
  if (!link) return null;
  const warnings = list(link.critical_warnings);
  const occurred = link.status === "OCCURRED";
  if (compact) return <p className="risk-link-line">리스크 대장: {occurred ? "등록 시 예상했던 위험이 발생함" : "등록 시 예상했던 위험의 신호"} · {text(link.risk_id)} {text(link.title)}</p>;
  return <div className="risk-link" aria-label="등록 시 예상했던 위험">
    <span className="eyebrow">리스크 대장 연결 · {RISK_STATUS[text(link.previous_status)] || text(link.previous_status)} → {text(link.status_label)}</span>
    <b>{occurred ? "등록 시 예상했던 위험이 발생함" : "등록 시 예상했던 위험의 신호"}: {text(link.risk_id)} {text(link.title)}{link.linked_cause ? ` · ${text((link.linked_cause as Dict).text)}` : ""}</b>
    {link.reason ? <p>{text(link.reason)}</p> : null}
    {warnings.map((warning, index) => <p key={index} className="critical-warning">등록 시 경고: {text(warning)}</p>)}
    <small>{link.expected_by === "briefing" ? `기준 일정 등록 때 브리핑이 예상함 (${text(link.expected_at).replace("T", " ").slice(0, 16)})` : ""}</small>
  </div>;
}
