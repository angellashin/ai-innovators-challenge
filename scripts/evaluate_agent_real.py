"""Opt-in paid evaluation. Reads local .env and never prints its secret values."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.evaluate_agent import ROOT, WORKFLOW_CASE_IDS, evaluate


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    allowed = {"LLM_BASE_URL", "LLM_MODEL", "API_KEY", "REPLAN_PAID_CALLS_ENABLED", "REPLAN_MAX_PAID_RUNS_PER_DAY"}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in allowed:
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--case-id", action="append", choices=sorted(WORKFLOW_CASE_IDS),
                        help="Evaluate only the selected workflow case; repeat for multiple cases")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "agent_evaluation_real.json")
    args = parser.parse_args()
    load_local_env(args.env_file)
    result = evaluate("real", set(args.case_id) if args.case_id else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    workflow = result["agent_workflow"]
    print(json.dumps({"model": result["model"], "executed_at": result["executed_at"],
                      "llm_call_count": result["llm_call_count"],
                      "tool_selection_rate": workflow["required_tool_rate"],
                      "ambiguous_stop_rate": workflow["ambiguous_stop_rate"],
                      "unsupported_draft_numeric_rate": workflow["unsupported_draft_numeric_rate"],
                      "unconfirmed_regulation_as_confirmed_delay_rate": workflow["unconfirmed_regulation_as_confirmed_delay_rate"],
                      "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
