"""Write document evidence through EKG's existing graph target interface."""
import csv
import hashlib
import uuid
from pathlib import Path


def digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def concept_tag_id(name: str) -> str:
    # Matches flowbi/ekg queries.ini's normalized concept_name -> md5(...)::uuid.
    normalized = "".join(c for c in name.lower().replace(" ", "_") if ord(c) < 128)
    return str(uuid.UUID(hashlib.md5(normalized.encode("utf-8")).hexdigest()))


def read_existing_ids(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None:
        return {}
    result = {}
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not {"concept", "name", "ekg_id"}.issubset(reader.fieldnames or []):
            raise ValueError("Existing-ID CSV requires concept,name,ekg_id columns")
        for row in reader:
            key = (row["concept"].strip().casefold(), row["name"].strip().casefold())
            ekg_id = row["ekg_id"].strip()
            if not all(key) or not ekg_id:
                raise ValueError("Existing-ID CSV contains an empty field")
            if key in result and result[key] != ekg_id:
                raise ValueError(f"Conflicting EKG IDs for {key}")
            result[key] = ekg_id
    return result


class GraphWriter:
    def __init__(self, target, tenant: str, resolver, existing_ids: dict[tuple[str, str], str] | None = None):
        self.target = target
        self.tenant = tenant
        self.resolver = resolver
        self.existing_ids = existing_ids or {}
        self.tags_written = set()
        self.matched = 0
        self.unmatched = 0

    def write(self, chunk, entities, relations):
        prefix = "unstructured:" + self.tenant + ":"
        doc_id = prefix + "document:" + digest(chunk.source)
        chunk_id = prefix + "chunk:" + digest(chunk.source, chunk.source_hash, str(chunk.index))
        self.target.upsert_node(doc_id, "Document", {"uri": chunk.source, "content_sha256": chunk.source_hash})
        self.target.upsert_node(chunk_id, "DocumentChunk", {"source_uri": chunk.source,
                                                             "index": chunk.index, "text": chunk.text})
        self.target.upsert_edge(prefix + "contains:" + digest(doc_id, chunk_id), "CONTAINS",
                                doc_id, chunk_id, {})
        entity_ids = []
        for entity in entities:
            existing_id = self.resolver.resolve(entity.concept, entity.name)
            if existing_id is None:
                existing_id = self.existing_ids.get((entity.concept.casefold(), entity.name.casefold()))
            if existing_id:
                self.matched += 1
            else:
                self.unmatched += 1
            entity_id = existing_id or prefix + "entity:" + digest(entity.concept.casefold(), entity.name.casefold())
            entity_ids.append(entity_id)
            if not existing_id:
                self.target.upsert_node(entity_id, "DocumentEntity", {"name": entity.name,
                                                                        "concept": entity.concept})
            mention_id = prefix + "mention:" + digest(chunk_id, entity_id, entity.evidence)
            self.target.upsert_edge(mention_id, "MENTIONS", chunk_id, entity_id,
                                    {"evidence": entity.evidence})
            if not existing_id:
                tag_id = concept_tag_id(entity.concept)
                if tag_id not in self.tags_written:
                    tag_name = "".join(c for c in entity.concept.lower().replace(" ", "_") if ord(c) < 128)
                    self.target.upsert_concept_tag(tag_id, tag_name,
                                                   "business_domain", entity.concept)
                    self.tags_written.add(tag_id)
                self.target.upsert_tagged_as(entity_id, tag_id)
        for relation in relations:
            source_id, target_id = entity_ids[relation.source], entity_ids[relation.target]
            relation_id = prefix + "relation:" + digest(chunk_id, source_id, target_id,
                                                         relation.predicate, relation.evidence)
            self.target.upsert_edge(relation_id, relation.predicate, source_id, target_id,
                                    {"evidence": relation.evidence, "source_uri": chunk.source})
