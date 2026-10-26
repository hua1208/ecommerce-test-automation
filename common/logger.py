"""
日志组件：统一格式，同时输出到控制台和滚动文件。

设计要点：
1. 配置项来自 config/dev.yaml 的 logging 段（级别、路径、大小、备份数）
2. 用 RotatingFileHandler，日志文件不会无限增长
3. 同名 logger 只初始化一次，避免重复添加 handler 导致一行日志打印多遍
4. propagate=False，防止日志同时冒泡到 root 再打印一遍
"""
from __future__ import annotations

import logging                          # 标准库日志模块
from logging.handlers import RotatingFileHandler   # 按大小切分文件的 handler

from common.config_loader import PROJECT_ROOT, load_config   # 复用配置层

LOGGER_NAME = "api_test"                # 所有日志的根名字，形如 api_test.client
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _create_logger() -> logging.Logger:
    """创建（或复用）根 logger，并把 console / file 两个 handler 挂上去。"""
    config = load_config()              # 拿配置（有缓存，不会重复读盘）

    # 按名字获取 logger。Python 保证同名 logger 全局只存在一个实例。
    logger = logging.getLogger(LOGGER_NAME)

    # 级别从配置读，字符串转成 logging 常量；写错了就退回 INFO，不让日志把用例搞挂
    level_name = str(config.get("logging.level", "INFO")).upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    # 关键：不向 root logger 冒泡。
    # 否则 root 上若有 handler（pytest 会自动装），一条日志会被打印两次。
    logger.propagate = False

    # 幂等保护：已经加过 handler 就直接返回。
    # 否则模块被多次 import 或 get_logger 被多次调用时，handler 会越堆越多，
    # 表现为"同一行日志打印了 5 遍"。
    if logger.handlers:
        return logger

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # ---------- 控制台 ----------
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    # ---------- 文件（滚动） ----------
    # 配置里写的是相对路径，用 PROJECT_ROOT 拼成绝对路径，
    # 这样不管从哪个目录启动，日志都落在项目的 logs/ 下。
    log_file = PROJECT_ROOT / str(config.get("logging.file", "logs/run.log"))
    log_file.parent.mkdir(parents=True, exist_ok=True)   # 目录不在就创建，parents 支持多级

    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=int(config.get("logging.max_bytes", 5 * 1024 * 1024)),  # 超过就切分
        backupCount=int(config.get("logging.backup_count", 3)),           # 保留几个历史文件
        encoding="utf-8",                                                 # 中文日志必须指定
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """
    获取子 logger。

    get_logger("client") 拿到的名字是 "api_test.client"，
    它继承父 logger 的级别和 handler，日志里能一眼看出是哪个模块打的。
    """
    base = _create_logger()
    if not name:
        return base
    return base.getChild(name)          # getChild 会自动拼成 "api_test.client"