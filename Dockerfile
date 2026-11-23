FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ascii_chess.py chess_server.py ./

# /data is the working directory for the terminal client (save.json, online.json)
RUN useradd --system --no-create-home app \
    && mkdir /data \
    && chown app /data
USER app

ENV HOST=0.0.0.0 \
    PORT=8765
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/health', timeout=2)"

CMD ["python", "chess_server.py"]
