"""Read concept names only from Flow.BI's PostgreSQL-compatible SQL API."""
from pathlib import Path
from contextlib import closing


def load_concepts(dsn: str, sql_file: Path, client_id: str, instance_id: str) -> list[str]:
    if not dsn:
        raise ValueError("FLOW_SQL_DSN is required")
    sql = sql_file.read_text(encoding="utf-8").strip()
    # A custom query is trusted configuration, but prohibit accidental writes.
    if not sql.lower().startswith(("select ", "with ")):
        raise ValueError("Concept SQL must be a SELECT or WITH query")
    import psycopg2
    with closing(psycopg2.connect(dsn)) as connection:
        connection.set_session(readonly=True)
        with connection.cursor() as cursor:
            cursor.execute(sql, {"client_id": client_id, "instance_id": instance_id})
            columns = [d[0].lower() for d in cursor.description]
            if columns != ["concept_name"]:
                raise ValueError("Concept query must return one column named concept_name")
            names = [str(row[0]).strip() for row in cursor.fetchall() if row[0] is not None]
    result = sorted(set(name for name in names if name), key=str.casefold)
    if not result:
        raise ValueError("Flow.BI SQL API returned no concept names")
    return result
