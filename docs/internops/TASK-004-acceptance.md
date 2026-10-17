# TASK-004 验收记录（针对性复核）

- 分支：`feature/internship-filter`
- 复核基线：`dd2726f`
- 结论：**通过**。9 个端到端场景全部由现有测试覆盖；本轮仅补 3 条必要验证与 1 处最小修复，无功能扩张。
- 回归：相关子集 **575 项**全部通过；`compileall` 通过。

---

## 1. `track=campus` 为什么能检索到 `track=unknown`

### 机制

`job_browse._base_predicates("campus")` 的过滤条件是**届别 + 批次**，不是 `track`：

```python
JobSnapshot.cohort == 2027,
func.lower(JobSnapshot.cohort_status) == "confirmed",
JobSnapshot.batch.in_(("formal", "early")),
JobSnapshot.track != "internship",      # ← 本轮新增
_ACTIVE_SOURCE,
```

迁移 027 之前写入的所有行，`track` 都是 `'unknown'`（迁移不回填，见 TASK-002b §2.3）。
如果 campus 谓词加上 `track == 'campus'`，**整个历史语料会从校招视图里消失**——这就是必须保留
"unknown 走 campus 门"这个兼容回退的原因。

### 是否会让未分类的实习岗错误进入 campus

分三种情况：

| 数据 | 是否进入 campus 查询 | 说明 |
|---|---|---|
| 历史行（027 之前，`track='unknown'`） | 是 | 正是兼容要求：它们本来就是校招语料 |
| 实习模式写入的行（`track='internship'`） | **否** | 本轮新增 `track != 'internship'` 排除，即使它带着 2027 届/formal 批次元数据 |
| 校招模式下被实习门拒绝的行 | 取决于届别/批次 | 该行 `availability_status='inactive'`，`track` 保持 `unknown`；若它同时带 2027 届 + formal 批次，仍会出现在校招视图 |

第三种是**本项目之前就存在的行为**，不是本轮引入的：这类行在校招模式下从来就是
"provisional 行 + excluded 原因"，`browse_jobs(evaluation="excluded")` 也正是靠它们工作。
本轮**没有**用 `availability_status` 过滤，因为那会连带隐藏 `direction_out`/`doctorate_only`
等所有被拒行，破坏"被排除"视图的既有语义。

**证据**：`tests/test_internship_pipeline.py::test_campus_query_never_surfaces_a_classified_internship`
在同一个库里放一条 `track='unknown'`（带 2027/formal）和一条 `track='internship'`（同样带 2027/formal），断言：

```
track=campus     -> ["legacy-unknown"]                 # unknown 保留，internship 被排除
track=internship -> ["intern-with-campus-cohort"]      # 实习岗只出现在实习视图
```

### 残留（不隐瞒）

`track='unknown'` 且带校招元数据的**未分类**行，查询层无法证明它是实习。
要彻底消除，需要在校招模式下把"被实习门拒绝"这一事实也写进 `track`（例如写 `track='internship'`
或在 027 增加一个 `excluded_track` 列）。这会改变既有字段语义，属于数据模型变更，**本轮不做**，
登记为后续可选改进。

---

## 2. 九个端到端场景 ↔ 实际测试

文件：`tests/test_internship_pipeline.py`（15 项，全部通过）。
运行方式：真实 `run_daily_pipeline` + 生产 `Storage` / `PostgresRecruitmentRepository`，SQLite 内存库。

