# ./backend/Dockerfile
FROM python:3.11-alpine3.20

# Build deps + herramientas de red/WireGuard + ping + wget para el healthcheck
RUN apk add --no-cache \
    build-base libffi-dev openssl-dev musl-dev \
    wireguard-tools iproute2 iptables iputils openresolv wget

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
