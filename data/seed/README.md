# 自带岗位数据（seed）

这里存放**随仓库分发的岗位快照**，目的是让任何人 `git clone` 之后不用先爬就能看到一个有内容的岗位情报站。

## 现在有什么

`jobs.jsonl.gz` —— gzip 压缩的 JSONL，约 3 MB，当前包含：

| 项目 | 数量 |
| --- | --- |
| 岗位 | 22444 |
| 公司 | 12488 |
| 校招 | 20149 |
| 实习 | 2295 |

每条岗位包含：公司、岗位名、城市、招聘类型（校招/实习）、面向届别、投递链接、截止时间、来源、首次发现时间。
**不包含 JD 正文**（见下方「为什么不打包 JD」）。

## 怎么用

不需要手动操作 —— `scripts/quickstart.py` 会在库为空时自动导入：

```bash
python scripts/quickstart.py
```

也可以单独导入或检查：

```bash
python scripts/import_seed.py            # 导入 data/seed 下全部种子
python scripts/import_seed.py --dry-run  # 只统计，不写库
```

导入是幂等的：已存在的岗位会跳过，所以叠加新种子不会产生重复。

## 怎么更新这份数据

自己采集到更新的数据后，重新导出并提交：

```bash
# 1. 采集（二选一或都跑）
python scripts/collect_offerbiu.py --types 实习 秋招 --max-pages 50
python scripts/run_local_task.py --task daily_recruitment_intelligence

# 2. 导出（默认只导在招岗位、不含 JD）
python scripts/export_seed.py

# 3. 提交
git add data/seed/jobs.jsonl.gz && git commit -m "chore(seed): refresh job catalog"
```

常用导出选项：

| 选项 | 作用 |
| --- | --- |
| `--with-jd` | 连 JD 正文一起导出（体积会大很多） |
| `--source offerbiu` | 只导出某个来源 |
| `--track internship` | 只导出实习岗 |
| `--limit 3000` | 限制条数，控制体积 |
| `--include-inactive` | 连已下架岗位一起导出 |

## 为什么不打包 JD 正文

岗位描述是第三方网站的内容，"公开可访问"不等于"允许再分发"。这个目录里的岗位只保留**元数据**（谁、在哪、什么时候、投什么链接），投递时点击链接去官网看原文。

需要 JD 正文来做简历匹配打分时，在自己的机器上跑一次企业官网采集（`daily_recruitment_intelligence` 任务），正文会存进本地数据库，不会进入这个目录。

同理：如果采集到了需要登录才能看的页面，或站点条款明确禁止转载，请不要把内容导出后提交。

## 文件格式

```text
{"kind":"meta","schema":1,"exported_at":"...","job_count":3655,"company_count":2284,...}
{"kind":"company","id":"offerbiu:某公司","name":"某公司"}
{"kind":"job","id":"...","company_id":"...","title":"...","city":"...","track":"campus","batch":"formal","cohort":2027,"cohort_status":"confirmed","detail_url":"...","source":"offerbiu","first_seen_at":"...","deadline_text":"尽快投递","recruit_type":"秋招","industry":"..."}
```

`schema` 用于版本兼容：`import_seed.py` 遇到不认识的版本会跳过该文件而不是写入错误数据。
