from __future__ import annotations

from pathlib import Path
from http import HTTPStatus
from io import BytesIO
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(path) for parent in (ROOT / "packages", ROOT / "adapters", ROOT / "apps")
               for path in parent.glob("*/src")]
from verdflow.local_api import request_documents, request_values, resource_paths
from verdflow.local_api import LocalApiConfig, LocalApiServer


class LocalApiParserTests(unittest.TestCase):
    def _handler(self, body: bytes, limit: int = 1024):
        server = LocalApiServer(object(), LocalApiConfig(request_body_limit_bytes=limit),
                                studio_queries=object(), dts_live_lifecycle=object())
        handler = object.__new__(server._handler_type())
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.send_response = lambda _: None
        handler.send_header = lambda *_: None
        handler.end_headers = lambda: None
        class Connection:
            def gettimeout(self):
                return 5.0
            def settimeout(self, _value):
                pass
        handler.connection = Connection()
        return handler

    def test_early_response_consumes_only_bounded_unread_body(self):
        handler = self._handler(b'{"request":1}')
        handler._send_json(HTTPStatus.FORBIDDEN, {"error": "auth"})
        self.assertEqual(handler.rfile.read(), b"")
        self.assertIn(b"auth", handler.wfile.getvalue())
        oversized = self._handler(b"x" * 1025)
        oversized._send_json(HTTPStatus.BAD_REQUEST, {"error": "large"})
        self.assertEqual(oversized.rfile.tell(), 0)

    def test_parsed_body_is_not_read_twice_when_sending_response(self):
        handler = self._handler(b'{"request":1}')
        self.assertEqual(handler._read_json(), {"request": 1})
        handler.rfile = BytesIO(b"not-a-second-request")
        handler._send_json(HTTPStatus.OK, {"ok": True})
        self.assertEqual(handler.rfile.tell(), 0)

    def test_profile_values_preserve_omitted_clear_and_replace_intent(self):
        document = {
            "display_name": "Example", "strategy_id": "test", "strategy_version": "1",
            "target_type": "dts", "expected_generation": 0,
            "assignments": {"tree": {"candidates": [{"asset_id": "oak", "weight": 1}]}},
        }
        missing = request_documents.role_binding_profile_save_request(document)
        self.assertFalse(missing.replace_strategy_parameters)
        self.assertIsNone(missing.strategy_parameters)
        cleared = request_documents.role_binding_profile_save_request({**document, "strategy_parameters": None})
        self.assertTrue(cleared.replace_strategy_parameters)
        self.assertIsNone(cleared.strategy_parameters)
        values = {"spacing": 4}
        replaced = request_documents.role_binding_profile_save_request({**document, "strategy_parameters": values})
        self.assertTrue(replaced.replace_strategy_parameters)
        self.assertEqual(replaced.strategy_parameters, values)
        self.assertNotIn("strategy_parameters", document)

    def test_profile_path_is_single_resource_not_version_lineage(self):
        self.assertEqual(resource_paths.profile_resource("/api/v1/role-binding-profiles/one%20profile"), "one profile")
        self.assertIsNone(resource_paths.profile_resource("/api/v1/role-binding-profiles/one/versions/1"))
        self.assertEqual(resource_paths.profile_action_resource("/api/v1/role-binding-profiles/one/sync-catalog", "sync-catalog"), "one")

    def test_query_values_reject_nonfinite_duplicate_and_multiple_values(self):
        with self.assertRaises(ValueError):
            request_values.query_float({"min_x": ["nan"]}, "min_x")
        with self.assertRaises(ValueError):
            request_values.query_values({"ids": ["a,b", "a"]}, "ids")
        with self.assertRaises(ValueError):
            request_values.query_required_text({"id": ["a", "b"]}, "id")

    def test_preview_parser_keeps_provider_resolution_out_of_transport_inputs(self):
        with self.assertRaises(ValueError):
            request_documents.plant_preview_output_request({"elevation_provider_id": "custom"})
        with self.assertRaises(ValueError):
            request_documents.plant_preview_output_request({"offset": [1, 2]})
        request = request_documents.plant_preview_output_request({"offset": [1, 2, 3]})
        self.assertEqual(request.offset, (1, 2, 3))


if __name__ == "__main__":
    unittest.main()
