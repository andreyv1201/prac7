FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=2
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY ml_service/requirements.txt /app/ml-requirements.txt
RUN pip install --no-cache-dir -r ml-requirements.txt && pip install --no-cache-dir torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu
COPY common /app/common
COPY configs /app/configs
COPY ml_service /app/ml_service
CMD ["python", "-m", "uvicorn", "ml_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
