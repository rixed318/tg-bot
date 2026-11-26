#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${1:-rixed}"

scp "$ROOT/src/tg_bot/queue_db.py" "${REMOTE_HOST}:/opt/rixed-joke-bot/joke_bot_queue.py"
scp "$ROOT/src/tg_bot/gateway.py" "${REMOTE_HOST}:/opt/rixed-joke-bot/joke_bot_gateway.py"
scp "$ROOT/deploy/rixed-joke-bot.service" "${REMOTE_HOST}:/etc/systemd/system/rixed-joke-bot.service"

ssh "$REMOTE_HOST" "systemctl daemon-reload && systemctl restart rixed-joke-bot.service && systemctl status --no-pager -n 10 rixed-joke-bot.service"
