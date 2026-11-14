FROM python:3.12-slim

# Avoid writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    QASHBOCKS_DB=/data/sample.db

WORKDIR /app

# Install package dependencies
COPY pyproject.toml .
COPY qashbocks/ qashbocks/
COPY bin/ bin/
COPY templates/ templates/
COPY tools/ tools/
COPY data/sample.db /app/data/sample.db

RUN pip install --no-cache-dir .

# Create volume mount points for persistent database and user configs
RUN mkdir -p /data /root/.config/qashbocks && \
    cp /app/data/sample.db /data/sample.db

VOLUME ["/data"]

ENTRYPOINT ["qashbocks"]
CMD ["accounts"]
