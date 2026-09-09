![NeuroCade banner](assets/banner.png)

# NeuroCade

NeuroCade is a neuroimaging workspace for managing MRI cases, running containerized processing tools, and coordinating AI-assisted analysis workflows.
It can be used as a local app or installed on a server and accessed through a web browser.
Learn more on [NeuroCade.org](https://NeuroCade.org).

## Quick Start

Install NeuroCade locally:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --mode local
```

On Linux, the installer prefers rootless Apptainer and downloads the latest
verified stable release with its matching host bridge. If Apptainer is not
available it uses Docker; macOS uses Docker. Docker installs build the
application image from the same source revision as the host runtime bridge.

To build a local checkout into an Apptainer SIF (requires Docker):

```bash
./scripts/install.sh --runtime apptainer --mode local --build-from-source
```

If `curl` is unavailable:

```bash
bash <(wget -qO- https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --mode local
```

From an existing checkout:

```bash
./scripts/install.sh --mode local
./scripts/run.sh status
```

For server installs, choose `--mode internal`. More detailed install
instructions are in [INSTALL.md](INSTALL.md).

## Release publishing

The release workflow publishes versioned Docker images to `docker.io/deepmi/neurocade`,
with `beta` and `latest` channel tags for beta and stable releases respectively.
The Docker Hub repository must be public so release validation and Apptainer
conversion can pull the image anonymously.

Configure these GitHub Actions secrets before running the release workflow:

- `DOCKERHUB_USERNAME`: the Docker Hub account used to publish images.
- `DOCKERHUB_TOKEN`: an access token for that account with push access to `deepmi/neurocade`.

GitHub releases continue to host the application SIF, bridge wheel, checksums,
and release manifest.
