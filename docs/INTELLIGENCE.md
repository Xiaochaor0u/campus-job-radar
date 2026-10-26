# 岗位情报站使用说明

面向"秋招 / 春招 / 日常实习信息差"的本地岗位监控：定时采集你关注的企业官网，识别**新发布**的岗位，在本地工作台按时间线展示，并可用群机器人推送到班级 / 宿舍求职群。

---

## 1. 它解决什么问题

企业发招聘公告的时间各不相同，学生只能：

- 盯着自己知道的那几家官网反复刷新；
- 在短视频、社交平台上偶然刷到别人转的岗位截图；
- 加了很多群，但信息是碎片化的、过期的。

这个工具把"盯官网"变成自动化的日常任务：

```
企业官网（config/companies.yaml 里配置）
        │  每天定时采集（Windows 计划任务 / cron）
        ▼
岗位快照对比 → 新增 / 变更 / 下架事件（job_events 表）
        │
        ├──▶ 本地工作台「今日新发布」时间线
        └──▶ 群机器人日报（企业微信 / 钉钉 / 飞书 / 通用 webhook）
```

---

## 2. 三步上手

### 2.1 一条命令起步

```bash
git clone <你的仓库地址>
cd recruitops-agent

python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

python scripts/quickstart.py
```

`quickstart.py` 依次做三件事：

1. 没有 `config/companies.yaml` 就从示例复制一份；
2. 库为空时导入仓库自带的岗位快照（4655 条 / 3072 家公司），**不需要先爬就能看到内容**；
3. 启动工作台并提示访问地址。

可选开关：

| 参数 | 作用 |
| --- | --- |
| `--no-seed` | 不导入自带数据，从空库开始 |
| `--no-serve` | 只准备数据，不启动服务 |
| `--collect` | 启动前先跑一次企业官网采集（较慢，但能拿到 JD 正文） |
| `--offerbiu` | 启动前先拉最新招聘公告 |
| `--port 8020` | 换端口 |

### 2.2 分步执行（想自己控制每一步时）

```bash
cp config/companies.example.yaml config/companies.yaml      # ① 配置要监控的企业
cp config/notify.example.yaml config/notify.yaml            # ② 配置群机器人（可跳过）

python scripts/import_seed.py                                # 导入自带岗位（可跳过）
python scripts/collect_offerbiu.py --types 实习 秋招 --max-pages 50   # 拉最新公告
python scripts/run_local_task.py --task daily_recruitment_intelligence  # 爬配置企业的官网

uvicorn apps.api.main:app --port 8010
# 浏览器打开 http://127.0.0.1:8010/ ，左侧「今日新发布」
```

> 用 PostgreSQL 时需要先跑迁移：`python scripts/apply_migrations.py`。
> 用默认 SQLite 时不需要任何迁移，首次运行会自动建表。

### 2.3 设成每天自动跑

```powershell
# Windows：注册 6 个每日计划任务（采集 / 健康检查 / 公告同步 / 邮箱 / 日报推送 / 投递复核）
.\scripts\install_windows_tasks.ps1 -DryRun   # 先看会创建什么
.\scripts\install_windows_tasks.ps1
```

```bash
# Linux / macOS：crontab -e
0 8  * * * cd /path/to/recruitops-agent && .venv/bin/python scripts/run_local_task.py --task daily_recruitment_intelligence
30 8 * * * cd /path/to/recruitops-agent && .venv/bin/python scripts/run_local_task.py --task offerbiu_announcements
30 9 * * * cd /path/to/recruitops-agent && .venv/bin/python scripts/run_local_task.py --task daily_intelligence_digest
```

每日流程跑完后，工作台「岗位变化」里出现的就是**当天真正新增的岗位**，群机器人也会在 09:30 推一份摘要。

---

## 3. 第一次运行为什么"没有新岗位"

这是设计如此，不是 bug。

第一次采集一家企业时，官网上的所有在招岗位对你来说都是"从没见过"的——如果全部算作"新发布"，第一天就会被几百条岗位淹没，而且这些岗位可能已经挂了三个月。

所以系统给每家企业写一条 **baseline（基线）** 记录，把首次采集的岗位当作"已知存量"，**不产生新发布事件**。从第二次采集开始：

