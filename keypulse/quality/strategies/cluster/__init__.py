from keypulse.quality.registry import StrategyRegistry
from keypulse.quality.strategies.cluster.s001_reject_empty import S001RejectEmpty
from keypulse.quality.strategies.cluster.s002_reject_numeric_or_separator import (
    S002RejectNumericOrSeparator,
)
from keypulse.quality.strategies.cluster.s003_reject_url_like import S003RejectUrlLike
from keypulse.quality.strategies.cluster.s004_reject_file_path import S004RejectFilePath
from keypulse.quality.strategies.cluster.s005_reject_secret_pattern import (
    S005RejectSecretPattern,
)
from keypulse.quality.strategies.cluster.s006_reject_command_flag import S006RejectCommandFlag
from keypulse.quality.strategies.cluster.s007_reject_terminal_resolution import (
    S007RejectTerminalResolution,
)
from keypulse.quality.strategies.cluster.s008_reject_insufficient_content import (
    S008RejectInsufficientContent,
)
from keypulse.quality.strategies.cluster.s009_reject_command_and_title_slug import (
    S009RejectCommandAndTitleSlug,
)


def register_cluster_strategies(registry: StrategyRegistry) -> None:
    registry.register(S001RejectEmpty())
    registry.register(S002RejectNumericOrSeparator())
    registry.register(S003RejectUrlLike())
    registry.register(S004RejectFilePath())
    registry.register(S005RejectSecretPattern())
    registry.register(S006RejectCommandFlag())
    registry.register(S007RejectTerminalResolution())
    registry.register(S008RejectInsufficientContent())
    registry.register(S009RejectCommandAndTitleSlug())


__all__ = ["register_cluster_strategies"]
