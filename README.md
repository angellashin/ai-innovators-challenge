# REPLAN MVP

설비 도입 프로젝트의 엑셀 일정을 읽고, 협력사 통보와 외부 감시에서 발견한 변경을 작업별로 반영해 대응안을 비교·승인·내보내는 데모입니다. 에이전트는 해석·연결·질문·초안을 맡고, 날짜·비용·일정은 결정적 계산기 도구가 계산하며, 확정·발송은 사람이 맡습니다.

## 협력사 통보와 외부 감시

핵심 흐름은 **Excel 업로드 → 에이전트의 감시 계획 제안·사람의 수락 → 협력사 통보 수신 → 해석·영향 작업 확인(모호하면 질문) → 일정 계산 → 밀린 기간의 공휴일·날씨 재점검 → L2 실제 사례 근거 연결 → 대응안 비교 → 조건 확인 → 승인 → 수정 Excel → 계속 감시**입니다. 외부 주기 감시에서 새 변화가 감지되어도 해석·영향 확인 단계부터 같은 흐름에 합류합니다.

두 입력 경로는 병렬입니다. 협력사 진행 메시지는 수신 직후 처리하는 핵심 트리거이며, 현재는 붙여넣기·합성 데모 통보로 입력합니다. 실제 메일 자동 수신은 아직 연결되지 않았습니다. 날씨는 6시간, 정책·공식 공지와 공개 기업 소식은 12시간 주기를 초기 감시 계획으로 제안하며, 이는 데이터 공급자의 갱신 보장이 아니라 사람이 조정·수락할 설정입니다. 등록 출처의 근거와 영향 작업 후보를 표시하되, 공지 발행일이나 L2 기사 속 지연 일수를 일정 지연으로 바꾸지 않습니다. 적용 여부·효력일이 불분명하면 질문하고, 승인된 일정과 근거를 새 Excel로 내보냅니다.

설정·동작 범위·데이터 출처는 [외부 변화 워크플로](docs/EXTERNAL_RISK_WORKFLOW.md)를 참고하세요.
오프라인 검증: `python scripts/evaluate_external.py`.

## 팀원 시작하기

```sh
git clone https://github.com/angellashin/ai-innovators-challenge.git
cd ai-innovators-challenge
cp .env.example .env
```

`.env`의 `REPLAN_DEMO_TOKEN`을 로컬용 값으로 바꾸세요. LLM 없이도 Excel/REPLAY 데모는 동작합니다. 실제 대회 키가 필요한 경우에만 `API_KEY`와 승인된 `LLM_MODEL` 별칭을 서버 환경에 추가하세요. **키가 있는 `.env`와 프로젝트 데이터는 커밋하지 않습니다.** Docker를 쓴다면 `docker compose up --build`로 API·worker·web을 함께 실행하고 `http://localhost:3000`을 여세요. 데모 토큰은 브라우저에 입력하지 않으며 Next.js 서버 프록시가 백엔드 요청에만 주입합니다.

| 경로 | 역할 |
| --- | --- |
| `apps/web/` | Next.js 화면과 제품용 브랜드 에셋 |
| `services/api/app/` | FastAPI, Excel 처리, 이벤트·일정 계산, LLM 어댑터, worker |
| `tests/` | API·import·계산·수집·agent 회귀 테스트 |
| `scripts/` | REPLAY 평가와 선택적 게이트웨이 점검 |
| `assets/brand/replan/` | 승인된 로고 원본과 정규화 파일 |
| `REPLAN_PROJECT_MASTER.md` | 제품 기획·데모 계약 |
| `DESIGN.md` | 앞으로의 화면 구현 기준과 미결정 사항 |

연동 범위와 운영 전 결정사항은 [`docs/INTEGRATION_MATRIX.md`](docs/INTEGRATION_MATRIX.md)에 기록합니다. GitHub Actions는 Python 테스트·REPLAY 평가·웹 빌드·Compose 설정을 PR마다 검증합니다.

현재 저장소는 단일 프로젝트·공유 데모 토큰·SQLite 기반의 MVP입니다. 인터넷 공개 운영용 인증/권한/백업을 갖춘 서비스로 보지 마세요. UI를 수정할 때는 [DESIGN.md](DESIGN.md)를 먼저 읽고, 변경 전후의 핵심 흐름을 테스트하세요. 로컬 `.lazyweb/` 연구 산출물은 생성 자료와 제3자 참고 이미지를 포함해 Git에 올리지 않고, 실행 가능한 디자인 기준만 `DESIGN.md`에 남깁니다.

## 로컬 실행

Python 3.9 이상과 Next.js를 실행할 Node.js가 필요합니다. 세 터미널에서 아래 순서로 실행하세요.

```sh
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
export REPLAN_DATA_DIR=.data
export REPLAN_DEMO_TOKEN=local-demo-token
./.venv/bin/uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8000
```

```sh
export REPLAN_DATA_DIR=.data
PYTHONPATH=services/api ./.venv/bin/python -m app.worker
```

