FROM python:3.13-alpine
RUN pip install boto3 paramiko colorama tomli schedule
COPY backup.py /app/backup.py

CMD ["python", "/app/backup.py"]