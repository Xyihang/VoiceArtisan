"""回归测试：版本号单一来源（HEALTH-1 H2）。

`VERSION.json` 是唯一版本源：`app.__version__`、`/docs` 的 OpenAPI version、MCP `serverInfo.version`
都必须与之一致。历史上三者分别是 1.1.10 / 1.1.8 / 1.0.0 —— 改版本时漏改一处就对外报错版本。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import app  # noqa: E402


class VersionConsistencyTests(unittest.TestCase):
    def test_package_version_matches_version_json(self) -> None:
        expected = json.loads((PROJECT / "VERSION.json").read_text(encoding="utf-8"))["version"]
        self.assertEqual(
            app.__version__, expected,
            "app.__version__ 与 VERSION.json 不一致：改版本时必须同时改两处",
        )

    def test_openapi_version_uses_package_version(self) -> None:
        from app.main import create_app

        self.assertEqual(create_app().version, app.__version__)

    def test_mcp_server_info_uses_package_version(self) -> None:
        from app import mcp_server

        self.assertEqual(mcp_server.__version__, app.__version__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
