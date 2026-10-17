# TASK-004 交付：Pipeline 与查询路径闭环

- 分支：`feature/internship-filter`
- 基线：`c19428b`
- 结论：实习岗位可以经**真实 Pipeline** 采集 → 标题筛 → JD 补全 → 完整条件评估 → 入库 → 查询 → 幂等重跑；campus 默认行为保持兼容
- 验证：Pipeline 集成 **13 项**、全量相关子集 **557 项**全部通过；`compileall` / `check_migrations` 通过

---

## 1. 本轮目标与结果

| 审查要点 | 处理 |
|---|---|
| 1. `ScreeningPolicy` 合法性 | `__post_init__` 校验类型与**矛盾组合**（实习模式 + 要求 2027 届 → `ValueError`）；JD 层届别判断独立保留 |
| 2. `eligible` / `qualification` / `retained` | 新增 `_screening_keeps_row`，逐处替换 `if not eligible` 的丢弃逻辑 |
| 3. 策略贯穿 Pipeline | 构造参数 → 标题筛 → JD 补全 → 完整评估 → 入库 → 查询 → 检查点 |
| 4. 查询路径 | `job_browse` 轨道化谓词 + `browse_jobs(track=...)` + API `track` 参数 |
| 5. `include_internships` 兼容 | 保留为 `ScreeningMode.ALL` 的入口，两者不再各自为政 |
| 6~8. 端到端与测试分类 | 注入假 crawler + 合成 JD + 隔离 SQLite；测试分类见 §3 |
| 9. 剩余 2027 硬编码 | 逐项分类见 §6 |
| 10. 不改用户文件 / 不推送 / 不改历史 | 遵守 |

---

## 2. 修改摘要

| 文件 | 改动 |
|---|---|
| `packages/matching/policy.py` | `__post_init__` 合法性校验；新增 `allowed_batches` |
| `packages/domain/models.py` | `Job` 增加 5 个实习字段；`JobBrowseItem` 增加 `track` / `internship_seasons` / `conversion_opportunity` |
| `packages/storage/sync.py` | `upsert_job_snapshot` 按 `model_fields_set` **选择性写入**新列 |
| `packages/pipeline/daily.py` | `screening_policy` 贯穿；`_screen_candidate()`；`_internship_payload()`；`_screening_only_analysis()`；`_screening_keeps_row()`；评分队列排除 retained 不可评岗位；检查点作用域纳入模式 |
| `packages/tools/crawler_audit.py` | 批次门 `allowed_batches` 参数化（**本轮发现的隐藏硬门**） |
| `packages/repositories/job_browse.py` | 轨道化 `_base_predicates` / `_full_statement`；`browse_jobs(track=)`；`_LEGACY_EXCLUSIONS` 扩展；类别缓存覆盖全部轨道 |
| `packages/repositories/postgres.py` | `browse_jobs` 透传 `track` |
| `apps/api/main.py` | `/api/jobs/browse` 新增 `track` 查询参数 |
| `tests/test_internship_pipeline.py`（新增） | 13 项 Pipeline 集成测试 |

### 2.1 语义分工（本轮最重要的一条）

```
eligible  = 能否进入评分阶段
qualification = 是否满足 JD 明确的资格门槛（qualified / not_qualified / undetermined）
retained  = 该行是否允许被删除（本实现恒为 True，由 pipeline 决定展示）
```

Pipeline 里原来有 4 处 `if not <eligible>: 丢弃/标 inactive`。本轮改为 `_screening_keeps_row()`：
只要评测结论 `retained=True`，行就保留为 `active`，并用 `_screening_only_analysis()` 落一条
`not_qualified` 的分析记录——**不调用 matcher**（评分队列已排除 `screening.eligible=False` 的候选）。

### 2.2 隐藏的第五道门：批次门

调试时发现实习岗**根本没进入标题筛**，原因是 `crawler_audit.py:217`：

```python
if job.batch not in {RecruitmentBatch.FORMAL, RecruitmentBatch.EARLY}:  # 无条件
    _reject(reasons, CrawlerRejectionReason.INELIGIBLE_BATCH)
```

这是 TASK-001 清点时按"2027 届"关键字搜索**没有命中**的一道门（它判的是 `batch`，不是 `cohort`）。
现在 `CrawlerAcceptanceInput.allowed_batches` 可配置，campus 默认值不变，实习模式放开。

### 2.3 写入不覆盖

`upsert_job_snapshot` 用 `_optional_job_field` 只在调用方**显式设置**该字段时才写列。
因此一次 campus 回跑不会把已分类的行重置为 `track='unknown'`、也不会把 JSON 证据清成 NULL。
端到端用例 `test_campus_run_cannot_erase_existing_internship_columns` 守护这一点。

