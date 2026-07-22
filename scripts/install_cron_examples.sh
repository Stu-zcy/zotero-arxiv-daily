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
# Daily push: every day at 09:00
0 9 * * * cd "$PROJECT_DIR" && uv run src/zotero_arxiv_daily/main.py $USER_FLAG --mode daily executor.max_paper_num=10 >> "logs/cron-daily.out" 2>&1

# Monthly push: first day of each month at 10:00, covering the previous calendar month
0 10 1 * * cd "$PROJECT_DIR" && uv run src/zotero_arxiv_daily/main.py $USER_FLAG --mode monthly executor.max_paper_num=15 >> "logs/cron-monthly.out" 2>&1
EOF
