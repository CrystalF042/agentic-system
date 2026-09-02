"""测试期间**禁止联网**。在测试脚本最上面 `import _no_network` 即可。

## 为什么需要它

一条会悄悄联网的断言，**在两台机器上就是两个测试**。

真机上刚发生过：探针「基准取不到 → 超额不计算」在我这里绿、在她机器上红。
红的不是代码，是探针自己——它把 `bench_close=None` 同时当成"没传"和"没有"，
于是有网的机器走取数分支、无网的机器走跳过分支。**同一份断言测了两件事。**

网络还会带来另外三种麻烦，每一种都不报错：

    慢       每次跑测试都在等超时
    脏       测试输出里混进 yfinance 的告警，真正的失败被淹掉
    假绿     某条断言哪天开始靠真实行情才成立，没人会发现

所以测试环境里把 socket 焊死：任何一次真实连接都会抛
`NetworkUsedInTest`，直接指出是哪一行想联网。

## 被 try/except 吞掉怎么办

`marks._raw_hist` / `corp_actions.fetch_actions` 这类取数函数本来就
`except Exception` 兜底——它们会把这个异常当成"取不到"，
于是走到"缺价"、"等待开盘"这些**本来就该被测的降级分支**。
这正是我们想要的：降级路径不该靠拔网线才能测到。

## 逃生舱

    CIO_TEST_ALLOW_NET=1 python scripts/test_book.py

只在你**明确想跑一次真实取数**时用。默认永远是断的。
"""
from __future__ import annotations

import os
import socket


class NetworkUsedInTest(RuntimeError):
    """测试里发生了真实网络连接。"""


ALLOW = os.environ.get("CIO_TEST_ALLOW_NET") == "1"
_MSG = ("测试试图联网。测试必须自带数据（注入价格、CIO_QUANT_MOCK=1、"
        "或 use_bench=False）——靠真实行情才通过的断言，换一台机器就是另一个结果。"
        "确实要跑真实取数：CIO_TEST_ALLOW_NET=1")

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_create = socket.create_connection
_real_getaddrinfo = socket.getaddrinfo


def _blocked(*a, **k):
    raise NetworkUsedInTest(_MSG)


# **两层，缺一不可。**
#
# 第一层是 Python 的 socket —— 拦住 httpx / requests / urllib。
# 但 **yfinance 走的是 `curl_cffi`**，它在 C 层自己开连接，**完全绕过
# Python 的 socket 模块**：只焊第一层的话，测试里那句 `yf.Ticker(...)`
# 照样出网，而且拦不住还不报错——正是我们要防的那种"看起来防住了"。
#
# 第二层用代理环境变量把出口指到本机一个死端口（9 = discard，通常没人监听）：
# curl 认这几个变量，连接会立刻被拒绝而不是挂起等超时。
_DEAD_PROXY = "http://127.0.0.1:9"
_PROXY_VARS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
               "ALL_PROXY", "all_proxy")


def install() -> bool:
    """焊死。返回是否真的焊上了（逃生舱打开时返回 False）。"""
    if ALLOW:
        return False
    for v in _PROXY_VARS:
        os.environ[v] = _DEAD_PROXY
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    socket.socket.connect = _blocked
    socket.socket.connect_ex = _blocked
    socket.create_connection = _blocked

    def _gai(host, *a, **k):
        # 本地回环放行：SQLite 用不着网络，但某些库会解析 localhost。
        if str(host) in ("localhost", "127.0.0.1", "::1", ""):
            return _real_getaddrinfo(host, *a, **k)
        raise NetworkUsedInTest(_MSG + f"（试图解析 {host}）")

    socket.getaddrinfo = _gai
    return True


def restore() -> None:
    socket.socket.connect = _real_connect
    socket.socket.connect_ex = _real_connect_ex
    socket.create_connection = _real_create
    socket.getaddrinfo = _real_getaddrinfo


BLOCKED = install()
