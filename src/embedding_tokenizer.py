"""Offline BGE-M3 token accounting and lossless source-span chunking."""

import hashlib
from bisect import bisect_right
from pathlib import Path

from tokenizers import Tokenizer

BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
BGE_M3_TOKENIZER_SHA256 = (
    "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08"
)
BGE_M3_MAX_TOKENS = 8192


def is_bge_m3(model: str) -> bool:
    """Recognize the supported BGE-M3 model names without matching other models."""
    return model.lower() in {
        "bge-m3",
        "bge-m3:latest",
        "baai/bge-m3",
        "@cf/baai/bge-m3",
    }


class BgeM3Tokenizer:
    """Load the pinned official tokenizer locally; never download or truncate."""

    def __init__(self, path: str | Path):
        data = Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != BGE_M3_TOKENIZER_SHA256:
            raise ValueError(
                "BGE-M3 tokenizer SHA-256 does not match the pinned artifact"
            )
        self.tokenizer: Tokenizer = Tokenizer.from_str(data.decode("utf-8"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.special_tokens: int = self.tokenizer.num_special_tokens_to_add(False)

    def count(self, text: str) -> int:
        """Count the actual provider input, including model special tokens."""
        return len(self.tokenizer.encode(text, add_special_tokens=True).ids)

    def chunks(self, text: str, max_tokens: int) -> list[tuple[str, int]]:
        """Slice original Unicode text with 20% token overlap and full coverage.

        Token decoding loses normalization and whitespace. Offsets select source
        spans instead; each emitted span is re-tokenized because a new boundary
        can change tokenization. Counts include special tokens on every chunk.
        """
        return [
            (text[start:end], count)
            for start, end, count in self.chunk_spans(text, max_tokens)
        ]

    def chunk_spans(self, text: str, max_tokens: int) -> list[tuple[int, int, int]]:
        """Return ordered (start, end, input_tokens) spans into the original text."""
        limit = min(max_tokens, BGE_M3_MAX_TOKENS)
        count = self.count(text)
        if count <= limit:
            return [(0, len(text), count)]
        payload_limit = limit - self.special_tokens
        if payload_limit <= 0:
            raise ValueError("BGE-M3 token limit leaves no room for text")

        offsets = self.tokenizer.encode(text, add_special_tokens=False).offsets
        ends = [end for _, end in offsets]
        chunks: list[tuple[int, int, int]] = []
        start = 0
        while start < len(text):
            first_token = bisect_right(ends, start)
            last_token = first_token + payload_limit - 1
            end = offsets[last_token][1] if last_token < len(offsets) else len(text)
            end = max(start + 1, end)
            chunk_count = self.count(text[start:end])
            while chunk_count > limit and end > start + 1:
                end -= 1
                chunk_count = self.count(text[start:end])
            if chunk_count > limit:
                raise ValueError(
                    "BGE-M3 token limit cannot fit a single Unicode character"
                )
            chunk = text[start:end]
            chunks.append((start, end, chunk_count))
            if end == len(text):
                break
            chunk_offsets = self.tokenizer.encode(
                chunk, add_special_tokens=False
            ).offsets
            overlap = int(payload_limit * 0.2)
            overlap_start = (
                start + chunk_offsets[-overlap][0]
                if overlap and len(chunk_offsets) > overlap
                else end
            )
            start = overlap_start if overlap_start > start else end
        return chunks
