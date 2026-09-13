from __future__ import annotations

import hashlib
import http.client
import io
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

import fetch_nrw_infrastructure as fetcher  # noqa: E402
import fetch_real_data as real_fetcher  # noqa: E402
import source_cache  # noqa: E402


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, *, content_length: int | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(payload)
        self.headers = headers or {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.headers["Content-Type"] = "application/octet-stream"

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class FailingResponse(FakeResponse):
    def __init__(self) -> None:
        super().__init__(b"partial")
        self.calls = 0

    def read(self, size: int = -1) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return super().read(size)
        raise OSError("stream interrupted")


class RangeResponse(FakeResponse):
    status = 206


def source_row(**overrides: str) -> dict[str, str]:
    row = {
        "dataset_id": "test_source",
        "url": "https://example.test/data.bin",
        "target_path": "data/raw/test/data.bin",
        "access_type": "public",
        "licence": "Test licence",
        "max_bytes": "1024",
    }
    row.update(overrides)
    return row


class InfrastructureDownloadTest(unittest.TestCase):
    def test_downloads_atomically_and_writes_checksum_provenance(self) -> None:
        payload = b"official NRW data"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = fetcher.fetch_source(
                source_row(),
                root=root,
                provenance_dir=root / "provenance",
                opener=lambda _request, timeout: FakeResponse(payload, content_length=len(payload)),
            )

            target = root / "data/raw/test/data.bin"
            downloaded = target.read_bytes()
            provenance = json.loads((root / "provenance/test_source.json").read_text(encoding="utf-8"))

        self.assertEqual(downloaded, payload)
        self.assertEqual(result["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(provenance["dataset_id"], "test_source")
        self.assertEqual(provenance["url"], source_row()["url"])
        self.assertEqual(provenance["licence"], "Test licence")
        self.assertEqual(provenance["bytes"], len(payload))
        self.assertEqual(provenance["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertTrue(provenance["accessed_at"].endswith("Z"))

    def test_interrupted_stream_keeps_only_an_invalid_resumable_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / source_row()["target_path"]

            with self.assertRaisesRegex(OSError, "stream interrupted"):
                fetcher.fetch_source(
                    source_row(),
                    root=root,
                    provenance_dir=root / "provenance",
                    opener=lambda _request, timeout: FailingResponse(),
                )

            self.assertFalse(target.exists())
            partials = list(target.parent.glob("*.part"))
            self.assertEqual(len(partials), 1)
            self.assertEqual(partials[0].read_bytes(), b"partial")

    def test_failed_replacement_preserves_last_valid_target(self) -> None:
        payload = b"last known good"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / source_row()["target_path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)

            with self.assertRaisesRegex(OSError, "stream interrupted"):
                fetcher.fetch_source(
                    source_row(),
                    root=root,
                    provenance_dir=root / "provenance",
                    opener=lambda _request, timeout: FailingResponse(),
                )

            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(len(list(target.parent.glob("*.part"))), 1)

    def test_rejects_declared_or_streamed_content_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "exceeds max_bytes"):
                fetcher.fetch_source(
                    source_row(max_bytes="4"),
                    root=root,
                    provenance_dir=root / "provenance",
                    opener=lambda _request, timeout: FakeResponse(b"12345", content_length=5),
                )

    def test_large_source_requires_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(PermissionError, "allow-large"):
                fetcher.fetch_source(
                    source_row(access_type="public_large"),
                    root=root,
                    provenance_dir=root / "provenance",
                    opener=lambda _request, timeout: FakeResponse(b"large"),
                )

    def test_resumes_only_when_server_confirms_range_support(self) -> None:
        payload = b"official NRW data"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / source_row()["target_path"]
            target.parent.mkdir(parents=True)
            partial = target.with_suffix(target.suffix + ".part")
            partial.write_bytes(payload[:8])
            partial.with_suffix(partial.suffix + ".json").write_text(json.dumps({
                "url": source_row()["url"], "bytes": 8,
                "identity_header": "ETag", "identity_value": '"first"',
            }))
            requests = []

            def opener(request, timeout):
                requests.append(request)
                return RangeResponse(payload[8:], content_length=len(payload) - 8, headers={
                    "Content-Range": f"bytes 8-{len(payload) - 1}/{len(payload)}", "ETag": '"first"',
                })

            result = fetcher.fetch_source(
                source_row(), root=root, provenance_dir=root / "provenance", opener=opener
            )
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(requests[0].get_header("Range"), "bytes=8-")
            self.assertEqual(requests[0].get_header("If-range"), '"first"')
            self.assertEqual(result["resume_status"], "resumed")

    def test_rejects_range_for_a_changed_remote_object_without_replacing_final(self) -> None:
        payload = b"old-prefix-new-tail"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / source_row()["target_path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(b"last valid")
            partial = target.with_suffix(target.suffix + ".part")
            partial.write_bytes(payload[:10])
            partial.with_suffix(partial.suffix + ".json").write_text(json.dumps({
                "url": source_row()["url"], "bytes": 10,
                "identity_header": "ETag", "identity_value": '"old"',
            }))
            with self.assertRaisesRegex(ValueError, "does not match the retained remote object"):
                fetcher.fetch_source(
                    source_row(), root=root, provenance_dir=root / "provenance",
                    opener=lambda *_args, **_kwargs: RangeResponse(payload[10:], headers={
                        "Content-Range": f"bytes 10-{len(payload) - 1}/{len(payload)}", "ETag": '"new"',
                    }),
                )
            self.assertEqual(target.read_bytes(), b"last valid")
            self.assertFalse(partial.exists())

    def test_completed_partial_is_finalized_after_matching_416_total(self) -> None:
        payload = b"completed candidate"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / source_row()["target_path"]
            target.parent.mkdir(parents=True)
            partial = target.with_suffix(target.suffix + ".part")
            partial.write_bytes(payload)
            partial.with_suffix(partial.suffix + ".json").write_text(json.dumps({
                "url": source_row()["url"], "bytes": len(payload),
                "identity_header": "ETag", "identity_value": '"stable"',
            }))

            def opener(_request, timeout):
                raise HTTPError(source_row()["url"], 416, "range complete", {
                    "Content-Range": f"bytes */{len(payload)}", "ETag": '"stable"',
                }, None)

            result = fetcher.fetch_source(source_row(), root=root, provenance_dir=root / "provenance", opener=opener)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(result["resume_status"], "completed_partial")
            self.assertFalse(partial.exists())

    def test_416_without_matching_identity_discards_partial_then_restarts(self) -> None:
        payload = b"old candidate"
        replacement = b"new complete input"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / source_row()["target_path"]
            target.parent.mkdir(parents=True)
            partial = target.with_suffix(target.suffix + ".part")
            partial.write_bytes(payload)
            partial.with_suffix(partial.suffix + ".json").write_text(json.dumps({
                "url": source_row()["url"], "bytes": len(payload),
                "identity_header": "ETag", "identity_value": '"old"',
            }))
            requests = []

            def opener(request, timeout):
                requests.append(request)
                if len(requests) == 1:
                    raise HTTPError(source_row()["url"], 416, "range complete", {
                        "Content-Range": f"bytes */{len(payload)}",
                    }, None)
                return FakeResponse(replacement, headers={"ETag": '"new"'})

            result = fetcher.fetch_source(source_row(), root=root, provenance_dir=root / "provenance", opener=opener)
            self.assertEqual(target.read_bytes(), replacement)
            self.assertEqual(requests[0].get_header("Range"), f"bytes={len(payload)}-")
            self.assertIsNone(requests[1].get_header("Range"))
            self.assertEqual(result["resume_status"], "restarted")

    def test_actual_http_response_rejects_short_declared_body_and_preserves_final(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / source_row()["target_path"]
            target.parent.mkdir(parents=True)
            target.write_bytes(b"last valid")
            client, server = socket.socketpair()
            try:
                server.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\ncut")
                server.close()
                response = http.client.HTTPResponse(client)
                response.begin()
                with self.assertRaisesRegex(ValueError, "received 3 bytes but Content-Length declared 100"):
                    fetcher.fetch_source(
                        source_row(), root=root, provenance_dir=root / "provenance",
                        opener=lambda _request, timeout: response,
                    )
                self.assertEqual(target.read_bytes(), b"last valid")
            finally:
                client.close()

    def test_format_validation_rejects_html_without_replacing_valid_geojson(self) -> None:
        row = source_row(target_path="data/raw/test/regions.geojson")
        valid = b'{"type":"FeatureCollection","features":[]}'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fetcher.fetch_source(row, root=root, provenance_dir=root / "provenance", opener=lambda *_args, **_kwargs: FakeResponse(valid))
            with self.assertRaisesRegex(ValueError, "not valid JSON"):
                fetcher.fetch_source(
                    row, root=root, provenance_dir=root / "provenance", refresh=True,
                    opener=lambda *_args, **_kwargs: FakeResponse(b"<html>maintenance</html>"),
                )
            self.assertEqual((root / row["target_path"]).read_bytes(), valid)

    def test_valid_cache_avoids_network_but_refresh_replaces_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = fetcher.fetch_source(
                source_row(), root=root, provenance_dir=root / "provenance",
                opener=lambda _request, timeout: FakeResponse(b"first"),
            )
            reused = fetcher.fetch_source(
                source_row(), root=root, provenance_dir=root / "provenance",
                opener=lambda *_args, **_kwargs: self.fail("cache reuse made a network request"),
            )
            refreshed = fetcher.fetch_source(
                source_row(), root=root, provenance_dir=root / "provenance", refresh=True,
                opener=lambda _request, timeout: FakeResponse(b"second"),
            )
            self.assertEqual(first["cache_status"], "downloaded")
            self.assertEqual(reused["cache_status"], "reused")
            self.assertEqual(refreshed["cache_status"], "refreshed")
            self.assertEqual((root / source_row()["target_path"]).read_bytes(), b"second")

    def test_corrupt_cache_and_failed_finalization_preserve_last_valid_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / source_row()["target_path"]
            fetcher.fetch_source(
                source_row(), root=root, provenance_dir=root / "provenance",
                opener=lambda _request, timeout: FakeResponse(b"last valid"),
            )
            target.write_bytes(b"corrupt")
            called = []
            fetcher.fetch_source(
                source_row(), root=root, provenance_dir=root / "provenance",
                opener=lambda _request, timeout: (called.append(True) or FakeResponse(b"repaired")),
            )
            self.assertEqual(called, [True])
            last_valid = target.read_bytes()
            real_replace = source_cache.os.replace

            def fail_final(partial, final):
                if Path(final).resolve() == target.resolve():
                    raise OSError("atomic finalization failed")
                return real_replace(partial, final)

            with patch.object(source_cache.os, "replace", side_effect=fail_final):
                with self.assertRaisesRegex(OSError, "atomic finalization failed"):
                    fetcher.fetch_source(
                        source_row(), root=root, provenance_dir=root / "provenance", refresh=True,
                        opener=lambda _request, timeout: FakeResponse(b"candidate"),
                    )
            self.assertEqual(target.read_bytes(), last_valid)


class GeneralFetchCacheTest(unittest.TestCase):
    def test_bnetza_fetch_row_reuses_valid_cache_without_discovery_request(self) -> None:
        row = {
            "dataset_id": "bnetza_charging_register_nrw",
            "source_url": "https://example.test/landing",
            "target_path": "data/raw/bnetza.csv",
            "access_type": "public",
            "fetch_strategy": "discover_bnetza_csv",
            "max_bytes": "1024",
        }
        payload = (
            b"preamble\n" * 10
            + "Ladeeinrichtungs-ID;Bundesland\n1;Nordrhein-Westfalen\n".encode("cp1252")
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_row = row | {"url": row["source_url"]}
            source_cache.fetch_source(
                cache_row, root=root, provenance_dir=root / "provenance",
                opener=lambda *_args, **_kwargs: FakeResponse(payload),
                validator=lambda candidate: source_cache.validate_consumed_format(candidate, dataset_id=row["dataset_id"]),
            )
            with patch.object(real_fetcher, "ROOT", root), patch.object(real_fetcher, "PROVENANCE_DIR", root / "provenance"), patch.object(
                real_fetcher, "discover_bnetza_csv", side_effect=AssertionError("discovery made a network request")
            ):
                dataset_id, message = real_fetcher.fetch_row(row, include_large=False, refresh=False)
        self.assertEqual(dataset_id, row["dataset_id"])
        self.assertEqual(message, f"reused: {len(payload)} bytes")

    def test_bnetza_cache_miss_discovers_before_downloading_csv(self) -> None:
        row = {
            "dataset_id": "bnetza_charging_register_nrw", "source_url": "https://example.test/landing",
            "target_path": "data/raw/bnetza.csv", "access_type": "public",
            "fetch_strategy": "discover_bnetza_csv", "max_bytes": "1024",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(real_fetcher, "ROOT", root), patch.object(real_fetcher, "PROVENANCE_DIR", root / "provenance"), patch.object(
                real_fetcher, "discover_bnetza_csv", return_value="https://example.test/register.csv"
            ) as discover, patch.object(real_fetcher, "fetch_cached_source", return_value={"cache_status": "downloaded", "bytes": 12}) as download:
                dataset_id, message = real_fetcher.fetch_row(row, include_large=False, refresh=False)
        self.assertEqual((dataset_id, message), (row["dataset_id"], "downloaded: 12 bytes"))
        discover.assert_called_once_with(row["source_url"])
        self.assertEqual(download.call_args.kwargs["resolved_url"], "https://example.test/register.csv")

class RepairEdgeCaseTest(unittest.TestCase):
    def test_416_error_body_does_not_count_as_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = source_row()
            target = root / row['target_path']
            target.parent.mkdir(parents=True)
            partial = target.with_suffix('.bin.part')
            partial.write_bytes(b'complete')
            source_cache.partial_metadata_path(partial).write_text(json.dumps({
                'url': row['url'], 'bytes': 8, 'identity_header': 'ETag', 'identity_value': '"stable"',
            }))
            def opener(*args, **kwargs):
                raise HTTPError(row['url'], 416, 'complete', {
                    'Content-Range': 'bytes */8', 'ETag': '"stable"', 'Content-Length': '5',
                }, io.BytesIO(b'error'))
            result = fetcher.fetch_source(row, root=root, provenance_dir=root / 'provenance', opener=opener)
            self.assertEqual(target.read_bytes(), b'complete')
            self.assertEqual(result['resume_status'], 'completed_partial')

    def test_weak_identity_partial_is_restarted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = source_row()
            target = root / row['target_path']
            target.parent.mkdir(parents=True)
            partial = target.with_suffix('.bin.part')
            partial.write_bytes(b'old')
            source_cache.partial_metadata_path(partial).write_text(json.dumps({
                'url': row['url'], 'bytes': 3, 'identity_header': 'ETag', 'identity_value': 'W/"same"',
            }))
            def opener(request, **kwargs):
                self.assertIsNone(request.get_header('Range'))
                self.assertIsNone(request.get_header('If-range'))
                return FakeResponse(b'new complete')
            fetcher.fetch_source(row, root=root, provenance_dir=root / 'provenance', opener=opener)
            self.assertEqual(target.read_bytes(), b'new complete')

    def test_unsolicited_partial_response_preserves_input_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = source_row()
            kwargs = dict(root=root, provenance_dir=root / 'provenance')
            fetcher.fetch_source(row, **kwargs, opener=lambda *a, **k: FakeResponse(b'valid'))
            provenance = root / 'provenance/test_source.json'
            previous = provenance.read_bytes()
            with self.assertRaisesRegex(ValueError, 'unsolicited partial'):
                fetcher.fetch_source(row, **kwargs, refresh=True, opener=lambda *a, **k: RangeResponse(b'tail'))
            self.assertEqual((root / row['target_path']).read_bytes(), b'valid')
            self.assertEqual(provenance.read_bytes(), previous)

    def test_invalid_formats_preserve_previous_final(self) -> None:
        import zipfile
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as output:
            output.writestr('data.shp', b'original contents')
        corrupt_zip = archive.getvalue().replace(b'original contents', b'corrupt! contents')
        for extension, body in [('zip', corrupt_zip), ('xlsx', b'<html>maintenance</html>'),
                                ('pbf', b'\x00\x00\x00\x10\x0a' + b'garbage' * 4),
                                ('csv', b'<html>error, retry</html>')]:
            with self.subTest(extension=extension), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                row = source_row(target_path=f'input.{extension}')
                target = root / row['target_path']
                target.write_bytes(b'prior input')
                with self.assertRaises(ValueError):
                    fetcher.fetch_source(row, root=root, provenance_dir=root / 'provenance', refresh=True,
                                         opener=lambda *a, **k: FakeResponse(body))
                self.assertEqual(target.read_bytes(), b'prior input')

    def test_pbf_validation_uses_bounded_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'input.pbf'
            target.write_bytes(b'\x00\x00\x00\x0d\x0a\x09OSMHeader\x18\x01\x00')
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded PBF read')):
                source_cache.validate_consumed_format(target)


if __name__ == "__main__":
    unittest.main()
