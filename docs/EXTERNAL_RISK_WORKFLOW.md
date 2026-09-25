# External change workflow

## Product scope
The primary loop is Excel baseline → monitor external evidence → match affected tasks →
calculate provisional impacts and alternatives → verify applicability and execution conditions →
approve → revised Excel with evidence. Supplier email is an optional input, not a prerequisite.

## Implemented sources and limits
- Open-Meteo daily forecasts for explicitly configured coordinates, outdoor tasks and thresholds.
  Forecasts are not observations or guaranteed work stoppages. No long-range weather prediction.
- Nager.Date public holidays for configured country, year, subdivision and task IDs. Non-public
  holiday types and other regions are excluded. Actual local/supplier work calendars need review.
- Registered HTTPS pages and RSS/Atom feeds. Each feed entry retains its URL, ID, publication time,
  retrieval time and a content hash. Source registration still uses REPLAN_ALLOWED_SOURCE_HOSTS.
  This is registered-source monitoring, not unrestricted web discovery.
- Optional paid LLM interpretation runs before NEEDS_INPUT for public notices. Candidate task IDs
  must exist and supporting quotes must occur verbatim in source text. LLM output cannot write
  a schedule patch. Missing dates or applicability require a documented operator decision.
  Existing API_KEY/LLM_MODEL/LLM_BASE_URL and REPLAN_PAID_CALLS_ENABLED gates and usage caps apply.

## Review and calculation
Configure source keywords and related tasks in the workspace. Match reasons are candidates,
not legal applicability judgments. Publication dates are never converted automatically to delays.
The operator can record a supported start constraint, unavailable date or finish estimate.
Weather and calendar proposals can be simulated before confirmation; all evidence included in a
scenario must be confirmed before approval or commit. Concurrent external calendar constraints
are unioned, avoiding double-counting, and conflicting finish claims stop for review.
Forecast updates supersede earlier proposals; successful scans below the limit retire them.
A failed fetch leaves previous evidence intact and is explicitly shown as a collection failure.
Changing evidence, the input version or operating calendars invalidates relevant approval.
Approved blocked dates remain attached to tasks in subsequent schedule versions. Removing an
already approved constraint requires a new reviewed schedule change, not silent forecast rollback.

## Data and evaluation
data/external/hu-2026-holidays.json is an actual Nager.Date API response captured with URL, timestamp
and hash. It is an aggregated public calendar source, not claimed to be an official legal notice.
The schedule and weather/notice fixtures used in tests are synthetic and labeled as such.
Run python scripts/evaluate_external.py for a four-case offline engineering regression:
real calendar + synthetic project, same-day weather/calendar overlap, relevant notice and unrelated
notice. This is not a representative news benchmark, verified LLM performance, or an accuracy claim
for the existing historical L2/L3 corpus. Existing historical benchmark results are preserved.

## Fields preserved on import
supplier_id, equipment_id, country_code, risk_tags (comma-separated), planned_cost, currency.
Supplier calendar IDs are isolated by project. Calendars are applied only to matching supplier_id,
with supplier/owner as a legacy fallback. Cost comparison still reports catalog extra_cost_krw;
planned_cost/currency preservation does not implement total cost forecasting or FX conversion.

## Setup
1. Import and confirm a schedule with task IDs, dates, dependencies and outdoor flags.
2. In 외부 변화 감시, configure coordinates/timezone/thresholds and select outdoor tasks.
3. Add country/year holiday calendars and select tasks for that location.
4. Register approved public URLs; connect keywords and task candidates where helpful.
5. Enable monitoring and keep the API and worker running. Use 외부 변화 지금 확인 for a scan.
6. Inspect evidence and automatically refreshed analysis. Confirm applicability, prepare actions,
   record each condition's reply, approve, then commit and download Excel.

Automatic email OAuth ingestion and external notification delivery remain unimplemented and are
labeled accordingly. No production credentials, account permissions or deployments are changed.

