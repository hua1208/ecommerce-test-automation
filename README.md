# 电商平台自动化测试框架

基于 **Python + pytest + requests + Allure + YAML** 的接口自动化测试工程。
覆盖登录鉴权、商品、购物车、订单四条业务链路，从接口契约校验到订单状态机，
沉淀了分层封装、数据驱动、统一断言、报告可视化与缺陷跟踪的完整实践。

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![pytest](https://img.shields.io/badge/pytest-9.0.0-0A9EDC?logo=pytest&logoColor=white)
![requests](https://img.shields.io/badge/requests-2.32.5-2B5B84)
![Allure](https://img.shields.io/badge/Allure-2.36.0-FF6A00)
![License](https://img.shields.io/badge/License-MIT-3DA639)

## 一、项目亮点

- **四层单向依赖架构**：配置 / 公共封装 / 测试数据 / 测试用例，任何一层都不反向依赖上层
- **封装层零断言**：请求客户端只负责发请求与记录日志，判定全部收敛到断言层，职责单一
- **数据驱动**：用例数据与代码分离，新增场景只加 YAML 条目、不改代码
- **全局状态治理**：针对"服务内存态、数据全局共享"的特点，设计会话级重置 + 顺序执行策略，消除假失败
- **缺陷驱动回归**：把测试过程中发现的 6 个真实缺陷固化为回归用例，防止修复后回退
- **可读的报告**：Allure 报告内含请求/响应附件、业务步骤与环境信息，失败可当场定位

## 二、技术架构

```
┌──────────────────────────────────────────────┐
│  api/testcases/   测试用例（只描述业务规则）    │
├──────────────────────────────────────────────┤
│  data/api/*.yaml  测试数据  │  api/conftest.py 夹具 │
├──────────────────────────────────────────────┤
│  common/  配置加载 · 日志 · API 客户端 · 断言 · 报告 │
├──────────────────────────────────────────────┤
│  config/*.yaml    环境地址 · 账号 · 超时 · 日志级别  │
└──────────────────────────────────────────────┘
```

依赖方向严格单向：`用例 → 数据/夹具 → 公共封装 → 配置`。

## 三、被测对象

| 项目 | 地址 |
| --- | --- |
| 接口服务 | http://43.133.227.52/api |
| 接口文档 | [ecommerce-api-doc](http://www.ceshixiaoniu.com/projects/ecommerce-api-doc.html) |
| 业务前端 | [ecommerce-practice-app](http://www.ceshixiaoniu.com/ecommerce-practice-app.html) |

> 该环境为公开练习站，数据保存在内存中且**全局共享**：任何人操作都会改变
> 商品库存与购物车，服务重启即还原。这直接决定了本项目的两条设计约束——
> 执行前重置数据、禁止并行执行。

## 四、测试覆盖

| 模块 | 接口数 | 覆盖场景 | 状态 |
| --- | --- | --- | --- |
| 健康检查 | 1 | 服务可用性 | 🚧 |
| 登录鉴权 | 1 | 正常登录、密码错误、缺失 token、token 失效、角色鉴权 | 🚧 |
| 商品 | 1 | 列表返回、字段完整性、库存边界（含零库存） | 🚧 |
| 购物车 | 3 | 加购、删除、金额合计、数量边界、非法入参 | 🚧 |
| 订单 | 5 | 下单、支付、取消、状态机流转、库存扣减、越权访问 | 🚧 |
| 公共契约 | — | 状态码与 `body.code` 一致性、字段类型 | 🚧 |

> 状态说明：🚧 用例开发中，完成后替换为 ✅ 并补充用例数量。

## 五、缺陷发现

测试过程中定位并复现 6 个问题，均已记录为缺陷单并固化为回归用例：

| 编号 | 缺陷描述 | 严重程度 |
| --- | --- | --- |
| D1 | 加购数量传 `0` 仍返回成功，且实际加购 1 件 | 中 |
| D2 | 加购数量超过库存仍返回成功，直到下单才被拦截 | 高 |
| D3 | 加购数量传非数字字符串，接口返回成功但购物车金额字段为 `null` | 高 |
| D4 | 请求体为非法 JSON 时返回 `404 商品不存在`，语义错误 | 低 |
| D5 | 已支付订单重复支付返回成功（幂等性待产品定义） | 待定 |
| D6 | 删除不存在的商品返回成功（幂等，行为可接受） | 低 |

缺陷详情与复现步骤见 [`docs/defects/`](docs/defects/)。

## 六、测试报告

```bash
allure serve ./allure-results                          # 本地启动报告服务
allure generate ./allure-results -o ./allure-report --clean   # 生成静态 HTML
```

<!-- 待补：docs/images/allure-overview.png（报告总览）、docs/images/allure-detail.png（失败用例详情） -->

## 七、工程实践

| 实践 | 落地方式 |
| --- | --- |
| 配置与代码分离 | `config/dev.yaml` 管理地址/账号/超时，切换环境零改代码 |
| 日志可追溯 | 每条请求与响应落盘 `logs/run.log`，滚动切分防撑爆 |
| 断言可复用 | `checker.py` 提供状态码、业务码、字段类型、金额、状态机五类校验 |
| 数据驱动 | `data/api/*.yaml` + `pytest.mark.parametrize` |
| 失败可定位 | Allure 步骤 + 请求响应附件 + 环境信息标注 |

## 八、项目结构

```
.
├── api/                  # API 自动化套件
│   ├── conftest.py       # 登录、数据重置等夹具
│   └── testcases/        # 用例，一个业务模块一个文件
├── ui/                   # UI 自动化套件（规划中）
├── common/               # 公共封装层
│   ├── config_loader.py  # 配置加载
│   ├── logger.py         # 日志
│   ├── api_client.py     # 请求客户端（Session + token）
│   ├── checker.py        # 断言封装
│   └── report.py         # 报告与环境信息
├── config/               # 环境配置
├── data/                 # 测试数据（yaml）
├── docs/                 # 测试策略、缺陷报告、截图
├── logs/                 # 运行日志（gitignore）
├── pytest.ini
├── requirements.txt
└── run.py                # 一键执行入口
```

## 九、快速开始

**环境要求**

| 类别 | 要求 | 说明 |
| --- | --- | --- |
| Python | >= 3.9 | 开发环境 3.13.0 |
| Java (JRE/JDK) | >= 8 | Allure CLI 渲染报告的依赖 |
| Allure CLI | >= 2.24 | 开发环境 2.36.0，pip 无法安装 |
| Git | >= 2.30 | — |

**安装与运行**

```bash
python -m pip install -r requirements.txt
python -m pytest -m smoke                       # 跑冒烟
python -m pytest                                 # 全量
python -m pytest -m "p0 and order"               # 按优先级 + 模块筛选
```

> 统一使用 `python -m pytest`，它会将当前目录加入 `sys.path`。

## 十、Roadmap

- [x] 配置层 / 日志 / 请求客户端
- [ ] 断言封装与夹具（进行中）
- [ ] 数据驱动用例
- [ ] Allure 报告与一键执行入口
- [ ] 接入 GitHub Actions 持续集成
- [ ] UI 自动化（Selenium / Playwright）

## 十一、License

MIT
