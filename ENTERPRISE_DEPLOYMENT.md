# Enterprise Deployment Guide

Step-by-step plan to deploy the VIP AI Platform to a private on-premise server (no Vercel, no cloud, no Supabase).

> **Use case:** ~10 internal users (boss + team), ~10 Agents + ~10 Twins, all data stays inside the company server.

---

## Target Architecture

```
┌──────────────────────────────────────────────────────────┐
│  ENTERPRISE SERVER (Ubuntu 22.04, 32GB+ RAM, NVIDIA GPU) │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Tailscale VPN  (only way users connect)           │  │
│  └─────────────────┬──────────────────────────────────┘  │
│                    │                                     │
│  ┌─────────────────▼──────────────────────────────────┐  │
│  │  Nginx Reverse Proxy + SSL  (port 443 only)        │  │
│  │   • https://vip.company.local    → Dashboard       │  │
│  │   • https://twin.company.local   → Twin Portal     │  │
│  │   • https://api.vip.company.local → Orchestrator   │  │
│  │   • https://auth.vip.company.local → Keycloak SSO  │  │
│  └─────┬─────────┬─────────┬─────────┬─────────┬──────┘  │
│        │         │         │         │         │         │
│        ▼         ▼         ▼         ▼         ▼         │
│  ┌────────┐ ┌────────┐ ┌─────────┐ ┌────────┐ ┌──────┐   │
│  │ Admin  │ │  Twin  │ │OpenClaw │ │Keycloak│ │ MinIO│   │
│  │Dashbrd │ │ Portal │ │Gateway  │ │  SSO   │ │Files │   │
│  └────────┘ └────────┘ └────┬────┘ └────────┘ └──────┘   │
│                             │                            │
│                             ▼                            │
│                  ┌────────────────────┐                  │
│                  │ VIP Orchestrator   │                  │
│                  │ (Brain — FastAPI)  │                  │
│                  └────┬──────────┬────┘                  │
│                       │          │                       │
│                ┌──────▼──┐  ┌────▼─────┐                 │
│                │10 Agents│  │ 10 Twins │                 │
│                └─────────┘  └──────────┘                 │
│                       │          │                       │
│                       └────┬─────┘                       │
│                            ▼                             │
│              ┌─────────────────────────────┐             │
│              │ vLLM + Qwen 2.5 14B (Local) │             │
│              └─────────────────────────────┘             │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ PostgreSQL 16 + pgvector  (all app data)           │  │
│  │ Redis 7                   (cache + queue)          │  │
│  │ Prometheus + Grafana      (monitoring + logs)      │  │
│  │ Restic                    (encrypted backups)      │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU       | 8 cores | 16+ cores |
| RAM       | 32 GB   | 64 GB |
| GPU       | NVIDIA 12GB VRAM | NVIDIA 24GB+ (RTX 4090, A6000) |
| Storage   | 1 TB NVMe | 2 TB NVMe + 4 TB HDD (backups) |
| Network   | 1 Gbit  | 10 Gbit |
| OS        | Ubuntu Server 22.04 LTS | Ubuntu Server 22.04 LTS |

> **Note:** 32GB RAM works but is tight. With vLLM + Postgres + 10 agents + 10 twins + monitoring, you will hit limits. Plan to upgrade to 64GB.

---

## Phase 1 — Server Foundation (Week 1)

### Step 1.1 — Install Ubuntu Server 22.04 LTS

1. Download Ubuntu Server 22.04 LTS ISO
2. Boot installer on target machine, install with these options:
   - Hostname: `vip-server`
   - User: `vipadmin`
   - Enable OpenSSH server
   - No snap apps
3. After install, log in via SSH from your workstation:
   ```bash
   ssh vipadmin@192.168.1.100
   ```

### Step 1.2 — System Update + Essentials

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git ufw htop nvtop build-essential
```

### Step 1.3 — Set Static IP

Edit `/etc/netplan/01-netcfg.yaml`:
```yaml
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: false
      addresses: [192.168.1.100/24]
      gateway4: 192.168.1.1
      nameservers:
        addresses: [1.1.1.1, 8.8.8.8]
```
Apply:
```bash
sudo netplan apply
```

