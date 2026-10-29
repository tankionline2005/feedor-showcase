FROM node:22-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# OCR of post pictures (app/ocr.py): Tesseract with the light tessdata_fast models
ADD https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/rus.traineddata \
    https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata /usr/share/tessdata_fast/
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
       fonts-dejavu-core fonts-dejavu-extra fonts-liberation2 fonts-roboto-unhinted \
       fonts-paratype fonts-montserrat fonts-manrope fonts-bebas-neue fonts-roboto-slab fonts-comfortaa fonts-play \
    && rm -rf /var/lib/apt/lists/* \
    && chmod 644 /usr/share/tessdata_fast/*.traineddata \
    && test "$(stat -c %s /usr/share/tessdata_fast/rus.traineddata)" -gt 1000000
ENV TESSDATA_FAST=/usr/share/tessdata_fast
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /fe/dist ./static
ENV STATIC_DIR=/app/static MEDIA_DIR=/data/media
EXPOSE 8000
CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
