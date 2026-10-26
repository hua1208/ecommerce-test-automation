"""
请求客户端：基于 requests.Session 的统一入口。

设计要点（很重要，逐条都值得记住）：
1. 用 Session 而不是每次 requests.get：复用 TCP 连接、自动管理 cookie、统一请求头
2. base_url 从配置读，业务代码里永远不出现 IP 和端口
3. token 统一注入 Authorization 头，用例不用关心怎么拼
4. 4xx/5xx 不抛异常 —— HTTP 状态码本身就是被测对象，应该交给断言层判断
5. 只把"网络层"故障（连不上、超时、DNS 失败）翻译成 ApiError
6. 不在这里写任何 assert，也不认识 login/order 等业务
"""
from __future__ import annotations

from typing import Any

import requests

from common.config_loader import Config, load_config
from common.logger import get_logger


class ApiError(RuntimeError):
    """网络层错误：连接失败、超时、DNS 解析失败等。与业务错误码无关。"""


class ApiClient:
    """接口请求客户端。一个实例持有一个 Session，对应"一个用户的一次会话"。"""

    def __init__(self, config: Config | None = None, token: str | None = None) -> None:
        self.config = config or load_config()          # 允许外部注入配置，便于测试替身

        # require：地址缺了整个项目没法跑，必须立刻报错
        # rstrip("/")：防止配置里多写了结尾斜杠，拼出 //login
        self.base_url = str(self.config.require("base_url")).rstrip("/")

        self.timeout = float(self.config.get("timeout", 15))
        self.verify = bool(self.config.get("verify_ssl", False))

        self.logger = get_logger("client")
        self._token: str | None = None

        # Session 的作用：
        # 1) 复用 TCP 连接（同一 host 的多次请求不必重新握手）
        # 2) 自动保存并回传 cookie
        # 3) 请求头可以在 session 级别统一设置
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",   # 本项目的接口都收 JSON
            "Accept": "application/json",
        })

        if token:
            self.set_token(token)

    # ---------------- token 管理 ----------------
    def set_token(self, token: str) -> None:
        """登录成功后调用，之后所有请求自动带上 Authorization。"""
        self._token = token
        self.session.headers["Authorization"] = f"Bearer {token}"

    def clear_token(self) -> None:
        """退出登录 / 验证未授权场景时调用。"""
        self._token = None
        self.session.headers.pop("Authorization", None)   # 不存在也不报错

    @property
    def token(self) -> str | None:
        """只读属性，外部拿得到但改不了（改只能走 set_token，保证请求头同步）。"""
        return self._token

    # ---------------- 核心请求 ----------------
    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """
        发一个请求，返回原始 Response。

        注意返回的是 Response 而不是 dict：
        状态码、响应头、耗时、原始文本都还有用，提前解析成 dict 会丢信息。
        解析交给第 4 步的断言层。
        """
        method = method.upper()
        # lstrip("/") 保证无论传 "/login" 还是 "login" 都拼成 base_url + "/login"
        url = f"{self.base_url}/{path.lstrip('/')}"

        # 用 setdefault：调用方显式传了就尊重调用方，没传才用默认值
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", self.verify)

        self.logger.info("→ %s %s | json=%s | params=%s",
                         method, url, kwargs.get("json"), kwargs.get("params"))

        try:
            response = self.session.request(method, url, **kwargs)
        except requests.exceptions.Timeout as exc:
            # 超时单独捕获，报错信息里带上超时设置，方便判断是不是配小了
            self.logger.error("✗ 超时 %s %s (%ss)", method, url, self.timeout)
            raise ApiError(f"请求超时: {method} {url}，超时设置 {self.timeout}s") from exc
        except requests.exceptions.RequestException as exc:
            # 连接失败、DNS 失败、SSL 错误等统一归为网络异常
            self.logger.error("✗ 网络异常 %s %s: %s", method, url, exc)
            raise ApiError(f"网络异常: {method} {url} -> {exc}") from exc

        # 成功拿到响应（无论 2xx 还是 4xx），完整记录状态码和耗时
        cost_ms = response.elapsed.total_seconds() * 1000
        self.logger.info("← %s %s | %.0fms | %s",
                         response.status_code, url, cost_ms, self._short(response.text))
        return response

    @staticmethod
    def _short(text: str, limit: int = 500) -> str:
        """日志里只截前 500 字符，避免大响应把日志刷爆。"""
        one_line = " ".join(text.split())        # 把换行压成空格，日志才一行一条
        return one_line if len(one_line) <= limit else one_line[:limit] + "...(truncated)"

    # ---------------- 便捷方法 ----------------
    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("DELETE", path, **kwargs)

    # ---------------- 资源管理 ----------------
    def close(self) -> None:
        """关闭 Session，释放连接池。"""
        self.session.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        # 支持 with ApiClient() as c: ...，退出时自动关连接
        self.close()