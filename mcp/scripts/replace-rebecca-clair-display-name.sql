\set ON_ERROR_STOP on

begin;

update documents
set content = replace(content, 'Rebecca Clair', 'Bex'),
    internal_metadata = replace(internal_metadata::text, 'Rebecca Clair', 'Bex')::jsonb,
    embedding = null,
    sync_state = 'pending',
    last_sync_at = null,
    sync_attempts = 0
where strpos(content, 'Rebecca Clair') > 0
   or strpos(internal_metadata::text, 'Rebecca Clair') > 0;

update messages
set content = replace(content, 'Rebecca Clair', 'Bex'),
    metadata = replace(metadata::text, 'Rebecca Clair', 'Bex')::jsonb,
    internal_metadata = replace(internal_metadata::text, 'Rebecca Clair', 'Bex')::jsonb
where strpos(content, 'Rebecca Clair') > 0
   or strpos(metadata::text, 'Rebecca Clair') > 0
   or strpos(internal_metadata::text, 'Rebecca Clair') > 0;

update message_embeddings
set content = replace(content, 'Rebecca Clair', 'Bex'),
    embedding = null,
    sync_state = 'pending',
    last_sync_at = null,
    sync_attempts = 0
where strpos(content, 'Rebecca Clair') > 0;

update peers
set metadata = replace(metadata::text, 'Rebecca Clair', 'Bex')::jsonb,
    internal_metadata = replace(internal_metadata::text, 'Rebecca Clair', 'Bex')::jsonb
where strpos(metadata::text, 'Rebecca Clair') > 0
   or strpos(internal_metadata::text, 'Rebecca Clair') > 0;

update sessions
set metadata = replace(metadata::text, 'Rebecca Clair', 'Bex')::jsonb,
    internal_metadata = replace(internal_metadata::text, 'Rebecca Clair', 'Bex')::jsonb
where strpos(metadata::text, 'Rebecca Clair') > 0
   or strpos(internal_metadata::text, 'Rebecca Clair') > 0;

update queue
set payload = replace(payload::text, 'Rebecca Clair', 'Bex')::jsonb,
    work_unit_key = replace(work_unit_key, 'Rebecca Clair', 'Bex'),
    error = replace(error, 'Rebecca Clair', 'Bex')
where strpos(payload::text, 'Rebecca Clair') > 0
   or strpos(work_unit_key, 'Rebecca Clair') > 0
   or strpos(coalesce(error, ''), 'Rebecca Clair') > 0;

update queue_dead_letter
set payload = replace(payload::text, 'Rebecca Clair', 'Bex')::jsonb,
    work_unit_key = replace(work_unit_key, 'Rebecca Clair', 'Bex'),
    error = replace(error, 'Rebecca Clair', 'Bex')
where strpos(payload::text, 'Rebecca Clair') > 0
   or strpos(work_unit_key, 'Rebecca Clair') > 0
   or strpos(coalesce(error, ''), 'Rebecca Clair') > 0;

update queue_failed_backup_20260708
set payload = replace(payload::text, 'Rebecca Clair', 'Bex')::jsonb,
    work_unit_key = replace(work_unit_key, 'Rebecca Clair', 'Bex'),
    error = replace(error, 'Rebecca Clair', 'Bex')
where strpos(payload::text, 'Rebecca Clair') > 0
   or strpos(work_unit_key, 'Rebecca Clair') > 0
   or strpos(coalesce(error, ''), 'Rebecca Clair') > 0;

commit;
