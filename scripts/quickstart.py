"""One-command bootstrap: get a usable workbench in a single step.

What it does, in order:
  1. prepares ``config/companies.yaml`` (copies the example) if missing;
  2. imports the bundled seed catalog when the database has no postings, so a
     fresh clone opens with thousands of real positions instead of an empty UI;
  3. starts the local web workbench (unless ``--no-serve``).

Examples:
    python scripts/quickstart.py                  # 导入种子 + 启动工作台
    python scripts/quickstart.py --no-serve       # 只准备数据
    python scripts/quickstart.py --port 8010 --no-seed
    python scripts/quickstart.py --collect        # 先跑一次真实采集再启动
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.config import get_settings  # noqa: E402
from packages.storage import JobSnapshot, Storage  # noqa: E402

SEED_FILE = ROOT / "data" / "seed" / "jobs.jsonl.gz"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", type=int, default=8010, help="工作台端口")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--no-seed", action="store_true", help="不导入种子数据")
    parser.add_argument("--no-serve", action="store_true", help="只准备数据，不启动服务")
    parser.add_argument("--collect", action="store_true", help="启动前先跑一次企业官网采集（较慢）")
    parser.add_argument("--offerbiu", action="store_true", help="启动前先导入最新 OfferBiu 公告")
    return parser


def _prepare_config() -> None:
    settings = get_settings()
    target = Path(settings.companies_config)
    if target.is_file():
        return
    example = ROOT / "config" / "companies.example.yaml"
    if example.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example, target)
        print(f"[1/3] 已生成企业配置：{target}")


def _job_count() -> int:
    settings = get_settings()
    storage = Storage.from_url(settings.database_url, initialize=True)
    with storage.session() as session:
        return int(session.query(JobSnapshot).count())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _prepare_config()

    existing = _job_count()
    if args.no_seed:
        print(f"[2/3] 跳过种子导入（当前库已有 {existing} 个岗位）")
    elif existing > 0:
        print(f"[2/3] 库里已有 {existing} 个岗位，跳过种子导入（--reseed 需手动执行 import_seed.py）")
    elif not SEED_FILE.is_file():
        print(f"[2/3] 未找到种子文件 {SEED_FILE}，跳过（可自行采集：scripts/collect_offerbiu.py）")
    else:
        print(f"[2/3] 导入自带岗位数据：{SEED_FILE.name}")
        from scripts.import_seed import main as import_main

        if import_main(["--file", str(SEED_FILE)]) != 0:
            print("种子导入失败，继续启动空库。", file=sys.stderr)

    if args.collect:
        print("[2.5/3] 采集企业官网岗位（可能需要几分钟）…")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_local_task.py"),
             "--task", "daily_recruitment_intelligence"],
            check=False,
        )
    if args.offerbiu:
        print("[2.5/3] 拉取最新 OfferBiu 公告…")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "collect_offerbiu.py"),
             "--types", "实习", "秋招", "--max-pages", "10"],
            check=False,
        )

    total = _job_count()
    print(f"[3/3] 当前库中岗位：{total} 个")

    if args.no_serve:
        print("准备好后启动工作台：")
        print(f"  python -m uvicorn apps.api.main:app --port {args.port}")
        return 0

    url = f"http://{args.host}:{args.port}/?view=intelligence"
    print(f"启动工作台：{url}（Ctrl+C 停止）")
    try:
        subprocess.run(
            [
                sys.executable, "-m", "uvicorn", "apps.api.main:app",
                "--host", args.host, "--port", str(args.port),
            ],
            cwd=str(ROOT),
            check=False,
        )
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
