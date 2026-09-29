"use client";

// A mailbox-shaped list of incoming supplier notices. Today the messages come from the bundled demo
// notices; a Gmail source only has to return InboxMessage rows, and the rest of the screen stays the same.

import { useState } from "react";

type Dict = Record<string, unknown>;

export type InboxMessage = {
  id: string;
  from: string;
  subject: string;
  receivedAt: string;
  preview: string;
  body: string;
  source: "demo" | "gmail";
  tag?: "lead" | "test";
  externalId?: string;
  correctsId?: string;
  /** The sender label and mode exactly as the source gave them; they go into the change event unchanged. */
  sourceLabel: string;
  mode: string;
};

function text(value: unknown, fallback = "") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function stripMarker(body: string) {
  return body.replace(/^\[가상 메시지\]\s*/, "");
}

/** Demo notices as mailbox rows, newest first; external notices belong to the watch, not the inbox. */
export function demoInbox(events: Dict[]): InboxMessage[] {
  return events
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => text(item.channel, "supplier_message") === "supplier_message")
    .map(({ item, index }) => {
      const body = text(item.body || item.content);
      const label = text(item.source_label, "협력사");
      return {
        id: `demo-${index}`,
        from: label.split(" / ")[0],
        subject: text(item.subject, stripMarker(body).split(/[.。]/)[0].slice(0, 60)),
        receivedAt: text(item.published_at),
        preview: stripMarker(body).slice(0, 90),
        body,
        source: "demo" as const,
        tag: item.event_id === "X2" ? "lead" as const : "test" as const,
        externalId: text(item.event_id) || undefined,
        correctsId: text(item.corrects_event_id) || undefined,
        sourceLabel: label,
        mode: text(item.mode, "SYNTHETIC"),
      };
    })
    .sort((a, b) => b.receivedAt.localeCompare(a.receivedAt));
}

/** The change-event payload for one mailbox row, whichever source it came from. */
export function toEventPayload(message: InboxMessage): Dict {
  return {
    event_id: message.externalId,
    corrects_event_id: message.correctsId,
    content: message.body,
    channel: "supplier_message",
    source_label: message.sourceLabel,
    published_at: message.receivedAt,
    mode: message.mode,
    data_origin: message.source === "demo" ? "SYNTHETIC" : "USER",
    simulation_as_of: message.receivedAt,
  };
}

function received(value: string) {
  return value ? value.replace("T", " ").slice(0, 16) : "-";
}

export function Inbox({ messages, selectedId, importedIds, busy, hint, onSelect, onImport }: {
  messages: InboxMessage[]; selectedId: string; importedIds: Set<string>; busy: boolean; hint?: string;
  onSelect: (id: string) => void; onImport: (message: InboxMessage) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const visible = showAll || !messages.some(row => row.tag === "lead") ? messages : messages.filter(row => row.tag === "lead");
  const selected = visible.find((row) => row.id === selectedId) || visible.find((row) => row.tag === "lead") || visible[0];
  if (!messages.length) return <p className="muted">받은 통보가 없습니다. 아래에 메시지를 붙여 넣으세요.</p>;
  return <div className="inbox" aria-label="받은편지함">
    <ul className="inbox-list" role="listbox" aria-label="받은 통보">
      {visible.map((row) => {
        const imported = Boolean(row.externalId && importedIds.has(row.externalId));
        return <li key={row.id} role="option" aria-selected={row.id === selected?.id}
          className={`inbox-row${row.id === selected?.id ? " selected" : ""}${imported ? " imported" : ""}`}
          onClick={() => onSelect(row.id)} onKeyDown={(event) => { if (event.key === "Enter") onSelect(row.id); }} tabIndex={0}>
          <span className="inbox-from">{row.from}</span>
          <time className="inbox-time">{received(row.receivedAt)}</time>
          <span className="inbox-subject">
            {(row.tag === "lead" || imported) && <span>{row.tag === "lead" && <em className="inbox-tag lead">대표 데모</em>}{imported && <em className="inbox-tag">등록됨</em>}</span>}
            <b>{row.subject}</b><small>{row.preview}</small>
          </span>
        </li>;
      })}
    </ul>
    {messages.some(row => row.tag === "lead") && <button className="text-button inbox-more" onClick={() => setShowAll(!showAll)}>{showAll ? "대표 통보만 보기" : `다른 예시 통보 ${messages.length - 1}건 보기`}</button>}
    {selected && <div className="inbox-reader" aria-label="선택한 통보">
      <div className="inbox-reader-head"><b>{selected.subject}</b><small>{selected.from} · {received(selected.receivedAt)}{selected.source === "demo" ? " · 대표 사례" : ""}</small></div>
      <p>{selected.body}</p>
      {hint && selected.tag === "lead" && <small className="inbox-hint">{hint}</small>}
      <button onClick={() => onImport(selected)} disabled={busy}>변경으로 등록</button>
    </div>}
  </div>;
}
