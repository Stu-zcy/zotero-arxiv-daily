# 密码学论文自动调研推送

这是基于 `zotero-arxiv-daily` 改造的个人论文调研系统，用于为多个用户自动推送密码学方向论文。当前主要覆盖：

- FHE / 全同态加密
- FHE 电路构造与实现
- 格密码与格基础理论
- PQC / 后量子密码
- hash-based PQC 与硬件/并行加速
- 公钥密码学、KEM
- 承诺、多项式承诺、向量承诺
- 属性加密、TEE/机密计算等扩展方向

系统支持两类推送：

- `daily`: arXiv `cs.CR` 主类全量候选（再由 Zotero rerank）+ 按用户关键词过滤的 IACR ePrint RSS
- `monthly` / `test-range`: CCF 2026 A/B venue + Crossref 检索 + 用户 topic 过滤

Zotero 不是主搜索源，而是 rerank 语料。月度候选主要由每个用户的 `users.yaml` profile 决定。

## 用户配置示例

README 不公开实际用户身份。以下统一使用 `user_a`（用户 A）说明配置方式。

用户 A 可以订阅 FHE、格密码和 PQC 等方向。公开的研究方向、关键词和检索规则放在 `users.yaml`：

```yaml
users:
  user_a:
    zotero:
      user_id: ${oc.env:USER_A_ZOTERO_ID,null}
      api_key: ${oc.env:USER_A_ZOTERO_KEY,null}
      include_path: null
      ignore_path: null
    email:
      receiver: ${oc.env:USER_A_RECEIVER,null}
    profile:
      min_topic_score: 1
      keywords:
        - fully homomorphic encryption
        - lattice cryptography
        - post-quantum cryptography
```

真实 Zotero 凭据、邮箱地址和 API Key 只存放在本地忽略文件或 GitHub Actions Secrets 中，不提交到 Git。

## 安装

```bash
cd /path/to/zotero-arxiv-daily
uv sync
```

如未安装 `uv`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 本地配置

`.env` 保存通用发件邮箱、DeepSeek 和用户 A 的本地凭据：

```bash
USER_A_ZOTERO_ID=...
USER_A_ZOTERO_KEY=...
USER_A_RECEIVER=...
OPENAI_API_KEY=...
OPENAI_API_BASE=https://api.deepseek.com
SENDER=...
SENDER_PASSWORD=...
OPENALEX_MAILTO=...
DEBUG=false
```

`users.local.yaml` 保存不应提交的多用户私密字段：

```yaml
users:
  user_a:
    zotero:
      user_id: "..."
      api_key: "..."
    email:
      receiver: "..."
```

可提交的用户方向配置在 `users.yaml`。

## 常用命令

每日推送所有用户：

```bash
uv run src/zotero_arxiv_daily/main.py --all-users --mode daily --send-email true executor.max_paper_num=10
```

月度推送所有用户：

```bash
uv run src/zotero_arxiv_daily/main.py --all-users --mode monthly executor.max_paper_num=15
```

指定历史区间测试：

```bash
uv run src/zotero_arxiv_daily/main.py \
  --all-users \
  --mode test-range \
  --start-date 2025-11-01 \
  --end-date 2026-06-24 \
  --send-email true \
  executor.max_paper_num=15
```

单用户运行：

```bash
uv run src/zotero_arxiv_daily/main.py --user user_a --mode daily --send-email true
uv run src/zotero_arxiv_daily/main.py --user user_a --mode test-range --start-date 2026-04-01 --end-date 2026-04-30 --send-email true
```

## 月推搜索策略

月推使用 CCF 2026 目录中的 A/B venue，数据文件为：

```text
data/ccf2026_entries.json
```

检索流程：

1. 按用户 profile 选择 CCF 领域和等级。
2. Crossref 按 venue + 日期拉取候选。
3. 本地严格校验 `container-title`，避免 Crossref 返回非目标 venue。
4. 使用用户 topic 组过滤候选。
5. 对 `profile.search_queries` 额外做 Crossref bibliographic search，提高召回。
6. 补召回结果仍必须通过 CCF venue 校验和用户 topic 校验。
7. 用用户 Zotero 文库进行 rerank。
8. DeepSeek 生成中文 TLDR。
9. SMTP 发邮件。

短词如 `SIS`、`LWE`、`FHE`、`PQC` 使用词边界匹配，避免误命中普通英文单词片段。

例如：

- `GRASP: Accelerating Hash-Based PQC Performance on GPU Parallel Architecture`
- venue: `IEEE Transactions on Computers`
- CCF: A
- 对订阅 PQC/GPU 方向的用户 A 属于强相关论文

