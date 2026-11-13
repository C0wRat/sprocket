# Sprocket in a container. See "Run it with Docker" in README.md for the USB setup.
#
#   docker build -t sprocket .
#   docker run -p 8800:8800 -v ~/Pictures/Sprocket:/scans \
#     -v /dev/bus/usb:/dev/bus/usb --device-cgroup-rule='c 189:* rmw' sprocket

FROM python:3.15-slim

# sane-utils gives scanimage (and the genesys backend the OpticFilm scanners use);
# coreutils' stdbuf, which Sprocket uses to stream the image, is already in the base.
RUN apt-get update \
    && apt-get install -y --no-install-recommends sane-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sprocket ./sprocket

ENV PYTHONUNBUFFERED=1 \
    SPROCKET_OUTPUT=/scans
VOLUME /scans
EXPOSE 8800

# Sprocket runs as root in the container so that it can open the scanner's USB device
# node, whose owner and group come from the host.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8800/api/status', timeout=4)"]

ENTRYPOINT ["python", "-m", "sprocket", "--host", "0.0.0.0"]
