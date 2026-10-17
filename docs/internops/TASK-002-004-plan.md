# InternOps — TASK-002 / 003 / 004 实施计划

> 依据：`D:\work\recruitops-agent` @ `feature/internship-filter` (HEAD `1584307`)，只读核查结果。
> 原则：**不推倒重建**；校招（campus）默认行为逐字节不变；实习业务作为"新增轨道"接入同一条 pipeline。
> 本文件是计划，不是已完成的工作。任何条目在代码落地并跑通测试前都不得对外声称"已实现"。

---

## 0. 先确定三条不可动摇的设计约束

| 编号 | 约束 | 原因 |
|---|---|---|
| C1 | 招聘类型（track）只有一个判定实现 | 现状 `matching/rules.py:194` 与 `recruitment_core/job_filters.py:210` 是两份近似但不等价的 `internship_reason`，两边都在生产路径上被调用（`runner.py:258`、`pipeline/daily.py:3012`）。继续各改一份必然产生"同一岗位两个结论"。 |
| C2 | 缺失信息一律 `unknown`，不得折算成 `gap` | JD 里没写"每周到岗几天"≠ 候选人达不到。把"没写"当"不满足"是本项目最容易犯且面试官最容易追问的错误。 |
| C3 | 时长不足不得直接淘汰岗位 | 需求 3.3 明确规定：3 个月要求 vs 2 个月可用 → 保留岗位 + 标注差距 + 标"是否可协商待核实"。只有 JD 出现"不接受短期/少于X个月不予考虑"这类**显式拒绝**才允许硬淘汰。 |

四个匹配状态（贯穿 TASK-002~004 的唯一语义）：

```
met                 明确满足（JD 有明确要求 + 候选人明确满足）
gap                 明确差距（JD 有明确要求 + 候选人明确不满足 + 该要求非协商性）
unknown             信息不足（JD 未写 / 候选人未配 / 无法解析）
needs_confirmation  存在差距但可协商性未知（默认：时长、到岗日期、每周天数）
```

`needs_confirmation` 是**待核实事项**，不是招聘事实。系统不得输出"该岗位可以协商缩短实习期"。

---

## TASK-002：实习业务模型设计

### 2.1 新增包 `packages/internship/`

选择新增独立包而不是继续往 `packages/matching/` 里塞，理由：
- `packages/matching/` 的 `Direction` / `AnalysisStatus` 是**校招评分域**，被 `DeepSeekMatchPayload`、`AnalysisRecord`、DB JSON、前端评分条共同消费；往里加实习语义会污染已有契约。
- 实习条件评估是**独立可测试的纯函数域**，不依赖 DB、不依赖 LLM。

```
packages/internship/
├── __init__.py
├── models.py           # 枚举 + pydantic 结构（唯一事实来源）
├── classification.py   # track / internship_kind 判定（替代两份 internship_reason）
├── requirements.py     # 从 JD 抽取实习条件（带 excerpt，绝不臆造）
└── evaluation.py       # 条件 × 用户偏好 → MatchEvaluation
```

### 2.2 `models.py` 字段设计

```python
class Track(StrEnum):
    CAMPUS      = "campus"        # 正式校园招聘
    INTERNSHIP  = "internship"    # 实习招聘（含日常/暑期/寒假/可转正）
    SOCIAL      = "social"        # 社会招聘
    UNKNOWN     = "unknown"       # 证据不足，保留原状

class InternshipKind(StrEnum):
    DAILY       = "daily"         # 日常实习
    SUMMER      = "summer"        # 暑期实习
    WINTER      = "winter"        # 寒假实习
    CONVERTIBLE = "convertible"   # 可转正实习
    UNSPECIFIED = "unspecified"   # 明确是实习，但批次/季节未写明

class ConditionStatus(StrEnum):
    MET = "met"
    GAP = "gap"
    UNKNOWN = "unknown"
    NEEDS_CONFIRMATION = "needs_confirmation"
```

为什么 `Track` 只有 4 个值而 `InternshipKind` 单独一个枚举：如果做成 8 个平铺枚举（`intern_daily`, `intern_summer`, ...），后续每加一个维度（远程/线下、转正/不转正）就是乘法爆炸，且所有 `if track == "internship"` 的判断点都要改。拆成"轨道 ∈ {campus, internship, social, unknown}" + "实习子类"两级，判定点稳定、扩展只动子类。

