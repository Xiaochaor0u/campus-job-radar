# TASK-003 交付：实习筛选引擎接入原有业务流程

- 分支：`feature/internship-filter`
- 基线：`9c85fe6`
- 性质：把 `packages/internship` 接入 `matching` 层的既有门禁；**campus 默认行为不变**
- 验证：InternOps 相关 **139 项**、业务核心回归 **489 项**全部通过，`compileall` / `check_migrations` 通过

> ⚠️ 本轮的 `packages/matching/title_policy.py` 改动**包含你此前未提交的 `include_internships` 参数**。
> 该参数被保留并扩展为 `ScreeningPolicy` 的兼容入口，两者在同一段代码里，无法拆成两个提交。
> 如果你希望把这段归属单独记录，我可以改用一个提交说明或拆分历史。

---

## 1. 本轮目标

TASK-002/002a 建好了实习分类、条件抽取、资格评估，但**没有任何生产调用点**。TASK-003 把它们接进既有门禁，同时保证：

1. `screen_job` / `screen_title_job` / `analyze` / `analyze_title_first` 在不传策略时**逐字节等价于旧行为**；
2. 实习模式真的能放行实习岗、跳过 2027 届门槛、识别 Python 方向；
3. 把"是否实习"的三份实现收敛成一份（本轮处理 `matching.rules` 与 `recruitment_core.job_filters` 两处）。

---

## 2. 主要修改

| 文件 | 改动 |
|---|---|
| `packages/matching/policy.py`（新增） | `ScreeningMode`（campus/internship/all）、`CohortPolicy`（require_confirmed_2027/any）、`ScreeningPolicy`（frozen dataclass）、`resolve_policy()` |
| `packages/matching/models.py` | 新增 `Direction.PYTHON_DEV`；`LEGACY_DIRECTIONS` / `INTERNSHIP_DIRECTIONS` / `ALL_DIRECTIONS`；新增 `AnalysisStatus.NOT_INTERNSHIP`、`NOT_QUALIFIED`；`ScreeningResult` 增加 5 个可选字段 |
| `packages/matching/rules.py` | `DEFAULT_DIRECTIONS = LEGACY_DIRECTIONS`；`classify_job_directions(..., allowed=)`；`requested_directions(..., fallback=)`；`canonical_direction` 识别 Python；`screen_job(..., policy=)`；新增 `_internship_screening()`；`internship_reason` 委托给 `packages.internship`；删除 7 个重复正则 |
| `packages/matching/title_policy.py` | `screen_title_job(..., policy=)`；`_DIRECTION_TITLE_PATTERNS[PYTHON_DEV]`；放行时回填 `track`/`seasons`/`conversion_opportunity` |
| `packages/matching/service.py` | `analyze` / `analyze_title_first` 增加 `policy`；实习门按策略跳过；`_FILTER_SUMMARIES` 补两个新状态 |
| `packages/matching/__init__.py` | 导出策略与方向分组 |
| `packages/recruitment_core/job_filters.py` | `internship_reason` 委托给 `packages.internship`；删除 5 个重复正则 |
| `packages/internship/{values,classification,requirements}.py` | **修复否定句误判**（见 §5.1） |
| `packages/internship/models.py` | `mandatory_failure` / `confirmation_required` 改为 `computed_field`，进入 JSON 序列化（见 §5.3） |
| `tests/test_title_first_replay.py` | 一处断言由 `set(Direction)` 改为 `set(LEGACY_DIRECTIONS)`（见 §5.4） |
| `tests/test_internship_screening.py`（新增） | 27 项，覆盖策略语义、campus 兼容、实习模式、G4 收敛 |

---

## 3. 六道门的处理状态

| 门 | 位置 | 本轮 |
|---|---|---|
| G1 标题层 | `title_policy.py:238` | ✅ 策略化：`accepts_internships` 为真时放行并打 `track` |
| G2 规则层 | `rules.py:screen_job` | ✅ 策略化：实习门 + 新增"实习模式只收实习"门 + 条件评估 |
| G3 服务层 | `service.py:analyze_title_first:720` | ✅ 策略化：实习模式不再无条件过滤 |
| G4 采集层 | `job_filters.py:210` | ✅ 委托给单一实现（三份变一份） |
| G5 读路径 SQL | `job_browse.py:52/107` | ⏳ 留 TASK-004（需与 pipeline 一起改，否则库里查不出实习岗） |
| G6 排除态语义 | `job_browse.py:33/402` | ⏳ 留 TASK-004 |

**2027 届**：筛选层的硬编码已收进 `CohortPolicy`。其余 12 组（`jd_repair`、`offerbiu_policy`、`approval/policy`、`oc_candidates`、`scheduler`、`application_capture`、10+ 爬虫、LLM prompt、Agent 指令）**本轮未动**，见 §7。

---

## 4. 关键设计

### 4.1 一个冻结的策略对象，而不是更多布尔参数

```python
@dataclass(frozen=True, slots=True)
class ScreeningPolicy:
    mode: ScreeningMode = ScreeningMode.CAMPUS
    cohort: CohortPolicy | None = None      # None → 由 mode 推导
```