```sh
cd apps/web
npm ci
export REPLAN_BACKEND_URL=http://localhost:8000
export REPLAN_DEMO_TOKEN=local-demo-token
npm run dev
```

`http://localhost:3000`에서 데모로 진입한 뒤 `REPLAN_demo_inputs.xlsx`를 업로드하세요. 미리보기 확인 후 기준 버전을 만들 수 있습니다. 변경 메시지를 등록하면 먼저 추출된 영향 작업과 날짜를 확인하고, 확인한 뒤 영향 분석을 실행합니다. 최초 분석은 비용 한도 없이도 실행되며, 대응안 비교 화면에서 필요할 때만 비용 한도를 추가합니다. 조건 업무를 확인하고 승인해야 새 일정 버전을 확정할 수 있습니다.

### 데모의 세 장면

- **H04:** 반입 지연 통보를 해석해 통보 지연 21일을 계산하고, 밀린 기간의 공휴일 14일을 재점검합니다. 무대응 완료일 2028-01-25와 대응안 완료일 2028-01-11(14일 회복)을 비교한 뒤 협력사 협의 메일 초안을 만듭니다.
- **H02:** 규제가 언급된 통보에 L2 실제 사례를 근거로 연결해 적용 가능성을 추론합니다. 적용이 확정된 지연으로 계산하지 않고 조건부 시나리오와 확인 질문으로 남깁니다.
- **V08:** 작업 번호가 없는 한국어 통보에서 원문 근거를 인용한 작업 후보를 제안합니다. 사람이 대상을 확인하기 전에는 일정 계산을 하지 않고 멈춥니다.

### 데모 데이터의 역할

| 층 | 자료 | 사용 범위 |
| --- | --- | --- |
| L1 | 합성 hero 프로젝트 일정 | 데모의 기준 일정과 결정적 일정 계산 입력 |
| L2 | 실제 리스크 사례 | 에이전트가 검색·인용하는 참고 근거. 기사 속 지연 일수는 계산에 사용하지 않음 |
| L3 | 일정 영향 확인 사례 | 채점 전용 정답. 에이전트는 접근할 수 없음 |

데모의 협력사 통보 데이터는 합성이지만, 협력사 통보 자체는 서비스의 핵심 변경 트리거입니다.

Docker를 사용할 경우 `REPLAN_DEMO_TOKEN`을 설정한 뒤 `docker compose up --build`로 API·worker·web을 함께 실행할 수 있습니다. SQLite 데이터는 `replan_data` 볼륨에 유지됩니다. 깨끗한 데모가 필요하면 기존 볼륨을 지우는 대신 다른 `REPLAN_DATA_DIR`의 로컬 실행 환경 또는 별도 Compose 프로젝트 이름을 사용하세요.

API 문서는 `http://localhost:8000/docs`에 있습니다. 분석/수집은 큐에 등록되며 별도 worker가 처리합니다. 감시 계획은 처음에 꺼져 있으므로 좌표·출처·간격을 검토하고 직접 활성화해야 합니다. 기상 일정 이벤트는 사용자가 `weather_limits.max_wind_speed_kmh` 또는 `weather_limits.max_precipitation_mm`를 설정한 경우에만 만듭니다. 외부 수집 실패는 스냅샷 오류로 남고 안전 판정으로 취급하지 않습니다.

### P1 운영 입력·연동

- `/api/projects/{project_id}/documents`는 PDF/TXT/MD/EML 원본을 저장하고 `202 QUEUED`를 반환합니다. 같은 SHA-256 문서는 중복 큐잉하지 않으며, worker가 파싱을 끝내면 `SUCCEEDED/FAILED` 상태와 검토용 이벤트를 기록합니다. 실패 문서는 `/documents/{document_id}/retry`로 재처리할 수 있습니다. PDF는 `pypdf`로 텍스트만 추출합니다.
- `/api/projects/{project_id}/mail-account`는 IMAP/Gmail/Outlook 연결 메타데이터만 저장합니다. 비밀번호·OAuth 토큰은 저장하지 않으며 실제 메일 수집은 별도 인증 작업이 필요합니다.
- `/api/projects/{project_id}/public-feeds`로 허용 호스트의 RSS/Atom URL을 등록하고 기존 감시계획에 합칠 수 있습니다. 피드 내용은 여전히 검토 사건입니다.
- 공급사 휴무일은 `/supplier-calendars`로 등록되어 시뮬레이터의 작업 가능일에서 제외됩니다.
- 알림은 `/notifications`의 인앱 기록으로 동작하고, 이메일·웹훅·Slack 채널은 `DRAFT` 설정으로만 보존합니다. 외부 발송은 P1 범위에서 자동 실행하지 않습니다.
- `/site-prep`의 `equipment_installation_v1` 템플릿은 허가, 적치장, 양중 장비, 안전 브리핑 체크리스트를 생성합니다. 시나리오 대응 업무의 `due_at`은 옵션의 `decision_lead_days` 또는 기본 3일 기준으로 자동 산출됩니다.