```python
class RequirementValue(BaseModel):
    """一个条件：抽取到的值 + 证据出处。value 为 None 表示 JD 没写。"""
    value: Any | None = None
    raw_text: str = ""          # JD 原文片段，前端"查看依据"直接展示
    source: str = "jd_raw"      # jd_raw / title / job_type / batch / unknown
    confidence: float = 0.0     # 0~1，规则命中强度

class InternshipRequirements(BaseModel):
    degree: RequirementValue          = Field(default_factory=RequirementValue)   # 本科/硕士/不限
    cohort_year: RequirementValue     = Field(default_factory=RequirementValue)   # 2028 等
    cities: RequirementValue          = Field(default_factory=RequirementValue)   # list[str]
    duration_months: RequirementValue = Field(default_factory=RequirementValue)   # 最少连续月数
    days_per_week: RequirementValue   = Field(default_factory=RequirementValue)   # 每周到岗天数
    start_date: RequirementValue      = Field(default_factory=RequirementValue)
    skills: list[str]                 = Field(default_factory=list)
    explicit_rejections: list[str]    = Field(default_factory=list)  # "不接受短期实习"等显式拒绝
    evidence: list[ConditionEvidence] = Field(default_factory=list)

class ConditionResult(BaseModel):
    name: str                      # degree / city / duration / days_per_week / ...
    status: ConditionStatus
    required: Any | None = None    # 来自 JD
    candidate: Any | None = None   # 来自用户配置
    jd_excerpt: str = ""           # 关键判断依据（原文）
    note: str = ""                 # 为什么是 needs_confirmation

class MatchEvaluation(BaseModel):
    verdict: Literal["eligible", "eligible_with_gaps", "insufficient_info", "rejected"]
    conditions: list[ConditionResult]
    hard_failures: list[ConditionResult]      # 只可能是显式拒绝类
    gaps: list[ConditionResult]
    unknowns: list[ConditionResult]
    needs_confirmation: list[ConditionResult]
```

关键取舍：`RequirementValue.value is None` 就是"JD 没写"，**不需要额外的 `present` 字段**；`None` 与 `"0"`、`"不限"` 严格区分。面试可讲：用 `Optional` 表达三态（有值/明确不限/未知）时，必须为"明确不限"准备一个非 None 的哨兵值（如 `value="unlimited"`），否则 `None` 会同时代表两件事——这是典型的建模陷阱。

### 2.3 `classification.py`：单一 track 判定

```python
def classify_track(job) -> TrackDecision:
    """返回 track + internship_kind + 证据链 + 置信度。证据不足 → Track.UNKNOWN。"""

class TrackDecision(BaseModel):
    track: Track
    internship_kind: InternshipKind | None
    evidence: list[ConditionEvidence]   # 复用 ScreeningEvidence 的 shape
    confidence: float
```

合并规则（取两份现有实现的并集，逐条保留证据）：
1. `batch` 含 internship/实习 → internship（高置信）
2. `title` 命中 `实习|intern` → internship（高置信）
3. `job_type` 含实习 → internship
4. `jd_url` 命中 `/intern(ship)?/`、`type=intern` → internship
5. JD 命中 `日常实习|暑期实习|寒假实习|可转正实习|实习生招聘计划` → internship + 对应 kind
6. JD 前 260 字符出现独立"实习"元数据行 → internship
7. JD 命中 `实习X个月` / `每周到岗X天` → internship
8. 显式 `校园招聘|校招|应届` 且无实习证据 → campus
9. `社会招聘|社招` 且无实习证据 → social
10. 以上全不命中 → **unknown**（不是 campus，不是 social）

第 10 条是对现状的**行为改变**：现在 `job_filters.recruitment_track()` 的兜底是 `"formal"`（`job_filters.py:307`），即"判不出来就当校招"。实习模式下这个兜底会把大量`unknown`当成校招漏掉。改法：新增返回 `"unknown"` 的判定函数，`recruitment_track()` 保留旧兜底作为兼容层（校招路径行为不变），实习路径调用新函数。这是"兼容但不将错就错"的具体做法，也是面试可讲的点：**不要为了少改一行而把错误语义固化**。

### 2.4 与旧结构的兼容

- 不改 `Domain.Job` / `JobSnapshot` 既有列语义（`batch`、`cohort`、`cohort_status` 原样保留）。
- 迁移 `027_internship_track.sql`（沿用 `020_title_first_capture.sql` 的写法：`ADD COLUMN ... NOT NULL DEFAULT`，索引 `IF NOT EXISTS`）：

