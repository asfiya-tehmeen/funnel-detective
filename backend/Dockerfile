FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
# The dataset is generated deterministically at build time (seed 20260720), so the image is self-contained.
# Ground truth is written outside the app directory and is never read by the API.
RUN python -m scripts.generate_data
ENV FD_VAR_DIR=/tmp/fd-var PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
