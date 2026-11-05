#!/bin/bash
# 一键启动（macOS）：双击运行。第一次会建环境、装依赖，之后直接开站。
cd "$(dirname "$0")" || exit 1

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "没找到 python3。先装 Python 3.11+（https://www.python.org/downloads/），再双击本文件。"
    read -n 1 -s -r -p "按任意键关闭…"
    exit 1
  fi
  echo "第一次运行：正在创建环境并安装依赖，需要几分钟，请不要关闭窗口…"
  python3 -m venv .venv || exit 1
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple || exit 1
fi

echo ""
echo "网站启动后，用浏览器打开：http://127.0.0.1:8010/?view=intelligence"
echo "停止服务按 Ctrl+C。"
echo ""
"$PY" scripts/quickstart.py
echo ""
read -n 1 -s -r -p "服务已停止，按任意键关闭…"
