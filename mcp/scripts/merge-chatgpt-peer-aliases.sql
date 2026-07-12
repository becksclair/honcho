\set ON_ERROR_STOP on

begin;

-- Preserve useful alias configuration on the canonical peers. Existing
-- canonical values win if both sides define the same key.
update peers canonical
set configuration = alias.configuration || canonical.configuration
from peers alias
where canonical.workspace_name = 'openclaw'
  and alias.workspace_name = canonical.workspace_name
  and canonical.name = 'bex'
  and alias.name = 'Bex';

update peers canonical
set configuration = alias.configuration || canonical.configuration
from peers alias
where canonical.workspace_name = 'openclaw'
  and alias.workspace_name = canonical.workspace_name
  and canonical.name = 'sarah'
  and alias.name = 'Sarah';

-- Sarah's alias contains cards about both user aliases. Consolidate those
-- facts under the canonical bex target key without duplicating entries.
with merged_card as (
  select coalesce(jsonb_agg(to_jsonb(fact) order by fact), '[]'::jsonb) as card
  from (
    select distinct jsonb_array_elements_text(card) as fact
    from (
      select coalesce(internal_metadata->'bex_peer_card', '[]'::jsonb) as card
      from peers where workspace_name = 'openclaw' and name = 'sarah'
      union all
      select coalesce(internal_metadata->'Bex_peer_card', '[]'::jsonb)
      from peers where workspace_name = 'openclaw' and name = 'Sarah'
      union all
      select coalesce(internal_metadata->'Rebecca_Clair_peer_card', '[]'::jsonb)
      from peers where workspace_name = 'openclaw' and name = 'Sarah'
    ) cards
  ) facts
)
update peers
set internal_metadata = jsonb_set(internal_metadata, '{bex_peer_card}', merged_card.card, true)
from merged_card
where workspace_name = 'openclaw' and name = 'sarah';

-- Avoid primary-key collisions if a session already contains both an alias
-- and its canonical peer, then rewrite remaining memberships.
delete from session_peers alias
using session_peers canonical
where alias.workspace_name = 'openclaw'
  and canonical.workspace_name = alias.workspace_name
  and canonical.session_name = alias.session_name
  and canonical.peer_name = case
    when alias.peer_name in ('Bex', 'Rebecca_Clair') then 'bex'
    when alias.peer_name = 'Sarah' then 'sarah'
  end
  and alias.peer_name in ('Bex', 'Rebecca_Clair', 'Sarah');

update session_peers
set peer_name = case
  when peer_name in ('Bex', 'Rebecca_Clair') then 'bex'
  when peer_name = 'Sarah' then 'sarah'
end
where workspace_name = 'openclaw'
  and peer_name in ('Bex', 'Rebecca_Clair', 'Sarah');

update messages
set peer_name = case
  when peer_name in ('Bex', 'Rebecca_Clair') then 'bex'
  when peer_name = 'Sarah' then 'sarah'
end
where workspace_name = 'openclaw'
  and peer_name in ('Bex', 'Rebecca_Clair', 'Sarah');

update message_embeddings
set peer_name = case
  when peer_name in ('Bex', 'Rebecca_Clair') then 'bex'
  when peer_name = 'Sarah' then 'sarah'
end
where workspace_name = 'openclaw'
  and peer_name in ('Bex', 'Rebecca_Clair', 'Sarah');

update documents
set observer = case
      when observer in ('Bex', 'Rebecca_Clair') then 'bex'
      when observer = 'Sarah' then 'sarah'
      else observer
    end,
    observed = case
      when observed in ('Bex', 'Rebecca_Clair') then 'bex'
      when observed = 'Sarah' then 'sarah'
      else observed
    end
where workspace_name = 'openclaw'
  and (
    observer in ('Bex', 'Rebecca_Clair', 'Sarah')
    or observed in ('Bex', 'Rebecca_Clair', 'Sarah')
  );

delete from collections
where workspace_name = 'openclaw'
  and (
    observer in ('Bex', 'Rebecca_Clair', 'Sarah')
    or observed in ('Bex', 'Rebecca_Clair', 'Sarah')
  );

delete from peers
where workspace_name = 'openclaw'
  and name in ('Bex', 'Rebecca_Clair', 'Sarah');

commit;
