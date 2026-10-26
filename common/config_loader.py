"""
配置加载器：把 config/<环境>.yaml 读成 Python 对象，供全项目使用。

职责：
1. 默认读 dev，可用环境变量 TEST_ENV 临时切换（如 TEST_ENV=test）
2. 用 yaml.safe_load 解析（禁止 yaml.load，后者能执行任意代码）
3. 支持 "accounts.tester.password" 这种点号路径取值
4. 必需项缺失时立刻抛错，不要等到断言阶段才变成 None
5. 进程内缓存，避免每条用例都读一次磁盘
"""
from __future__ import annotations           # 让类型注解支持 str | None 这种新写法

import os                                     # 读环境变量 TEST_ENV
from functools import lru_cache               # 缓存：同一个 env 只读一次文件
from pathlib import Path                      # 跨平台路径处理，比字符串拼接安全
from typing import Any                        # 表示任意类型

import yaml                                   # PyYAML

# __file__ 是当前文件路径 common/config_loader.py
# .resolve() 转绝对路径 → .parent 是 common/ → 再 .parent 就是项目根
# 为什么不用 os.getcwd()？因为 cwd 取决于"从哪执行"，PyCharm、命令行、CI 各不相同，
# 用文件自身位置才永远正确。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEFAULT_ENV = "dev"

# 用独一无二的对象表示"没取到值"。
# 不用 None 是因为配置里可能合法地写 null，None 无法区分"不存在"和"值为空"。
_MISSING = object()


class ConfigError(RuntimeError):
    """配置相关错误：文件不存在、格式非法、必需项缺失。"""


class Config:
    """配置对象。对外只暴露 get / require，内部字典不直接改，防止用例误改配置。"""

    def __init__(self, data: dict[str, Any], env: str, source: Path) -> None:
        self._data = data        # 解析后的完整字典
        self.env = env           # 环境名，例如 "dev"
        self.source = source     # 配置文件路径，报错时能指出是哪个文件

    def get(self, key: str, default: Any = None) -> Any:
        """
        按点号路径取值，取不到返回 default，全程不抛 KeyError。

        get("accounts.tester.password")
        等价于 data["accounts"]["tester"]["password"]
        """
        node: Any = self._data
        for part in key.split("."):            # "a.b.c" → ["a", "b", "c"]
            if not isinstance(node, dict) or part not in node:
                return default                 # 中途断了，安全返回默认值
            node = node[part]                  # 往下走一层
        return node

    def require(self, key: str) -> Any:
        """
        必需配置项：缺失立刻抛 ConfigError。

        和 get 的区别：get 缺了给 None，调用方可能带着 None 一路跑到断言才炸；
        require 让错误在最早期暴露，并明确指出是哪个文件缺了哪个键。
        """
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise ConfigError(f"缺少必需配置项: {key}（文件: {self.source}）")
        return value

    def __repr__(self) -> str:
        # 调试时 ,  print()   只显示环境和文件名，不打印值，避免密码进日志
        # print(config)
        return f"<Config env={self.env!r} source={self.source.name!r}>"


@lru_cache(maxsize=None)                       # 同一 env 只读一次磁盘
def load_config(env: str | None = None) -> Config:
    """
    加载配置。优先级：显式传参 > 环境变量 TEST_ENV > 默认 dev。

    conftest、用例、封装层都会调用它，缓存后整个测试进程只读一次文件；
    需要换环境时调用 load_config.cache_clear()。
    """
    env = (env or os.getenv("TEST_ENV") or DEFAULT_ENV).strip()
    path = CONFIG_DIR / f"{env}.yaml"

    if not path.is_file():
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        with path.open("r", encoding="utf-8") as fp:   # 显式 utf-8，防中文注释在 Windows 乱码
            data = yaml.safe_load(fp) or {}            # 空文件时返回 None，用 {} 兜底
    except yaml.YAMLError as exc:
        # 包一层自己的异常，报错时能直接看到是哪个文件出的问题
        raise ConfigError(f"YAML 解析失败: {path}\n{exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"配置文件根节点必须是字典: {path}")

    return Config(data, env, path)


