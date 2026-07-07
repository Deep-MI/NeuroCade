# Docker Image

`docker/backend.Dockerfile` builds one image:

1. a Node stage builds the React SPA
2. a Python runtime stage installs backend dependencies and Apptainer
3. uvicorn serves both `/api/app` and the built SPA

Build:

```bash
./scripts/run.sh build
```

Run:

```bash
./scripts/run.sh start -d
```

The container needs `--privileged --device /dev/fuse` so Apptainer can execute
tool images inside Docker. `scripts/run.sh` supplies those flags and mounts
`neurocade-data/` at `/data`.

Tool metadata is packaged in the Python wheel and loaded on demand by
`tool_search`/`tool_call`; no catalog file is generated at image build or
container startup.
