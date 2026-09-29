# L1 Hero Project — BatteryCo Hungary EV Battery Manufacturing Complex

**This is a SYNTHETIC benchmark project.** Every company name, task, date, dependency, and cost in this
directory was generated for this STEP and is clearly marked `"data_origin": "SYNTHETIC"`. It is not a
record of any real company's project, and it does **not** reproduce the history of any real-world case
in `data/l3_ground_truth/`. Real-world risk evidence stays in `data/l2_risk_signals/` and
`data/l3_ground_truth/`; this directory only supplies a fictional project schedule those cases can be
tested against — the L1 (synthetic input) / L2-L3 (real-world evidence) boundary is deliberate and must
stay intact.

## Purpose

REPLAN's actual product input is a project-schedule spreadsheet a user uploads. This Hero Project plays
that role for demos and for agent evaluation: it is designed to (1) look like a realistic project Excel a
manufacturing PM would actually maintain, (2) let the verified L2/L3 real-world risk cases be mapped onto
it as benchmark inputs, and (3) carry enough real dependency structure that an agent can compute
risk → affected task → schedule impact → response, not just look up a flat task list.

## Task-taxonomy derivation (done before building the WBS, per the task brief)

Before drafting any task, `data/l3_ground_truth/ground_truth.json` and
`data/evaluation/eval_affected_task_retrieval.json` were read to see which project stages actually
recur across the 12 verified cases. The recurring stages were: environmental/construction permitting
gating downstream work (V3-005, V3-006), site clearing (V3-005), foundation and structural "go-vertical"
work (V2-002, V3-007 — V3-007 even uses the exact term "go vertical" that Intel itself used), equipment
installation and commissioning driven by labor availability (V2-001, V3-001), and trial-production /
cell-production-line milestones (V3-006). A second cluster of cases (V2-003, V2-005, V3-002, V3-003,
V3-004) describe disruptions to an **already-operating** facility's production line rather than a
construction milestone — this Hero WBS is a greenfield-construction project ending at Start of
Production, so those cases only map onto it as a low-confidence proxy (see
`l3_to_wbs_mapping.json`), not a genuine match; that gap is recorded honestly rather than forced.
This confirmed the given taxonomy (Permitting → Site preparation → Construction → Equipment procurement
→ Equipment delivery → Equipment installation → Utility connection → Commissioning → Trial production →
Start of production) is well covered by the 8-phase WBS below before any task was written.

## Scale and schedule

- **64 tasks** across **8 phases** (target was 50-70; simple 15-20 task lists and
  100+ task over-engineering were both avoided).
- Project duration: **2025-01-06 to 2027-12-21** (~35.4 months,
  within the requested 24-36 month range).
- **15 milestones** (e.g. Land acquired, Construction permit approved, Structure topped out,
  Building enclosed, Equipment delivered, Commissioning completed, Trial production started, Start of
  Production).
- During synthetic project planning, the dates were derived once by a forward-pass schedule engine from
  `predecessor_ids`, `dependency_type` (FS/SS), and `duration_days`. They are now the **approved,
  frozen integrated baseline**: all 64 work packages retain a fixed `baseline_start` and
  `baseline_finish`, and each finish minus start equals `duration_days` exactly.
- REPLAN treats this baseline as an input fact. It assesses external risks against the existing dates;
  it does not regenerate the plan. Only an approved response scenario can create a separate revised
  working schedule, while the original baseline remains auditable.
- `criticality` was **not** hand-labeled either. It was computed via a full forward/backward-pass CPM
  float calculation (float ≤ 0 days → `critical`, ≤ 10 → `high`, ≤ 30 → `medium`, else `low`), so the
  30/64 critical-path tasks reflect the schedule's actual dependency structure (a largely
  serial permitting → construction → equipment → commissioning → production pipeline, with real slack
  only where genuine parallel tracks exist, e.g. the two equipment-vendor tracks in Phase 6 and the
  utility-connection track in Phase 5) rather than an arbitrary count.

## Phases

1. Project Initiation — feasibility, site selection, financing, partner selection, land acquisition
2. Regulation & Engineering — environmental review/permit, geotechnical survey, detailed engineering,
   utility agreement, construction permit, local regulatory approval
3. Procurement — equipment specification, long-lead equipment orders (two vendor tracks), electrical/
   mechanical/construction-material procurement, logistics planning
