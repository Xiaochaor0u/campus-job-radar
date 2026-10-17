# TASK-002a 交付：数据模型修正（已实施）

- 分支：`feature/internship-filter`
- 基线：`23e956a`（TASK-002 成果保留，未回退）
- 本轮性质：**只改 `packages/internship/` 与对应测试**；未触碰生产 Pipeline、未改数据库、未推送
- 验证：新增包 **83 项**测试全绿；业务核心回归 **359 项**全绿；`compileall` 退出码 0；`check_migrations` 通过（26 个文件）

---

## 1. 本轮目标与处理方式

| 审查意见 | 处理 | 落地位置 |
|---|---|---|
| ① 实习类型混合了不同维度 | 拆成三个正交事实：`track` / `seasons[]` / `conversion_opportunity` | `models.py`、`classification.py` |
| ② 不能把学历设为唯一硬淘汰 | 引入 `RequirementStrength`（由 JD 措辞决定）；资格判断与保留策略分离 | `requirements.py`、`evaluation.py`、`models.py` |
| ③ 条件差距与可协商性混为一谈 | `ConditionStatus` 只表示比较结果；`Negotiability` 单独表示可协商性；`confirmation_required` 由两者派生 | `models.py`、`evaluation.py` |
| ④ 检查 3284 行是否重复/过度封装 | 删除 4 类重复实现与 1 处死代码（详见 §3） | `values.py`（新增共享层） |

---

## 2. 三处模型修正

### 2.1 实习类型：从"一个枚举"改为"三个正交事实"

**修正前**

```python
class InternshipKind(StrEnum):
    DAILY, SUMMER, WINTER, CONVERTIBLE, UNSPECIFIED   # ← 季节与转正混在一个枚举里
```

一个"可转正的暑期实习"只能记成 `CONVERTIBLE` 或 `SUMMER` 之一，信息必然丢失。

**修正后**

```python
class Track(StrEnum):            CAMPUS / INTERNSHIP / SOCIAL / UNKNOWN
class InternshipSeason(StrEnum): SUMMER / WINTER / DAILY / UNSPECIFIED
class ConversionOpportunity(StrEnum): YES / NO / UNKNOWN      # ← 三态

class TrackDecision(BaseModel):
    track: Track
    seasons: list[InternshipSeason] = []          # 有证据才填，可为多个
    conversion_opportunity: ConversionOpportunity = UNKNOWN
```

实测（合成 fixture）：`"可转正暑期实习-Python开发"` →

```json
{"track": "internship", "seasons": ["summer"], "conversion_opportunity": "yes"}
```

两个属性同时保留。`seasons` 是列表，所以"暑期/寒假实习"也不会被压成一个值。

`ConversionOpportunity` 是三态而不是布尔，因为**没写"不转正"不等于"不转正"**：
`"负责接口自动化测试。"` → `unknown`；`"本岗位无转正机会。"` → `no`。否定式优先匹配，
避免 `"无转正机会"` 因为含 `"转正机会"` 而被判成 `yes`。

### 2.2 硬性资格：从"字段名决定"改为"JD 措辞决定"

**修正前**：学历低于要求恒为 `hard=True`，其余恒为软差距 —— 这是用字段名预先决定淘汰。

**修正后**：每个抽取到的条件都带 `RequirementStrength`：

```python
class RequirementStrength(StrEnum):
    MANDATORY = "mandatory"      # 明确设为资格门槛
    PREFERRED = "preferred"      # 明确只是优先
    DESCRIPTIVE = "descriptive"  # 仅仅提到
    UNKNOWN = "unknown"
```

判定只看措辞：`优先|更佳|加分` → `PREFERRED`；`仅限|必须|须|要求|及以上|不少于|至少` → `MANDATORY`；
**都没有 → `DESCRIPTIVE`**（保守默认，因此"只是提了一句"永远不会淘汰人）。

实测：

| JD 原文 | value | strength |
|---|---|---|
| `任职要求：本科及以上。` | 本科 | `mandatory` |
| `任职要求：本科优先。` | 本科 | `preferred` |
| `团队成员多为本科。` | 本科 | `descriptive` |
| `仅限2027届毕业生。` | 2027 | `mandatory` |
| `2027届优先。` | 2027 | `preferred` |
| `面向2027届毕业生。` | 2027 | `descriptive` |

资格判断只由 **`MANDATORY` 且属于资格门槛**的条件决定：

