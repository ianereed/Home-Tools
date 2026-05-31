import sqlite3 as _sqlite3
import time
from pathlib import Path

import pytest

from meal_planner.db import add_recipe_tag, init_db, insert_ingredient, insert_recipe
from meal_planner.queries import (
    add_ingredient,
    create_recipe,
    delete_ingredient,
    delete_recipe,
    get_recipe,
    get_recipe_tags,
    list_all_tags,
    list_ingredients,
    list_recipes,
    search_recipes,
    set_recipe_tags,
    update_ingredient,
    update_recipe,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "recipes.db"
    init_db(p)
    return p


@pytest.fixture
def seeded_db(db_path: Path) -> Path:
    r1 = insert_recipe(title="Chicken Soup", base_servings=4, path=db_path)
    r2 = insert_recipe(title="Beef Stew", base_servings=6, path=db_path)
    r3 = insert_recipe(title="Veggie Stir Fry", base_servings=2, path=db_path)
    add_recipe_tag(r1, "asian", path=db_path)
    add_recipe_tag(r1, "soup", path=db_path)
    add_recipe_tag(r2, "hearty", path=db_path)
    add_recipe_tag(r3, "asian", path=db_path)
    add_recipe_tag(r3, "vegetarian", path=db_path)
    return db_path


def test_list_recipes_empty(db_path: Path) -> None:
    assert list_recipes(path=db_path) == []


def test_list_recipes_populated(seeded_db: Path) -> None:
    recipes = list_recipes(path=seeded_db)
    assert len(recipes) == 3
    titles = [r.title for r in recipes]
    assert titles == sorted(titles)  # ordered by title


def test_list_recipes_tag_filter(seeded_db: Path) -> None:
    asian = list_recipes(tag="asian", path=seeded_db)
    assert {r.title for r in asian} == {"Chicken Soup", "Veggie Stir Fry"}

    soup_only = list_recipes(tag="soup", path=seeded_db)
    assert [r.title for r in soup_only] == ["Chicken Soup"]

    no_match = list_recipes(tag="nonexistent", path=seeded_db)
    assert no_match == []


def test_get_recipe_existing(seeded_db: Path) -> None:
    all_recipes = list_recipes(path=seeded_db)
    for r in all_recipes:
        fetched = get_recipe(r.id, path=seeded_db)
        assert fetched.title == r.title
        assert fetched.id == r.id


def test_get_recipe_missing_raises(seeded_db: Path) -> None:
    with pytest.raises(KeyError):
        get_recipe(99999, path=seeded_db)


def test_search_recipes_name_substring(seeded_db: Path) -> None:
    results = search_recipes(name_substring="stir", path=seeded_db)
    assert [r.title for r in results] == ["Veggie Stir Fry"]

    results_ci = search_recipes(name_substring="SOUP", path=seeded_db)
    assert [r.title for r in results_ci] == ["Chicken Soup"]

    all_results = search_recipes(name_substring="", path=seeded_db)
    assert len(all_results) == 3


def test_search_recipes_tags_single(seeded_db: Path) -> None:
    results = search_recipes(tags=("asian",), path=seeded_db)
    assert {r.title for r in results} == {"Chicken Soup", "Veggie Stir Fry"}


def test_search_recipes_tags_multi(seeded_db: Path) -> None:
    # Only Veggie Stir Fry has both "asian" AND "vegetarian"
    results = search_recipes(tags=("asian", "vegetarian"), path=seeded_db)
    assert [r.title for r in results] == ["Veggie Stir Fry"]


def test_search_recipes_name_and_tags(seeded_db: Path) -> None:
    # name contains "chicken" AND has "asian" tag
    results = search_recipes(name_substring="chicken", tags=("asian",), path=seeded_db)
    assert [r.title for r in results] == ["Chicken Soup"]

    # name contains "beef" AND has "asian" tag — no match
    results_none = search_recipes(name_substring="beef", tags=("asian",), path=seeded_db)
    assert results_none == []


def test_search_recipes_tags_dedup(seeded_db: Path) -> None:
    # duplicate tag in input must produce same results as single occurrence
    deduped = search_recipes(tags=("asian", "asian"), path=seeded_db)
    single = search_recipes(tags=("asian",), path=seeded_db)
    assert {r.id for r in deduped} == {r.id for r in single}


def test_list_all_tags_returns_sorted_distinct_linked_tags(db_path: Path) -> None:
    """Orphan tags (no recipe_tags row) are excluded; result is sorted."""
    import sqlite3 as _sqlite3

    r1 = insert_recipe(title="A", base_servings=2, path=db_path)
    r2 = insert_recipe(title="B", base_servings=2, path=db_path)
    add_recipe_tag(r1, "soup", path=db_path)
    add_recipe_tag(r1, "asian", path=db_path)
    add_recipe_tag(r2, "hearty", path=db_path)

    # Insert orphan tag directly — no recipe_tags row
    conn = _sqlite3.connect(db_path)
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES ('orphan')")
    conn.commit()
    conn.close()

    tags = list_all_tags(path=db_path)
    assert tags == ["asian", "hearty", "soup"]
    assert "orphan" not in tags


def test_search_recipes_tag_logic_or_returns_union(seeded_db: Path) -> None:
    """OR logic returns all recipes that have ANY of the listed tags."""
    # Chicken Soup: asian, soup; Beef Stew: hearty; Veggie Stir Fry: asian, vegetarian
    results = search_recipes(tags=("soup", "vegetarian"), tag_logic="or", path=seeded_db)
    titles = {r.title for r in results}
    assert titles == {"Chicken Soup", "Veggie Stir Fry"}


def test_search_recipes_tag_logic_and_returns_intersection(seeded_db: Path) -> None:
    """AND logic returns only recipes that have ALL listed tags."""
    results = search_recipes(tags=("asian", "vegetarian"), tag_logic="and", path=seeded_db)
    assert [r.title for r in results] == ["Veggie Stir Fry"]


def test_search_recipes_empty_tags_returns_all_regardless_of_logic(seeded_db: Path) -> None:
    """Empty tags tuple returns all recipes for both AND and OR logic."""
    and_results = search_recipes(tags=(), tag_logic="and", path=seeded_db)
    or_results = search_recipes(tags=(), tag_logic="or", path=seeded_db)
    assert len(and_results) == 3
    assert len(or_results) == 3


def test_search_recipes_invalid_tag_logic_raises(seeded_db: Path) -> None:
    with pytest.raises(ValueError, match="tag_logic"):
        search_recipes(tags=("asian",), tag_logic="xor", path=seeded_db)


def test_search_recipes_sort_alpha_default(seeded_db: Path) -> None:
    """No sort arg → alpha order by title (default behavior preserved)."""
    results = search_recipes(path=seeded_db)
    titles = [r.title for r in results]
    assert titles == sorted(titles, key=str.casefold)


def test_search_recipes_sort_alpha_explicit(seeded_db: Path) -> None:
    """sort='alpha' → same alpha order as default."""
    default = search_recipes(path=seeded_db)
    explicit = search_recipes(sort="alpha", path=seeded_db)
    assert [r.id for r in default] == [r.id for r in explicit]


def test_search_recipes_sort_recent_returns_id_desc(seeded_db: Path) -> None:
    """sort='recent' returns recipes in id-DESC order (most-recently-added first)."""
    # seeded_db inserts: Chicken Soup (r1), Beef Stew (r2), Veggie Stir Fry (r3)
    # id-DESC order should be: Veggie Stir Fry, Beef Stew, Chicken Soup
    results = search_recipes(sort="recent", path=seeded_db)
    ids = [r.id for r in results]
    assert ids == sorted(ids, reverse=True)
    assert len(ids) == 3


def test_search_recipes_sort_recent_with_tag_filter(seeded_db: Path) -> None:
    """sort='recent' with tags= returns intersection in id-DESC order."""
    # Both Chicken Soup and Veggie Stir Fry have "asian"; Veggie Stir Fry was inserted last
    results = search_recipes(tags=("asian",), sort="recent", path=seeded_db)
    ids = [r.id for r in results]
    assert ids == sorted(ids, reverse=True)
    assert {r.title for r in results} == {"Chicken Soup", "Veggie Stir Fry"}


def test_search_recipes_sort_invalid_raises(seeded_db: Path) -> None:
    """sort='weird' raises ValueError before any SQL runs."""
    with pytest.raises(ValueError, match="sort"):
        search_recipes(sort="weird", path=seeded_db)


def test_get_recipe_roundtrip_all_fields(db_path: Path) -> None:
    rid = insert_recipe(
        title="Full Recipe",
        base_servings=6,
        instructions="Mix and bake.",
        cook_time_min=45,
        source="Grandma",
        photo_path="/photos/full.jpg",
        path=db_path,
    )
    r = get_recipe(rid, path=db_path)
    assert r.title == "Full Recipe"
    assert r.base_servings == 6
    assert r.instructions == "Mix and bake."
    assert r.cook_time_min == 45
    assert r.source == "Grandma"
    assert r.photo_path == "/photos/full.jpg"
    assert r.created_at is not None
    assert r.updated_at is not None


# ---------------------------------------------------------------------------
# Mutation tests
# ---------------------------------------------------------------------------


def test_create_recipe_returns_id(db_path: Path) -> None:
    rid = create_recipe(title="New Recipe", base_servings=2, path=db_path)
    assert isinstance(rid, int)
    r = get_recipe(rid, path=db_path)
    assert r.title == "New Recipe"
    assert r.base_servings == 2


def test_update_recipe_partial(db_path: Path) -> None:
    rid = insert_recipe(title="Original", base_servings=4, path=db_path)
    update_recipe(rid, title="Updated Title", path=db_path)
    r = get_recipe(rid, path=db_path)
    assert r.title == "Updated Title"
    assert r.base_servings == 4  # unchanged


def test_update_recipe_bumps_updated_at(db_path: Path) -> None:
    rid = insert_recipe(title="Recipe", path=db_path)
    before = get_recipe(rid, path=db_path).updated_at
    time.sleep(0.01)
    update_recipe(rid, title="New Title", path=db_path)
    assert get_recipe(rid, path=db_path).updated_at > before


def test_update_recipe_missing_raises(db_path: Path) -> None:
    with pytest.raises(KeyError):
        update_recipe(99999, title="X", path=db_path)


def test_update_recipe_clears_cook_time_min_to_zero(db_path: Path) -> None:
    rid = insert_recipe(title="Soup", base_servings=4, cook_time_min=15, path=db_path)
    update_recipe(rid, cook_time_min=0, path=db_path)
    assert get_recipe(rid, path=db_path).cook_time_min == 0


# ---------------------------------------------------------------------------
# Phase 19.5: recipe_book column
# ---------------------------------------------------------------------------


def test_insert_recipe_stores_recipe_book(db_path: Path) -> None:
    rid = insert_recipe(title="Pie", recipe_book="Pioneer Woman", path=db_path)
    r = get_recipe(rid, path=db_path)
    assert r.recipe_book == "Pioneer Woman"


def test_insert_recipe_recipe_book_defaults_to_none(db_path: Path) -> None:
    rid = insert_recipe(title="No book", path=db_path)
    r = get_recipe(rid, path=db_path)
    assert r.recipe_book is None


def test_create_recipe_passes_recipe_book_through(db_path: Path) -> None:
    rid = create_recipe(title="Tart", recipe_book="Serious Eats", path=db_path)
    assert get_recipe(rid, path=db_path).recipe_book == "Serious Eats"


def test_update_recipe_sets_recipe_book(db_path: Path) -> None:
    rid = insert_recipe(title="Stew", path=db_path)
    assert get_recipe(rid, path=db_path).recipe_book is None
    update_recipe(rid, recipe_book="NYT Cooking", path=db_path)
    assert get_recipe(rid, path=db_path).recipe_book == "NYT Cooking"


def test_update_recipe_clears_recipe_book_with_none(db_path: Path) -> None:
    rid = insert_recipe(title="Stew", recipe_book="Old Book", path=db_path)
    update_recipe(rid, recipe_book=None, path=db_path)
    assert get_recipe(rid, path=db_path).recipe_book is None


def test_update_recipe_recipe_book_unset_preserves_existing(db_path: Path) -> None:
    """Omitting recipe_book kwarg leaves the existing value untouched (sentinel-driven)."""
    rid = insert_recipe(title="Stew", recipe_book="Existing", path=db_path)
    update_recipe(rid, title="New Title", path=db_path)  # no recipe_book kwarg
    r = get_recipe(rid, path=db_path)
    assert r.title == "New Title"
    assert r.recipe_book == "Existing"


def test_list_all_recipe_books_returns_distinct_sorted(db_path: Path) -> None:
    insert_recipe(title="r1", recipe_book="Serious Eats", path=db_path)
    insert_recipe(title="r2", recipe_book="NYT Cooking", path=db_path)
    insert_recipe(title="r3", recipe_book="Serious Eats", path=db_path)  # dup
    insert_recipe(title="r4", recipe_book=None, path=db_path)  # excluded
    insert_recipe(title="r5", recipe_book="", path=db_path)  # excluded (empty)
    from meal_planner.queries import list_all_recipe_books
    books = list_all_recipe_books(path=db_path)
    assert books == ["NYT Cooking", "Serious Eats"]


def test_search_recipes_filters_by_recipe_book(db_path: Path) -> None:
    from meal_planner.queries import search_recipes
    insert_recipe(title="a", recipe_book="NYT Cooking", path=db_path)
    insert_recipe(title="b", recipe_book="Serious Eats", path=db_path)
    insert_recipe(title="c", recipe_book="NYT Cooking", path=db_path)
    insert_recipe(title="d", recipe_book=None, path=db_path)

    nyt = search_recipes(recipe_books=("NYT Cooking",), path=db_path)
    assert [r.title for r in nyt] == ["a", "c"]

    multi = search_recipes(recipe_books=("NYT Cooking", "Serious Eats"), path=db_path)
    assert sorted(r.title for r in multi) == ["a", "b", "c"]


def test_search_recipes_recipe_book_filter_is_case_insensitive(db_path: Path) -> None:
    from meal_planner.queries import search_recipes
    insert_recipe(title="a", recipe_book="NYT Cooking", path=db_path)
    # Both upper- and lower-case filter values should match the canonical-cased DB value
    out = search_recipes(recipe_books=("nyt cooking",), path=db_path)
    assert [r.title for r in out] == ["a"]


def test_search_recipes_combines_tag_and_book_filters(db_path: Path) -> None:
    from meal_planner.queries import search_recipes
    a = insert_recipe(title="a", recipe_book="NYT Cooking", path=db_path)
    add_recipe_tag(a, "soup", path=db_path)
    b = insert_recipe(title="b", recipe_book="Serious Eats", path=db_path)
    add_recipe_tag(b, "soup", path=db_path)
    c = insert_recipe(title="c", recipe_book="NYT Cooking", path=db_path)
    add_recipe_tag(c, "dessert", path=db_path)

    # Tag=soup AND book=NYT → only "a"
    out = search_recipes(
        tags=("soup",), tag_logic="and",
        recipe_books=("NYT Cooking",), path=db_path,
    )
    assert [r.title for r in out] == ["a"]


def test_get_recipe_roundtrip_includes_recipe_book(db_path: Path) -> None:
    rid = insert_recipe(
        title="Full", recipe_book="Cook's Illustrated", path=db_path,
    )
    assert get_recipe(rid, path=db_path).recipe_book == "Cook's Illustrated"


def test_delete_recipe_removes_row_and_cascades(db_path: Path) -> None:
    rid = insert_recipe(title="To Delete", path=db_path)
    insert_ingredient(recipe_id=rid, name="Flour", sort_order=0, path=db_path)
    delete_recipe(rid, path=db_path)
    with pytest.raises(KeyError):
        get_recipe(rid, path=db_path)
    # FK cascade should have removed the ingredient
    conn = _sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    row = conn.execute("SELECT id FROM ingredients WHERE recipe_id = ?", (rid,)).fetchone()
    conn.close()
    assert row is None


def test_delete_recipe_missing_raises(db_path: Path) -> None:
    with pytest.raises(KeyError):
        delete_recipe(99999, path=db_path)


def test_add_ingredient_returns_id_and_bumps_updated_at(db_path: Path) -> None:
    rid = insert_recipe(title="Pasta", path=db_path)
    before = get_recipe(rid, path=db_path).updated_at
    time.sleep(0.01)
    iid = add_ingredient(rid, name="Spaghetti", qty_per_serving=100.0, unit="g", sort_order=0, path=db_path)
    assert isinstance(iid, int)
    assert get_recipe(rid, path=db_path).updated_at > before


def test_add_ingredient_missing_recipe_raises(db_path: Path) -> None:
    with pytest.raises(KeyError):
        add_ingredient(99999, name="Flour", sort_order=0, path=db_path)


def test_update_ingredient_partial_and_bumps_parent(db_path: Path) -> None:
    rid = insert_recipe(title="Pizza", path=db_path)
    iid = insert_ingredient(recipe_id=rid, name="Tomato", sort_order=0, path=db_path)
    before = get_recipe(rid, path=db_path).updated_at
    time.sleep(0.01)
    update_ingredient(iid, name="San Marzano Tomato", path=db_path)
    assert get_recipe(rid, path=db_path).updated_at > before
    conn = _sqlite3.connect(str(db_path))
    row = conn.execute("SELECT name FROM ingredients WHERE id = ?", (iid,)).fetchone()
    conn.close()
    assert row[0] == "San Marzano Tomato"


def test_update_ingredient_missing_raises(db_path: Path) -> None:
    with pytest.raises(KeyError):
        update_ingredient(99999, name="X", path=db_path)


def test_delete_ingredient_and_bumps_parent(db_path: Path) -> None:
    rid = insert_recipe(title="Salad", path=db_path)
    iid = insert_ingredient(recipe_id=rid, name="Lettuce", sort_order=0, path=db_path)
    before = get_recipe(rid, path=db_path).updated_at
    time.sleep(0.01)
    delete_ingredient(iid, path=db_path)
    assert get_recipe(rid, path=db_path).updated_at > before
    conn = _sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM ingredients WHERE id = ?", (iid,)).fetchone()
    conn.close()
    assert row is None


def test_delete_ingredient_missing_raises(db_path: Path) -> None:
    with pytest.raises(KeyError):
        delete_ingredient(99999, path=db_path)


def test_set_recipe_tags_replace_style(db_path: Path) -> None:
    rid = insert_recipe(title="Stew", path=db_path)
    add_recipe_tag(rid, "hearty", path=db_path)
    add_recipe_tag(rid, "winter", path=db_path)
    set_recipe_tags(rid, ["new-tag", "another-tag"], path=db_path)
    conn = _sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT t.name FROM tags t JOIN recipe_tags rt ON rt.tag_id = t.id WHERE rt.recipe_id = ?",
        (rid,),
    ).fetchall()
    conn.close()
    assert {r[0] for r in rows} == {"new-tag", "another-tag"}


