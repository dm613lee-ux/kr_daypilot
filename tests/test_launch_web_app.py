from __future__ import annotations

import unittest
from unittest.mock import patch

from kr_precision_backtest import launch_web_app


class LaunchWebAppTest(unittest.TestCase):
    def test_choose_port_skips_stale_kr_daypilot_servers(self) -> None:
        def is_current(_host: str, port: int) -> bool:
            return port == 8767

        def is_healthy(_host: str, port: int) -> bool:
            return port in {8765, 8766, 8767}

        with (
            patch.object(launch_web_app, "server_is_current", side_effect=is_current),
            patch.object(launch_web_app, "health_ok", side_effect=is_healthy),
            patch.object(launch_web_app, "port_responds", return_value=True),
        ):
            port, stale_ports = launch_web_app.choose_port("127.0.0.1", 8765)

        self.assertEqual(port, 8767)
        self.assertEqual(stale_ports, [8765, 8766])

    def test_choose_port_uses_next_free_port_after_stale_server(self) -> None:
        def is_healthy(_host: str, port: int) -> bool:
            return port == 8765

        def responds(_host: str, port: int) -> bool:
            return port == 8765

        with (
            patch.object(launch_web_app, "server_is_current", return_value=False),
            patch.object(launch_web_app, "health_ok", side_effect=is_healthy),
            patch.object(launch_web_app, "port_responds", side_effect=responds),
        ):
            port, stale_ports = launch_web_app.choose_port("127.0.0.1", 8765)

        self.assertEqual(port, 8766)
        self.assertEqual(stale_ports, [8765])


if __name__ == "__main__":
    unittest.main()
