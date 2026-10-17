# TASK-002 交付：实习业务模型设计（已实施）

> **注意：本文记录的是 TASK-002 时点的设计，其中 `InternshipKind`、`MatchVerdict`、
> `needs_confirmation` 三项已在 TASK-002a 被修正（拆成 `seasons` + `conversion_opportunity`、
> `Qualification`、以及 `ConditionStatus.GAP` + `Negotiability`）。**
> 现行设计以 [`TASK-002a-report.md`](TASK-002a-report.md) 为准；本文保留作为演进记录。

- 分支：`feature/internship-filter`
- 交付形态：**纯新增**，未修改任何既有文件（`packages/internship/` 为新包，`tests/test_internship_*.py` 为新文件）
- 新增代码量：`packages/internship/` 4 个模块 + 1 个 `__init__`，3 个测试文件
- 验证：新增 **66 项**测试全绿；业务核心回归 **359 项**全绿；`compileall` 通过

---

## 1. 本轮目标

原项目的筛选模型只认识「校招 / 非校招」，实习相关的事实被分散在两处互不等价的实现里：

| 事实 | 实现 A | 实现 B |
|---|---|---|
| 是否实习 | `packages/matching/rules.py:194 internship_reason` | `packages/recruitment_core/job_filters.py:210 internship_reason` |
| 招聘通道 | — | `job_filters.py:299 recruitment_track`（兜底 `"formal"`） |
| 学历门槛 | 无（只有"是否博士限定"） | 无 |
| 实习时长 / 每周到岗 / 到岗时间 | **完全没有** | **完全没有** |

所以 TASK-002 解决三件事：

1. 把"这条招聘信息属于哪条轨道"收敛成**一个**实现；
2. 把实习岗位特有的结构性条件（时长 / 每周到岗 / 到岗时间 / 届别 / 学历 / 城市）变成**可抽取、可审计、可判断**的结构化数据；
3. 定义「未知」「待核实」两种状态，使系统不会把"JD 没写"或"也许能协商"当成"不符合"。

---

## 2. 字段设计

### 2.1 两条轴，而不是一个扁平枚举

```python
class Track(StrEnum):            # 轨道：所有门只需要回答这一个问题
    CAMPUS = "campus"
    INTERNSHIP = "internship"
    SOCIAL = "social"
    UNKNOWN = "unknown"

class InternshipKind(StrEnum):   # 实习子类：只在 Track==INTERNSHIP 时有意义
    DAILY = "daily"
    SUMMER = "summer"
    WINTER = "winter"
    CONVERTIBLE = "convertible"
    UNSPECIFIED = "unspecified"
```

**为什么不做一个 8 值的平铺枚举**（`intern_daily` / `intern_summer` / `formal_campus` / `social` …）：
平铺枚举会让每个判断点都变成"N 选一"，而 pipeline 里其实有 6 处门只需要知道"是不是实习"。
将来再加一个维度（远程/线下、转正/不转正）就是乘法膨胀。拆成两级后，**新增子类只改一处，门一个都不用动**。

### 2.2 条件与状态

```python
class ConditionName(StrEnum):
    DEGREE = "degree"                 # 学历
    COHORT_YEAR = "cohort_year"       # 毕业届别
    CITY = "city"                     # 工作城市
    DURATION_MONTHS = "duration_months"   # 最短连续实习月数
    DAYS_PER_WEEK = "days_per_week"       # 每周到岗天数
    START_DATE = "start_date"             # 到岗时间
    DIRECTION = "direction"               # 技术方向（由 packages.matching 提供）
    OTHER_GATE = "other_gate"             # 其他明确门槛

class ConditionStatus(StrEnum):
    MET = "met"                          # 明确满足
    GAP = "gap"                          # 明确差距
    UNKNOWN = "unknown"                  # 信息不足，无法判断
    NEEDS_CONFIRMATION = "needs_confirmation"  # 有差距，可协商性未知
```

