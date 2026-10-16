FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FE_ROOT=/data/FE FE_INDEX=/var/lib/fe-index/fe-index.sqlite3 \
    FE_RESCAN_SECONDS=180
WORKDIR /app
COPY requirements.txt /app/
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY fe_gradiva/ /app/fe_gradiva/
EXPOSE 8000
CMD ["python", "-m", "fe_gradiva.server"]
