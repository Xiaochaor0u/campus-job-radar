# TASK-002b 交付：数据库迁移 027（已实施并验证）

- 分支：`feature/internship-filter`
- 基线：`875af94`
- 新增迁移：`migrations/027_internship_track.sql`（**唯一新增**，001–026 未改动）
- 验证：SQLite 契约测试 **20 项**、全量相关子集 **462 项**、**PostgreSQL 一次性容器 23 项检查全部通过**

---

## 1. 本轮目标

把 TASK-002a 收敛后的实习分类事实落进 PostgreSQL，同时：

1. 不丢、不覆盖、不回填已有岗位记录；
2. 明确 `NULL` / `[]` / 非空数组的语义，不用 `DEFAULT '[]'` 伪造"已分析"；
3. 季节数组受枚举约束（拒绝重复、非字符串、非法枚举值）；
4. 只加有真实查询依据的索引；
5. 在**真实 PostgreSQL** 上验证，而不是只跑 SQLite。

---

## 2. 数据库结构变化

### 2.1 新增列（全部在 `job_snapshots`）

| 列 | PostgreSQL 类型 | 可空 | 默认 | 约束 |
|---|---|---|---|---|
| `track` | `VARCHAR(32)` | 否 | `'unknown'` | `CHECK IN ('campus','internship','social','unknown')` |
| `internship_seasons` | `JSONB` | **是** | 无 | `recruitops_valid_internship_seasons(...)` |
| `conversion_opportunity` | `VARCHAR(16)` | 否 | `'unknown'` | `CHECK IN ('yes','no','unknown')` |
| `internship_requirements` | `JSONB` | **是** | 无 | — |
| `match_conditions` | `JSONB` | **是** | 无 | — |

新增索引：`ix_job_snapshots_track (track)` —— **只此一个**。

### 2.2 `NULL` / `[]` / 非空数组的语义（本轮最重要的决定）

| 值 | 含义 | 谁能产生 |
|---|---|---|
| `internship_seasons IS NULL` | **尚未分析**（历史行、未进入实习管线） | 迁移后未被管线触及的所有既有行 |
| `internship_seasons = '[]'` | **已分析，JD 未给出明确季节** | 分类器判为实习但 `seasons == []` |
| `internship_seasons = '["summer"]'` | 实际识别到的季节 | 分类器命中季节证据 |

同理 `internship_requirements` / `match_conditions` 也是 **NULL = 未分析、`{}` = 已分析但无内容**。

**为什么 `track` / `conversion_opportunity` 反过来用 `NOT NULL DEFAULT 'unknown'`？**
因为 `'unknown'` 在领域里就表示"没有证据"——对一条从未被分析的历史行来说，"没有证据"正是事实，
不存在第二种状态与它混淆。而季节不同：`[]`（已分析、无季节）与 NULL（未分析）是**两种不同事实**，
一旦用 `DEFAULT '[]'` 就永久失去区分能力。这是对"按要求加默认值"的有意拒绝，理由已写进迁移注释。

### 2.3 不做回填

迁移中**没有任何 `UPDATE`**。历史行的 `track` 一律保持 `'unknown'`、JSON 列保持 NULL。
从一条校招时代的记录里猜"它其实是实习"就是在伪造分类证据。

---

## 3. 季节数组的枚举约束如何实现

`CHECK` 约束里不能写子查询，而"数组内不得重复"必须用聚合，所以约束体放在一个
`IMMUTABLE` SQL 函数里，再由 `CHECK` 调用：

```sql
CREATE OR REPLACE FUNCTION recruitops_valid_internship_seasons(value JSONB)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $fn$
    SELECT value IS NULL OR (
        jsonb_typeof(value) = 'array'
        AND value <@ '["summer", "winter", "daily"]'::jsonb
        AND jsonb_array_length(value) = (
            SELECT count(DISTINCT season)
            FROM jsonb_array_elements_text(value) AS element(season)
        )
    );
$fn$;
```

一次覆盖四件事：

1. **类型**：`jsonb_typeof = 'array'`（拒绝 `{}`、`"summer"`、数字）；
2. **枚举成员**：`<@` 包含判断，只允许 `summer/winter/daily`；
3. **非法元素类型**：`["summer", 1]` 因 `<@` 不成立被拒；
4. **重复**：长度 ≠ distinct 计数即拒绝。

