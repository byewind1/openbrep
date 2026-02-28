#!/usr/bin/env bash
set -euo pipefail

# 切换到脚本所在目录（项目根目录）
cd "$(dirname "$0")"

# 若存在虚拟环境则激活
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
fi

streamlit run ui/app.py
