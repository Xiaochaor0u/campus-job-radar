# TASK-005 交付：真实实习招聘数据源接入

- 分支：`feature/internship-filter`
- 数据源：**腾讯招聘（join.qq.com）公开只读 JSON API**
- 结论：**达到自动采集验收标准** —— 真实实习岗位经现有 Pipeline 完成采集 → 分类 → 条件提取 → 入库 → 查询，并通过一次重复采集验证
- 验证：离线回归 **400 项**全部通过；真实采集执行 **2 轮**（首轮 + 幂等复采）

---

## 1. 来源评估（四个候选，只选用一个）

| 来源 | 接入方式 | 实测结果 | 结论 |
|---|---|---|---|
| **腾讯 `join.qq.com`** | 公开只读 JSON API：`/api/v1/position/searchPosition` + `/api/v1/jobDetails/getJobDetailsByPostId` | 项目映射接口确认存在 **「应届实习」(mappingId=2)** 与 **「日常实习」(mappingId=104)**；列表宣称 **255** 条实习岗；详情接口返回含 `desc`/`request` 的 JD | **采用** |
| 美团 `zhaopin.meituan.com` | 公开 JSON API，仓库已有适配器 | 接口 HTTP 200 但返回空 `data`；我按仓库既有契约构造请求未复现成功，未继续逆向 | 未采用（契约不明，避免耗时） |
| 华为 `career.huawei.com` | 需浏览器触发内部 API | 需 Chromium，本轮未验证 | 未采用 |
| 字节 `jobs.bytedance.com` | Playwright 页面驱动 | 仓库代码注释已写明"未捕获到正式校招岗位搜索 API" | 未采用 |

**为什么不是"HTTP 200 就算通"**：腾讯的验证包含三层——① 项目映射中存在实习项目；② 列表接口返回 255 条且带 `postId`/`positionTitle`/`workCities`；③ 详情接口能取到 `desc`+`request` 两段 JD。三层都过才算出数据。

## 2. 代码修改

| 文件 | 说明 |
|---|---|
| `packages/recruitment_core/crawlers/tencent_intern.py`（新增，~250 行） | 腾讯实习岗适配器。复用 `TencentCrawler` 的 `_row_post_id` / `_detail_texts` / `_city_text` / `_plain_text` 解析逻辑与 `BaseCrawler` 的请求重试风格；**不复用**其正式校招项目选择状态机（那是另一条轨道） |
| `scripts/collect_tencent_internships.py`（新增） | 真实采集驱动：调 `run_daily_pipeline`（实习模式）→ 隔离 SQLite → 复采 → 查询，输出 JSON 证据 |
| `tests/test_tencent_internship_adapter.py`（新增，8 项） | 回放真实记录的 API 响应做离线回归 + 第 5 项的表述反例 |
| `tests/fixtures/tencent_intern_sample.json`（新增） | 3 条真实响应的录制快照（含 `fetched_at` 与来源 URL），供离线测试 |

**访问合规**：只用匿名访客就能访问的只读接口；无登录、无验证码、无 share 链接凭证、不绕过任何限制；
顺序分页 + 页上限 + 请求间隔，`--limit` 直接限制详情请求数。

**字段映射**：岗位名 ← `positionTitle`；公司 ← 配置的"腾讯"；招聘类型 ← `batch=internship`；
城市 ← `workCities`/`workCityList`；JD ← `岗位描述`(desc) + `任职要求`(request)；原始链接 ←
`https://join.qq.com/post_detail.html?postid={postId}`；采集时间 ← pipeline 写入的 `first_seen_at/last_seen_at`；
`cohort=None` 且 `cohort_status='unconfirmed'`（**实习岗不按届别招募，不猜**）。

## 3. 真实执行证据

证据文件：`docs/internops/evidence/task-005-tencent-20261017T072326Z.json`
隔离数据库：`%TEMP%\internops-task005-20261017T072326Z.sqlite`（**未触碰任何现有数据库**）

### 运行序列（三次真实执行，全部保留）

| 证据文件 | 目的 | 说明 |
|---|---|---|
| `…T072038Z.json` | 首次全量（255 条详情） | 发现 **91 条返回 404「岗位已下架」**，当时被计为 `detail_failed` |
| `…T072151Z.json` | 修正后限流（前 60 条详情） | 下架改为单列 `delisted`；样本内下架为 0 |
| `…T072326Z.json` | **最终验收证据** | 修正"截断却声明全量"的完整性问题 |

保留全过程是因为第一次运行正是"91 条下架"这一发现的唯一证据；它的 `detail_failed` 计数已被后续版本修正。