- 官网上出现、库里没有的岗位 → `new`（新发布）
- 已在库、但标题 / JD / 城市等指纹变化 → `updated`（变更）
- 库里有、连续两次完整采集都没再出现（且有 3 天宽限期）→ `removed`（下架）

也就是说：**装好后的第二天，你在「今日新发布」里看到的都是真实的新岗位。**

---

## 4. 配置要监控的企业

`config/companies.yaml` 决定采哪些企业。每家一条：

```yaml
companies:
  - id: acme-tech              # 唯一标识，全库主键，不要随意改
    name: Acme 科技             # 展示名称
    careers_url: https://acme.example.com/campus   # 校招 / 实习入口页
    crawler: render            # 用哪个采集适配器，见下表
    integration_status: connected   # connected 才会被采集
    industries: [internet]
    recruitment_types: [campus]     # campus / internship
    recruitment_targets: ["2027"]
```

**已内置的采集适配器（`crawler` 字段）：**

仓库自带 79 个适配器 key，但**其中 18 个是返回空列表的占位实现**（大华、商汤、地平线、Momenta、PonyAI、文远知行、蔚来、小鹏、宇树之外的机器人公司等），配置进去只会白跑。真正可用的是 57 个，分三类：

| 类型 | 说明 | 代表 key |
| --- | --- | --- |
| 公司专用（真实实现） | 直连该公司公开 JSON API，最快最稳 | `tencent` `bytedance` `meituan` `jd` `pdd` `bilibili` `kuaishou` `mihoyo` `xiaomi` `oppo` `huawei` `dji` `hikvision` `unitree` `byd` `lixiang` `sf` `boe` `cvte` `tplink` `fanruan` `tonghuashun` `duoyi` `gbits` `job4399` `ourpalm` |
| 招聘 SaaS 通用 | 一家适配器覆盖一批同平台公司，**中厂主力** | `beisen`（`*.zhiye.com`）`hotjob`（`*.hotjob.cn`）`moka`（`app.mokahr.com`）`feishu`（`jobs.*.feishu.cn`）`ourats` `moseeker` `beisen_mobile` |
| 通用兜底 | 自建站，需浏览器渲染或静态解析 | `render`（SPA 自动渲染）`static_html`（静态列表页） |

完整列表见 `packages/recruitment_core/crawlers/__init__.py` 的 `CRAWLER_MAP`。

### 4.1 怎么判断一家公司用哪个适配器

**看招聘页的域名就知道**，不需要读代码：

| 招聘页长这样 | 用这个 crawler |
| --- | --- |
| `https://xxx.zhiye.com/` | `beisen` |
| `https://xxx.hotjob.cn/SUxxxx/pb/account.html` | `hotjob` |
| `https://app.mokahr.com/campus-recruitment/xxx/xxxxx` | `moka` |
| `https://xxx.jobs.feishu.cn/campus` | `feishu` |
| 公司自建域名 + 页面打开是空壳、内容靠 JS 渲染 | `render` |
| 公司自建域名 + HTML 里直接能看到岗位列表 | `static_html` |

先把 `integration_status` 写成 `pending_verification`，验证通过再改 `connected`。

### 4.2 中小厂实测结果（2026-10-26）

用北森（`beisen`）通用适配器实测了 5 家中厂，**API 直接返回真实 2027 届校招岗位，无需写代码**：

| 公司 | 招聘页 | 在招岗位 |
| --- | --- | --- |
| 科大讯飞 | `iflytek.zhiye.com` | 120 |
| 传音控股 | `transsion.zhiye.com` | 177 |
| 新华三 | `h3c.zhiye.com` | 79 |
| 大华股份 | `dahua.zhiye.com` | 137 |
| 贝壳找房 | `ke.zhiye.com` | 29 |

一次完整采集入库 135 条（含 JD 正文），其中 38 条标题写明「2027届」的进入 27 届校招视图。

Moka / 飞书系的入口也已探明但需浏览器渲染，示例配置里标为 `pending_verification`：货拉拉、知乎、唯品会（Moka），得物、小鹏汽车（飞书）。

> 想继续扩中厂：找一个可能是北森租户的公司，试 `<公司英文名>.zhiye.com`，能打开招聘页就说明可以配 `beisen`。这是目前命中率最高的扩量方式。

### 4.3 用 OfferBiu 公告快速扩量（推荐第一步）

