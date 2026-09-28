"""Resolve (Flow.BI concept type, document instance name) to existing EKG IDs.

The EKG GraphTarget API is write-only, so this module supplies narrow read
queries for each supported target. A match must be unique; ambiguity is an error.
"""
import re


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid graph value-property name: {value!r}")
    return value


def _gremlin_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _graphson_id(value):
    if isinstance(value, dict):
        if "@value" in value:
            return _graphson_id(value["@value"])
        if "id" in value:
            return _graphson_id(value["id"])
    return str(value)


def _graphson_items(value):
    if isinstance(value, dict) and "@value" in value:
        return _graphson_items(value["@value"])
    return value if isinstance(value, list) else [value]


class ExistingNodeResolver:
    def __init__(self, target, target_name: str, value_properties: dict[str, str] | None = None,
                 graph_labels: dict[str, str] | None = None):
        self.target = target
        self.target_name = target_name
        if any(not isinstance(k, str) or not isinstance(v, str)
               for mapping in (value_properties or {}, graph_labels or {})
               for k, v in mapping.items()):
            raise ValueError("Graph mapping keys and values must be strings")
        self.value_properties = {key.casefold(): _identifier(value)
                                 for key, value in (value_properties or {}).items()}
        self.graph_labels = {key.casefold(): value for key, value in (graph_labels or {}).items()}
        self.cache = {}

    def resolve(self, concept: str, name: str) -> str | None:
        key = (concept.casefold(), name)
        if key not in self.cache:
            prop = self.value_properties.get(concept.casefold(), "name")
            graph_label = self.graph_labels.get(concept.casefold(), concept)
            matches = self._lookup(graph_label, name, prop)
            unique = list(dict.fromkeys(str(item) for item in matches))
            if len(unique) > 1:
                raise ValueError(f"Ambiguous graph match for concept={concept!r}, name={name!r}")
            self.cache[key] = unique[0] if unique else None
        return self.cache[key]

    def _lookup(self, concept: str, name: str, prop: str) -> list[str]:
        t = self.target
        if self.target_name == "neo4j":
            label = concept.replace("`", "``")
            with t._driver.session(database=t._database) as session:
                result = session.run(
                    f"MATCH (n:`{label}`) WHERE n.`{prop}` = $value "
                    "RETURN n._ekg_id AS id LIMIT 2", value=name)
                return [row["id"] for row in result if row["id"] is not None]
        if self.target_name in ("gml", "graphml"):
            return [node_id for node_id, data in t._graph.nodes(data=True)
                    if data.get("entity_label") == concept and data.get(prop) == name]
        if self.target_name == "spanner":
            table = _identifier(t._node_table)
            with t._db.snapshot() as snapshot:
                rows = snapshot.execute_sql(
                    f"SELECT id FROM {table} WHERE label = @label "
                    f"AND JSON_VALUE(properties, '$.{prop}') = @value LIMIT 2",
                    params={"label": concept, "value": name},
                    param_types={"label": t._spanner.param_types.STRING,
                                 "value": t._spanner.param_types.STRING})
                return [row[0] for row in rows]
        query = (f"g.V().hasLabel('{_gremlin_quote(concept)}')"
                 f".has('{_gremlin_quote(prop)}','{_gremlin_quote(name)}').limit(2).id()")
        if self.target_name == "cosmos":
            return [_graphson_id(item) for item in t._submit(query)]
        if self.target_name == "neptune":
            response = t._post(t._gremlin_url, json={"gremlin": query}).json()
            data = response.get("result", {}).get("data", [])
            return [_graphson_id(item) for item in _graphson_items(data)]
        raise ValueError(f"Unsupported graph target: {self.target_name}")
