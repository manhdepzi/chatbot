-- Optional RAG/vector layer for HVAC quote memory.
-- Run after supabase/schema.sql when the project needs semantic search.
-- The app still works without this file; deterministic pricing remains the source of truth.

create extension if not exists vector;

create table if not exists public.quote_memory_embeddings (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  quote_memory_item_id uuid not null references public.quote_memory_items(id) on delete cascade,
  embedding_model text not null,
  retrieval_text text not null,
  embedding vector(768) not null,
  created_at timestamptz not null default now(),
  unique (quote_memory_item_id, embedding_model)
);

create index if not exists quote_memory_embeddings_vector_idx
  on public.quote_memory_embeddings
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

alter table public.quote_memory_embeddings enable row level security;

drop policy if exists "members read quote memory embeddings" on public.quote_memory_embeddings;
create policy "members read quote memory embeddings"
  on public.quote_memory_embeddings for select
  using (public.is_company_member(company_id));

drop policy if exists "admins write quote memory embeddings" on public.quote_memory_embeddings;
create policy "admins write quote memory embeddings"
  on public.quote_memory_embeddings for all
  using (public.is_company_admin(company_id));
