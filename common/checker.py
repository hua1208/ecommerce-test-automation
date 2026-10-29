"""
断言层：把"响应是否符合预期"的判断收敛到唯一的地方。

五类断言（对应 docs/dev-notes.md 第 4 步）：
1. status()  HTTP 状态码
2. code()    业务码，并校验 HTTP 码与业务码的对应关系
3. fields()  字段存在性与类型（专抓 null 污染、类型漂移）
4. amount()  金额一致性（单价×数量=小计，小计之和=总额）
5. state()   订单状态机（合法跃迁 + 状态文案）

设计约束：
- 只依赖标准库，不 import pytest / allure —— 保证任何场景都能调用
- 失败一律抛 AssertionError，消息是"人能看懂的多行文本"
- 不发请求、不打日志、不吞异常：它只做判断
- 每个方法返回解析后的 body，方便用例继续取字段
"""
from __future__ import annotations

import json
import math                          # 金额比较需要容差
from collections.abc import Iterable
from urllib.parse import urlparse    # 从完整 URL 里取出 path
from typing import Any, NoReturn

# 响应体在失败信息里最多展示多少字符，太长会淹没重点
_EXCERPT = 300

# 订单状态机：key 是"当前状态"，value 是"允许迁移到的状态"
TRANSITIONS: dict[str, set[str]] = {
    "pending": {"paid", "cancelled"},
    "paid": set(),                   # 已支付是终态，不能再取消（服务返回 409）
    "cancelled": set(),              # 已取消是终态
}

# 状态码 → 中文文案，用于校验 statusText 字段
STATUS_TEXT: dict[str, str] = {
    "pending": "待支付",
    "paid": "已支付",
    "cancelled": "已取消",
}

# 哨兵对象：用来区分"字段不存在"和"字段值为 None"
_MISSING = object()


