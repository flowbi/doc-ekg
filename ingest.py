#!/usr/bin/env python3
"""Extend an EKG-generated graph with evidence from text and Office documents."""
import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

from documents import chunks, iter_files
from extraction import lexical_extract, llm_extract
from flow_concepts import load_concepts
from graph_writer import GraphWriter, read_existing_ids
from resolver import ExistingNodeResolver


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("documents", nargs="+", type=Path,
                   help="TXT, Markdown-like, HTML, RTF, PDF, Word, PowerPoint files or directories")
    p.add_argument("--ekg-path", type=Path, required=True, help="Path to checked-out flowbi/ekg")
    p.add_argument("--concept-sql", type=Path, default=Path(__file__).with_name("concepts.sql"),
                   help="Override the concept query file")
    p.add_argument("--client-id", default=os.getenv("GSR_CLIENT_ID"))
    p.add_argument("--instance-id", default=os.getenv("GSR_INST_ID"))
    p.add_argument("--flow-dsn", default=os.getenv("FLOW_SQL_DSN"), help="PostgreSQL DSN for Flow.BI SQL API")
    p.add_argument("--target", choices=("neo4j", "neptune", "cosmos", "spanner", "gml", "graphml"),
                   default=os.getenv("EKG_GRAPH_TARGET", "neo4j"))
    p.add_argument("--target-options", type=Path, required=True,
                   help="JSON object of EKG GraphTargetConfig options; keep credentials private")
    p.add_argument("--existing-ids", type=Path,
                   help="Optional CSV fallback for nodes without a queryable value property")
    p.add_argument("--value-properties", type=Path,
                   help='Optional JSON concept-to-property map, e.g. {"Customer":"customer_name"}')
    p.add_argument("--graph-labels", type=Path,
                   help='Optional JSON concept-to-graph-label map, e.g. {"customer":"Customer"}')
    p.add_argument("--extractor", choices=("llm", "lexical"), default="llm")
    p.add_argument("--llm-url", default=os.getenv("LLM_URL"))
    p.add_argument("--llm-model", default=os.getenv("LLM_MODEL"))
    p.add_argument("--llm-key", default=os.getenv("LLM_API_KEY"))
    p.add_argument("--chunk-chars", type=int, default=5000)
    p.add_argument("--overlap", type=int, default=300)
    p.add_argument("--libreoffice", help="Path to LibreOffice/soffice for .rtf, .doc and .ppt")
    p.add_argument("--log-targets", default=os.getenv("EKG_LOG_TARGETS", "console"))
    p.add_argument("--log-level", default=os.getenv("EKG_LOG_LEVEL", "INFO"))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    for value, label in ((args.client_id, "GSR_CLIENT_ID"), (args.instance_id, "GSR_INST_ID")):
        try:
            uuid.UUID(value or "")
        except ValueError as exc:
            raise ValueError(f"{label} must be a UUID") from exc
    ekg_path = args.ekg_path.resolve()
    if not (ekg_path / "graph" / "__init__.py").is_file():
        raise ValueError("--ekg-path must point to the EKG repository root")
    sys.path.insert(0, str(ekg_path))
    from graph import GraphTargetConfig, create_graph_target
    from logging_ import configure_logging

    level = getattr(logging, args.log_level.upper(), None)
    if not isinstance(level, int):
        raise ValueError("Invalid log level")
    log = configure_logging(
        targets=[t.strip() for t in args.log_targets.split(",") if t.strip()],
        log_level=level,
        log_group=os.getenv("EKG_LOG_GROUP", "/ekg-etl"),
        log_stream=os.getenv("EKG_LOG_STREAM", "document-ingest"),
        region=os.getenv("AWS_REGION"),
        project=os.getenv("GCP_PROJECT"),
        azure_connection_string=os.getenv("AZURE_MONITOR_CONNECTION_STRING", ""),
    )
    files = sorted(iter_files(args.documents))
    if not files:
        raise ValueError("No supported documents found")
    concepts = load_concepts(args.flow_dsn, args.concept_sql, args.client_id, args.instance_id)
    log.info("Loaded %d concept names from Flow.BI SQL API", len(concepts))
    options = json.loads(args.target_options.read_text(encoding="utf-8"))
    if not isinstance(options, dict):
        raise ValueError("Target options must be a JSON object")
    target = create_graph_target(args.target, GraphTargetConfig(args.target, options))
    existing_ids = read_existing_ids(args.existing_ids)
    value_properties = json.loads(args.value_properties.read_text(encoding="utf-8")) if args.value_properties else {}
    if not isinstance(value_properties, dict):
        raise ValueError("Value properties must be a JSON object")
    graph_labels = json.loads(args.graph_labels.read_text(encoding="utf-8")) if args.graph_labels else {}
    if not isinstance(graph_labels, dict):
        raise ValueError("Graph labels must be a JSON object")
    resolver = ExistingNodeResolver(target, args.target, value_properties, graph_labels)
    writer = GraphWriter(target, f"{args.client_id}:{args.instance_id}", resolver, existing_ids)
    try:
        target.precheck()
        count = 0
        for path in files:
            for chunk in chunks(path, args.chunk_chars, args.overlap, args.libreoffice):
                if args.extractor == "lexical":
                    entities, relations = lexical_extract(chunk.text, concepts)
                else:
                    entities, relations = llm_extract(chunk.text, concepts, args.llm_url,
                                                      args.llm_model, args.llm_key)
                writer.write(chunk, entities, relations)
                count += 1
            log.info("Processed %s", path)
        log.info("Completed %d documents, %d chunks; entity mentions matched=%d, unmatched=%d",
                 len(files), count, writer.matched, writer.unmatched)
    finally:
        target.close()


if __name__ == "__main__":
    main()
