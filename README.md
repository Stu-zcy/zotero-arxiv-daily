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

## 当前用户

当前配置包含两个用户：

- `chenyang`
  - 方向：FHE、PQC、hash-based PQC、GPU/FPGA/accelerator、格、公钥、承诺、属性加密、TEE
  - Zotero 凭据从 `.env` 读取
- `liruoyi`
  - 方向：格密码理论（格困难问题、LWE/SIS/NTRU、格归约与陷门）及全部同态加密方向
  - 召回策略：命中 1 个强领域词即可，覆盖 PHE/SHE/leveled/FHE、主流方案与 bootstrapping/key switching/packing 等技术
  - Zotero 凭据从 `users.local.yaml` 读取

真实密钥只保存在本地 `.env` 和 `users.local.yaml`，这两个文件不会提交到 Git。

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

`.env` 保存通用发件邮箱、DeepSeek、旧用户 Zotero：

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

`users.local.yaml` 保存不应提交的多用户私密字段：

```yaml
users:
  liruoyi:
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
uv run src/zotero_arxiv_daily/main.py --user chenyang --mode daily --send-email true
uv run src/zotero_arxiv_daily/main.py --user liruoyi --mode test-range --start-date 2026-04-01 --end-date 2026-04-30 --send-email true
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
- 对 `chenyang` 属于强相关 PQC/GPU 方向
- 对 `liruoyi` 不属于 FHE/格基础/FHE circuit 方向

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
from:chen.zcy@foxmail.com
```

并检查 Gmail 的“垃圾邮件 / 所有邮件 / 过滤器归档”。日志中的 `SMTP accepted message for ...` 表示 SMTP 服务器接受投递，但不保证 Gmail 一定放入 Inbox。

## 后台运行

macOS / Linux：

```bash
scripts/install_cron_examples.sh all
```

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_windows_tasks.ps1 -UserId all
```

默认不做常驻 daemon，使用系统调度器，便于迁移和失败重跑。

## 测试

```bash
uv run pytest -q
```

## GitHub

当前仓库：

```text
origin   https://github.com/Stu-zcy/zotero-arxiv-daily.git
upstream https://github.com/TideDra/zotero-arxiv-daily.git
```

更多细节见：

- [docs/RUNBOOK.md](docs/RUNBOOK.md)
