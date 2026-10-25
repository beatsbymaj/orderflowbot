FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir .
RUN useradd --create-home flowforge && mkdir -p /app/reports && chown flowforge /app/reports
USER flowforge
