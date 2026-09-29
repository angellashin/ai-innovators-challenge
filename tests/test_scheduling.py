import unittest

from services.api.app.scheduling import simulate, validate_tasks


def _row(task_id, start, finish, duration, predecessors, location, resource, status):
    return {
        "task_id": task_id,
        "baseline_start": start,
        "baseline_finish": finish,
        "duration_workdays": duration,
        "predecessor_ids": predecessors,
        "dependency_type": "FS",
        "location_id": location,
        "resource_group": resource,
        "resource_demand": 1,
        "resource_capacity": 1,
        "status": status,
    }


PROJECT = {
    "project_id": "DEMO-P001",
    "baseline_start": "2026-09-14",
    "baseline_finish": "2026-10-27",
    "target_finish": "2026-10-28",
    "weekend_days": [5, 6],
    "nonworking_dates": ["2026-10-23"],
}


TASKS = [
    _row("T01", "2026-09-14", "2026-09-16", 3, "", "OFFICE", "design", "done"),
    _row("T02", "2026-09-17", "2026-09-18", 2, "T01", "OFFICE", "design", "done"),
    _row("T03", "2026-09-21", "2026-09-25", 5, "T02", "SUPPLIER", "fabrication", "active"),
    _row("T04", "2026-09-28", "2026-09-29", 2, "T03", "SUPPLIER", "test", "todo"),
    _row("T05", "2026-09-30", "2026-09-30", 1, "T04", "SUPPLIER", "shipping", "todo"),
    _row("T06", "2026-10-01", "2026-10-06", 4, "T05", "TRANSIT", "transport", "todo"),
    _row("T07", "2026-09-17", "2026-09-23", 5, "T01", "SITE-H1", "civil", "active"),
    _row("T08", "2026-09-24", "2026-09-30", 5, "T07", "SITE-H1", "electrical", "todo"),
    _row("T09", "2026-09-24", "2026-09-25", 2, "T07", "SITE-H1", "civil", "todo"),
    _row("T10", "2026-09-28", "2026-09-28", 1, "T09", "SITE-H1", "lifting", "todo"),
    _row("T11", "2026-10-07", "2026-10-07", 1, "T06,T09,T10", "SITE-H1", "lifting", "todo"),
    _row("T12", "2026-10-08", "2026-10-12", 3, "T11", "SITE-H1", "mechanical", "todo"),
    _row("T13", "2026-10-13", "2026-10-14", 2, "T12", "SITE-H1", "piping", "todo"),
    _row("T14", "2026-10-13", "2026-10-14", 2, "T12,T08", "SITE-H1", "electrical", "todo"),
    _row("T15", "2026-10-15", "2026-10-16", 2, "T14", "SITE-H1", "control", "todo"),
    _row("T16", "2026-10-19", "2026-10-20", 2, "T15", "SITE-H1", "training", "todo"),
    _row("T17", "2026-10-19", "2026-10-19", 1, "T13,T15", "SITE-H1", "quality", "todo"),
    _row("T18", "2026-10-21", "2026-10-22", 2, "T16,T17", "SITE-H1", "commissioning", "todo"),
    _row("T19", "2026-10-26", "2026-10-26", 1, "T18", "SITE-H1", "customer", "todo"),
    _row("T20", "2026-10-27", "2026-10-27", 1, "T19", "OFFICE", "documents", "todo"),
]


OPTIONS = [
    {
        "option_id": "OPT-01",
        "target_ids": ["T07", "T08", "T09", "T10"],
        "operation": "SHIFT_EARLIER",
        "approval_state": "conditional",
        "conditions": "respect FS",
    },
    {
        "option_id": "OPT-02",
        "target_ids": ["T12"],
        "operation": "SET_DURATION_WORKDAYS",
        "new_value": 2,
        "approval_state": "conditional",
    },
    {
        "option_id": "OPT-03",
        "target_ids": ["T06"],
        "operation": "SET_DURATION_WORKDAYS",
        "new_value": 2,
        "approval_state": "conditional",
    },
]


E01 = {
    "event_id": "E01",
    "content": "Fabrication finish moves to September 30. FAT can start October 1.",
}

E01_TYPED = {
    "event_id": "E01",
    "patch": {
        "estimated_finish": {"T03": "2026-09-30"},
        "not_before": {"T04": "2026-10-01"},
    },
}


def _by_id(result, task_id):
    return next(item for item in result["schedule"] if item["task_id"] == task_id)


def _option(*option_ids):
    return [option for option in OPTIONS if option["option_id"] in option_ids]


