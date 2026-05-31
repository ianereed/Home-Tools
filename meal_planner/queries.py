from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from meal_planner import db as _db
from meal_planner.models import Ingredient, Recipe


class _Unset:
    """Sentinel for partial-update kwargs.

    `None` means "set this field to NULL". `_UNSET` means "do not change
    this field". Distinguishes "explicitly clear" from "not provided".
    """
    __slots__ = ()
    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET: _Unset = _Unset()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_recipe(row) -> Recipe:
    # recipe_book defaults to None for rows pre-dating the column add
    # (legacy DBs that bootstrap before init_db's _add_column_if_missing runs).
    try:
        rb = row["recipe_book"]
    except (IndexError, KeyError):
        rb = None
    return Recipe(
        id=row["id"],
        title=row["title"],
        base_servings=row["base_servings"],
        instructions=row["instructions"],
        cook_time_min=row["cook_time_min"],
        source=row["source"],
        photo_path=row["photo_path"],
        recipe_book=rb,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_recipes(
    *, tag: str | None = None, path: Path | None = None
) -> list[Recipe]:
    """Return all recipes ordered by title, optionally filtered to a single tag."""
    p = path or _db.DB_PATH
    with _db._get_conn(p) as conn:
        if tag is None:
            rows = conn.execute(
                "SELECT * FROM recipes ORDER BY title COLLATE NOCASE"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT r.* FROM recipes r
                JOIN recipe_tags rt ON rt.recipe_id = r.id
                JOIN tags t ON t.id = rt.tag_id
                WHERE t.name = ?
                ORDER BY r.title COLLATE NOCASE
                """,
                (tag,),
            ).fetchall()
    return [_row_to_recipe(r) for r in rows]


def get_recipe(recipe_id: int, *, path: Path | None = None) -> Recipe:
    """Return the recipe with the given id.

    Raises KeyError if no matching recipe exists.
    """
    p = path or _db.DB_PATH
    with _db._get_conn(p) as conn:
        row = conn.execute(
            "SELECT * FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
    if row is None:
        raise KeyError(recipe_id)
    return _row_to_recipe(row)


def list_all_tags(*, path: Path | None = None) -> list[str]:
    """Return tags linked to ≥1 recipe, sorted alphabetically.

    Tags with no recipe links are excluded so the filter UI never shows
    pills the user can't usefully click.
    """
    p = path or _db.DB_PATH
    with _db._get_conn(p) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT t.name
            FROM tags t
            JOIN recipe_tags rt ON rt.tag_id = t.id
            ORDER BY t.name
            """
        ).fetchall()
    return [r["name"] for r in rows]


def list_all_recipe_books(*, path: Path | None = None) -> list[str]:
    """Return distinct recipe_book values from the recipes table.

    NULL/empty rows are excluded — the filter UI shouldn't expose a
    "no book" pill (use the absence of selection instead).
    """
    p = path or _db.DB_PATH
    with _db._get_conn(p) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT recipe_book
            FROM recipes
            WHERE recipe_book IS NOT NULL AND TRIM(recipe_book) <> ''
            ORDER BY recipe_book COLLATE NOCASE
            """
        ).fetchall()
    return [r["recipe_book"] for r in rows]


def list_ingredients(recipe_id: int, *, path: Path | None = None) -> list[Ingredient]:
    """Return all ingredients for a recipe ordered by sort_order, name."""
    p = path or _db.DB_PATH
    with _db._get_conn(p) as conn:
        rows = conn.execute(
            "SELECT * FROM ingredients WHERE recipe_id = ? ORDER BY sort_order, name COLLATE NOCASE",
            (recipe_id,),
        ).fetchall()
    return [
        Ingredient(
            id=r["id"],
            recipe_id=r["recipe_id"],
            name=r["name"],
            qty_per_serving=r["qty_per_serving"],
            unit=r["unit"],
            notes=r["notes"],
            todoist_section=r["todoist_section"],
            sort_order=r["sort_order"],
        )
        for r in rows
    ]


def get_recipe_tags(recipe_id: int, *, path: Path | None = None) -> list[str]:
    """Return tag names linked to a recipe, sorted alphabetically."""
    p = path or _db.DB_PATH
    with _db._get_conn(p) as conn:
        rows = conn.execute(
            """
            SELECT t.name FROM tags t
            JOIN recipe_tags rt ON rt.tag_id = t.id
            WHERE rt.recipe_id = ?
            ORDER BY t.name COLLATE NOCASE
            """,
            (recipe_id,),
        ).fetchall()
    return [r["name"] for r in rows]


def search_recipes(
    *,
    name_substring: str = "",
    tags: tuple[str, ...] = (),
    tag_logic: str = "and",
    recipe_books: tuple[str, ...] = (),
    sort: str = "alpha",
    path: Path | None = None,
) -> list[Recipe]:
    """Return recipes matching name_substring, the tag filter, and recipe_books.

    name_substring is a case-insensitive LIKE match on the title.
    tags filters to recipes based on tag_logic:
      "and" — recipes that have ALL listed tags (default).
      "or"  — recipes that have ANY listed tag.
    recipe_books is OR semantics: a recipe matches if its recipe_book is in
    the given set (case-insensitive). Empty tuple = no recipe_book filter.
    The recipe_books filter is AND-combined with the tag filter.
    sort controls result ordering:
      "alpha"   — alphabetical by title (default; case-insensitive).
      "recent"  — most-recently-added first (id DESC).
    Raises ValueError for unrecognized tag_logic or sort.
    """
    if tag_logic not in ("and", "or"):
        raise ValueError(f"tag_logic must be 'and' or 'or', got {tag_logic!r}")
    # sort is validated before any SQL is composed — never reaches ORDER BY unsanitized
    if sort not in ("alpha", "recent"):
        raise ValueError(f"sort must be 'alpha' or 'recent', got {sort!r}")
    order_by = "r.title COLLATE NOCASE" if sort == "alpha" else "r.id DESC"
    tags = tuple(dict.fromkeys(tags))  # dedupe while preserving order
    recipe_books = tuple(dict.fromkeys(recipe_books))

    # Compose the recipe_book WHERE clause + params separately so we can AND it
    # into both the tag-filtered and unfiltered queries below.
    book_clause = ""
    book_params: tuple = ()
    if recipe_books:
        book_placeholders = ",".join("?" * len(recipe_books))
        book_clause = f" AND lower(r.recipe_book) IN ({book_placeholders})"
        book_params = tuple(rb.lower() for rb in recipe_books)

    p = path or _db.DB_PATH
    with _db._get_conn(p) as conn:
        if tags:
            placeholders = ",".join("?" * len(tags))
            if tag_logic == "and":
                rows = conn.execute(
                    f"""
                    SELECT r.* FROM recipes r
                    WHERE lower(r.title) LIKE lower(?)
                      AND (
                        SELECT COUNT(DISTINCT t.name)
                        FROM recipe_tags rt
                        JOIN tags t ON t.id = rt.tag_id
                        WHERE rt.recipe_id = r.id AND t.name IN ({placeholders})
                      ) = ?
                      {book_clause}
                    ORDER BY {order_by}
                    """,
                    (f"%{name_substring}%", *tags, len(tags), *book_params),
                ).fetchall()
            else:  # or
                rows = conn.execute(
                    f"""
                    SELECT r.* FROM recipes r
                    WHERE lower(r.title) LIKE lower(?)
                      AND EXISTS (
                        SELECT 1 FROM recipe_tags rt
                        JOIN tags t ON t.id = rt.tag_id
                        WHERE rt.recipe_id = r.id AND t.name IN ({placeholders})
                      )
                      {book_clause}
                    ORDER BY {order_by}
                    """,
                    (f"%{name_substring}%", *tags, *book_params),
                ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT r.* FROM recipes r
                WHERE lower(r.title) LIKE lower(?)
                  {book_clause}
                ORDER BY {order_by}
                """,
                (f"%{name_substring}%", *book_params),
            ).fetchall()
    return [_row_to_recipe(r) for r in rows]


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def create_recipe(
    *,
    title: str,
    base_servings: int = 4,
    instructions: str | None = None,
    cook_time_min: int | None = None,
    source: str | None = None,
    recipe_book: str | None = None,
    path: Path | None = None,
) -> int:
    """Create a new recipe. Returns the new recipe id."""
    return _db.insert_recipe(
        title=title,
        base_servings=base_servings,
        instructions=instructions,
        cook_time_min=cook_time_min,
        source=source,
        recipe_book=recipe_book,
        path=path,
    )


