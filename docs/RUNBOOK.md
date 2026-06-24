# Zotero Paper Digest Runbook

This repository runs two kinds of paper digests:

- Daily: arXiv `cs.CR` primary announcements, user-topic filtering, plus IACR ePrint RSS.
- Monthly/range: CCF 2026 A/B venues selected per user profile, retrieved from Crossref and filtered by user-specific topic groups. The default profiles include `网络与信息安全`, `计算机科学理论`, and selected `计算机体系结构/并行与分布计算/存储系统` venues so cryptographic implementation papers such as PQC/FHE acceleration are not missed.

## 1. Install

Install `uv` first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then sync the project:

```bash
cd /path/to/zotero-arxiv-daily
uv sync
```

## 2. Configure Secrets

Keep secrets local. Do not commit `.env` or `users.local.yaml`.

`.env`:

```bash
ZOTERO_ID=...
ZOTERO_KEY=...
OPENAI_API_KEY=...
OPENAI_API_BASE=https://api.deepseek.com
SENDER=...
SENDER_PASSWORD=...
RECEIVER=...
OPENALEX_MAILTO=...
DEBUG=false
```

`users.local.yaml`:

```yaml
users:
  liruoyi:
    zotero:
      user_id: "..."
      api_key: "..."
    email:
      receiver: lxr@example.com
```

Tracked user defaults live in `users.yaml`. Local values override them.

## 3. Manual Runs

Run one user:

```bash
uv run src/zotero_arxiv_daily/main.py --user chenyang --mode daily --send-email true
uv run src/zotero_arxiv_daily/main.py --user liruoyi --mode monthly executor.max_paper_num=15
```

Run all configured users:

```bash
uv run src/zotero_arxiv_daily/main.py --all-users --mode daily --send-email true executor.max_paper_num=10
uv run src/zotero_arxiv_daily/main.py --all-users --mode monthly executor.max_paper_num=15
```

Run a replay/range digest:

```bash
uv run src/zotero_arxiv_daily/main.py \
  --all-users \
  --mode test-range \
  --start-date 2025-12-24 \
  --end-date 2026-06-24 \
  --send-email true \
  executor.max_paper_num=15
```

## 3.1 Monthly Search Strategy

Zotero is used for reranking, not as the primary search query source. The monthly search is driven by each user's `users.yaml` profile:

- `profile.monthly_fields`: CCF fields to monitor.
- `profile.topics`: topic groups with keywords.
- `profile.min_topic_score`: minimum number of keyword hits inside one topic group.
- `profile.search_queries`: supplemental Crossref bibliographic queries for recall.

The Crossref retriever first pulls papers by CCF venue and date, then applies strict local venue matching. It also runs supplemental bibliographic searches and keeps only papers that still match a selected CCF venue and a user topic. Short cryptographic tokens such as `LWE`, `SIS`, `FHE`, and `PQC` are matched with word boundaries to avoid accidental substring hits.

## 4. Logs And State

- Logs: `logs/{user}/{mode}/YYYY-MM-DD.log`
- Daily/monthly seen state: `state/{user}/seen.json`
- `test-range` ignores seen state so historical reports can be replayed.

If Gmail does not show an email, search for:

```text
Paper Digest [user/mode]
from:chen.zcy@foxmail.com
```

Also check Spam, All Mail, and Gmail filters. The program logs `SMTP accepted message for ...` only after the SMTP server accepts the recipient; that does not guarantee Gmail placed it in Inbox.

## 5. Background Scheduling

macOS/Linux cron examples:

```bash
scripts/install_cron_examples.sh chenyang
scripts/install_cron_examples.sh liruoyi
```

Windows Task Scheduler:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows_tasks.ps1 -UserId chenyang
powershell -ExecutionPolicy Bypass -File scripts/install_windows_tasks.ps1 -UserId liruoyi
```

For all users, schedule these commands directly:

```bash
uv run src/zotero_arxiv_daily/main.py --all-users --mode daily --send-email true executor.max_paper_num=10
uv run src/zotero_arxiv_daily/main.py --all-users --mode monthly executor.max_paper_num=15
```

## 6. GitHub Upload

The current clone still points to the upstream repository. To upload as your own private repository:

```bash
git remote rename origin upstream
gh auth login
gh repo create zotero-arxiv-daily --private --source=. --remote=origin --push
```

If `gh` is unavailable, create an empty private repository on GitHub and run:

```bash
git remote rename origin upstream
git remote add origin git@github.com:<your-account>/zotero-arxiv-daily.git
git push -u origin main
```
