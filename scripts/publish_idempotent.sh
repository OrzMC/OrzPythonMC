#!/usr/bin/env bash
# 发布单个包到 PyPI,但先查该版本是否已存在 —— 发布流水线因此可以安全重跑。
#
# PyPI 不允许覆盖同名版本:任何一次重跑(某个平台二进制失败、官网重部署失败、
# 手动补发)若直接 `uv publish`,都会在 pypi job 上炸掉并掩盖真正的问题。
#
# 环境变量:PACKAGE(orzmc / orzmc_app)。认证走 OIDC,不需要任何 token。
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

# 只用 Trusted Publisher(OIDC):没有长期密钥。`--trusted-publishing always` 是**强制**
# OIDC(`automatic` 在存在 token 时仍会用 token,那样迁移是否成功无从验证),它会真的去
# upload.pypi.org 换一次 token。
if uv publish --trusted-publishing always "dist/${package}-*"; then
  echo "已通过 OIDC(Trusted Publisher)发布 ${package} ${version}"
  exit 0
fi
echo "::error::${package} 发布失败:OIDC 不可用。先跑 'gh workflow run release.yml --ref main -f check_oidc=true' 预检,它会打印 PyPI 的原始响应并点名不匹配的字段(Workflow name 必须是 release.yml、Environment name 必须是 release)。"
exit 1
