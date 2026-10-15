# WorkBuddy 开发对话上下文

本文件记录「麦麦时光机」在腾讯 WorkBuddy 中的开发过程，用于核验 WorkBuddy 专项奖励的参与资格。

## 一、需求澄清

**用户**：我想参加麦当劳程序员创意开发大赛。

**WorkBuddy**：检索并梳理官方活动规则（活动时间、参与方式、必须提交的文件清单、排名机制、奖励设置），
同时拉取麦当劳 MCP Server 的完整 Tool 列表，据此给出选题建议并发起确认。

**用户决策**：
- 选题：麦麦时光机 —— 通过历史订单分析消费习惯，生成年度回忆、口味画像、消费趋势，并推荐下一次尝试的餐品
- 形态：WorkBuddy Skill + CLI
- 已有准备：GitHub 账号、麦当劳 MCP Token

## 二、方案设计（WorkBuddy 产出）

1. **MCP 客户端层**：用 Python 标准库实现 Streamable HTTP 客户端，处理 `initialize` 会话握手、
   SSE/JSON 双解析、401/429 错误、指数退避重试。
2. **采集与归一化层**：不硬编码返回体结构，改用「候选字段名 + 递归搜索」做 Schema 容错；
   解析失败保留原始 JSON 供排查。
3. **分析层**：口味画像、品类偏好、时段/星期/月度趋势、年度回忆、黄金搭档、下次尝试推荐。
4. **呈现层**：终端 ASCII 简报 + Markdown 报告 + 单文件 HTML 分享卡片。
5. **演示层**：`mcdtm demo` 用确定性随机种子生成虚构数据，0 成本展示完整效果。

## 三、开发过程（关键指令与产出）

| 步骤 | 指令要点 | 产出 |
| --- | --- | --- |
| 1 | 建项目骨架，实现 MCP Streamable HTTP 客户端 | `mcd_timemachine/mcp_client.py`、`__init__.py` |
| 2 | 实现数据采集与 Schema 容错归一化 | `mcd_timemachine/collector.py` |
| 3 | 实现口味画像 / 趋势 / 回忆 / 推荐算法 | `mcd_timemachine/analyzer.py` |
| 4 | 实现终端简报 + Markdown + HTML 报告渲染 | `mcd_timemachine/reporter.py` |
| 5 | 实现示例数据生成器与命令行入口 | `mcd_timemachine/demo_data.py`、`cli.py`、`__main__.py` |
| 6 | 冒烟测试：`python -m mcd_timemachine demo` | 全链路跑通，报告正常生成 |
| 7 | 根据测试结果调优：品类识别补「玉米/薯饼/脆汁鸡」，画像阈值下调并保底 3 个标签，推荐去重用名称归一化 | 画像与推荐结果明显更合理 |
| 8 | 补齐参赛材料与 WorkBuddy Skill | `README.md`、`MCP_INTEGRATION.md`、`CONTEST_DECLARATION.md`、`mcp-config.example.json`、`skill/SKILL.md` |

## 四、WorkBuddy 在项目中的实际作用

- **调研**：检索并归纳官方赛事规则与 29 个 MCP Tool 的能力边界，据此收敛选题
- **架构设计**：分层设计（客户端 / 采集 / 分析 / 呈现 / 演示）与容错策略
- **编码实现**：全部源码由 WorkBuddy 在 Agent 模式下直接写入文件
- **自测迭代**：运行 demo 链路，根据输出反向修正品类规则、阈值与去重逻辑
- **参赛材料**：README、MCP 集成说明、Skill 定义一次性生成并落盘

## 五、说明

- 开发全程在 WorkBuddy 中完成，未使用其他 AI 编程工具生成主体代码。
- 本项目为麦当劳程序员创意开发大赛参赛作品，非麦当劳官方产品。
- 本文件不含任何真实 Token、密钥或个人敏感信息。