**`unspecified` 故意不在允许集合里**：JD 未指名季节时存 `[]`，而不是往数组里塞一个"未指定"标签，
否则同一个事实会有两种表示。领域层的 `InternshipSeason.UNSPECIFIED` 只在读取端派生
（`TrackDecision.primary_season`），从不落库。

`scripts/apply_migrations.py` 的 `split_sql` 支持 `$fn$...$fn$` 美元引用，函数体里的分号不会被误切分
（这一点已实测，见 §5）。

**已知不对称（有意为之）**：SQLite 没有 `jsonb`，这条函数只存在于 PostgreSQL 迁移里。
SQLite 侧由 ORM 的 `@validates("internship_seasons")` 提供同等校验（去重、拒绝非法标签、
拒绝 `unspecified`、拒绝非序列）。两侧规则一致，实现方式不同，已在测试中分别覆盖。

---

## 4. 为什么只给 `track` 建索引

| 字段 | 是否有索引 | 依据 |
|---|---|---|
| `track` | **有** | 实习列表、`all` 模式的并集查询、排除社招，全部以它为谓词 |
| `internship_seasons` | 无 | 目前没有任何按季节的 SQL 过滤；JSONB 的 `<@`/`jsonb_path_exists` 已能做数据库层过滤，需要时再加 GIN 才有意义 |
| `conversion_opportunity` | 无 | 低基数，且暂无"只看可转正"的查询；低基数索引收益极小 |
| `internship_requirements` / `match_conditions` | 无 | 展示用证据，不参与过滤 |

即：**列的存在与否按真实持久化需求决定，索引按真实查询决定**。
`match_conditions` 里存的是逐条件的完整评测结果（含 `note`、`jd_excerpt`），
它更适合 JSON 而不是拆成 8 张宽表列——那会把领域模型的中间结构机械地展平，
而这些字段只用来展示，不需要 SQL 谓词。

---

## 5. 验证结果

证据文件：[`docs/internops/evidence/task-002b-tests.txt`](evidence/task-002b-tests.txt)

### 5.1 单元 / 契约测试（`tests/test_internship_migration.py`，20 项）

| 测试 | 守护的规则 |
|---|---|
| `test_orm_declares_exactly_the_new_columns` | JSON 列可空、枚举列非空有默认 |
| `test_migration_027_adds_the_same_columns_as_the_orm` | **解析 027 SQL 的 `ADD COLUMN`，与 ORM 列集合逐一对齐** |
| `test_migration_027_is_additive_only` | 无 `DROP`/`TRUNCATE`，且**没有 `UPDATE` 回填** |
| `test_migrations_are_contiguous_and_027_is_the_head` | 版本恰为 1..27，证明 001–026 未被增删 |
| `test_not_analysed_and_analysed_without_season_stay_distinct` | `NULL` vs `[]` vs `["summer"]` 三态互不混淆 |
| `test_empty_json_evidence_is_independent_of_null` | `{}` ≠ NULL |
| `test_seasons_accept_enum_members_and_dedupe_them` | 枚举成员/字符串混写去重 |
| `test_invalid_seasons_are_rejected_before_reaching_the_database`（5 参数） | 非法标签、非字符串、裸字符串 |
| `test_unspecified_must_be_expressed_as_an_empty_list` | `unspecified` 不可落库 |
| `test_enum_check_constraints_are_enforced_by_the_database`（2 参数） | CHECK 真的生效（不是只写在模型里） |

### 5.2 PostgreSQL 真实迁移验证（`scripts/verify_internship_pg_migration.py`）

流程：Docker 启动一次性 `pgvector/pgvector:pg16` → 只应用 **001–026** → 插入 3 行代表性旧数据
（含中文、引号、反斜杠、URL 参数）→ 快照 → 应用 **027** → 复核 → 删除容器。
**不接触任何既有数据库**。

```
[PASS] 001-026 applied
[PASS] seed rows inserted
[PASS] 027 applied
[PASS] existing rows preserved byte-for-byte
[PASS] row count unchanged
[PASS] new columns default to unknown/NULL without backfill
[PASS] JSON evidence columns are JSONB
[PASS] track index exists
[PASS] seasons validation function exists
[PASS] seasons accepts NULL / empty array / valid array
[PASS] seasons rejects duplicate labels / unknown label / non-string element
       / object instead of array / uppercase label
[PASS] track CHECK rejects out-of-range value
[PASS] track CHECK accepts internship
[PASS] conversion CHECK rejects out-of-range value
[PASS] no pending migrations after 027
[PASS] ledger records 27 migrations
[PASS] re-run applies nothing
RESULT: PASSED (23 checks)
```

