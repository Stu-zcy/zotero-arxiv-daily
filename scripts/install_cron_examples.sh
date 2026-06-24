#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_ID="${1:-all}"
USER_FLAG='--all-users'
if [[ "$USER_ID" != "all" ]]; then
  USER_FLAG="--user \"$USER_ID\""
fi

cat <<EOF
# Add these lines with: crontab -e
# Daily push: every day at 08:00
0 8 * * * cd "$PROJECT_DIR" && uv run src/zotero_arxiv_daily/main.py $USER_FLAG --mode daily --send-email true executor.max_paper_num=10 >> "logs/cron-daily.out" 2>&1

# Monthly push: first day of each month at 09:00, covering the previous calendar month
0 9 1 * * cd "$PROJECT_DIR" && uv run src/zotero_arxiv_daily/main.py $USER_FLAG --mode monthly executor.max_paper_num=15 >> "logs/cron-monthly.out" 2>&1
EOF
