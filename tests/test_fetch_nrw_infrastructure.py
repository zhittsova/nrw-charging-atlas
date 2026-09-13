from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

import fetch_nrw_infrastructure as fetcher  # noqa: E402


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, *, content_length: int | None = None) -> None:
        super().__init__(payload)
        self.headers = {}
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

    def test_failed_stream_leaves_no_target_or_partial_file(self) -> None:
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
            self.assertEqual(list(target.parent.glob("*.part")), [])

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


if __name__ == "__main__":
    unittest.main()
