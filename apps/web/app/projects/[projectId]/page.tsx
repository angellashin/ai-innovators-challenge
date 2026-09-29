"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import Home from "../../workspace";
import { SECTIONS, SectionId, sectionFromHash } from "../../stages";

const NAV_GROUPS: { label: string; sections: SectionId[] }[] = [
  { label: "프로젝트", sections: ["overview", "schedule"] },
  { label: "리스크 관리", sections: ["watch", "changes"] },
  { label: "대응", sections: ["scenarios", "execute"] },
  { label: "기록", sections: ["history"] },
];

export default function ProjectWorkspacePage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const [activeSection, setActiveSection] = useState<SectionId>("overview");

  useEffect(() => {
    const syncSection = () => setActiveSection(sectionFromHash(window.location.hash));
    syncSection();
    window.addEventListener("hashchange", syncSection);
    return () => window.removeEventListener("hashchange", syncSection);
  }, []);

  return (
    <div className="project-shell">
      <aside className="shell-nav">
        <div className="shell-caption">프로젝트 작업공간</div>
        <nav aria-label="프로젝트 작업공간 메뉴">{NAV_GROUPS.map((group) => <div className="shell-nav-group" key={group.label}>
          <span className="shell-group-label">{group.label}</span>
          {SECTIONS.filter(({ id }) => group.sections.includes(id)).map(({ id, label }) => <a className={activeSection === id ? "shell-link active" : "shell-link"} aria-current={activeSection === id ? "page" : undefined} href={`#${id}`} key={id}>{label}</a>)}
        </div>)}</nav>
        <Link className="shell-back" href="/workspaces">← 프로젝트 목록</Link>
      </aside>
      <div className="shell-content"><Home initialProjectId={projectId} /></div>
    </div>
  );
}
