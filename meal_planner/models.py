from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Recipe:
    id: int
    title: str
    base_servings: int
    instructions: str | None
    cook_time_min: int | None
    source: str | None
    photo_path: str | None
    recipe_book: str | None
    created_at: str
    updated_at: str


@dataclass
class Ingredient:
    id: int
    recipe_id: int
    name: str
    qty_per_serving: float | None
    unit: str | None
    notes: str | None
    todoist_section: str | None
    sort_order: int
    # qty_raw preserves the original string when qty_per_serving couldn't parse
    # (e.g. ranges "2-3", "8-10"). Default allows existing constructors that
    # don't pass it to keep working. Read path populates from the DB column.
    qty_raw: str | None = None


@dataclass
class GroceryLine:
    name: str
    qty: float | None
    unit: str
    source_recipe_titles: list[str] = field(default_factory=list)
    todoist_section: str = ""