등록 공지는 서버가 허용한 호스트에서만 가져옵니다. 기본 허용 호스트는 `environment.ec.europa.eu`이며, 다른 공식 출처는 서버의 `REPLAN_ALLOWED_SOURCE_HOSTS`에 명시적으로 추가해야 합니다. 페이지 내용은 정책 적용 확정이 아니라 검토 대상입니다.

## 검증과 선택적 LLM

```sh
./.venv/bin/python -m pytest -q
./.venv/bin/python scripts/evaluate_replay.py
# 스크립트는 현재 작업 디렉터리와 무관하게 저장소의 기본 엑셀을 찾습니다.
./.venv/bin/python scripts/evaluate_replay.py --input /path/to/REPLAN_demo_inputs.xlsx
```

`scripts/evaluate_replay.py`는 인터넷/LLM 호출 없이 제공 엑셀의 baseline과 E01~E05 경계를 재현합니다. 실제 대회 API는 `API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`을 서버에만 설정하세요. `python scripts/smoke_llm.py`는 모델 목록 조회만 하며, `--roundtrip`을 명시해야 생성 및 도구 호출을 테스트합니다. 분석 중 유료 호출을 허용하려면 `REPLAN_PAID_CALLS_ENABLED=true`를 별도로 지정합니다. 기본 일일 유료 실행 상한은 20건(`REPLAN_MAX_PAID_RUNS_PER_DAY`)이고 비용 단가가 검증되지 않은 호출은 0원이 아닌 `UNKNOWN`으로 기록합니다.

협력사 메시지는 규칙으로 먼저 해석하고, 규칙이 실패했을 때만 설정된 LLM에 작업 ID·원문 날짜·정확한 인용을 요구합니다. 검증된 해석도 사람 확인 전에는 잠정 patch입니다. 유료 호출이 켜져 있으면 이어서 `run_agent`가 작업 후보, 일정 계산, 밀린 기간 외부 제약, L2 사례, 대응안 도구를 필요한 순서로 선택합니다. 호출 결과와 멈춘 이유는 분석 화면의 **에이전트 판단 과정**에 저장됩니다. LLM이 꺼져 있으면 기존 규칙 순서가 작동합니다. 메일은 검증된 계산 도구의 날짜·일수·비용만 남긴 발송 전 초안입니다. 규제·인허가·인력·물류 사유는 `search_risk_signals`가 찾은 L2 실제 사례의 URL·발행일을 참고 근거로 제시하며 사례의 지연 일수는 계산에 사용하지 않습니다. 통보 이후 발행된 사례는 당시 판단 근거에서 제외합니다. 규제 적용 날짜나 기간이 확인되지 않은 조건부 시나리오는 추가 지연을 계산하지 않고 입력을 요청합니다. 감시 계획은 업로드한 Excel의 국가·기간·공정·위험 태그·협력사에서 제안하고 각 항목을 수락·수정·제외한 후 활성화합니다. 야외 작업 분류와 기상 임계값은 모두 현장 확인 대상입니다.

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

## 범위와 주의

- 데모용 단일 공유 토큰과 SQLite 파일은 프로젝트 운영팀 공용 계정 하나를 모델링합니다. 협력사는 계정을 만들지 않습니다. 인터넷 공개 배포에는 안전한 팀 계정 인증, 자격 증명 관리, 백업, 영속 볼륨, HTTPS 프록시와 운영 감시가 추가로 필요합니다.
- 원본 엑셀은 수정하지 않습니다. 원본 바이트는 서버의 `uploads/`에 별도로 보존하며 인증된 `/api/projects/{project_id}/imports/{import_id}/original`에서 다시 받을 수 있습니다. 업로드 미리보기와 확정 일정은 SQLite에 저장하고 내보내기는 새 `.xlsx`를 만듭니다.
- REPLAY 합성 사건과 LIVE 공개 소스는 출처와 시각을 분리합니다. 현재 공식 페이지의 변경은 정책 적용 확정이 아니라 검토 사건으로만 취급합니다.
- 제공 키가 없어도 모든 일정 계산과 REPLAY 데모가 동작합니다. 실제 유료 LLM 응답 및 외부 공개 API의 운영 가용성은 별도 현장 점검이 필요합니다.
- 평가 통보와 정답 대부분은 LLM으로 작성한 합성 데이터이며 사람이 독립 검토하지 않았습니다. 표본이 작고 각 설정을 1회만 실행했으므로 위 수치를 일반 성능으로 해석할 수 없습니다. 대응안의 단축 일수와 비용도 합성 값입니다.
- 규제는 통보 언급이나 등록 출처에서만 감지하며 인터넷 전반을 조사하지 않습니다. 메일 자동 수신은 미연결이고, 먼 날짜의 날씨는 계절 통계 기반 조건부 위험입니다. 공휴일은 달력일 기준으로 다룹니다.

향후에는 실제 협력사 메일로 흐름을 검증하고, 사람이 작성·검토한 평가 세트를 늘리며, 규제 감지 출처와 L3 작업 후보 Recall을 개선해야 합니다.