- `ScreeningPolicy()` 复现历史行为：要求 2027 届、拒实习、四个原方向。
- `cohort=None` 表示"由模式推导"，调用方无法写出"实习模式但仍要求 2027 届"这种自相矛盾的组合。
- frozen + slots：可安全跨线程传递，也可按值写进 checkpoint（TASK-004 需要）。

旧布尔开关保留为兼容入口：

```python
resolve_policy(include_internships=True)  → ScreeningPolicy(mode=ALL)
```

`include_internships` 只表达"别拒实习"，不表达"校招还要不要按 2027 届筛"，所以映射到 `ALL` 而不是 `INTERNSHIP`。

### 4.2 新方向只在实习模式可达

```python
LEGACY_DIRECTIONS      = (CPP_SOFTWARE, ROBOT_ARM, EMBODIED_LEARNING, LLM_AGENT)
INTERNSHIP_DIRECTIONS  = LEGACY_DIRECTIONS + (PYTHON_DEV,)
DEFAULT_DIRECTIONS     = LEGACY_DIRECTIONS           # ← 校招默认，值不变
```

`classify_job_directions(job, allowed=...)` 只在 `PYTHON_DEV in allowed` 时才尝试 Python 信号，因此
**不传 allowed 时分类输出与改造前完全一致**。`ALL` 模式拿到的是超集（含 Python），`CAMPUS` 仍只有四个。

### 4.3 实习门槛与资格判断分离

实习模式下的 `screen_job` 不会因为"条件差距"返回不可用，只有**资格门失败**才返回 `NOT_QUALIFIED`：

```
NOT_QUALIFIED  ⇐  mandatory 资格门（学历/届别/其他）+ 明确不满足
gap / needs_confirmation ⇐ 只记录在 condition_evaluation 里，eligible 仍为 True
```

`ScreeningResult` 新增的 5 个字段（`track`、`seasons`、`conversion_opportunity`、
`internship_requirements`、`condition_evaluation`）就是 TASK-004 要写进 027 那几列的内容，
因此 pipeline 不需要再算一次。

---

## 5. 开发过程中发现并修复的真实问题

### 5.1 否定句被误判成实习（我的分类器 bug，被仓库既有测试抓到）

`tests/test_matching_rule_repair.py` 里有两句：

```
不要求保证稳定的实习投入时间。
无需保证稳定的实习投入时间。
```

它们描述"岗位**不要求**什么"，却被 `_INTERN_COMMITMENT_RE`（`保证…稳定的…实习`）命中。
TASK-002 的版本漏掉了旧实现里的句首锚点，`G4` 一委派就暴露出来。

修复：在 `packages/internship/values.py` 增加显式否定守卫

```python
NEGATED_REQUIREMENT_RE = r"(?:不要求|无需|无须|不需|不用|不须|不强制|不硬性|免除)\s*$"
```

只匹配**紧邻匹配起点之前**的显式否定短语，所以 `实习期不少于3个月`（含"不"）不会被误判。
守卫同时用于分类（时长/承诺信号）与抽取（时长/每周天数）。

### 5.2 2027 届门槛挡在实习门之前，`ALL` 模式失效

原 `screen_job` 的顺序是"届别 → 实习 → 博士 → JD → 方向"。实习岗通常没有 2027 届信息，
于是 `ALL` 模式下实习岗先被 `COHORT_UNCONFIRMED` 拦下，永远走不到实习分支。

修复：被策略接受的实习岗**跳过届别门**，其余岗位保持原顺序：

```python
accepted_internship = bool(internship) and active.accepts_internships
if not accepted_internship and active.requires_confirmed_2027 and not is_confirmed_2027(job):
    return COHORT_UNCONFIRMED
```

campus 模式下 `accepted_internship` 恒为假，顺序与结果一字未变（有黄金用例守护）。

### 5.3 `confirmation_required` 没进 JSON，前端拿不到

`mandatory_failure` / `confirmation_required` 原本是 pydantic `@property`，
`model_dump()` **不会**序列化属性，于是写进 `match_conditions` 的 JSON 里没有这两个字段，
而它们恰恰是"待核实事项"的载体。

修复：改为 `@computed_field @property`，既保留属性访问，又进入序列化输出。

### 5.4 仓库自带测试断言 `set(Direction)`

`tests/test_title_first_replay.py:751` 断言 `set(requested_directions(profile)) == set(Direction)`，
测试名是"passes four directions"。加入 `PYTHON_DEV` 后该断言失败。

判断：这是**测试依赖枚举成员数**的写法，意图是"校招默认集"。改为
`== set(LEGACY_DIRECTIONS)` 后意图更明确（不再随枚举新增而漂移），并加注释说明
`python_dev` 不得成为校招默认。这不是弱化，而是把隐式断言变成显式断言。

### 5.5 三份实习判定收敛为一份

`matching.rules` 与 `recruitment_core.job_filters` 各自一套 `internship_reason`，
现在都委托给 `packages.internship.classification`，并删除两处共 12 个重复正则。
新增测试 `test_job_filters_and_matching_share_one_internship_classifier`（6 组输入）
直接断言两条调用路径返回同一个结论。

