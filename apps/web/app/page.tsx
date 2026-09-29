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
  ["02", "연결된 영향 확인", "기준 일정과 외부 신호를 연결해 놓치기 쉬운 리스크까지 확인합니다."],
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
          <p className="landing-kicker"><span aria-hidden="true" /> PROJECT RISK MANAGEMENT</p>
          <h1 id="landing-title">일정에 영향 주기 전에,<br />리스크를 먼저 찾습니다.</h1>
          <p>
            REPLAN은 기준 일정과 외부 신호를 연결해 프로젝트에 영향을 줄 수 있는 리스크를 찾습니다.
            근거와 대응안을 보여주고, 일정 반영은 담당자가 승인합니다.
          </p>
          <div className="landing-hero-actions">
            <Link className="landing-primary-button" href="/demo">대표 사례 데모 보기 <span aria-hidden="true">→</span></Link>
            <a className="landing-quiet-link" href="#workflow">이용 흐름 보기</a>
          </div>
          <small className="landing-demo-note">대표 사례의 분석 흐름으로 서비스 사용 방식을 확인할 수 있습니다.</small>
        </div>

        <div className="landing-hero-index" aria-hidden="true"><span>01</span><i /><span>03</span></div>
      </section>

      <section className="landing-statement" id="product">
        <div className="landing-section-label"><span>01</span> Product</div>
        <div>
          <p className="landing-overline">일정에 보이지 않던 리스크까지</p>
          <h2>문제가 드러난 뒤가 아니라<br />영향이 커지기 전에 대응합니다.</h2>
          <p className="landing-statement-copy">
            한 작업의 지연이 다른 작업과 품목에 어떤 영향을 주는지 기준 일정에서 확인합니다. REPLAN은 외부 근거와 작업 간 연결을 찾아 담당자가 확인할 질문과 대응 기한을 제시합니다.
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
          <p className="landing-overline">배터리 공장 건설</p>
          <h2>리스크를 발견하고<br />대응을 결정합니다.</h2>
          <p>기준 일정에 외부 신호를 연결하면, 현재 일정으로 흡수되는 영향과 추가 확인이 필요한 작업을 구분해 보여줍니다.</p>
          <Link className="landing-inline-link" href="/demo">사례 직접 살펴보기 <span aria-hidden="true">↗</span></Link>
        </div>

        <div className="landing-product-card" aria-label="REPLAN 대표 사례의 영향 분석 요약">
          <div className="product-card-topline">
            <div><span className="live-dot" /> 외부 리스크 분석</div>
            <span>대표 사례</span>
          </div>
          <div className="product-card-change">
            <span>수출 허가 · 공급망 영향</span>
            <strong>연결된 작업까지 확인해<br />일정 영향을 비교합니다.</strong>
            <p>담당자가 근거와 적용 범위를 확인한 뒤 대응안을 선택합니다.</p>
          </div>
          <div className="product-card-options">
            <article>
              <span>현재 일정 기준</span>
              <h3>예상 영향 없음</h3>
              <dl><div><dt>완료 예정</dt><dd>2027-12-21</dd></div><div><dt>변화</dt><dd>0일</dd></div></dl>
            </article>
            <article className="recommended-option">
              <span>추가 확인이 필요한 경우</span>
              <h3>일정 영향 확인 필요</h3>
              <dl><div><dt>완료 예정</dt><dd>2028-02-11</dd></div><div><dt>확인할 품목</dt><dd>P-C</dd></div></dl>
            </article>
          </div>
          <div className="product-card-footer"><span>확인 기한 2027-02-08</span><b>협력사 확인 후 대응 결정 →</b></div>
        </div>
      </section>

      <section className="landing-principles">
        <article><span>01</span><h3>근거 확인</h3><p>어떤 통보와 공지를 연결했는지 확인할 수 있습니다.</p></article>
        <article><span>02</span><h3>일정 계산</h3><p>날짜와 선후행 관계를 기준으로 영향을 계산합니다.</p></article>
        <article><span>03</span><h3>사람의 결정</h3><p>확인과 승인 전에는 일정을 바꾸지 않습니다.</p></article>
      </section>

      <section className="landing-final-cta">
        <p className="landing-overline">REPLAN 데모</p>
        <h2>프로젝트 리스크를<br />직접 확인해보세요.</h2>
        <Link className="landing-primary-button landing-primary-button-light" href="/demo">대표 사례 보기 <span aria-hidden="true">→</span></Link>
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