### 2.3 三态 vs 两态的建模陷阱

```python
class RequirementValue(BaseModel):
    value: Any | None = None   # None = JD 没写；UNLIMITED = 明确"不限"；其它 = 解析出的要求
    raw_text: str = ""         # 产出该值的 JD 原文片段
    source: str = "unknown"    # jd_raw / struct
    confidence: float = 0.0

UNLIMITED = "unlimited"
```

`None` 只表示"没有信息"。为了不让 `None` 同时表示"明确不限"，引入非 `None` 的哨兵值 `UNLIMITED`。
这是本模块最容易被写错的地方：如果把"学历不限"也存成 `None`，评测层就无法区分
"JD 没提学历"和"JD 说学历不限"，前者应该是 `unknown`，后者应该是 `met`。

### 2.4 顶层结构

```python
class InternshipRequirements(BaseModel):
    degree / cohort_year / cities / duration_months / days_per_week / start_date: RequirementValue
    cohort_year_preferred_only: bool      # "2028届优先" ≠ 门槛
    explicit_rejections: list[str]        # "不接受短期实习" 等原文
    evidence: list[EvidenceRef]           # 每条抽取都可追溯

class InternshipPreferences(BaseModel):   # 用户配置，来自配置文件/数据库，不写死
    graduation: str | None                # "2028-06"
    degree: str | None
    preferred_cities: list[str]
    availability: SeasonAvailability      # summer_months / winter_months / daily_months
    max_days_per_week: int | None
    earliest_start: str | None
    directions: list[str]
    secondary_directions: list[str]

class MatchEvaluation(BaseModel):
    verdict: MatchVerdict                 # eligible / eligible_with_gaps / insufficient_info / rejected
    conditions: list[ConditionResult]
    hard_failures / gaps / unknowns / needs_confirmation: list[ConditionResult]
    @property retained -> bool            # verdict is not REJECTED
```

`SeasonAvailability.months_for(kind)` 按实习子类取可用时长：暑期实习取暑假窗口、寒假实习取寒假窗口、
未指定季节的实习取两者较大值（候选人最优情况，且评估结果里会如实写明）。

### 2.5 有意不做的两个维度

| 维度 | 归属 | 理由 |
|---|---|---|
| 技术方向 | `packages.matching.classify_job_directions` + `Direction` | 已有实现且被评分/DB/前端共同消费，重复即再造一份事实 |
| 技能要求 | `DeepSeekMatchPayload.score_breakdown.required_skills` | 已有结构化评分位，且技能判断本质需要模型辅助 |

`evaluate_conditions(..., extra_conditions=[...])` 允许调用方把方向/技能类条件**合并进同一个评测结果**，
从而保持"一个岗位一份评测"的外观，而不在本包内重复实现。

---

## 3. 状态语义（唯一解释）

| 状态 | 含义 | 是否淘汰岗位 |
|---|---|---|
| `met` | JD 有明确要求，且候选人明确满足 | 否 |
| `gap` | JD 有明确要求，候选人不满足，且**该项不可协商或已显式拒绝** | 否（`hard=False`）／是（`hard=True`） |
| `unknown` | JD 没写 / 用户没配 / 表述无法解析 | **永不淘汰**，且**永不转成 gap** |
| `needs_confirmation` | 存在差距，但"能否通融"只有招聘方知道 | **永不淘汰** |

`hard=True` 的判定在本轮被刻意收窄到**只有一条**：学历低于 JD 明确要求。
理由：学历门槛在中式 JD 里是二值资格判断；而时长、每周天数、到岗时间、城市都属于
"招聘方常可豁免"的项，把它们做成硬淘汰不符合需求 3.3，也不符合"发现岗位"这个产品定位。

`verdict` 由 `_verdict()` 单一函数计算：