| # | 场景 | 测试名 | 输入（合成） | 关键断言 |
|---|---|---|---|---|
| 1 | 正常实习岗 | `test_normal_internship_is_persisted_and_queryable` | 标题「Python开发暑期实习生」；JD：本科及以上 / 2028届 / 西安 / 3个月 / 每周3天 / 可转正 | `matcher.calls == ["intern-ok"]`；`track=internship`；`seasons=['summer']`；`conversion=yes`；`qualification=qualified`；`browse_jobs(track="internship")` 返回该行 |
| 2 | 时长差距 | `test_duration_gap_is_retained_with_confirmation_required` | 同一 JD，要求 **6 个月**，候选人暑假 2 个月 | `duration.status=gap`；`negotiability=unknown`；`confirmation_required=true`；`availability=active`；`match_conditions.retained=true`；`qualification=qualified`（差距不否定资格） |
| 3 | 学历硬门槛 | `test_mandatory_degree_gate_is_retained_but_not_scored` | JD：**硕士及以上**，候选人本科 | `qualification=not_qualified`；`availability=active`（保留）；分析记录 `not_qualified` 且 `match_score is None`；**`"intern-degree" not in matcher.calls`**（未调用评分器） |
| 4 | 届别明确不符 | `test_explicit_jd_cohort_requirement_is_judged_independently` | JD：**仅限2027届**，候选人 2028-06 | `cohort.required=2027`；`cohort.status=gap`；`cohort.strength=mandatory`；`qualification=not_qualified`；`availability=active` |
| 5 | 条件未知 | `test_unknown_conditions_are_recorded_as_unknown_and_kept` | JD 只有软性描述，`city=None` | `duration.status=unknown`；`degree.status=unknown`；`gaps==[]`；`qualification=undetermined`；`availability=active` |
| 6 | 非实习岗（实习模式） | `test_non_internship_posting_is_not_kept_in_the_internship_view` | C++ 校招岗，实习模式运行 | 行转 `inactive`；`track=unknown`；`rejection_reasons["not_internship"]==1`；`browse_jobs(track="internship").items == []` |
| 7 | 重复采集 | `test_repeated_collection_does_not_duplicate_rows` | 同一岗位跑两次（同一 storage） | 行数恒为 1；`first_seen_at` 不变；`match_conditions` 一致；分析状态一致 |
| 8 | campus 兼容 | `test_campus_default_still_rejects_internships_and_keeps_campus_rows`<br>`test_campus_run_cannot_erase_existing_internship_columns` | 实习岗 + 校招岗，campus 模式；以及"先实习模式后 campus 回跑" | 实习岗不入库（`"intern-ok" not in rows`）；校招行 `track=unknown`、`match_conditions is None`；campus 查询只返回校招行；campus 回跑后 `track` 仍为 `internship`、`match_conditions` 未被清空 |
| 9 | 数据库读取 | `test_all_mode_stores_both_tracks_and_each_query_returns_its_own`<br>（场景 1/8 的 `browse_jobs` 断言） | ALL 模式，两条岗位 | `track=internship` → 实习岗；`track=campus` → 校招岗；`track=all` → 两者 |

补充（本轮新增，非新功能）：
`test_campus_query_never_surfaces_a_classified_internship`（第 1 项）、
`test_checkpoint_same_mode_resume_is_accepted` 与 `test_checkpoint_scope_isolates_screening_modes`（第 3 项）、
`test_contradictory_policy_is_rejected`、`test_allowed_batches_follow_the_mode`。

---

## 3. 检查点跨模式恢复

两张表分开回答：

| 问题 | 验证 |
|---|---|
| 模式是否进入检查点键？ | 是。`run()` 中 `_checkpoint_scope_digest = company_scope_digest(selected)`，非 campus 模式再追加 `":screen-<mode>"`，该 digest 写入每个 company receipt 与 index（`"scope_digest"`），加载时逐条比对。 |
| 跨模式恢复会不会错误复用另一模式的**已完成任务状态**？ | 不会。`test_checkpoint_scope_isolates_screening_modes`：实习模式跑完并写出 checkpoint（含已完成公司状态）后，用 **campus** 策略 + `resume_from_checkpoint=True` 恢复 → 抛 `PipelineError`（scope digest 不匹配），**在读取任何已完成状态之前**失败。 |
| 是否只是"永远拒绝恢复"？ | 不是。`test_checkpoint_same_mode_resume_is_accepted`：同模式恢复成功，`selected_companies == 1`。 |

局限（登记）：digest 只覆盖 `mode`，不覆盖 `cohort` 覆盖项。当前不存在非默认 `cohort` 的调用方，
若将来出现需要一并纳入。

---

## 4. 数据库类型声明

