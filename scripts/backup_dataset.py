"""Copy a finalized local dataset to a new directory and verify SHA-256 checksums."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def checksums(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Dataset backup does not follow symlinks")
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
    if not result:
        raise ValueError("Source dataset is empty")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.source.is_symlink():
        raise ValueError("Source must be a real dataset directory")
    source, destination = args.source.resolve(), args.destination.resolve()
    if not source.is_dir() or destination.exists() or destination.is_relative_to(source):
        raise ValueError("Use an existing source and a new destination outside that source")
    expected = checksums(source)
    shutil.copytree(source, destination)
    if checksums(destination) != expected or checksums(source) != expected:
        raise RuntimeError("Backup verification failed or source changed during copying")
    report = {"passed": True, "source": str(source), "destination": str(destination),
              "algorithm": "SHA-256", "files": expected}
    (destination / "backup-checksums.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "files"} | {"file_count": len(expected)},
                     indent=2))


if __name__ == "__main__":
    main()
