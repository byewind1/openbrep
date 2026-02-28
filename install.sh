#!/usr/bin/env bash
set -euo pipefail

# 检查 Python 3.10+
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未检测到 python3，请先安装 Python 3.10+"
  echo "   下载地址: https://www.python.org/downloads/"
  exit 1
fi

PY_VER=$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)

PY_OK=$(python3 - <<'PY'
import sys
print("1" if sys.version_info >= (3, 10) else "0")
PY
)

if [ "$PY_OK" != "1" ]; then
  echo "❌ 当前 Python 版本为 ${PY_VER}，需要 3.10+"
  echo "   请升级 Python: https://www.python.org/downloads/"
  exit 1
fi

echo "✅ 检测到 Python ${PY_VER}"
echo "📦 正在安装依赖：pip install \".[ui]\""
python3 -m pip install ".[ui]"

echo "\n✅ 安装完成！"
echo "👉 运行 ./start.command 或双击 start.command 启动"