```python
QUALIFICATION_GATES = {DEGREE, COHORT_YEAR, OTHER_GATE}   # 资格类
# 时长 / 每周天数 / 到岗时间 / 城市 属于可行性，永远不产生 NOT_QUALIFIED
```

```python
class Qualification(StrEnum):
    QUALIFIED / NOT_QUALIFIED / UNDETERMINED
```

- `仅限2027届` + 2028 届候选人 → `cohort` 差距 + `mandatory_failure` → `NOT_QUALIFIED`
- `2027届优先` + 2028 届候选人 → 差距仍在，但**不是** `mandatory_failure` → `UNDETERMINED`
  （JD 没给出明确门槛，系统不下"符合资格"的结论）
- 时长差 1 个月 → **不影响资格**，只影响 `gaps` / `confirmation_required`

**资格与保留彻底分离**：

```python
class MatchEvaluation(BaseModel):
    qualification: Qualification
    retained: bool = True     # 保留策略输出；资格逻辑从不置为 False
```

`NOT_QUALIFIED` 的岗位依然 `retained=True`，带标签留在库里，由后续展示层决定怎么呈现。

### 2.3 差距与可协商性：从"一个状态"改为"两个正交字段"

**修正前**

```python
class ConditionStatus(StrEnum):
    MET / GAP / UNKNOWN / NEEDS_CONFIRMATION   # ← 把"有差距"和"能否协商"合并了
```

`needs_confirmation` 一旦使用，"已经确定存在 1 个月差距"这个事实就丢了。

**修正后**

```python
class ConditionStatus(StrEnum):   MET / GAP / UNKNOWN        # 只回答比较结果
class Negotiability(StrEnum):     NEGOTIABLE / NOT_NEGOTIABLE / UNKNOWN

class ConditionResult(BaseModel):
    status: ConditionStatus
    strength: RequirementStrength
    negotiability: Negotiability

    @property
    def confirmation_required(self) -> bool:
        return self.status is GAP and self.negotiability is UNKNOWN
```

实测（要求 3 个月 / 可实习 2 个月 / JD 未说明能否协商）：

```
duration_months   status=gap   strength=mandatory   negotiability=unknown   confirm=True
note: 可实习 2 个月，少于要求的 3 个月，明确存在 1 个月差距；JD 未说明是否可协商，需向招聘方核实
```

两个事实都在：**差距确定（`gap`）**、**可协商性未知（`unknown`）**。
`confirmation_required` 是派生量，不是替代品。

三种情形的对照：

| JD 表述 | status | negotiability | confirmation_required |
|---|---|---|---|
| 未说明能否协商 | `gap` | `unknown` | **True**（需要问 HR） |
| `不接受短期实习` | `gap` | `not_negotiable` | False（已明确，不必再问） |
| `实习时长可协商` | `gap` | `negotiable` | False |

可协商性只从 JD 原文读：`可协商|可放宽|可视情况|可沟通|时间可议|可调整|弹性`
→ `explicit_flexibilities`；`不接受短期实习|低于X个月不予考虑|…` → `explicit_rejections`。
城市差距的 `negotiability` 固定为 `not_negotiable`（城市是客观事实，不存在协商空间），
所以城市差距不会进 `confirmation_required`。

### 2.4 修正后的完整实测输出

（合成 fixture；**不是真实招聘数据**）

```python
job = {"title": "可转正暑期实习-Python开发", "city": "西安市",
       "jd_raw": "岗位职责：参与内部工具与自动化脚本开发。\n"
                 "任职要求：本科及以上，2028届优先，工作地点：西安、郑州，"
                 "能连续实习3个月以上，每周到岗3天。表现优秀可转正。"}
profile = {"degree": "本科", "internship": {"graduation": "2028-06",
           "preferred_cities": ["西安", "郑州"],
           "available_months": {"summer": 2, "winter": 1.5}, "max_days_per_week": 3}}
```

```
### classify_track
{"track": "internship", "seasons": ["summer"], "conversion_opportunity": "yes", "confidence": 0.95}

### extract_requirements（value / strength）
- degree           value='本科'   strength=mandatory
- cohort_year      value=2028     strength=preferred      # "2028届优先"
- duration_months  value=3        strength=mandatory      # "3个月以上"
- days_per_week    value=3        strength=mandatory

### evaluate_conditions
qualification = qualified | retained = True | confirmation_required = 1
- degree            status=met      strength=mandatory    negotiability=not_negotiable confirm=False
- cohort_year       status=met      strength=preferred    negotiability=not_negotiable confirm=False
- city              status=met      strength=descriptive  negotiability=not_negotiable confirm=False
- duration_months   status=gap      strength=mandatory    negotiability=unknown        confirm=True
- days_per_week     status=met      strength=mandatory    negotiability=not_negotiable confirm=False
- start_date        status=unknown  strength=descriptive  negotiability=not_negotiable confirm=False
```

