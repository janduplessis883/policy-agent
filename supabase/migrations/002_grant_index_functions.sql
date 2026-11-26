-- Run this in the Supabase SQL editor if 001 was applied before the key fix.
-- It does not delete indexed rows. Safe to run more than once.
--
-- The secret key calls PostgREST as service_role. These functions were not
-- explicitly granted to that role, which returns HTTP 403 permission denied.
-- Execute is removed from public, anon and authenticated, and given only to service_role.

do $$
declare
    fn record;
    signature text;
begin
    for fn in
        select n.nspname as schema_name,
               p.proname as name,
               pg_get_function_identity_arguments(p.oid) as args
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
              'nathan_try_index_lock',
              'nathan_index_unlock',
              'replace_policy_chunks',
              'delete_policy_pages',
              'touch_policy_page',
              'search_policy_chunks_fts',
              'search_policy_chunks_vector',
              'neighbour_policy_chunks'
          )
    loop
        signature := format('%I.%I(%s)', fn.schema_name, fn.name, fn.args);
        execute format('revoke all on function %s from public', signature);
        if exists (select 1 from pg_roles where rolname = 'anon') then
            execute format('revoke all on function %s from anon', signature);
        end if;
        if exists (select 1 from pg_roles where rolname = 'authenticated') then
            execute format('revoke all on function %s from authenticated', signature);
        end if;
        if exists (select 1 from pg_roles where rolname = 'service_role') then
            execute format('grant execute on function %s to service_role', signature);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant select, insert, update, delete on table policy_chunks to service_role;
        grant select, insert, update, delete on table policy_index_lock to service_role;
        if exists (select 1 from pg_class where relname = 'policy_chunks_id_seq') then
            grant usage, select on sequence policy_chunks_id_seq to service_role;
        end if;
    end if;
end $$;
