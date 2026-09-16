from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from senecio_polymarket.backend import artifact_identity as ai
from senecio_polymarket.backend import runtime_provenance as rp
from senecio_polymarket.backend import supabase_client as sc
from senecio_polymarket.backend import authority_seal as aseal

VALID_COMMIT = "a" * 40
VALID_TREE = "b" * 40
VALID_IMAGE = "sha256:" + "c" * 64


def make_runtime(root: Path) -> None:
    for dirname in ai.CANONICAL_DIRS:
        (root / dirname).mkdir(parents=True, exist_ok=True)
    (root / "backend" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "frontend" / "index.html").write_text("ok\n", encoding="utf-8")
    (root / "oracle" / "model.py").write_text("X=1\n", encoding="utf-8")
    (root / "oracle_runtime" / "runner.py").write_text("Y=1\n", encoding="utf-8")
    (root / "requirements.lock").write_text("pkg==1\n", encoding="utf-8")
    (root / "start_single_authority.sh").write_text("#!/bin/sh\n", encoding="utf-8")


class B81ProvenanceTests(unittest.TestCase):
    def test_valid_runtime_round_trip_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree=VALID_TREE, root=root, output=manifest)
            result = ai.read_artifact_identity(manifest, root=root)
        self.assertTrue(result["exact"])
        self.assertTrue(result["checks"]["build_digest_matches_runtime_files"])
        self.assertEqual(result["build_digest"], result["computed_build_digest"])

    def test_missing_or_malformed_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            missing = ai.read_artifact_identity(root=root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            manifest.parent.mkdir()
            manifest.write_text("{broken", encoding="utf-8")
            malformed = ai.read_artifact_identity(manifest, root=root)
        self.assertFalse(missing["exact"])
        self.assertFalse(malformed["exact"])
        self.assertFalse(missing["checks"]["build_digest_matches_runtime_files"])
        self.assertFalse(malformed["checks"]["build_digest_matches_runtime_files"])

    def test_runtime_byte_mutation_breaks_exactness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree=VALID_TREE, root=root, output=manifest)
            (root / "backend" / "main.py").write_text("print('tampered')\n", encoding="utf-8")
            result = ai.read_artifact_identity(manifest, root=root)
        self.assertFalse(result["exact"])
        self.assertFalse(result["checks"]["build_digest_matches_runtime_files"])

    def test_declared_input_list_must_equal_recomputed_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            payload = ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree=VALID_TREE, root=root, output=manifest)
            payload["canonical_inputs"] = payload["canonical_inputs"][:-1]
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = ai.read_artifact_identity(manifest, root=root)
        self.assertFalse(result["checks"]["canonical_inputs_exact"])
        self.assertFalse(result["exact"])

    def test_nonregular_required_input_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            (root / "requirements.lock").unlink()
            (root / "requirements.lock").mkdir()
            with self.assertRaises(ai.ArtifactIdentityError):
                ai.canonical_build_digest(root)

    def test_runtime_env_cannot_create_internal_exactness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree=VALID_TREE, root=root, output=manifest)
            manifest.unlink()
            env = {
                "SENEX_SOURCE_COMMIT": VALID_COMMIT,
                "SENEX_SOURCE_TREE": VALID_TREE,
                "SENEX_BUILD_DIGEST": "sha256:" + "d" * 64,
                "SENEX_IMAGE_DIGEST": VALID_IMAGE,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                result = ai.read_artifact_identity(manifest, root=root)
        self.assertFalse(result["exact"])
        self.assertFalse(result["checks"]["build_digest_matches_runtime_files"])

    def test_runtime_provenance_oci_is_external_only(self) -> None:
        fake = ai._empty("NO_INTERNAL_IDENTITY")
        with mock.patch.object(rp, "read_artifact_identity", return_value=fake):
            with mock.patch.dict(os.environ, {"SENEX_IMAGE_DIGEST": VALID_IMAGE}, clear=False):
                payload = rp.runtime_provenance()
        self.assertFalse(payload["exact"])
        self.assertFalse(payload["provider_oci_attestation"]["self_proof"])
        self.assertEqual(payload["provider_oci_attestation"]["role"], "EXTERNAL_ATTESTATION_ONLY")

    def test_supabase_facade_uses_shared_identity_and_never_identity_env(self) -> None:
        source = Path("senecio_polymarket/backend/supabase_client.py").read_text(encoding="utf-8")
        self.assertIn("from .artifact_identity import", source)
        runtime_block = source[source.index("def _runtime_identity"):source.index("def _cursor_tuple")]
        self.assertNotIn("SENEX_SOURCE_COMMIT", runtime_block)
        self.assertNotIn("SENEX_SOURCE_TREE", runtime_block)
        self.assertNotIn("SENEX_IMAGE_DIGEST", runtime_block)
        self.assertNotIn("SENEX_BUILD_DIGEST", runtime_block)

    def test_authority_seal_internal_identity_excludes_image_digest(self) -> None:
        projection = aseal._identity_fields({
            "source_commit": VALID_COMMIT,
            "source_tree": VALID_TREE,
            "build_digest": "sha256:" + "d" * 64,
            "image_digest": VALID_IMAGE,
        })
        self.assertEqual(set(projection), {"source_commit", "source_tree", "build_digest"})
        self.assertNotIn("image_digest", projection)

    def test_invalid_identity_blocks_authority_before_d1(self) -> None:
        sc.reset_r7b_incremental_state_for_tests()
        with mock.patch.object(sc, "internal_identity_projection", side_effect=ai.ArtifactIdentityError("bad")):
            with mock.patch.object(sc, "_get_client") as get_client:
                with self.assertRaises(sc.AuthorityHistoryIncompleteError):
                    asyncio.run(sc.fetch_authority_history("BTCUSDT"))
        get_client.assert_not_called()

    def test_dockerfile_bakes_internal_identity_without_runtime_self_claims(self) -> None:
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG NF_GIT_SHA", dockerfile)
        self.assertIn("--source-commit \"${NF_GIT_SHA}\"", dockerfile)
        self.assertIn('--source-tree "${SOURCE_TREE}"', dockerfile)
        self.assertIn("artifact-identity.json", dockerfile)
        self.assertIn("artifact_identity.py", dockerfile)
        self.assertNotIn("ARG SENEX_SOURCE_COMMIT", dockerfile)
        self.assertNotIn("ARG SENEX_SOURCE_TREE", dockerfile)
        self.assertNotIn("ARG SENEX_IMAGE_DIGEST", dockerfile)
        self.assertNotIn("ARG SENEX_BUILD_DIGEST", dockerfile)
        self.assertNotIn("ENV SENEX_SOURCE_COMMIT", dockerfile)
        self.assertNotIn("ENV SENEX_SOURCE_TREE", dockerfile)
        self.assertNotIn("ENV SENEX_IMAGE_DIGEST", dockerfile)
        self.assertNotIn("ENV SENEX_BUILD_DIGEST", dockerfile)

    def test_dockerfile_does_not_embed_full_build_context_in_final_layers(self) -> None:
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("/source-tree.txt", dockerfile)
        self.assertIn('--source-tree "${SOURCE_TREE}"', dockerfile)
        self.assertNotIn("COPY --from=source /source /source", dockerfile)
        self.assertNotIn("--source-root /source", dockerfile)

    def test_generated_identity_output_is_not_canonical_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            payload = ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree=VALID_TREE, root=root, output=manifest)
        self.assertNotIn(f"{ai.PROVENANCE_DIRNAME}/{ai.IDENTITY_FILENAME}", payload["canonical_inputs"])


    def test_git_tree_sha_matches_git_for_regular_tree(self) -> None:
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            (root / "alpha.txt").write_bytes(b"alpha\n")
            (root / "sub" / "beta.txt").write_bytes(b"beta\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "alpha.txt", "sub/beta.txt"], check=True)
            expected = subprocess.check_output(["git", "-C", str(root), "write-tree"], text=True).strip()
            actual = ai.git_tree_sha(root)
        self.assertEqual(actual, expected)

    def test_write_identity_can_compute_source_tree_from_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runtime = base / "runtime"
            source = base / "source"
            runtime.mkdir(); source.mkdir()
            make_runtime(runtime)
            (source / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (source / "app.py").write_text("print(1)\n", encoding="utf-8")
            manifest = runtime / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            payload = ai.write_artifact_identity(source_commit=VALID_COMMIT, source_root=source, root=runtime, output=manifest)
            self.assertEqual(payload["source_tree"], ai.git_tree_sha(source))

    def test_valid_declared_sha_does_not_exact_when_recompute_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            payload = ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree=VALID_TREE, root=root, output=manifest)
            declared = payload["build_digest"]
            self.assertTrue(declared.startswith("sha256:"))
            (root / "requirements.lock").unlink()
            result = ai.read_artifact_identity(manifest, root=root)
        self.assertEqual(result["declared_build_digest"], declared)
        self.assertIsNone(result["computed_build_digest"])
        self.assertFalse(result["checks"]["build_digest_matches_runtime_files"])
        self.assertFalse(result["exact"])

    def test_declared_computed_mismatch_is_not_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            payload = ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree=VALID_TREE, root=root, output=manifest)
            payload["build_digest"] = "sha256:" + "e" * 64
            manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            result = ai.read_artifact_identity(manifest, root=root)
        self.assertFalse(result["checks"]["build_digest_matches_runtime_files"])
        self.assertFalse(result["exact"])
        self.assertIsNotNone(result["computed_build_digest"])

    def test_malformed_commit_or_tree_cannot_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            with self.assertRaises(ai.ArtifactIdentityError):
                ai.write_artifact_identity(source_commit="not-a-sha", source_tree=VALID_TREE, root=root, output=manifest)
            with self.assertRaises(ai.ArtifactIdentityError):
                ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree="not-a-sha", root=root, output=manifest)
            with self.assertRaises(ai.ArtifactIdentityError):
                ai.write_artifact_identity(source_commit="unknown", source_tree=VALID_TREE, root=root, output=manifest)

    def test_digest_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            first, files_a = ai.canonical_build_digest(root)
            second, files_b = ai.canonical_build_digest(root)
        self.assertEqual(first, second)
        self.assertEqual(files_a, files_b)

    def test_symlink_substitution_of_canonical_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            outside = Path(tmp) / "outside.py"
            outside.write_text("evil\n", encoding="utf-8")
            target = root / "backend" / "main.py"
            target.unlink()
            try:
                target.symlink_to(outside)
            except OSError as exc:
                self.skipTest("symlink unsupported: " + str(exc))
            with self.assertRaises(ai.ArtifactIdentityError):
                ai.canonical_build_digest(root)

    def test_provider_oci_absent_does_not_hurt_valid_internal_exactness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree=VALID_TREE, root=root, output=manifest)
            identity = ai.read_artifact_identity(manifest, root=root)
            with mock.patch.object(rp, "read_artifact_identity", return_value=identity):
                with mock.patch.dict(os.environ, {"SENEX_IMAGE_DIGEST": ""}, clear=False):
                    payload = rp.runtime_provenance()
        self.assertTrue(identity["exact"])
        self.assertTrue(payload["exact"])
        self.assertFalse(payload["provider_oci_attestation"]["self_proof"])
        self.assertTrue(not payload["image_digest"])


    def test_paper_safety_locks_remain_hard(self) -> None:
        from senecio_polymarket.backend.portfolio.live_gate import LiveGate
        gate = LiveGate().evaluate()
        self.assertEqual(gate.trade_mode, "PAPER")
        self.assertTrue(gate.live_capital_locked)
        self.assertFalse(gate.unlocked)
        source = Path("senecio_polymarket/backend/main_real.py").read_text(encoding="utf-8")
        self.assertIn('"PAPER"', source)
        self.assertIn("live_capital_locked", source)


    def test_manifest_rejects_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_runtime(root)
            manifest = root / ai.PROVENANCE_DIRNAME / ai.IDENTITY_FILENAME
            payload = ai.write_artifact_identity(source_commit=VALID_COMMIT, source_tree=VALID_TREE, root=root, output=manifest)
            payload["unexpected"] = "must-fail-closed"
            manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            result = ai.read_artifact_identity(manifest, root=root)
        self.assertFalse(result["checks"]["manifest_exact"])
        self.assertFalse(result["exact"])

    def test_invalid_identity_blocks_exact_count_before_d1(self) -> None:
        sc.reset_r7b_incremental_state_for_tests()
        with mock.patch.object(sc, "internal_identity_projection", side_effect=ai.ArtifactIdentityError("bad")):
            with mock.patch.object(sc, "_get_client") as get_client:
                with self.assertRaises(sc.AuthorityHistoryIncompleteError):
                    asyncio.run(sc.count_predictions_exact())
        get_client.assert_not_called()

    def test_readiness_rejects_non_exact_provenance_and_preserves_safety(self) -> None:
        from senecio_polymarket.backend.readiness_contract import build_readiness_contract
        snapshot = {
            "authority_history_complete": True,
            "exact_count_complete": True,
            "provenance": {"exact": False},
            "live_gate": {"trade_mode": "PAPER", "live_capital_locked": True, "orders_enabled": False},
            "snapshot_id": "s",
            "generation": 1,
            "canonical_sha256": "sha256:" + "1" * 64,
        }
        payload = build_readiness_contract(
            snapshot,
            {"snapshot_stale": False, "last_refresh_error": None},
            oracle_started=True,
            adapters={},
        )
        self.assertEqual(payload["status"], "not_ready")
        self.assertFalse(payload["checks"]["provenance_exact"])
        self.assertEqual(payload["safety"]["trade_mode"], "PAPER")
        self.assertFalse(payload["safety"]["orders_enabled"])
        self.assertTrue(payload["safety"]["live_capital_locked"])


    def test_source_stage_import_cannot_mutate_measured_git_tree(self) -> None:
        import shutil
        import subprocess
        import sys
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        source_stage, _ = dockerfile.split("\nFROM python:3.11-slim\n", 1)
        self.assertIn("ENV PYTHONDONTWRITEBYTECODE=1", source_stage)
        self.assertIn("PYTHONPATH=/source python -B -c", source_stage)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            pkg = root / "senecio_polymarket" / "backend"
            pkg.mkdir(parents=True)
            (root / "senecio_polymarket" / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            shutil.copy2(Path("senecio_polymarket/backend/artifact_identity.py"), pkg / "artifact_identity.py")
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"], check=True)
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
            expected = subprocess.check_output(["git", "-C", str(root), "write-tree"], text=True).strip()
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONPATH"] = str(root)
            code = "from pathlib import Path; from senecio_polymarket.backend.artifact_identity import git_tree_sha; print(git_tree_sha(Path(r'{}')))".format(root)
            actual = subprocess.check_output([sys.executable, "-B", "-c", code], text=True, env=env).strip()
            self.assertEqual(actual, expected)
            self.assertFalse(any(root.rglob("*.pyc")))


if __name__ == "__main__":
    unittest.main()
