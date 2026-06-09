FROM python:3.11-slim

WORKDIR /app

# System deps for pandas/scikit
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Önce bibex_core (editable install)
COPY packages/bibex_core /opt/bibex_core
RUN pip install --no-cache-dir -e /opt/bibex_core

# API
COPY apps/api/requirements.txt /tmp/requirements.txt
# requirements.txt'deki -e ../../packages/... satırı bizim için zaten yapıldı; geri kalanı kur
RUN grep -v "^-e " /tmp/requirements.txt > /tmp/req.txt && \
    pip install --no-cache-dir -r /tmp/req.txt

COPY apps/api /app

# NLTK data preheat
RUN python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords')" || true

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
