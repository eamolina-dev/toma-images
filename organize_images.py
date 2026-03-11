#!/usr/bin/env python3
"""Organiza imágenes de productos usando fuzzy matching.

Requisitos:
- pip install rapidfuzz

Uso:
    python organize_images.py \
      --csv toma-lista_de_precios-bebidas.csv \
      --source productos \
      --output imagenes_ordenadas
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rapidfuzz import fuzz


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class Product:
    name: str
    category: str
    slug: str


@dataclass
class MatchResult:
    product: Product
    source_path: Path
    destination_path: Path
    score: float


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    text = strip_accents(text.lower()).strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_category(category: str) -> str:
    normalized = normalize_text(category)
    return normalized.replace(" ", "-") if normalized else "sin-categoria"


def read_products(csv_path: Path) -> list[Product]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"name", "category", "slug"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"El CSV no tiene las columnas requeridas: {sorted(missing)}")

        products: list[Product] = []
        for row in reader:
            name = (row.get("name") or "").strip()
            category = (row.get("category") or "").strip()
            slug = (row.get("slug") or "").strip()
            if not slug:
                continue
            products.append(Product(name=name, category=category, slug=slug))
    return products


def find_images(source_dir: Path, output_dir: Path) -> list[Path]:
    files: list[Path] = []
    output_resolved = output_dir.resolve()
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            path.resolve().relative_to(output_resolved)
            continue
        except ValueError:
            pass
        files.append(path)
    return files


def score_candidate(product: Product, file_path: Path) -> float:
    filename = normalize_text(file_path.stem)
    slug = normalize_text(product.slug)
    name = normalize_text(product.name)

    if not filename:
        return 0.0

    scores = [
        fuzz.token_set_ratio(filename, slug),
        fuzz.partial_ratio(filename, slug),
    ]
    if name:
        scores.append(fuzz.token_set_ratio(filename, name))
        scores.append(fuzz.partial_ratio(filename, name))

    return max(scores)


def best_match_for_product(
    product: Product,
    candidates: Iterable[Path],
    threshold: float,
    min_gap: float,
) -> tuple[Path | None, float, float]:
    ranked: list[tuple[float, Path]] = []
    for file_path in candidates:
        score = score_candidate(product, file_path)
        ranked.append((score, file_path))

    if not ranked:
        return None, 0.0, 0.0

    ranked.sort(key=lambda x: x[0], reverse=True)
    best_score, best_path = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0

    if best_score < threshold:
        return None, best_score, second_score

    if best_score - second_score < min_gap:
        return None, best_score, second_score

    return best_path, best_score, second_score


def move_match(match: MatchResult) -> bool:
    match.destination_path.parent.mkdir(parents=True, exist_ok=True)
    if match.destination_path.exists():
        return False
    shutil.move(str(match.source_path), str(match.destination_path))
    return True


def build_destination(output_dir: Path, product: Product, source_path: Path) -> Path:
    category_folder = normalize_category(product.category)
    extension = source_path.suffix.lower() or ".jpg"
    filename = f"{product.slug}{extension}"
    return output_dir / category_folder / filename


def main() -> int:
    parser = argparse.ArgumentParser(description="Organiza imágenes por categoría y slug usando fuzzy matching")
    parser.add_argument("--csv", dest="csv_path", default="toma-lista_de_precios-bebidas.csv", help="Ruta del CSV")
    parser.add_argument("--source", dest="source_dir", default="productos", help="Carpeta raíz con imágenes")
    parser.add_argument("--output", dest="output_dir", default="imagenes_ordenadas", help="Carpeta destino")
    parser.add_argument("--threshold", type=float, default=72.0, help="Score mínimo (0-100) para aceptar match")
    parser.add_argument("--min-gap", type=float, default=5.0, help="Diferencia mínima vs segundo mejor match")
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    source_dir = Path(args.source_dir)
    output_dir = Path(args.output_dir)

    if not csv_path.exists():
        raise FileNotFoundError(f"No existe el CSV: {csv_path}")
    if not source_dir.exists():
        raise FileNotFoundError(f"No existe el directorio fuente: {source_dir}")

    products = read_products(csv_path)
    image_candidates = find_images(source_dir, output_dir)
    available_images = set(image_candidates)

    print(f"Productos leídos: {len(products)}")
    print(f"Imágenes candidatas encontradas: {len(available_images)}")

    assigned: list[MatchResult] = []
    manual_review: list[tuple[Product, float, float]] = []
    conflicts: list[MatchResult] = []

    for product in products:
        best_path, best_score, second_score = best_match_for_product(
            product=product,
            candidates=available_images,
            threshold=args.threshold,
            min_gap=args.min_gap,
        )

        if best_path is None:
            manual_review.append((product, best_score, second_score))
            continue

        destination = build_destination(output_dir, product, best_path)
        result = MatchResult(
            product=product,
            source_path=best_path,
            destination_path=destination,
            score=best_score,
        )

        moved = move_match(result)
        if not moved:
            conflicts.append(result)
            continue

        assigned.append(result)
        available_images.remove(best_path)

    print("\n=== LOG DE ASIGNACIONES ===")
    if not assigned:
        print("No hubo asignaciones automáticas.")
    for m in assigned:
        print(
            f"[{m.score:5.1f}] {m.source_path} -> {m.destination_path} "
            f"(slug={m.product.slug}, category={m.product.category})"
        )

    print("\n=== CONFLICTOS (NO SOBRESCRITURA) ===")
    if not conflicts:
        print("Sin conflictos de sobrescritura.")
    for c in conflicts:
        print(
            f"Destino existente, no movido: {c.source_path} -> {c.destination_path} "
            f"(slug={c.product.slug})"
        )

    print("\n=== REVISIÓN MANUAL ===")
    if not manual_review:
        print("Sin pendientes para revisión manual.")
    for p, best_score, second_score in manual_review:
        print(
            f"Sin match claro para slug='{p.slug}' name='{p.name}' "
            f"category='{p.category}' (best={best_score:.1f}, second={second_score:.1f})"
        )

    print("\nResumen:")
    print(f"  Asignadas: {len(assigned)}")
    print(f"  Conflictos (sin sobrescribir): {len(conflicts)}")
    print(f"  Revisión manual: {len(manual_review)}")
    print(f"  Imágenes sin usar: {len(available_images)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
