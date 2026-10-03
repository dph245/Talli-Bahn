FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . && useradd --create-home talli && mkdir /app/data /app/efa-cache && chown talli:talli /app/data /app/efa-cache
USER talli
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
