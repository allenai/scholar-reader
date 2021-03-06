import logging
from argparse import ArgumentParser
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from common.bounding_box import compute_accuracy
from common.commands.base import add_arxiv_id_filter_args, load_arxiv_ids_using_args
from common.models import BoundingBox as BoundingBoxModel
from common.models import Entity as EntityModel
from common.models import EntityData as EntityDataModel
from common.models import Paper, Version, setup_database_connections
from common.types import ArxivId, BoundingBox, FloatRectangle
from peewee import fn

PageNumber = int
EntityType = str
Regions = List[FrozenSet[FloatRectangle]]
RegionsByPageAndType = Dict[Tuple[PageNumber, EntityType], Regions]


@dataclass(frozen=True)
class ExpectedActualPair:
    actual: Regions
    expected: Regions
    page: int
    entity_type: str


@dataclass
class AccuracyResults:
    arxiv_id: ArxivId
    num_matched: int
    num_expected: int
    num_actual: int
    precision: Optional[float]
    recall: Optional[float]


# When processing symbols, ignore those that are not of the types included in the gold set.
# For example, operators will be ignored because they are not in the gold set. If a symbol
# does not have an explicit type, it is assumed to be of one of the allowed types.
GOLD_SYMBOL_TYPES = ["function", "identifier"]


def fetch_boxes(
    arxiv_id: ArxivId, schema: str, version: Optional[int], types: List[str]
) -> Optional[RegionsByPageAndType]:
    # Discover the most recent version of data in the database for the paper.

    setup_database_connections(schema)
    if version is None:
        version_number = (
            Version.select(fn.Max(Version.index))
            .join(Paper)
            .where(Paper.arxiv_id == arxiv_id)
            .scalar()
        )
        if version_number is None:
            logging.warning(  # pylint: disable=logging-not-lazy
                "There are no entities for paper %s in database schema %s",
                arxiv_id,
                schema,
            )
            return None
        version = int(version_number)

    # Load bounding boxes from rows in the tables.
    # Note that filtering of entities occurs in two stages:
    # 1. In the 'where' function call in the query below.
    # 2. When iterating over the returned 'rows' object.
    # In general, it is encouraged to write filtering conditions in the where clause to
    # consolidate conditions and as it could be faster. That said, if it is particularly tricky
    # to filter (e.g., involving many interrelated conditions), filters can be written as rules
    # in the loop over the rows.
    rows = (
        EntityModel.select(
            EntityModel.id,
            EntityModel.type,
            BoundingBoxModel.left,
            BoundingBoxModel.top,
            BoundingBoxModel.width,
            BoundingBoxModel.height,
            BoundingBoxModel.page,
            # Aggregate data for an entity into an array, where each field
            # is a dictionary: {"key", "...", "value", "..."}. All values will
            # be of type string.
            fn.json_agg(
                fn.json_build_object(
                    "key", EntityDataModel.key, "value", EntityDataModel.value
                )
            ).alias("data"),
        )
        .join(Paper)
        .switch(EntityModel)
        .join(BoundingBoxModel)
        .switch(EntityModel)
        .join(EntityDataModel)
        .where(
            EntityModel.version == version,
            Paper.arxiv_id == arxiv_id,
            EntityModel.type << types,
        )
        .group_by(
            EntityModel.id,
            EntityModel.type,
            BoundingBoxModel.left,
            BoundingBoxModel.top,
            BoundingBoxModel.width,
            BoundingBoxModel.height,
            BoundingBoxModel.page,
        )
        .dicts()
    )

    boxes_by_entity_db_id: Dict[str, List[BoundingBox]] = defaultdict(list)
    types_by_entity_db_id: Dict[str, str] = {}
    for row in rows:
        if row["type"] == "symbol":
            if any(
                [
                    d["key"] == "type" and d["value"] not in GOLD_SYMBOL_TYPES
                    for d in row["data"]
                ]
            ):
                continue

        boxes_by_entity_db_id[row["id"]].append(
            BoundingBox(
                row["left"], row["top"], row["width"], row["height"], row["page"],
            )
        )
        types_by_entity_db_id[row["id"]] = row["type"]

    regions: RegionsByPageAndType = defaultdict(list)
    for db_id, bounding_boxes in boxes_by_entity_db_id.items():
        by_page = group_by_page(bounding_boxes)
        for page, page_boxes in by_page.items():
            key = (page, types_by_entity_db_id[db_id])
            rectangles = frozenset(
                [FloatRectangle(b.left, b.top, b.width, b.height) for b in page_boxes]
            )
            regions[key].append(rectangles)

    return regions


def group_by_page(boxes: List[BoundingBox]) -> Dict[PageNumber, List[BoundingBox]]:
    by_page: Dict[PageNumber, List[BoundingBox]] = defaultdict(list)
    for box in boxes:
        by_page[box.page].append(box)
    return by_page