注意 `duration` 是 `gap` 而 `qualification` 仍是 `qualified`：**时长差距不构成资格淘汰**，
但它进了 `confirmation_required`，等着人去问。这正是审查意见 ③ 要求的形态。

---

## 3. 代码规模审计

### 3.1 数字（`cloc` 式逐文件统计）

| 类别 | 提交 `23e956a`（TASK-002） | 现在（TASK-002a） | 变化 |
|---|---|---|---|
| 业务代码 `packages/internship/` | 1538（5 文件） | **1642（6 文件）** | +104 |
| ├ `__init__.py` | 76 | 80 | +4 |
| ├ `models.py` | 270 | 362 | +92 |
| ├ `values.py`（新增） | — | 100 | +100 |
| ├ `classification.py` | 311 | 225 | **−86** |
| ├ `requirements.py` | 349 | 349 | ±0 |
| └ `evaluation.py` | 532 | 526 | −6 |
| 测试代码 | 597（66 项） | **758（83 项）** | +161 |
| 文档 `docs/internops/` | 1149 | 见本轮新增 | — |
| 合计（业务+测试） | 2135 | 2400 | +265 |

业务代码中：空白行 276、纯注释行 31，即**实际代码约 1335 行 / 6 个模块 ≈ 每模块 220 行**。

### 3.2 删掉的重复实现（审查意见 ④）

| 重复 | 修正前 | 修正后 |
|---|---|---|
| `Mapping/属性` 取值 + 文本归一化 | `classification`、`requirements`、`evaluation` 各一份 `_field`/`_text`（3 份） | `values.field/text/compact/excerpt`（1 份） |
| **"剔除求职者自身实习经历"正则** | 两个模块各一份且**写法不同**（`经历|经验|背景|项目` vs 同结构），语义规则被复制 | `values.strip_experience_mentions`（1 份） |
| 学历等级映射 | `requirements._DEGREE_RANK` 与 `evaluation._DEGREE_ALIASES` 两套 | `values.DEGREE_RANK` + `degree_rank()`（1 套，中英别名统一） |
| 差距措辞拼接 | 时长/每周天数/到岗时间各写一遍相同 5 行分支（3 份） | `evaluation._gap_note()`（1 份） |
| 死代码 | `FLEXIBLE_CONDITIONS` 常量与其 import 均未使用 | 删除 |

其中第 2 条是**真正的风险点**而不是风格问题：两份"经历剥离"正则一旦被后人改动其一，
就会出现"分类认为是实习、抽取认为没有时长"这类自相矛盾。现在只有一处。

### 3.3 为什么代码反而增长了约 100 行

因为本轮**新增了两个维度**（`RequirementStrength`、`Negotiability`），而不是在原有结构上堆封装：

- `models.py` +92：4 个新枚举 + `ConditionResult` 的两个派生属性 + `QUALIFICATION_GATES`
- `classification.py` −86：靠共享层净减
- `evaluation.py` −6：新增可协商性分支，同时靠 `_gap_note()` 抵消

结论：**没有引入新抽象层来"展示复杂度"**，净增集中在两个被明确要求的数据维度上。
文档（1149 行）占了 TASK-002 插入量的 35%，这部分属于交付物而非代码。

---

## 4. 实际验证

证据文件：[`docs/internops/evidence/task-002a-tests.txt`](evidence/task-002a-tests.txt)（由 junit xml 统计，非人工填写）

| 命令 | 结果 |
|---|---|
| `python -m pytest -q tests/test_internship_classification.py tests/test_internship_requirements.py tests/test_internship_evaluation.py` | `total=83 failures=0 errors=0 skipped=0` |
| `python -m pytest -q <20 个业务核心文件>` | `total=359 failures=0 errors=0 skipped=0` |
| `python -m compileall -q apps packages evals scripts` | exit 0 |
| `python scripts/check_migrations.py` | exit 0，`26 file(s)` |

### 4.1 本轮新增/改写的守护性测试

