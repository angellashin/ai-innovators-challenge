# REPLAN MVP

설비 도입 프로젝트의 엑셀 일정을 읽고, 변경 사건을 작업별로 반영해 대응안을 비교·승인·내보내는 데모입니다. 날짜·비용·자원 판정은 결정적 계산기로 수행하며 LLM은 선택적 설명·도구 호출에만 사용합니다.

## 외부 변화 중심 작업공간

기준 Excel에 현장·작업을 연결하고 기상 예보, 국가·지역 공휴일, 등록된 공지·뉴스를 주기적으로 확인합니다.
외부 근거와 영향 작업 후보를 표시하고, 날짜가 명확한 작업 제약은 조건부 일정으로 자동 계산합니다.
공지의 발행일을 지연 일수로 바꾸지 않으며 적용 여부·효력일이 불분명하면 확인을 요청합니다.
분석 결과는 자동 갱신되고, 외부 근거·실행 조건을 확인한 뒤 승인한 일정과 근거 시트를 Excel로 내보냅니다.
협력사 메일 샘플은 합성 보조 입력이며 실제 자동 수신은 아직 연결되지 않습니다.

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

## 범위와 주의

- 데모용 단일 공유 토큰과 SQLite 파일은 프로젝트 운영팀 공용 계정 하나를 모델링합니다. 협력사는 계정을 만들지 않습니다. 인터넷 공개 배포에는 안전한 팀 계정 인증, 자격 증명 관리, 백업, 영속 볼륨, HTTPS 프록시와 운영 감시가 추가로 필요합니다.
- 원본 엑셀은 수정하지 않습니다. 원본 바이트는 서버의 `uploads/`에 별도로 보존하며 인증된 `/api/projects/{project_id}/imports/{import_id}/original`에서 다시 받을 수 있습니다. 업로드 미리보기와 확정 일정은 SQLite에 저장하고 내보내기는 새 `.xlsx`를 만듭니다.
- REPLAY 합성 사건과 LIVE 공개 소스는 출처와 시각을 분리합니다. 현재 공식 페이지의 변경은 정책 적용 확정이 아니라 검토 사건으로만 취급합니다.
- 제공 키가 없어도 모든 일정 계산과 REPLAY 데모가 동작합니다. 실제 유료 LLM 응답 및 외부 공개 API의 운영 가용성은 별도 현장 점검이 필요합니다.