OfferBiu 是上游的公司发现源，但它同时是一个**内容源**：公开接口按公司提供招聘公告，含招聘类型（秋招 / 春招 / 实习）、面向届别、城市、岗位方向、截止时间、投递链接。实测全站 **24342 条公告**：

| 筛选 | 数量 |
| --- | --- |
| 全部公告 | 24342 |
| 实习 | 2298 |
| 秋招 | 13129 |
| 春招 | 7178 |
| 含「互联网」行业 | 2272 |

一条命令导入（不需要任何 API Key）：

```bash
# 实习全量（约 2250 条，1390 家公司）
python scripts/collect_offerbiu.py --types 实习 --max-pages 50

# 互联网方向秋招（约 1270 条）
python scripts/collect_offerbiu.py --types 秋招 --industry 互联网 --max-pages 50

# 只统计不写库
python scripts/collect_offerbiu.py --types 春招 --dry-run
```

导入后每条公告变成一条岗位记录（`source=offerbiu`），公司写入 `company_snapshots`，所以「今日新发布」「全部岗位」「群机器人推送」全部可用：

- `positionsText` 作为岗位名（可能并列多个方向，例如「算法实习生、产品实习生」）；
- `targetYears` 作为届别，`cohort_status=confirmed`（公告自述，属于证据）；
- `recruitType` 决定轨道：含「实习」→ 实习，含「秋招 / 春招」→ 校招；
- `applyUrl` 作为投递链接，`deadlineText` 显示在列表的「截止」列。

首轮导入同样走基线机制（不刷屏），但**已有的岗位可以在「全部岗位」标签页立即看到**，按校招 / 实习、来源、关键词筛选。

### 4.4 两条路怎么配合

| 需求 | 用哪条 |
| --- | --- |
| 岗位数量要够多、覆盖中小厂 | OfferBiu 公告导入（几千条，一条命令） |
| 要看到某家公司的**逐条具体岗位 + 完整 JD** | `companies.yaml` 企业官网采集 |
| 要看「今天哪家公司开了新批次」 | OfferBiu 公告（公告是批次级别的） |
| 要做简历匹配打分 | 企业官网采集（有 JD 正文才能评） |

建议两个都跑：OfferBiu 保证广度，官网采集保证深度。

### 4.5 为什么有些岗位不在「27届校招」视图里

上游的校招视图有严格口径：`cohort == 2027` **且** `cohort_status == confirmed`（届别已确认）。本仓库补了两条归一化规则（`packages/pipeline/daily.py` 的 `_observed_job`）：

1. 适配器返回 `cohort=0` / 空值 → 视为「无届别证据」，不会被当成有效届别存进去；
2. 岗位标题里明写「2027届」「2027年毕业」→ 提取为 `cohort=2027`、`confirmed`，并把匹配到的原文记进 `cohort_evidence`。

所以标题没写届别的岗位（如「AI训练师(业务方向)(J72942)」）会诚实保持未知：**它们会入库、会出现在「今日新发布」、能参与匹配评分，只是不进 27 届校招视图**。工作台的覆盖统计条会同时显示「已入库 / 在招 / 可进校招视图」三个数，避免误以为抓失败了。

想核对全量抓取情况：`GET /api/intelligence/coverage`。

`integration_status` 写 `pending_verification` 的企业会被保留但跳过采集，适合先登记、后补适配器。

---

## 5. 配置群机器人

复制 `config/notify.example.yaml` 为 `config/notify.yaml`。没有这个文件时推送任务返回 `disabled`，工作台照常可用。

```yaml
webhooks:
  - name: 宿舍求职群
    type: wecom
    url: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=你的KEY
digest:
  hours: 24
  max_items_per_push: 8
```

| type | 在哪里拿 webhook | 额外字段 |
| --- | --- | --- |
| `wecom` | 企业微信群 → 群设置 → 群机器人 → 添加 | — |
| `dingtalk` | 钉钉群 → 群设置 → 智能群助手 → 自定义 | `secret`（安全设置选"加签"时填） |
| `feishu` | 飞书群 → 设置 → 群机器人 → 自定义机器人 | `secret`（开启签名校验时填） |
| `generic` | 任何接收 JSON 的地址，消息放在 `message` 字段 | — |

`generic` 用来接 QQ 群：用 OneBot 协议的自建桥（如 go-cqhttp 的 `/send_group_msg`）或自己写个转发服务即可。

先看效果再决定推不推（不会真的发送）：