其中"existing rows preserved byte-for-byte"是把迁移前后 12 个既有列的完整元组逐行比对，
不只看行数。

### 5.3 汇总

| 项目 | 结果 |
|---|---|
| 相关测试子集（24 文件） | `total=462 failures=0 errors=0 skipped=0` |
| PostgreSQL 迁移检查 | `23/23 PASSED` |
| `python -m compileall -q apps packages evals scripts` | exit 0 |
| `python scripts/check_migrations.py` | exit 0，`27 file(s)` |

---

## 6. 遗留问题

| 问题 | 状态 | 说明 |
|---|---|---|
| **真实存量数据未迁移** | 未执行 | PG 验证用的是新建空库 + 3 行合成数据。你的真实库（若有）我没有连接、没有迁移；迁移上线需要你在自己的库上执行 `python scripts/apply_migrations.py` |
| 迁移耗时未测量 | 未知 | `ADD COLUMN ... DEFAULT` 在 PG 11+ 不重写表，但大表上的 `ADD CONSTRAINT` 会做一次校验扫描；行数规模未知 |
| 生产链路仍未写入新列 | 未变 | 列已存在但没有任何代码写它，TASK-003/004 接入 |
| SQLite 上无 `internship_seasons` 的 DB 级约束 | 已知不对称 | 由 ORM 校验兜底；SQLite 侧的 `track`/`conversion` CHECK 是有的 |
| `ix_job_snapshots_track` 与既有 `ix_job_snapshots_cohort` 可能重复扫描 | 未优化 | 读路径尚未改造，等 TASK-004/007 有了真实查询计划再决定是否合并索引 |
| `schema_migrations` 有 checksum 漂移检测 | 约束 | **001–026 从此不可再编辑**，后续任何结构变化只能新增 028、029… |

---

## 7. 技术讲解（面试用）

**Q：为什么 `internship_seasons` 不加 `NOT NULL DEFAULT '[]'`？**
A：`NOT NULL DEFAULT '[]'` 会让所有历史行看起来"已经分析过、且没有季节"。这让"未分析"这一状态
永久消失，之后无论加多少逻辑都补不回来。三态（NULL / [] / 非空）各自对应不同事实，
只要有一个状态无法表达，后面的统计、排序、重跑判断都会失真。反过来 `track` 用 `'unknown'`
是安全的，因为 `'unknown'` 的语义就是"没有证据"，与"从未分析"重合而不是冲突。

**Q：CHECK 里为什么不能直接判重？**
A：PostgreSQL 的 `CHECK` 约束不允许子查询（`0A000: cannot use subquery in check constraint`）。
判"数组无重复"必须用 `count(DISTINCT ...)`，只能把逻辑放进 `IMMUTABLE` 函数再由 CHECK 调用。
代价是数据库里多了一个不被 ORM 管理的函数对象，因此迁移里用 `CREATE OR REPLACE` 保证可重复创建。

**Q：`JSON().with_variant(JSONB, "postgresql")` 解决了什么？**
A：同一个 ORM 声明在 PostgreSQL 上是 `JSONB`（支持 `<@`、GIN 索引、更快的路径查询），
在 SQLite 上是 `JSON`（TEXT 存储）。一份模型代码同时跑通两种数据库，测试用 SQLite，
生产用 PostgreSQL，不需要两套模型或手工 DDL 分支。

**Q：怎么保证"迁移和 ORM 不会漂移"？**
A：两条自动检查：① `test_migration_027_adds_the_same_columns_as_the_orm` 直接解析 SQL 的
`ADD COLUMN` 与 ORM 列集合比对；② `scripts/apply_migrations.py` 的 `schema_migrations`
记录 name+checksum，任何对已应用迁移的修改都会在启动时抛 `MigrationError`。

**Q：为什么用一次性容器而不是项目的 compose？**
A：项目的 `docker-compose.yml` 挂了具名卷 `recruitops-postgres`，容器删除后数据仍在——
那正好是"用户真实的库"。验证迁移必须用一个**删除即销毁**的实例，
所以用 `docker run --rm -p 127.0.0.1::5432`（随机宿主端口）+ 无卷，运行后 `docker rm -f`。
这也是"不要拿用户数据做迁移试验"的具体落地。
