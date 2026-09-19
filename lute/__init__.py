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

__version__ = "3.13.0"

# 静态资源的缓存失效不再靠任何手写的版本串：模板里一律用 vstatic() /
# vstatic_js()（见 lute/utils/static_assets.py），?v= 直接取文件内容哈希，
# 改文件即自动换 URL。原 ASSET_CACHE_BUST 常量已随之删除。
