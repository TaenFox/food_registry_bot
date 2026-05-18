from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractionImageInput:
    data: bytes
    media_type: str


@dataclass(frozen=True)
class JournalExtractionRequest:
    text: str | None = None
    images: tuple[ExtractionImageInput, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        normalized_text = self.text.strip() if self.text is not None else None
        object.__setattr__(self, "text", normalized_text or None)

        if not self.text and not self.images:
            raise ValueError("JournalExtractionRequest requires text or images")
