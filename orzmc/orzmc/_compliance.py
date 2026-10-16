"""Minecraft Usage Guidelines 声明与联系方式（单一事实源）。

本文件由 scripts/sync_compliance.py 生成；请改父仓库 docs/compliance/compliance.json 后重新生成。
"""

from __future__ import annotations

DISCLAIMER_EN = "NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG OR MICROSOFT."
DISCLAIMER_ZH = "OrzMC 是独立的社区开源项目，与 Mojang / Microsoft 无隶属、赞助或背书关系，对本项目负责。"
TRADEMARK_ZH = "“Minecraft”名称、品牌与素材的一切权利归 Mojang / Microsoft 所有。"
CONTACT_EMAIL = "824219521@qq.com"
PRIVACY_URL = "https://orzmc.jokerhub.cn/privacy/"
WEBSITE = "https://orzmc.jokerhub.cn"


def disclaimer_line() -> str:
    """一行式声明（CLI 页脚）。"""
    return f"{DISCLAIMER_EN} {DISCLAIMER_ZH} 联系：{CONTACT_EMAIL}"
