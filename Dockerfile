FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV WINEDEBUG=-all
ENV WINEPREFIX=/root/.wine

# Установка зависимостей, Wine и Xvfb для эмуляции экрана
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    wget \
    curl \
    cabextract \
    xvfb \
    wine64 \
    wine32 \
    python3 \
    python3-pip \
    ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Скачивание и тихая установка MT5
RUN mkdir -p /root/.wine && \
    xvfb-run -a wineboot --init && \
    wget -q https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe -O /tmp/mt5setup.exe && \
    xvfb-run -a wine /tmp/mt5setup.exe /auto && \
    rm -f /tmp/mt5setup.exe

# Скачивание mt5server для взаимодействия Python с MT5 в Wine
RUN wget -q https://github.com/lucas-campagna/mt5linux/releases/latest/download/mt5server.exe -O /app/mt5server.exe

COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY . /app
RUN chmod +x /app/start.sh

EXPOSE 10000

CMD ["/app/start.sh"]
