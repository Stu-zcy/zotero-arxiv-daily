import os
import sys
import logging
import argparse
import copy
from datetime import date
from pathlib import Path
from omegaconf import DictConfig
import hydra
from loguru import logger
import dotenv
from zotero_arxiv_daily.executor import Executor
from zotero_arxiv_daily.user_config import RuntimeArgs, apply_runtime_config, configured_user_ids
os.environ["TOKENIZERS_PARALLELISM"] = "false"
dotenv.load_dotenv()


def _parse_bool(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def _extract_runtime_args(argv: list[str]) -> RuntimeArgs:
    args = list(argv)
    if len(args) > 1 and args[1] == "run":
        args.pop(1)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--user")
    parser.add_argument("--receiver-user")
    parser.add_argument("--all-users", action="store_true")
    parser.add_argument("--mode", choices=["daily", "monthly", "test-range", "iacr-range"])
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--send-email", type=_parse_bool)
    parser.add_argument("--ignore-seen", type=_parse_bool)
    parsed, remaining = parser.parse_known_args(args[1:])
    sys.argv = [args[0], *remaining]
    return RuntimeArgs(
        user=parsed.user,
        receiver_user=parsed.receiver_user,
        all_users=parsed.all_users,
        mode=parsed.mode,
        start_date=parsed.start_date,
        end_date=parsed.end_date,
        send_email=parsed.send_email,
        ignore_seen=parsed.ignore_seen,
    )


RUNTIME_ARGS = _extract_runtime_args(sys.argv)


@hydra.main(version_base=None, config_path="../../config", config_name="default")
def main(config:DictConfig):
    if RUNTIME_ARGS.all_users:
        failures = []
        for user_id in configured_user_ids():
            user_args = RuntimeArgs(
                user=user_id,
                receiver_user=RUNTIME_ARGS.receiver_user,
                all_users=False,
                mode=RUNTIME_ARGS.mode,
                start_date=RUNTIME_ARGS.start_date,
                end_date=RUNTIME_ARGS.end_date,
                send_email=RUNTIME_ARGS.send_email,
                ignore_seen=RUNTIME_ARGS.ignore_seen,
            )
            try:
                user_config = apply_runtime_config(copy.deepcopy(config), user_args)
                _run_once(user_config)
            except Exception as exc:
                failures.append((user_id, exc))
                logger.exception(f"Failed to run for user {user_id}: {exc}")
        if failures:
            raise RuntimeError(f"Failed users: {', '.join(user for user, _ in failures)}")
        return
    config = apply_runtime_config(config, RUNTIME_ARGS)
    _run_once(config)


def _run_once(config: DictConfig):
    # Configure loguru log level based on config
    log_level = "DEBUG" if config.executor.debug else "INFO"
    logger.remove()  # Remove default handler
    logger.add(
        sys.stdout,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    runtime = config.get("runtime", {})
    log_user = runtime.get("user") or "default"
    log_mode = runtime.get("mode") or "legacy"
    log_path = Path.cwd() / "logs" / str(log_user) / str(log_mode) / f"{date.today().isoformat()}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_path,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )
    
    for logger_name in logging.root.manager.loggerDict:
        if "zotero_arxiv_daily" in logger_name:
            continue
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    if config.executor.debug:
        logger.info("Debug mode is enabled")
    
    executor = Executor(config)
    executor.run()

if __name__ == '__main__':
    main()
