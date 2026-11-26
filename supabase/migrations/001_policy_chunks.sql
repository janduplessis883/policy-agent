-- Nathan policy chunk index.
-- Paste this whole file into the Supabase SQL editor and run it once.
-- Safe to run again: objects are created only if missing, and functions are replaced.
--
-- The vector column is 1536 dimensions (OpenAI text-embedding-3-small).
-- A different embedding size needs a new column before any rows are stored.

create extension if not exists vector;

create table if not exists policy_chunks (
    id bigint generated always as identity primary key,
    page_id text not null,
    title text not null,
    section_heading text not null default '',
    chunk_index integer not null,
    chunk_text text not null,
    content_hash text not null,
    last_edited_time text,
    is_live boolean not null default false,
    embedding vector(1536),
    embedding_model text,
    embedding_dimensions integer,
    fts tsvector generated always as (
        to_tsvector(
            'english',
            coalesce(title, '') || ' ' || coalesce(section_heading, '') || ' ' || coalesce(chunk_text, '')
        )
    ) stored,
    unique (page_id, chunk_index)
);

create index if not exists policy_chunks_fts on policy_chunks using gin (fts);
create index if not exists policy_chunks_page on policy_chunks (page_id);
create index if not exists policy_chunks_embedding
    on policy_chunks using hnsw (embedding vector_cosine_ops);

-- Lease used by the HTTP client. Supabase's connection pooler does not keep a
-- session advisory lock between REST calls, so the timestamp is the lock that
-- actually serialises syncs. The function also takes an advisory lock for a
-- direct Postgres session (the optional smoke script).
create table if not exists policy_index_lock (
    id integer primary key,
    holder text,
    locked_until timestamptz
);

alter table policy_chunks enable row level security;
alter table policy_index_lock enable row level security;

-- No policies are added for anon or authenticated. Those roles see nothing.
-- The service role key bypasses RLS and is the only key Nathan should use.
-- Do not grant the anon key access, and do not put either key in the browser.

create or replace function nathan_try_index_lock(p_holder text, p_seconds integer)
returns boolean
language plpgsql
as $$
declare
    acquired boolean;
begin
    with upsert as (
        insert into policy_index_lock as existing (id, holder, locked_until)
        values (1, p_holder, now() + make_interval(secs => p_seconds))
        on conflict (id) do update
            set holder = excluded.holder,
                locked_until = excluded.locked_until
            where existing.locked_until is null or existing.locked_until < now()
        returning 1
    )
    select exists(select 1 from upsert) into acquired;
    if acquired then
        perform pg_try_advisory_lock(824611);
    end if;
    return acquired;
end;
$$;

create or replace function nathan_index_unlock(p_holder text)
returns void
language plpgsql
as $$
begin
    update policy_index_lock
    set locked_until = now()
    where id = 1 and holder = p_holder;
    perform pg_advisory_unlock(824611);
end;
$$;

create or replace function replace_policy_chunks(p_page_id text, p_chunks jsonb)
returns void
language plpgsql
as $$
declare
    item jsonb;
begin
    delete from policy_chunks where page_id = p_page_id;
    for item in select value from jsonb_array_elements(coalesce(p_chunks, '[]'::jsonb))
    loop
        insert into policy_chunks (
            page_id, title, section_heading, chunk_index, chunk_text, content_hash,
            last_edited_time, is_live, embedding, embedding_model, embedding_dimensions
        ) values (
            p_page_id,
            coalesce(item->>'title', ''),
            coalesce(item->>'section_heading', ''),
            (item->>'chunk_index')::integer,
            coalesce(item->>'chunk_text', ''),
            coalesce(item->>'content_hash', ''),
            item->>'last_edited_time',
            coalesce((item->>'is_live')::boolean, false),
            case
                when coalesce(item->>'embedding', '') = '' then null
                else (item->>'embedding')::vector
            end,
            item->>'embedding_model',
            nullif(item->>'embedding_dimensions', '')::integer
        );
    end loop;
end;
$$;

create or replace function delete_policy_pages(p_page_ids text[])
returns void
language sql
as $$
    delete from policy_chunks where page_id = any(p_page_ids);
$$;

create or replace function touch_policy_page(
    p_page_id text,
    p_title text,
    p_last_edited text,
    p_is_live boolean
)
returns void
language sql
as $$
    update policy_chunks
    set title = p_title,
        last_edited_time = p_last_edited,
        is_live = p_is_live
    where page_id = p_page_id;
$$;

create or replace function search_policy_chunks_fts(
    query_text text,
    match_count integer,
    live_only boolean
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
    where (not live_only or is_live)
      and fts @@ websearch_to_tsquery('english', query_text)
    order by ts_rank(fts, websearch_to_tsquery('english', query_text)) desc
    limit match_count;
$$;

create or replace function search_policy_chunks_vector(
    query_embedding vector(1536),
    match_count integer,
    live_only boolean
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
      and (not live_only or is_live)
    order by embedding <=> query_embedding
    limit match_count;
$$;

create or replace function neighbour_policy_chunks(p_page_id text, p_index integer)
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
    where policy_chunks.page_id = p_page_id
      and chunk_index between p_index - 1 and p_index + 1
    order by chunk_index;
$$;

-- Fresh installs get the same grants as 002. Roles that do not exist (plain Postgres
-- in CI) are skipped. Supabase projects have anon, authenticated and service_role.
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