4. Site & Civil Construction — site prep, ground improvement, foundation, structural steel ("go
   vertical"), enclosure, internal civil works, site paving/fencing
5. Utilities & Facility — grid connection, substation, water, wastewater, HVAC, fire protection,
   clean/dry room, utility commissioning
6. Manufacturing Equipment — two parallel vendor tracks (cell line / module-pack line) from
   manufacturing through FAT, international shipping, customs, delivery, mechanical/electrical hookup,
   automation integration, and calibration
7. Commissioning — facility inspection, per-line equipment commissioning, integrated line testing,
   safety and regulatory inspection / operating-license approval
8. Production Launch — workforce recruitment/training, trial production (both lines), process
   qualification, yield stabilization, ramp-up, Start of Production

## Partners (11)

BatteryCo (project owner, South Korea), Global EPC, Local Civil Contractor, Equipment Vendor A (cell
equipment, South Korea), Equipment Vendor B (module/pack equipment, Germany), Logistics Partner, Utility
Provider, Regulatory Authority, Environmental Consultant, Automation Integrator, and Workforce Agency —
all synthetic. The two-vendor equipment structure (Vendor A / Vendor B) intentionally creates a real
parallel-track dependency pattern in Phase 6, so an agent can be tested on whether a delay in one
vendor's track propagates only to that track's downstream tasks, not the whole project.

## Integrated schedule authority and parties

This is one integrated project schedule, not 11 separate companies' schedules. In a realistic delivery
model, **BatteryCo Project Operations Team** is the project sponsor that approves the baseline, while the
synthetic **Global EPC Project Controls Team** maintains the controlled integrated schedule from a
synthetic Primavera P6 export. The Global EPC schedule function is therefore the *schedule authority*;
it is not automatically the accountable party for every work package.

Each WBS row now keeps four separate facts: `accountable_party` (the party responsible for the work
commitment), `performing_party` (who carries out or issues the activity),
`external_constraint_parties` (whose decision/service can constrain it), and `supplier_id` (only an
actual contracted supplier). A regulatory authority or utility provider can appear as an external
constraint or service performer, but is never treated as a supplier or owner of the integrated
baseline. This distinction is deliberate so supplier-calendar monitoring and risk investigation do not
mistakenly target public authorities.

## How this connects to L2/L3

`l3_to_wbs_mapping.json` maps each of the 12 Grade A/B cases in `data/l3_ground_truth/ground_truth.json`
onto specific `task_id`s in this WBS, split into `direct_task_ids` (the task(s) the real case's own
`affected_project_stage` most directly describes) and `downstream_task_ids` (what this WBS's dependency
graph gates behind those tasks). Where the real case's own L3 record could not confidently identify a
single task (e.g. V2-004, whose `affected_task_retrieval` metric is itself `not_usable` in
`data/evaluation/`), no task is asserted and `mapping_confidence` is `low` with the reason recorded — per
the STEP 4 brief's rule against forcing a mapping OBSERVED evidence cannot support. Five cases
(V2-003, V2-005, V3-002, V3-003, V3-004) describe disruptions to an already-operating facility rather
than a construction milestone; since this WBS is a construction project, those are recorded as
low-confidence proxy mappings onto the closest analogous task (Ramp-up), not genuine matches.

## Intended use in the service

This is the file a demo user "uploads": `hero_battery_factory_project.xlsx` plays the role of the
Excel a project manager already has, matching REPLAN's actual input flow. `hero_battery_factory_project.json`
is the machine-readable mirror an agent would parse instead of re-reading the spreadsheet. When a demo
or evaluation run wants to inject one of the verified L2/L3 real-world risk events, `l3_to_wbs_mapping.json`
tells the harness which `task_id`(s) in this WBS should be perturbed and which downstream tasks a correct
agent response should identify as affected — i.e. it is the affected-task-retrieval ground truth for this
specific WBS, derived from (but distinct from) the real-world ground truth in `data/l3_ground_truth/`.

## What this STEP explicitly did NOT do

Per the task brief: no new real-world risk events were collected, `data/l2_risk_signals/` and
`data/l3_ground_truth/` were not modified, no synthetic L4 augmentation was generated, and no agent,
UI, or evaluation code was implemented. Nothing here is presented as real/confidential company data, and
nothing in `data/l3_ground_truth/` is presented as this project's own history.
