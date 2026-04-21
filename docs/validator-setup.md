# Validator setup guide (Docker + Watchtower)

This guide covers running a Vocence validator using **Docker** and **Watchtower**. The same image is built and published by the team via CI/CD; validators run that image and auto-update when a new one is pushed.

---

## Prerequisites

- **From the Vocence team:** Chutes permission, owner API URL (`API_URL`), Hippius corpus + validator keys, and readonly validator-bucket credentials for the active validator set.
- **Your side:** Bittensor wallet (coldkey + hotkey), Docker and Docker Compose installed.

---

## 0. Install Docker and Docker Compose

You need Docker and Docker Compose on the machine that will run the validator.

### Ubuntu / Debian (easiest)

Use Docker’s official script (installs Engine + Compose plugin, avoids repo conflicts):

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

Add your user to the `docker` group so you can run Docker without `sudo`:

```bash
sudo usermod -aG docker $USER
# Log out and back in (or reboot) for the group change to take effect
```

Verify:

```bash
docker --version
docker compose version
```

### If you prefer manual APT install

If the Docker repo is **already** on your system (e.g. from a previous install), you can just install the packages:

```bash
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

If you see a **Signed-By conflict** (`/usr/share/keyrings/docker.asc != /etc/apt/keyrings/docker.asc`), remove the duplicate Docker list file so only one key path is used, then run the commands above:

```bash
sudo rm -f /etc/apt/sources.list.d/docker.list
# If the repo was only in that file, re-add it from https://docs.docker.com/engine/install/ubuntu/
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

### Other Linux / macOS / Windows

