"""Rescore known workflow status aliases in a saved evaluation without LLM calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.evaluate_agent import rescore_workflow_status_aliases  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    rescored = rescore_workflow_status_aliases(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rescored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    workflow = rescored["agent_workflow"]
    print(json.dumps({"cases": workflow["cases"], "execution_failures": workflow["execution_failures"],
                      "required_tool_rate": workflow["required_tool_rate"],
                      "ambiguous_stop_rate": workflow["ambiguous_stop_rate"],
                      "offline_rescored_case_ids": rescored["offline_rescored_case_ids"],
                      "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
