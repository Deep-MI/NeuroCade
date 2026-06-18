FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates coreutils findutils grep sed \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
CMD ["bash"]
