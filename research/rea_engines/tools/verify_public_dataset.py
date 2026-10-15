"""Offline integrity verification for locally stored Binance Vision kline data.

No network calls are made. Every archive must match both its downloaded .CHECKSUM
and the SHA-256 recorded in the dataset manifest.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
from pathlib import Path
import sys


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(2 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a local public-data CSV, manifest and source archives without network access.")
    parser.add_argument("--csv", required=True, help="Canonical CSV path")
    parser.add_argument("--manifest", required=True, help="Dataset manifest JSON path")
    parser.add_argument("--expected-csv-sha256", required=True, help="Pinned expected CSV SHA-256")
    args = parser.parse_args()
    csv_path = Path(args.csv)
    manifest_path = Path(args.manifest)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        expected = args.expected_csv_sha256.strip().lower()
        if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
            raise ValueError("expected CSV SHA-256 must be 64 hexadecimal characters")
        actual_csv = sha256_file(csv_path)
        if actual_csv != expected or actual_csv != str(manifest.get("canonicalCsvSha256", "")).lower():
            raise ValueError(f"canonical CSV hash mismatch: actual={actual_csv}")
        if manifest.get("canonicalCsv") != csv_path.name:
            raise ValueError("CSV filename does not match manifest canonicalCsv")
        if not isinstance(manifest.get("archives"), list) or not manifest["archives"]:
            raise ValueError("manifest must have a non-empty archives list")
        archive_root = manifest_path.parent / "source_archives" / f'{manifest.get("symbol")}-{manifest.get("interval")}'
        archive_errors = []
        archives_checked = 0
        total_zip_bytes = 0
        for item in manifest["archives"]:
            zip_path = archive_root / item["zip"]
            checksum_path = zip_path.with_name(zip_path.name + ".CHECKSUM")
            if not zip_path.is_file() or not checksum_path.is_file():
                archive_errors.append({"archive": item.get("zip"), "error": "missing ZIP or CHECKSUM"})
                continue
            actual_zip_sha = sha256_file(zip_path)
            checksum_parts = checksum_path.read_text(encoding="ascii").strip().split()
            if not checksum_parts:
                archive_errors.append({"archive": item["zip"], "error": "empty CHECKSUM"})
                continue
            official_sha = checksum_parts[0].lower()
            if actual_zip_sha != official_sha or actual_zip_sha != str(item.get("sha256", "")).lower():
                archive_errors.append({"archive": item["zip"], "error": "SHA-256 mismatch"})
            if item.get("checksum_verified") is not True:
                archive_errors.append({"archive": item["zip"], "error": "manifest does not mark checksum verified"})
            archives_checked += 1
            total_zip_bytes += zip_path.stat().st_size
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            expected_header = ["timestamp", "open", "high", "low", "close", "volume"]
            if reader.fieldnames != expected_header:
                raise ValueError(f"unexpected CSV header: {reader.fieldnames!r}")
            rows = list(reader)
        if len(rows) != int(manifest.get("rowCount", -1)):
            raise ValueError(f"row count mismatch: CSV={len(rows)} manifest={manifest.get('rowCount')}")
        parsed = [datetime.datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")) for row in rows]
        if any(parsed[index] >= parsed[index + 1] for index in range(len(parsed) - 1)):
            raise ValueError("CSV timestamps are not strictly increasing")
        gaps = []
        for index in range(1, len(parsed)):
            delta = int((parsed[index] - parsed[index - 1]).total_seconds())
            typical = 14400
            if delta != typical:
                gaps.append({"previous_timestamp": rows[index - 1]["timestamp"], "timestamp": rows[index]["timestamp"], "delta_seconds": delta})
        if len(gaps) != int(manifest.get("irregularIntervals", -1)):
            raise ValueError("irregular interval count differs from manifest")
        result = {
            "status": "verified" if not archive_errors else "failed",
            "csv_path": str(csv_path.resolve()),
            "csv_bytes": csv_path.stat().st_size,
            "csv_sha256": actual_csv,
            "manifest_path": str(manifest_path.resolve()),
            "manifest_sha256": sha256_file(manifest_path),
            "csv_rows": len(rows),
            "first_timestamp": rows[0]["timestamp"],
            "last_timestamp": rows[-1]["timestamp"],
            "archives_declared": len(manifest["archives"]),
            "archives_checked": archives_checked,
            "total_zip_bytes": total_zip_bytes,
            "archive_errors": archive_errors,
            "gaps": gaps,
            "license_status": manifest.get("licenseStatus"),
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["status"] == "verified" and not archive_errors else 1
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
