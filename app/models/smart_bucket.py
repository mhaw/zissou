from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SmartBucketRule:
    field: str
    operator: str
    value: str


@dataclass
class SmartBucket:
    id: str | None = None
    name: str = ""
    rules: list[SmartBucketRule] = field(default_factory=list)
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