```sql
ALTER TABLE job_snapshots ADD COLUMN track VARCHAR(32) NOT NULL DEFAULT 'unknown';
ALTER TABLE job_snapshots ADD COLUMN internship_kind VARCHAR(32);
ALTER TABLE job_snapshots ADD COLUMN internship_requirements JSON NOT NULL DEFAULT '{}';
ALTER TABLE job_snapshots ADD COLUMN match_conditions JSON NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS ix_job_snapshots_track_city ON job_snapshots (track, city);
```

- 回填：只回填**有证据**的行。`track` 由 `classify_track()` 重算写入；判不出的保持 `unknown`，不为了"数据好看"猜一个。旧行 `internship_kind`/`match_conditions` 留空。
- 空值处理：`internship_kind` 允许 NULL（校招/社招行本来就是空）；`internship_requirements` 用 `'{}'` 而不是 NULL，避免 SQL 里 `COALESCE` 满天飞。
- 迁移测试：`tests/test_migration_027.py` —— 在 sqlite 临时库上建 026 结构 → 插旧行 → 跑 027 → 断言旧行 `track='unknown'`、既有列值一字不变 → 再跑一次 027 幂等通过。

### 2.5 输入输出示例（这也是 TASK-003/004 测试的黄金用例）

输入（岗位，全部字段来自 fixtures，非真实数据）：

```json
{"title": "Python开发实习生", "city": "西安", "job_type": "实习",
 "jd_raw": "岗位职责：参与内部工具开发。任职要求：本科及以上，2028届优先，"
           "每周到岗3天，连续实习3个月以上，不接受短期实习。"}
```

输出 `classify_track`：`track=internship, kind=unspecified, confidence=0.9`
输出 `extract_internship_requirements`：`degree=本科及以上`、`cohort_year=2028`、`days_per_week=3`、`duration_months=3`、`explicit_rejections=["不接受短期实习"]`

用户配置（`config/candidate_profile.yaml`，非硬编码）：

```yaml
internship:
  graduation: "2028-06"
  preferred_cities: ["西安", "郑州"]
  available_months: {summer: 2, winter: 1.5}
  max_days_per_week: 3          # 可选，未配置 = unknown
  directions: ["python_dev", "test_dev", "automation_test"]
  secondary_directions: ["java_dev", "data_analysis", "enterprise_it", "manufacturing_digital"]
```

输出 `evaluate_conditions`：`duration` → `needs_confirmation`（要求3个月/可2个月，且 JD 有"不接受短期实习"→ 提升为 `gap` 但**仍保留岗位**，`verdict=eligible_with_gaps`）；`city` → `met`；`degree` → `met`。

---

## TASK-003：完整实习筛选引擎

### 3.1 你现有的改动怎么处理（保留 + 补强，不覆盖）

现状 `packages/matching/title_policy.py:214-258`：

```python
def screen_title_job(job, profile=None, *, include_internships: bool = False) -> ScreeningResult:
    ...
    if not include_internships and (_INTERNSHIP_TITLE_PATTERN.search(title) or internship_reason(job)):
        ... return INELIGIBLE / ANALYSIS_STATUS.INTERNSHIP
```

核查结论：
- **行为兼容正确**：`False` 时与原实现等价（`tests/test_title_policy.py`、`test_title_first_replay.py`、`test_matching_title_policy.py` 等 359 项业务核心测试全绿）。
- **但它是个死参数**：全仓库 grep，`include_internships` 只出现在定义处和你新增的两个测试文件里，**没有任何生产调用点**。也就是说今天的实习模式对 pipeline 完全没有影响。
- **它只拆掉了 6 道门里的第 1 道**（见报告 G1~G6）。
- **注释已失真**：docstring 仍写 "reject explicit internship evidence from any stage"，与参数语义矛盾。

处理方案（保留你的名字与默认值，避免重写你的测试）：

```python
class ScreeningMode(StrEnum):
    CAMPUS = "campus"
    INTERNSHIP = "internship"
    ALL = "all"

@dataclass(frozen=True)
class ScreeningPolicy:
    mode: ScreeningMode = ScreeningMode.CAMPUS
    allowed_directions: tuple[Direction, ...] | None = None   # None → DEFAULT_DIRECTIONS
    cohort_policy: CohortPolicy = CohortPolicy.REQUIRE_CONFIRMED_2027
    @property
    def includes_internships(self) -> bool: return self.mode is not ScreeningMode.CAMPUS
```