class ResponseChecker:
    """响应断言集合。通过模块级单例 check 使用，不需要自己实例化。"""

    def __init__(self, excerpt: int = _EXCERPT) -> None:
        self.excerpt = excerpt

    # ==================== 1. 状态码 ====================
    def status(self, response, expected: int | Iterable[int] = 200) -> dict:
        allowed = {expected} if isinstance(expected, int) else set(expected)
        if response.status_code not in allowed:
            self._fail(response, "HTTP 状态码不符",
                       f"其中之一 {sorted(allowed)}", response.status_code, {},
                       hint="4xx 先看 body.message；401 查 token，403 查角色，409 看业务规则")
        try:                          # 状态码对了就完成使命，body 能不能解析不归它管
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    # ==================== 2. 业务码 ====================
    def code(self, response, expected: int = 0,
             message_contains: str | None = None) -> dict:
        """
        断言 body.code，并校验它与 HTTP 状态码的对应关系。

        ⚠ 本项目的契约不是"两者恒等"，而是：
              code == 0   → HTTP 200
              code != 0   → HTTP == code   （401/400/403/404/409）
        这条规则只写在这一个方法里，将来服务改了也只改这里。
        """
        body = self._body(response)

        if "code" not in body:
            self._fail(response, "响应体缺少 code 字段",
                       "包含 code 的 JSON 对象", sorted(body.keys())[:10], body)

        actual = body["code"]
        if actual != expected:
            self._fail(response, "业务码不符", expected, actual, body,
                       hint="0=成功；非 0 时看 message 判断是参数、权限还是业务规则问题")

        expected_http = 200 if actual == 0 else actual
        if response.status_code != expected_http:
            self._fail(
                response, "HTTP 状态码与业务码不匹配",
                f"code={actual} 时 HTTP 应为 {expected_http}",
                f"HTTP {response.status_code} / code {actual}",
                body,
                hint="两者不一致说明服务端某个分支漏设了状态码，是最容易漏测的契约不一致",
            )

        if message_contains is not None:
            message = str(body.get("message", ""))
            if message_contains not in message:
                self._fail(response, "message 文案不符",
                           f"包含「{message_contains}」", message or "(空)", body)
        return body

    # ==================== 3. 字段类型 ====================
    def fields(self, response, schema: dict[str, Any], *,
               allow_none: Iterable[str] = ()) -> dict:
        """
        断言字段存在且类型正确。一次收集全部问题再报，而不是遇到第一个就停。

        schema 写法：
            {"products": list,
             "products.0.id": int,          # 支持列表下标
             "products.0.price": (int, float)}   # 支持多类型

        allow_none：允许为 null 的字段路径白名单（比如某些可选字段）
        """
        body = self._body(response)
        problems: list[str] = []

        for path, expected_type in schema.items():
            actual = self._dig(body, path, default=_MISSING)

            if actual is _MISSING:
                problems.append(f"{path}: 字段缺失")
            elif actual is None:
                if path not in allow_none:
                    problems.append(f"{path}: 值为 null（期望 {self._type_name(expected_type)}）")
            elif not self._type_ok(actual, expected_type):
                problems.append(
                    f"{path}: 期望 {self._type_name(expected_type)}，"
                    f"实际 {type(actual).__name__} {self._excerpt(repr(actual))}"
                )

        if problems:
            self._fail_many(response, "字段校验失败", "全部字段符合 schema", problems, body)
        return body

    # ==================== 4. 金额一致性 ====================
    def amount(self, response) -> dict:
        """
        断言金额自洽：subtotal == price × quantity，totalAmount == Σ subtotal。

        ⚠ 只用于 GET /cart —— POST/DELETE /cart 的响应不含 totalAmount。
        """
        body = self._body(response)
        items = body.get("items")
        if not isinstance(items, list):
            self._fail(response, "购物车缺少 items 列表", "list", type(items).__name__, body)

        problems: list[str] = []
        computed = 0.0
        for index, item in enumerate(items):
            if not isinstance(item, dict):      # 脏数据：items:[null] / ["x"]
                problems.append(f"items[{index}] 不是对象: {self._excerpt(repr(item))}")
                continue
            price, quantity, subtotal = item.get("price"), item.get("quantity"), item.get("subtotal")
            if not (self._is_number(price) and self._is_number(quantity)):
                problems.append(f"items[{index}] price/quantity 非法: {price!r} / {quantity!r}")
                continue
            expected_subtotal = price * quantity
            if not self._close(subtotal, expected_subtotal):
                problems.append(f"items[{index}] subtotal: 期望 {expected_subtotal:.2f}，实际 {subtotal!r}")
            computed += expected_subtotal

        if "totalAmount" not in body:
            problems.append("totalAmount: 字段缺失（注意：只有 GET /cart 会返回该字段）")
        elif not self._is_number(body["totalAmount"]):
            problems.append(f"totalAmount: 不是数字，实际 {body['totalAmount']!r}")
        elif not self._close(body["totalAmount"], computed):
            problems.append(f"totalAmount: 期望 {computed:.2f}（小计之和），实际 {body['totalAmount']!r}")

        if problems:
            self._fail_many(response, "金额不一致", "各层金额自洽", problems, body,
                            hint="先排除 D2/D3 造成的脏数据：quantity 为 null 或超过库存")
        return body

    # ==================== 5. 订单状态机 ====================
    def state(self, response, expected: str, previous: str | None = None) -> dict:
        """
        断言订单状态。previous 给了就同时校验"跃迁合法"。

        check.state(resp, "paid", previous="pending")
        """
        body = self._body(response)
        actual = self._order_status(body)

        if actual is None:
            self._fail(response, "响应体里找不到订单状态",
                       "order.status 或 status", sorted(body.keys())[:10], body)

        if actual != expected:
            self._fail(response, "订单状态不符", expected, actual, body,
                       hint=f"当前状态 {actual} 允许迁往 {sorted(TRANSITIONS.get(actual, set())) or '无（终态）'}")

        if previous is not None:
            allowed = TRANSITIONS.get(previous, set())
            if actual not in allowed:
                self._fail(response, "订单状态跃迁非法",
                           f"{previous} → {sorted(allowed) or '无（终态）'}",
                           f"{previous} → {actual}", body)

        expected_text = STATUS_TEXT.get(actual)
        text = self._order_field(body, "statusText")
        if expected_text and text != expected_text:
            self._fail(response, "状态文案不符", expected_text, text, body)
        return body

    # ==================== 内部工具 ====================
    def _body(self, response) -> dict:
        """安全解析 JSON，失败时给出比 JSONDecodeError 更有用的信息。"""
        try:
            data = response.json()
        except ValueError:  # 不是 JSON（网关 HTML 错误页等）
            self._fail(response, "响应体不是合法 JSON",
                    "JSON 对象",
                    self._excerpt(getattr(response, "text", "") or ""), {},
                    hint="502/504 常见于服务不可用或网关超时，先确认服务是否存活")
        if not isinstance(data, dict):
            self._fail(response, "响应体不是 JSON 对象",
                       "dict", type(data).__name__, {})
        return data

    # ==================== 失败信息拼装 ====================
    def _fail(self, response, title: str, expected: Any, actual: Any,
              body: dict, hint: str = "") -> NoReturn:
        """单条问题：期望 vs 实际。"""
        self._raise(response, title, [f"期望: {expected}", f"实际: {actual}"], body, hint)

    def _fail_many(self, response, title: str, expected: Any,
                   problems: list[str], body: dict, hint: str = "") -> NoReturn:
        """多条问题：字段/金额这类需要一次列全的场景。"""
        lines = [f"期望: {expected}"] + [f"  - {p}" for p in problems]
        self._raise(response, title, lines, body, hint)

    def _raise(self, response, title: str, lines: list[str],
               body: dict, hint: str) -> NoReturn:
        """统一拼装失败信息并抛出。所有断言最终都走这里。"""
        parts = [f"【{title}】{self._request_line(response)}"]
        parts += [f"  {line}" for line in lines]
        if hint:
            parts.append(f"  提示: {hint}")
        # 真实 Response 有 .text；单测里的假 Response 可能没有，退回用 body 序列化
        raw = (getattr(response, "text", None) or "").strip()
        if not raw and body:
            raw = json.dumps(body, ensure_ascii=False)
        if raw:
            parts.append(f"  响应体: {self._excerpt(raw)}")
        raise AssertionError("\n".join(parts))

    @staticmethod
    def _request_line(response) -> str:
        """'POST /api/orders' 这样的一行，定位失败接口用。"""
        request = getattr(response, "request", None)
        if request is None:
            return "(未知请求)"
        path = urlparse(request.url).path      # 去掉域名，只留 path
        return f"{request.method} {path}"

    def _excerpt(self, text: str) -> str:
        """压成一行并截断，避免长响应淹没失败信息。"""
        one_line = " ".join(str(text).split())
        return one_line if len(one_line) <= self.excerpt else one_line[: self.excerpt] + "...(截断)"

    @staticmethod
    def _dig(data: Any, path: str, default: Any = _MISSING) -> Any:
        """按 'a.b.0.c' 取值，支持列表下标；取不到返回 default。"""
        current = data
        for segment in path.split("."):
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            elif isinstance(current, list) and segment.lstrip("-").isdigit():
                index = int(segment)
                if -len(current) <= index < len(current):
                    current = current[index]
                else:
                    return default
            else:
                return default
        return current

    @staticmethod
    def _is_number(value: Any) -> bool:
        """数字判定。必须排除 bool —— 因为 Python 里 True 是 int 的子类。"""
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @classmethod
    def _type_ok(cls, value: Any, expected: Any) -> bool:
        types = expected if isinstance(expected, tuple) else (expected,)
        return any(cls._is_number(value) if t in (int, float) else isinstance(value, t)
                   for t in types)

    @staticmethod
    def _type_name(expected: Any) -> str:
        if isinstance(expected, tuple):
            return " / ".join(t.__name__ for t in expected)
        return getattr(expected, "__name__", str(expected))

    @classmethod
    def _close(cls, left: Any, right: float) -> bool:
        """金额比较：绝对误差 0.01（一分钱），避免浮点表示误差。"""
        if not cls._is_number(left):
            return False
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=0.01)

    @staticmethod
    def _order_status(body: dict) -> str | None:
        order = body.get("order")
        if isinstance(order, dict) and "status" in order:
            return order["status"]
        return body.get("status")

    @staticmethod
    def _order_field(body: dict, key: str) -> Any:
        order = body.get("order")
        if isinstance(order, dict):
            return order.get(key)
        return body.get(key)


# 模块级单例：用例里直接 from common.checker import check
check = ResponseChecker()