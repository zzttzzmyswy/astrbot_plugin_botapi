# tests/test_setup_canary.py
def test_astrbot_importable():
    import astrbot
    from astrbot.api.event import MessageChain
    from astrbot.core.platform.platform import PlatformStatus
    assert MessageChain is not None


def test_botapi_platform_registration_has_nonempty_template():
    """botapi 注册为平台适配器，且模板非空。

    回归：v3.0.4 曾传 default_config_tmpl={}，被 AstrBot config_service 的
    `if not platform.default_config_tmpl: continue` 跳过，新建平台下拉看不到 botapi。
    模板必须含 type/enable/id（register 装饰器只在模板非空时自动注入）。
    """
    import astrbot_plugin_botapi.adapter  # noqa: F401  触发注册装饰器执行
    from astrbot.core.platform.register import platform_registry

    matched = [p for p in platform_registry if p.name == "botapi"]
    assert matched, "botapi 未注册为平台适配器"
    pm = matched[0]
    assert pm.default_config_tmpl
    tmpl = pm.default_config_tmpl
    assert tmpl.get("type") == "botapi"
    assert tmpl.get("id") == "botapi"
    assert "enable" in tmpl
