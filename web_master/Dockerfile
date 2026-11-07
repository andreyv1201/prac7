FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY common /app/common
COPY configs /app/configs
COPY web_master /app/web_master
COPY scripts /app/scripts
CMD ["python", "-m", "uvicorn", "web_master.app:app", "--host", "0.0.0.0", "--port", "8000"]
