SELECT DISTINCT concept_name AS concept_name
FROM intf_automation.table_concepts
WHERE gsr_client = %(client_id)s::uuid
  AND gsr_inst = %(instance_id)s::uuid
  AND gsr_sdts = (
      SELECT gsr_sdts
      FROM control.control_sdts
      WHERE gsr_client = %(client_id)s::uuid
        AND gsr_inst = %(instance_id)s::uuid
        AND is_latest
  )
  AND concept_name IS NOT NULL
  AND btrim(concept_name) <> ''
ORDER BY concept_name