```bash
python scripts/intelligence_digest.py            # 打印最近 24 小时的变化
python scripts/intelligence_digest.py --push     # 真的推送到配置的群
```

---

## 6. 工作台

左侧 **今日新发布**：

- 顶部选统计窗口（24 小时 / 3 天 / 7 天 / 30 天）和岗位类型（校招 / 实习）；
- 每家企业一张卡片，标注「新增 N / 变更 N / 下架 N」；
- 每条岗位可点开官网原文。

原有的「27 届校招」「实习岗位」「投递记录」「招聘邮箱」「定时任务」等视图全部保留不变。

### 6.1 智能推荐（按简历排序）

「今日新发布」页有三个标签：**岗位变化 / 全部岗位 / 智能推荐**。

智能推荐回答的是"这两万多个岗位里，哪几个值得我点开"：

1. 在标签页里展开「没有模型密钥？把简历内容粘贴到这里」，把简历的教育背景 / 技能 / 项目 / 实习经历粘进去；
2. 点「提取关键词」，系统用内置词表识别技能、方向、学历、毕业年份（纯本地规则，不联网、不调用模型）；
3. 核对识别结果，点「保存并推荐」；
4. 下面立刻按匹配度排出岗位，每条都标出加分原因（例：`方向命中 大模型/智能体 +35`）。

也可以走「配置」页上传简历（PDF / TXT / MD），那条路径会用模型做更细的结构化解析，需要配置 DeepSeek API Key。

**打分规则（规则版，总分 100）**

| 维度 | 分值 | 说明 |
| --- | --- | --- |
| 方向匹配 | 35 | 简历里的目标方向（如「大模型与智能体」）出现在岗位标题或公告方向里 |
| 关键词匹配 | 30 | 岗位标题关键词命中率 |
| 技能匹配 | 20 | 技能名出现在岗位描述里 |
| 招聘类型 | 15 | 找校招 vs 找实习与岗位轨道一致（仅在已命中其他项时计入，避免所有岗位都有保底分） |

同一公司 + 同一岗位文字的重复批次只会出现一次。分数是**相对排序工具**，不是录用概率。

**LLM 精评（可选，需 API Key）**

配置模型连接后，上游的评分链路可以给出四维分数（同样是 100 分制）：

| 维度 | 分值 | 说明 |
| --- | --- | --- |
| 核心方向 | 30 | 岗位方向与简历主方向的一致性 |
| 必备技能 | 30 | JD 明确要求的技能是否具备 |
| 项目证据 | 25 | 简历项目能否作为该岗位的证据 |
| 工程栈 | 15 | 技术栈吻合度 |

区别在于：**LLM 评分需要 JD 正文**，所以只对通过企业官网采集到的岗位生效（当前库中约 135 条带完整 JD）；规则推荐则覆盖全部岗位，包括只有公告标题的 OfferBiu 条目。

**接口**（本地只读）

| 端点 | 用途 |
| --- | --- |
| `GET /api/intelligence/digest?hours=24&track=internship` | 按企业分组的变化汇总 |
| `GET /api/intelligence/events?event_type=new&limit=50` | 扁平事件流 |
| `GET /api/intelligence/push-preview` | 渲染将要推送的原文（不发送） |
| `GET /api/intelligence/recommend?min_score=60&track=campus` | 按简历偏好排序的岗位 |
| `POST /api/intelligence/resume-extract` | 粘贴简历文本 → 关键词建议（无模型） |
| `POST /api/intelligence/profile-save` | 保存推荐偏好（写入 candidate_profile.yaml） |

### 6.2 每日新增与订阅

看「今天新出现了哪些岗位，且符合我的条件」。位置：情报页第四个标签 **每日新增**。

**两种用法：**

| 用法 | 适合 | 怎么做 |
| --- | --- | --- |
| 临时筛选 | 偶尔看一眼 | 填条件 → 点「筛选新增岗位」，不保存 |
| 保存订阅 | 长期关注某个方向 | 填条件 + 订阅名称 → 点「保存为订阅」，下次点顶部那颗胶囊即可 |

**四个筛选维度**（同一订阅内是 AND 关系，一个字段内多个值取 OR）：