```
任一 hard 失败                       → rejected
全部 unknown / 无条件                → insufficient_info
至少一个 met 且其余只有 unknown      → eligible
其它（有 soft gap 或 needs_confirmation）→ eligible_with_gaps
```

---

## 4. 与旧数据结构的兼容方案

本轮**不改任何既有表结构**（迁移 027 放到 TASK-002b，单独一轮，便于隔离风险）。
兼容策略：

| 旧数据 | 处理 |
|---|---|
| `JobSnapshot.batch ∈ {formal, early, internship, unknown}` | `classification` 直接识别为校园/实习证据，不新增列 |
| `JobSnapshot.cohort / cohort_status` | 原样保留；实习岗位的届别门槛由 `RequirementValue` 表达，不覆盖旧列 |
| `JobAnalysisSnapshot.analysis_status ∈ {internship, cohort_unconfirmed, …}` | 原样保留；新状态值只在 TASK-003 引入，且为**新增枚举值**（JSON 字符串，向后兼容） |
| 无 JD / JD 为空 | 所有 `RequirementValue.value is None` → 全部 `unknown` → `insufficient_info`，不惩罚 |
| 用户未配置 | `preferences_from_profile` 返回空值 → 对应条件 `unknown`，不猜测 |

`packages.internship` 与既有代码的唯一耦合点是**入参形状**（dict 或带属性的对象都能读），
因此它可以在不修改调用方的前提下被 `pipeline/daily.py`、`matching/rules.py` 直接使用。

---

## 5. 关键输入输出（真实执行结果，非手写）

输入（合成 fixture，用于验证链路；**不是真实招聘数据**）：

```python
job = {
    "title": "Python开发实习生",
    "city": "西安市",
    "job_type": "暑期实习",
    "jd_raw": "岗位职责：参与内部工具与自动化脚本开发。\n"
              "任职要求：本科及以上，2028届优先，工作地点：西安、郑州，"
              "能连续实习3个月以上，每周到岗3天，能尽快到岗。",
}
profile = {
    "degree": "本科",
    "internship": {
        "graduation": "2028-06",
        "preferred_cities": ["西安", "郑州"],
        "available_months": {"summer": 2, "winter": 1.5},
        "max_days_per_week": 3,
        "directions": ["python_dev", "test_dev"],
    },
}
```

### 5.1 `classify_track`

```json
{
  "track": "internship",
  "internship_kind": "summer",
  "confidence": 0.95,
  "evidence": [
    {"source": "title", "signal": "internship_title", "excerpt": "Python开发实习生", "reason": "标题明确标注实习"},
    {"source": "job_type", "signal": "internship_job_type", "excerpt": "暑期实习", "reason": "招聘类型明确标注实习"},
    {"source": "jd_raw", "signal": "internship_commitment", "excerpt": "实习3个月以上", "reason": "岗位要求当前候选人连续实习"}
  ]
}
```

### 5.2 `extract_requirements`（节选）

```json
{
  "degree":          {"value": "本科", "raw_text": "本科及以上,", "source": "jd_raw", "confidence": 0.9},
  "cohort_year":     {"value": 2028, "raw_text": "本科及以上,2028届优先,工作地", "source": "jd_raw", "confidence": 0.5},
  "cohort_year_preferred_only": true,
  "cities":          {"value": ["西安"], "raw_text": "西安市", "source": "struct", "confidence": 0.9},
  "duration_months": {"value": 3, "raw_text": "实习3个月", "source": "jd_raw", "confidence": 0.9},
  "days_per_week":   {"value": 3, "raw_text": "每周到岗3天", "source": "jd_raw", "confidence": 0.9},
  "start_date":      {"value": "尽快", "raw_text": "尽快到岗", "source": "jd_raw", "confidence": 0.7},
  "explicit_rejections": []
}
```

注意 `cohort_year_preferred_only: true`：JD 写的是"2028届**优先**"，所以它**不是门槛**，
评测时降级为 `unknown` 而不是 `gap`。