def update_recipe(
    recipe_id: int,
    *,
    title: str | None | _Unset = _UNSET,
    base_servings: int | None | _Unset = _UNSET,
    instructions: str | None | _Unset = _UNSET,
    cook_time_min: int | None | _Unset = _UNSET,
    source: str | None | _Unset = _UNSET,
    recipe_book: str | None | _Unset = _UNSET,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Partial update: only non-_UNSET fields are written. Always bumps updated_at.

    Pass None to explicitly clear a nullable field to NULL.
    Omit a kwarg (or pass _UNSET) to leave it unchanged.

    When conn is passed, uses it without committing or closing (caller owns the
    transaction). When conn is None, opens and commits its own connection.
    Raises KeyError if recipe_id does not exist.
    """
    fields: dict[str, object] = {}
    if title is not _UNSET:
        fields["title"] = title
    if base_servings is not _UNSET:
        fields["base_servings"] = base_servings
    if instructions is not _UNSET:
        fields["instructions"] = instructions
    if cook_time_min is not _UNSET:
        fields["cook_time_min"] = cook_time_min
    if source is not _UNSET:
        fields["source"] = source
    if recipe_book is not _UNSET:
        fields["recipe_book"] = recipe_book
    fields["updated_at"] = _now_utc()
    set_clause = ", ".join(f"{k} = ?" for k in fields)

    def _run(c: sqlite3.Connection) -> None:
        cur = c.execute(
            f"UPDATE recipes SET {set_clause} WHERE id = ?",
            [*fields.values(), recipe_id],
        )
        if cur.rowcount == 0:
            raise KeyError(recipe_id)

    if conn is not None:
        _run(conn)
        return
    p = path or _db.DB_PATH
    with _db._get_conn(p) as c:
        _run(c)


def delete_recipe(
    recipe_id: int,
    *,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Delete a recipe and cascade to ingredients + recipe_tags via FK ON DELETE CASCADE.

    photos_intake.recipe_id is SET NULL (per schema), not deleted — the
    photo-intake row remains catalogued but no longer references a recipe.

    When conn is passed, uses it without committing or closing (caller owns the
    transaction). When conn is None, opens and commits its own connection.
    Raises KeyError if recipe_id does not exist.
    """
    def _run(c: sqlite3.Connection) -> None:
        cur = c.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
        if cur.rowcount == 0:
            raise KeyError(recipe_id)
        # GC orphan tag rows (FK cascade only clears recipe_tags rows, not
        # tags). Matches set_recipe_tags' cleanup so list_all_tags stays tight.
        c.execute(
            "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM recipe_tags)"
        )

    if conn is not None:
        _run(conn)
        return
    p = path or _db.DB_PATH
    with _db._get_conn(p) as c:
        _run(c)


def add_ingredient(
    recipe_id: int,
    *,
    name: str,
    qty_per_serving: float | None = None,
    unit: str | None = None,
    notes: str | None = None,
    todoist_section: str | None = None,
    sort_order: int = 0,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Add an ingredient to a recipe. Returns the new ingredient id. Bumps recipe updated_at.

    When conn is passed, uses it without committing or closing (caller owns the
    transaction). When conn is None, opens and commits its own connection.
    Raises KeyError if recipe_id does not exist.
    """
    now = _now_utc()

    def _run(c: sqlite3.Connection) -> int:
        try:
            cur = c.execute(
                """
                INSERT INTO ingredients
                  (recipe_id, name, qty_per_serving, unit, notes, todoist_section, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (recipe_id, name, qty_per_serving, unit, notes, todoist_section, sort_order),
            )
        except sqlite3.IntegrityError:
            # FK violation is the only realistic IntegrityError here: name is
            # NOT NULL but typed `str` (no None default), and ingredients has
            # no UNIQUE constraints. If a UNIQUE is added later, this catch
            # would silently misreport "missing recipe" — narrow it then.
            raise KeyError(recipe_id)
        ingredient_id = cur.lastrowid
        c.execute(
            "UPDATE recipes SET updated_at = ? WHERE id = ?", (now, recipe_id)
        )
        return ingredient_id  # type: ignore[return-value]

    if conn is not None:
        return _run(conn)
    p = path or _db.DB_PATH
    with _db._get_conn(p) as c:
        return _run(c)


def update_ingredient(
    ingredient_id: int,
    *,
    name: str | None | _Unset = _UNSET,
    qty_per_serving: float | None | _Unset = _UNSET,
    unit: str | None | _Unset = _UNSET,
    notes: str | None | _Unset = _UNSET,
    todoist_section: str | None | _Unset = _UNSET,
    sort_order: int | None | _Unset = _UNSET,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Partial update: only non-_UNSET fields are written. Always bumps parent recipe updated_at.

    Pass None to explicitly clear a nullable field to NULL.
    Omit a kwarg (or pass _UNSET) to leave it unchanged.
    Note: name is NOT NULL in the schema — passing name=None will raise IntegrityError.

    When conn is passed, uses it without committing or closing (caller owns the
    transaction). When conn is None, opens and commits its own connection.
    Raises KeyError if ingredient_id does not exist.
    """
    fields: dict[str, object] = {}
    if name is not _UNSET:
        fields["name"] = name
    if qty_per_serving is not _UNSET:
        fields["qty_per_serving"] = qty_per_serving
    if unit is not _UNSET:
        fields["unit"] = unit
    if notes is not _UNSET:
        fields["notes"] = notes
    if todoist_section is not _UNSET:
        fields["todoist_section"] = todoist_section
    if sort_order is not _UNSET:
        fields["sort_order"] = sort_order
    now = _now_utc()

    def _run(c: sqlite3.Connection) -> None:
        row = c.execute(
            "SELECT recipe_id FROM ingredients WHERE id = ?", (ingredient_id,)
        ).fetchone()
        if row is None:
            raise KeyError(ingredient_id)
        if fields:
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            c.execute(
                f"UPDATE ingredients SET {set_clause} WHERE id = ?",
                [*fields.values(), ingredient_id],
            )
        c.execute(
            "UPDATE recipes SET updated_at = ? WHERE id = ?", (now, row["recipe_id"])
        )

    if conn is not None:
        _run(conn)
        return
    p = path or _db.DB_PATH
    with _db._get_conn(p) as c:
        _run(c)


def delete_ingredient(
    ingredient_id: int,
    *,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Delete an ingredient. Bumps parent recipe updated_at BEFORE deleting.

    When conn is passed, uses it without committing or closing (caller owns the
    transaction). When conn is None, opens and commits its own connection.
    Raises KeyError if ingredient_id does not exist.
    """
    now = _now_utc()

    def _run(c: sqlite3.Connection) -> None:
        row = c.execute(
            "SELECT recipe_id FROM ingredients WHERE id = ?", (ingredient_id,)
        ).fetchone()
        if row is None:
            raise KeyError(ingredient_id)
        c.execute(
            "UPDATE recipes SET updated_at = ? WHERE id = ?", (now, row["recipe_id"])
        )
        c.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))

    if conn is not None:
        _run(conn)
        return
    p = path or _db.DB_PATH
    with _db._get_conn(p) as c:
        _run(c)


def set_recipe_tags(
    recipe_id: int,
    tags: list[str],
    *,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Replace-style tag update: deletes all existing tags for this recipe, inserts fresh set.

    Tags are lowercased and deduplicated. Orphan tag rows (not linked to any recipe)
    are garbage-collected. Bumps recipe updated_at.

    When conn is passed, uses it without committing or closing (caller owns the
    transaction). When conn is None, opens and commits its own connection.
    Raises KeyError if recipe_id does not exist.
    """
    normalized = list(dict.fromkeys(t.strip().lower() for t in tags))
    now = _now_utc()

    def _run(c: sqlite3.Connection) -> None:
        if c.execute("SELECT 1 FROM recipes WHERE id = ?", (recipe_id,)).fetchone() is None:
            raise KeyError(recipe_id)
        c.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
        for tag in normalized:
            c.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
            c.execute(
                "INSERT OR IGNORE INTO recipe_tags (recipe_id, tag_id)"
                " SELECT ?, id FROM tags WHERE name = ?",
                (recipe_id, tag),
            )
        c.execute(
            "DELETE FROM tags WHERE id NOT IN (SELECT DISTINCT tag_id FROM recipe_tags)"
        )
        c.execute(
            "UPDATE recipes SET updated_at = ? WHERE id = ?", (now, recipe_id)
        )

    if conn is not None:
        _run(conn)
        return
    p = path or _db.DB_PATH
    with _db._get_conn(p) as c:
        _run(c)