| 测试 | 守护的规则 |
|---|---|
| `test_summer_and_conversion_are_independent_attributes` | 可转正 + 暑期实习两个属性同时保留 |
| `test_multiple_seasons_are_all_kept` | `seasons` 是列表，不被压成一个值 |
| `test_conversion_is_tri_state` | `无转正机会` → `no`；未提及 → `unknown` |
| `test_absence_of_conversion_statement_is_unknown_not_no` | 沉默 ≠ 否定 |
| `test_degree_strength_follows_wording`（4 参数） | `及以上/要求/优先/仅提及` 四种措辞 |
| `test_cohort_strength_follows_wording`（3 参数） | `仅限/优先/面向` 三种措辞 |
| `test_preferred_degree_does_not_override_a_requirement` | `本科及以上，硕士优先` 仍以本科为门槛 |
| `test_shortfall_is_a_gap_and_negotiability_is_separate` | **差距与可协商性正交**（核心） |
| `test_explicit_refusal_makes_the_gap_non_negotiable` | `不接受短期实习` → 不可协商 |
| `test_explicit_flexibility_makes_the_gap_negotiable` | `时长可协商` → 可协商 |
| `test_duration_never_makes_a_candidate_ineligible` | 时长差距不进 `mandatory_failures` |
| `test_mandatory_degree_gate_disqualifies_but_keeps_the_row` | **不合格 ≠ 删除**（核心） |
| `test_preferred_degree_never_disqualifies` | 优先条件不淘汰 |
| `test_no_stated_gate_is_undetermined_not_qualified` | 无门槛时不下"符合"结论 |

### 4.2 修 bug 过程中发现的两个措辞判定缺陷（真实记录）

1. **`任职要求：要求本科` 被判成 `descriptive`**
   原实现只看学历 token **之后** 8 个字符，"要求"在 token **之前**，于是漏判。
   改为前后各取窗口（前 8 / 后 6）。

2. **`本科及以上，硕士优先` 被抽成"硕士"**
   原实现看 token 之后 8 个字符，`本科` 的窗口里落进了 `硕士优先` 的"优先"，
   于是本科被误判为 `PREFERRED`，最终选中了硕士。
   改为：**偏好判定只看向后 4 个字符**（紧邻），门槛判定才用宽窗口。
   这条正是"优先条件不得覆盖真实要求"的具体实现难点。

---

## 5. TASK-002b 迁移前核查（本轮只核查，不实施）

审查意见要求"不能直接假定 027 一定是正确的下一个版本"。实测结果：

| 核查项 | 实测结果 |
|---|---|
| 迁移文件数 | **26**，编号 001–026，无缺号无重号 |
| 下一个版本号 | **027**（由 `schema_migrations` 之外的目录枚举确认，非假设） |
| 命名规范 | `^(?P<version>\d+)_[A-Za-z0-9][A-Za-z0-9_.-]*\.sql$`（`scripts/check_migrations.py:11`） |
| 静态校验 | `python scripts/check_migrations.py` → **通过，26 file(s)**；规则见 `check_migrations.py:12-24` |
| 破坏性 SQL | `DROP TABLE/DATABASE/SCHEMA`、`TRUNCATE`、`ALTER TABLE ... DROP COLUMN` **一律拒绝**（仅 `024` 有豁免） |
| 必需片段 | 全量 SQL 中必须出现 `CREATE EXTENSION IF NOT EXISTS vector`（已由 `002` 满足） |
| 应用方式 | `scripts/apply_migrations.py`，事务内按版本顺序执行 |
| 版本台账 | 表 `schema_migrations(version, name, checksum)`（`apply_migrations.py:248`） |
| **漂移检测** | 已应用的迁移若 `name` 或 `checksum` 变化 → `MigrationError`（`:279`、`:333`） |
| 默认数据库 | **PostgreSQL @ `localhost:5433`**（`apply_migrations.py:28`），非 5432 |
| 本机 PostgreSQL | 5432 与 5433 均无监听 → 本轮无法执行真实迁移 |
| 应用默认库 | `settings.database_url` 默认 **sqlite**（`packages/config.py:120`） |

**对 TASK-002b 的硬约束（先记下，实施时遵守）**：

1. **只能新增 `027_*.sql`，绝不能修改 001–026**——否则 checksum 漂移检测会直接拒绝启动。
2. 只能 `ADD COLUMN`（带 `DEFAULT`）与 `CREATE INDEX IF NOT EXISTS`，**不能用 `ALTER TABLE ... DROP COLUMN`**。
3. 新列必须可空或带默认值，保证旧行可读；不得重建表、不得回填推测值。
4. 迁移 SQL 与 `packages/storage/models.py` 必须同一次改动内一致，否则 ORM 与库结构漂移。
5. 迁移测试要在 **sqlite 临时库**上跑（本机无 PG），并单独声明 PG 未验证。
6. 需要先核对 `tests/test_storage_models.py` / `tests/test_postgres_repository.py` 是否对列集合有硬断言。