- `screen_title_job(job, profile=None, *, include_internships: bool = False, policy: ScreeningPolicy | None = None)`：`policy` 为空时用旧布尔逻辑（你的测试继续保持通过）；`policy` 存在时以 policy 为准。
- 实习模式要新增**独立结果状态**，不要复用 `eligible`：新增 `AnalysisStatus.INTERNSHIP_ELIGIBLE = "internship_eligible"`，并在 `ScreeningResult` 上带 `track` + `requirements`，否则前端无法区分"校招通过"和"实习通过"。

### 3.2 六道门逐一改造

| 门 | 位置 | 校招模式 | 实习模式 |
|---|---|---|---|
| G1 | `title_policy.screen_title_job:238` | 保持拒绝 | 放行，标注 track |
| G2 | `rules.screen_job:656` | 保持拒绝 | 放行，改走 `internship.evaluate` |
| G3 | `matching/service.py:720 analyze_title_first` | 保持 | `internship_reason` 分支改为按 policy 走 |
| G4 | `job_filters.is_formal_campus_job:286` / `is_displayable_campus_job:294` | 不变 | 采集侧不再用"是否实习"做丢弃条件（改用 track 标注） |
| G5 | `job_browse._base_predicates:52` / `_full_statement:107` | `cohort=2027 AND confirmed AND batch IN (formal,early)` | `track='internship'`；新增 `mode=all` 的并集谓词 |
| G6 | `job_browse._LEGACY_EXCLUSIONS:33` + `:402` | 保留 `internship` 为排除态 | 排除态改为 `not_interested`，`internship` 不再等于排除 |

### 3.3 2027 届硬编码的清点结果（全部已核实行号）

必须参数化的"仅 2027"判断点：

| 文件 | 行 | 用途 |
|---|---|---|
| `packages/matching/rules.py` | 186 `is_confirmed_2027`, 615, 646 | 筛选总门 |
| `packages/pipeline/daily.py` | 3791 | 入库时把 cohort 写成 2027 |
| `packages/pipeline/daily.py` | 4915 | `expected_cohort=2027` |
| `packages/repositories/job_browse.py` | 54, 139 | 列表 SQL 硬过滤 |
| `packages/approval/policy.py` | 165 | 审批策略 |
| `packages/recruitment_core/jd_repair.py` | 254 | JD 修补 |
| `packages/recruitment_core/jd_repair_import.py` | 43 | 修补导入 |
| `packages/recruitment_core/offerbiu_policy.py` | 17 | 强制 2027 |
| `packages/tools/oc_candidates.py` | 928 | 候选 |
| `packages/tools/application_capture.py` | 71 | 投递记录 |
| `packages/scheduler/runtime.py` | 1513 | 调度 |
| `packages/recruitment_core/crawlers/aifly.py` 295 / `chaitin.py` 347 等 | — | 爬虫直接写死 `cohort: 2027` |
| `packages/matching/service.py` | 217 | LLM prompt 里写死 "cohort=2027" |
| `packages/codex_runtime/instructions.py` | 31 | Agent 指令写死 2027 |

改造原则：**不删除这些判断，全部收进 `CohortPolicy`**（`REQUIRE_CONFIRMED_2027` / `ANY` / `EXPLICIT_ONLY`）。campus 模式默认 `REQUIRE_CONFIRMED_2027` → 校招行为零变化；internship 模式用 `ANY`（实习岗位本来就不按毕业届别招）。prompt 与 instructions 里的 2027 文案同步参数化，否则模型会拿着过期的招聘事实做判断。

### 3.4 Python / 测试开发方向覆盖不足

现状 `Direction` 只有 4 个值：`cpp_software`、`robot_arm`、`embodied_learning`、`llm_agent`（`matching/models.py:17`）。`测试开发/测开/自动化测试/软件测试/SDET` 已经被折进 `cpp_software`（`title_policy.py:95-100`、`rules.py:282`），所以**测试开发是能过的**；真正缺的是 **Python 开发**——`tests/test_internops_baseline.py:13` 已经把 `("Python开发工程师","校招",False,"direction_out")` 固化进了断言，这正是你发现的缺陷的书面证据。

方案（保校招零影响）：

```python
# matching/models.py
class Direction(StrEnum):
    CPP_SOFTWARE = "cpp_software"
    ROBOT_ARM = "robot_arm"
    EMBODIED_LEARNING = "embodied_learning"
    LLM_AGENT = "llm_agent"
    PYTHON_DEV = "python_dev"      # 新增
```

