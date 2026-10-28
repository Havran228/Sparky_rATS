FROM python:3.12-slim-bookworm

WORKDIR /app

# The image bakes in the parse extra (ADR-024), so Docling is present without a later pip.
# libgl1 and libglib2.0-0 are the OpenCV/OCR libraries Docling imports at startup.
# poppler-utils provides pdftotext for the round-trip ATS check. Typst compiles CVs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
        libgl1 \
        libglib2.0-0 \
        poppler-utils \
        fonts-liberation \
        fonts-roboto \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/install_typst.sh /tmp/install_typst.sh
RUN bash /tmp/install_typst.sh && rm /tmp/install_typst.sh

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir ".[dev,parse]"

EXPOSE 8000 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