---

## 6. 验证结果

证据：[`docs/internops/evidence/task-003-tests.txt`](evidence/task-003-tests.txt)

| 项目 | 结果 |
|---|---|
| InternOps / internship 相关（7 文件） | `total=139 failures=0 errors=0 skipped=0` |
| 业务核心回归（25 文件） | `total=489 failures=0 errors=0 skipped=0` |
| `test_recruitment_core.py`（公开 CI 排除项，我额外跑） | 15 项中 **14 通过**，1 项失败为 `FileNotFoundError: config/companies.yaml`（本地配置、gitignored、与本轮无关） |
| `compileall` | exit 0 |
| `check_migrations` | exit 0，27 files |

### campus 兼容性的黄金用例

```python
test_campus_outcomes_are_identical_with_and_without_an_explicit_policy  # 6 组输入 × 2 次调用
test_campus_default_directions_do_not_gain_python
```

覆盖：正常校招 → `eligible`；缺届别 → `cohort_unconfirmed`；无届别的实习 → 仍是 `cohort_unconfirmed`
（历史顺序）；有 2027 届的实习 → `internship`；`Python开发工程师` → **`direction_out`**
（即你 baseline 里固化的那条缺陷，在 campus 下**保持原样**）；销售岗 → `direction_out`。

### 实习模式的守护用例

```python
test_internship_job_passes_without_a_2027_cohort          # 不再要求 2027 届
test_internship_result_carries_requirements_and_evaluation
test_duration_shortfall_keeps_the_posting_and_asks_for_confirmation
test_mandatory_degree_gate_marks_not_qualified_but_keeps_the_row
test_internship_mode_rejects_non_internship_postings
test_internship_mode_gains_the_python_direction
test_all_mode_judges_each_track_by_its_own_rules
test_title_policy_tags_the_track_when_internships_are_accepted
```

---

## 7. 遗留问题

| 问题 | 状态 |
|---|---|
| **G5/G6 读路径未改** | 实习岗即使入库，`job_browse` 的 `cohort=2027 AND batch IN (formal,early)` 仍查不出。必须与 pipeline 同一轮（TASK-004）改，否则界面上看不到任何效果 |
| **pipeline 未传策略** | `daily.py` 的 9 处调用点仍是默认 campus。TASK-004 |
| 其余 12 组 2027 硬编码 | 未动（`jd_repair`、`offerbiu_policy`、`approval/policy`、`oc_candidates`、`scheduler`、`application_capture`、爬虫、LLM prompt、Agent 指令） |
| `analyze_title_first` 的实习模式未被直接测试 | 需要构造 `MatchingService` 与假 client；本轮只验证了 campus 路径不变。TASK-004 用注入的 matcher 覆盖 |
| `is_analysis_eligible` / `filter_target_jobs` 未接受 policy | 目前无调用方需要；需要时加 |
| `NOT_QUALIFIED` 未进 `job_browse._LEGACY_EXCLUSIONS` | 属 G6，TASK-004 |
| 提交包含你未提交的 `title_policy.py` 改动 | 见文首说明 |

---

## 8. 技术讲解（面试用）

**Q：为什么用策略对象而不是再加一个布尔参数？**
A：`include_internships: bool` 只能表达"拒/不拒实习"，但真实需要三态（campus/internship/all）
外加"要不要 2027 届门槛"。两个布尔就有 4 种组合，其中 2 种无意义（实习模式仍要求 2027 届）。
冻结 dataclass 把不合法组合从"运行时才发现"变成"构造时不存在"，还能整对象写进 checkpoint。

**Q：怎么保证"默认行为不变"不是嘴上说说？**
A：三条：① `DEFAULT_DIRECTIONS` 保持原四元组，`PYTHON_DEV` 只在 `allowed` 含它时才产出；
② 所有新参数都是 keyword-only 且默认为 `None`，旧调用点签名不变；
③ 用黄金用例把历史结论（含你 baseline 里那条 `Python开发工程师 → direction_out`）
在"不传策略 / 显式传 CAMPUS"两种写法下各断言一次。

**Q：为什么实习岗要跳过届别门，而不是把届别门改成可选？**
A：届别对校招是硬门槛，对实习基本无意义（实习岗通常不分届）。把整个门变可选会让校招失去保护；
只在"策略接受实习 **且** 该岗位确实是实习"时跳过，影响面最小且可解释。

**Q：`NOT_QUALIFIED` 和 `eligible=False` 的关系？**
A：`eligible=False` 只表示"不要进入 LLM 评分"，不表示"删除记录"。资格结论
（`qualification`）与保留策略（`retained`）在 TASK-002a 已分开，本轮沿用：
返回 `NOT_QUALIFIED` 时 `condition_evaluation["retained"]` 仍为 `True`，由 pipeline 决定是否保留。

**Q：三份实习判定合并的风险怎么控制？**
A：先合并，再用既有测试当回归网。合并后立刻暴露出 5.1 的否定句误判——这正说明
"两份近似实现"一直在互相掩护。修复后新增 6 组输入断言两条调用路径结论一致，防止再分裂。
