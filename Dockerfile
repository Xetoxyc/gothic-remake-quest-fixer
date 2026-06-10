# The Oodle library is x86_64 Linux, so this image is amd64.
# On Apple Silicon / ARM hosts, Docker Desktop runs it via emulation.
FROM --platform=linux/amd64 python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Oodle is fetched at container start (not baked into the image).
ENV OODLE_LIB=/app/liboo2corelinux64.so.9 \
    OODLE_URL=https://raw.githubusercontent.com/natimerry/repak-rivals/master/liboo2corelinux64.so.9 \
    PORT=5000

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
