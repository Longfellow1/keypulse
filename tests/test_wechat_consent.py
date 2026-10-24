from penpal.sources.discover import _WECHAT_AUTHORIZE_WARNING


def test_wechat_authorize_warning_describes_probe_only_local_behavior() -> None:
    assert "仅做本机探测" in _WECHAT_AUTHORIZE_WARNING
    assert "读取尚未实现" in _WECHAT_AUTHORIZE_WARNING
    assert "不会读取或导入微信消息" in _WECHAT_AUTHORIZE_WARNING
    assert "不会生成 Daily 报告" in _WECHAT_AUTHORIZE_WARNING
    assert "不会调用云端 LLM" in _WECHAT_AUTHORIZE_WARNING
    assert "LLM 渲染时" not in _WECHAT_AUTHORIZE_WARNING
    assert "cloud-first" not in _WECHAT_AUTHORIZE_WARNING
