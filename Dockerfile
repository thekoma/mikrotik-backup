FROM python:3.12-slim

# Install required system packages and clean up in one layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends openssh-client && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install --no-cache-dir boto3 paramiko colorama tomli schedule

# Copy application
COPY backup.py /app/backup.py

# Set working directory
WORKDIR /app

CMD ["python", "/app/backup.py"]