| 测试类别 | 数据库 | 是否等同于 PostgreSQL 链路 |
|---|---|---|
| Pipeline 集成测试（`tests/test_internship_pipeline.py`，15 项） | **SQLite 内存库**（`sqlite:///:memory:`） | **否** |
| 存储/仓储集成（`test_postgres_repository.py`、`test_repository_pagination.py`） | **SQLite** | **否**（类名叫 Postgres，实际跑在 SQLite 上） |
| 迁移验证（TASK-002b，`scripts/verify_internship_pg_migration.py`） | **PostgreSQL 16**（一次性 Docker 容器，pgvector/pgvector:pg16） | 是，但仅覆盖**迁移 DDL 与约束**，不含 Pipeline 业务链路 |
| 真实招聘网站采集 | 未执行 | 无 |

因此准确表述是：**业务链路在 SQLite 上完成端到端验证；PostgreSQL 只验证了迁移本身**。
`JSONB` 与 `recruitops_valid_internship_seasons` 约束在 SQLite 上不存在，由 ORM 校验兜底。
把业务链路放到 PostgreSQL 上跑需要在 CI 起容器，本轮不做。

---

## 5. `service.py` 固定 2027 提示词 —— 已最小策略化

原实现（`_prompt_system()`）无条件写："岗位已经通过确定性筛选：cohort=2027、cohort_status=confirmed、非实习且非博士限定。"
在实习模式启用 LLM 评分时，这句话与筛选结论矛盾。

改动（`packages/matching/service.py`）：

```python
def _screened_clause(screening) -> str:
    if screening is None or (screening.track or "campus") != "internship":
        return "岗位已经通过确定性筛选：cohort=2027、cohort_status=confirmed、非实习且非博士限定。"
    # 实习模式：由实际结论生成
    ... track=internship；seasons=…；conversion=…；qualification=…
    ... 已记录的条件差距：…；存在差距不等于岗位不合格。
```

- `_prompt_system(screening)` 在 `_complete_validated` 中被调用并透传 `screening`。
- **campus 文案逐字未变**，因此既有校招分析和缓存的可比性不受影响；`PROMPT_VERSION` 未改动，
  避免让全部历史分析失效。
- 回归：`test_matching*.py` 等全部通过（575 项子集）。

仍未做（属 TASK-006）：实习模式下 `DeepSeekMatchPayload` 的评分维度是否适配"实习条件差距"、
提示词是否要引用 `condition_evaluation` 的逐条证据、以及结构化输出的额外校验。

---

## 6. 范围纪律

- 未新增功能，只做：1 处查询谓词加固（`track != 'internship'`）、1 处提示词策略化、3 条必要测试。
- 未覆盖用户未提交文件：`tests/test_internops_title_mode.py`（AM）、`tests/test_internops_baseline.py`（??）保持原样。
- 未推送、未合并、未改写历史。

---

## 7. 验收结论

| 验收标准 | 结论 |
|---|---|
| 实习岗位经过真实 Pipeline 业务处理 | ✅（真实 `run_daily_pipeline`，非 mock 业务逻辑） |
| 正确入库（新列、证据、状态） | ✅ |
| 正确查询（三种 track、互不串味） | ✅ |
| 幂等重跑 | ✅ |
| campus 默认行为兼容 | ✅（黄金用例 + 575 项相关子集回归） |

**TASK-004 正式结束。**

## 8. 实际遗留问题（按优先级）

1. **真实数据源尚未接入**（TASK-005）。当前所有岗位文本均为合成 fixture。
2. **LLM 评分路径未在实习模式端到端验证**（TASK-006）：`_complete_validated` 的实习分支只做了文案策略化。
3. **PostgreSQL 上未跑业务链路**：迁移已验证，Pipeline 只在 SQLite 上验证。
4. `track='unknown'` 且带校招元数据的未分类实习行，查询层无法区分（见 §1 残留）。
5. 检查点 digest 未纳入 `cohort` 覆盖项（当前无调用方使用）。
6. `job_browse` 未按 `seasons` / `conversion_opportunity` 过滤（无查询依据，暂不加）。
