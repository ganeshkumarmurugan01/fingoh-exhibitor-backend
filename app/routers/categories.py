from fastapi import APIRouter, Query
from app.db import get_db

router = APIRouter()

@router.get("/categories")
def list_categories(
    industry: str = Query("pharma"),
    level: int = Query(None),
    parent_id: str = Query(None),
):
    """Public endpoint — returns category master entries."""
    db = get_db()
    q = db.table("category_master").select("*").eq("industry", industry)
    if level is not None:
        q = q.eq("level", level)
    if parent_id:
        q = q.eq("parent_id", parent_id)
    result = q.order("code").execute()
    return result.data or []

@router.get("/categories/tree")
def category_tree(industry: str = Query("pharma")):
    """Returns full category tree grouped by L1 > L2 > L3."""
    db = get_db()
    all_cats = db.table("category_master").select("*").eq("industry", industry).order("code").execute()
    cats = all_cats.data or []

    # Build id map
    id_map = {c["id"]: c for c in cats}

    # Group into tree
    l1 = [c for c in cats if c["level"] == 1]
    for c1 in l1:
        c1["children"] = []
        l2 = [c for c in cats if c["level"] == 2 and c.get("parent_id") == c1["id"]]
        for c2 in l2:
            c2["children"] = [c for c in cats if c["level"] == 3 and c.get("parent_id") == c2["id"]]
            c1["children"].append(c2)

    return l1

@router.post("/categories/custom")
def add_custom_category(
    industry: str,
    name: str,
    parent_id: str = None,
    level: int = 3,
):
    """Add a custom category submitted by an exhibitor."""
    db = get_db()
    result = db.table("category_master").insert({
        "industry": industry,
        "level": level,
        "name": name,
        "parent_id": parent_id,
        "is_custom": True,
    }).execute()
    return result.data[0] if result.data else {}