def test_set_recipe_tags_lowercase_dedup_and_bumps_updated_at(db_path: Path) -> None:
    rid = insert_recipe(title="Curry", path=db_path)
    before = get_recipe(rid, path=db_path).updated_at
    time.sleep(0.01)
    set_recipe_tags(rid, ["Asian", "ASIAN", " spicy "], path=db_path)
    after = get_recipe(rid, path=db_path).updated_at
    assert after > before
    tags = list_all_tags(path=db_path)
    assert tags == ["asian", "spicy"]  # deduped + lowercased


def test_set_recipe_tags_gc_orphans(db_path: Path) -> None:
    rid = insert_recipe(title="Soup", path=db_path)
    add_recipe_tag(rid, "orphan-soon", path=db_path)
    # Replace tags — "orphan-soon" has no other recipe links, so should be GC'd
    set_recipe_tags(rid, ["fresh"], path=db_path)
    conn = _sqlite3.connect(str(db_path))
    row = conn.execute("SELECT id FROM tags WHERE name = 'orphan-soon'").fetchone()
    conn.close()
    assert row is None


def test_set_recipe_tags_missing_recipe_raises(db_path: Path) -> None:
    with pytest.raises(KeyError):
        set_recipe_tags(99999, ["tag"], path=db_path)


def test_set_recipe_tags_empty_list_clears_all_tags(db_path: Path) -> None:
    """Passing [] removes every tag from the recipe and GCs orphans."""
    rid = insert_recipe(title="Stew", path=db_path)
    add_recipe_tag(rid, "hearty", path=db_path)
    add_recipe_tag(rid, "winter", path=db_path)
    set_recipe_tags(rid, [], path=db_path)
    conn = _sqlite3.connect(str(db_path))
    rt_count = conn.execute(
        "SELECT COUNT(*) FROM recipe_tags WHERE recipe_id = ?", (rid,)
    ).fetchone()[0]
    tag_count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    conn.close()
    assert rt_count == 0
    assert tag_count == 0  # GC removed both orphaned tags


