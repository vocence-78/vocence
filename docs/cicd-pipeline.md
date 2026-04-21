# CI/CD pipeline (Vocence)

This document describes how tests run in CI and how the Vocence validator Docker image is built, published, and how validators receive updates automatically.

---

## Overview

- **Trigger:** Every push or pull request to `main`/`master` that touches code, tests, or config (see paths below) triggers the workflow.
- **Tests:** The workflow runs the test suite (`pytest tests/`) first. The Docker image is only built and pushed if tests pass.
- **Build:** On push to `main`/`master`, the workflow builds a multi-platform Docker image (linux/amd64, linux/arm64) and pushes it to **Docker Hub**.
- **Validators:** Validators run the same image via `docker-compose` with **Watchtower**. When a new image is published, Watchtower pulls it and restarts the validator container, so all validators stay up to date without manual steps.

---

## Workflow: CI and Docker Publish

- **File:** [.github/workflows/docker-publish.yml](../.github/workflows/docker-publish.yml)
- **Name:** Vocence - CI and Docker Publish

### When it runs

- **Push** or **pull_request** to `main`/`master`, when any of these paths change:
  - `vocence/**`
  - `tests/**`
  - `pyproject.toml`
  - `uv.lock`
  - `Dockerfile`
  - `.github/workflows/docker-publish.yml`
- **Manual:** You can also run it from the Actions tab (**workflow_dispatch**).

### What it does

1. **Test job:** Checkout, install Python 3.12 and uv, install dependencies (including test extras), run `pytest tests/`. All triggers run this job.
2. **Build-and-push job** (only on **push** to `main`/`master`, and only if tests passed): Checkout, log in to Docker Hub, build the image with Docker Buildx, push to Docker Hub with tags: `latest`, git SHA, branch name, and optional semver-style tags.

### Image name

The image name is:

```text
docker.io/<DOCKERHUB_USERNAME>/vocence
```

Example: if `DOCKERHUB_IMAGE_NAMESPACE` is `vocence78`, the image is `docker.io/vocence78/vocence`.

---

## Setup for maintainers (publishing the image)

1. **Docker Hub**
   - Create a Docker Hub account or org (e.g. `vocence-bt`).
   - Create a repository (e.g. `vocence-bt/vocence` or just use the repo name `vocence`).

2. **Docker Hub access token**
   - Go to [Docker Hub → Security → New Access Token](https://hub.docker.com/settings/security).
   - Create a token with **Read & Write** for the repo.

3. **GitHub Actions secrets**
   - In your GitHub repo: **Settings → Secrets and variables → Actions**.
   - Add:
     - `DOCKERHUB_USERNAME`: Your **personal** Docker Hub username (used only for login; an org name cannot authenticate).
     - `DOCKERHUB_TOKEN`: Docker Hub access token created from your **personal** account.
     - `DOCKERHUB_IMAGE_NAMESPACE`: Namespace (org or username) for the image (e.g. `vocence78`). The image will be `vocence78/vocence`.

4. **Validator image name**
   - Validators must use the **same** image name you publish. Default in `docker-compose.yml` is `vocence78/vocence:latest`.
   - If you use a different Docker Hub user/org, either:
     - Set the default in the workflow/env (e.g. a repo variable or hardcode in the workflow), or
     - Tell validators to set `DOCKER_IMAGE=<your-org>/vocence:latest` in their `.env` when running `docker-compose`.

After this, every qualifying push to `main`/`master` will build and push the image; validators using Watchtower will pick it up within the poll interval (e.g. 5 minutes).

---

## Validator side: Docker + Watchtower

Validators do **not** build from source in production. They:

1. Use the **published image** (e.g. `vocence78/vocence:latest`) in `docker-compose.yml`.
2. Run **Watchtower** in the same stack so it can restart the validator container when a new image is available.

Details (paths, env, volumes, healthcheck) are in [validator-setup.md](validator-setup.md).

### How Watchtower fits in

- Watchtower runs in the same `docker-compose` stack.
- It is configured to only manage containers that have the label `com.centurylinklabs.watchtower.enable=true` (the validator service has this label).
- It polls the registry (e.g. every 300 seconds); when it sees a new image for that container, it pulls it and restarts the container (with `WATCHTOWER_ROLLING_RESTART=true`).
- So when the team pushes a new image to Docker Hub, all validators that pull `vocence78/vocence:latest` (or the configured tag) will auto-update within the poll interval, with no manual steps.

---

## Summary

| Step | Who | Action |
|------|-----|--------|
| Push to main/master (relevant paths) | Developer | Push code; workflow runs automatically (or trigger manually). |
| Build & push | GitHub Actions | Builds image, pushes to Docker Hub (e.g. `vocence78/vocence:latest`). |
| Pull & restart | Watchtower (on each validator) | Polls Docker Hub, pulls new image, restarts validator container. |

All validators running the same repo’s image and Watchtower stay in sync with the latest release without handwork.
