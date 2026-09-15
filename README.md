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
verified stable release with its matching host bridge. If no compatible stable
release exists, it uses the newest compatible release and says so. If Apptainer
is not available it uses Docker; macOS uses Docker. Force a runtime with
`--runtime docker` or `--runtime apptainer`. Docker installs build the application
image from the same source revision as the host runtime bridge.

Install the current beta channel explicitly:

```bash
# Docker
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --mode local --runtime docker --image docker.io/deepmi/neurocade:beta

# Rootless Apptainer on Linux amd64
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --mode local --runtime apptainer --version beta
```

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

GitHub releases continue to host the application SIF, bridge wheel, verified
source archive, checksums, and release manifest. Installer-owned archive
installs can update transactionally with `./scripts/update.sh --yes`; Git
checkouts remain under Git control.

### Retrying a partial release

On the failed Release run, choose **Re-run failed jobs**. Each run saves its
version and source commit in a `release-plan` artifact before building. A rerun
restores that plan, even if `main` or the calendar date has changed.

Images are built under `staging-<run-id>-<attempt>` tags. These are temporary
validation images, not supported release versions. Only after Docker and
Apptainer smoke tests pass does publication upload the assets to a draft GitHub
release, promote the validated image to its version tag, and publish the release.
The `beta` or `latest` channel is updated last.

If publication is interrupted, a rerun completes the same draft and accepts an
existing Git tag only when it points to the original commit. Once the GitHub
release is public, reruns preserve its image and assets and only repair its
channel tag. Retrying an older version does not move a newer channel backwards.
Scheduled and manual releases share a concurrency group to prevent overlapping
publication.

The plan is retained for 90 days, subject to repository retention limits. If a
run failed before saving its plan, start a new workflow run. Do not delete the
plan artifact while a release needs recovery. Runs from before this retry
support was introduced continue to use their original workflow.

Docker Hub and GitHub cannot publish atomically: a failure during final
publication can still leave a versioned image or Git tag alongside a draft.
Rerunning completes that state. Staging tags remain available for debugging and
can be removed from Docker Hub after the release is complete or abandoned.
### Local MCP agents

For optional local agent access to NeuroCade and FastSurfer, see [MCP setup and usage](MCP.md).
