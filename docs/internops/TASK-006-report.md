# TASK-006 交付：实习岗位 LLM 智能匹配与真实 JD 评测

- 分支：`feature/internship-filter`
- 基线：`21c508e`
- 验证：相关子集 **375 项**通过；评测集 **8/8**；`compileall` 通过
- 边界：所有 LLM 交互用**确定性桩客户端**，未调用真实模型（无 API key、不产生费用）

---

## 1. 目标

在「确定性规则 + LLM 辅助 + 结构化结果校验」三者之间划清职责：

| 职责 | 归属 |
|---|---|
| 招聘事实（学历/届别/时长/城市是硬门槛还是优先项） | 规则层（TASK-002a/003 已建） |
| 满足/差距/未知/可协商 | 规则层 |
| 解释、排序、优势与不足、简历证据对齐 | LLM |
| 校验 LLM 没有编造招聘要求 | 本轮的逐字引用校验 |

## 2. 修改

| 文件 | 内容 |
|---|---|
| `packages/matching/service.py` | `_prompt_system`/`_prompt_user` 实习分支；`_condition_payload` 把规则结论交给模型；`ground_model_claims` 逐字引用校验；`condition_gap_lines` 把规则差距写进分析记录 |
| `evals/internship_eval.py`（新增） | 可人工核验的评测集运行器，`python -m evals.internship_eval` 打印逐条表格 |
| `tests/fixtures/internship_eval_set.json`（新增） | 5 条手写 case（含理由）+ 3 条真实腾讯 JD case |
| `tests/test_internship_llm_matching.py`（新增，10 项） | 提示词策略、条件透传、编造过滤、规则差距渲染、campus 不变 |
| `tests/test_internship_eval_set.py`（新增） | 评测集纳入回归 |

### 2.1 提示词不再声明不存在的招聘事实

实习模式的系统提示改为按实际筛选结论生成：`track=internship`、`seasons`、`conversion`、
`qualification`、已记录的条件差距；并明确"存在差距不等于岗位不合格"。
campus 分支逐字未变（有测试守护）。

用户消息新增 `internship_conditions` 块，把逐条件结论（含 `jd_excerpt`）交给模型去**解释**，
而不是让它自己推断门槛。

### 2.2 结构化输出校验：不能编造招聘要求

实习模式下，模型输出的两类断言必须能在 JD 原文中找到（忽略标点空白后逐字匹配）：

- `evidence[].jd_requirement`
- `missing_core_requirements[]`

不匹配的条目被**丢弃**并记 WARNING，不进入结果、不落库。campus 路径完全不受影响。

同时把规则层算出的条件差距（带 JD 原文）前置到 `analysis.gaps`，保证推荐结果里
"差距 + 依据"来自规则而不是模型。

## 3. 评测集

`python -m evals.internship_eval` → **8/8 通过**

| 类型 | 用例 | 期望 |
|---|---|---|
| 合成 | 2 个月 vs 要求 3 个月 | `gap` + `unknown` 可协商 + 保留 |
| 合成 | 硕士及以上 vs 本科 | `not_qualified` + 保留 |
| 合成 | JD「仅限2027届」vs 2028 届 | `not_qualified` + 保留 |
| 合成 | JD 未写任何硬条件 | `undetermined`，不产生差距 |
| 合成 | 正式岗 + "有相关实习经历者优先" | **不得判为实习** |
| 真实 | 3 条腾讯「应届实习」JD | 是实习、JD 两段齐全、至少抽到一条要求证据 |

真实 case 只断言**可先验证明的不变量**，不复制当前实现输出——否则评测集退化成回归快照。

### 3.1 评测集当场抓到的错误（真实记录）

首次运行 8 条中 1 条失败：正式岗被判成实习。排查发现是**评测脚手架的问题**——
运行器给所有 case 硬编码了 `batch="internship"`，而批次本身就是高置信实习信号。
修正后：分类器在三组输入下分别给出 `campus / internship(batch) / internship(batch)`，
行为正确。

这条记录说明评测集的价值：它暴露的是"我自己的假设写进了脚手架"，而不是"代码通过即可"。

## 4. 验证

| 项目 | 结果 |
|---|---|
| `test_internship_llm_matching.py` | 10 项通过 |
| `test_internship_eval_set.py` | 1 项通过（内部 8 个 case） |
| 相关子集（18 文件） | `total=375 failures=0 errors=0 skipped=0` |
| `python -m evals.internship_eval` | `8/8 cases match the expectation` |
| `compileall` | exit 0 |

## 5. 遗留问题

1. **未调用真实模型**：桩客户端验证的是契约与校验逻辑，不是真实模型的输出质量；
   接上真实 API 后需要重跑评测集并记录模型版本。
2. **被丢弃的模型断言没有逐条留痕**：只记 WARNING 与计数，`AnalysisRecord` 没有字段保存
   "模型说过但被丢弃"的内容。要留痕需迁移 028。
3. **`prompt_version` 未提升**：campus 文案未变刻意保留可比性；实习模式是全新提示词，
   但它也在同一个版本号下。若将来需要区分，应单独打版本。
4. **`relation="missing"` 的模型输出会触发仓库既有拒答路径**（测试中发现），
   本轮未改动该行为，只在评测与测试里避开；属于既有语义，登记。
5. **技能/方向维度未接实习条件**：`score_breakdown` 的四个维度仍是校招口径，
   没有专门的"实习可行性"维度；本轮用 `internship_conditions` 让模型在解释中体现。
6. 评测集只有 8 条，规模小；真实 case 仅覆盖腾讯一个来源。
