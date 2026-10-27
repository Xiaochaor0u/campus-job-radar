# 岗位情报站 · RecruitOps

> 帮你盯着几百家公司的招聘官网，一有新岗位就告诉你。
> **拉下来自带 22444 个岗位**，不用先学爬虫，装好就能看。

**解决的痛点**：秋招、春招、实习的招聘公告发布得很零散。你不知道哪家公司今天开了新批次，只能反复刷自己记得的那几个官网，或者靠群里、短视频里的截图。这个工具把这些事自动化：每天定时去官网看一遍，把"新增了什么岗位"整理成一张清单给你。

---

## 目录

- [它到底能做什么](#它到底能做什么)
- [第 0 步：装 Python（只需一次）](#第-0-步装-python只需一次)
- [第 1 步：下载这个项目](#第-1-步下载这个项目)
- [第 2 步：打开命令行](#第-2-步打开命令行)
- [第 3 步：一键启动](#第-3-步一键启动)
- [第 4 步：看懂工作台](#第-4-步看懂工作台)
- [第 5 步：让它每天自动更新（可选）](#第-5-步让它每天自动更新可选)
- [第 6 步：推送到微信/QQ 群（可选）](#第-6-步推送到微信qq-群可选)
- [常见问题（照着做就行）](#常见问题照着做就行)
- [进阶用法（不写代码也能做）](#进阶用法不写代码也能做)
- [技术说明（给开发者）](#技术说明给开发者)
- [合规与数据](#合规与数据)

---

## 它到底能做什么

装好之后，打开浏览器你会看到三块东西：

| 页面 | 你能看到什么 |
| --- | --- |
| **今日新发布** | 今天哪些公司放了新岗位，按公司分组，点开就是官网链接 |
| **全部岗位** | 按校招 / 实习、按来源筛选，支持搜索公司名、岗位名、城市 |
| **实习岗位** | 专门的实习岗列表（含招募条件、能不能投的分析） |
| **智能推荐** | 粘贴简历 → 按你的方向/技能给全部岗位打分排序，不需要 API Key |
| **每日新增** | 只看今天新出来的岗位，按关键词 / 方向 / 城市 / 校招实习筛选，条件可存成订阅 |
| **岗位画像** | 在列表里点某一条岗位右侧的「分析 / 画像」，现场看它的准入门槛、薪资原文、同公司同方向岗位数 |

数据规模（自带，不需要你先爬）：

| 项目 | 数量 |
| --- | --- |
| 岗位 | 22444 个 |
| 覆盖公司 | 12488 家 |
| 校招 | 20149 个 |
| 实习 | 2295 个 |

这些数据打包在项目里（`data/seed/jobs.jsonl.gz`，3 MB），所以你拉下来就有内容。

---

## 第 0 步：装 Python（只需一次）

这个项目用 Python 写的，需要先装它。**装过的可以跳过。**

### Windows

1. 打开 <https://www.python.org/downloads/>，点黄色大按钮下载 Python 3.11 或更新版本。
2. 双击安装包。**安装界面第一页最下面有个勾选框 `Add python.exe to PATH`，一定要勾上。** 不勾的话后面所有命令都会报错。
3. 点 `Install Now`，等进度条走完，点 `Close`。
4. 验证：按 `Win + R`，输入 `cmd` 回车，在黑窗口里输入：

   ```bat
   python --version
   ```

   看到 `Python 3.11.x` 或更高就成功了。

### macOS

1. 打开 <https://www.python.org/downloads/> 下载 macOS 安装包，双击一路下一步。
2. 验证：打开「终端」（启动台搜 Terminal），输入：

   ```bash
   python3 --version
   ```

---

## 第 1 步：下载这个项目

### 方法 A：直接下载（不用装 Git）

1. 打开本项目的 GitHub 页面。
2. 点绿色按钮 `Code` → `Download ZIP`。
3. 下载完成后，**解压到桌面**（不要在压缩包里直接运行）。
4. 会得到一个文件夹，名字类似 `recruitops-agent-main`，可以改名为 `recruitops-agent`。

> 路径里最好不要有空格，放在桌面或 `C:\recruitops-agent` 都行。

### 方法 B：用 Git

```bash
git clone https://github.com/Xiaochaor0u/recruitops-agent.git
```

---

## 第 2 步：打开命令行

命令行就是个能敲命令的黑窗口，不用怕——你只需要复制粘贴。

### Windows

**最简单**：打开解压出来的文件夹，在文件夹空白处**按住 Shift + 鼠标右键**，选「在终端中打开」或「在此处打开 PowerShell 窗口」。

如果没看到这个选项：按 `Win + R`，输入 `powershell` 回车，然后输入（路径换成你自己的）：

```powershell
cd "C:\Users\你的用户名\Desktop\recruitops-agent"
```

> 小技巧：输入 `cd ` 之后，把文件夹直接拖进窗口，路径会自动填上，不用手打。

### macOS

打开「终端」，输入 `cd `（后面有空格），把文件夹拖进终端窗口，回车。

---

## 第 3 步：一键启动

**把下面 4 行依次复制粘贴到命令行，每贴一行按一次回车，等它跑完再贴下一行。**

### Windows（PowerShell）

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\Activate.ps1
```

```powershell
pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

```powershell
python scripts\quickstart.py
```

### macOS / Linux

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

```bash
python scripts/quickstart.py
```

### 每一步在干什么

| 命令 | 作用 | 大概耗时 |
| --- | --- | --- |
| `python -m venv .venv` | 建一个「沙盒」，依赖装在项目里，不污染系统 | 5 秒 |
| `Activate.ps1` / `source` | 进入沙盒（成功后命令行最前面会出现 `(.venv)`） | 1 秒 |
| `pip install -e ".[dev]"` | 下载项目依赖（约 100 MB） | 2~5 分钟 |
| `python scripts\quickstart.py` | 导入自带岗位 + 启动网站 | 30 秒 |

最后一行跑完会长这样：

```text
[2/3] 导入自带岗位数据：jobs.jsonl.gz
已导入 22444 个岗位 · 12488 家公司（已存在跳过 0）
[3/3] 当前库中岗位：22444 个
启动工作台：http://127.0.0.1:8010/?view=intelligence（Ctrl+C 停止）
```

浏览器打开 <http://127.0.0.1:8010/?view=intelligence> 就能用了。

> **这个窗口不要关**，关掉网站就停了。停止服务按 `Ctrl + C`。
> 下次想再打开：进项目文件夹 → 激活沙盒 → 跑最后那行命令。

---

## 第 4 步：看懂工作台

左侧是导航，三块常用功能：

### ⓪ 手动采集（先把数据拉进来）

「今日新发布」里的数据来自采集：**你不开采集，它就不会自己更新**。两种更新方式：

- **手动（推荐新手）**：打开「今日新发布」页，顶部有「手动采集」面板——
  1. 勾选你要的行业范围（比如计算机专业的只勾「互联网/软件/AI」「半导体/电子硬件」，金融、地产的公告就不会进来）
  2. 勾选招聘类型（实习 / 秋招 / 春招）
  3. 点「开始采集」。全量约 3–6 分钟，页面会显示进度，采完自动刷新
- **自动（可选）**：跑一次 `scripts/install_windows_tasks.ps1`，之后每天自动采集，不用手动点

范围会记住：你选过的行业范围会保存下来，下次打开还是你的选择。已经入库的岗位不受缩小范围的影响。

### ① 今日新发布

- 顶部四个数字：新发布 / 内容变更 / 已下架 / 有变化的企业
- 下面按公司分组，每条前面有「新增」「变更」「下架」标签
- **点岗位名**直接跳官网原页，可以立刻投
- 右上角切换「最近 24 小时 / 3 天 / 7 天 / 30 天」

> 第一次用可能显示「还没有岗位变化记录」，这正常：第一次采集只是把现存岗位记成「底账」，第二天起才记录真正的「新增」。

### ② 全部岗位

- 所有已收录的岗位（不只是今天新增的）
- 可筛选：校招 / 实习、招聘公告 / 官网采集
- 搜索框能搜公司名、岗位名、城市

### ③ 实习岗位

- 专门看实习岗，包含「这个实习要求什么条件」「你差在哪」
- 上传简历后才有匹配分数（可选，不上传也能看岗位）

### ④ 智能推荐（按你的简历排序）

「今日新发布」页顶部有三个标签：**岗位变化 / 全部岗位 / 智能推荐**。

点「智能推荐」，第一次会提示没有偏好信息。展开下面那行「没有模型密钥？把简历内容粘贴到这里」：

1. 把简历里的**教育背景、技能、项目、实习经历**粘进文本框（不用上传文件）；
2. 点「提取关键词」——这一步**不联网、不需要 API Key**，只用本地词表识别；
3. 看看识别出的方向、技能对不对，点「保存并推荐」；
4. 下面立刻按匹配度排出岗位，每条会写明加分原因，比如 `方向命中 大模型/智能体 +35`。

打分的四个维度（总分 100）：方向匹配 35 + 关键词匹配 30 + 技能匹配 20 + 招聘类型 15。分数用来**排序**，不是录用概率。

> 也可以在「配置」页上传 PDF/Word 简历做更细的解析，但那一步需要配置 DeepSeek API Key。没有 Key 就用上面粘贴的方式，效果对排序够用。

### ⑤ 每日新增（只看今天新出来的）

点「每日新增」标签，回答的是"**今天新出现了哪些岗位，且符合我的条件**"。

用法有两种：

- **偶尔看一次**：填关键词/方向/城市/类型，点「筛选新增岗位」，看完就走，不保存；
- **长期关注**：同样填好条件，再填个订阅名称（比如"大模型实习-北京"），点「保存为订阅」。以后打开这个标签，点顶部那颗胶囊就自动跑一遍。

时间窗口默认「今天」，想放宽就切「最近 3 天 / 7 天 / 30 天」。

页面顶部会显示「数据最后更新：X 分钟前」——**筛选本身不用等（毫秒级）**，真正要等的是每天 08:00 / 08:30 的采集任务。如果显示 3 小时前，说明该等采集，不是该等筛选。

小提示：填「算法」会顺带匹配「机器学习」「深度学习」这类写法，否则标题写「Agent 研发」的岗位永远搜不到。靠近义词命中的会标注「关键词近义」，排在精确命中的后面。

### ⑥ 岗位画像（想投之前先看清这个岗）

在「全部岗位」表里，每行最右边有个「分析」按钮；「智能推荐」列表里每行有个「画像」按钮。**点哪条就分析哪条**，不会预先算全库。

展开后你会看到：

| 内容 | 例子 |
| --- | --- |
| 薪资 | 「公告未披露」或「15-20万」（只转述公告原文） |
| 门槛 | 学历本科、要求 2 年自动化测试经验，每条都附上公告原句 |
| 相对位置 | 该公司在库 52 个岗位，同方向 0 个，合肥占 31 个 |
| 判定 | 要求偏高 / 中等 / 较低 / 信息不足，下面是逐条依据 |
| 模型解读 | 2~3 句人话（配了 API Key 才有） |

**先说清三个真实数字**，免得你以为它坏掉了：

| 项目 | 能查到的比例 |
| --- | --- |
| 公告里写了薪资 | **约 0.4%**（绝大多数招聘公告不写钱） |
| 公告里写了学历/院校/经验 | **约 7%** |
| 同公司同方向岗位统计 | 100%（本地库算的） |

所以多数岗位点开是「薪资未披露 + 门槛信息不足」，但**相对位置和解读仍然有效**——"这家公司这个方向只放了 1 个岗"本身就是有用信息。

**为什么不给"往年录了什么学生"**：没有公开数据能支持这种结论。工具只报公告写明的条件，并且明确标注这是"公告门槛"不是"实际录取水平"。

### ⑦ 「高匹配」为什么是 0（不是坏了）

「27届校招」页那个「高匹配 0」不是 bug，是新库还没评过分。评分要三个条件同时成立：

1. 岗位有真正的 JD 正文（公告级岗位大多只有岗位名列表）
2. 岗位标题命中你简历里的目标方向（否则判「方向不符」，直接不给分）
3. 配置页的模型密钥 + 岗位评分开关都打开

想让它出数字，跑这一条（不重爬，只补评分）：

```bash
python scripts/score_pending.py --dry-run      # 先看有多少条能评
python scripts/score_pending.py --concurrency 4
python scripts/score_pending.py --rescore       # 换过简历后，先清旧分再重评
```

**关键**：评分里的「项目证据」占 25 分，只能从上传简历解析出来。只粘关键词的话分数会卡在 60 出头——实测同一条岗位，画像里没有项目证据是 64 分，把你简历里的项目写进去是 86 分。

**高匹配多少取决于你的画像写多宽**：画像只写"大模型"，硬件、结构、测试岗会全部被过滤掉。22444 条里，官网采集的 135 条中标题能命中方向的只有 61 条。

### ⑧ 公司名旁边的「民营 / 国资 / 外资」

「公司」和「全部岗位」里公司名后面有个小标签。它来自招聘公告自带的性质字段（覆盖约 44%），没有的用 `config/company_nature.yaml` 补——那是个纯文本文件，你不同意哪家直接改，刷新页面就生效。

只做粗分类是有意的：混合所有制、国资参股但民营团队经营的公司，不同口径答案不同，标太细反而更容易错。

另外「公司」页只列**已接入官网采集**的几十家，不是全部 6553 家——后者的公司在「全部岗位」里按公司名搜索。两个页面口径不同，不是数据丢了。

---

## 第 5 步：让它每天自动更新（可选）

不做也能用，只是岗位清单不会自己变新。

### Windows

在项目文件夹里打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows_tasks.ps1 -DryRun
```

这一步**只是预览**，会列出将要创建的 6 个计划任务，不会真的创建。确认后去掉 `-DryRun` 再跑一次：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows_tasks.ps1
```

之后每天自动执行：

| 时间 | 做什么 |
| --- | --- |
| 08:00 | 抓配置企业的官网岗位 |
| 08:15 | 检查爬虫是否正常 |
| 08:30 | 同步最新招聘公告（数量最多） |
| 09:00 | 同步招聘邮箱（不用可不配） |
| 09:30 | 把新岗位推到群里（需先做第 6 步） |
| 20:00 | 复核投递进度（不用可不配） |

想取消：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall_windows_tasks.ps1
```

### macOS / Linux

```bash
crontab -e
```

粘贴下面几行（路径换成你的），保存退出：

```cron
0 8  * * * cd /你的路径/recruitops-agent && .venv/bin/python scripts/run_local_task.py --task daily_recruitment_intelligence
30 8 * * * cd /你的路径/recruitops-agent && .venv/bin/python scripts/run_local_task.py --task offerbiu_announcements
30 9 * * * cd /你的路径/recruitops-agent && .venv/bin/python scripts/run_local_task.py --task daily_intelligence_digest
```

---

## 第 6 步：推送到微信/QQ 群（可选）

宿舍群、班级群一起用：每天 09:30 自动发「今天哪些公司放岗了」。

### 1. 拿一个群机器人地址

| 群类型 | 怎么拿地址 |
| --- | --- |
| 企业微信群 | 群右上角 → 群机器人 → 添加机器人 → 复制 Webhook 地址 |
| 钉钉群 | 群设置 → 智能群助手 → 添加机器人 → 自定义 → 复制 Webhook |
| 飞书群 | 群设置 → 群机器人 → 添加机器人 → 自定义机器人 → 复制地址 |
| QQ 群 | 需自建转发服务（OneBot 等），地址填到 `generic` 类型 |

### 2. 填配置

打开 `config` 文件夹，把 `notify.example.yaml` **复制一份改名**为 `notify.yaml`，用记事本打开填地址：

```yaml
webhooks:
  - name: 宿舍求职群
    type: wecom
    url: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的KEY

digest:
  hours: 24
  max_items_per_push: 8
```

`type` 填 `wecom`（企业微信）/ `dingtalk`（钉钉）/ `feishu`（飞书）/ `generic`（通用）。
钉钉、飞书如果开了加签，再加一行 `secret: 你的密钥`。

### 3. 先预览再发送

```powershell
python scripts\intelligence_digest.py
```

屏幕上会打印「将要发送的内容」。确认后加 `--push` 真发：

```powershell
python scripts\intelligence_digest.py --push
```

---

## 常见问题（照着做就行）

### 1. 提示「'python' 不是内部或外部命令」

装 Python 时没勾 `Add python.exe to PATH`。最省事：卸载重装，**记得勾那个框**。

### 2. PowerShell 提示「无法加载文件 Activate.ps1，因为在此系统上禁止运行脚本」

先运行这行，再重做第 3 步：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 3. `pip install` 特别慢或卡住

命令里已经带了清华源。还慢就换阿里源：

```powershell
pip install -e ".[dev]" -i https://mirrors.aliyun.com/pypi/simple/
```

### 4. 页面中文乱码

在 PowerShell 里先运行 `chcp 65001` 再启动。

### 5. 提示端口 8010 被占用

换个端口：

```powershell
python scripts\quickstart.py --port 8020
```

然后访问 <http://127.0.0.1:8020/?view=intelligence>。

### 6. 页面能开，但「岗位变化」是空的

正常。第一次采集只建「底账」，不算新发布。想立刻有内容：

- 点「全部岗位」看已有的 2 万多个岗位；
- 或等第二天定时任务跑完再看。

### 7. 想立刻看最新岗位，不想等定时任务

打开「今日新发布」页 → 顶部「手动采集」面板 → 选范围 → 点「开始采集」，采完自动刷新。

也想在命令行跑：`python scripts\collect_offerbiu.py --types 实习 秋招 春招`（`--max-pages` 要给足，秋招有一万三千多条，默认给 300）。

### 8. 只想要互联网岗位，不想要律所、医院、学校

手动采集面板里只勾你要的行业（比如「互联网/软件/AI」「半导体/电子硬件」），范围外的公告直接不入库。

已经入库的岗位可以用「全部岗位」的搜索框临时筛。

> 注意：缩小范围只是不再往里加，原来已经进库的岗位不会被删。

### 9. 数据存在哪？会上传到网上吗？

全部存在你电脑上的项目文件夹（`.data` 目录里的数据库），**不上传任何服务器**。想清空：停服务 → 删 `.data` 文件夹 → 重跑 `quickstart.py`。

### 10. 想卸载

删掉整个项目文件夹即可，不留系统级残留。

---

## 进阶用法（不写代码也能做）

### A. 增删要监控的公司

打开 `config/companies.yaml`（没有就把 `companies.example.yaml` 复制改名），每家公司一段：

```yaml
  - id: tencent
    name: 腾讯
    careers_url: https://join.qq.com/
    crawler: tencent
    integration_status: connected
```

- `integration_status` 写 `connected` 才会抓，写 `pending_verification` 只登记不抓
- 不想要哪家，把 `connected` 改成 `pending_verification`
- 想加新公司，复制一段，改 `id`、`name`、`careers_url`、`crawler`

**`crawler` 怎么选？看招聘页网址就知道：**

| 招聘页长得像 | 填什么 |
| --- | --- |
| `xxx.zhiye.com` | `beisen` |
| `xxx.hotjob.cn` | `hotjob` |
| `app.mokahr.com/campus-recruitment/xxx` | `moka` |
| `xxx.jobs.feishu.cn` | `feishu` |
| 其他（打开空白、要等几秒才出内容） | `render` |

### B. 多拉一些岗位

```powershell
# 实习全量
python scripts\collect_offerbiu.py --types 实习 --max-pages 60

# 秋招 + 春招
python scripts\collect_offerbiu.py --types 秋招 春招 --max-pages 150
```

### C. 更新打包的数据（分享给别人的时候）

```powershell
python scripts\export_seed.py
```

会重新生成 `data/seed/jobs.jsonl.gz`，提交到 GitHub 后，别人拉下来就自带最新岗位。

---

## 技术说明（给开发者）

<details>
<summary>点展开</summary>

### 架构

- **采集层**：Requests / Playwright / BeautifulSoup，79 个站点适配器（57 个真实实现，18 个占位）
- **应用层**：FastAPI + 原生 HTML/CSS/JS（无前端构建）
- **情报层**：`packages/intelligence/` —— 事件写入、日报聚合、群机器人推送、OfferBiu 公告导入
- **调度层**：本地任务调度器 + Windows 计划任务 / cron，6 个固定任务
- **数据层**：SQLAlchemy 2.0，SQLite（默认）/ PostgreSQL 16，迁移在 `migrations/`

### 数据流

```
企业官网 / OfferBiu 公告
        ↓  定时任务
   job_snapshots（岗位快照）
        ↓  与上一轮对比
   job_events（新增/变更/下架事件）
        ↓
   工作台「今日新发布」 + 群机器人日报
```

设计约定：

- **基线机制**：每家公司第一次采集只记底账，不产生大量「新发布」噪音
- **事件与采集解耦**：事件写失败不影响已提交的岗位数据
- **口径分离**：`cohort` 未知的岗位照样入库、照样进「今日新发布」，只是不进上游严格的「27 届校招」视图

### 主要脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/quickstart.py` | 一键：准备配置 + 导入种子 + 启动 |
| `scripts/collect_offerbiu.py` | 拉招聘公告（`--dry-run` 只统计） |
| `scripts/export_seed.py` / `import_seed.py` | 导出 / 导入岗位快照 |
| `scripts/intelligence_digest.py` | 预览或推送日报（`--push` 真发） |
| `scripts/run_local_task.py` | 手动触发某个定时任务 |
| `scripts/install_windows_tasks.ps1` | 注册 Windows 计划任务 |

### API

| 端点 | 用途 |
| --- | --- |
| `GET /api/intelligence/digest?hours=24&track=internship` | 按企业分组的变化汇总 |
| `GET /api/intelligence/events?event_type=new` | 扁平事件流 |
| `GET /api/intelligence/jobs?track=campus&page=1` | 分页浏览全部岗位 |
| `GET /api/intelligence/coverage` | 已入库 / 在招 / 校招口径统计 |
| `GET /api/intelligence/push-preview` | 渲染将要推送的原文 |

### 测试

```bash
python -m pytest tests/test_intelligence.py tests/test_intelligence_api.py tests/test_scheduler.py
```

</details>

---

## 相对上游做了什么

本仓库是 [RecruitOps](https://github.com/849879772/recruitops-agent)（MIT）的二次开发：

- **第一阶段 · InternOps**：实习轨道分类、条件抽取与评估（见 [docs/internops/README.md](docs/internops/README.md)）
- **第二阶段 · 岗位情报站**：定时采集打通、新岗位事件识别、今日新发布看板、群机器人推送、OfferBiu 公告批量导入、自带岗位快照、保姆级文档
- **未改动**：上游爬虫实现、匹配评分、邮件、投递管理、桌面壳

更多细节见 [docs/INTELLIGENCE.md](docs/INTELLIGENCE.md)。

---

## 合规与数据

- 只访问公开可访问的招聘页面，默认低频（每天 1~2 次），不绕过登录 / 验证码 / robots 限制。
- 打包的数据只含**元数据**（公司、岗位名、城市、届别、截止时间、投递链接），**不含 JD 正文**。投递时点链接去官网看原文。
- 数据只存本机，不上传任何服务器。
- 对外提供服务前请自行确认站点条款并加鉴权。

安全问题请见 [SECURITY.md](SECURITY.md)。

## License

[MIT](LICENSE)。上游 [RecruitOps](https://github.com/849879772/recruitops-agent) 版权归原作者，二次开发部分同样以 MIT 发布。
