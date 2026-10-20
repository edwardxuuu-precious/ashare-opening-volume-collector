FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/scripts ./scripts
COPY backend/server ./server
COPY public/data ./seed
RUN useradd --uid 10001 --create-home stock && mkdir /data && chown stock:stock /data
USER stock
EXPOSE 8787
VOLUME ["/data"]
CMD ["python", "server/service.py", "--host", "0.0.0.0", "--port", "8787", "--out", "/data", "--seed", "/app/seed"]
