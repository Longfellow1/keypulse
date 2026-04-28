from __future__ import annotations

from datetime import datetime
from typing import Iterator

from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent
from keypulse.sources.wechat_probe import probe
from keypulse.utils.logging import get_logger


LOGGER = get_logger("sources.wechat")


class WechatSource(DataSource):
    name = "wechat"
    privacy_tier = "red"
    liveness = "after_unlock"
    description = "微信本地消息（红区，需用户授权 + chatlog 工具）"

    def discover(self) -> list[DataSourceInstance]:
        result = probe()
        if not (result.chatlog_installed and result.wechat_running and result.user_authorized):
            return []
        return [
            DataSourceInstance(
                plugin=self.name,
                locator="wechat:placeholder",
                label="微信消息（红区）",
                metadata={
                    "chatlog_path": result.chatlog_path,
                    "wechat_app": result.wechat_app_path,
                    "warning": "S5a 占位 plugin，read 暂未实现",
                },
            )
        ]

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        LOGGER.warning("wechat plugin S5a is probe-only, read not yet implemented")
        return iter(())
