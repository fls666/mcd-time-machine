# MCP 集成说明

本文件说明「麦麦时光机」实际使用的麦当劳 MCP Server、Tool、调用流程、
数据准确性保障机制与业务价值。

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
Token 只从环境变量读取（CLI 的 `--token` 仅作临时调试，不推荐）。

## 2. 使用的 Tool

### 核心（必须）

| Tool | 用途 | 在时光机里的角色 |
| --- | --- | --- |
| `order-list` | 查询近期到店 / 外送历史订单 | **数据源主干**：所有画像、趋势、回忆都由它推导 |

### 增强（可选，失败不影响主流程，会在报告中标注）

| Tool | 用途 | 业务价值 |
| --- | --- | --- |
| `query-meals` | 查询当前门店可售卖的餐品菜单 | 提供**真实菜单**作为「下次试试」的推荐池，避免推荐下架商品 |
| `list-nutrition-foods` | 餐品营养信息列表 | 给推荐单品补上热量参考 |
| `query-my-coupons` | 我的优惠券查询 | 预留：结合推荐结果提示可用券 |
| `query-my-account` | 我的积分查询 | 预留：展示积分资产与年度积分流向 |
| `campaign-calendar` | 活动日历查询 | 预留：把「最常光顾的月份」与当月营销活动对齐 |
| `mall-order-list` | 麦麦商城订单查询 | 预留：并入积分兑换侧的时间线 |

调用映射见 `mcd_timemachine/mcp_client.py` 中的 `TOOLS` 常量。

## 3. 调用流程

```
        ┌──────────────────────────┐
        │  POST initialize         │  ← 响应头带回 mcp-session-id
        │  POST notifications/...  │
        │  POST tools/list         │  ← 发现可用工具与入参 schema
        └────────────┬─────────────┘
                     │
                     ▼
        ┌────────────────────────────────────────────┐
        │  POST tools/call  name: order-list         │
        │  ① 探测分页参数命名（pageNo/pageNum/page…）  │◄───┐
        │  ② 取一页，按订单号去重后并入结果集           │    │
        │  ③ 本页不满 / 全重复 / hasMore=false → 停止  │    │
        │  ④ 否则 page+1，回到 ①                      │────┘
        │  ⑤ 达到 --max-pages → 标记「可能不完整」     │
        └────────────────────┬───────────────────────┘
                             │
        ┌────────────────────┼────────────────────────┐
        ▼                    ▼                        ▼
  query-meals       list-nutrition-foods        campaign-calendar
  query-my-coupons  query-my-account            mall-order-list
        │                    │                        │
        └────────────────────┼────────────────────────┘
                             ▼
              Schema 容错归一化（候选字段名 + 有限深度搜索）
                             ▼
              覆盖率统计 provenance（页数 / 条数 / 字段完整度 / 工具状态）
                             ▼
              本地分析 → 关键词 / 画像 / 口味变化 / 回忆 / 推荐
                             ▼
              终端简报 + report.md + report.html（顶部固定「数据说明」）
                             + story.html（六章节滚动故事 + 隐私友好分享卡片）
```

### 关键实现点

1. **会话保持**：从 `initialize` 响应头读取 `mcp-session-id`，后续请求原样带回。
2. **响应严格匹配**：SSE 流按 `event:` / `data:` 边界解析成多条消息，
   **按 JSON-RPC id 精确匹配**本次请求的响应，通知类消息（无 id）被忽略；
   匹配不到或返回 session 相关错误时，自动重建会话并重试一次。
3. **入参试探**：`order-list` 等工具的分页参数命名存在版本差异，
   按 `(pageNo,pageSize)` → `(pageNum,pageSize)` → `(page,size)` → … 共 7 种组合依次尝试。
4. **Schema 容错**：不假设返回体固定结构，而是递归搜索「像订单」的字典节点，
   再按候选字段名抽取时间 / 金额 / 门店 / 商品明细；解析不出时保留原始 JSON，
   供 `mcdtm inspect` 与 `mcdtm doctor` 排查。

## 4. 数据准确性保障（针对静态审查问题的修复）