def test_delete_recipe_cascades_to_recipe_tags(db_path: Path) -> None:
    """delete_recipe relies on FK ON DELETE CASCADE to clear recipe_tags rows."""
    rid = insert_recipe(title="Tagged", path=db_path)
    add_recipe_tag(rid, "asian", path=db_path)
    add_recipe_tag(rid, "soup", path=db_path)
    delete_recipe(rid, path=db_path)
    conn = _sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    rows = conn.execute(
        "SELECT 1 FROM recipe_tags WHERE recipe_id = ?", (rid,)
    ).fetchall()
    conn.close()
    assert rows == []


def test_update_recipe_multi_field(db_path: Path) -> None:
    """A single update_recipe call can change several columns at once."""
    rid = insert_recipe(title="Original", base_servings=4, path=db_path)
    update_recipe(
        rid,
        title="Updated",
        base_servings=8,
        instructions="New steps.",
        cook_time_min=30,
        source="cookbook",
        path=db_path,
    )
    r = get_recipe(rid, path=db_path)
    assert r.title == "Updated"
    assert r.base_servings == 8
    assert r.instructions == "New steps."
    assert r.cook_time_min == 30
    assert r.source == "cookbook"


# ---------------------------------------------------------------------------
# conn-passed path tests (single-transaction save invariant for A2 UI)
# ---------------------------------------------------------------------------


