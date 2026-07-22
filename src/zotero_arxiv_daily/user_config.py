from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from omegaconf import DictConfig, OmegaConf, open_dict


@dataclass
class RuntimeArgs:
    user: str | None = None
    receiver_user: str | None = None
    all_users: bool = False
    mode: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    send_email: bool | None = None
    ignore_seen: bool | None = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_yaml_if_exists(path: Path) -> DictConfig:
    if not path.exists():
        return OmegaConf.create({})
    return OmegaConf.load(path)


def load_users_config(root: Path | None = None) -> DictConfig:
    root = root or _repo_root()
    base = _load_yaml_if_exists(root / "users.yaml")
    local = _load_yaml_if_exists(root / "users.local.yaml")
    return OmegaConf.merge(base, local)


def configured_user_ids(root: Path | None = None) -> list[str]:
    users = load_users_config(root).get("users", {})
    return list(users.keys())


def _previous_month_window(today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    first_this_month = today.replace(day=1)
    last_previous_month = first_this_month - timedelta(days=1)
    first_previous_month = last_previous_month.replace(day=1)
    return first_previous_month.isoformat(), last_previous_month.isoformat()


def _as_plain_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if OmegaConf.is_config(value):
        return list(OmegaConf.to_container(value, resolve=True))
    return [value]


def _require(value: Any, message: str) -> Any:
    if value in (None, "", "???"):
        raise ValueError(message)
    return value


def _apply_profile(config: DictConfig, user_cfg: DictConfig) -> None:
    profile = user_cfg.get("profile") or {}
    keywords = _as_plain_list(profile.get("keywords"))
    search_queries = _as_plain_list(profile.get("search_queries"))
    topics = _as_plain_list(profile.get("topics"))
    min_topic_score = profile.get("min_topic_score")
    for source in ("arxiv", "ccf_crossref", "ccf_openalex", "iacr_eprint"):
        if not hasattr(config.source, source):
            continue
        if keywords and hasattr(getattr(config.source, source), "keywords"):
            getattr(config.source, source).keywords = keywords
        if search_queries and hasattr(getattr(config.source, source), "search_queries"):
            getattr(config.source, source).search_queries = search_queries
        if topics and hasattr(getattr(config.source, source), "topics"):
            getattr(config.source, source).topics = topics
        if min_topic_score is not None and hasattr(getattr(config.source, source), "min_topic_score"):
            getattr(config.source, source).min_topic_score = min_topic_score


def apply_runtime_config(config: DictConfig, args: RuntimeArgs, root: Path | None = None) -> DictConfig:
    if not args.user and not args.mode:
        return config

    users_cfg = load_users_config(root)
    user_cfg = None
    if args.user:
        user_cfg = users_cfg.get("users", {}).get(args.user)
        if user_cfg is None:
            raise ValueError(f"User '{args.user}' is not configured in users.yaml/users.local.yaml")

    mode = args.mode or "daily"
    if mode not in {"daily", "monthly", "test-range", "iacr-range"}:
        raise ValueError("mode must be one of: daily, monthly, test-range, iacr-range")

    with open_dict(config):
        config.runtime = {
            "user": args.user,
            "mode": mode,
            "start_date": args.start_date,
            "end_date": args.end_date,
        }
        if not hasattr(config, "state"):
            config.state = {}
        config.state.user = args.user or "default"
        config.state.mode = mode
        config.state.path = f"state/{config.state.user}/seen.json"
        config.state.enabled = mode in {"daily", "monthly"}
        config.state.ignore_seen = bool(args.ignore_seen) or mode in {"test-range", "iacr-range"}

        if user_cfg is not None:
            zotero_cfg = user_cfg.get("zotero") or {}
            config.zotero.user_id = _require(
                zotero_cfg.get("user_id"),
                f"Zotero user_id is missing for user '{args.user}'",
            )
            config.zotero.api_key = _require(
                zotero_cfg.get("api_key"),
                f"Zotero api_key is missing for user '{args.user}'",
            )
            if "include_path" in zotero_cfg:
                config.zotero.include_path = zotero_cfg.get("include_path")
            if "ignore_path" in zotero_cfg:
                config.zotero.ignore_path = zotero_cfg.get("ignore_path")
            email_cfg = user_cfg.get("email") or {}
            if email_cfg.get("receiver"):
                config.email.receiver = email_cfg.get("receiver")
            _apply_profile(config, user_cfg)

            monthly = user_cfg.get("monthly") or {}
            profile = user_cfg.get("profile") or {}
            monthly_fields = profile.get("monthly_fields") or monthly.get("ccf_fields")
            if hasattr(config.source, "ccf_crossref"):
                if monthly_fields:
                    config.source.ccf_crossref.fields = monthly_fields
                if monthly.get("ccf_ranks"):
                    config.source.ccf_crossref.ranks = monthly.get("ccf_ranks")
            if hasattr(config.source, "ccf_openalex"):
                if monthly_fields:
                    config.source.ccf_openalex.fields = monthly_fields
                if monthly.get("ccf_ranks"):
                    config.source.ccf_openalex.ranks = monthly.get("ccf_ranks")

        if args.receiver_user:
            receiver_cfg = users_cfg.get("users", {}).get(args.receiver_user)
            if receiver_cfg is None:
                raise ValueError(
                    f"Receiver user '{args.receiver_user}' is not configured in users.yaml/users.local.yaml"
                )
            receiver = (receiver_cfg.get("email") or {}).get("receiver")
            config.email.receiver = _require(
                receiver,
                f"Email receiver is missing for user '{args.receiver_user}'",
            )

        if mode == "daily":
            config.executor.source = ["arxiv", "iacr_eprint"]
            config.source.arxiv.category = ["cs.CR"]
            config.source.arxiv.include_cross_list = False
            config.source.arxiv.extract_full_text = False
            config.source.arxiv.keyword_required = False
            config.source.iacr_eprint.lookback_days = 1
            config.source.iacr_eprint.categories = []
            if args.send_email is not None:
                config.executor.send_empty = bool(args.send_email)
        elif mode in {"monthly", "test-range"}:
            config.executor.source = ["ccf_crossref"]
            start_date, end_date = args.start_date, args.end_date
            if mode == "monthly" and (not start_date or not end_date):
                start_date, end_date = _previous_month_window()
            if not start_date or not end_date:
                raise ValueError("test-range requires --start-date and --end-date")
            config.source.ccf_crossref.start_date = start_date
            config.source.ccf_crossref.end_date = end_date
            if hasattr(config.source, "ccf_openalex"):
                config.source.ccf_openalex.start_date = start_date
                config.source.ccf_openalex.end_date = end_date
            if args.send_email is not None:
                config.executor.send_empty = bool(args.send_email)
        elif mode == "iacr-range":
            if not args.start_date or not args.end_date:
                raise ValueError("iacr-range requires --start-date and --end-date")
            config.executor.source = ["iacr_eprint"]
            config.source.iacr_eprint.start_date = args.start_date
            config.source.iacr_eprint.end_date = args.end_date
            config.source.iacr_eprint.categories = []
            if args.send_email is not None:
                config.executor.send_empty = bool(args.send_email)

        config.executor.debug = False

    logger.info(f"Runtime mode={mode}, user={args.user or 'default'}")
    return config