- **Linux (other distros):** [Install Docker Engine](https://docs.docker.com/engine/install/)
- **macOS:** [Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) (includes Compose)
- **Windows:** [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) (includes Compose)

Use `docker compose` (with a space) as in this guide; it’s the Compose V2 plugin. If you have the older `docker-compose` (with a hyphen), that works too.

---

## 1. Prepare environment and wallet

1. **Clone the repo** (only needed for config and compose file; the validator runs from the published image):

   ```bash
   git clone https://github.com/Vocence-bt/vocence
   cd vocence
   ```

2. **Create `.env`** from the example and fill in values (wallet, Chutes, Hippius, API_URL, etc.):

   ```bash
   cp env.example .env
   # Edit .env: NETWORK, NETUID (78), WALLET_NAME, HOTKEY_NAME,
   # CHUTES_API_KEY, API_URL, HIPPIUS_* keys, VALIDATOR_NAME, etc.
   ```

3. **Set `VALIDATOR_BUCKETS_JSON`** in `.env`. This env var is used only for global scoring and must contain readonly access details for validator sample buckets:

   ```env
   VALIDATOR_BUCKETS_JSON='[
     {"hotkey":"5F...","bucket_name":"vocence-samples-rt21","access_key":"readonly-access-key","secret_key":"readonly-secret-key"},
     {"hotkey":"5G...","bucket_name":"vocence-samples-rizzo","access_key":"readonly-access-key","secret_key":"readonly-secret-key"},
     {"hotkey":"5H...","bucket_name":"vocence-samples-kraken","access_key":"readonly-access-key","secret_key":"readonly-secret-key"}
   ]'
   ```

   Notes:

   - Only four fields are required: `hotkey`, `bucket_name`, `access_key`, `secret_key`.
   - Validators use this env var together with the owner API's active-validator list to decide which buckets to score from each cycle.
   - These credentials are sensitive and should stay only in `.env` or your secret manager.

4. **Bittensor wallets** must be available at `~/.bittensor/wallets` on the host (coldkey and hotkey). The compose file mounts this directory into the container at `/home/validator/.bittensor/wallets` (read-only) so the app finds `~/.bittensor/wallets` when running as the validator user. If the wallet lives under **root’s home** (e.g. `/root/.bittensor/wallets`), the container user (uid 1000) must be able to read it—on the host run: `sudo chown -R 1000:1000 /root/.bittensor/wallets`.

5. **Create the logs directory** so the container can write daily log files. The validator runs as user `validator` (uid 1000); if `./logs` is created by Docker on first run, it is owned by root and the container cannot write. Do this once before the first `docker compose up -d`:

   ```bash
   mkdir -p logs
   sudo chown 1000:1000 logs
   ```

   If you already started the stack and `logs/` is empty, run the same two commands and then `docker compose restart validator`.

---

## 2. Run with Docker Compose

Start the validator and Watchtower:

```bash
docker compose up -d
```

- **Validator:** Docker pulls the image from Docker Hub (e.g. `vocence78/vocence:latest`) if it isn’t already on your machine, then runs it (`vocence serve` — sample generation + weight setting in one process).
- **Watchtower:** Polls Docker Hub every 5 minutes; when the team pushes a new image, it pulls and restarts the validator so you stay up to date without manual steps.

### How scoring works now

The validator still generates its own samples locally, but final weight setting is based on **global consensus scoring**:

- The owner API returns the current valid miner list.
- The owner API returns the current active validator list.
- Your validator intersects that active-validator list with `VALIDATOR_BUCKETS_JSON`.
- For each matching validator bucket, it reads the recent scoring window (default 50 evaluations).
- It computes a global miner win rate using stake-weighted aggregation (`sqrt(stake)`).
- A miner must have more than 40 evaluations in at least 3 active validator buckets to be globally eligible.
- The winner must still beat earlier eligible miners and the owner base model by the configured threshold margin.
- If active validator coverage is too low or no miner satisfies the rules, the validator burns for that cycle.

This design reduces single-validator drift and helps honest validators converge on the same weights.

For the full scoring rules and winner-selection details, see [scoring.md](scoring.md).

### Overriding the image (optional)

Validators normally use `vocence78/vocence:latest`; the dev team’s CI pushes every new build as `latest`, and Watchtower updates you automatically. Override only if the team gives you a different image name:

```bash
DOCKER_IMAGE=vocence78/vocence:latest
```

Then run `docker compose up -d` as above.

---

## 3. Logs and health

- **Stream logs (stdout):**  
  `docker compose logs -f validator` (use the service name `validator`, not the container name `vocence-validator`)
- **Daily log files:**  
  Logs are written **in real time** into **`logs/vocence_YYYY-MM-DD.log`** (UTC date). Ensure you created `logs` and set ownership (step 4 in section 1) before first run. If `logs/` stays empty: `sudo chown 1000:1000 logs` then `docker compose restart validator`.
- **Watchtower logs:**  
  `docker compose logs -f watchtower`
- **Restart validator only:**  
  `docker compose restart validator`
- **Stop everything:**  
  `docker compose down`

The validator service has a healthcheck; Docker will report its status in `docker ps`.

---

## 4. How updates work

1. The team pushes code to `main`/`master`; GitHub Actions builds the Docker image and pushes it to Docker Hub (see [CI/CD pipeline](cicd-pipeline.md)).
2. On each validator host, Watchtower runs in the same stack and polls the registry (default every 300 seconds).
3. When a new image is available for the validator container, Watchtower pulls it and restarts the container (rolling restart).
4. No manual pull or restart is required; all validators using this setup stay in sync with the published image.

---

## Summary

| What | How |
|------|-----|
| Run validator | `docker compose up -d` (uses published image + your `.env` and wallets). |
| Updates | Automatic via Watchtower when the team pushes a new image. |
| Logs (stream) | `docker compose logs -f validator` |
| Logs (daily files) | `logs/vocence_YYYY-MM-DD.log` in the project directory. |
| Config | `.env` (including `VALIDATOR_BUCKETS_JSON`) and `~/.bittensor/wallets` on the host. |

For the full CI/CD flow (how the image is built and published), see [cicd-pipeline.md](cicd-pipeline.md). For CLI options (e.g. split generator vs weight setter if you run without Docker), see [CLI.md](CLI.md).

---

## Troubleshooting

- **Watchtower: "client version 1.25 is too old. Minimum supported API version is 1.40"**  
  The host Docker daemon requires a newer API. The compose file sets `DOCKER_API_VERSION=1.40` for Watchtower. If you still see this, pull the latest image and restart: `docker compose pull watchtower && docker compose up -d watchtower`.

- **"Keyfile at: .../owner_hotkey does not exist" (wallet in /root/.bittensor/wallets)**  
  The wallet is mounted correctly, but the container runs as user `validator` (uid 1000). If the wallet on the host is under root’s home and owned by root, the container cannot read it (and may report “does not exist”). On the host run: `sudo chown -R 1000:1000 /root/.bittensor/wallets`, then `docker compose restart validator`.
