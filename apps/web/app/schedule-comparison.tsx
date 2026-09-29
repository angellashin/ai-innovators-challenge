"use client";
import { useEffect, useState } from "react";
type Dates = {start: string; finish: string};
type Row = {task_id: string; name: string; baseline: Dates; risk: Dates | null; revised: Dates; shift_days: number; changed: boolean};
type Comparison = {baseline_finish: string; risk_finish: string | null; revised_finish: string; risk_days: number | null; recovered_days: number | null; remaining_days: number; committed: boolean; changed_count: number; rows: Row[]};
const day = (value: string) => Date.parse(value) / 86400000;
const signed = (value: number) => `${value > 0 ? "+" : ""}${value}일`;
export function ScheduleComparison({projectId, scenarioId, versionId}: {projectId: string; scenarioId?: string; versionId?: string}) {
  const [data, setData] = useState<Comparison | null>(null);
  const [error, setError] = useState(false);
  const [all, setAll] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError(false);
    const query = new URLSearchParams();
    // A committed version is the source of truth. Its server-side metadata already
    // identifies the approved scenario, so an older client-side selection must not
    // override it after a refresh or when the user opens the Schedule page later.
    if (scenarioId && !versionId) query.set("scenario_id", scenarioId);
    if (versionId) query.set("version_id", versionId);
    fetch(`/api/proxy/api/projects/${projectId}/schedule-comparison?${query}`, {signal: controller.signal})
      .then(async response => {if (!response.ok) throw Error(); return response.json();})
      .then(setData).catch(error => {if (error.name !== "AbortError") setError(true);});
    return () => controller.abort();
  }, [projectId, scenarioId, versionId]);
  if (error) return <p role="alert">일정 비교를 불러오지 못했습니다. 잠시 후 다시 시도하거나 연결 상태를 확인하세요.</p>;
  if (!data) return <p role="status">원본과 계산된 일정을 비교하고 있습니다…</p>;
  const changed = data.rows.filter(row => row.changed);
  const rows = all || !changed.length ? data.rows : changed;
  const dates = rows.flatMap(row => [row.baseline, row.risk, row.revised].flatMap(value => value ? [day(value.start), day(value.finish)] : []));
  const min = Math.min(...dates), max = Math.max(...dates), total = Math.max(1, max-min+1);
  const series = [{key:"baseline" as const, label:"최초 일정"}, ...(data.risk_finish ? [{key:"risk" as const,label:"무대응"}] : []), {key:"revised" as const,label:data.committed ? "반영 완료" : scenarioId ? "선택안 미리보기" : "현재 일정"}];
  return <section className="schedule-comparison" aria-label="최초 일정과 위험 반영 일정 비교">
    <div className="panel-heading"><div><span className="eyebrow">계산기 · 선후행 관계와 작업 달력으로 재계산</span><h3>{data.committed ? "일정에 이렇게 반영됐습니다" : "대응하면 일정이 어떻게 달라질까요?"}</h3></div><span className="status-chip">{data.committed ? "확정된 버전" : "미리보기"}</span></div>
    <div className="schedule-outcomes">
      <div><span>처음 완료일</span><strong>{data.baseline_finish}</strong><small>원본 기준 유지</small></div>
      {data.risk_finish && <div className="risk"><span>대응하지 않으면</span><strong>{signed(data.risk_days || 0)}</strong><small>{data.risk_finish}</small></div>}
      <div className="revised"><span>{data.committed ? "승인 반영 후" : "선택안 적용 시"}</span><strong>{signed(data.remaining_days)}</strong><small>{data.revised_finish}</small></div>
    </div>
    {data.risk_finish && <p className="recovery-equation"><b>위험 {signed(data.risk_days || 0)}</b><span>−</span><b>대응으로 {data.recovered_days}일 회복</b><span>=</span><b>최초 대비 {signed(data.remaining_days)}</b></p>}
    <p className="muted">완료일 차이는 달력 일수입니다. 여러 작업에 겹쳐 전파되므로 각 작업의 지연을 합산하지 않습니다.</p>
    <div className="comparison-legend">{series.map(item => <span key={item.key}><i className={item.key}/>{item.label}</span>)}</div>
    <div className="comparison-scroll"><div className="comparison-chart">
      <div className="comparison-axis"><b>작업 / 종료일 변화</b><div>{Array.from({length:5},(_,i) => <span key={i} style={{left:`${i*25}%`}}>{new Date((min+(max-min)*i/4)*86400000).toISOString().slice(0,10)}</span>)}</div></div>
      {rows.map(row => <div className="comparison-row" key={row.task_id}>
        <div className="comparison-label"><b>{row.task_id} · {row.name}</b><span>{row.baseline.finish} → {row.revised.finish}</span><strong>{signed(row.shift_days)}</strong></div>
        <div className="comparison-tracks">{series.map(item => {const value=row[item.key]; return <div className="comparison-track" key={item.key}>{value && <div className={`comparison-bar ${item.key}`} style={{left:`${(day(value.start)-min)/total*100}%`,width:`${Math.max(.2,(day(value.finish)-day(value.start)+1)/total*100)}%`}} title={`${item.label}: ${value.start} ~ ${value.finish}`} aria-label={`${row.task_id} ${item.label}: ${value.start} ~ ${value.finish}`}/>}</div>;})}</div>
      </div>)}
    </div></div>
    <button className="text-button" onClick={() => setAll(!all)}>{all ? "변경 영향 작업만 보기" : `전체 ${data.rows.length}개 작업 보기`}</button><small className="muted"> · 변경 영향 {data.changed_count}개 · 막대에 마우스를 올리면 정확한 기간</small>
  </section>;
}
