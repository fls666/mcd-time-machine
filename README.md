# 麦麦时光机 · McD Time Machine

> 把吃过的每一单，变成一份可分享的年度回忆、口味画像和消费趋势报告。

基于 **麦当劳中国 MCP（Model Context Protocol）开放能力** 开发的 Skill + CLI 工具：读取你的历史订单，
在本地完成统计与画像，输出一份终端简报 + Markdown 报告 + 一张可以直接发出去的 HTML 年度卡片。

**零第三方依赖**，只用 Python 标准库；**数据不出本机**，不上传、不外传。

---

## 为什么做这个

麦当劳 MCP 开放了历史订单、菜单、营养、优惠券、积分等 29 个 Tool，但普通用户很难直接消费这些数据。
「麦麦时光机」把它们串成一件有情绪价值的事：

- 你第一次点麦当劳是什么时候、点了什么
- 你是「嗜辣星人」还是「咖啡续命」，有哪些数据支撑
- 哪几个月最上头，哪几家店是你的主场
- 哪些经典款你居然从来没吃过

---

## 效果预览

### 终端简报

```
========================================================
麦麦时光机 · 你的麦当劳消费回忆
========================================================
统计区间   2025-09-13  ~  2026-10-10   (跨度 393 天)
订单总数   120 单
累计消费   ¥6,436.50
平均客单   ¥53.64
单品总数   453 件
最长连续   4 天

-- 口味画像 ----------------------------------------------------
  汉堡原教旨    ######                   汉堡占比 24.9%
  甜食主义     ####                     甜品占了 15.7%
  嗜辣星人     ###                      13.0% 的单品带「辣」字
  咖啡续命     ##                       咖啡类占比 8.8%

-- 品类偏好 ----------------------------------------------------
  小食     ######################## 141 件   31.1%
  汉堡     ###################      113 件   24.9%
  甜品     ############              71 件   15.7%
```

### HTML 年度报告

`mcdtm-output/report.html` 是一张单文件、零外链的分享卡片：红色主视觉 + 数据条 + 回忆时间轴，
浏览器打开即可截图发朋友圈，也可以直接部署成网页。

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

### 2. 配置 Token

```bash
export MCD_MCP_TOKEN="你的MCP Token"     # Windows PowerShell: $env:MCD_MCP_TOKEN="..."
```

也可以在命令里显式传：`mcdtm fetch --token "你的Token"`。

### 3. 三步跑出报告

```bash
mcdtm tools        # 看看当前账号能用到哪些 MCP 工具
mcdtm fetch        # 拉取历史订单与菜单等数据（原始数据落在 mcdtm-output/raw/）
mcdtm analyze      # 分析并生成 report.md / report.html / profile.json
```

一步到位：

```bash
mcdtm all
```

没有 Token 也能玩：

```bash
mcdtm demo --count 160
```

---

## 命令一览

| 命令 | 作用 |
| --- | --- |
| `mcdtm demo` | 用**虚构**示例数据跑通全流程，用于演示与自测 |
| `mcdtm tools` | 列出麦当劳 MCP 当前开放的工具，`*` 标记本工具会用到的 |
| `mcdtm fetch` | 拉取历史订单、菜单、营养、优惠券、积分、活动日历 |
| `mcdtm analyze` | 基于已拉取数据做分析并生成报告 |
| `mcdtm all` | `fetch` + `analyze` |
| `mcdtm inspect orders` | 打印某个工具的原始返回，便于核对字段结构 |

通用参数：`--token` / `--endpoint`（默认 `https://mcp.mcd.cn`）/ `--out`（默认 `./mcdtm-output`）。

---

## 它算出了什么

| 维度 | 内容 |
| --- | --- |
| 数字总览 | 订单总数、累计消费、平均客单价、单品总数、月均消费、最长连续打卡 |
| 口味画像 | 嗜辣星人 / 甜食主义 / 汉堡原教旨 / 薯条依赖症 / 咖啡续命 / 早起干饭人 / 夜宵常客 / 性价比猎手 |
| 品类偏好 | 汉堡、小食、甜品、饮料、早餐、沙拉 的件数与占比 |
| 消费趋势 | 逐月订单数与消费额 |
| 时间切片 | 七个时段分布、一周七天节奏 |
| 年度回忆 | 故事的起点、最豪横的一单、最沉迷的月份、你的主场、深夜食堂、最长连续打卡 |
| 黄金搭档 | 同单共现次数最高的组合 |
| 下次试试 | 结合品类偏好与真实菜单，推荐你还没点过的单品（命中营养 Tool 时附带热量） |

---

## 项目结构

```
mcd-time-machine/
├── mcd_timemachine/
│   ├── mcp_client.py     # Streamable HTTP MCP 客户端（标准库实现，含 401/429 处理、SSE 解析）
│   ├── collector.py      # 数据采集 + Schema 容错归一化
│   ├── analyzer.py       # 口味画像 / 趋势 / 回忆 / 推荐
│   ├── reporter.py       # 终端简报 + Markdown + HTML 卡片
│   ├── demo_data.py      # 虚构示例数据生成器
│   └── cli.py            # 命令行入口
├── skill/SKILL.md        # WorkBuddy Skill 定义（对话式使用）
├── MCP_INTEGRATION.md    # 用了哪些 MCP Tool、怎么调用、业务价值
├── CONTEST_DECLARATION.md
├── mcp-config.example.json
├── workbuddy.md          # 使用 WorkBuddy 开发的对话上下文
└── README.md
```

---

## 设计要点

**1. 不硬编码返回体结构**
麦当劳 MCP 各 Tool 的字段命名不统一，且会随版本演进。这里用「候选字段名 + 递归搜索」做兼容解析，
解析失败不报错，而是把原始 JSON 落盘，`mcdtm inspect` 可随时人工核对。

**2. 隐私优先**
- 所有数据只写入本地 `mcdtm-output/`，无任何外发请求（除 MCP 官方接口）
- 仓库中的配置示例 `mcp-config.example.json` 只含 `${MCD_MCP_TOKEN}` 占位符
- `.gitignore` 已排除 `mcdtm-output/`、`.env` 等一切可能含敏感信息的文件

**3. 没有 Token 也能演示**
`mcdtm demo` 用确定性随机种子生成虚构订单， reviewers 和路人 0 成本看到完整效果。

**4. 零依赖**
只依赖 Python 标准库，`git clone` 下来就能跑，不需要解决依赖地狱。

---

## 常见问题

**Q：为什么 `mcdtm fetch` 解析出 0 条订单？**
A：先跑 `mcdtm inspect orders` 看原始返回。若结构确实不同，把输出贴到 Issue，
或直接改 `collector.py` 里的 `K_*` 字段候选表，欢迎 PR。

**Q：提示 401？**
A：Token 无效或过期，去 [open.mcd.cn/mcp](https://open.mcd.cn/mcp) 控制台重新激活。

**Q：提示 429？**
A：触发了 600 次/分钟的限流，客户端已内置指数退避重试，一般会自动恢复。

**Q：营养热量没显示？**
A：只有账号能调到 `list-nutrition-foods` 且餐品能在返回中对上名时才会展示，属增强项，不影响主流程。

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
