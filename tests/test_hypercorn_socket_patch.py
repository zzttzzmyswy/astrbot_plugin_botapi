# tests/test_hypercorn_socket_patch.py
"""热重启修复回归：监听 socket 必须带 FD_CLOEXEC，execv 时才不会成为孤儿。

背景：hypercorn 为多 worker 在 _create_sockets 里 set_inheritable(True)（去 FD_CLOEXEC），
AstrBot 热重启 os.execv 会把该 fd 带进新进程 → 6186 端口被旧 socket 占死、服务起不来。
main.py import 时 monkey-patch 恢复 FD_CLOEXEC。本测试验证补丁生效且行为正确。
"""

import socket
from unittest import mock

import hypercorn.config as hc


def test_main_patch_installed():
    """main 模块加载后，hypercorn Config._create_sockets 应被替换为补丁函数。"""
    from astrbot_plugin_botapi import main  # noqa: F401  触发补丁安装

    import astrbot_plugin_botapi.main as main_mod

    assert hc.Config._create_sockets is not main_mod._orig_create_sockets
    assert hc.Config._create_sockets is main_mod._patched_create_sockets


def test_patch_restores_fd_cloexec():
    """补丁应把 hypercorn 设为 inheritable(True) 的监听 socket 恢复为 False（FD_CLOEXEC）。"""
    from astrbot_plugin_botapi import main  # noqa: F401  触发补丁安装

    import astrbot_plugin_botapi.main as main_mod

    def _fake_orig(self, binds, type_=socket.SOCK_STREAM):
        s = socket.socket(socket.AF_INET, type_)
        s.set_inheritable(True)  # 模拟 hypercorn 去掉 FD_CLOEXEC
        return [s]

    cfg = hc.Config()
    with mock.patch.object(main_mod, "_orig_create_sockets", _fake_orig):
        socks = main_mod._patched_create_sockets(cfg, ["127.0.0.1:0"])
        try:
            assert len(socks) == 1
            assert socks[0].get_inheritable() is False
        finally:
            socks[0].close()
