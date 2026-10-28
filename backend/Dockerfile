FROM python:3.12-slim-bookworm

WORKDIR /app

# PDF and DOCX parsing is the optional Docling extra (ADR-022): pip install '.[parse]'.
# Text, Markdown, and HTML parse without it. Typst and Chromium arrive later.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .

EXPOSE 8000 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