| 指标 | 实测 |
|---|---|
| 来源 | `https://join.qq.com`，项目 `[2, 104]` |
| 分页 | 6 页，唯一岗位 **255**，与接口 `count` 一致 |
| 详情 | 本轮取样前 60 条：**成功 60**，失败 0，无 `任职要求` 缺失 |
| 入库 | **11** 条实习岗（其余 49 条被方向筛选拒绝） |
| JD 完整度 | **11/11** 同时含「岗位职责」与「任职要求」 |
| 资格结论 | 4 条 `qualified`、7 条 `undetermined`（JD 未写学历/时长等硬条件） |
| 重复采集 | 第 2 轮 **new=0**，行数仍 11，分类与资格结论完全一致 |
| 查询 | `track=internship` → 11；`track=campus` → 0；`track=all` → 11 |

**下架与失败的区分**（第 8 项要求）：对全部 255 条做详情请求时，接口对 **91 条**返回
`status=404「岗位已下架」`。适配器把它单独记为 `delisted`，**不计入 `detail_failed`**，
并且不生成岗位对象——这是来源自己声明的下架，不是"我们没抓到"。样本内（前 60 条）下架为 0。

**公司级状态为 `partial / pagination_unknown`**：本轮只取样 60/255，脚本如实上报"完整性未知"
而不是宣称"完整"。这不是失败；要得到 `complete` 需抓全 255 条并接受 91 条已下架。

真实岗位样本（来自证据 JSON）：

```
AI-HR培训生(分析方向)         深圳总部、上海    jd=655 字   qualification=undetermined
AI安全技术工程师              深圳总部、北京、上海、广州  jd=604 字  qualification=undetermined
AI安全技术工程师(风控算法)     深圳总部、北京、广州  jd=607 字  qualification=qualified
```

## 4. 测试结果（区分模拟与真实）

| 类别 | 数据 | 联网 | 结果 |
|---|---|---|---|
| 适配器离线回归（8 项） | **真实 API 响应录制快照** | 否 | 通过 |
| 实习分类/抽取/评估（83 项） | 合成 | 否 | 通过 |
| Pipeline 集成（15 项） | 合成 | 否 | 通过 |
| 迁移/查询/API 回归 | 混合 | 否 | 通过 |
| **真实网络采集** | **真实腾讯岗位** | **是** | 见 §3（非 pytest，由脚本执行并留存 JSON 证据） |
| 本轮相关子集合计 | — | 否 | `total=400 failures=0 errors=0 skipped=0` |

诚实边界：真实采集**没有**写成 pytest 用例（会依赖网络、不可重复）。它由
`scripts/collect_tencent_internships.py` 执行，输出带时间戳的 JSON；离线回归只回放录制快照。

## 5. 表述区分测试（第 5 项）

真实 JD 里"实习经历/暑期实习"是最容易误判的表述。新增用例覆盖：

| 输入 | 期望 |
|---|---|
| 正式岗「C++软件开发工程师」+ "有相关实习经历者优先" | **不判为实习** |
| 正式岗「算法工程师」+ "有暑期实习经验者加分" | **不判为实习** |
| 「测试开发实习生」+ 每周到岗3天 | 判为实习 |
| 「后台开发」+ "实习期不少于3个月" | 判为实习 |

另外，对**真实**录制的腾讯实习 JD 做了断言：`track=internship`、`seasons==[]`
（项目名是"应届实习/日常实习"，不含暑期/寒假字样——空列表表示"已分析无明确季节"，不猜）。

## 6. 与既有 Pipeline 的关系（第 6 项）

没有另建演示流程：采集结果走 `run_daily_pipeline(crawler=..., screening_policy=INTERNSHIP)`，
经过规范化 → 批次审计 → 标题筛选 → JD 补全 → 完整条件评估 → `upsert_job_snapshot` 入库，
再用生产 `PostgresRecruitmentRepository.browse_jobs(track=...)` 查回。

## 7. 遗留问题

1. **只接了一个来源**。美团契约未复现、华为/字节需浏览器，均未继续。
2. **样本截断**：本轮只取 60/255；完整抓取需约 255 次详情请求（其中 91 条会返回下架）。
3. **`qualification=undetermined` 占 7/11**：腾讯实习 JD 多为职责描述，未写学历/时长，属真实数据特征，
   不是解析缺陷；这也是 TASK-006 需要 LLM 辅助解释的地方。
4. **`analysis_status=pending`**：未注入外部 matcher 时确定性模式不进入评分阶段（既有行为）。
5. **未做增量调度**：重复采集是手动执行一次，尚无定时任务。
6. **Chromium 安装状态**：安装任务已启动但未确认完成（本轮腾讯源不需要浏览器，未阻塞）。
7. 岗位内容变更（同一 postId 的 JD 被修改）本轮未构造真实案例验证，仅验证了重复采集的幂等性。