### 5.3 `evaluate_conditions`

```
verdict = eligible_with_gaps | retained = True
- degree            met                  hard=False required='本科'      candidate='本科'
- cohort_year       unknown              hard=False required=2028         candidate='2028-06'
  note: JD 仅将该届别列为优先条件，不是应聘门槛
- city              met                  hard=False required=['西安']     candidate=['西安', '郑州']
- duration_months   needs_confirmation   hard=False required=3            candidate=2.0
  note: 可实习 2 个月少于要求的 3 个月；是否可以缩短实习期需向招聘方核实
- days_per_week     met                  hard=False required=3            candidate=3
- start_date        unknown              hard=False required='尽快'        candidate=None
  note: 到岗时间表述不明确，无法比较
```

这正是需求 3.3 要求的行为：
**岗位保留（`retained=True`）**，时长差距被标注为"待核实"，且没有把"JD 没写"当成"不满足"。

---

## 6. 实际验证

```powershell
# 1) 新增模块测试（66 项）
python -m pytest tests/test_internship_classification.py tests/test_internship_requirements.py `
                 tests/test_internship_evaluation.py -q
# → 66 passed

# 2) 业务核心回归（20 个文件，359 项，证明既有链路未被影响）
python -m pytest tests/test_matching.py tests/test_title_policy.py tests/test_daily_pipeline.py `
                 tests/test_title_first_pipeline.py tests/test_repository_pagination.py `
                 tests/test_storage_models.py tests/test_api.py ... -q
# → 359 passed