| 维度 | 说明 | 示例 |
| --- | --- | --- |
| 关键词 | 岗位标题包含即命中，支持近义词 | `算法` `大模型` `Agent` |
| 岗位方向 | 与关键词同样匹配标题，语义上更宽 | `算法工程师` `后端` |
| 城市 | 岗位城市包含即可，支持"北京、上海" | `北京` |
| 招聘类型 | 校招 / 实习 / 都要 | 只看实习 |

**时间窗口**默认「今天新增」，可切到 3 / 7 / 30 天。窗口按本地日界对齐（今天 = 今天 00:00 到现在）。

**同义词机制**：填「算法」会一并匹配「机器学习 / 深度学习」这类标题变体，否则「Agent 研发」这种岗位永远命中不了。命中结果会区分标注：

- `关键词` / `方向` —— 标题里**原样**出现了你填的词；
- `关键词近义` / `方向近义` —— 靠同义词命中的，排在精确命中之后。

同义词表刻意收窄（每个词 2~4 个），因为它一旦放宽，`算法` 会把 4700 条新增里的 1200 条都算命中，那就不是筛选了。

**数据新鲜度**：页面顶部显示「数据最后更新：X 分钟前」。**筛选本身是毫秒级**，需要等的是采集任务（08:00 / 08:30 那两个）。看到「数据最后更新是 3 小时前」，说明该等采集而不是该等筛选。

**订阅存哪**：`watch_rules` 表（迁移 `029_watch_rules.sql`），纯本地。订阅只保存条件，不保存结果——命中清单每次都按最新事件现算，所以规则不会过期。

**接口**

| 端点 | 用途 |
| --- | --- |
| `GET /api/intelligence/watch/meta` | 订阅列表 + 数据最后更新时间 |
| `POST /api/intelligence/watch/rules` | 新建 / 更新订阅（带 `id` 即更新） |
| `DELETE /api/intelligence/watch/rules/{id}` | 删除订阅 |
| `GET /api/intelligence/watch/run?rule_id=...&window=today` | 按订阅跑命中清单 |
| `GET /api/intelligence/watch/run?keywords=算法&cities=北京&window=7d` | 临时筛选（不保存） |

---

## 7. 事件语义与实现细节

| 表 / 字段 | 说明 |
| --- | --- |
| `job_events` | 只追加的事件日志；`(run_id, job_id, event_type)` 唯一约束保证重跑幂等 |
| `baseline` | 每家企业一条，标记"已建立基线"，不进入日报和工作台 |
| `new` | 官网出现、库里没有的岗位 |
| `updated` | `content_fingerprint` 变化（沿用上游 pipeline 的变更判定） |
| `removed` | 连续两次完整采集缺失且超过宽限期（`offline_grace_runs` / `offline_grace_days` 可调） |

事件在每日采集任务（`daily_recruitment_intelligence`）成功写入快照之后由 `packages/intelligence/writer.py` 追加。**事件写入失败不会影响采集本身**——采集已经提交，失败原因记录在任务结果的 `intelligence_events_error` 里。

企业级配置里可以给某家企业单独加备注性字段（如 `recruitment_types`），pipeline 会原样读进 `extra`，适配器可自行使用。

---

## 8. 已知限制（如实记录）

1. **采集覆盖**：内置适配器约 70 个，但企业内部招聘站的 DOM 会变，任何一个适配器都可能某天失效；失效会体现在「公司」视图的抓取状态里，不会假装成功。
2. **需要登录 / 强验证码 / 滑块**的站点抓不到，工具不绕过任何访问限制。
3. **下架判定有延迟**：宽限期默认 2 次采集 + 3 天，避免官网临时抖动误报下架。
4. **简历匹配评分是可选项**，需要配置 DeepSeek API Key（`RECRUITOPS_LLM_API_KEY`）；不配也能用岗位监控和推送。
5. 桌面版（Electron 壳、内置浏览器闪填）来自上游，本仓库的改造集中在 Web 工作台与定时采集链路，桌面壳的打包流程未在本仓库验证。

---

## 9. 合规边界

- 只访问**公开可访问**的招聘页面，默认低频（每天 1~2 次），不绕过登录、验证码、robots 限制。
- 采集到的岗位正文仅保存在本机数据库，供个人求职参考；**本仓库不附带、不再分发任何第三方岗位原文**。
- 对外公开服务前请自行确认各站点条款——"公开可访问"不等于"允许转载"。
- 若把工作台开放到公网，请自行加上鉴权；本项目定位是**本地部署的个人 / 小群体工具**。