| 风险 | 修复方式 | 守卫测试 |
| --- | --- | --- |
| 只取第一页导致年度数据不完整 | 循环翻页 + 终止条件 + `--max-pages` 截断告警 | `test_fetches_all_pages`、`test_stops_when_page_not_full`、`test_max_pages_limit_marks_truncated` |
| 去重标识不可靠（曾取 JSON 前 200 字符） | 以**完整订单号**为键；无订单号时用整条记录 SHA-256 | `test_same_order_id_across_pages_deduped`、`test_orders_without_id_use_full_hash` |
| 嵌套单品字段污染整单金额 / 时间 | 整单字段只在订单本体与浅层查找，显式跳过 `items` 等明细容器 | `test_order_total_not_polluted_by_item_price`、`test_nested_item_time_not_used_as_order_time` |
| 商品节点被误判成订单 | 识别规则要求「有订单号」或「有时间且带金额/明细」，纯商品节点直接排除 | `test_item_node_is_not_treated_as_order` |
| 统计结果看起来比实际完整 | 报告顶部固定「数据说明」区块，列出来源、区间、覆盖、字段完整度与告警 | `test_contains_coverage_section`、`test_missing_amount_warning` |
| 示例数据被误认为真实数据 | 示例报告顶部橙色横幅 + 徽章 + Markdown 提示 | `test_demo_is_clearly_marked`、`test_source_badges` |

## 5. 调用频率与合规

- 单次完整采集约发起 6–8 次 `tools/call`，远低于 600 次/分钟限额；翻页时每页 1 次。
- 不调用任何写操作类 Tool（`create-order`、`auto-bind-coupons`、`draw-lottery` 等一律不涉及），
  只读取历史数据，**不会对用户账号产生任何下单或领券行为**。
- Token 仅从环境变量 / 命令行读取，不写入任何文件；输出目录 `mcdtm-output/` 已被 `.gitignore` 排除。
- 原始返回（`mcdtm-output/raw/`）含订单与门店等个人信息，**不应分享或提交到公开仓库**。

## 6. 联调验证方式

Windows 逐步教程见 [docs/WINDOWS_SETUP.md](./docs/WINDOWS_SETUP.md)，
一键向导见 `scripts/windows_wizard.py`。

```bash
export MCD_MCP_TOKEN="..."      # Windows PowerShell: $env:MCD_MCP_TOKEN="..."
mcdtm doctor      # 输出 serverInfo、工具总数、order-list 的 inputSchema 与首屏返回样例
```

诊断结果写入 `mcdtm-output/doctor.json`。

### 6.1 如何反馈真实结构（脱敏，但不承诺绝对安全）

`doctor.json` 与 `mcdtm-output/raw/` 内含**真实订单号、金额、门店**，不能直接公开。
请先导出脱敏版本：

```bash
mcdtm share       # 生成 mcdtm-output/share.json
```

`share.json` 只保留**字段名与值的形状**：

| 原值 | 脱敏后 |
| --- | --- |
| `MCD20260305123456` | `aaa99999999999999` |
| `2026-03-05 12:30:00` | `9999-99-99 99:99:99` |
| `38.5` | `<num:1dp:2位>` |
| `南京西路餐厅` | `汉汉汉汉汉汉` |

文件还附带 `suggested_mappings`，自动推断 order_id / order_time / order_amount /
store / items_container / item_name / item_qty / item_price 对应的字段路径，
据此即可直接补齐 `collector.py` 的候选表，无需改动上层分析逻辑。

**关于「是否绝对安全」的如实说明**：脱敏会把所有值替换为类型与长度，
并对产物做一次 PII 扫描（手机号 / 邮箱 / 身份证 / 银行卡 / URL / 24 位以上长串 / 6 位以上数字），
命中项替换为占位符，命中类型记录在 `pii_scan.hits`。
但它**无法穷举所有未知字段与自定义格式**，因此定位是「大幅降低风险的排障材料」，
而非「可以闭眼公开的文件」——公开前请人工通读一遍。

> 实现见 `mcd_timemachine/share.py`，脱敏逻辑有 14 条单元测试守卫
> （`tests/test_share.py`），确保真实值不会泄漏进产物。

## 7. 业务价值

| 视角 | 价值 |
| --- | --- |
| 用户 | 把散落的消费记录变成有情绪价值的年度回忆，并给出可执行的「下次吃什么」建议 |
| 麦当劳 | 为开放的 MCP 能力提供一个可被普通消费者感知的样板，验证「订单数据 → 用户洞察」链路 |
| 开发者生态 | 零依赖、带分页与容错实现的 MCP 客户端，可作为接入麦当劳 MCP 的参考起点 |
