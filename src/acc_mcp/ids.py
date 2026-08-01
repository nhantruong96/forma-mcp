"""ID helpers: the "b." prefix convention and URN encoding.

Data Management API uses hub/project ids with a "b." prefix; the ACC Admin,
BIM 360 Docs, Reviews and Files APIs use the bare GUID for the same project.
"""

from __future__ import annotations

import urllib.parse


def with_b(pid: str) -> str:
    """Ensure the DM-style id: 'b.' + GUID."""
    pid = (pid or "").strip()
    return pid if pid.startswith("b.") else f"b.{pid}"


def without_b(pid: str) -> str:
    """Ensure the bare GUID (Admin / Docs / Reviews style)."""
    pid = (pid or "").strip()
    return pid[2:] if pid.startswith("b.") else pid


def enc(urn: str) -> str:
    """URL-encode a URN for use in a path segment (keeps nothing unsafe)."""
    return urllib.parse.quote(urn or "", safe="")


def storage_parts(storage_urn: str) -> tuple[str, str]:
    """Split 'urn:adsk.objects:os.object:<bucketKey>/<objectKey>' -> (bucket, key)."""
    prefix = "urn:adsk.objects:os.object:"
    if not storage_urn.startswith(prefix):
        raise ValueError(f"Not a storage URN: {storage_urn}")
    bucket, _, key = storage_urn[len(prefix):].partition("/")
    if not bucket or not key:
        raise ValueError(f"Malformed storage URN: {storage_urn}")
    return bucket, key
