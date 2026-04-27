import re
from typing import Optional

APP_ALIASES = {
    "Google Chrome": "Chrome",
    "Microsoft Edge": "Edge",
    "Code": "VS Code",
    "Code - Insiders": "VS Code Insiders",
    "com.apple.Safari": "Safari",
    "com.apple.Terminal": "Terminal",
    "Obsidian.app": "Obsidian",
    "iTerm2": "iTerm",
}

TAIL_PATTERNS = [
    r'\s*-\s+[\w\s.]+$',
    r'\s*—\s+[\w\s.]+$',
    r'\s*\([A-Za-z0-9]+\)$',
]

_TAIL_REGEX = [re.compile(pattern) for pattern in TAIL_PATTERNS]


def canonicalize_app(app_name: str) -> str:
    """规范化应用名：去 .app 后缀、合并别名。
    例：'Google Chrome' -> 'Chrome'，'Code - Insiders.app' -> 'VS Code Insiders'，
        'com.apple.Safari' -> 'Safari'。空串/None 返回 ''。
    """
    if not app_name or not isinstance(app_name, str):
        return ""

    name = app_name.strip()

    if name in APP_ALIASES:
        return APP_ALIASES[name]

    name = name.removesuffix(".app")

    if name in APP_ALIASES:
        return APP_ALIASES[name]

    return name


def strip_slug_tail(title: str) -> str:
    """剥离窗口标题里常见的应用尾巴 / 浏览器标签后缀。
    例：'AGX_淘宝搜索 - Google Chrome - lang (Har)' -> 'AGX_淘宝搜索'
        '5. 导入前注意 — Obsidian' -> '5. 导入前注意'
        'README.md — keypulse' -> 'README.md'
    使用迭代剥离：识别 ' - <App>'、' — <App>'、' (Har)' 等尾巴。
    """
    if not title or not isinstance(title, str):
        return ""

    result = title.strip()

    changed = True
    while changed:
        changed = False
        for regex in _TAIL_REGEX:
            new_result = regex.sub("", result)
            if new_result != result:
                result = new_result.rstrip()
                changed = True
                break

    return result


def title_to_object_hint(title: str) -> str:
    """把规范化后的标题转成"做什么"的对象提示，用于 Pass1 prompt。
    规则：
      - 含 '.md'/'.py'/'.toml'/'.json' 等扩展名：返回 '文件 <basename>'
      - 含 'http://' / 'https://' / 'localhost'：返回 'URL <host>'
      - 包含 '设计图片'/'购车'/'立项' 等中文短语：原样返回前 30 个字符
      - 否则返回 strip_slug_tail 结果裁到 30 字
    """
    if not title or not isinstance(title, str):
        return ""

    cleaned = strip_slug_tail(title).strip()

    file_extensions = [".md", ".py", ".toml", ".json", ".txt", ".js", ".ts", ".go", ".rs", ".java"]
    for ext in file_extensions:
        if ext in cleaned:
            basename = cleaned.split("/")[-1] if "/" in cleaned else cleaned
            return f"文件 {basename}"[:50]

    if "http://" in cleaned or "https://" in cleaned or "localhost" in cleaned:
        try:
            if "://" in cleaned:
                host = cleaned.split("://")[1].split("/")[0].split(":")[0]
            else:
                host = cleaned.split("/")[0].split(":")[0]
            return f"URL {host}"[:50]
        except Exception:
            return f"URL {cleaned[:20]}"

    chinese_phrases = ["设计图片", "购车", "立项", "淘宝", "亚马逊", "eBay"]
    for phrase in chinese_phrases:
        if phrase in cleaned:
            return cleaned[:30]

    return cleaned[:30]
