# Document ingestion for Flow.BI EKG

This extension reads document formats: plain text (`.txt`, `.text`), Markdown (`.md`, `.markdown`, `.mdown`), reStructuredText (`.rst`), AsciiDoc (`.adoc`, `.asciidoc`), Org (`.org`), HTML (`.html`, `.htm`), RTF (`.rtf`), text-based `.pdf`, Word (`.doc`, `.docx`), and PowerPoint (`.ppt`, `.pptx`). It retrieves **concept names only** from Flow.BI's PostgreSQL-compatible SQL API, extracts document entities and explicit relationships, and adds evidence-bearing nodes and edges to an existing EKG graph. It reuses EKG's graph adapters and logging handlers rather than copying their implementations. Data files (`.csv`, `.tsv`, `.log`, `.xls`, `.xlsx`) and source code files such as `.java`, `.py`, and `.js` are excluded by the format allowlist.

The Flow.BI concept names are **allowed entity types**, not the entity names to search for in text. For example, if Flow.BI returns `Customer` and `Product`, the LLM may extract `Acme` as a `Customer` and `Widget A` as a `Product`. The resolver then looks for a graph node with the corresponding EKG label and matching value property, such as `(:Customer {name: "Acme"})`. A unique match receives the document `MENTIONS` edge directly. Extracted relationships can likewise connect the matched existing nodes.

## Setup

Requires Python 3.10 or newer.

1. Check out [flowbi/ekg](https://github.com/flowbi/ekg) separately. Pass its checkout directory to `--ekg-path`.
2. Install `requirements.txt` and the EKG dependency for your selected target: `neo4j`, `gremlinpython`, `google-cloud-spanner`, or `networkx`. Cloud logging targets need the dependencies listed in EKG's README. For `.rtf`, `.doc`, and `.ppt` files, install LibreOffice and put `soffice` on `PATH`, or pass `--libreoffice /path/to/soffice`.
3. Set the Flow.BI SQL API PostgreSQL DSN and tenant UUIDs. Use a database account with `SELECT` access only.
4. Configure access to Flow.BI's SQL API. The script uses a configurable read-only query that returns one column named `concept_name`.
5. Create a JSON file with the options expected by EKG's `GraphTargetConfig` for your target. For Neo4j, for example:

   ```json
   {"endpoint":"bolt://localhost:7687","database":"neo4j","username":"neo4j","password":"REPLACE_ME"}
   ```

   Keep this file outside version control and readable only by its owner. GML and GraphML use `{"endpoint":"/absolute/path/to/existing.gml"}` or `.graphml` and modify the existing graph file.

```bash
export FLOW_SQL_DSN='host=sql-api.example.org port=5432 dbname=flowbi user=readonly password=... sslmode=require'
export GSR_CLIENT_ID='00000000-0000-0000-0000-000000000001'
export GSR_INST_ID='00000000-0000-0000-0000-000000000002'
export LLM_URL='https://your-llm-service.example/v1/chat/completions'
export LLM_MODEL='your-model'
export LLM_API_KEY='...'

python ingest.py ./documents --ekg-path /path/to/ekg \
  --target neo4j --target-options /private/path/neo4j.json
```

The LLM endpoint must expose an OpenAI-compatible chat completions response and support JSON object responses. `--extractor lexical` avoids an LLM and detects literal concept-name mentions only; it does not infer named instances or relationships and will usually **not** match existing customer or product nodes.

The readers include Word paragraphs and tables, PowerPoint slide text and tables, and HTML visible text. The `.rtf`, `.doc`, and `.ppt` readers convert to modern Office formats in a temporary directory before extraction. Scanned PDFs and text embedded in images need OCR before ingestion.

## Linking to EKG nodes

By default, the resolver matches graph nodes where the node label equals the Flow.BI concept name and the `name` property equals the extracted instance name. Both comparisons are exact, and multiple matches cause an error. If your EKG graph uses other labels or properties, provide mappings:

```json
{"customer":"Customer","product":"Product"}
```

Pass this file as `--graph-labels labels.json`. A separate `--value-properties properties.json` can contain `{"Customer":"customer_name","Product":"product_name"}`. This works for all six EKG targets through target-specific read queries.

For existing nodes without a queryable name property, provide `--existing-ids /path/to/ids.csv` as a fallback:

```csv
concept,name,ekg_id
Customer,Acme,Customer_12345
```

`ekg_id` must be the exact node ID produced by EKG's `id_column_expr` or ID-component logic. The script checks the graph first, then uses this CSV only when lookup returns no match. Verify CSV IDs against the graph before a run; EKG's target interface does not expose a portable node-existence lookup for supplied IDs, and some adapters silently skip an edge when an endpoint is absent.

Each file becomes a `Document`, each text part a `DocumentChunk`, linked by `CONTAINS`. A matched instance creates a `MENTIONS` edge from the chunk to the **existing typed graph node**, without changing that node. An unmatched instance becomes a new `DocumentEntity` with a concept tag, so its evidence is retained for review. Extracted relationships connect the resolved endpoints, with source URI and exact quoted evidence. IDs are deterministic across repeated runs of unchanged files. If a file changes, old chunk and evidence nodes remain; clean them up with a separately designed retention policy if needed.

## Configuration and logging

`--target` accepts `neo4j`, `neptune`, `cosmos`, `spanner`, `gml`, and `graphml`. This uses EKG's **incremental** graph methods, preserving its existing graph. EKG's console, CloudWatch, Azure Monitor, and Google Cloud logging are selected with `--log-targets` or `EKG_LOG_TARGETS` (comma-separated). Relevant variables include `EKG_LOG_GROUP`, `EKG_LOG_STREAM`, `AWS_REGION`, `GCP_PROJECT`, and `AZURE_MONITOR_CONNECTION_STRING`.

The pipeline fails if SQL retrieval, LLM extraction, document reading, or graph writes fail. It does not commit all chunks as one transaction across external systems, so a retry may be needed after a partial run. Stable IDs make repeated writes idempotent for unchanged input. The extracted data should be reviewed before relying on it for critical decisions.
