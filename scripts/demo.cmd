@echo off
rem REPLAN demo: the agent runs from recorded responses (no key, no cost) in a separate, clean data store.
rem Your usual stack is stopped first; its data is kept.
cd /d "%~dp0.."
docker compose down
set REPLAN_LLM_MODE=replay
docker compose -p replan-demo up -d --build
echo.
echo Demo ready: http://localhost:3000  (agent: recorded replay, cost 0)
echo Clean restart: docker compose -p replan-demo down -v  then run this script again
echo Back to your usual stack: docker compose -p replan-demo down  then  docker compose up -d
