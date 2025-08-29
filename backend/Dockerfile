# backend/Dockerfile
FROM python:3.11-alpine3.20

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Actualizamos paquetes base (reduce CVEs) y agregamos lo mínimo para wg-quick
RUN apk update \
 && apk upgrade --no-cache \
 && apk add --no-cache \
      wireguard-tools iproute2 iptables ip6tables \
      ca-certificates curl \
 && update-ca-certificates

# (Opcional) Userspace WireGuard (boringtun) para hosts sin módulo WG (Docker Desktop, etc.)
RUN set -eux; \
    ARCH="$(uname -m)"; \
    if [ "$ARCH" = "x86_64" ]; then \
      curl -L -o /usr/local/bin/boringtun https://github.com/cloudflare/boringtun/releases/download/v0.4.0/boringtun-linux-x86_64 && \
      chmod +x /usr/local/bin/boringtun; \
    elif [ "$ARCH" = "aarch64" ]; then \
      curl -L -o /usr/local/bin/boringtun https://github.com/cloudflare/boringtun/releases/download/v0.4.0/boringtun-linux-aarch64 && \
      chmod +x /usr/local/bin/boringtun; \
    else \
      echo "Arquitectura $ARCH sin binario boringtun preempaquetado; omitiendo userspace WG"; \
    fi

# Dependencias Python
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Código de la app
COPY . /app

# Carpeta de datos (SQLite, etc.)
RUN mkdir -p /app/data && chmod 700 /app/data

EXPOSE 8000

# Uvicorn (root porque la API invoca wg-quick y requiere NET_ADMIN)
CMD ["python", "-m", "uvicorn", "main:app", "--host=0.0.0.0", "--port=8000"]