def test_list_ingredients_empty(db_path: Path) -> None:
    rid = insert_recipe(title="Empty Recipe", path=db_path)
    result = list_ingredients(rid, path=db_path)
    assert result == []


def test_list_ingredients_populated(db_path: Path) -> None:
    from meal_planner.db import insert_ingredient
    rid = insert_recipe(title="Pasta", path=db_path)
    insert_ingredient(recipe_id=rid, name="Flour", sort_order=1, path=db_path)
    insert_ingredient(recipe_id=rid, name="Egg", sort_order=0, path=db_path)
    result = list_ingredients(rid, path=db_path)
    assert len(result) == 2
    # Ordered by sort_order first, then name
    assert result[0].name == "Egg"
    assert result[1].name == "Flour"
    assert all(r.recipe_id == rid for r in result)


def test_get_recipe_tags_empty(db_path: Path) -> None:
    rid = insert_recipe(title="Untagged", path=db_path)
    assert get_recipe_tags(rid, path=db_path) == []


def test_get_recipe_tags_populated(db_path: Path) -> None:
    rid = insert_recipe(title="Tagged", path=db_path)
    add_recipe_tag(rid, "italian", path=db_path)
    add_recipe_tag(rid, "asian", path=db_path)
    tags = get_recipe_tags(rid, path=db_path)
    assert tags == ["asian", "italian"]  # sorted alpha


