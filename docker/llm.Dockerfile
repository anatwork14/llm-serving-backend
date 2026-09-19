FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG PRISM_LLAMA_TAG=prism-b10709-9a9394a
ARG PRISM_CUDA_VERSION=12.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgomp1 \
        tar \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    asset="llama-${PRISM_LLAMA_TAG}-bin-linux-cuda-${PRISM_CUDA_VERSION}-x64.tar.gz"; \
    url="https://github.com/PrismML-Eng/llama.cpp/releases/download/${PRISM_LLAMA_TAG}/${asset}"; \
    mkdir -p /tmp/llama-extract /opt/llama; \
    curl -fL --retry 5 --retry-all-errors --connect-timeout 20 -o /tmp/llama.tar.gz "${url}"; \
    tar -xzf /tmp/llama.tar.gz -C /tmp/llama-extract; \
    server="$(find /tmp/llama-extract -type f -name llama-server -print -quit)"; \
    test -n "${server}"; \
    bindir="$(dirname "${server}")"; \
    cp -a "${bindir}/." /opt/llama/; \
    chmod +x /opt/llama/llama-server; \
    rm -rf /tmp/llama.tar.gz /tmp/llama-extract

COPY docker/llm-entrypoint.sh /usr/local/bin/llm-entrypoint
RUN chmod +x /usr/local/bin/llm-entrypoint

WORKDIR /models

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/llm-entrypoint"]
