"""Command line entry point.

    python -m pks parse    <corpus-dir>  -o out/parsed
    python -m pks extract  <parsed-dir>  -o out/items
    python -m pks relate   <items-dir>   -o out/relations
    python -m pks validate <items-dir>   --documents out/parsed
    python -m pks inspect  <items-dir>

`parse` and `validate` work offline. `extract` and `relate --llm` need
``PKS_API_KEY`` (and optionally ``PKS_API_BASE`` / ``PKS_MODEL``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import parse as parser
from . import relations, validate
from .ids import new_source_id
from .extract import LLMConfig

HERE = Path(__file__).resolve().parent
DEFAULT_SCHEMA = HERE.parent.parent / "schemas" / "knowledge_item.schema.json"


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_parse(args) -> int:
    source_dir = Path(args.source)
    if not source_dir.exists():
        print(f"no such directory: {source_dir}", file=sys.stderr)
        return 2

    files = sorted(p for p in source_dir.rglob("*")
                   if p.is_file() and p.suffix.lower() in parser_suffixes())
    if not files:
        print(f"no supported files in {source_dir}", file=sys.stderr)
        return 2

    out = Path(args.out)
    taken: set[str] = set()
    summary = []
    for path in files:
        source_id = new_source_id(args.source_type, taken)
        parsed = parser.parse_file(path, source_id, args.source_type)
        _write(out / f"{parsed.document['document_id']}.json", parsed.document)
        summary.append({
            "file": str(path),
            "document_id": parsed.document["document_id"],
            "blocks": len(parsed.document["structure"]["blocks"]),
            "status": parsed.document["parsing_status"],
            "warnings": [w["code"] for w in parsed.warnings],
        })

    _write(out / "_parse_summary.json", summary)
    total_blocks = sum(s["blocks"] for s in summary)
    print(f"parsed {len(summary)} file(s), {total_blocks} blocks -> {out}")
    for entry in summary:
        flag = "" if entry["status"] == "success" else f"  [{entry['status']}]"
        print(f"  {entry['document_id']}  {entry['blocks']:>4} blocks  {Path(entry['file']).name}{flag}")
    return 0


def parser_suffixes() -> set[str]:
    return {".md", ".markdown", ".txt", ".srt", ".vtt"}


def cmd_extract(args) -> int:
    parsed_dir = Path(args.parsed)
    documents = [json.loads(p.read_text(encoding="utf-8"))
                 for p in sorted(parsed_dir.glob("doc_*.json"))]
    if not documents:
        print(f"no doc_*.json files in {parsed_dir} — run `pks parse` first", file=sys.stderr)
        return 2

    config = LLMConfig()
    if not config.ready():
        print("PKS_API_KEY is not set; extraction needs a model", file=sys.stderr)
        return 2

    out = Path(args.out)
    total, errors = 0, []
    for document in documents:
        items, doc_errors = extend(config, document, args.schema, args.material_type)
        for item in items:
            _write(out / f"{item['knowledge_id']}.json", item)
        total += len(items)
        errors.extend(f"{document['document_id']}: {e}" for e in doc_errors)
        print(f"  {document['document_id']}  {len(items):>3} items")

    if errors:
        _write(out / "_errors.json", errors)
    print(f"extracted {total} item(s) from {len(documents)} document(s) -> {out}")
    return 0


def extend(config, document, schema, material_type):
    from .extract import extract
    return extract(document, config, Path(schema), material_type)


def cmd_relate(args) -> int:
    items = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(Path(args.items).glob("kn_*.json"))]
    if len(items) < 2:
        print(f"need at least two kn_*.json files in {args.items} — run `pks extract` first",
              file=sys.stderr)
        return 2

    config = LLMConfig() if args.llm else None
    edges, stats = relations.build_edges(
        items, config=config,
        min_confidence=args.min_confidence,
        max_pairs=args.max_pairs,
    )
    graph = relations.index(edges, [i["knowledge_id"] for i in items])
    consistency = relations.check_symmetry(edges, graph)
    relations.dump(edges, graph, Path(args.out))

    print(json.dumps({**stats, **consistency}, ensure_ascii=False, indent=2))
    print(f"edges -> {Path(args.out) / 'edges.json'}")
    return 0


def cmd_validate(args) -> int:
    items = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(Path(args.items).glob("kn_*.json"))]
    documents = {}
    if args.documents:
        for path in sorted(Path(args.documents).glob("doc_*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            documents[doc["document_id"]] = doc

    edges = None
    if args.relations:
        edges_path = Path(args.relations) / "edges.json"
        if edges_path.exists():
            edges = json.loads(edges_path.read_text(encoding="utf-8"))

    report = validate.run(items, documents, Path(args.schema), edges)
    print(json.dumps({k: report[k] for k in ("items", "documents", "edges", "issues", "by_kind")},
                     ensure_ascii=False, indent=2))
    if report["detail"] and args.verbose:
        for issue in report["detail"][:40]:
            print(f"  [{issue['kind']}] {issue['target']}: {issue['detail']}")
    return 0 if report["issues"] == 0 else 1


def cmd_inspect(args) -> int:
    items = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(Path(args.items).glob("kn_*.json"))]
    if not items:
        print("no items", file=sys.stderr)
        return 2
    from collections import Counter

    domains = Counter(i["domain"][0] for i in items)
    types = Counter(i["content_type"] for i in items)
    attributions = Counter((i.get("attribution") or [{}])[0].get("kind") for i in items)
    epistemic = Counter(i["epistemic_status"] for i in items)

    print(f"items {len(items)}\n")
    for label, counter in (("domain", domains), ("content_type", types),
                           ("attribution", attributions), ("epistemic", epistemic)):
        print(f"{label}:")
        for key, count in counter.most_common():
            print(f"  {key:<18} {count}")
        print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pks", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="source files -> parsed documents")
    p.add_argument("source")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--source-type", default="local")
    p.set_defaults(func=cmd_parse)

    e = sub.add_parser("extract", help="parsed documents -> knowledge items")
    e.add_argument("parsed")
    e.add_argument("-o", "--out", required=True)
    e.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    e.add_argument("--material-type", default="knowledge_note")
    e.set_defaults(func=cmd_extract)

    r = sub.add_parser("relate", help="items -> candidate edges")
    r.add_argument("items")
    r.add_argument("-o", "--out", required=True)
    r.add_argument("--llm", action="store_true", help="use a model instead of rules")
    r.add_argument("--min-confidence", type=float, default=0.5)
    r.add_argument("--max-pairs", type=int, default=400)
    r.set_defaults(func=cmd_relate)

    v = sub.add_parser("validate", help="schema, provenance and epistemic checks")
    v.add_argument("items")
    v.add_argument("--documents")
    v.add_argument("--relations")
    v.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    v.add_argument("--verbose", action="store_true")
    v.set_defaults(func=cmd_validate)

    i = sub.add_parser("inspect", help="distribution summary")
    i.add_argument("items")
    i.set_defaults(func=cmd_inspect)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
