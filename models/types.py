from dataclasses import dataclass
from enum import Enum


class Category(str, Enum):
    URGENT = "urgent"
    RESPOND = "respond"
    REVIEW = "review"
    REFERENCE = "reference"
    IGNORE = "ignore"


class Importance(str, Enum):
    P0 = "P0"  # critical
    P1 = "P1"  # needs to be done
    P2 = "P2"  # would be pretty great if accomplished
    P3 = "P3"  # nice to have


@dataclass
class Classification:
    category: Category
    confidence: float
    alternatives: dict[str, float]
    tags: list[str]
    reasoning: str
    importance: Importance = Importance.P2