- `DEFAULT_DIRECTIONS = tuple(Direction)` 这行**必须改**为显式元组，保持校招默认集合仍为原 4 个：

```python
LEGACY_DIRECTIONS = (CPP_SOFTWARE, ROBOT_ARM, EMBODIED_LEARNING, LLM_AGENT)
DEFAULT_DIRECTIONS = LEGACY_DIRECTIONS                     # 校招默认，零变化
INTERNSHIP_DIRECTIONS = LEGACY_DIRECTIONS + (PYTHON_DEV,)  # 实习模式
```

- `_DIRECTION_TITLE_PATTERNS[PYTHON_DEV]`：`Python`、`Python开发`、`Python工程师`、`后端开发`、`Django/Flask/FastAPI`、`数据分析`（放二级）、`企业内部IT`（放二级）。
- 二级方向（Java/数据分析/企业IT/制造业数字化/材料研发）不进 `Direction`，进 `profile.internship.secondary_directions`，只影响排序权重，不影响放行——否则枚举会无限膨胀。
- 必须新增**负面测试**：`销售实习生`、`产品实习生`、`人力实习生` 在实习模式下仍然 `direction_out`；`Java开发实习生` 在只配 Python 方向时 `direction_out` 而不是 `gap`（方向不匹配 ≠ 条件差距）。

### 3.5 缺失条件与时长差距

- 缺失条件：`requirements.py` 抽取不到 → `RequirementValue(value=None)` → `ConditionStatus.UNKNOWN` → 岗位**保留**，`verdict` 至少 `eligible_with_gaps`。禁止任何 `default=False/0` 的兜底。
- 时长差距：`evaluation.py` 逻辑

```python
if req.duration_months.value is None:            → UNKNOWN
elif candidate_months >= req.duration_months:    → MET
elif job_has_explicit_short_term_rejection:      → GAP（如实反映，仍保留行，标 rejected 理由）
else:                                            → NEEDS_CONFIRMATION（保留，标注"是否可协商需向 HR 核实"）
```

### 3.6 TASK-003 测试清单

| 类型 | 文件 | 用例 |
|---|---|---|
| 单元 | `tests/internship/test_classification.py` | 12 类判定的正例/负例/未知例（表驱动） |
| 单元 | `tests/internship/test_requirements.py` | 抽取"3个月/每周3天/2028届/本科及以上/西安"，缺字段→None，不得瞎填 |
| 单元 | `tests/internship/test_evaluation.py` | 四状态矩阵；时长 2<3 且无显式拒绝→`needs_confirmation` 且 `verdict != rejected` |
| 参数化 | `tests/test_internops_title_mode.py`（扩你的文件） | mode × (title,job_type,jd) 矩阵；保留你现有 4 个用例不动 |
| 兼容性 | `tests/test_internops_campus_compat.py` | 对 `test_title_policy.py`/`test_matching_rule_repair.py` 的既有输入断言 campus 结果与 HEAD 完全一致（黄金快照） |
| 负面 | 同上 | 社招/销售/产品/博士限定 在实习模式下仍被拒，且拒绝理由可解释 |
| 回归 | `tests/test_internops_baseline.py`（你的未跟踪文件） | `Python开发工程师` 那条断言**必须改**，改动要在提交信息里显式说明"补齐方向覆盖" |

---

## TASK-004：Pipeline 集成

### 4.1 要改的调用点（全部已核实）

`packages/pipeline/daily.py`：

| 行 | 现状 | 改动 |
|---|---|---|
| 1331 `_screen_title` | `screen_title_job(job, profile)` | 传入 `self.policy` |
| 3012 | 标题初筛 | 同上 |
| 3055, 3072, 3088 | JD 补全后**重复再筛 3 次** | 同上，且改为一次筛选结果复用（避免 4 次重复解析） |
| 3122 | 共享岗位再筛 | 同上 |
| 1725 `DeterministicMatcher.match` → 1735 | `_screen_job(job, profile)` | 传 policy |
| 4295 `_prepare_job_details` → 4342/4391/4401 | `_screen_job` 决定是否补 JD | 传 policy；实习模式下 `jd_incomplete` 仍触发补全 |
| 3754 `_title_first_job_model` → 3791 | cohort 硬编码 | 按 `CohortPolicy` 取值 |
| 1836 `__init__` | 无 mode 参数 | 新增 `screening_policy: ScreeningPolicy | None = None`，默认 campus（零行为变化） |

`packages/matching/service.py`：`analyze`（609）与 `analyze_title_first`（720、738）接收 policy。

