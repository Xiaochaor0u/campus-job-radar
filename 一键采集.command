#!/bin/bash
# 一键采集（macOS）：双击运行。中文提示由 Python 输出，这里只负责找到解释器。
cd "$(dirname "$0")" || exit 1

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "没找到 python3。先装 Python 3.11+（https://www.python.org/downloads/），再双击本文件。"
    read -n 1 -s -r -p "按任意键关闭…"
    exit 1
  fi
  echo "第一次运行：正在创建环境并安装依赖，需要几分钟…"
  python3 -m venv .venv || exit 1
  .venv/bin/python -m pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple || exit 1
fi

"$PY" scripts/easy_collect.py
echo ""
read -n 1 -s -r -p "按任意键关闭…"
