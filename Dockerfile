FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils tesseract-ocr tesseract-ocr-por imagemagick libzbar0 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 p33 \
    && mkdir /dados && chown p33:p33 /dados
WORKDIR /app
COPY app.py leitura.py ./
COPY web/ web/
USER p33
ENV P33_DB=/dados/p33.sqlite3 P33_HOST=0.0.0.0
EXPOSE 8080
CMD ["python", "app.py", "--demo"]