def test_update_recipe_conn_passed(db_path: Path) -> None:
    """update_recipe uses caller-supplied conn and does not commit/close it."""
    from meal_planner.db import _get_conn
    rid = insert_recipe(title="ConnTest", path=db_path)
    with _get_conn(db_path) as conn:
        update_recipe(rid, title="ConnUpdated", conn=conn)
    r = get_recipe(rid, path=db_path)
    assert r.title == "ConnUpdated"


def test_delete_recipe_conn_passed(db_path: Path) -> None:
    """delete_recipe uses caller-supplied conn."""
    from meal_planner.db import _get_conn
    rid = insert_recipe(title="ToDeleteConn", path=db_path)
    with _get_conn(db_path) as conn:
        delete_recipe(rid, conn=conn)
    with pytest.raises(KeyError):
        get_recipe(rid, path=db_path)


def test_add_ingredient_conn_passed(db_path: Path) -> None:
    """add_ingredient uses caller-supplied conn and returns the new id."""
    from meal_planner.db import _get_conn
    rid = insert_recipe(title="ConnIngredient", path=db_path)
    with _get_conn(db_path) as conn:
        iid = add_ingredient(rid, name="Salt", sort_order=0, conn=conn)
    assert isinstance(iid, int)


def test_update_ingredient_conn_passed(db_path: Path) -> None:
    """update_ingredient uses caller-supplied conn."""
    from meal_planner.db import _get_conn, insert_ingredient
    rid = insert_recipe(title="ConnUpdateIngr", path=db_path)
    iid = insert_ingredient(recipe_id=rid, name="Pepper", sort_order=0, path=db_path)
    with _get_conn(db_path) as conn:
        update_ingredient(iid, name="Black Pepper", conn=conn)
    conn2 = _sqlite3.connect(str(db_path))
    row = conn2.execute("SELECT name FROM ingredients WHERE id = ?", (iid,)).fetchone()
    conn2.close()
    assert row[0] == "Black Pepper"


