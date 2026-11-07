import hashlib
import json
import pathlib
import subprocess
import sys
import sqlite3
import tempfile
import unittest

from session_search.recovery_handoff import (
    build_locator,
    build_receipt,
    load_verified_handoff,
    verify_artifact_file,
    verify_local_recovery,
    write_private_json,
)


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RecoveryHandoffTest(unittest.TestCase):
    def _projection_db(self, root: pathlib.Path, name: str, marker: str) -> pathlib.Path:
        db = root / name
        conn = sqlite3.connect(db)
        try:
            conn.execute("CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO corpus_meta(key,value) VALUES ('schema_version','session-search-corpus-v2')"
            )
            conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
            conn.execute("INSERT INTO marker(value) VALUES (?)", [marker])
            conn.commit()
        finally:
            conn.close()
        return db

    def _verified_receipt(self, root: pathlib.Path, marker: str = "v1") -> tuple[dict, pathlib.Path, pathlib.Path]:
        db = self._projection_db(root, f"{marker}.sqlite3", marker)
        transport = root / f"{marker}.zst"
        evidence = root / f"{marker}-evidence.tgz"
        transport.write_bytes(("transport-" + marker).encode())
        evidence.write_bytes(("evidence-" + marker).encode())
        receipt = build_receipt(
            projection_transport_path=transport,
            projection_drive_file_id=f"drive-projection-{marker}",
            projection_db_path=db,
            evidence_path=evidence,
            evidence_drive_file_id=f"drive-evidence-{marker}",
            source_commit="a" * 40,
            accepted_set_sha256="b" * 64,
            verification={"status": "VERIFIED", "verified_at": "2026-11-08T00:00:00Z"},
            source_watermark={"latest_observed_message_time": 123.0},
        )
        return receipt, transport, evidence

    def test_receipt_requires_verified_terminal_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            db = self._projection_db(root, "corpus.sqlite3", "x")
            transport = root / "corpus.zst"
            evidence = root / "evidence.tgz"
            transport.write_bytes(b"transport")
            evidence.write_bytes(b"evidence")
            with self.assertRaisesRegex(ValueError, "RECOVERY_VERIFICATION_NOT_ACCEPTED"):
                build_receipt(
                    projection_transport_path=transport,
                    projection_drive_file_id="p",
                    projection_db_path=db,
                    evidence_path=evidence,
                    evidence_drive_file_id="e",
                    source_commit="a" * 40,
                    accepted_set_sha256="b" * 64,
                    verification={"status": "UNKNOWN"},
                )

    def test_locator_binds_exact_immutable_receipt_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            receipt, transport, evidence = self._verified_receipt(root)
            receipt_path = root / "receipt.json"
            locator_path = root / "current.json"
            write_private_json(receipt_path, receipt)
            locator = build_locator(receipt_path, receipt_drive_file_id="drive-receipt-v1")
            write_private_json(locator_path, locator)

            loaded = load_verified_handoff(locator_path, receipt_path)
            self.assertEqual(loaded["receipt"], receipt)

            changed = dict(receipt)
            changed["accepted_set_sha256"] = "c" * 64
            write_private_json(receipt_path, changed)
            with self.assertRaisesRegex(ValueError, "RECOVERY_RECEIPT_DIGEST_MISMATCH"):
                load_verified_handoff(locator_path, receipt_path)

    def test_new_accepted_corpus_repoints_without_code_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            receipt1, _, _ = self._verified_receipt(root, "v1")
            receipt2, _, _ = self._verified_receipt(root, "v2")
            r1 = root / "r1.json"
            r2 = root / "r2.json"
            current = root / "current.json"
            write_private_json(r1, receipt1)
            write_private_json(r2, receipt2)

            write_private_json(current, build_locator(r1, receipt_drive_file_id="receipt-v1"))
            self.assertEqual(load_verified_handoff(current, r1)["receipt"], receipt1)

            write_private_json(current, build_locator(r2, receipt_drive_file_id="receipt-v2"))
            self.assertEqual(load_verified_handoff(current, r2)["receipt"], receipt2)

    def test_artifact_verification_rejects_wrong_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            receipt, transport, _ = self._verified_receipt(root)
            descriptor = receipt["artifacts"]["projection_transport"]
            verify_artifact_file(transport, descriptor)
            transport.write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "RECOVERY_ARTIFACT_SIZE_MISMATCH|RECOVERY_ARTIFACT_DIGEST_MISMATCH"):
                verify_artifact_file(transport, descriptor)

    def test_local_recovery_verifies_chain_and_creates_corpus_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            receipt, transport, evidence = self._verified_receipt(root)
            db = root / "v1.sqlite3"
            receipt_path = root / "receipt.json"
            locator_path = root / "current.json"
            lock_path = root / "corpus-lock.json"
            write_private_json(receipt_path, receipt)
            write_private_json(
                locator_path,
                build_locator(receipt_path, receipt_drive_file_id="drive-receipt-v1"),
            )

            result = verify_local_recovery(
                locator_path=locator_path,
                receipt_path=receipt_path,
                projection_transport_path=transport,
                evidence_path=evidence,
                projection_db_path=db,
                corpus_lock_out=lock_path,
            )
            self.assertEqual(result["status"], "VERIFIED")
            self.assertTrue(lock_path.is_file())
            self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)

    def test_local_recovery_rejects_wrong_decoded_projection(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            receipt, transport, evidence = self._verified_receipt(root)
            receipt_path = root / "receipt.json"
            locator_path = root / "current.json"
            write_private_json(receipt_path, receipt)
            write_private_json(
                locator_path,
                build_locator(receipt_path, receipt_drive_file_id="drive-receipt-v1"),
            )
            wrong = self._projection_db(root, "wrong.sqlite3", "wrong")

            with self.assertRaisesRegex(
                ValueError,
                "RECOVERY_DECODED_(SIZE|DIGEST)_MISMATCH",
            ):
                verify_local_recovery(
                    locator_path=locator_path,
                    receipt_path=receipt_path,
                    projection_transport_path=transport,
                    evidence_path=evidence,
                    projection_db_path=wrong,
                )

    def test_cli_builders_emit_only_sanitized_status(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            receipt, transport, evidence = self._verified_receipt(root)
            db = root / "v1.sqlite3"
            spec = root / "receipt-spec.json"
            out = root / "receipt.json"
            write_private_json(
                spec,
                {
                    "projection_transport_path": str(transport),
                    "projection_drive_file_id": "private-projection-id",
                    "projection_db_path": str(db),
                    "evidence_path": str(evidence),
                    "evidence_drive_file_id": "private-evidence-id",
                    "source_commit": "a" * 40,
                    "accepted_set_sha256": "b" * 64,
                    "verification": {
                        "status": "VERIFIED",
                        "verified_at": "2026-11-08T00:00:00Z",
                    },
                },
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "session_search.recovery_handoff",
                    "build-receipt",
                    "--spec",
                    str(spec),
                    "--out",
                    str(out),
                ],
                cwd=pathlib.Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("private-projection-id", result.stdout)
            self.assertNotIn("private-evidence-id", result.stdout)
            self.assertEqual(out.stat().st_mode & 0o777, 0o600)

    def test_private_writer_uses_mode_0600(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "private.json"
            write_private_json(path, {"x": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
