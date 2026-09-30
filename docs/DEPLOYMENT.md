# MarketEdge AI — Deployment

There are three supported targets, all built from the same two images (`backend/`, `frontend/`):

| Target | Use for | Files |
|---|---|---|
| Docker Compose (dev) | local development, demos with SAMPLE data | `docker-compose.yml` |
| Docker Compose (production, single host) | a small deployment on one VM with automatic TLS | `docker-compose.prod.yml`, `ops/caddy/Caddyfile` |
| Kubernetes | anything that needs HA or autoscaling (EKS, AKS, GKE, or self-managed) | `deploy/k8s/` (kustomize) |

Nothing here is tied to one cloud. The notes below map each piece to AWS and Azure services.

## Production checklist (every target)

**Licensed data.** Production refuses to start with sample (`DEMO_`) providers.
* Configure licensed providers (see ARCHITECTURE §12).
* Or, for a clearly labelled demo only, set `ALLOW_SAMPLE_IN_PRODUCTION=true`.

**Secrets:**
* `SECRET_KEY` (≥ 32 random chars)
* `ENCRYPTION_KEY` (Fernet)
* `METRICS_TOKEN`
* the database password

Supply them as files (`*_FILE`) from Docker/Kubernetes secrets or your cloud secret manager, never baked into images. Provider API keys go in Admin → Providers, where they are stored encrypted.

**Enforced at start-up:**
* `COOKIE_SECURE=true` and TLS end to end at the edge;
* default or short secrets are refused.

