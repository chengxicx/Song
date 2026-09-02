"""
Version info.

Lute follows the version numbers at
https://packaging.python.org/en/latest/specifications/version-specifiers/#version-specifiers

e.g.

3.0.0a1.dev1
3.0.0a1
3.0.0b1
3.0.0

The version needs to be included in Lute itself, because Lute displays
it in the application version screen.

Flit pulls into the pyproject.toml using "dynamic".
"""

__version__ = "3.11.1"

# 独立于显示版本的资源缓存号：改动 CSS/JS 时递增，强制浏览器重新拉取，而不影响 About 页面展示的版本号。
ASSET_CACHE_BUST = "asset-3.11.1-4"