---

## 3. 测试分类与结果

证据：[`docs/internops/evidence/task-004-e2e.txt`](evidence/task-004-e2e.txt)

| 类别 | 覆盖 | 结果 |
|---|---|---|
| **单元测试** | `test_internship_{classification,requirements,evaluation}.py`（83 项）——分类器、条件抽取、四状态评估 | 全通过 |
| **契约 / 迁移测试** | `test_internship_{migration,screening}.py`（47 项）——ORM↔迁移一致性、策略语义、campus 黄金用例 | 全通过 |
| **Pipeline 集成测试** | `test_internship_pipeline.py`（**13 项**）——真实 `run_daily_pipeline` + 生产 `Storage` / `Repository` | `total=13 failures=0` |
| **数据库集成测试** | 同一批用例的持久化断言（`JobSnapshot` 列、`JobAnalysisSnapshot` 状态、`browse_jobs` 读取） | 全通过 |
| **真实外部招聘网站采集** | **未执行** | **无证据，不做任何声明** |

全量相关子集（30 文件，含 API / checkpoint / storage）：`total=557 failures=0 errors=0 skipped=0`。

固定说明：**crawler 是注入式假对象，JD 是本文件内合成的文本**。这些用例证明的是"链路正确"，
不是"某招聘网站可用"。

---

## 4. 端到端覆盖的 9 个场景

| # | 场景 | 断言要点 |
|---|---|---|
| 1 | 正常实习岗 | 入库 `track=internship`、`seasons=['summer']`、`conversion=yes`、`qualification=qualified`、可被 `track=internship` 查出 |
| 2 | 时长差距 | `duration.status=gap`、`negotiability=unknown`、`confirmation_required=true`、行保留且仍评分 |
| 3 | 学历硬门槛 | `qualification=not_qualified`、`availability=active`、分析状态 `not_qualified`、**matcher 未被调用** |
| 4 | 届别明确不符 | JD「仅限2027届」→ `cohort.status=gap`、`strength=mandatory` → `not_qualified`，行仍保留 |
| 5 | 条件未知 | 时长/学历均 `unknown`、`gaps==[]`、`qualification=undetermined`、不惩罚 |
| 6 | 非实习岗（实习模式） | 不进入实习视图，provisional 行转 `inactive`，`track` 保持 `unknown` |
| 7 | 重复采集 | 行数不变、`first_seen_at` 不变、`match_conditions` 一致、分析状态一致 |
| 8 | campus 兼容 | 实习岗被拒；campus 行 `track=unknown`、`match_conditions IS NULL`；默认查询仍只返回 campus 行 |
| 9 | 数据库读取 | `track=internship|campus|all` 三种查询分别返回正确集合 |

另加：策略矛盾组合抛 `ValueError`；campus 检查点不能被实习模式恢复（`PipelineError`）。

---

## 5. 数据库前后对比

同一 storage 连续三次运行（同一批 6 条岗位）：

```
run 1 (mode=internship)  rows=6
| campus-1        | unknown    | None       | unknown | -               | inactive | -              |
| intern-cohort   | internship | ['summer'] | unknown | not_qualified   | active   | not_qualified  |
| intern-degree   | internship | ['summer'] | unknown | not_qualified   | active   | not_qualified  |
| intern-duration | internship | ['summer'] | unknown | qualified       | active   | pending        |
| intern-ok       | internship | ['summer'] | yes     | qualified       | active   | pending        |
| intern-unknown  | internship | ['summer'] | unknown | undetermined    | active   | pending        |

run 2 (mode=internship, 同一 storage)  rows=6   ← 幂等，行数与分类完全一致
run 3 (mode=campus, 同一 storage)      rows=6   ← 实习分类**未被清零**
```

说明：该演示未注入 matcher，因此合格岗位停在 `pending`（纯确定性模式的既有行为：不进入评分阶段）；
注入 matcher 的用例里状态为 `eligible`（见场景 1）。

查询路径：

```
track=internship -> 5 rows  (全部实习岗，含 not_qualified 的两条)
track=campus     -> 1 row   (campus-1)
track=all        -> 6 rows
```

`not_qualified` 的实习岗**既能被查到**（保留了行与证据），又不占用评分资源。

---

## 6. 剩余 2027 届硬编码分类

