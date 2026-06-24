# tests/test_setup_canary.py
def test_astrbot_importable():
    import astrbot
    from astrbot.api.platform import Platform, register_platform_adapter
    from astrbot.api.event import MessageChain
    from astrbot.core.platform.platform import PlatformStatus
    assert Platform is not None
