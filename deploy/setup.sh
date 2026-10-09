#!/usr/bin/env bash
# Install and start RAGForge on an Ubuntu 24.04 server (docs/DEPLOY.md), for example
# Oracle Cloud's Always Free ARM VM. Run it from the repository, as a user with sudo:
#
#   git clone https://github.com/TXsShadowFox/ragforge.git && cd ragforge && ./deploy/setup.sh
#
# The first time it asks for the server's public IP address and the Groq API key, and
# writes .env with random passwords. Run it again after `git pull` to update: it keeps
# .env, rebuilds the images and restarts what changed.
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=(sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml)

install_docker() {
  if command -v docker >/dev/null; then
    return
  fi
  echo "Installing Docker (from Docker's own package repository)"
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
    "https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin
}

open_firewall() {
  # Oracle's Ubuntu images allow only SSH in iptables. Open HTTP and HTTPS (and QUIC for
  # HTTP/3), and keep the rules after a reboot.
  for rule in "-p tcp --dport 80" "-p tcp --dport 443" "-p udp --dport 443"; do
    # shellcheck disable=SC2086 # the rule is several words on purpose
    if ! sudo iptables -C INPUT $rule -j ACCEPT 2>/dev/null; then
      sudo iptables -I INPUT 1 $rule -j ACCEPT
    fi
  done
  if command -v netfilter-persistent >/dev/null; then
    sudo netfilter-persistent save
  fi
}

secret() {
  openssl rand -hex 24
}

set_value() {
  # set_value KEY VALUE: replace KEY's line in .env (values here have no special characters)
  sed -i "s|^$1=.*|$1=$2|" .env
}

get_value() {
  grep "^$1=" .env | tail -n 1 | cut -d= -f2-
}

write_env() {
  if [[ -f .env ]]; then
    echo "Keeping the existing .env"
    return
  fi
  local ip answer groq_key
  ip=$(curl -4 -fsS https://ifconfig.me || true)
  read -r -p "The server's public IP address [${ip}]: " answer
  ip=${answer:-$ip}
  read -r -s -p "Groq API key (gsk_..., from https://console.groq.com/keys): " groq_key
  echo
  cp .env.example .env
  chmod 600 .env
  set_value APP_ENV prod
  set_value POSTGRES_PASSWORD "$(secret)"
  set_value RABBITMQ_PASSWORD "$(secret)"
  set_value S3_SECRET_KEY "$(secret)"
  set_value JWT_SECRET "$(secret)$(secret)"
  set_value GRAFANA_ADMIN_PASSWORD "$(secret)"
  set_value LLM_API_KEY "$groq_key"
  # sslip.io names point at the IP address in them: HTTPS without buying a domain.
  set_value RAGFORGE_HOST "ragforge.${ip}.sslip.io"
  set_value DEMO_HOST "demo.${ip}.sslip.io"
  set_value GRAFANA_HOST "grafana.${ip}.sslip.io"
  printf '\n# --- The public demo tenant (deploy/seed_demo.py) ---\nDEMO_EMAIL=demo@%s\nDEMO_PASSWORD=%s\n' \
    "ragforge.${ip}.sslip.io" "$(secret)" >> .env
  echo "Wrote .env (only readable by you). Grafana's admin password is in it."
}

main() {
  install_docker
  open_firewall
  write_env
  echo "Building and starting everything (the first time ~15 minutes on 2 ARM CPUs)"
  "${COMPOSE[@]}" up -d --build --wait --wait-timeout 1800
  DEMO_EMAIL=$(get_value DEMO_EMAIL) DEMO_PASSWORD=$(get_value DEMO_PASSWORD) \
    python3 deploy/seed_demo.py --api "https://$(get_value RAGFORGE_HOST)" \
    --demo-site "https://$(get_value DEMO_HOST)"
  echo "Grafana (read-only for visitors): https://$(get_value GRAFANA_HOST)"
}

main "$@"
