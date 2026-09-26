# REPLAN MVP

> **처음이라면**: [인수인계 요약(HANDOFF.md)](docs/HANDOFF.md) · [데모 가이드(DEMO_GUIDE.md)](docs/DEMO_GUIDE.md)

설비 도입 프로젝트의 엑셀 일정을 읽고, 협력사 통보와 외부 공지에서 생긴 변경이 어느 작업에 닿는지 찾아 대응안을 비교·승인·내보내는 데모입니다. **에이전트**는 해석·원인 연결·조사·질문·초안을 맡고, **결정적 계산기**가 날짜·비용·일정을 계산하며, **사람**이 확인·승인·발송을 맡습니다.

## 대표 장면

### X2 — 통보에 없던 숨은 위험을 에이전트가 찾음

협력사가 "셀 설비 서보 모터 자석 부품(P-A1)이 중국 수출 허가를 기다리느라 T042 통관이 1주 늦어진다"고 알립니다. 규칙만으로는 **완료일 변화 0일**(T042는 여유가 큼)이라 할 일이 없습니다. 사람이 **조사 시작**을 누르면 에이전트가:

1. 같은 시기 등록 공지("중국산 희토류 자석은 선적마다 수출 허가, 검토 최대 60일")와 통보 사유를 연결하고,
2. 구매 목록에서 통보에 없던 같은 협력사·원산지 품목 **P-B**(교육 시뮬레이터 구동부 → T058)와 **P-C**(정밀 서보 스테이지 → T051)를 찾고,
3. 계산기로 T058은 여유 245일로 흡수, **T051은 여유 0일**임을 확인하고,
4. P-C 허가가 늦으면 **완료 2028-02-11(+52일)**, 영향을 피하려면 **2027-02-08까지 서류 제출**이 필요하다고 계산한 뒤,
5. 통보에 없던 품목이라 대응안 비교 대신 **확인 요청과 협력사 메일 초안**(제출 기한 포함)을 남기고 멈춥니다("확인이 필요합니다").
6. 사람이 "P-C도 해당함"을 기록하면 P-C 지연을 반영해 일정을 **다시 계산**하고(완료 2028-02-11), 가장 많이 회복하는 **추천 조합**(제어 통합·교정 집중 투입 + 시운전·통합시험 병행 준비, **2028-01-25**, 17일 회복)을 먼저 보여줍니다. 승인하면 새 일정 버전과 Excel에 반영됩니다.

화면에는 "통보 내용만 반영" 대 "에이전트 조사 후" 비교와 다음 행동 하나가 먼저 보이고, 원문 문장 강조·근거 기사(RS-019, CNBC 2025-10-15)·판단 기록(확인마다 '왜', LLM 호출 수·비용)·메일 초안은 접어 둡니다. 일정·날짜는 합성이며 기사 속 지연 기간은 계산에 쓰지 않습니다.

![X2 조사 결과](docs/demo/x2_3_investigation_result.png)
![X2 확인 후 재계산](docs/demo/x2_4_after_recalculation.png)

### H04 — 결정적 계산과 비용 효율

T045 설비 반입 완료일이 2026-12-05 → 2026-12-28로 늦어지는 통보입니다. 계산기가 통보 지연 **21일**과 밀린 기간에 새로 걸린 공휴일 **14일**을 나눠 계산하고(무대응 **2028-01-25**, 설치팀 추가·야간 작업 **2028-01-11**, 두 안 조합 2027-12-28), 에이전트는 계산이 끝난 8개 안을 **LLM 1회**로 비교·설명하고 협의 메일 초안을 씁니다. 진단 전 같은 흐름은 6회·입력 17만 토큰·$1.30이었고 지금은 1회·6천 토큰·약 $0.09입니다([진단 기록](docs/AGENT_AUDIT.md)).

![H04 영향 내역](docs/demo/H04_05_impact.png)

단계별 화면은 [데모 가이드](docs/DEMO_GUIDE.md)에 있습니다.

## 실행

```sh
cp .env.example .env          # REPLAN_DEMO_TOKEN을 로컬 값으로
scripts\demo.cmd              # Windows cmd (macOS·Linux: scripts/demo.sh)
```

`http://localhost:3000` → **데모 보기 → 워크스페이스 열기 → 프로젝트 만들기**. 데모 스크립트는 평소 스택을 멈추고(데이터 유지) 별도의 깨끗한 데이터로 `REPLAN_LLM_MODE=replay` 스택을 띄웁니다. 재생 모드는 녹화된 응답(`data/llm_replay/hero_demo.json`)으로 에이전트를 돌려 **키·네트워크·비용 없이** 두 장면을 끝까지 보여줍니다. 키 없이 기본 모드로 띄우면 규칙과 계산기만 동작합니다. 실제 LLM은 서버 환경에 `API_KEY`·`LLM_MODEL`·`LLM_BASE_URL`과 `REPLAN_PAID_CALLS_ENABLED=true`를 둘 때만 호출합니다. **키가 있는 `.env`와 프로젝트 데이터는 커밋하지 않습니다.**

