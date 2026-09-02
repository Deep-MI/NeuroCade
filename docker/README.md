# Application image

`backend.Dockerfile` is the canonical NeuroCade release artifact. Its first
stage builds the React client; its Python 3.12 stage installs the monolithic
FastAPI application and serves both the API and SPA with one uvicorn worker.

The application image contains no Docker CLI, Apptainer, FUSE tooling, or
runtime socket. Neuroimaging containers are launched by the authenticated
host-native `neurocade-runtime-bridge` selected during installation.

Release CI publishes the amd64 OCI image, converts that exact tagged image to a
checksummed application SIF, and smoke-tests both artifacts as a non-root user.

Build locally with:

```bash
NEUROCADE_RUNTIME=docker ./scripts/run.sh build
```
