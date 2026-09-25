FROM python:3.11-slim

WORKDIR /workspace/services/api
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/api/app /workspace/services/api/app
COPY data/l1_project/hero_battery_factory_project.xlsx /workspace/data/l1_project/hero_battery_factory_project.xlsx
COPY data/hero_demo/supplier_messages.json /workspace/data/hero_demo/supplier_messages.json
COPY data/external/hu-2025-holidays.json data/external/hu-2026-holidays.json data/external/hu-2027-holidays.json data/external/hu-2028-holidays.json /workspace/data/external/
COPY data/l2_risk_signals/risk_signals.json /workspace/data/l2_risk_signals/risk_signals.json
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
