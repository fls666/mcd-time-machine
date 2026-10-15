# 麦麦时光机 · McD Time Machine

> 把吃过的每一单，变成一份可分享的年度回忆、口味画像和消费趋势报告。

基于 **麦当劳中国 MCP（Model Context Protocol）开放能力** 开发的 Skill + CLI 工具：读取你的历史订单，
在本地完成统计与画像，输出一份终端简报 + Markdown 报告 + 一张可以直接发出去的 HTML 年度卡片。

**零第三方依赖**（只用 Python 标准库）；**数据不出本机**，不上传、不外传。

---

## 先看效果（不用装任何东西）

- **示例年度报告（HTML）**：[docs/preview.html](./docs/preview.html) —— 直接在浏览器打开，单文件、离线可看
- **示例年度报告（Markdown）**：[docs/preview.md](./docs/preview.md)
- 仓库已配置 GitHub Actions，主分支更新后会自动把示例报告发布到 Pages

> 示例报告由 `mcdtm demo` 用**虚构订单**生成，报告顶部有醒目的「示例数据」标识，不会被误认为真实消费记录。

---

## 为什么做这个

麦当劳 MCP 开放了历史订单、菜单、营养、优惠券、积分等 29 个 Tool，但普通用户很难直接消费这些数据。
「麦麦时光机」把它们串成一件有情绪价值的事：

- 你第一次点麦当劳是什么时候、点了什么
- 你是「嗜辣星人」还是「咖啡续命」，有哪些数据支撑
- 你最难忘的一单，是哪一天、花了多少、点了什么
- 这一年的口味往哪个方向变了
- 哪些经典款你居然从来没吃过

---

## 快速开始

### 0. 环境要求

