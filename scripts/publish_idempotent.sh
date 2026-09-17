#!/usr/bin/env bash
# 发布单个包到 PyPI,但先查该版本是否已存在 —— 发布流水线因此可以安全重跑。
#
# PyPI 不允许覆盖同名版本:任何一次重跑(某个平台二进制失败、官网重部署失败、
# 手动补发)若直接 `uv publish`,都会在 pypi job 上炸掉并掩盖真正的问题。
#
# 环境变量:PACKAGE(orzmc / orzmc_app)、UV_PUBLISH_TOKEN。
set -euo pipefail

package="${PACKAGE:?PACKAGE 未设置}"
version="$(sed -n 's/^__version__ = "\([^"]*\)".*/\1/p' orzmc/orzmc/version.py)"
if [ -z "$version" ]; then
  echo "::error::无法从 orzmc/version.py 读取版本号"
  exit 1
fi

if curl -fsS "https://pypi.org/pypi/${package}/${version}/json" >/dev/null 2>&1; then
  echo "${package} ${version} 已在 PyPI 上,跳过发布(重跑发布流水线是幂等的)"
  exit 0
fi

echo "发布 ${package} ${version} 到 PyPI"
uv publish "dist/${package}-*"
