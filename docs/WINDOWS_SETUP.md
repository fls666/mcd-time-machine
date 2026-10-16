# Windows 真实 MCP 联调教程（一步一步版）

这份文档只讲一件事：**在你的 Windows 电脑上，用真实麦当劳账号跑通《麦麦时光机》**。
全程约 15 分钟，不需要懂 Python，也不需要装任何第三方库。

> 先看这里：本教程**只做只读操作**。麦麦时光机只会调用「查询历史订单 / 菜单 / 营养 / 积分 / 优惠券」这类读取接口，
> 绝不调用 `create-order`（下单）、`auto-bind-coupons`（领券）、`draw-lottery`（抽奖）。
> 你不会因为跑这个工具而被扣积分、被下单或被领券。

---

## 目录

- [0. 前置清单](#0-前置清单)
- [1. 懒人通道：一键向导](#1-懒人通道一键向导)
- [2. 安装 Python](#2-安装-python)
- [3. 申请 MCP Token](#3-申请-mcp-token)
- [4. 拿到项目代码](#4-拿到项目代码)
- [5. 配置环境变量（4 种方式任选）](#5-配置环境变量4-种方式任选)
- [6. 第一次联调：doctor](#6-第一次联调doctor)
- [7. 生成真实年度报告](#7-生成真实年度报告)
- [8. 排障对照表](#8-排障对照表)
- [9. 把结构发给我（安全脱敏）](#9-把结构发给我安全脱敏)
- [10. 隐私与安全须知](#10-隐私与安全须知)

---

## 0. 前置清单

| 需要 | 说明 | 检查方式 |
| --- | --- | --- |
| Windows 10 / 11 | 任意版本 | `Win + R` → 输入 `winver` |
| Python 3.9 以上 | 本项目零第三方依赖 | 见 [第 2 节](#2-安装-python) |
| 一个麦当劳会员账号 | 用手机号登录开放平台 | 见 [第 3 节](#3-申请-mcp-token) |
| 能正常上网 | 需访问 `https://mcp.mcd.cn` | 浏览器打开该地址应返回 JSON 而非超时 |

> **给小白的话**：下面出现的「命令」都要在终端里执行。
> 推荐用 **PowerShell**：按 `Win`，输入 `PowerShell`，回车即可打开。

---

## 1. 懒人通道：一键向导

如果你不想逐条敲命令，项目里带了一个交互向导，会把「配 Token → 诊断 → 导出结构」串起来。

**第一步**，进入项目目录（路径换成你自己的）：

```powershell
cd C:\Users\AIIUNIE\WorkBuddy\2026-10-15-23-26-06\mcd-time-machine
```

**第二步**，运行向导：

```powershell
python scripts\windows_wizard.py
```

向导会依次做四件事：

1. 检查 Python 版本（低于 3.9 会提示你去下载）
2. 让你粘贴 Token（**输入时不回显**），并询问是否永久写入环境变量
3. 自动跑 `doctor`，把连通性和真实返回结构打出来
4. 自动生成 `mcdtm-output\share.json`（脱敏版，分享给他人求助前请自己先过目一遍）

看到 `联调完成` 就可以跳到 [第 7 节](#7-生成真实年度报告)。
如果卡住了，看 [第 8 节](#8-排障对照表)，或者继续往下看分步骤详解。

---

## 2. 安装 Python

### 2.1 先检查有没有

打开 PowerShell，输入：

```powershell
python --version
```

- 显示 `Python 3.x.x` 且 **3.9 及以上** → 直接跳到 [第 3 节](#3-申请-mcp-token)
- 显示 `Python 3.8.x` 或更低 → 需要升级
- 提示「不是内部或外部命令」，或者直接弹出 Microsoft Store → 需要安装

> 如果你装了多个版本，也可以用官方启动器：`py -3 --version`。

### 2.2 安装（python.org 版本，推荐）

1. 打开 <https://www.python.org/downloads/windows/>
2. 点大大的 `Download Python 3.13.x` 按钮
3. **运行安装包时，务必勾选最下面两个选项**：
   - ✅ `Add python.exe to PATH`  ← 最关键，不勾后面每条命令都要写全路径
   - ✅ `Use admin privileges when installing py.exe`
4. 点 `Install Now`
5. 装完**重新打开一个 PowerShell**（旧窗口不认识新的 PATH），再验证：

```powershell
python --version
```

### 2.3 安装（Microsoft Store 版本，备选）

PowerShell 里直接执行：

```powershell
winget install Python.Python.3.13
```

或在 Microsoft Store 搜 `Python 3.13` 安装。装完同样要**重开终端**验证。

> Microsoft Store 版偶尔会在写入某些目录时报权限错误。
> 如果遇到，卸载后改用 2.2 的 python.org 版本。

---

## 3. 申请 MCP Token

Token 是「你麦当劳账号的钥匙」，由麦当劳官方开放平台发放，**免费**。

1. 浏览器打开 <https://open.mcd.cn/mcp>
2. 点右上角 **【登录】**
3. 用**手机号验证码**登录（就是你的麦当劳会员账号）
4. 登录成功后，右上角按钮会变成 **【控制台】**，点它
5. 在弹出的控制台里点 **【激活】** → 勾选同意服务协议
6. Token 生成后点**一键复制**

Token 形如一串长字符（几十位字母数字混合）。**先别急着关页面**，下一步要用。

> ⚠️ **Token 与你的麦当劳账号强绑定，拿到它等于能操作你的账号。**
> 不要发到群里、不要提交到 Git、不要写进任何要公开的文件。
> 本项目的 `.gitignore` 已排除所有可能存放它的文件。

---

## 4. 拿到项目代码

### 方式 A：git clone（装了 Git 的话）

```powershell
cd C:\Users\AIIUNIE\WorkBuddy
git clone https://github.com/fls666/mcd-time-machine.git
cd mcd-time-machine
```

### 方式 B：下载 ZIP（没装 Git 也能用）

1. 打开 <https://github.com/fls666/mcd-time-machine>
2. 点绿色 `Code` 按钮 → `Download ZIP`
3. 解压到任意目录（比如 `C:\Users\AIIUNIE\WorkBuddy\`）
4. 在 PowerShell 里 `cd` 进解压出来的文件夹：

```powershell
cd C:\Users\AIIUNIE\WorkBuddy\mcd-time-machine-main
```

> **不需要 `pip install`**。本项目只用 Python 标准库，这是刻意设计的，
> 就是为了让没有 Python 经验的人也能直接跑起来。

---

## 5. 配置环境变量（4 种方式任选）

> **为什么用环境变量而不是 `--token`？**
> 命令行参数会被记进 shell 历史和进程列表，同一个命令被别人看到就等于泄露 Token。
> 环境变量也存在内存里，但不会出现在你敲过的命令记录中。

### 方式 1：PowerShell 永久生效（推荐）

把下面这行的 `你的Token` 换成真实 Token，在 PowerShell 里执行一次：

```powershell
[System.Environment]::SetEnvironmentVariable('MCD_MCP_TOKEN', '你的Token', 'User')
```

**验证**（必须新开一个 PowerShell 窗口）：

```powershell
echo $env:MCD_MCP_TOKEN
```

能看到 Token 就成功了。

### 方式 2：CMD 永久生效

```cmd
setx MCD_MCP_TOKEN "你的Token"
```

同样要**新开窗口**才生效，验证用：

```cmd
echo %MCD_MCP_TOKEN%
```

> `setx` 的值长度上限是 1024 字符，Token 一般远小于此。
> 注意 `setx` 会把 Token 明文写进注册表，这是 Windows 的常规做法，
> 如果你在公用电脑上操作，跑完后可以用方式 3 的临时方案替代。

### 方式 3：仅当前窗口生效（最安全，公用电脑推荐）

PowerShell：

```powershell
$env:MCD_MCP_TOKEN = '你的Token'
```

CMD：

```cmd
set MCD_MCP_TOKEN=你的Token
```

关掉窗口就失效，下次要重新设置。

### 方式 4：Git Bash / WSL

```bash
export MCD_MCP_TOKEN="你的Token"
```

想永久生效，把它写进 `~/.bashrc`：

```bash
echo 'export MCD_MCP_TOKEN="你的Token"' >> ~/.bashrc
```

---

## 6. 第一次联调：doctor

`doctor` 是最重要的一步——它不改任何东西，只探一下「能不能连上、返回长什么样」。

```powershell
python -m mcd_timemachine doctor
```

### 成功时你会看到

```
接入地址   https://mcp.mcd.cn
服务端     {'name': '...', 'version': '...'}
协议版本   2025-06-18
工具总数   29
目标工具   order-list （存在）
入参 schema {"type": "object", "properties": {...}}
调用成功   参数={'pageNo': 1, 'pageSize': 20} 类型=dict
返回样例：
{"code": 0, "data": {"list": [...]}}

完整诊断已写入 mcdtm-output\doctor.json
```

看到 `调用成功` 就说明**真实联调通过**了。

### 顺手看一下工具清单

```powershell
python -m mcd_timemachine tools
```

会列出你账号当前可用的所有工具，前面带 `*` 的是本工具会用到的。

### 中文乱码怎么办

如果输出是问号或报 `UnicodeEncodeError`，先执行：

```powershell
chcp 65001
$env:PYTHONIOENCODING = "utf-8"
python -m mcd_timemachine doctor
```

想在 PowerShell 里一劳永逸：

```powershell
[System.Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'User')
```

---

## 7. 生成真实年度报告

联调通过后，两条命令就能出报告。

### 分步执行（推荐，方便定位问题）

```powershell
python -m mcd_timemachine fetch
python -m mcd_timemachine analyze
```

`fetch` 会打印：

```
[fetch] MCP 会话已建立
[fetch] 翻页 3 页，原始 128 条，去重后 126 单
[fetch] 有日期 126，有金额 126，有明细 118
```

### 一步到位

```powershell
python -m mcd_timemachine all
```

### 打开报告

```powershell
start mcdtm-output\story.html    :: V2.0 沉浸式故事页（手机竖屏 / 可导出分享卡片）
start mcdtm-output\report.html   :: 完整数据报告
```

### 关于「订单条数」的重要说明

`order-list` 的官方描述是「查询**近期**的到店/外送历史订单」，
这意味着**服务端可能只保留并返回有限条数的订单**（有的账号只有 10 单左右），
这是麦当劳接口本身的范围，不是本工具漏抓。

本工具的处理原则：

- 能翻页就翻，翻到本页不满、全是重复或 `hasMore=false` 为止
- 达到 `--max-pages`（默认 30）上限会**在报告里明确标注「可能不完整」**
- 报告顶部「数据说明」区块会写清：统计区间、最终单数、原始条数、去重条数、翻了几页、
  有多少单有日期 / 有金额 / 有明细

**所以：如果只拿到 10 单，报告不会假装你一年只吃了 10 次，它会如实写出来。**
宁可承认数据不全，也不给你一个看起来漂亮但不可信的结论。

想尝试多翻一些页：

```powershell
python -m mcd_timemachine fetch --max-pages 50 --page-size 50
```

---

## 8. 排障对照表

| 现象 | 原因 | 解决办法 |
| --- | --- | --- |
| `未找到 MCP Token...` | 环境变量没设上 | 回到 [第 5 节](#5-配置环境变量4-种方式任选)，**新开窗口**后再 `echo` 验证 |
| `401 未授权` | Token 无效/过期/复制不完整 | 重新去 <https://open.mcd.cn/mcp> 控制台复制；注意别把首尾空格一起复制 |
| `HTTP 429` | 请求太频繁（限额 600 次/分钟） | 客户端已自动退避重试；若持续出现，等 1 分钟再跑 |
| `网络请求失败` / 超时 | 网络不通或代理 | 见下方「代理与网络」 |
| `SSL: CERTIFICATE_VERIFY_FAILED` | 公司网络做了 HTTPS 拦截 | 换手机热点试试；或联系 IT 放行 `mcp.mcd.cn` |
| 中文变问号 / `UnicodeEncodeError` | 控制台编码 | `chcp 65001` + `$env:PYTHONIOENCODING="utf-8"` |
| `python 不是内部或外部命令` | 安装时没勾 PATH | 重装并勾选 `Add python.exe to PATH`，或用完整路径：`C:\Users\...\Python313\python.exe` |
| `order-list 不存在` | 账号权限或服务端版本差异 | 先跑 `python -m mcd_timemachine tools` 看实际工具名，再用 `doctor --tool 实际名称` |
| `fetch` 后显示「未解析出订单」 | 真实返回结构和字段名不匹配 | 见 [第 9 节](#9-把结构发给我安全脱敏)，把 `share.json` 发我 |
| 只拿到很少的订单 | 服务端只返回近期订单 | 属接口范围限制，报告会如实标注，见第 7 节说明 |

### 代理与网络

如果你在公司网络下，需要走代理：

```powershell
$env:HTTPS_PROXY = "http://proxy.company.com:8080"
$env:HTTP_PROXY  = "http://proxy.company.com:8080"
python -m mcd_timemachine doctor
```

Python 的 `urllib` 会自动读取这两个环境变量。

先确认基础连通性：

```powershell
curl https://mcp.mcd.cn
```

能返回一段 JSON（哪怕是报错 JSON）就说明网络层是通的。

---

## 9. 把结构发给我（安全脱敏）

如果 `fetch` 解析出 0 单，说明真实字段名和我的候选表不匹配——
这很常见，也很好修，但我需要知道真实结构长什么样。

**但真实返回里有你的订单号、金额、门店，不能直接贴。** 所以：

```powershell
python -m mcd_timemachine share
```

它会生成 `mcdtm-output\share.json`，长这样：

```json
{
  "orderNo": "aaa99999999999999",
  "payTime": "9999-99-99 99:99:99",
  "totalAmount": "<num:1dp:2位>",
  "storeName": "汉汉汉汉汉汉",
  "items": [{ "goodsName": "汉汉汉", "price": "<num:1dp:2位>", "qty": "<num:int:1位>" }]
}
```

**字段名全部保留，值全部替换成类型与长度，不保留任何一个原始字符**：

| 原值 | 脱敏后 | 保留了什么 |
| --- | --- | --- |
| `MCD20260305123456` | `<str:17:alnum>` | 是字母数字串、长度 17 |
| `2026-03-05 12:30:00` | `<str:19:datetime>` | 是日期时间、长度 19 |
| `38.5` | `<num:1dp:2位>` | 数字类型、1 位小数、2 位整数 |
| `南京西路餐厅` | `<str:6:cjk>` | 是中文串、长度 6 |
| `13812345678` | `<str:11:phone>` | 是手机号格式（值已丢弃） |

生成时还会对整份产物再做一次敏感信息扫描，把手机号、邮箱、身份证、银行卡号、
URL、24 位以上长串、6 位以上数字串替换掉，命中情况记在 `pii_scan.hits` 里。

> ⚠️ **这仍不等于绝对安全。** 脱敏能去掉绝大多数敏感内容，但无法穷举所有未知字段。
> 请把 `share.json` 当作「风险大幅降低的排障材料」，**公开前自己先通读一遍**。

文件里还会附带一份 **`suggested_mappings`**（自动推断的字段映射建议）：

```
order_id      -> ['data.list[].orderNo']
order_time    -> ['data.list[].payTime']
order_amount  -> ['data.list[].totalAmount']
store         -> ['data.list[].storeName']
items_container -> ['data.list[].items']
item_name     -> ['data.list[].items[].goodsName']
```

把 `share.json` 的内容贴到 GitHub Issue 或直接发我，就能快速补齐兼容，
不需要你懂代码，也不需要透露任何真实数据。

> 顺带一提：`mcdtm-output\raw\` 目录里是**未脱敏的原始返回**，
> 含订单与门店信息，**不要上传、不要贴出来**。它已被 `.gitignore` 排除。

---

## 10. 隐私与安全须知

| 事项 | 说明 |
| --- | --- |
| 数据落盘位置 | 只在本机 `mcdtm-output\`，不上传任何服务器 |
| 外发行为 | 无。唯一的外部连接是官方 `https://mcp.mcd.cn` |
| 写操作 | 无。不调用下单、领券、抽奖类接口 |
| Token 存储 | 只从环境变量读，不写进任何项目文件 |
| `.gitignore` 已排除 | `mcdtm-output/`、`.env`、`*token*.txt` 等 |
| 不要公开的文件 | `mcdtm-output\raw\`、`mcdtm-output\orders.normalized.json` |
| 建议公开前自查 | `mcdtm-output\share.json`（已脱敏，但仍建议人工通读） |
| 绝对不要公开 | `mcdtm-output\raw\`、`doctor.json`、`orders.normalized.json` |

跑完后想彻底清理：

```powershell
Remove-Item -Recurse -Force mcdtm-output
```

---

## 附：完整命令速查

```powershell
# 进入项目
cd C:\Users\AIIUNIE\WorkBuddy\2026-10-15-23-26-06\mcd-time-machine

# 设置 Token（新开窗口后验证）
$env:MCD_MCP_TOKEN = '你的Token'

# 先看效果，不需要 Token
python -m mcd_timemachine demo

# 诊断真实连通性
python -m mcd_timemachine doctor

# 看可用工具
python -m mcd_timemachine tools

# 拉真实订单
python -m mcd_timemachine fetch

# 生成报告
python -m mcd_timemachine analyze

# 一步到位
python -m mcd_timemachine all

# 看原始返回（排查用，含隐私）
python -m mcd_timemachine inspect orders

# 导出脱敏结构（分享前仍需人工检查）
python -m mcd_timemachine share

# 打开报告
start mcdtm-output\story.html    :: V2.0 沉浸式故事页（手机竖屏 / 可导出分享卡片）
start mcdtm-output\report.html   :: 完整数据报告
```

如果装了 `pipx` 或用 `pip install -e .`，也可以用更短的 `mcdtm` 命令，
上面所有 `python -m mcd_timemachine` 都可直接替换成 `mcdtm`。