## 日志与状态

日志：

```text
logs/{user}/{mode}/YYYY-MM-DD.log
```

去重状态：

```text
state/{user}/seen.json
```

`test-range` 默认忽略 seen，方便重复回放历史调研。

如果邮箱未显示邮件，先搜索：

```text
Paper Digest
from:<sender-address>
```

并检查 Gmail 的“垃圾邮件 / 所有邮件 / 过滤器归档”。日志中的 `SMTP accepted message for ...` 表示 SMTP 服务器接受投递，但不保证 Gmail 一定放入 Inbox。

## GitHub Actions 配置

GitHub Actions 是推荐的唯一自动推送端。当前工作流位于 `.github/workflows/main.yml`，支持定时执行和网页手动补发。

### 1. Fork 并启用 Actions

1. Fork 本仓库。
2. 打开 fork 仓库的 `Actions` 页面并启用工作流。
3. 确认默认分支为 `main`，因为 GitHub 只从默认分支读取定时工作流。

### 2. 配置 Secrets

打开仓库 `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`，为工作流添加以下 Secrets。值不会写入公开提交，也不会在 Actions 日志中明文显示。

| Secret | 用途 |
| --- | --- |
| `USER_A_ZOTERO_ID` | 用户 A 的 Zotero user ID |
| `USER_A_ZOTERO_KEY` | 用户 A 的 Zotero API Key，只需只读权限 |
| `USER_A_RECEIVER` | 用户 A 的收件邮箱 |
| `SENDER` | SMTP 发件邮箱 |
| `SENDER_PASSWORD` | SMTP 授权码，不是普通登录密码 |
| `OPENAI_API_KEY` | DeepSeek API Key |
| `OPENAI_API_BASE` | 固定为 `https://api.deepseek.com` |

Secret 名称必须与 `users.yaml` 中的 `${oc.env:...}` 以及工作流 `env` 映射一致。例如：

```yaml
env:
  USER_A_ZOTERO_ID: ${{ secrets.USER_A_ZOTERO_ID }}
  USER_A_ZOTERO_KEY: ${{ secrets.USER_A_ZOTERO_KEY }}
  USER_A_RECEIVER: ${{ secrets.USER_A_RECEIVER }}
  SENDER: ${{ secrets.SENDER }}
  SENDER_PASSWORD: ${{ secrets.SENDER_PASSWORD }}
  OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  OPENAI_API_BASE: ${{ secrets.OPENAI_API_BASE }}
```

不要把 Secret 值写入 `users.yaml`、工作流 YAML、README、Repository Variables 或命令日志。

### 3. 配置每天 09:07 推送

GitHub cron 使用 UTC。北京时间 09:07 对应 UTC 01:07：

```yaml
on:
  schedule:
    - cron: "7 1 * * *"
  workflow_dispatch:
```

避开整点可以降低 GitHub Actions 高负载时的延迟或丢弃概率。定时运行会处理所有已配置用户；即使当天没有匹配论文，也会发送一封结果邮件。

### 4. 手动补发

打开 `Actions` -> `Send paper digest` -> `Run workflow`，可选择：

- `mode=daily`：补发当天 arXiv + IACR 日报。
- `mode=iacr-range`：按 `start_date` 和 `end_date` 补发 IACR 区间报告。
- `user=all` 或用户 A：选择全部用户或单个用户。
- `send_empty=true`：即使没有匹配论文也发送结果。

运行完成后检查日志中的 `SMTP accepted message for ***` 和 `Email sent successfully`。前者表示 SMTP 服务已接受邮件，但不保证收件服务一定放入收件箱。

### 5. 避免重复推送

同一时间只保留一个自动调度端。启用 GitHub Actions 后，应停用 Windows Task Scheduler、macOS `cron` 或其他服务器上的相同任务；手动补发仍可从 Actions 页面执行。

## 本地后台运行

macOS / Linux：

```bash
scripts/install_cron_examples.sh all
```

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows_tasks.ps1 -UserId all
```

默认不做常驻 daemon。本地调度仅用于不采用 GitHub Actions 的部署方式。

## 测试

```bash
uv run pytest -q
```

## 仓库来源

当前仓库：

```text
origin   https://github.com/Stu-zcy/zotero-arxiv-daily.git
upstream https://github.com/TideDra/zotero-arxiv-daily.git
```

更多细节见：

- [docs/RUNBOOK.md](docs/RUNBOOK.md)
