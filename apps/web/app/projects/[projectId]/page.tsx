"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import Home from "../../workspace";

const nav = [
  ["개요", "overview"],
  ["변경", "changes"],
  ["일정", "schedule"],
  ["대응안", "scenarios"],
  ["실행 항목", "actions"],
  ["기록", "history"],
];

export default function ProjectWorkspacePage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const [activeSection, setActiveSection] = useState("overview");

  useEffect(() => {
    const syncSection = () => {
      const next = window.location.hash.replace("#", "");
      setActiveSection(nav.some(([, id]) => id === next) ? next : "overview");
    };
    syncSection();
    window.addEventListener("hashchange", syncSection);
    return () => window.removeEventListener("hashchange", syncSection);
  }, []);

  return (
    <div className="project-shell">
      <aside className="shell-nav">
        <div className="shell-caption">프로젝트 운영팀</div>
        <nav aria-label="프로젝트 작업공간 메뉴">{nav.map(([label, id]) => <a className={activeSection === id ? "shell-link active" : "shell-link"} href={`#${id}`} key={label}>{label}</a>)}</nav>
        <Link className="shell-back" href="/workspaces">← 프로젝트 목록</Link>
      </aside>
      <div className="shell-content"><Home initialProjectId={projectId} /></div>
    </div>
  );
}
