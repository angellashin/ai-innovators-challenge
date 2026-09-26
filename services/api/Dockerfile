FROM python:3.11-slim

WORKDIR /workspace/services/api
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/api/app /workspace/services/api/app
COPY data/l1_project/hero_battery_factory_project.xlsx /workspace/data/l1_project/hero_battery_factory_project.xlsx
# Runtime data only. L3 ground truth and evaluation sets stay out of the image.
COPY data/hero_demo/supplier_messages.json data/hero_demo/response_options.json /workspace/data/hero_demo/
COPY data/external/*-holidays.json /workspace/data/external/
COPY data/l2_risk_signals/risk_signals.json /workspace/data/l2_risk_signals/risk_signals.json
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
