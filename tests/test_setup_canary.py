# tests/test_setup_canary.py
def test_astrbot_importable():
    import astrbot
    from astrbot.api.event import MessageChain
    from astrbot.core.platform.platform import PlatformStatus
    assert MessageChain is not None