### 4.2 结果字段传递（需求 3.6 的"可查看依据"）

一条岗位在库里要有四块可核查信息：

1. `track` / `internship_kind`（JobSnapshot 新列）— 它是什么
2. `internship_requirements`（JSON）— JD 到底要求什么，每条带 `raw_text`
3. `match_conditions`（JSON，`MatchEvaluation` 序列化）— 逐条件 met/gap/unknown/needs_confirmation
4. `JobAnalysisSnapshot.advantages/gaps/summary/evidence` — LLM 的解释，**不允许覆盖 1~3**

职责边界（面试重点）：规则层负责"招聘事实与硬性判断"，LLM 只负责"解释与排序建议"。LLM 输出的 `gaps` 必须与规则层 `gaps` 交叉校验，出现规则层没有的新"要求"即判为幻觉、降级为 `refused` 或丢弃该条。

### 4.3 任务状态 / 断点恢复 / 异常

- checkpoint payload 里记录 `screening_policy.mode` 的摘要；恢复时若模式不一致 → 明确报错提示需要新任务，**不静默复用**上一轮 checkpoint（否则会混入另一套筛选结论）。
- `PipelineInterrupted` 路径不变；新增 metric：`rejected_by_condition` 分条件计数，写进 `DailyPipelineResult.to_dict()`，让"为什么这批没结果"可查。
- 异常：`requirements` 抽取失败不得让整批任务失败 → 捕获后该岗位 `track=unknown` + 记录 `capture_failure_reason`，继续处理下一岗。

### 4.4 TASK-004 验收证据（可复现、非编造）

`tests/test_internops_pipeline_e2e.py`：
- 注入假 crawler（fixture 返回 4 条模拟岗位）+ sqlite 临时库，跑 `DailyRecruitmentPipeline(screening_policy=internship)`
- 断言：① 1 条实习岗位入库且 `track='internship'`；② 1 条销售岗 `direction_out` 被计为 rejected 并有 reason；③ 1 条时长不足岗位**入库并带 `needs_confirmation`**；④ 同 task 再跑一次 → 不产生重复行（幂等）；⑤ 同 task 用 campus 模式跑 → 实习岗全部被拒（证明模式隔离生效）
- 输出：一个 JSON 报告 + 数据库 Dump 片段作为运行证据，写进 `docs/evidence/TASK-004/`

这一条用 fixture 完成是合规的：它证明的是**链路正确性**，不会被包装成"真实招聘数据"。真实数据在 TASK-005 单独取证。

---

## 依赖与被阻塞项（必须先解决）

| 阻塞 | 影响 | 解决方式 |
|---|---|---|
| DSH 沙箱对 `D:\work\recruitops-agent` 只读 | 无法落地任何代码/测试/迁移 | 需要你授权完整访问，或在仓库副本中开发后由你合入 |
| Playwright 浏览器未安装（`%LOCALAPPDATA%\ms-playwright` 不存在） | TASK-005 采集、TASK-007 界面 e2e 无法执行 | `python -m playwright install chromium` |
| 沙箱禁止命名管道 → 所有 `subprocess(pipe)` 失败 | Playwright/桌面相关测试在本沙箱内**不可能**通过 | 在上面的浏览器问题之外，需要在你自己的终端跑，或授权完整访问 |
| 无 PostgreSQL 实例、Docker CLI 无法连接 | TASK-008 的 PG 集成测试/容器验证 | 项目默认库是 sqlite（`config.py:120`），先用 sqlite 保证链路；PG 验证需要你启动服务或授权 |

---

## 排期建议（按依赖顺序，不并行）

1. TASK-002：`packages/internship/models.py` + `classification.py` + 单测（不碰任何现有文件，纯新增）→ 安全的第一步
2. TASK-002b：迁移 027 + 迁移测试 + `job_snapshots` 读写（触碰 storage 层，需回归 `test_storage_models.py` / `test_postgres_repository.py`）
3. TASK-003：`policy.py` + `title_policy` 参数化 + `rules.screen_job` + 六道门（触碰 matching，需跑全量匹配回归）
4. TASK-004：pipeline 线程化 + e2e 证据
5. 之后才是 TASK-005 真实来源、TASK-006 简历/LLM、TASK-007 API/UI、TASK-008 工程化

每个 TASK 完成后按你的六段格式（本轮目标 / 主要修改 / 实际验证 / 遗留问题 / 技术讲解 / Git 状态）汇报，并单独提交一次 commit（不推送）。
