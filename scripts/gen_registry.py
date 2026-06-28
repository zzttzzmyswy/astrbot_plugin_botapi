#!/usr/bin/env python3
"""生成自定义插件源 registry.json + registry-md5.json。

AstrBot 的「插件管理 → 插件源」可加自定义源 URL（指向 registry.json 的 raw 地址），
之后在该源里一键安装/升级本插件。AstrBot 用 registry-md5.json 判断缓存是否过期：
md5 变了才重新拉 registry.json，所以每次发版后跑一次本脚本、提交两个文件即可。

用法：
    python scripts/gen_registry.py
    git add registry.json registry-md5.json
    git commit -m "chore: 更新自定义插件源 registry"
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_metadata() -> dict:
    """metadata.yaml 是扁平 key: value，手动解析（不依赖 PyYAML）。"""
    m = {}
    for line in (ROOT / "metadata.yaml").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        m[k.strip()] = v.strip().strip('"').strip("'")
    return m


def main() -> None:
    md = parse_metadata()
    key = md["name"].replace("_", "-")  # marketplace_name：下划线→连字符
    entry = {
        "display_name": "BotAPI 移动端适配器",
        "desc": md["desc"],
        "author": md["author"],
        "repo": md["repo"],
        "tags": ["platform", "botapi", "移动端", "sse"],
        "social_link": "",
        "stars": 0,
        "version": md["version"],
        "astrbot_version": md["astrbot_version"],
        "support_platforms": ["botapi"],
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    content = json.dumps({key: entry}, ensure_ascii=False, indent=2) + "\n"
    (ROOT / "registry.json").write_text(content, encoding="utf-8")
    digest = hashlib.md5(content.encode("utf-8")).hexdigest()
    (ROOT / "registry-md5.json").write_text(
        json.dumps({"md5": digest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote registry.json ({len(content)} bytes, md5={digest})")
    print(f"source URL: https://raw.githubusercontent.com/"
          f"{md['repo'].split('//github.com/')[1]}/main/registry.json")


if __name__ == "__main__":
    main()
