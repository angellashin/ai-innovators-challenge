#!/bin/sh
# REPLAN demo: the agent runs from recorded responses (no key, no cost) in a separate, clean data store.
# Your usual stack is stopped first; its data is kept.
set -e
cd "$(dirname "$0")/.."
docker compose down
REPLAN_LLM_MODE=replay docker compose -p replan-demo up -d --build
echo "Demo ready: http://localhost:3000  (agent: recorded replay, cost 0)"
echo "Clean restart: docker compose -p replan-demo down -v, then run this script again"