**Backups** are scheduled and a restore has been rehearsed ([OPERATIONS.md](OPERATIONS.md#backups-and-restore)). `ENCRYPTION_KEY` is stored separately from them.

**Monitoring:**
* Prometheus scrapes `/metrics` with the token;
* alert rules are loaded;
* logs are shipped (`LOG_FORMAT=json`).

**Exactly one `beat` scheduler.**

## Single host: `docker-compose.prod.yml`

Caddy obtains and renews Let's Encrypt certificates. It is the only service that publishes ports (80 for the ACME challenge and redirect, 443). Postgres and Redis sit on the `data` network, which is marked `internal` and has no route in or out. The API, workers, beat and frontend run with a read-only root filesystem, `no-new-privileges` and all capabilities dropped; Redis also runs read-only.

```sh
cp .env.example .env
# edit .env: ENVIRONMENT=production, DOMAIN=marketedge.example.com, ACME_EMAIL=you@example.com,
#            MARKET_DATA_PROVIDER=..., *_DATA_PROVIDER=..., BOOTSTRAP_ADMIN_EMAIL/PASSWORD (first boot only)
mkdir -p secrets && chmod 700 secrets
openssl rand -hex 32 > secrets/secret_key
python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())" > secrets/encryption_key
openssl rand -hex 24 > secrets/postgres_password
openssl rand -hex 24 > secrets/metrics_token
docker compose -f docker-compose.prod.yml up -d --build
```

* **DNS:** point the domain's A/AAAA record at the host before the first start, so the certificate can be issued.
* **Migrations:** the API runs `alembic upgrade head` on start (`RUN_MIGRATIONS=true`).
* **Backups:** go to `./backups` nightly. Set `BACKUP_UPLOAD_CMD` for an off-host copy.
* **Sizing:** one VM with 4–8 vCPU and 16 GB RAM covers a few thousand users on EOD data. Measured numbers are in ARCHITECTURE §13.

## Kubernetes: `deploy/k8s`

```sh
kubectl create namespace marketedge
kubectl -n marketedge create secret generic marketedge-secrets \
  --from-literal=DATABASE_URL='postgresql+psycopg://marketedge:…@DB_HOST:5432/marketedge?sslmode=require' \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=ENCRYPTION_KEY="$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')" \
  --from-literal=METRICS_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=PGPASSWORD='…'
# edit deploy/k8s/overlays/production/kustomization.yaml: registry, tag, hostname
kubectl apply -k deploy/k8s/overlays/production
kubectl -n marketedge wait --for=condition=complete job/migrate --timeout=5m
```

The base contains:

**Workloads:**
* `api`: 2–8 replicas behind an HPA at 70% CPU, with a PDB. Probes: `/ready` for readiness, `/health` for liveness and startup. It does **not** migrate on start.
* `migrate`: a Job that runs `alembic upgrade head`. Run it before each rollout; it is an Argo CD PreSync hook, or CI deletes and re-applies it.
* `worker`, `worker-scans`, `worker-backtests`: one Deployment per Celery queue (see OPERATIONS). They have long termination grace periods so running tasks can finish; `acks_late` plus `reject_on_worker_lost` redelivers anything interrupted.
* `beat`: 1 replica with the `Recreate` strategy, so there is never a second scheduler.
* `frontend`: 2–6 replicas behind an HPA, with a PDB.
* `redis`: in-cluster, with no persistence needed. To use a managed Redis instead, delete it from the kustomization and set `REDIS_URL` (`rediss://` for TLS).
* `db-backup`: a nightly CronJob running `pg_dump` to a PVC.

**Networking:**
* **Ingress:** cert-manager TLS (ClusterIssuer `letsencrypt`). Only the **frontend** is exposed; it proxies `/api/v1/*` to the internal API Service, so `/metrics` is never public.
* **NetworkPolicies:** default-deny, then:
  * ingress-controller → frontend → api;
  * `monitoring` namespace → api;
  * backend pods → redis, Postgres (private CIDRs, port 5432) and HTTPS/SMTP egress.

**Hardening:**
* The namespace enforces Pod Security `restricted`.
* Every pod runs as non-root with a RuntimeDefault seccomp profile.
* Every container has a read-only root filesystem, all capabilities dropped and no privilege escalation.
* ServiceAccount tokens are not mounted.
* `/tmp` is an emptyDir.
* Secrets are mounted as files at `/run/secrets` (mode 0400) and read through `*_FILE`.

**Storage.** The `marketdata` PVC (licensed CSV drops) is shared by the API and workers, so it needs a **ReadWriteMany** storage class. Remove it if you use API-based providers only.

### AWS mapping

| Piece | AWS |
|---|---|
| Kubernetes | EKS (managed node groups; Karpenter for worker nodes) |
| Postgres | RDS for PostgreSQL 16, Multi-AZ, PITR on, `sslmode=require` |
| Redis | ElastiCache for Redis (TLS → `rediss://`), or keep in-cluster |
| Ingress / TLS | AWS Load Balancer Controller: set `ingressClassName: alb`, the `alb.ingress.kubernetes.io/*` annotations and ACM certificate ARNs instead of cert-manager |
| Secrets | Secrets Manager via External Secrets Operator → `marketedge-secrets` |
| RWX volume | EFS CSI driver |
| Backups off-site | `BACKUP_UPLOAD_CMD='aws s3 cp "$1" s3://bucket/marketedge/'` (IRSA role; bucket versioning + lifecycle) |
| Registry | ECR |
| Logs / metrics | CloudWatch Container Insights, or Amazon Managed Prometheus + Grafana |
| Edge | AWS WAF on the ALB (rate-based and managed rule groups) |

### Azure mapping

| Piece | Azure |
|---|---|
| Kubernetes | AKS (a user node pool for workers) |
| Postgres | Azure Database for PostgreSQL Flexible Server, zone-redundant HA, PITR |
| Redis | Azure Cache for Redis (TLS, port 6380 → `rediss://…:6380/0`) |
| Ingress / TLS | Application Gateway for Containers or ingress-nginx + cert-manager (as shipped) |
| Secrets | Key Vault via the Secrets Store CSI driver (sync to `marketedge-secrets`) or External Secrets |
| RWX volume | Azure Files (`azurefile-csi`) |
| Backups off-site | `BACKUP_UPLOAD_CMD='az storage blob upload --auth-mode login -c backups -f "$1" -n "$(basename "$1")"'` (workload identity) |
| Registry | ACR |
| Logs / metrics | Azure Monitor managed Prometheus + Managed Grafana, Container Insights |
| Edge | Front Door / App Gateway WAF |

## CI/CD: `.github/workflows/ci.yml`

The pipeline runs on every pull request and every push to `main`.

**Backend:**
* ruff lint;
* Alembic upgrade → downgrade → upgrade against a real Postgres 16 service;
* the pytest suite, including the parallel-equals-serial check with real `fork` on Linux;
* bandit;
* pip-audit.

**Frontend:**
* `tsc`;
* ESLint;
* production build;
* `npm audit --audit-level=high`.

**Manifests:**
* `kubectl kustomize` render;
* `docker compose config` for both compose files.

**Images:** built with Buildx and a GitHub Actions cache. Both images are scanned with Trivy, and the job fails on fixable CRITICAL/HIGH findings. On `main`, both images are pushed to GHCR, tagged with the commit SHA.

Production images contain no pip, setuptools, wheel or npm, so you cannot `pip install` inside a production container. For debugging, use the dev compose (built with `DEV_TOOLS=true`) or a debug sidecar.

Deployment itself is deliberately left out: pick Argo CD/Flux (GitOps on the overlay) or add a `kubectl apply -k` job with environment protection rules.
