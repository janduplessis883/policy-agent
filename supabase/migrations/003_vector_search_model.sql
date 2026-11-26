-- Run this in the Supabase SQL editor after 001 and 002.
-- It does not delete indexed rows.
--
-- Vector search only compares a question with chunks stored for the same
-- embedding model. A changed model is re-embedded on the next Re-index.
-- Safe to run more than once.

drop function if exists search_policy_chunks_vector(vector, integer, boolean);
drop function if exists search_policy_chunks_vector(vector, integer, boolean, text);

create or replace function search_policy_chunks_vector(
    query_embedding vector(1536),
    match_count integer,
    live_only boolean,
    p_embedding_model text default null
)
returns table (
    page_id text,
    title text,
    section_heading text,
    chunk_text text,
    chunk_index integer,
    is_live boolean
)
language sql
stable
as $$
    select page_id, title, section_heading, chunk_text, chunk_index, is_live
    from policy_chunks
    where embedding is not null
      and (p_embedding_model is null or embedding_model = p_embedding_model)
      and (not live_only or is_live)
    order by embedding <=> query_embedding
    limit match_count;
$$;

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
          and p.proname = 'search_policy_chunks_vector'
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
end $$;
