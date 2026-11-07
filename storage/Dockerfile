FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY common /app/common
COPY configs /app/configs
COPY storage /app/storage
CMD ["python", "-m", "uvicorn", "storage.app:app", "--host", "0.0.0.0", "--port", "8000"]