def compute_accuracy_for_paper(
    arxiv_id: ArxivId,
    entity_types: List[str],
    expected_schema: str,
    expected_version: Optional[int],
    actual_schema: str,
    actual_version: Optional[int],
) -> Optional[AccuracyResults]:
    """ 'actual_version' and 'expected_version' default to the latest version if not provided. """

    # Load extracted bounding boxes from the database.
    actual = fetch_boxes(arxiv_id, actual_schema, actual_version, entity_types)
    if not actual:
        logging.warning("Failed to load extracted bounding boxes.")
        return None

    # Load gold bounding boxes from the database.
    expected = fetch_boxes(arxiv_id, expected_schema, expected_version, entity_types)
    if not expected:
        logging.warning("Failed to load ground truth bounding boxes.")
        return None

    expected_actual_pairs = []
    key_set: Set[Tuple[int, str]] = set()
    key_set.update(list(expected.keys()), list(actual.keys()))
    for key in sorted(key_set, key=lambda k: (k[1], k[0])):
        page_number, entity_type = key
        actual_regions = actual.get(key, [])
        expected_regions = expected.get(key, [])
        if actual_regions or expected_regions:
            expected_actual_pairs.append(
                ExpectedActualPair(
                    actual_regions, expected_regions, page_number, entity_type
                )
            )

    print(f"Computing page-by-page bounding box accuracies for paper {arxiv_id}....")
    total_expected = 0
    total_actual = 0
    total_matches = 0
    for pair in expected_actual_pairs:
        precision, recall, matches = compute_accuracy(
            pair.actual, pair.expected, minimum_iou=0.35
        )
        print(f"Accuracy for page {pair.page} entities of type {pair.entity_type}:")
        print(f"Precision: {precision}")
        print(f"Recall: {recall}")
        print(f"# Actual: {len(pair.actual)}")
        print(f"# Expected: {len(pair.expected)}")
        print(f"# Matched: {len(matches)}")
        print()

        total_actual += len(pair.actual)
        total_expected += len(pair.expected)
        total_matches += len(matches)

    print(f"Summary statistics for paper {arxiv_id}:")
    overall_precision = total_matches / total_actual if total_actual > 0 else None
    overall_recall = total_matches / total_expected if total_expected > 0 else None
    print(f"# Actual: {total_actual}")
    print(f"# Expected: {total_expected}")
    print(f"# Matches: {total_matches}")
    print(f"Precision: {overall_precision}")
    print(f"Recall: {overall_recall}")
    print()

    return AccuracyResults(
        arxiv_id,
        total_matches,
        total_expected,
        total_actual,
        overall_precision,
        overall_recall,
    )


if __name__ == "__main__":
    parser = ArgumentParser(
        description="Compute accuracy of bounding box extraction for a set of papers."
    )
    add_arxiv_id_filter_args(parser)
    parser.add_argument(
        "--expected-schema",
        type=str,
        default="public",
        help="Database schema containing the expected (gold / ground truth) bounding box data.",
    )
    parser.add_argument(
        "--expected-version",
        type=int,
        help=(
            "Index of the version of entity data that contains the expected (gold / ground "
            + "truth) data. Defaults to the most recent version in the schema."
        ),
    )
    parser.add_argument(
        "--actual-schema",
        type=str,
        default="public",
        help="Database schema containing the actual (extracted) bounding box data.",
    )
    parser.add_argument(
        "--actual-version",
        type=int,
        help=(
            "Index of the version of entity data that contains the extracted (actual) data "
            + "Defaults to the most recent version in the schema."
        ),
    )
    parser.add_argument(
        "--entity-types",
        nargs="+",
        type=str,
        help="Types of entities for which to compute bounding box accuracy.",
        default=["citation", "symbol", "sentence", "equation"],
        choices=["citation", "symbol", "sentence", "equation"],
    )
    args = parser.parse_args()

    arxiv_ids = load_arxiv_ids_using_args(args)
    if arxiv_ids is None:
        raise SystemExit(
            "Could not load arXiv IDs from the command line arguments provided."
        )

    processing_failures = 0
    missing_expected_boxes = 0
    missing_actual_boxes = 0
    precisions: List[float] = []
    recalls: List[float] = []

    for id_ in arxiv_ids:
        accuracy = compute_accuracy_for_paper(
            id_,
            args.entity_types,
            args.expected_schema,
            args.expected_version,
            args.actual_schema,
            args.actual_version,
        )

        if accuracy is None:
            processing_failures += 1
            continue

        if accuracy.num_actual > 0 and accuracy.precision:
            precisions.append(accuracy.precision)
        else:
            missing_actual_boxes += 1

        if accuracy.num_expected > 0 and accuracy.recall:
            recalls.append(accuracy.recall)
        else:
            missing_expected_boxes += 1

    print("Summary accuracy for all papers processed:")
    print(f"# papers that could not be processed: {processing_failures}")
    print(f"# papers missing actual boxes: {missing_actual_boxes}")
    print(f"# papers missing expected boxes: {missing_expected_boxes}")

    avg_precision = sum(precisions) / len(precisions) if precisions else None
    avg_recall = sum(recalls) / len(recalls) if recalls else None
    min_precision = min(precisions) if precisions else None
    min_recall = min(recalls) if recalls else None

    print(f"Average precision: {avg_precision}")
    print(f"Average recall: {avg_recall}")
    print(f"Minimum precision: {min_precision}")
    print(f"Minimum recall: {min_recall}")