# 3) 全量编译
python -m compileall -q apps packages evals scripts
# → exit 0
```

测试覆盖的关键反例（这部分比正例更重要）：

| 测试 | 守护的规则 |
|---|---|
| `test_internship_lookalikes_are_not_matched[Internal Tools Engineer]` | `intern` 子串不能把 `internal` 判成实习 |
| `test_internship_lookalikes_are_not_matched[有相关实习经历者优先]` | 候选人的**过往经历**不是岗位属性 |
| `test_applicant_experience_is_not_a_requirement` | "有3个月实习经历者优先" 不能变成"要求实习3个月" |
| `test_preferred_degree_is_not_a_threshold` | "硕士优先" 不是学历门槛 |
| `test_missing_information_is_unknown_and_never_a_gap` | 全空输入 → `insufficient_info`，`gaps == []` |
| `test_duration_shortfall_is_retained_with_open_question` | 2 < 3 → `needs_confirmation` 且 `retained is True` |
| `test_duration_shortfall_with_explicit_refusal_is_a_soft_gap` | JD 明说不接受短期 → `gap` 但**仍保留** |
| `test_degree_below_threshold_is_the_only_hard_failure` | 唯一的淘汰路径 |

---

## 7. 开发过程中实际遇到的问题（真实记录）

1. **`batch="campus"` 判不出来（测试失败）**
   我按直觉写了 `batch="campus"` 的用例，跑出来是 `UNKNOWN`。
   查 `packages/domain/models.py:25` 才发现真实枚举是 `formal / early / internship / unknown`。
   改为结构化映射 `_CAMPUS_BATCH_VALUES` 后通过。
   → 教训：**入参取值必须从领域枚举里读，不能凭命名习惯猜**。

2. **城市正则把整句话当成了城市（测试失败）**
   `([^\n。；;]{2,40})` 在 `工作地点：西安，每周到岗3天，…` 上捕获了整句，切出
   `["西安", "每周到岗3天", "连续实习3", "个月以上"]`。把逗号/竖线加入排除集后修复。

3. **"实习时长" 把候选人的经历也匹配了（设计发现）**
   `实习(?:期)?…(\d+)个月` 会命中 "有3个月实习经历者优先"。
   解决：抽取前先用 `_EXPERIENCE_MENTION_RE` 剥掉"…实习经历/经验/背景/项目"片段。
   这是本轮最有价值的一个修正——它直接对应需求里"不能把非要求当要求"。

4. **区间写法 `实习3-6个月` 未处理**
   增加区间分支，取下限（最低必须满足的时长）。

5. **中文数字 `十二 个月` 解析失败**
   写了一个小转换器处理 `一~十二 / 两 / 半`。

6. **证据片段噪声** `本科及以上,20`（尾部粘上了下一句的年份）
   收紧窗口到 token 后 4 个字符，并对片段做空白压缩。

7. **提示语显示 `2.0 个月`**
   加 `_fmt()`，整数值按整数渲染。

---

## 8. 边界与已知限制（不隐瞒）

| 限制 | 影响 | 计划 |
|---|---|---|
| `start_date` 只比较"能解析出年-月"的值；"尽快/立即"一律 `unknown` | 到岗时间是常见的模糊表述 | 接受现状，`unknown` 比瞎猜安全 |
| 城市仅做字符串级匹配（"北京" vs "北京市" 已归一，但"北京/远程"未处理） | 边界城市表述可能漏配 | TASK-003 加入城市别名表 |
| 技能与方向判断不在本包 | `evaluate_conditions` 需要调用方传 `extra_conditions` | TASK-003 由 `packages.matching` 提供 |
| 尚未接入任何生产调用点 | 当前对系统行为影响为 0（与 `include_internships` 同样状态） | TASK-003/004 线程化 |
| `InternshipKind` 把"可转正"和"季节"放在同一枚举 | "可转正暑期实习" 只能记为一种 | 记 `CONVERTIBLE`，季节信息仍在证据里；后续可拆为独立布尔字段 |
| 未做数据库迁移 | 新结构目前只在内存中 | TASK-002b 迁移 027 |

---

## 9. 技术讲解（面试用）

**Q：为什么把"是否实习"抽成单独一个包？**
A：因为它被两处实现、六处调用点使用，而且判定依据（标题/批次/URL/JD 元数据/时长要求）会持续增加。
把它收敛为一个纯函数模块后，新增一条判定只需要改一处并加一个用例；而语义纠错（例如"实习经历"误判）
也只修一处。这是典型的"同一事实单点定义"。

**Q：`unknown` 和 `gap` 为什么必须分开？**
A：`gap` 会参与淘汰决策和排序，`unknown` 不会。如果把两者合并，系统就会在信息缺失时替用户做决定，
而这是招聘场景里最危险的错误（用户会错过本可以投的岗位）。同理，"是否可以协商"必须建模成
`needs_confirmation` 而不是 `gap`，因为协商性属于招聘方的事实，不是模型可以推断的结论。

**Q：为什么只有学历是 hard？**
A：因为它是二值资格判断，且 JD 通常显式写"XX及以上"。其余项（时长、每周天数、到岗时间、城市）
在中式实习招聘里经常可豁免，把它们做成硬淘汰会直接违反需求（"不能仅因为时长不足就删除岗位"）。
将来若要放开，只需把 `hard` 的赋值集中改一处——这也是把判定收敛进 `_result(..., hard=...)` 的理由。

**Q：怎么做"不编造"？**
A：三个机制叠加：① 抽取函数只返回正则实际命中的片段，`raw_text` 原样保留；
② 未命中的字段保持 `None`，没有默认值；③ 评测层对 `None` 一律产出 `unknown`。
测试里专门用"稀疏 JD"断言 `stated() == {}`，防止未来有人加了个 `default=0` 而无人发现。

**Q：pydantic 在这里的价值？**
A：`extra="forbid"` 让字段名拼写错误直接报错而不是静默丢弃；`Field(ge=0, le=7)` 把
"每周 8 天"这种明显错误在构造期拦住。相比 dataclass，它还能在 `to_payload()` 时
`model_dump(mode="json")` 直接落库，避免手写序列化产生字段漂移。
