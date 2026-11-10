FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY run.py .

# La config (con el token) no va dentro de la imagen: se monta en /config/config.toml
USER nobody
CMD ["python", "run.py", "/config/config.toml"]