### Step 1.4 — Firewall (UFW)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh                # SSH for admin
sudo ufw allow 41641/udp          # Tailscale
sudo ufw enable
```

> **Note:** Do NOT open ports 80/443 publicly — Tailscale handles secure access.

---

## Phase 2 — Install Core Tools (Week 1)

### Step 2.1 — Install Docker + Docker Compose

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker vipadmin
newgrp docker
docker --version
docker compose version
```

### Step 2.2 — Install NVIDIA GPU Drivers + Container Toolkit

```bash
sudo apt install -y nvidia-driver-535
sudo reboot

# After reboot, verify:
nvidia-smi

# Install NVIDIA Container Toolkit (so Docker can use GPU)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update
sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Verify GPU works inside Docker:
docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu22.04 nvidia-smi
```

### Step 2.3 — Install Tailscale (VPN for users)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Follow URL printed in terminal to authorize this machine
# Note the assigned name (e.g., vip-server.tailA1b2c3.ts.net)
```

---

## Phase 3 — Clone the Project (Week 1)

### Step 3.1 — Generate SSH Key for GitHub

```bash
ssh-keygen -t ed25519 -C "vipadmin@vip-server"
cat ~/.ssh/id_ed25519.pub
# Copy the output, add as Deploy Key to:
# https://github.com/tripleh-aiteam/VIP-Agent/settings/keys
```

### Step 3.2 — Clone Repo

```bash
mkdir -p /opt/vip
cd /opt/vip
git clone git@github.com:tripleh-aiteam/VIP-Agent.git
cd VIP-Agent
```

### Step 3.3 — Create Production `.env`

```bash
cp .env.example .env.prod
nano .env.prod
```

Set these values (use strong passwords — generate with `openssl rand -base64 32`):
```
POSTGRES_USER=vip
POSTGRES_PASSWORD=<strong-random-password>
POSTGRES_DB=vip_platform

REDIS_URL=redis://redis:6379/0

KEYCLOAK_ADMIN=admin
KEYCLOAK_ADMIN_PASSWORD=<strong-random-password>

MINIO_ROOT_USER=vipadmin
MINIO_ROOT_PASSWORD=<strong-random-password>

LLM_BASE_URL=http://vllm:8000/v1
LLM_MODEL=Qwen/Qwen2.5-14B-Instruct
```

> Make sure `.env.prod` is in `.gitignore`. **Never commit secrets.**

---

## Phase 4 — Deploy Local LLM (Week 2)

### Step 4.1 — Pull Model Weights (One-Time Download)

```bash
mkdir -p /opt/vip/models
cd /opt/vip/models

# Install huggingface-cli
pip install huggingface_hub
huggingface-cli download Qwen/Qwen2.5-14B-Instruct --local-dir ./qwen2.5-14b
```

> This downloads ~28GB. Run once, then disconnect from internet if going air-gapped.

### Step 4.2 — Run vLLM Server

Add to `docker-compose.prod.yml`:
```yaml
vllm:
  image: vllm/vllm-openai:latest
  command: >
    --model /models/qwen2.5-14b
    --gpu-memory-utilization 0.9
    --max-model-len 8192
  ports: ["8100:8000"]
  volumes:
    - /opt/vip/models:/models
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
  restart: unless-stopped
```

Test:
```bash
docker compose -f docker-compose.prod.yml up -d vllm
curl http://localhost:8100/v1/models
```

---

## Phase 5 — Deploy Core Services (Week 2-3)

### Step 5.1 — Start Database + Cache + Storage

```bash
docker compose -f docker-compose.prod.yml up -d postgres redis minio
docker compose -f docker-compose.prod.yml ps
```

### Step 5.2 — Start Auth (Keycloak)

```bash
docker compose -f docker-compose.prod.yml up -d keycloak
# Wait 30s for first-time DB init
# Open https://auth.vip.company.local
# Login: admin / <KEYCLOAK_ADMIN_PASSWORD>
# Create realm: "vip"
# Create 10 users (one per employee)
# Enable 2FA for all users
```

### Step 5.3 — Start All VIP Services

```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```

Expected services running:
- `vip-postgres`, `vip-redis`, `vip-minio`
- `vip-keycloak`
- `vip-vllm`
- `vip-orchestrator`, `vip-gateway`, `vip-judgement`, `vip-report-composer`
- `vip-dashboard`, `vip-twin-portal`
- `vip-nginx`

---

## Phase 6 — Nginx Reverse Proxy + Internal SSL (Week 3)

### Step 6.1 — Generate Self-Signed SSL Cert (Internal Use)

```bash
sudo mkdir -p /etc/vip/certs
cd /etc/vip/certs
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout vip.key -out vip.crt \
  -subj "/CN=*.vip.company.local"
