FROM python:3.12-slim

WORKDIR /app
COPY api.py index.html ./

ENV DASH_DATA_DIR=/data \
    DASH_SCRIPTS_DIR=/scripts \
    DASH_PORT=28080

VOLUME ["/data", "/scripts"]

EXPOSE 28080

CMD ["python", "api.py"]
