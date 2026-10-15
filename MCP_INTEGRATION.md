# MCP 集成说明

本文件说明「麦麦时光机」实际使用的麦当劳 MCP Server、Tool、调用流程与业务价值。

## 1. 接入信息

| 项目 | 内容 |
| --- | --- |
| MCP Server | 麦当劳中国官方 MCP Server |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（JSON-RPC 2.0） |
| 鉴权方式 | 请求头 `Authorization: Bearer ${MCD_MCP_TOKEN}` |
| 限流 | 600 次 / 分钟（超限时返回 429，客户端内置指数退避重试） |
| Token 申请 | https://open.mcd.cn/mcp 手机号登录 → 控制台 → 激活 |

配置文件见 [`mcp-config.example.json`](./mcp-config.example.json)，**仅含环境变量占位符**，不含任何真实凭证。

## 2. 使用的 Tool

### 核心（必须）

| Tool | 用途 | 在时光机里的角色 |
| --- | --- | --- |
| `order-list` | 查询近期到店 / 外送历史订单 | **数据源主干**：所有画像、趋势、回忆都由它推导 |

### 增强（可选，失败不影响主流程）

| Tool | 用途 | 业务价值 |
| --- | --- | --- |
| `query-meals` | 查询当前门店可售卖的餐品菜单 | 提供**真实菜单**作为「下次试试」的推荐池，避免推荐下架商品 |
| `list-nutrition-foods` | 餐品营养信息列表 | 给推荐单品补上热量参考；为画像补充能量维度 |
| `query-my-coupons` | 我的优惠券查询 | 预留：结合推荐结果提示可用券 |
| `query-my-account` | 我的积分查询 | 预留：展示积分资产与年度积分流向 |
| `campaign-calendar` | 活动日历查询 | 预留：把「最沉迷的月份」与当月营销活动对齐，增强回忆感 |
| `mall-order-list` | 麦麦商城订单查询 | 预留：并入积分兑换侧的时间线 |

调用映射见 `mcd_timemachine/mcp_client.py` 中的 `TOOLS` 常量。

## 3. 调用流程

```
                        ┌──────────────────────────┐
   MCD_MCP_TOKEN  ─────►│  POST initialize         │  ← 响应头带回 mcp-session-id
                        │  POST notifications/...  │
                        │  POST tools/list         │  ← 发现可用工具与入参 schema
                        └────────────┬─────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        ▼                            ▼                            ▼
  POST tools/call             POST tools/call              POST tools/call
  name: order-list            name: query-meals            name: list-nutrition-foods
  （分页参数按候选组合          name: query-my-coupons       name: campaign-calendar
    自动试探，兼容命名差异）     name: query-my-account       name: mall-order-list
        │                            │                            │
        └────────────────────────────┼────────────────────────────┘
                                     ▼
                    Schema 容错归一化（候选字段名 + 递归搜索）
                                     ▼
                    本地统计分析 → 口味画像 / 趋势 / 回忆 / 推荐
                                     ▼
                    终端简报 + report.md + report.html
```

### 关键实现点

1. **会话保持**：从 `initialize` 响应头读取 `mcp-session-id`，后续请求原样带回。
2. **双解析**：同一套代码同时支持 `application/json` 与 `text/event-stream`（SSE）两种响应体。
3. **入参试探**：`order-list` 等工具的分页参数命名存在版本差异，`call_tool_any()` 会按
   `{pageNo,pageSize}` → `{pageNum,pageSize}` → `{page,size}` → … 的顺序依次尝试，直到成功。
4. **Schema 容错**：不假设返回体固定结构，而是递归搜索「像订单」的字典节点，
   再按候选字段名抽取时间 / 金额 / 门店 / 商品明细；解析不出时保留原始 JSON 供 `mcdtm inspect` 排查。

## 4. 调用频率与合规

- 单次完整采集约发起 6–8 次 `tools/call`，远低于 600 次/分钟限额。
- 不调用任何写操作类 Tool（`create-order`、`auto-bind-coupons`、`draw-lottery` 等一律不涉及），
  只读取历史数据，**不会对用户账号产生任何下单或领券行为**。
- Token 仅从环境变量 / 命令行读取，不写入任何文件；输出目录 `mcdtm-output/` 已被 `.gitignore` 排除。

## 5. 业务价值

| 视角 | 价值 |
| --- | --- |
| 用户 | 把散落的消费记录变成有情绪价值的年度回忆，并给出可执行的「下次吃什么」建议 |
| 麦当劳 | 为开放的 MCP 能力提供一个可被普通消费者感知的样板，验证「订单数据 → 用户洞察」链路 |
| 开发者生态 | 零依赖、Schema 容错的 MCP 客户端实现，可作为接入麦当劳 MCP 的参考起点 |
