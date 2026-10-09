# Deploy

The public demo runs on one server with Docker Compose: the main stack
(`docker-compose.yml`) plus the production file (`docker-compose.prod.yml`). Caddy is the only
public entry, and it gets HTTPS certificates by itself. Any Ubuntu 24.04 server (x86 or ARM)
with 4 GB of RAM or more, a public IP address and ports 80/443 open works. The demo runs on
an AWS EC2 `m7i-flex.large` (2 vCPUs, 8 GB); Oracle Cloud's Always Free ARM VM (2 CPUs,
12 GB) is the option that stays free with no time limit.

## What runs where

| Address | What |
|---|---|
| `https://ragforge.<ip>.sslip.io` | the dashboard, and the API for programs and the widget (`/v1`, `/docs`, `/widget.js`) |
| `https://demo.<ip>.sslip.io` | the widget demo: a "customer website" on its own address |
| `https://grafana.<ip>.sslip.io` | the live metrics, read-only for visitors |

Postgres, Redis, RabbitMQ, Qdrant, RustFS, Prometheus and Jaeger are reachable only inside
Docker. [sslip.io](https://sslip.io) names point at the IP address written in them, so HTTPS
works without buying a domain. With a domain, put its names in `.env` instead.

Limits for a public demo (in `docker-compose.prod.yml`): uploads up to 5 MB, at most 20
documents per tenant, plus the usual rate limits. The LLM is Groq's free tier (about 1,000
requests a day): when it is used up, questions get "busy" (503) until the next day.

## 1. Create the server

### AWS EC2 (the demo)

New AWS accounts get $100 of credit, and up to $100 more for a few "getting started" tasks
(launching an instance, a budget). On the **Free plan** nothing is ever charged: after 6
months, or when the credit is used up, AWS closes the account instead. An `m7i-flex.large`
with a fixed IP and 30 GB costs about $70-75 a month (estimate), so the credit lasts roughly
1.5 to 3 months.

1. Create an account at [aws.amazon.com](https://aws.amazon.com) and choose the **Free plan**.
2. Choose a region at the top right, and do everything below **in that region**: key pairs
   belong to one region (a key imported in Mumbai does not exist in Sydney).
3. EC2 > Key Pairs > Actions > **Import key pair**: your public key
   (`ssh-keygen -t ed25519` makes one).
4. EC2 > Instances > **Launch instances**:
   - Image: **Ubuntu Server 24.04 LTS** (x86)
   - Instance type: **m7i-flex.large** (8 GB; "Free tier eligible"), or c7i-flex.large (4 GB)
   - Key pair: the one you imported
   - Network settings: allow **SSH**, **HTTP** and **HTTPS** traffic from the internet
   - Storage: **30 GB** gp3
5. EC2 > Elastic IPs > **Allocate**, then **Associate** it with the instance: the address
   stays the same after a restart (the demo's addresses contain it).

If SSH says `Permission denied (publickey)`, the instance has another key: open it in the
console > **Connect** > **EC2 Instance Connect**, and add your public key to
`~/.ssh/authorized_keys` there.

### Oracle Cloud Always Free (free, no time limit)

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com) (Free Tier). The home region
   cannot be changed later, and the free ARM VM runs only there. The card only checks who
   you are; Always Free resources are not charged.
2. Compute > Instances > **Create instance**:
   - Image: **Canonical Ubuntu 24.04**
   - Shape: Ampere **VM.Standard.A1.Flex**, **2 OCPUs, 12 GB** of memory
   - Networking: a public subnet, with a public IPv4 address
   - SSH keys: paste your public key
   - "Out of capacity" is common for free ARM VMs: try another availability domain, or
     again a few hours later.
3. Open HTTP and HTTPS: Networking > Virtual cloud networks > your network > Security Lists >
   Default Security List > **Add Ingress Rules**: source `0.0.0.0/0`, TCP, destination
   port `80`; again for `443`.

Oracle can reclaim Always Free VMs that stay idle (low CPU, network and memory use for 7
days). The running stack uses more than 20% of the memory, so it does not count as idle.

## 2. Install

```bash
ssh -i ~/.ssh/<your key> ubuntu@<ip>
git clone https://github.com/TXsShadowFox/ragforge.git
cd ragforge
./deploy/setup.sh
```

`deploy/setup.sh`:

1. installs Docker (from Docker's own package repository);
2. opens ports 80 and 443 in the server's firewall (Oracle's Ubuntu images allow only SSH);
3. the first time, asks for the server's public IP address and the Groq API key (or reads
   them from `RAGFORGE_IP` and `GROQ_API_KEY`, to run without questions), and writes `.env`
   with random passwords (readable only by you; never in git);
4. builds the images and starts everything (~10-15 minutes the first time on 2 CPUs);
5. runs `deploy/seed_demo.py`: a demo tenant with the 6 sample college documents and a
   public key for the demo site, written to `deploy/demo-config.js` (the demo page loads it),
   so the demo's link stays `https://demo.<ip>.sslip.io` after every update.

## 3. Update

```bash
cd ragforge && git pull && ./deploy/setup.sh
```

It keeps `.env`, rebuilds the images and restarts what changed, and Caddy (a second), so it
reads the new Caddyfile and demo page. The data (Docker volumes) and the certificates stay.

## Run it

```bash
COMPOSE="sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE ps                 # status
$COMPOSE logs -f api        # logs of one service (each keeps at most 3 x 10 MB)
$COMPOSE restart api
$COMPOSE exec postgres pg_dump -U ragforge ragforge > backup.sql   # a database backup
```

Grafana's admin login is in `.env` (`GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD`).

## Try the production setup on your laptop

The same files, with names that point at `127.0.0.1` and Caddy's own certificates (Let's
Encrypt cannot reach a laptop). Ports 80 and 443 must be free.

```bash
export RAGFORGE_HOST=ragforge.127.0.0.1.sslip.io DEMO_HOST=demo.127.0.0.1.sslip.io \
  GRAFANA_HOST=grafana.127.0.0.1.sslip.io CADDY_GLOBAL_OPTIONS=local_certs
touch deploy/demo-config.js   # the demo's key goes here (seed_demo.py)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --wait
DEMO_EMAIL=demo@example.com DEMO_PASSWORD=demo-password-123 python deploy/seed_demo.py \
  --api https://$RAGFORGE_HOST --demo-site https://$DEMO_HOST --insecure
```

Browsers warn about the certificate (it is Caddy's own); `--insecure` lets the seed script
accept it. `make down` stops everything.

## When something goes wrong

- **The site does not load:** check the security list (ports 80/443), then
  `sudo iptables -L INPUT -n` (setup.sh adds ACCEPT rules at the top).
- **No certificate:** Caddy needs ports 80 and 443 open from the internet, and the name must
  point at the server. `$COMPOSE logs caddy` shows why.
- **Answers say "busy":** Groq's free tier is used up for the day, or for the minute.
