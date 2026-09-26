"""Deterministic investigation tools reproduce the loop design's calculator values."""

import json
from pathlib import Path

import pytest

from app import investigation as inv
from app.external_risks import combine_patches, match_notice
from app.importers import parse_upload
from app.main import ConfirmInput, normalize_import_snapshot
from app.shifted_external import bundled_hero_calendars, recheck_shifted_schedule

ROOT = Path(__file__).resolve().parents[1]
HERO = ROOT / "data/l1_project/hero_battery_factory_project.xlsx"
LOOP = json.loads((ROOT / "data/hero_demo/external_loop_signals.json").read_text(encoding="utf-8"))
X2_PATCH = {"estimated_finish": {"T042": "2026-03-14"}}


@pytest.fixture(scope="module")
def hero():
    parsed = parse_upload(HERO.name, HERO.read_bytes())
    snapshot = normalize_import_snapshot(parsed, {"name": "Hero", "mode": "REPLAY"}, ConfirmInput())
    return snapshot["project"], snapshot["tasks"], snapshot["procurement"]


def conditional(hero, base_patch, changes):
    project, tasks, procurement = hero
    patch, applied = inv.conditional_changes(tasks, procurement, changes)
    calendars = bundled_hero_calendars(tasks)
    base = recheck_shifted_schedule(project, tasks, {"patch": base_patch}, [], None, calendars, [], {})
    result = recheck_shifted_schedule(project, tasks, {"patch": combine_patches([base_patch, patch])}, [], None,
                                      calendars, [], {})
    return inv.conditional_summary(result, base["finish_date"], "2027-12-21", applied, project["target_finish"])


def notice(signal_id):
    return next(item for item in LOOP["notices"] if item["signal_id"] == signal_id)


def test_x2_finds_later_items_and_only_the_critical_one_moves_the_finish(hero):
    project, tasks, procurement = hero
    found = inv.procurement_items(tasks, procurement, supplier_id="Equipment Vendor A",
                                  origin_country="China", customs_required=True, arriving_after="2026-03-07")
    assert [row["item_id"] for row in found["items"]] == ["P-B", "P-C"]
    assert [row["needed_by"] for row in found["items"]] == ["2026-10-08", "2027-04-09"]

    named = inv.mentioned_items(LOOP["supplier_messages"][0]["content"], procurement)
    assert [(row["item_id"], row["supplier_id"], row["planned_arrival"]) for row in named] == [
        ("P-A1", "Equipment Vendor A", "2026-03-07")]
    assert named[0]["origin_country"] == "China"
    pinned = inv.schedule_slack(project, tasks, ["T042"], X2_PATCH, bound_days=60)["tasks"][0]
    assert 200 <= pinned["float_calendar_days"] < inv.FLOAT_SEARCH_DAYS and pinned["absorbs_bound"]
    slack = inv.schedule_slack(project, tasks, ["T058", "T051"], X2_PATCH, bound_days=60)
    by_task = {row["task_id"]: row for row in slack["tasks"]}
    assert by_task["T058"]["absorbs_bound"] and by_task["T058"]["float_calendar_days"] >= 200
    assert by_task["T051"]["float_calendar_days"] == 0 and by_task["T051"]["on_critical_path"]

    worst_c = conditional(hero, X2_PATCH, [{"kind": "hold_after_arrival", "item_id": "P-C", "value": 60}])
    assert worst_c["finish_with_reported_change_only"] == "2027-12-21"
    assert (worst_c["finish_date"], worst_c["added_shift_days_vs_reported_change"]) == ("2028-02-11", 52)
    assert worst_c["changes"][0]["latest_action_date"] == "2027-02-08"
    assert worst_c["changes"][0]["not_before"] == "2027-05-25"
    worst_b = conditional(hero, X2_PATCH, [{"kind": "hold_after_arrival", "item_id": "P-B", "value": 60}])
    assert worst_b["finish_date"] == "2027-12-21"


def test_x2_links_the_customs_notice_and_x2_control_only_overlaps(hero):
    project, tasks, _ = hero
    source = notice("N-X2")
    signal = {"id": "sig", "data": {"channel": "registered_public_source", "version_id": "V", "title": source["title"],
                                    "content": source["content"], "published_at": source["published_at"],
                                    **match_notice({"title": source["title"], "content": source["content"]}, tasks, [])}}
    x2, x2c = LOOP["supplier_messages"]
    linked = inv.related_signals({**x2, "related_task_ids": ["T042"]}, [signal], "V")["signals"]
    assert linked[0]["overlapping_task_ids"] == ["T042"]
    assert {"통관", "수출 허가"} <= set(linked[0]["reason_terms"])
    tempted = inv.related_signals({**x2c, "related_task_ids": ["T054"]}, [signal], "V")["signals"]
    assert tempted[0]["overlapping_task_ids"] == ["T054"] and tempted[0]["reason_terms"] == []


def test_x1a_deadline_and_worst_case_and_x1b_absorbs(hero):
    project, tasks, _ = hero
    facts = inv.notice_facts(notice("X1-A")["content"])
    assert any(row["kind"] == "max_duration_days" and row["value"] == 30 for row in facts)
    assert any(row["kind"] == "date" and row["value"] == "2026-11-01" for row in facts)
    worst = conditional(hero, {}, [{"kind": "hold_after_start", "task_id": "T046", "value": 30}])
    assert (worst["finish_date"], worst["changes"][0]["latest_action_date"]) == ("2028-02-01", "2026-11-05")
    x1b = inv.notice_facts(notice("X1-B")["content"])
    assert any(row["kind"] == "max_duration_days" and row["value"] == 45 for row in x1b)
    slack = inv.schedule_slack(project, tasks, ["T013"], {}, bound_days=45)
    assert slack["tasks"][0]["absorbs_bound"] is True


def test_task_facts_show_origin_and_linked_items(hero):
    _, tasks, procurement = hero
    facts = inv.task_facts(tasks, procurement, ["T051", "T047"])["tasks"]
    assert facts[0]["items"][0]["item_id"] == "P-C"
    assert facts[1]["supplier_origin_countries"] == ["Germany"]