Docker 없이 실행하려면 API(`uvicorn app.main:app --app-dir services/api`), worker(`PYTHONPATH=services/api python -m app.worker`), 웹(`apps/web`에서 `npm run dev`)을 같은 `REPLAN_DATA_DIR`·`REPLAN_DEMO_TOKEN`으로 띄웁니다(웹에는 `REPLAN_BACKEND_URL`). API 문서는 `http://localhost:8000/docs`.

| 경로 | 역할 |
| --- | --- |
| `apps/web/` | Next.js 화면(6단계: 개요 → 일정 → 변경 → 대응안 → 실행 → 이력) |
| `services/api/app/` | FastAPI, Excel 처리, 일정 계산기, 에이전트 런타임·조사 도구, worker |
| `scripts/` | REPLAY 평가, 에이전트 흐름 녹화·재생(`agent_flows.py`), 평가 스크립트 |
| `data/` | 합성 hero 일정(L1), 실제 리스크 사례(L2), 채점 전용 정답(L3), 데모 통보·공지, 녹화된 LLM 응답 |
| `docs/` | 데모 가이드, 에이전트 진단·개선 기록, 루프 설계, 외부 변화·연동 문서 |

데이터 층: L1 합성 일정은 계산 입력, L2 실제 사례는 에이전트가 검색·인용하는 참고 근거(기사 속 지연 일수는 계산에 쓰지 않음), L3는 채점 전용 정답으로 에이전트와 서비스 이미지에서 제외합니다.

## 검증과 선택적 LLM

```sh
./.venv/bin/python -m pytest -q
./.venv/bin/python scripts/evaluate_replay.py        # REPLAY 16개 검사, 인터넷·LLM 없음
./.venv/bin/python scripts/agent_flows.py --mode replay   # 녹화된 에이전트 흐름 9개 재생, 비용 0
```

`REPLAN_LLM_MODE=record`는 녹화본에 없는 요청만 실제로 호출해 저장하고, `replay`는 녹화본으로만 답합니다(프롬프트·도구·입력이 바뀌면 `replay miss`로 멈춤). 모의·실제 평가 명령과 기준은 아래와 같습니다.

```sh
python scripts/evaluate_agent.py --mode mock --output artifacts/agent_evaluation.json
python scripts/evaluate_hero.py --project data/l1_project/hero_battery_factory_project.xlsx --ground-truth data/l3_ground_truth/ground_truth.json --mapping data/l1_project/l3_to_wbs_mapping.json --eval-dir data/evaluation --output artifacts/hero_evaluation.json
# .env에 LLM_BASE_URL, LLM_MODEL, API_KEY, REPLAN_PAID_CALLS_ENABLED=true를 설정한 뒤에만 실행
python scripts/evaluate_agent_real.py --output artifacts/agent_evaluation_real.json
```

모의 평가는 원본 9건·표현 변형 11건·L3 적격 사례 6건과 H01~H08·V01~V11의 도구 선택, 질문 중단, 초안 수치, 미확정 규제 처리를 검사하며 실제 유료 호출은 0건입니다. 모의 점수는 인터페이스 검증용이며 실제 모델 품질의 추정치가 아닙니다. 실제 모델의 도구 선택에 따라 호출 수가 달라지므로 유료 평가 전에는 `REPLAN_MAX_PAID_RUNS_PER_DAY`를 예상 호출 수 이상으로 명시하고, 출력의 `expected_paid_call_count`와 `llm_call_count`를 확인해야 합니다. 전체 실제 평가의 기록된 호출 수는 약 **81회**이며 게이트웨이의 JSON 형식 재시도 시 HTTP 요청이 더 늘 수 있습니다. 결과에는 모델명·UTC 실행 시각·호출 수가 저장됩니다. 정답 파일은 채점 코드만 읽으며 에이전트에는 원문·작업 목록·동일 사건을 제외한 L2 참고 사례만 전달합니다. 서비스 이미지는 L2 자료와 데모 입력만 포함하고 `data/l3_ground_truth`, L3 매핑, hero 정답 파일을 포함하지 않습니다.

### 실제 LLM 평가 결과

아래 수치는 `bedrock-gpt-5.6-sol`로 각 설정을 **1회** 실행한 결과입니다. 3-A와 3-C는 에이전트를 포함한 비교 설정이며, 3-C는 L3 작업 후보를 최대 3개로 제한합니다.

| 평가 | 규칙만 | 3-A | 3-C |
| --- | ---: | ---: | ---: |
| 협력사 통보 변형 11건 결과 판정 | 54.5% | 100% | 100% |
| 협력사 통보 변형 11건 작업 식별 | 63.6% | 90.9% | 100% |
| 원문에 없는 날짜 생성 | 0% | 0% | 0% |
| L3 실제 사례 6건 영향 작업 F1 | 0% | 45.2% | 38.1% |

