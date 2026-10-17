# TASK-007 交付：实习岗位工作台与前后端交互

- 分支：`feature/internship-filter`
- 基线：`8525907`
- 验证：相关子集 **332 项**通过；`compileall` / `check_migrations` 通过
- 未启用浏览器：Playwright Chromium 下载未完成，因此界面验证是**静态资产断言 + 接口契约测试**，
  不是真实浏览器点击测试

---

## 1. 决策（上轮提问的默认选择）

| 问题 | 选择 | 理由 |
|---|---|---|
| 界面形态 | **复用现有 `apps/web` 单文件工作台**，新增一个"实习岗位"导航项 | 不改框架、不新建页面；浏览、分页、排序、筛选、搜索全部复用 |
| 接口 | **复用 `/api/jobs/browse`**，用 `track=internship` | 接口在 TASK-004 已支持；避免为同一份数据维护两个端点 |

## 2. 修改

| 文件 | 内容 |
|---|---|
| `apps/web/index.html` | 新增导航项 `data-view="jobs" data-job-nav-mode="internship"`，文案「实习岗位」 |
| `apps/web/app.js` | `jobBrowseQuery()` 在实习模式下追加 `track=internship`；岗位卡片在 `job.track === "internship"` 时显示「实习 · 夏季 · 可转正」元信息 |
| `packages/repositories/job_browse.py` | 新增 `city` 过滤（多城市字符串用 contains 匹配，转义 LIKE 通配符） |
| `packages/repositories/postgres.py` | `browse_jobs(city=…)` 透传；`_job()` 补齐 5 个实习字段，使 `/api/jobs/{id}` 返回完整证据 |
| `apps/api/main.py` | `/api/jobs/browse` 增加 `city` 查询参数 |
| `tests/test_internship_workbench.py`（新增，6 项） | 实习视图只返回实习岗、城市过滤、详情带证据、界面的三处静态接线 |

## 3. 用户能完成的流程

| 能力 | 实现 |
|---|---|
| 查询实习岗位 | 导航「实习岗位」→ `GET /api/jobs/browse?track=internship` |
| 按城市筛选 | `city=` 参数（已加） |
| 查看详细 JD 与原始链接 | 卡片已有「岗位详情」外链；`GET /api/jobs/{id}` 返回 `jd_raw` + `detail_url` |
| 查看匹配依据 | 详情返回 `match_conditions`（逐条件状态、可协商性、JD 原文）与 `analysis.gaps`（TASK-006 已把规则差距前置） |
| 收藏/投递状态 | 复用既有 `/api/local-ui/applications`（新增/改状态），本轮未改动 |
| 实习类型/季节/转正 | 卡片元信息 + 详情字段 |
| 不自动投递 | 未新增任何自动提交路径 |

## 4. 验证

| 项目 | 结果 |
|---|---|
| `test_internship_workbench.py` | 6 项通过 |
| 相关子集（21 文件） | `total=332 failures=0 errors=0 skipped=0` |
| `compileall` | exit 0 |
| `scripts/check_migrations.py` | exit 0，27 files |

**没有做到的**：真实浏览器端到端（点击导航 → 看到列表 → 打开详情）。Chromium 下载未完成，
所以本轮只证明"接口契约正确 + 资产里确有接线"，不能声称界面已在浏览器中跑通。

## 5. 遗留问题

1. **未做浏览器级 UI 验证**（无 Chromium）。补装后应加一条 Playwright 用例：切到实习视图 →
   列表只出现实习岗 → 打开详情看到条件依据。
2. **前端未展示逐条条件**：详情页目前靠 `analysis.gaps` 显示差距，`match_conditions` 的
   结构化条件（状态/可协商性/JD 原文）只由接口返回，尚未在界面上渲染成"满足/差距/未知/待核实"四栏。
3. `city` 过滤是子串匹配，适合"深圳总部、北京"这类多值城市，但会命中"西安"与"西安市"以外的
   同名字段；若要做精确城市树需要额外数据。
4. 未做按实习季节/是否可转正的 SQL 过滤（`internship_seasons` / `conversion_opportunity`
   仍只在应用层可读）。
5. 采集任务启动/进度仍走既有任务接口，本轮没有新增"一键采集实习岗"的入口。
