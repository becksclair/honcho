\set ON_ERROR_STOP on

begin;

with normalized as (
  select min(ordinality) as first_position,
         case
           when fact in (
             'IDENTITY: Alias: Bex',
             'IDENTITY: Name: Bex',
             'IDENTITY: Name: Rebecca Clair; preferred name: Bex'
           ) then 'IDENTITY: Name: Rebecca Clair; preferred name: Bex'
           else fact
         end as fact
  from peers
  cross join lateral jsonb_array_elements_text(
    internal_metadata->'bex_peer_card'
  ) with ordinality as card(fact, ordinality)
  where workspace_name = 'openclaw'
    and name = 'sarah'
  group by case
    when fact in (
      'IDENTITY: Alias: Bex',
      'IDENTITY: Name: Bex',
      'IDENTITY: Name: Rebecca Clair; preferred name: Bex'
    ) then 'IDENTITY: Name: Rebecca Clair; preferred name: Bex'
    else fact
  end
), rebuilt as (
  select jsonb_agg(to_jsonb(fact) order by first_position) as peer_card
  from normalized
)
update peers
set internal_metadata = jsonb_set(
  internal_metadata,
  '{bex_peer_card}',
  rebuilt.peer_card,
  true
)
from rebuilt
where workspace_name = 'openclaw'
  and name = 'sarah';

commit;
