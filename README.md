![NeuroCade banner](assets/banner.png)

# NeuroCade

NeuroCade is a neuroimaging workspace for managing MRI cases, running containerized processing tools, and coordinating AI-assisted analysis workflows.
It can be used as a local app or installed on a server and acccessed via a web-browser.

## Quick Start

Install NeuroCade locally:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/Deep-MI/NeuroCade/main/scripts/install.sh) --mode local
```

The installer requires Docker, writes `.env`, builds the single NeuroCade image,
and starts one container.

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
