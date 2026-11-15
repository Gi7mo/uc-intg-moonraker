FROM python:3.15-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --upgrade -r requirements.txt

COPY driver.json .
COPY intg-moonraker ./intg-moonraker

RUN useradd --system --uid 10001 --no-create-home intg \
    && mkdir /config && chown intg /config
USER intg

# Network configuration
ENV UC_DISABLE_MDNS_PUBLISH="false"
ENV UC_MDNS_LOCAL_HOSTNAME=""
ENV UC_INTEGRATION_INTERFACE="0.0.0.0"
ENV UC_INTEGRATION_HTTP_PORT="9090"

# Configuration path
ENV UC_CONFIG_HOME="/config"

LABEL org.opencontainers.image.source=https://github.com/Gi7mo/uc-intg-moonraker

CMD ["python3", "-u", "intg-moonraker/driver.py"]