```

### Step 6.2 — Configure Nginx

Create `/opt/vip/VIP-Agent/infra/nginx/nginx.conf`:
```nginx
events { worker_connections 1024; }

http {
  upstream dashboard { server admin-dashboard:3000; }
  upstream twin      { server twin-portal:3001; }
  upstream api       { server orchestrator-api:8000; }
  upstream auth      { server keycloak:8080; }

  # VIP Dashboard
  server {
    listen 443 ssl;
    server_name vip.company.local;
    ssl_certificate /etc/ssl/vip.crt;
    ssl_certificate_key /etc/ssl/vip.key;
    location / { proxy_pass http://dashboard; proxy_set_header Host $host; }
  }

  # Twin Portal
  server {
    listen 443 ssl;
    server_name twin.company.local;
    ssl_certificate /etc/ssl/vip.crt;
    ssl_certificate_key /etc/ssl/vip.key;
    location / { proxy_pass http://twin; proxy_set_header Host $host; }
  }

  # Orchestrator API
  server {
    listen 443 ssl;
    server_name api.vip.company.local;
    ssl_certificate /etc/ssl/vip.crt;
    ssl_certificate_key /etc/ssl/vip.key;
    location / { proxy_pass http://api; proxy_set_header Host $host; }
  }

  # Keycloak SSO
  server {
    listen 443 ssl;
    server_name auth.vip.company.local;
    ssl_certificate /etc/ssl/vip.crt;
    ssl_certificate_key /etc/ssl/vip.key;
    location / { proxy_pass http://auth; proxy_set_header Host $host; }
  }
}
```

### Step 6.3 — Configure DNS for Internal URLs

**Option A — Router DNS (best, configure once for entire office):**
- Log into office router (UniFi / pfSense / OPNsense)
- Add local DNS records:
  ```
  vip.company.local       → 192.168.1.100
  twin.company.local      → 192.168.1.100
  api.vip.company.local   → 192.168.1.100
  auth.vip.company.local  → 192.168.1.100
  ```

**Option B — Per-laptop hosts file (fallback):**

On each user's laptop, edit:
- Windows: `C:\Windows\System32\drivers\etc\hosts`
- Mac/Linux: `/etc/hosts`

Add lines:
```
192.168.1.100   vip.company.local
192.168.1.100   twin.company.local
192.168.1.100   api.vip.company.local
192.168.1.100   auth.vip.company.local
```

### Step 6.4 — Distribute SSL Cert to User Laptops

Copy `/etc/vip/certs/vip.crt` to each user's laptop and install as a trusted root CA. This removes the browser SSL warning.

---

## Phase 7 — Monitoring + Logging (Week 4)

### Step 7.1 — Add Prometheus + Grafana + Loki to Compose

Already in template — start them:
```bash
docker compose -f docker-compose.prod.yml up -d prometheus grafana loki
```

### Step 7.2 — Access Grafana

```
https://grafana.vip.company.local
Default login: admin / admin (change immediately)
```

Import dashboards:
- Dashboard ID `1860` (Node Exporter)
- Dashboard ID `13639` (Loki Logs)
- Dashboard ID `9628` (PostgreSQL)

---

## Phase 8 — Backups (Week 4)

### Step 8.1 — Set Up Restic for Encrypted Backups

```bash
sudo apt install -y restic
restic init --repo /mnt/backup-drive/vip-restic
# Save the password printed by Restic to a secure place
```

### Step 8.2 — Backup Cron Job

Add to `/etc/cron.d/vip-backup`:
```cron
# Daily DB backup at 2 AM
0 2 * * * vipadmin docker exec vip-postgres pg_dumpall -U vip > /opt/vip/backups/db-$(date +\%F).sql

# Daily file backup at 3 AM
0 3 * * * vipadmin restic -r /mnt/backup-drive/vip-restic backup /opt/vip --password-file /opt/vip/.restic-pass

# Weekly cleanup (keep last 30 daily, 12 weekly, 6 monthly)
0 4 * * 0 vipadmin restic -r /mnt/backup-drive/vip-restic forget --keep-daily 30 --keep-weekly 12 --keep-monthly 6 --prune
```

---

## Phase 9 — Add Users + Hand Off (Week 5)

### Step 9.1 — Onboard 10 Users to Tailscale

For each employee:
1. Send invite to Tailscale network (admin: https://login.tailscale.com)
2. They install Tailscale client (Windows/Mac/iOS/Android)
3. They log in
4. Confirm they can ping `vip-server.tailA1b2c3.ts.net`

### Step 9.2 — Create Users in Keycloak

For each employee, in Keycloak admin:
1. `Users → Add User`
2. Set username, email, full name
3. `Credentials → Set Password` (force reset on first login)
4. Enable OTP/2FA requirement
5. Assign role (`vip-user` or `vip-admin`)

### Step 9.3 — User Access Test

Each user opens browser:
```
https://vip.company.local      → VIP Dashboard
https://twin.company.local     → My Twin
```
Login via Keycloak. Confirm full functionality.

---

## Phase 10 — Hardening + Documentation (Week 5-6)

### Security Checklist

- [ ] All `.env*` files in `.gitignore`
- [ ] No public ports open (only Tailscale)
- [ ] All passwords are strong (≥32 chars)
- [ ] 2FA enforced for all Keycloak users
- [ ] Daily encrypted backups verified
- [ ] Grafana alerts configured for: disk full, GPU OOM, service down
- [ ] OS auto-security-updates enabled (`unattended-upgrades`)
- [ ] SSH key-only login (disable password auth in `/etc/ssh/sshd_config`)
- [ ] Postgres only accessible inside Docker network
- [ ] Nginx logs rotated weekly
- [ ] Restic backup restore test passed

### Documentation to Write

- [ ] Runbook: how to restart a service
- [ ] Runbook: how to restore from backup
- [ ] Runbook: how to add new agent/twin
- [ ] Runbook: how to update LLM model
- [ ] Runbook: how to onboard new user
- [ ] Disaster recovery plan

---

## Update Workflow (After Initial Deploy)

When you push code to GitHub:

```bash
# On the server:
cd /opt/vip/VIP-Agent
git pull origin main
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

For zero-downtime deploys (advanced), use a deploy script with rolling restarts.

---

## Estimated Timeline

| Week | Phase | Deliverable |
|------|-------|-------------|
| 1    | Foundation + Tools | Server ready, Docker + GPU + Tailscale working |
| 2    | LLM + Core Services | vLLM serving Qwen, DB + Redis up |
| 3    | App Deploy + Nginx | All services accessible via HTTPS URLs |
| 4    | Monitoring + Backups | Grafana dashboards, daily backups verified |
| 5    | Users + Hardening | 10 users onboarded, security checklist done |
| 6    | Documentation + Demo | Runbooks written, demo to boss |

---

## Cost Estimate

| Item | Cost |
|------|------|
| Software (all open source) | $0 |
| Tailscale (≤100 users) | $0 |
| SSL (self-signed internal) | $0 |
| Server hardware (one-time) | ~$3,000-$8,000 (depending on GPU) |
| Backup drive (4TB external) | ~$120 |
| **Total recurring** | **$0/month** |
| **Total one-time** | **~$3,000-$8,000** |

Compare to cloud: ~$2,000-$5,000/month for equivalent setup on AWS/GCP.

---

## Reference URLs

- Tailscale: https://tailscale.com
- vLLM: https://docs.vllm.ai
- Keycloak: https://www.keycloak.org
- Qwen 2.5: https://huggingface.co/Qwen/Qwen2.5-14B-Instruct
- pgvector: https://github.com/pgvector/pgvector
- MinIO: https://min.io
- Restic: https://restic.net

---

## Support

For issues during deployment, check:
1. `docker compose logs <service-name>` — service-specific errors
2. `nvidia-smi` — GPU status
3. `tailscale status` — VPN connectivity
4. Grafana dashboards — real-time metrics