**待你确认后再动的设计草案**（不实施）：
`job_snapshots` 增加 `track VARCHAR(32) NOT NULL DEFAULT 'unknown'`、
`internship_seasons JSON NOT NULL DEFAULT '[]'`、
`conversion_opportunity VARCHAR(16) NOT NULL DEFAULT 'unknown'`、
`internship_requirements JSON NOT NULL DEFAULT '{}'`、
`match_conditions JSON NOT NULL DEFAULT '{}'`、索引 `(track, city)`。
理由：`seasons` 与 `conversion` 现在都是多值/三态，必须用 JSON 或独立列，**不能塞进既有的 `batch` 列**
（`batch` 的取值域是 `formal/early/internship/unknown`，被 `job_browse` 用作过滤条件，改语义会破坏读路径）。

---

## 6. 遗留问题（不隐瞒）

| 问题 | 状态 | 说明 |
|---|---|---|
| 生产链路仍未接入 | **未变** | `packages/internship` 目前仍无生产调用点，TASK-003 处理 |
| 数据库 | 未迁移 | TASK-002b |
| PG 未验证 | 阻塞 | 本机无 PostgreSQL（5432/5433 均无监听），迁移与 PG 集成测试只能在 sqlite 上验证 |
| `strength` 判定是启发式 | 已知限制 | 依赖措辞关键词；跨句的"要求"可能被误纳入窗口。缓解：`DESCRIPTIVE` 是默认值，误判方向只会更保守 |
| 到岗时间"尽快/立即" | 恒为 `unknown` | 无法与日期比较，`unknown` 比瞎猜安全 |
| 城市匹配 | 字符串级 | 去"市"后缀；"北京/远程"等复合表述未处理 |
| `OTHER_GATE` | 定义了但未抽取 | 留给"是否接受已毕业""是否需相关实习经历"等显式门槛，TASK-003 再补 |
| 技能与技术方向 | 不在本包 | 由 `packages.matching` 提供，通过 `extra_conditions` 合并 |

---

## 7. 技术讲解（面试用）

**Q：为什么"资格判断"和"是否保留岗位"要分开？**
A：它们是两个不同的问题，答案也不同。"这位候选人是否满足该岗位的硬性资格"是可判定的业务事实；
"这条记录是否应该从库里消失"是产品/展示策略。把两者合并成 `rejected` 会让数据不可恢复：
用户换了条件、或想复盘"当时为什么没投"，记录已经没了。现在 `NOT_QUALIFIED` 只是一个标签，
`retained=True` 恒成立，展示层可以自行决定折叠还是置灰。

**Q：为什么不用布尔 `is_hard` 而要 4 值 `RequirementStrength`？**
A：布尔无法区分"明确要求"和"只是提到"。审查意见里的两个反例正好落在这两个不同格子：
`仅限2027届`（强制）与 `2027届优先`（偏好）——两者都不是"没提到"。
`DESCRIPTIVE` 作为默认值还带来一个安全性质：**任何未被识别为门槛的条件都不会淘汰人**，
误判方向是保守的。

**Q：`status=gap` 与 `negotiability=unknown` 分开存，工程上解决了什么？**
A：解决了"同一个字段承担两种语义"的经典问题。合并成 `needs_confirmation` 时：
① 前端无法显示"差 1 个月"这个确定信息；② 排序无法按差距大小排；③ 数据库无法统计
"多少岗位存在时长差距"；④ 一旦 HR 确认可以协商，也没法只更新可协商性而不重算差距。
拆开后 `confirmation_required` 是纯派生量，`status` 永远只描述比较结果。

**Q：三态枚举在数据库里怎么存？**
A：按字符串存（`StrEnum` 天然如此），`NOT NULL DEFAULT 'unknown'`。好处是新增态不需要改类型，
迁移只加默认值；代价是失去了数据库层的枚举约束——所以校验放在应用层的 pydantic 模型里，
并在读取时把未知字符串降级为 `unknown`（TASK-002b 里会这么写）。

**Q：`seasons` 为什么用列表？**
A：因为"暑期/寒假均可"是真实存在的写法，用单值会丢掉一半信息。用列表的代价是查询要处理
JSON 包含语义，所以 `job_browse` 的筛选会按 `track` + 城市过滤，季节筛选在应用层做（数据量小，
避免引入 JSON 索引复杂度）。
