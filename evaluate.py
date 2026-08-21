"""CLI for the offline evaluation harness.

Runs a model from MODEL_REGISTRY over the curated relevance set
synchronously (no Celery, no broker) and prints the aggregate metrics.
Persists each run to evaluation_runs by default so results are queryable
afterwards via /api/v1/evaluations -- pass --no-persist for a throwaway
look.

    python evaluate.py --model v1_vector
    python evaluate.py --model v2_tactical --param alpha=0.3
    python evaluate.py --model v2_tactical --sweep alpha=0.3,0.5,0.7

--sweep runs the same model once per value of one parameter and prints a
comparison table. It exists so a parameter's effect is reproducible from a
single command rather than reconstructed from a shell history; it does no
selection or tuning of its own.
"""
from __future__ import annotations

import argparse
import sys

from app.db.session import SessionLocal
from app.evaluation.relevance import DEFAULT_RELEVANCE_SET_PATH, load_relevance_set
from app.evaluation.runner import (
    MODEL_REGISTRY,
    EvaluationResult,
    persist_evaluation_result,
    run_evaluation,
)

METRIC_COLUMNS = [
    ("Precision@k", "mean_precision_at_k"),
    ("Recall@k", "mean_recall_at_k"),
    ("NDCG@k", "mean_ndcg_at_k"),
    ("PosConsistency", "mean_position_consistency"),
]


def _parse_param(raw: str) -> tuple[str, float]:
    name, _, value = raw.partition("=")
    if not name or not value:
        raise argparse.ArgumentTypeError(f"--param expects name=value, got {raw!r}")
    try:
        return name, float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--param value must be numeric, got {value!r}") from None


def _parse_sweep(raw: str) -> tuple[str, list[float]]:
    name, _, values = raw.partition("=")
    if not name or not values:
        raise argparse.ArgumentTypeError(f"--sweep expects name=v1,v2,v3, got {raw!r}")
    try:
        return name, [float(v) for v in values.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"--sweep values must be numeric, got {values!r}") from None


def _format_table(rows: list[tuple[str, EvaluationResult, int | None]], param_label: str) -> str:
    header = [param_label, *(name for name, _ in METRIC_COLUMNS), "errors", "run_id"]
    body = [
        [
            label,
            *(
                "n/a" if getattr(result, attr) is None else f"{getattr(result, attr):.4f}"
                for _, attr in METRIC_COLUMNS
            ),
            str(result.num_errors),
            "-" if run_id is None else str(run_id),
        ]
        for label, result, run_id in rows
    ]
    widths = [max(len(r[i]) for r in [header, *body]) for i in range(len(header))]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()
    return "\n".join([line(header), line(["-" * w for w in widths]), *(line(b) for b in body)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, choices=sorted(MODEL_REGISTRY))
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--dataset", default=str(DEFAULT_RELEVANCE_SET_PATH))
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        type=_parse_param,
        metavar="NAME=VALUE",
        help="model hyperparameter, repeatable (e.g. --param alpha=0.3)",
    )
    parser.add_argument(
        "--sweep",
        type=_parse_sweep,
        metavar="NAME=V1,V2,V3",
        help="run once per value of one hyperparameter and print a comparison table",
    )
    parser.add_argument("--no-persist", action="store_true", help="don't write to evaluation_runs")
    args = parser.parse_args(argv)

    base_params = dict(args.param)
    if args.sweep and args.sweep[0] in base_params:
        parser.error(f"{args.sweep[0]} given as both --param and --sweep")

    if args.sweep:
        sweep_name, sweep_values = args.sweep
        param_sets = [{**base_params, sweep_name: value} for value in sweep_values]
        param_label = sweep_name
    else:
        param_sets = [base_params]
        param_label = "params"

    db = SessionLocal()
    try:
        relevance_set = load_relevance_set(db, args.dataset)
        rows = []
        for params in param_sets:
            result = run_evaluation(
                db, relevance_set, model_version=args.model, k=args.k, model_params=params
            )
            run_id = None
            if not args.no_persist:
                run_id = persist_evaluation_result(db, result).id
                db.commit()
            label = (
                f"{params[param_label]:g}"
                if args.sweep
                else (", ".join(f"{n}={v:g}" for n, v in sorted(params.items())) or "defaults")
            )
            rows.append((label, result, run_id))
    finally:
        db.close()

    print(f"model={args.model}  dataset={relevance_set.dataset_name}  k={args.k}  "
          f"queries={rows[0][1].num_queries}")
    print(_format_table(rows, param_label))
    return 0


if __name__ == "__main__":
    sys.exit(main())