통보 변형 11건에는 표현 변경과 작업 번호 없는 한국어 통보 4건이 포함됩니다. 원본 통보 9건은 세 방식 모두 100%였습니다. L3 영향 작업의 3-A는 Precision 31.8%·Recall 77.8%, 3-C는 Precision 33.3%·Recall 44.4%였습니다. **3-C의 후보 3개 제한은 Precision을 실질적으로 높이지 못하고 Recall을 낮췄습니다.**

3-C 에이전트 워크플로 19건의 원본 채점은 필수 도구 호출 84.2%, 모호 사례 중단 84.2%, 실행 실패 3건입니다. 사람 검토 대기 상태 `NEEDS_APPROVAL`, `PATCH_PROPOSED`, `PATCH_RECHECKED_PENDING_REVIEW`를 정상 종료로 분류해 **같은 실행 결과를 오프라인 재채점**하면 실행 실패 0건, 두 지표 모두 100%입니다. 재채점은 추가 LLM 호출이 아니며 원본 수치를 대체하지 않습니다. 초안에 계산 결과에 없는 날짜·금액은 0%, 미확정 규제를 확정 지연으로 처리한 비율도 0%였습니다.

## 테스트·평가용 시나리오

데모 앞면에는 두 장면만 둡니다. 아래 자료는 회귀 테스트와 평가에 쓰며 화면의 합성 통보 목록에서도 불러올 수 있습니다.

- **H01~H08**(`data/hero_demo/supplier_messages.json`): 협력사 통보 원본 9건. 제작·FAT·반입 지연, 규제 언급(H02), 완료일 미정(H06), 연락처만 변경(H07), 정정 통보(H08).
- **V01~V11**(`supplier_message_variants.json`): 표현 변형 11건. 작업 번호 없는 한국어 통보(V08 등)는 에이전트가 원문 인용과 함께 작업 후보를 제시하고 사람이 지정한 뒤에만 계산합니다.
- **X1-A**(EU 역외 설치·시운전 기술자 취업 허가 확인 공지, 근거 RS-001), **X1-B**(헝가리 인허가 처리 지연, 여유로 흡수되어 기록만), **X2-대조**(사유가 달라 연결하지 않음): `data/hero_demo/external_loop_signals.json`.
- **REPLAN_demo_inputs.xlsx**: 직접 업로드 예시(20개 작업, E01~E05)이자 REPLAY 16개 검사 입력.
- 감시 계획·공휴일·기상·등록 공지의 설정과 한계: [외부 변화 워크플로](docs/EXTERNAL_RISK_WORKFLOW.md), 운영 연동 범위: [연동 매트릭스](docs/INTEGRATION_MATRIX.md).

## 범위와 주의

- 데모용 단일 공유 토큰과 SQLite 파일은 프로젝트 운영팀 공용 계정 하나를 모델링합니다. 협력사는 계정을 만들지 않습니다. 인터넷 공개 배포에는 안전한 팀 계정 인증, 자격 증명 관리, 백업, 영속 볼륨, HTTPS 프록시와 운영 감시가 추가로 필요합니다.
- 원본 엑셀은 수정하지 않습니다. 원본 바이트는 서버의 `uploads/`에 별도로 보존하며 인증된 `/api/projects/{project_id}/imports/{import_id}/original`에서 다시 받을 수 있습니다. 업로드 미리보기와 확정 일정은 SQLite에 저장하고 내보내기는 새 `.xlsx`를 만듭니다.
- REPLAY 합성 사건과 LIVE 공개 소스는 출처와 시각을 분리합니다. 현재 공식 페이지의 변경은 정책 적용 확정이 아니라 검토 사건으로만 취급합니다.
- 제공 키가 없어도 모든 일정 계산과 REPLAY 데모가 동작합니다. 실제 유료 LLM 응답 및 외부 공개 API의 운영 가용성은 별도 현장 점검이 필요합니다.
- 평가 통보와 정답 대부분은 LLM으로 작성한 합성 데이터이며 사람이 독립 검토하지 않았습니다. 표본이 작고 각 설정을 1회만 실행했으므로 위 수치를 일반 성능으로 해석할 수 없습니다. 대응안의 단축 일수와 비용도 합성 값입니다.
- 규제는 통보 언급이나 등록 출처에서만 감지하며 인터넷 전반을 조사하지 않습니다. 메일 자동 수신은 미연결이고, 먼 날짜의 날씨는 계절 통계 기반 조건부 위험입니다. 공휴일은 달력일 기준으로 다룹니다.

향후에는 실제 협력사 메일로 흐름을 검증하고, 사람이 작성·검토한 평가 세트를 늘리며, 규제 감지 출처와 L3 작업 후보 Recall을 개선해야 합니다.
