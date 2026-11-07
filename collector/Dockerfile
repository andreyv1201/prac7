FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY common /app/common
COPY configs /app/configs
COPY collector /app/collector
COPY data /app/data
CMD ["python", "-m", "uvicorn", "collector.app:app", "--host", "0.0.0.0", "--port", "8000"]