def test_delete_ingredient_conn_passed(db_path: Path) -> None:
    """delete_ingredient uses caller-supplied conn."""
    from meal_planner.db import _get_conn, insert_ingredient
    rid = insert_recipe(title="ConnDeleteIngr", path=db_path)
    iid = insert_ingredient(recipe_id=rid, name="Garlic", sort_order=0, path=db_path)
    with _get_conn(db_path) as conn:
        delete_ingredient(iid, conn=conn)
    conn2 = _sqlite3.connect(str(db_path))
    row = conn2.execute("SELECT id FROM ingredients WHERE id = ?", (iid,)).fetchone()
    conn2.close()
    assert row is None


def test_set_recipe_tags_conn_passed(db_path: Path) -> None:
    """set_recipe_tags uses caller-supplied conn."""
    from meal_planner.db import _get_conn
    rid = insert_recipe(title="ConnTags", path=db_path)
    with _get_conn(db_path) as conn:
        set_recipe_tags(rid, ["italian", "pasta"], conn=conn)
    tags = list_all_tags(path=db_path)
    assert "italian" in tags
    assert "pasta" in tags


def test_single_transaction_save_path(db_path: Path) -> None:
    """All mutation fns share one conn — either all commit or none do."""
    from meal_planner.db import _get_conn, insert_ingredient
    rid = insert_recipe(title="TxnTest", path=db_path)
    iid = insert_ingredient(recipe_id=rid, name="Flour", sort_order=0, path=db_path)
    # Simulate a mid-transaction failure — raise after update_recipe, before set_recipe_tags
    with _get_conn(db_path) as conn:
        update_recipe(rid, title="TxnUpdated", conn=conn)
        # Verify the update is visible within the same connection
        row = conn.execute("SELECT title FROM recipes WHERE id = ?", (rid,)).fetchone()
        assert row[0] == "TxnUpdated"
        # Rollback by raising
        try:
            conn.execute("INVALID SQL")
        except Exception:
            conn.rollback()
    # After rollback the original title should still be there
    assert get_recipe(rid, path=db_path).title == "TxnTest"


