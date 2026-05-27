ARG BUILD_FROM

# ── Build stage: compile libmbus from source ─────────────────────────────────
FROM ${BUILD_FROM} AS build_base

RUN apk add --no-cache \
    git \
    build-base \
    autoconf \
    automake \
    libtool

# Pin to a specific release tag for reproducible, supply-chain-safe builds.
# To upgrade: verify the new tag/SHA and update this line.
RUN git clone --depth 1 --branch 0.9.0 https://github.com/rscada/libmbus.git /tmp/libmbus

WORKDIR /tmp/libmbus
# build.sh runs: autoheader → aclocal → libtoolize → automake → autoconf → ./configure → make
RUN ./build.sh && make install

# ── Runtime image ─────────────────────────────────────────────────────────────
ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python, then paho-mqtt via pip, then remove pip to keep the image lean.
# --break-system-packages is required on Alpine 3.17+ (PEP 668 / EXTERNALLY-MANAGED).
RUN apk add --no-cache python3 \
    && apk add --no-cache --virtual .pip-deps py3-pip \
    && true

# Copy compiled binary and shared library from the build stage.
# musl libc (Alpine) searches /usr/local/lib by default — no ldconfig needed.
COPY --from=build_base /usr/local/bin/mbus-tcp-request-data /usr/local/bin/
COPY --from=build_base /usr/local/bin/mbus-serial-request-data /usr/local/bin/
COPY --from=build_base /usr/local/lib/libmbus.so* /usr/local/lib/

# Install Python dependencies, then remove the pip toolchain.
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && apk del .pip-deps

# Application code
COPY app/ /app/

# Container entrypoint
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
