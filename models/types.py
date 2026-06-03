from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    URGENT = "urgent"
    RESPOND = "respond"
    REVIEW = "review"
    REFERENCE = "reference"
    IGNORE = "ignore"


@dataclass
class Classification:
    category: Category
    confidence: float
    alternatives: dict[str, float]
    tags: list[str]
    reasoning: str
