from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from food_registry_bot.db.models import EntryType


class NormalizedEntryItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    quantity: Optional[int] = Field(default=None, ge=0)
    unit: Optional[str] = Field(default=None, min_length=1, max_length=32)


class NormalizedEntryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: EntryType
    occurred_at: Optional[datetime] = None
    items: List[NormalizedEntryItemPayload] = Field(min_length=1)


class NormalizedJournalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: List[NormalizedEntryPayload] = Field(min_length=1)