- Python **3.9+**（无需 pip 安装任何依赖）
- 一个麦当劳 MCP Token（[申请入口](https://open.mcd.cn/mcp)，手机号登录后点「控制台 → 激活」）

> 只想先看看效果？跳过 Token，直接跑 `mcdtm demo`，用虚构数据体验全流程。

### 1. 安装

```bash
git clone https://github.com/fls666/mcd-time-machine.git
cd mcd-time-machine
pip install -e .          # 或者直接用 python -m mcd_timemachine，无需安装
```

### 2. 配置 Token（请用环境变量）

```bash
export MCD_MCP_TOKEN="你的MCP Token"      # Windows PowerShell: $env:MCD_MCP_TOKEN="..."
```

> **不要用 `--token` 传密钥**：命令行参数会留在 shell 历史里，并可能被同机其他进程看到。
> `--token` 仅为临时调试保留。

### 3. 先诊断，再采集

```bash
mcdtm doctor      # 检查连通性、打印 order-list 的真实入参 schema 与返回结构
mcdtm tools       # 列出当前账号可用的 MCP 工具
mcdtm all         # 拉取（自动翻页）+ 分析 + 生成报告
```

没有 Token 也能玩：

```bash
mcdtm demo --count 160
```

**Windows 用户**：看 [docs/WINDOWS_SETUP.md](./docs/WINDOWS_SETUP.md)，
从装 Python 到配环境变量到排障，每一步都有截图级说明；
也可以直接跑 `python scripts\windows_wizard.py` 走一键向导。

---

## 命令一览

| 命令 | 作用 |
| --- | --- |
| `mcdtm demo` | 用**虚构**示例数据跑通全流程，用于演示与自测 |
| `mcdtm doctor` | 诊断真实 MCP：连通性、工具 schema、首屏返回结构（结果写入 `doctor.json`） |
| `mcdtm tools` | 列出麦当劳 MCP 当前开放的工具，`*` 标记本工具会用到的 |
| `mcdtm fetch` | 拉取历史订单（**自动翻页**）与菜单、营养、优惠券等增强数据 |
| `mcdtm analyze` | 基于已拉取数据做分析并生成报告 |
| `mcdtm all` | `fetch` + `analyze` |
| `mcdtm inspect orders` | 打印某个工具的原始返回，便于核对字段结构（**含隐私，勿公开**） |
| `mcdtm share` | 导出**脱敏**后的结构报告 `share.json`，可安全贴到 Issue 求助 |

通用参数（写在子命令前后都可以）：`--token` / `--endpoint`（默认 `https://mcp.mcd.cn`）/ `--out`（默认 `./mcdtm-output`）。
`fetch` 另有 `--max-pages`（默认 30）与 `--page-size`（默认 100）。

---

## 数据可信度：报告会如实说明自己

一个看起来漂亮、但统计可能不完整的年度报告，会损害作品的可信度。
所以每份报告**开头固定有一块「数据说明」**，明确列出：

| 项目 | 说明 |
| --- | --- |
| 数据来源 | 「真实数据 · 来自麦当劳 MCP」或「示例数据 · 虚构生成」，用彩色徽章区分 |
| 统计区间 | 实际覆盖的首尾日期 |
| 订单覆盖 | 最终统计 N 单，原始拉取 M 条，去重 X 条，翻了 P 页 |
| 字段完整度 | 有日期 / 有金额 / 有明细 各是多少单 |
| 覆盖范围告警 | 若达到分页上限，会明确写「可能仍有订单未纳入统计」 |
| 工具状态表 | 每个 MCP Tool 是成功还是被跳过 |

同时：
- 若存在缺金额的订单，报告会直接提示「累计消费可能低于实际值」
- 示例数据报告顶部有橙色横幅，写明「不来自任何真实账号」
- 所有统计口径（去重规则、翻页规则）都在 [`MCP_INTEGRATION.md`](./MCP_INTEGRATION.md) 中说明

---

## 它算出了什么

| 维度 | 内容 |
| --- | --- |
| 数据说明 | 来源、区间、覆盖、字段完整度、告警、工具状态 |
| 数字总览 | 订单总数、累计消费、平均客单价、单品总数、月均消费、最长连续打卡 |
| 年度关键词 | 年度单品、品类控、时段档、口味标签、黄金搭档、主场门店 |
| 口味画像 | 嗜辣星人 / 甜食主义 / 汉堡原教旨 / 薯条依赖症 / 咖啡续命 / 早起干饭人 / 夜宵常客 / 性价比猎手 |
| 口味变化 | 前后半程各品类占比的变化，给出上升 / 下降的百分点 |
| 品类偏好 | 汉堡、小食、甜品、饮料、早餐、沙拉 的件数与占比 |
| 消费趋势 | 逐月订单数与消费额 |
| 时间切片 | 七个时段分布、一周七天节奏 |
| 年度回忆 | 故事的起点、**最难忘的一单**、最豪横的一次、最沉迷的月份、你的主场、深夜食堂、最长连续打卡 |
| 黄金搭档 | 同单共现次数最高的组合 |
| 下次试试 | 结合品类偏好与真实菜单，推荐你还没点过的单品（命中营养 Tool 时附带热量） |

---

## 项目结构

```
mcd-time-machine/
├── mcd_timemachine/
│   ├── mcp_client.py     # Streamable HTTP MCP 客户端（按 id 匹配响应、会话失效重建）
│   ├── collector.py      # 分页采集 + Schema 容错归一化 + 覆盖率统计
│   ├── analyzer.py       # 口味画像 / 趋势 / 关键词 / 回忆 / 推荐
│   ├── reporter.py       # 终端简报 + Markdown + HTML 卡片
│   ├── demo_data.py      # 虚构示例数据生成器
│   └── cli.py            # 命令行入口
├── tests/                # 50+ 条 unittest 用例，零第三方依赖
├── docs/preview.html     # 示例年度报告（可直接在浏览器打开）
├── skill/SKILL.md        # WorkBuddy Skill 定义（对话式使用）
├── MCP_INTEGRATION.md    # 用了哪些 MCP Tool、怎么调用、业务价值
├── CONTEST_DECLARATION.md
├── mcp-config.example.json
├── workbuddy.md          # 使用 WorkBuddy 开发的对话上下文与证据
└── README.md
```

---

## 测试

```bash
python -m unittest discover -s tests -v
```

覆盖：空数据、缺金额/缺日期订单、重复订单去重、嵌套单品字段污染、
分页终止与截断、边界日期、连续打卡、HTML 转义与结构闭合、示例数据确定性。

`push` / `pull_request` 时 GitHub Actions 会在 Python 3.9 ~ 3.13 上自动跑编译检查、单元测试与 demo 冒烟。

---

## 设计要点

**1. 真分页，不是「只取第一页」**
先探测服务端接受的分页参数命名（7 种组合），再循环翻页，直到本页不满、全是重复订单或命中终止信号；
达到 `--max-pages` 上限时会在报告里明确标注「可能不完整」。

**2. 可靠去重**
以**完整订单号**为唯一键；无订单号时退化为整条记录的 SHA-256 全量哈希，不再用「JSON 前 200 字符」这种会误判的近似标识。

**3. 防字段误判**
抽取整单金额 / 时间时只在订单本体与浅层结构中查找，并显式跳过 `items` / `products` 等明细容器，
避免把某个单品的价格或时间当成整单数据（有专门的回归测试守着）。

**4. 不假设返回体结构**
麦当劳 MCP 各 Tool 的字段命名不统一且会随版本演进，因此用「候选字段名 + 有限深度搜索」做兼容解析；
解析不出时保留原始 JSON，`mcdtm inspect` / `mcdtm doctor` 可随时核对。

**5. 隐私优先**
- 所有数据只写入本地 `mcdtm-output/`，无任何外发请求（除 MCP 官方接口）
- 仓库配置示例只含 `${MCD_MCP_TOKEN}` 占位符
- `.gitignore` 已排除 `mcdtm-output/`、`.env` 等一切可能含敏感信息的文件
- **请勿把 `mcdtm-output/raw/` 下的原始返回（含订单、门店等个人信息）提交到任何公开仓库或发给他人**

**6. 零依赖**
只依赖 Python 标准库，`git clone` 下来就能跑。

---

## 已知限制

- **真实 MCP 联调需本人完成**：本项目已通过 63 条单元测试与示例数据全链路验证，
  但各账号的返回结构可能不同。首次使用请先运行 `mcdtm doctor`；
  若 `order-list` 解析异常，跑 `mcdtm share` 生成脱敏结构报告贴到 Issue，
  我会据此补进字段候选表（**不要贴 `doctor.json` 或 `raw/`，里面有真实数据**）。
- **`order-list` 只返回「近期」订单**：这是麦当劳官方接口的范围（见
  [工具说明](https://github.com/M-China/mcd-mcp-server)），服务端可能只保留有限条数。
  本工具不会假装数据完整，会在「数据说明」里写清实际覆盖了多少单。
- 只调用只读类 Tool，**不会下单、领券或抽奖**，因此不涉及任何写操作风险。
- 门店、渠道等维度依赖返回体中是否有对应字段，缺失时会在「数据说明」中如实反映。

---

## 常见问题

**Q：为什么 `mcdtm fetch` 解析出 0 条订单？**
A：先跑 `mcdtm doctor`，再跑 `mcdtm share`，把生成的 `share.json`（已脱敏）贴到 Issue。
我据此补齐 `collector.py` 里的 `K_*` 字段候选表，通常一次就能修好。也欢迎直接 PR。

**Q：会漏掉订单吗？**
A：会翻页直到取不到新数据。若订单量极大且达到 `--max-pages` 上限，报告「数据说明」会明确标注不完整——不会默默少算。

**Q：提示 401 / 429？**
A：401 是 Token 无效或过期，去控制台重新激活；429 是触发 600 次/分钟限流，客户端已内置指数退避重试。

**Q：营养热量没显示？**
A：只有账号能调到 `list-nutrition-foods` 且餐品能对上名时才展示，属增强项，不影响主流程。

---

## 参赛信息

本项目为 **麦当劳程序员创意开发大赛（2026）** 参赛作品，由参赛者独立开发，**非麦当劳官方产品**。
活动规则与排行榜以 [官方活动仓库](https://github.com/M-China/mcd-developer-innovation-challenge) 为准。

项目输出仅供参考，**不构成医疗、营养或其他专业建议**；餐品信息、价格与供应状态以麦当劳官方渠道实时结果为准。

觉得有意思的话，点个 Star 就是最大的支持。

---

## 许可

代码部分采用 [MIT License](./LICENSE)。
McDonald's 相关商标、品牌资产归其权利人所有，本项目不构成任何商标授权。
