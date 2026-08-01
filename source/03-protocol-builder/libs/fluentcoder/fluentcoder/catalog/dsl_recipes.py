"""Curated DSL/API recipes for LM authoring repair.

The recipe corpus is intentionally small and hand-authored. It captures
executable wrong/right snippets for API shapes that local models commonly
confuse, then ranks them against authoring failures and lookup requests.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol


class RecipeEmbedder(Protocol):
    """Minimal embedding interface used by the recipe store/retriever."""

    def embed(self, text: str) -> list[float]:
        ...


class HashingRecipeEmbedder:
    """Small deterministic local embedder with no model/runtime dependency."""

    def __init__(self, dimensions: int = 96) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


class FakeRecipeEmbedder:
    """Deterministic test embedder; aliases the production local embedder."""

    def __init__(self, dimensions: int = 96) -> None:
        self._delegate = HashingRecipeEmbedder(dimensions=dimensions)

    def embed(self, text: str) -> list[float]:
        return self._delegate.embed(text)


@dataclass(frozen=True)
class DslRecipe:
    name: str
    object_key: str
    action: str
    failure_category: str | None
    bad_pattern: str | None
    good_patterns: tuple[str, ...]
    context_text: str
    tags: tuple[str, ...]
    active: bool = True

    def retrieval_text(self) -> str:
        return " ".join(
            part
            for part in (
                self.name,
                self.object_key,
                self.action,
                self.failure_category or "",
                self.bad_pattern or "",
                " ".join(self.good_patterns),
                self.context_text,
                " ".join(self.tags),
            )
            if part
        )

    def to_public_dict(self, *, score: float | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "object_key": self.object_key,
            "action": self.action,
            "failure_category": self.failure_category,
            "bad_pattern": self.bad_pattern,
            "good_patterns": list(self.good_patterns),
            "context_text": self.context_text,
            "tags": list(self.tags),
        }
        if score is not None:
            payload["score"] = score
        return payload


def seed_curated_dsl_recipes(db: Any, embedder: RecipeEmbedder | None = None) -> int:
    """Insert/update the curated v1 recipe corpus.

    Returns the number of seed rows considered. Existing rows are updated in
    place, so repeated calls are idempotent.
    """

    embedder = embedder or HashingRecipeEmbedder()
    for recipe in CURATED_DSL_RECIPES:
        db.upsert_dsl_recipe(recipe.to_public_dict(), embedder=embedder)
    return len(CURATED_DSL_RECIPES)


def retrieve_dsl_recipes(
    db: Any,
    query: str,
    *,
    object_key: str | None = None,
    action: str | None = None,
    failure_category: str | None = None,
    bad_method: str | None = None,
    context_text: str | None = None,
    limit: int = 4,
    embedder: RecipeEmbedder | None = None,
) -> list[dict[str, Any]]:
    embedder = embedder or HashingRecipeEmbedder()
    parts = [query, object_key or "", action or "", failure_category or "", bad_method or "", context_text or ""]
    query_text = " ".join(part for part in parts if part)
    query_embedding = embedder.embed(query_text)
    rows = db.get_active_dsl_recipes()
    scored: list[tuple[float, DslRecipe]] = []
    for row in rows:
        recipe = _recipe_from_row(row)
        embedding = _load_embedding(row.get("embedding"))
        if not embedding:
            embedding = embedder.embed(recipe.retrieval_text())
        score = _cosine(query_embedding, embedding)
        score += _lexical_boost(
            recipe,
            query_text=query_text,
            object_key=object_key,
            action=action,
            failure_category=failure_category,
            bad_method=bad_method,
        )
        scored.append((score, recipe))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [recipe.to_public_dict(score=round(score, 6)) for score, recipe in scored[:limit]]


def _recipe_from_row(row: dict[str, Any]) -> DslRecipe:
    return DslRecipe(
        name=str(row["name"]),
        object_key=str(row["object_key"]),
        action=str(row["action"]),
        failure_category=row.get("failure_category"),
        bad_pattern=row.get("bad_pattern"),
        good_patterns=tuple(_json_list(row.get("good_patterns"))),
        context_text=str(row.get("context_text") or ""),
        tags=tuple(_json_list(row.get("tags"))),
        active=bool(row.get("active", 1)),
    )


def _json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [str(value)]
    if isinstance(data, list):
        return [str(item) for item in data]
    return [str(data)]


def _load_embedding(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, list):
        return [float(item) for item in value]
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [float(item) for item in data]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return sum(left[i] * right[i] for i in range(size))


def _lexical_boost(
    recipe: DslRecipe,
    *,
    query_text: str,
    object_key: str | None,
    action: str | None,
    failure_category: str | None,
    bad_method: str | None,
) -> float:
    score = 0.0
    q = query_text.lower()
    recipe_text = recipe.retrieval_text().lower()
    object_values = {recipe.object_key.lower(), recipe.object_key.lower().replace("wt.", "")}
    if object_key and object_key.lower() in object_values:
        score += 2.0
    elif any(value and value in q for value in object_values):
        score += 1.2
    if action and action.lower() == recipe.action.lower():
        score += 1.0
    if failure_category and recipe.failure_category == failure_category:
        score += 1.5
    if bad_method and recipe.bad_pattern and bad_method.lower() in recipe.bad_pattern.lower():
        score += 2.0
    for token in _tokens(q):
        if token in recipe_text:
            score += 0.05
    return score


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if token]


CURATED_DSL_RECIPES: tuple[DslRecipe, ...] = (
    DslRecipe(
        name="gripper_move_to_deck_slot",
        object_key="wt.gripper",
        action="move",
        failure_category="missing_method",
        bad_pattern="wt.gripper.place(source_plate, ('Nest61mm_Pos', 1))",
        good_patterns=("wt.gripper.move(source_plate, to=('Nest61mm_Pos', 1))",),
        context_text="Move labware with the gripper to a deck slot. Gripper has move(), not place().",
        tags=("gripper", "deck", "slot", "place"),
    ),
    DslRecipe(
        name="gripper_stack_plate_onto_magnet",
        object_key="wt.gripper",
        action="move",
        failure_category="missing_method",
        bad_pattern="wt.gripper.place(source_plate, magnet_rack)",
        good_patterns=("wt.gripper.move(source_plate, onto=magnet_rack)",),
        context_text="Stack a plate onto a magnet rack with gripper.move(..., onto=magnet_rack).",
        tags=("gripper", "magnet", "stack", "place"),
    ),
    DslRecipe(
        name="mca96_tip_cycle_and_transfer",
        object_key="wt.mca96",
        action="pick_up aspirate dispense return_tips",
        failure_category="missing_method",
        bad_pattern="wt.mca96.get_tips(tips)",
        good_patterns=(
            "head = wt.mca96",
            "head.mount_adapter()",
            "head.pick_up(tips)",
            "head.aspirate(source, 20.0, liquid_class='Water Free Single')",
            "head.dispense(dest, 20.0, liquid_class='Water Free Single')",
            "head.return_tips(tips)",
            "head.drop_adapter()",
        ),
        context_text="MCA96 uses pick_up/return_tips, while LiHa uses get_tips/drop_tips.",
        tags=("mca96", "tips", "aspirate", "dispense", "get_tips"),
    ),
    DslRecipe(
        name="mca96_empty_tips_to_waste",
        object_key="wt.mca96",
        action="empty_tips",
        failure_category=None,
        bad_pattern=None,
        good_patterns=("head.empty_tips(waste, 200.0, liquid_class='<from ZEIA liquid_classes>')",),
        context_text="Use empty_tips to expel residual liquid from mounted MCA96 tips to waste. Liquid class from ZEIA/recipe only.",
        tags=("mca96", "waste", "empty_tips"),
    ),
    DslRecipe(
        name="liha_columnwise_transfer_with_well_offset",
        object_key="wt.liha",
        action="get_tips aspirate dispense drop_tips",
        failure_category="missing_method",
        bad_pattern="wt.liha.pick_up(tips)",
        good_patterns=(
            "head = wt.liha",
            "head.get_tips(tips)",
            "for col in range(12):",
            "    head.aspirate(source, 20.0, liquid_class='Water Free Single')",
            "    head.dispense(dest, 20.0, liquid_class='Water Free Single', well_offset=col * 8)",
            "head.drop_tips()",
        ),
        context_text="LiHa uses get_tips/drop_tips and column-wise well_offset for plate fills.",
        tags=("liha", "well_offset", "column", "pick_up"),
    ),
    DslRecipe(
        name="labware_fill_all_and_well_access",
        object_key="labware",
        action="fill_all well all_wells",
        failure_category="missing_method",
        bad_pattern="plate['A1']",
        good_patterns=(
            "plate.fill_all(water, 80.0)",
            "plate.well('A1').add_layer(sample, 20.0)",
            "for well in plate.all_wells():",
        ),
        context_text="Labware is accessed through methods: fill_all(), well('A1'), and all_wells().",
        tags=("labware", "plate", "subscript", "fill", "all_wells"),
    ),
    DslRecipe(
        name="trough_fill_all_not_fill",
        object_key="labware",
        action="fill_all",
        failure_category="missing_method",
        bad_pattern="trough.fill(water, 5000.0)",
        good_patterns=("trough.fill_all(water, 5000.0)",),
        context_text="Trough labware uses fill_all() to seed the pool/wells; there is no fill().",
        tags=("trough", "fill", "fill_all"),
    ),
    DslRecipe(
        name="waste_chute_dispense_patterns",
        object_key="waste",
        action="dispense empty_tips",
        failure_category=None,
        bad_pattern="waste.fill(...)",
        good_patterns=(
            "head.dispense(waste, 200.0, liquid_class='Water Free Single')",
            "head.empty_tips(waste, 20.0)",
        ),
        context_text="Waste receives liquid through head dispense or empty_tips calls, not labware fill calls.",
        tags=("waste", "dispense", "empty_tips"),
    ),
)
