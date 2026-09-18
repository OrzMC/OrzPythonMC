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

# 优先走 Trusted Publisher(OIDC,没有长期密钥可泄露);失败(例如 PyPI 那边还没配好
# publisher)时回退到 API token —— 这样迁移期无论先改代码还是先配 PyPI 都不会卡发版。
# 想确认走的是哪条路:`--trusted-publishing always` 会真的去换 OIDC token,成功即 OIDC。
if uv publish --trusted-publishing always "dist/${package}-*"; then
  echo "已通过 OIDC(Trusted Publisher)发布 ${package} ${version}"
  exit 0
fi
if [ -z "${UV_PUBLISH_TOKEN:-}" ]; then
  echo "::error::${package} 发布失败:OIDC 不可用,且没有 token 可回退。请在 PyPI 项目里配置 Trusted Publisher(Workflow name=release.yml、Environment name=release),或给 workflow 配上 UV_PUBLISH_TOKEN。"
  exit 1
fi
echo "::warning::${package} 的 OIDC 发布失败,回退到 API token(Trusted Publisher 可能还没配好)"
uv publish "dist/${package}-*"
