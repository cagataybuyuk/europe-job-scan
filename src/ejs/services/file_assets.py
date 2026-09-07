from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from ejs.contracts.file_upload import ApprovedFileAsset, MAX_FILE_UPLOAD_BYTES


@dataclass(frozen=True)
class VerifiedFileAsset:
    path: str
    file_name: str
    mime_type: str
    size_bytes: int
    sha256: str


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_asset(spec: ApprovedFileAsset) -> VerifiedFileAsset:
    spec.validate()
    path = Path(spec.local_path)
    if not path.is_file():
        raise FileNotFoundError(f"approved asset not found: {spec.expected_file_name}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_FILE_UPLOAD_BYTES:
        raise PermissionError("asset size is outside FILE-1 bounds")
    if path.name != spec.expected_file_name:
        raise ValueError("local asset filename does not match approved manifest")
    if size != spec.expected_size_bytes:
        raise ValueError("local asset size does not match approved manifest")
    digest = sha256_file(path)
    if digest.lower() != spec.expected_sha256.lower():
        raise ValueError("local asset hash does not match approved manifest")
    if spec.expected_mime_type == "application/pdf":
        with path.open("rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise ValueError("PDF asset magic header is invalid")
    return VerifiedFileAsset(
        path=str(path),
        file_name=path.name,
        mime_type=spec.expected_mime_type,
        size_bytes=size,
        sha256=digest,
    )


def accept_allows(accept: str, *, file_name: str, mime_type: str) -> bool:
    value = (accept or "").strip().lower()
    if not value:
        return True
    tokens = [x.strip() for x in value.split(",") if x.strip()]
    suffix = Path(file_name).suffix.lower()
    for token in tokens:
        if token.startswith(".") and token == suffix:
            return True
        if token.endswith("/*") and mime_type.lower().startswith(token[:-1]):
            return True
        if token == mime_type.lower():
            return True
    return False
