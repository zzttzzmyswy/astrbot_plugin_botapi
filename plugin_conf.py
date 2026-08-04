# plugin_conf.py — 全局插件配置单例（账户数据源 + host/port）
# 账户数据（tokens/bindings/sessions）必须存这里（全局），不能存 botapi 平台条目——
# AstrBot BotConfigService.update_bot 会用 WebUI 表单整体覆盖平台条目，导致账户丢失。
# 动态键映射（bindings/sessions）用 list 包裹：check_config_integrity 会剔除 dict 动态键，
# 但 list 元素完全保留（已实测）。
import json
import os
from pathlib import Path

_conf = None


def _load_schema():
    p = Path(__file__).parent / "_conf_schema.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8-sig"))
    return {}


def load_plugin_conf():
    """返回全局插件配置单例（AstrBotConfig 是 dict 子类）。首次调用创建并读盘。"""
    global _conf
    if _conf is None:
        from astrbot.core.utils.astrbot_path import get_astrbot_config_path
        from astrbot.core.config.astrbot_config import AstrBotConfig
        conf_path = os.path.join(get_astrbot_config_path(),
                                 "astrbot_plugin_botapi_config.json")
        # AstrBotConfig 在配置文件缺失时会 save_config 落盘默认配置；若 data/config 目录
        # 不存在（未跑 `astrbot init` 的测试环境）会 FileNotFoundError，这里先确保目录存在。
        os.makedirs(os.path.dirname(conf_path) or ".", exist_ok=True)
        _conf = AstrBotConfig(config_path=conf_path, schema=_load_schema())
    return _conf


def reset_plugin_conf():
    """清空单例（测试/配置重载用）。"""
    global _conf
    _conf = None


def save():
    try:
        load_plugin_conf().save_config()
    except Exception:
        pass


def get_host():
    """返回插件配置 host 原文（空/缺省为 falsy，供 __init__ 的
    get_host() or legacy_host or "0.0.0.0" 回退链判断——注意 get_host 本身不再默认填值）。"""
    return load_plugin_conf().get("host")


def get_port():
    """返回插件配置 port 原文（空/0/缺省为 falsy，供 __init__ 回退链判断）。"""
    return load_plugin_conf().get("port")


def get_tokens():
    return list(load_plugin_conf().get("tokens") or [])


def set_tokens(tokens):
    load_plugin_conf()["tokens"] = list(tokens)


def get_bindings():
    return list(load_plugin_conf().get("bindings") or [])


def set_bindings(bindings):
    load_plugin_conf()["bindings"] = list(bindings)


def get_sessions_map():
    """返回 {token: [会话对象]}。"""
    out = {}
    for item in load_plugin_conf().get("sessions") or []:
        if isinstance(item, dict) and item.get("token"):
            out[item["token"]] = list(item.get("list") or [])
    return out


def set_sessions_map(sessions_map):
    load_plugin_conf()["sessions"] = [
        {"token": tok, "list": list(lst)} for tok, lst in sessions_map.items()
    ]
