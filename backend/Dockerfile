FROM python:3.12-slim-bookworm

WORKDIR /app

# PDF and DOCX parsing is the optional Docling extra (ADR-022): pip install '.[parse]'.
# Typst compiles CVs inside a throwaway root. pdftotext reads the text layer back.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
        poppler-utils \
        fonts-liberation \
        fonts-roboto \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/install_typst.sh /tmp/install_typst.sh
RUN bash /tmp/install_typst.sh && rm /tmp/install_typst.sh

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .

EXPOSE 8000 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
