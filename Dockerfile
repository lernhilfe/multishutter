FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
RUN apt-get update && apt-get install -y --no-install-recommends libusb-1.0-0 libexif12 libltdl7 libgomp1 ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app && useradd --uid 1000 --gid 1000 --create-home app
WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir --only-binary=:all: -r requirements-web.txt
COPY multishutter ./multishutter
USER 1000:1000
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "multishutter.web:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
