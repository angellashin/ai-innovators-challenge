# 제출 점검표

AI Innovators Challenge @ AI Tech Day 2026 예선(PoC 온라인 심사). 주제: AWS가 제공하는 LLM API를 활용한 AI 서비스 개발. 평가 항목별로 저장소에서 근거가 되는 위치입니다.

## 목적 부합성 (10)

| 확인할 점 | 근거 위치 |
| --- | --- |
| 해결하는 문제와 대상 사용자 | [README](../README.md) 맨 위 한 문장 소개, "대표 예시" |
| AWS LLM API를 서비스의 핵심에 사용 | [README "왜 AI 에이전트인가"](../README.md#왜-ai-에이전트인가), `services/api/app/adapters/llm.py`(OpenAI 호환 chat completions 어댑터) |
| LLM을 쓰는 곳과 쓰지 않는 곳의 구분 | [README "AWS LLM API 호출 원칙과 비용 통제"](../README.md#aws-llm-api-호출-원칙과-비용-통제) |

## 데이터 활용성 (10)

| 확인할 점 | 근거 위치 |
| --- | --- |
| 합성 데이터와 실제 데이터의 구분 | [README "데이터"](../README.md#데이터), `data/l1_project/README (9).md`(합성 hero 프로젝트 설명) |
| 실제 사례를 근거로 인용 | `data/l2_risk_signals/risk_signals.json`(실제 기사 19건, 링크·발행일), `services/api/app/risk_signals.py`(정해진 검색어 `CASE_QUERIES`) |
| 실제 사례 정답으로 평가 | `data/l3_ground_truth/ground_truth.json`(12건), `scripts/evaluate_hero.py`, `data/evaluation/` |
| 실제 외부 데이터 | `data/external/*-holidays.json`(Nager.Date 응답 스냅숏), `services/api/app/adapters/sources.py`(Open-Meteo·Nager.Date·등록 공지) |
| 근거 문서 처리 | `services/api/app/evidence_rag.py`(원문 보존, 인용 가능한 문단, 해시) |
| 엑셀에 없는 원인을 근거로만 연결 | `services/api/app/briefing.py`의 `linked_cause`(에이전트가 읽은 사례가 없으면 버림), `tests/test_risk_register.py` |

## 기술적 우월성 (30)

| 확인할 점 | 근거 위치 |
| --- | --- |
| 도구를 스스로 고르는 에이전트 루프 | `services/api/app/agent/runtime.py`(도구 호출·검증·반복 차단·한 턴 여러 호출), `services/api/app/worker.py`의 `_run_investigation` |
| 결정적 계산기와 역할 분리 | `services/api/app/scheduling/simulator.py`, `shifted_external.py`, `investigation.py`(여유·조건부 일정·행동 기한) |
| 환각 방지 | `runtime.py`의 `_ground_final`(계산기에 없는 숫자 제거), `external_risks.py`의 `interpret_notice`(인용문 원문 대조) |
| 전제 조건 강제 | `worker.py`의 조사 도구(원문 값만, 여유 확인 후 조건부 계산, 사람 확인 전 비교 금지, 범위 밖 작업 거절) |
| 사전·사후 에이전트와 공유 상태 | `briefing.py`, `worker.py`의 `_auto_narrow`, `risk_register.py` |
| 비용 통제 | `worker.py`의 `_reserve_paid_attempt`(일일 한도, 자동 분류 별도 원장), `adapters/llm.py`의 record/replay |
| 근거와 결과 | [README "결과"](../README.md#결과), [에이전트 감사 기록](AGENT_AUDIT.md)(에이전트 켬·끔 비교, 비용 88% 감소) |

## 서비스 활용성·완성도 (30)

| 확인할 점 | 근거 위치 |
| --- | --- |
| 한 줄 실행 | `scripts/demo.cmd`, `scripts/demo.sh`, `compose.yaml` |
| 처음부터 끝까지 동작 | [데모 가이드](DEMO_GUIDE.md)(X2: 기준 일정 → 브리핑 → 감시 시작 → 받은편지함 → 조사 → 재계산 → Excel, H04), `docs/demo/*.png` 캡처 |
| 사람의 확인·승인 흐름 | `apps/web/app/workspace.tsx`(해석 확인, 조건 확인, 승인, 새 버전 확정), `services/api/app/main.py`의 승인·확정 API |
| 화면 구조 | `apps/web/app/stages.ts`(7단계 한 목록), `inbox.tsx`, `risk-panels.tsx` |
| 오류·한도 상황 | "에이전트 연결 확인 필요", "자동 추리기 한도 초과 · 규칙 결과만" 표시(`risk-panels.tsx`, `workspace.tsx`) |
| 배포 | http://13.209.206.223 (AWS EC2 서울, Docker Compose + Nginx, Elastic IP), [AWS 배포 안내](../deploy/aws/README.md), `compose.yaml` |

## 제출 코드 (10)

| 확인할 점 | 근거 위치 |
| --- | --- |
| 문서 | [README](../README.md)(요약 → 기술 상세), [HANDOFF](HANDOFF.md), [DEMO_GUIDE](DEMO_GUIDE.md) |
| 테스트 | `tests/`(pytest 148개), `scripts/evaluate_replay.py`(결정적 일정 계산 회귀 16/16), `scripts/evaluate_external.py`(4/4), `scripts/agent_flows.py --mode replay`(녹화 흐름 11개) |
| CI | `.github/workflows/ci.yml`(pytest, REPLAY, 외부 변화 회귀, hero 평가, 웹 빌드, Compose) |
| 코드 품질 | 주요 모듈마다 모듈 설명(docstring), 디버그 출력 없음, pyflakes 경고 1건(`scheduling/simulator.py`의 쓰지 않는 지역 변수, 동작 영향 때문에 유지) |
| 비밀 관리 | `.env`는 커밋하지 않음(`.gitignore`), `.env.example`은 자리표시자만, 실제 모델 평가 출력(`artifacts/agent_evaluation_real*.json`)은 제외 |

## 제출 전 확인 명령

```sh
./.venv/bin/python -m pytest -q
./.venv/bin/python scripts/evaluate_replay.py
./.venv/bin/python scripts/agent_flows.py --mode replay
cd apps/web && npm run build
```