| 位置 | 是否影响当前实习链路 | 处理 |
|---|---|---|
| `crawler_audit.py` 批次门（原 217 行） | **是**（实习岗直接被丢弃） | ✅ 本轮已参数化 |
| `job_browse.py` 谓词（原 54/139 行） | **是**（实习岗查不出来） | ✅ 本轮已轨道化 |
| `daily.py` `expected_cohort=2027` | 否 | 该调用点 `require_confirmed_cohort=False`，不生效；登记 |
| `rules.py` `is_confirmed_2027` | 否（已被 `CohortPolicy` 包住） | 保留为 campus 默认 |
| `matching/service.py:218` prompt 文案 | **是（间接）** | 未改：实习模式若走 LLM 评分，提示词仍写"cohort=2027"。**登记为 TASK-006 必改项** |
| `codex_runtime/instructions.py:31` Agent 指令 | 是（若用 Agent 触发采集） | 登记，未改 |
| `approval/policy.py:165` 审批 | 否（当前链路不写审批） | 登记 |
| `jd_repair.py` / `jd_repair_import.py` | 否（JD 修补独立流程） | 登记 |
| `offerbiu_policy` / `offerbiu_registry` / `oc_*` / `public_entries` | 否（OfferBiu / OC 专属发现链路） | 登记 |
| `scheduler/runtime.py:1513` / `application_capture.py:71` | 否（投递记录生成，当前链路不触发） | 登记 |
| `job_cohorts.py`、10+ 爬虫里的 `"cohort": 2027` | 否（采集源自身的届别证据） | 保留：这是**来源事实**，不是我们的门槛 |
| `title_first_replay.py` 的 2027 scope | 否（回放工具） | 登记 |

原则：只有**当前闭环内会让实习岗被丢弃或被查不到**的才在本轮改；其余登记，避免扩大改动面。

---

## 7. 遗留问题（不隐瞒）

| 问题 | 影响 | 计划 |
|---|---|---|
| **LLM 评分路径未在实习模式端到端验证** | 注入的是确定性 matcher；`MatchingService.analyze` 的实习分支只在单元层验证过 | TASK-006 |
| prompt 仍写死 `cohort=2027` | 实习模式若启用 LLM，提示词与筛选结论不一致 | TASK-006 必改 |
| `job_browse` 未按 `conversion_opportunity` / `season` 过滤 | 只能按 track 查，季节/转正在应用层筛 | 需要时再加（当前无查询依据） |
| `availability_status` 未进 `_base_predicates` | 被拒的 provisional 行仍可能出现在列表（既有行为） | 既有语义，未改 |
| 纯确定性模式（matcher=None）不评分 | 合格行停 `pending`，既有行为 | 非本轮问题，记录 |
| 真实招聘网站采集 | **未做** | TASK-005 |
| SQLite 上无 `internship_seasons` 的 DB 级约束 | ORM 校验兜底 | 已知 |
| 未覆盖的形态 | "可转正"与季节同时出现在不同句、多城市远程混合 | 后续用例 |

---

## 8. 技术讲解（面试用）

**Q：`eligible` / `qualification` / `retained` 为什么要三个概念？**
A：它们回答三个不同问题。"能不能进 LLM 评分"是成本问题；"是否符合资格"是业务事实；
"这行能不能删"是产品/数据策略。合并任意两个都会丢信息：合并 1&2，就无法"保留但跳过评分"；
合并 2&3，用户换了条件后无法复盘。Pipeline 里原来 4 处 `if not eligible: 丢弃` 正是前两者的混淆。

**Q：为什么"保留但跳过评分"要用一条 `not_qualified` 分析记录而不是留空？**
A：留空会让 UI 把它显示成"待评分"，用户会等一个永远不会来的分数。写入 `not_qualified` +
`filter_reasons` 后，列表能直接给出"不符合哪条门槛"，且不产生任何模型调用。

**Q：怎么保证 campus 行为没变？**
A：三处硬约束：① 策略默认 `CAMPUS`，所有新参数 keyword-only 且默认 `None`；
② `_screen_candidate` 只在 `policy.evaluates_conditions` 时才升级到完整评估，campus 走原路径；
③ 用"同一输入在传/不传策略下结果相同"的黄金用例，加上整套既有 pipeline 测试（557 项）兜底。

**Q：批次门这个坑是怎么发现的？**
A：端到端测试第一次跑出来 `rejection_reasons={'ineligible_batch': 1}`，而实习岗根本没到标题筛。
顺着拒绝码找到 `crawler_audit.py` 的无条件 `batch in {FORMAL, EARLY}`。
这说明**按关键字 grep 清点门槛是不完备的**——门槛可能用别的字段表达（batch vs cohort）。
教训：清点"会丢弃岗位的地方"，应该搜 `continue`/`return` 附近的拒绝码，而不是只搜年份字面量。

**Q：为什么把策略折进 checkpoint 的 scope digest？**
A：检查点恢复的语义是"沿用上次的判定继续跑"。若策略变了，之前的每公司结论都来自另一套规则，
继续跑会混合两种语义。复用既有的 digest 不匹配拒绝，比新增一套检查点格式更小、更可测。
