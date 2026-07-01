"""Content encoding for SimpleWorkspace effects.

This module provides adaptive content encoding:
- Text files: diff-match-patch delta (90-99% size reduction for typical edits)
- Binary files: adaptive zlib compression + base64

Validated by SW-02 and SW-03 spikes.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import zlib
from typing import Literal

logger = logging.getLogger(__name__)

# Import diff-match-patch with fallback for when not installed
try:
    from diff_match_patch import diff_match_patch
except ImportError:
    diff_match_patch = None  # type: ignore


class ContentEncodingError(Exception):
    """Raised when content encoding/decoding fails."""


class ContentHashMismatchError(ContentEncodingError):
    """Raised when decoded content hash doesn't match expected hash."""

    def __init__(self, path: str, expected: str, actual: str):
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(f"Hash mismatch for {path}: expected {expected[:8]}..., got {actual[:8]}...")


class ContentEncoder:
    """Adaptive content encoder for SimpleWorkspace effects.

    Strategies:
    - Text files: diff-match-patch delta (90-99% size reduction for typical edits)
    - Binary files: adaptive zlib compression + base64

    Thresholds (validated by spikes):
    - Delta threshold: 80% (if delta > 80% of full, use full content)
    - Small file threshold: 500 bytes (always use full content)
    - Diff timeout: 0.5 seconds (prevent hangs on adversarial input)
    """

    DELTA_THRESHOLD = 0.8  # Use full if delta > 80% of full size
    SMALL_FILE_THRESHOLD = 500  # Skip delta for tiny files
    DIFF_TIMEOUT = 0.5  # Seconds

    def __init__(self) -> None:
        if diff_match_patch is not None:
            self._dmp = diff_match_patch()
            self._dmp.Diff_Timeout = self.DIFF_TIMEOUT
        else:
            self._dmp = None

    def is_binary(self, content: bytes) -> bool:
        """Detect binary content using null byte detection.

        Validated by SW-03: 100% accuracy, 6 microseconds.
        """
        return b"\x00" in content[:8192]

    def encode_new(
        self,
        content: bytes,
        is_binary: bool,
    ) -> tuple[Literal["full", "zlib_base64", "raw_base64"], bytes]:
        """Encode new file content.

        Args:
            content: File content bytes
            is_binary: Whether content is binary

        Returns:
            (encoding_type, encoded_bytes)
        """
        if is_binary:
            return self._encode_binary(content)
        # For new files, store full content (no delta possible)
        return ("full", content)

    def encode_modification(
        self,
        old_content: bytes,
        new_content: bytes,
        is_binary: bool,
    ) -> tuple[Literal["full", "delta", "zlib_base64", "raw_base64"], bytes]:
        """Encode file modification.

        Args:
            old_content: Original file content
            new_content: New file content
            is_binary: Whether content is binary

        Returns:
            (encoding_type, encoded_bytes)
        """
        if is_binary:
            return self._encode_binary(new_content)

        # Small files: skip delta
        if len(new_content) < self.SMALL_FILE_THRESHOLD:
            return ("full", new_content)

        # No diff-match-patch available: use full
        if self._dmp is None:
            return ("full", new_content)

        # Try text delta
        try:
            old_text = old_content.decode("utf-8")
            new_text = new_content.decode("utf-8")

            patches = self._dmp.patch_make(old_text, new_text)
            delta = self._dmp.patch_toText(patches).encode("utf-8")

            # Check threshold
            if len(delta) > self.DELTA_THRESHOLD * len(new_content):
                return ("full", new_content)

            return ("delta", delta)

        except (UnicodeDecodeError, ValueError, TypeError) as e:
            logger.debug("Delta encoding failed, using full content: %s", e)
            return ("full", new_content)

    def _encode_binary(
        self,
        content: bytes,
    ) -> tuple[Literal["zlib_base64", "raw_base64"], bytes]:
        """Encode binary content with adaptive compression.

        Validated by SW-03:
        - Compressible data: -99% with zlib
        - Random/compressed data: +33% baseline (base64 overhead)
        """
        raw = base64.b64encode(content)
        compressed = base64.b64encode(zlib.compress(content, level=6))

        if len(compressed) < len(raw):
            return ("zlib_base64", compressed)
        return ("raw_base64", raw)

    def decode(
        self,
        encoding: str,
        data: bytes,
        old_content: bytes | None = None,
    ) -> bytes:
        """Decode content back to original bytes.

        Args:
            encoding: Encoding type (full, delta, zlib_base64, raw_base64)
            data: Encoded data
            old_content: Required for delta decoding

        Returns:
            Decoded content bytes

        Raises:
            ValueError: If encoding is unknown or delta requires old_content
        """
        if encoding == "full":
            return data

        if encoding == "delta":
            if old_content is None:
                raise ValueError("Delta decoding requires old_content")
            if self._dmp is None:
                raise ValueError("diff-match-patch not installed for delta decoding")

            old_text = old_content.decode("utf-8")
            patches = self._dmp.patch_fromText(data.decode("utf-8"))
            result, _ = self._dmp.patch_apply(patches, old_text)
            return result.encode("utf-8")

        if encoding == "zlib_base64":
            return zlib.decompress(base64.b64decode(data))

        if encoding == "raw_base64":
            return base64.b64decode(data)

        raise ValueError(f"Unknown encoding: {encoding}")

    def decode_and_verify(
        self,
        encoding: str,
        data: bytes,
        old_content: bytes | None = None,
        expected_hash: str | None = None,
        path: str = "<unknown>",
    ) -> bytes:
        """Decode content and verify hash if provided.

        Args:
            encoding: Encoding type
            data: Encoded data
            old_content: Required for delta decoding
            expected_hash: Expected SHA256 hash
            path: File path for error messages

        Returns:
            Decoded content bytes

        Raises:
            ContentHashMismatchError: If hash doesn't match
        """
        result = self.decode(encoding, data, old_content)

        if expected_hash is not None:
            actual_hash = hashlib.sha256(result).hexdigest()
            if actual_hash != expected_hash:
                raise ContentHashMismatchError(path, expected_hash, actual_hash)

        return result


# Module-level singleton - use this instead of creating instances
_DEFAULT_ENCODER: ContentEncoder | None = None


def get_encoder() -> ContentEncoder:
    """Get the default content encoder singleton."""
    global _DEFAULT_ENCODER
    if _DEFAULT_ENCODER is None:
        _DEFAULT_ENCODER = ContentEncoder()
    return _DEFAULT_ENCODER