def test_update_ingredient_clears_text_field_to_empty_string(db_path: Path) -> None:
    """update_ingredient must accept "" so the UI can clear unit/notes/section."""
    rid = insert_recipe(title="Clearable", path=db_path)
    iid = insert_ingredient(
        recipe_id=rid, name="Salt", unit="cups", notes="taste",
        todoist_section="pantry", sort_order=0, path=db_path,
    )
    update_ingredient(iid, unit="", notes="", todoist_section="", path=db_path)
    conn = _sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT unit, notes, todoist_section FROM ingredients WHERE id = ?", (iid,)
    ).fetchone()
    conn.close()
    assert row == ("", "", "")


def test_delete_recipe_garbage_collects_orphan_tags(db_path: Path) -> None:
    """When the last recipe using a tag is deleted, the tag row is GC'd."""
    r_keep = insert_recipe(title="Keeper", path=db_path)
    r_drop = insert_recipe(title="Goner", path=db_path)
    add_recipe_tag(r_keep, "shared", path=db_path)
    add_recipe_tag(r_drop, "shared", path=db_path)
    add_recipe_tag(r_drop, "lonely", path=db_path)  # only on r_drop

    delete_recipe(r_drop, path=db_path)

    conn = _sqlite3.connect(str(db_path))
    tag_names = {
        row[0] for row in conn.execute("SELECT name FROM tags").fetchall()
    }
    conn.close()
    assert "shared" in tag_names  # still linked to r_keep
    assert "lonely" not in tag_names  # GC'd


