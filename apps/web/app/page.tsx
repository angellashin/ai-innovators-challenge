"use client";

import Image from "next/image";
import Link from "next/link";

const fieldScenes = [
  { src: "/images/landing/field-fabrication.jpg", alt: "제작 현장에서 도면을 검토하는 엔지니어" },
  { src: "/images/landing/field-logistics.jpg", alt: "항만에서 중량 설비 운송을 진행하는 현장" },
  { src: "/images/landing/field-commissioning.jpg", alt: "플랜트 설비를 점검하는 시운전 엔지니어" },
];

const operatingLoop = [
  ["01", "일정 올리기", "Excel 일정을 연결하면 먼저 취약한 품목과 작업을 알려줍니다."],
  ["02", "숨은 영향 확인", "협력사 통보와 관련 공지를 연결해 메일에 없는 위험까지 계산합니다."],
  ["03", "대응 결정", "담당자가 근거와 조건을 확인한 뒤 새 일정을 승인합니다."],
];

export default function LandingPage() {
  return (
    <main className="landing-page">
      <header className="landing-nav">
        <Link className="brand-lockup" href="/" aria-label="REPLAN 홈">
          <Image src="/brand/replan-wordmark.png" alt="REPLAN" width={1500} height={350} priority />
        </Link>

        <nav className="landing-nav-links" aria-label="주요 메뉴">
          <a href="#product">무엇을 찾나요</a>
          <a href="#workflow">이용 흐름</a>
          <a href="#use-case">대표 사례</a>
        </nav>

        <Link className="landing-nav-cta" href="/demo">
          데모 보기 <span aria-hidden="true">↗</span>
        </Link>
      </header>

      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-hero-media" aria-hidden="true">
          {fieldScenes.map((scene, index) => (
            <div
              className={`landing-hero-frame landing-hero-frame-${index + 1}`}
              key={scene.src}
              style={{ position: "absolute" }}
            >
              <Image src={scene.src} alt="" fill sizes="100vw" priority={index === 0} />
            </div>
          ))}
        </div>
        <div className="landing-hero-scrim" aria-hidden="true" />

        <div className="landing-hero-copy">
          <p className="landing-kicker"><span aria-hidden="true" /> 설비 프로젝트 일정 위험 탐지</p>
          <h1 id="landing-title">메일에는 영향 없음.<br />일정에는 52일 위험.</h1>
          <p>
            REPLAN은 협력사 통보에 없는 관련 품목까지 확인해 숨은 일정 위험을 찾습니다.
            근거와 대응안을 보여주고, 일정 변경은 담당자가 승인합니다.
          </p>
          <div className="landing-hero-actions">
            <Link className="landing-primary-button" href="/demo">52일 위험 사례 보기 <span aria-hidden="true">→</span></Link>
            <a className="landing-quiet-link" href="#workflow">이용 흐름 보기</a>
          </div>
          <small className="landing-demo-note">대표 사례와 녹화된 AI 응답으로 서비스 흐름을 확인할 수 있습니다.</small>
        </div>

        <div className="landing-hero-index" aria-hidden="true"><span>01</span><i /><span>03</span></div>
      </section>

      <section className="landing-statement" id="product">
        <div className="landing-section-label"><span>01</span> Product</div>
        <div>
          <p className="landing-overline">메일에 적히지 않은 위험까지</p>
          <h2>영향 없는 통보처럼 보여도<br />다른 품목은 늦어질 수 있습니다.</h2>
          <p className="landing-statement-copy">
            한 품목의 지연은 기존 일정 안에서 흡수될 수 있습니다. 하지만 같은 수출 허가를 기다리는 다른 품목은 일정 완충 기간이 없을 수 있습니다. REPLAN은 이 연결을 찾아 담당자가 확인할 질문과 기한을 제시합니다.
          </p>
        </div>
      </section>

      <section className="landing-workflow" id="workflow" aria-labelledby="workflow-title">
        <div className="landing-workflow-head">
          <div className="landing-section-label"><span>02</span> 사용 방법</div>
          <h2 id="workflow-title">세 가지 행동으로 시작합니다</h2>
          <p>위험을 찾는 일은 AI가 돕고, 사실 확인과 일정 변경은 담당자가 결정합니다.</p>
        </div>
        <ol className="landing-loop">
          {operatingLoop.map(([number, title, description]) => (
            <li key={number}>
              <span>{number}</span>
              <h3>{title}</h3>
              <p>{description}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="landing-product-story" id="use-case">
        <div className="landing-story-copy">
          <div className="landing-section-label"><span>03</span> 대표 사례</div>
          <p className="landing-overline">대표 사례 데모</p>
          <h2>0일에서<br />52일로.</h2>
          <p>협력사 메일에 적힌 부품만 계산하면 완료일은 그대로입니다. REPLAN은 같은 허가가 필요한 다른 부품을 찾아 최악의 경우 52일 지연될 수 있음을 보여줍니다.</p>
          <Link className="landing-inline-link" href="/demo">사례 직접 살펴보기 <span aria-hidden="true">↗</span></Link>
        </div>

        <div className="landing-product-card" aria-label="REPLAN 대표 사례의 영향 분석 요약">
          <div className="product-card-topline">
            <div><span className="live-dot" /> 협력사 통보 분석</div>
            <span>대표 사례 데이터</span>
          </div>
          <div className="product-card-change">
            <span>희토류 자석 부품 · 수출 허가 지연</span>
            <strong>메일에 없던 부품도<br />같은 허가가 필요할 수 있습니다.</strong>
            <p>담당자가 협력사에 적용 여부를 확인한 뒤 일정을 다시 계산합니다.</p>
          </div>
          <div className="product-card-options">
            <article>
              <span>메일 내용만 반영</span>
              <h3>완료일 영향 없음</h3>
              <dl><div><dt>완료 예정</dt><dd>2027-12-21</dd></div><div><dt>변화</dt><dd>0일</dd></div></dl>
            </article>
            <article className="recommended-option">
              <span>숨은 위험 조사 후 · 확인 필요</span>
              <h3>최악의 경우 +52일</h3>
              <dl><div><dt>완료 예정</dt><dd>2028-02-11</dd></div><div><dt>확인할 품목</dt><dd>P-C</dd></div></dl>
            </article>
          </div>
          <div className="product-card-footer"><span>확인 기한 2027-02-08</span><b>협력사 확인 후 대응 결정 →</b></div>
        </div>
      </section>

      <section className="landing-principles">
        <article><span>01</span><h3>근거 확인</h3><p>어떤 통보와 공지를 연결했는지 확인할 수 있습니다.</p></article>
        <article><span>02</span><h3>일정 계산</h3><p>날짜와 비용은 계산기로 산출합니다.</p></article>
        <article><span>03</span><h3>사람의 결정</h3><p>확인과 승인 전에는 일정을 바꾸지 않습니다.</p></article>
      </section>

      <section className="landing-final-cta">
        <p className="landing-overline">REPLAN 데모</p>
        <h2>메일에 없는 위험을<br />직접 확인해보세요.</h2>
        <Link className="landing-primary-button landing-primary-button-light" href="/demo">대표 사례 시작하기 <span aria-hidden="true">→</span></Link>
      </section>

      <footer className="landing-footer">
        <Link className="brand-lockup" href="/" aria-label="REPLAN 홈">
          <Image src="/brand/replan-wordmark.png" alt="REPLAN" width={1500} height={350} />
        </Link>
        <p>Project change control for complex operations.</p>
        <span>© 2026 REPLAN</span>
      </footer>
    </main>
  );
}