def _tasks_with(task_id, **updates):
    copied = [dict(task) for task in TASKS]
    for task in copied:
        if task["task_id"] == task_id:
            task.update(updates)
    return copied


class SchedulingTests(unittest.TestCase):
    def test_baseline_reproduces_reference_finish_and_holiday(self):
        result = simulate(PROJECT, TASKS)

        self.assertEqual(result["violations"], [])
        self.assertEqual(result["finish_date"], "2026-10-27")
        self.assertIs(result["target_met"], True)
        self.assertEqual(_by_id(result, "T19")["planned_start"], "2026-10-26")

    def test_e01_reference_table_preserves_schedule_only_decisions(self):
        cases = [
            ([], "2026-10-30", False),
            (["OPT-01"], "2026-10-30", False),
            (["OPT-02"], "2026-10-29", False),
            (["OPT-03"], "2026-10-28", True),
            (["OPT-02", "OPT-03"], "2026-10-27", True),
        ]

        for option_ids, finish, target_met in cases:
            with self.subTest(option_ids=option_ids):
                result = simulate(PROJECT, TASKS, event=E01, options=_option(*option_ids))
                self.assertEqual(result["finish_date"], finish)
                self.assertIs(result["target_met"], target_met)

    def test_typed_e01_patch_reproduces_reference_result(self):
        result = simulate(PROJECT, TASKS, event=E01_TYPED, options=_option("OPT-02"))

        self.assertEqual(result["finish_date"], "2026-10-29")
        self.assertEqual(_by_id(result, "T03")["planned_finish"], "2026-09-30")
        self.assertEqual(_by_id(result, "T04")["planned_start"], "2026-10-01")

    def test_e01_identifies_target_recovery_options_without_cost_gate(self):
        target_recovery_options = []
        for option_ids in ([], ["OPT-01"], ["OPT-02"], ["OPT-03"], ["OPT-02", "OPT-03"]):
            result = simulate(PROJECT, TASKS, event=E01, options=_option(*option_ids))
            if result["target_met"]:
                target_recovery_options.append(option_ids)

        self.assertEqual(target_recovery_options, [["OPT-03"], ["OPT-02", "OPT-03"]])

    def test_resource_unavailable_delays_only_matching_scope(self):
        event = {
            "event_id": "E04",
            "resource_unavailable": [
                {
                    "location_id": "SITE-H1",
                    "resource_group": "mechanical",
                    "start": "2026-10-08",
                    "finish": "2026-10-09",
                }
            ],
        }

        result = simulate(PROJECT, TASKS, event=event)

        self.assertEqual(_by_id(result, "T12")["planned_start"], "2026-10-12")
        self.assertEqual(_by_id(result, "T12")["planned_finish"], "2026-10-14")
        self.assertEqual(_by_id(result, "T08")["planned_start"], "2026-09-24")

    def test_typed_e02_blocked_dates_are_task_scoped(self):
        event = {
            "event_id": "E02",
            "patch": {"blocked_dates": {"T11": ["2026-10-07"]}},
        }

        result = simulate(PROJECT, TASKS, event=event)

        self.assertEqual(_by_id(result, "T11")["planned_start"], "2026-10-08")
        self.assertEqual(_by_id(result, "T12")["planned_start"], "2026-10-09")
        self.assertEqual(_by_id(result, "T08")["planned_finish"], "2026-09-30")

    def test_typed_e04_resource_unavailable_is_nonpreemptive(self):
        event = {
            "event_id": "E04",
            "patch": {"resource_unavailable": {"기계팀": ["2026-10-09"]}},
        }

        result = simulate(PROJECT, _tasks_with("T12", resource_group="기계팀"), event=event)

        self.assertEqual(_by_id(result, "T12")["planned_start"], "2026-10-12")
        self.assertEqual(_by_id(result, "T12")["planned_finish"], "2026-10-14")

    def test_validate_tasks_reports_missing_predecessor_and_cycle(self):
        tasks = [
            _row("A", "2026-01-01", "2026-01-01", 1, "B", "L", "R", "todo"),
            _row("B", "2026-01-02", "2026-01-02", 1, "A", "L", "R", "todo"),
            _row("C", "2026-01-03", "2026-01-03", 1, "Z", "L", "R", "todo"),
        ]

        errors = validate_tasks(tasks)

        self.assertIn("C references missing predecessor: Z", errors)
        self.assertIn("cycle detected involving: A,B", errors)


if __name__ == "__main__":
    unittest.main()