# ---------------------------------------------------------------------------
# Sentinel (_UNSET) tests — verify None means "clear" and omission means "skip"
# ---------------------------------------------------------------------------


def test_update_recipe_clears_cook_time_to_null(db_path: Path) -> None:
    rid = insert_recipe(title="Soup", base_servings=4, cook_time_min=30, path=db_path)
    update_recipe(rid, cook_time_min=None, path=db_path)
    assert get_recipe(rid, path=db_path).cook_time_min is None


def test_update_recipe_clears_instructions_to_null(db_path: Path) -> None:
    rid = insert_recipe(title="Stew", base_servings=4, instructions="Stir well.", path=db_path)
    update_recipe(rid, instructions=None, path=db_path)
    assert get_recipe(rid, path=db_path).instructions is None


def test_update_recipe_omitted_kwarg_does_not_clear(db_path: Path) -> None:
    rid = insert_recipe(title="Pasta", base_servings=4, cook_time_min=20, path=db_path)
    # Update only the title — cook_time_min not passed, must stay 20
    update_recipe(rid, title="Pasta Updated", path=db_path)
    r = get_recipe(rid, path=db_path)
    assert r.title == "Pasta Updated"
    assert r.cook_time_min == 20


def test_update_ingredient_clears_qty_to_null(db_path: Path) -> None:
    from meal_planner.db import insert_ingredient
    rid = insert_recipe(title="Curry", path=db_path)
    iid = insert_ingredient(
        recipe_id=rid, name="Coconut milk", qty_per_serving=2.5,
        unit="cups", sort_order=0, path=db_path,
    )
    update_ingredient(iid, qty_per_serving=None, path=db_path)
    ing = list_ingredients(rid, path=db_path)[0]
    assert ing.qty_per_serving is None


def test_update_ingredient_omitted_kwarg_does_not_clear(db_path: Path) -> None:
    from meal_planner.db import insert_ingredient
    rid = insert_recipe(title="Risotto", path=db_path)
    iid = insert_ingredient(
        recipe_id=rid, name="Arborio rice", qty_per_serving=1.5,
        unit="cups", sort_order=0, path=db_path,
    )
    # Update only the name — qty_per_serving not passed, must stay 1.5
    update_ingredient(iid, name="Carnaroli rice", path=db_path)
    ing = list_ingredients(rid, path=db_path)[0]
    assert ing.name == "Carnaroli rice"
    assert ing.qty_per_serving == 1.5
