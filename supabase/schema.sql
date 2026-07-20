-- HVAC AI Quotation System - Supabase/PostgreSQL schema
-- Run this file in Supabase SQL Editor.
-- Design goals:
-- 1) Multi-company ready.
-- 2) AI only learns from approved quotation data.
-- 3) Full audit trail for price/rule/session changes.
-- 4) Preserve customer takeoff files, quote templates, generated files.

create extension if not exists pgcrypto;

create type public.member_role as enum ('owner', 'admin', 'estimator', 'sales', 'approver', 'viewer');
create type public.approval_status as enum ('draft', 'pending', 'approved', 'rejected', 'archived');
create type public.file_kind as enum ('customer_takeoff', 'quote_template', 'completed_quote', 'generated_draft', 'generated_final', 'other');
create type public.quote_status as enum ('draft', 'needs_input', 'blocked', 'ready', 'pending_approval', 'approved', 'issued', 'rejected');
create type public.price_source_kind as enum ('manual', 'price_list', 'completed_quote_memory', 'rule', 'ai_suggestion');

create table public.companies (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  tax_code text,
  address text,
  phone text,
  email text,
  website text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  phone text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.company_members (
  company_id uuid not null references public.companies(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  role public.member_role not null default 'viewer',
  active boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (company_id, user_id)
);

create table public.customers (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  name text not null,
  tax_code text,
  address text,
  phone text,
  email text,
  notes text,
  created_at timestamptz not null default now(),
  unique (company_id, name)
);

create table public.suppliers (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  name text not null,
  region text,
  phone text,
  email text,
  notes text,
  created_at timestamptz not null default now(),
  unique (company_id, name)
);

create table public.projects (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  customer_id uuid references public.customers(id),
  name text not null,
  address text,
  system_name text,
  status text not null default 'active',
  created_by uuid references public.profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.project_files (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  kind public.file_kind not null,
  file_name text not null,
  storage_bucket text not null default 'quotation-files',
  storage_path text not null,
  mime_type text,
  file_size bigint,
  checksum text,
  uploaded_by uuid references public.profiles(id),
  created_at timestamptz not null default now()
);

create table public.product_categories (
  id uuid primary key default gen_random_uuid(),
  company_id uuid references public.companies(id) on delete cascade,
  key text not null,
  display_name_vi text not null,
  display_name_en text,
  keywords text[] not null default '{}',
  requires_dimensions boolean not null default false,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (company_id, key)
);

create table public.product_master (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  product_code text not null,
  display_name_vi text not null,
  category_id uuid references public.product_categories(id),
  category_key text,
  pricing_method text not null,
  required_fields text[] not null default '{}',
  default_unit text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (company_id, product_code)
);

create table public.product_aliases (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  product_id uuid references public.product_master(id) on delete cascade,
  product_code text not null,
  alias_text text not null,
  normalized_alias text not null,
  language text,
  confidence numeric(5,2) not null default 100,
  approval_status public.approval_status not null default 'approved',
  source text,
  approved_by uuid references public.profiles(id),
  created_at timestamptz not null default now(),
  unique (company_id, normalized_alias, product_code)
);

create table public.sales_product_answers (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  session_id uuid references public.quotation_sessions(id) on delete set null,
  line_signature text not null,
  description text not null,
  product_code text,
  category_key text,
  unit_price numeric(18,2),
  unit text,
  note text,
  approved_by uuid references public.profiles(id),
  created_at timestamptz not null default now()
);

create table public.materials (
  id uuid primary key default gen_random_uuid(),
  company_id uuid references public.companies(id) on delete cascade,
  code text not null,
  name_vi text not null,
  description text,
  active boolean not null default true,
  unique (company_id, code)
);

create table public.units (
  code text primary key,
  name_vi text not null,
  base_code text,
  is_package boolean not null default false
);

create table public.unit_conversions (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  from_unit text not null references public.units(code),
  to_unit text not null references public.units(code),
  multiplier numeric(18,6) not null,
  rounding_mode text not null default 'none', -- none, ceil, floor, round
  note text,
  unique (company_id, from_unit, to_unit, note)
);

create table public.thickness_rules (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  material_id uuid not null references public.materials(id),
  min_size_mm numeric(18,3) not null,
  max_size_mm numeric(18,3) not null,
  thickness_mm numeric(18,3) not null,
  approval_status public.approval_status not null default 'approved',
  created_at timestamptz not null default now()
);

create table public.price_lists (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  supplier_id uuid references public.suppliers(id),
  customer_id uuid references public.customers(id),
  name text not null,
  region text,
  valid_from date,
  valid_to date,
  currency text not null default 'VND',
  is_default boolean not null default false,
  approval_status public.approval_status not null default 'draft',
  notes text,
  created_by uuid references public.profiles(id),
  approved_by uuid references public.profiles(id),
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (company_id, name)
);

create table public.price_list_items (
  id uuid primary key default gen_random_uuid(),
  price_list_id uuid not null references public.price_lists(id) on delete cascade,
  category_id uuid references public.product_categories(id),
  material_id uuid references public.materials(id),
  thickness_mm numeric(18,3),
  product_key text,
  description_pattern text,
  unit text not null references public.units(code),
  unit_price numeric(18,2) not null,
  min_qty numeric(18,3),
  package_size numeric(18,3),
  package_unit text references public.units(code),
  source_note text,
  created_at timestamptz not null default now()
);

create table public.product_pricing_rules (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  category_id uuid not null references public.product_categories(id),
  quote_code text,
  unit_mode text not null default 'area',
  material_spec text,
  brand text,
  base_unit_price numeric(18,2) not null default 0,
  area_multiplier numeric(18,6) not null default 1,
  length_mm numeric(18,3),
  formula_json jsonb not null default '{}'::jsonb,
  approval_status public.approval_status not null default 'draft',
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (company_id, category_id)
);

create table public.coefficient_rules (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  key text not null,
  value numeric(18,6) not null,
  value_type text not null,
  approval_status public.approval_status not null default 'approved',
  description text,
  unique (company_id, key)
);

create table public.company_settings (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  key text not null,
  value text not null,
  description text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (company_id, key)
);

create table public.quote_memory_sources (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  project_id uuid references public.projects(id),
  file_id uuid references public.project_files(id),
  source_file_name text not null,
  customer_id uuid references public.customers(id),
  approval_status public.approval_status not null default 'pending',
  approved_by uuid references public.profiles(id),
  approved_at timestamptz,
  notes text,
  created_at timestamptz not null default now()
);

create table public.quote_memory_items (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  source_id uuid not null references public.quote_memory_sources(id) on delete cascade,
  signature text not null,
  normalized_description text not null,
  category_id uuid references public.product_categories(id),
  width_mm numeric(18,3),
  height_mm numeric(18,3),
  diameter_mm numeric(18,3),
  length_mm numeric(18,3),
  thickness_mm numeric(18,3),
  material_id uuid references public.materials(id),
  unit text not null references public.units(code),
  quantity numeric(18,3),
  quote_unit_price numeric(18,2) not null,
  quote_line_total numeric(18,2),
  package_quantity numeric(18,3),
  package_unit text references public.units(code),
  package_length_m numeric(18,3),
  material_spec text,
  brand text,
  note text,
  confidence numeric(5,2) not null default 100,
  approval_status public.approval_status not null default 'pending',
  created_at timestamptz not null default now()
);

create unique index quote_memory_items_source_signature_idx
  on public.quote_memory_items(source_id, signature);

create index quote_memory_items_lookup_idx
  on public.quote_memory_items(company_id, normalized_description, unit, approval_status);

create table public.quotation_sessions (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  project_id uuid references public.projects(id),
  customer_id uuid references public.customers(id),
  price_list_id uuid references public.price_lists(id),
  name text not null,
  status public.quote_status not null default 'draft',
  readiness_score numeric(5,2),
  summary_json jsonb not null default '{}'::jsonb,
  warnings_json jsonb not null default '[]'::jsonb,
  created_by uuid references public.profiles(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.quotation_items (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.quotation_sessions(id) on delete cascade,
  line_no integer,
  source_row integer,
  mark text,
  description text not null,
  category_id uuid references public.product_categories(id),
  material_id uuid references public.materials(id),
  width_mm numeric(18,3),
  height_mm numeric(18,3),
  diameter_mm numeric(18,3),
  length_mm numeric(18,3),
  thickness_mm numeric(18,3),
  quantity numeric(18,3) not null default 0,
  unit text references public.units(code),
  quote_quantity numeric(18,3),
  quote_unit text references public.units(code),
  quote_unit_price numeric(18,2),
  line_total numeric(18,2),
  price_source public.price_source_kind,
  price_source_id uuid,
  confidence numeric(5,2),
  warning_json jsonb not null default '[]'::jsonb,
  raw_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.quotation_approvals (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.quotation_sessions(id) on delete cascade,
  status public.approval_status not null,
  approver_id uuid references public.profiles(id),
  notes text,
  readiness_score numeric(5,2),
  created_at timestamptz not null default now()
);

create table public.template_mappings (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  template_name text not null,
  sheet_name text not null,
  header_row integer,
  data_start_row integer,
  summary_row integer,
  mapping_json jsonb not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (company_id, template_name, sheet_name)
);

create table public.audit_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references public.companies(id) on delete cascade,
  actor_id uuid references public.profiles(id),
  action text not null,
  entity_table text not null,
  entity_id uuid,
  before_json jsonb,
  after_json jsonb,
  ip_address inet,
  user_agent text,
  created_at timestamptz not null default now()
);

insert into public.units (code, name_vi, base_code, is_package) values
  ('m', 'mét', null, false),
  ('m2', 'mét vuông', null, false),
  ('cai', 'cái', null, false),
  ('bo', 'bộ', null, false),
  ('lo', 'lô', null, false),
  ('ong', 'ống', 'm', true),
  ('cuon', 'cuộn', 'm', true)
on conflict (code) do nothing;

-- Helper: a user can access rows from companies they belong to.
create or replace function public.is_company_member(target_company_id uuid)
returns boolean
language sql
stable
security definer
as $$
  select exists (
    select 1
    from public.company_members cm
    where cm.company_id = target_company_id
      and cm.user_id = auth.uid()
      and cm.active = true
  );
$$;

create or replace function public.is_company_admin(target_company_id uuid)
returns boolean
language sql
stable
security definer
as $$
  select exists (
    select 1
    from public.company_members cm
    where cm.company_id = target_company_id
      and cm.user_id = auth.uid()
      and cm.active = true
      and cm.role in ('owner', 'admin')
  );
$$;

-- Enable RLS. Service role bypasses these policies for backend jobs/imports.
alter table public.companies enable row level security;
alter table public.profiles enable row level security;
alter table public.company_members enable row level security;
alter table public.customers enable row level security;
alter table public.suppliers enable row level security;
alter table public.projects enable row level security;
alter table public.project_files enable row level security;
alter table public.product_categories enable row level security;
alter table public.product_master enable row level security;
alter table public.product_aliases enable row level security;
alter table public.sales_product_answers enable row level security;
alter table public.materials enable row level security;
alter table public.unit_conversions enable row level security;
alter table public.thickness_rules enable row level security;
alter table public.price_lists enable row level security;
alter table public.price_list_items enable row level security;
alter table public.product_pricing_rules enable row level security;
alter table public.coefficient_rules enable row level security;
alter table public.company_settings enable row level security;
alter table public.quote_memory_sources enable row level security;
alter table public.quote_memory_items enable row level security;
alter table public.quotation_sessions enable row level security;
alter table public.quotation_items enable row level security;
alter table public.quotation_approvals enable row level security;
alter table public.template_mappings enable row level security;
alter table public.audit_events enable row level security;

create policy "members can read companies"
  on public.companies for select
  using (public.is_company_member(id));

create policy "users can read own profile"
  on public.profiles for select
  using (id = auth.uid());

create policy "users can update own profile"
  on public.profiles for update
  using (id = auth.uid());

-- Generic company-scoped read/write policies.
-- Keep writes admin-only for master data. Backend service role can import/learn after approval.
create policy "members read customers" on public.customers for select using (public.is_company_member(company_id));
create policy "admins write customers" on public.customers for all using (public.is_company_admin(company_id));
create policy "members read suppliers" on public.suppliers for select using (public.is_company_member(company_id));
create policy "admins write suppliers" on public.suppliers for all using (public.is_company_admin(company_id));
create policy "members read projects" on public.projects for select using (public.is_company_member(company_id));
create policy "members write projects" on public.projects for all using (public.is_company_member(company_id));
create policy "members read files" on public.project_files for select using (public.is_company_member(company_id));
create policy "members write files" on public.project_files for all using (public.is_company_member(company_id));
create policy "members read company members" on public.company_members for select using (public.is_company_member(company_id));
create policy "admins write company members" on public.company_members for all using (public.is_company_admin(company_id));
create policy "members read categories" on public.product_categories for select using (company_id is null or public.is_company_member(company_id));
create policy "admins write categories" on public.product_categories for all using (company_id is not null and public.is_company_admin(company_id));
create policy "members read product master" on public.product_master for select using (public.is_company_member(company_id));
create policy "admins write product master" on public.product_master for all using (public.is_company_admin(company_id));
create policy "members read product aliases" on public.product_aliases for select using (public.is_company_member(company_id));
create policy "admins write product aliases" on public.product_aliases for all using (public.is_company_admin(company_id));
create policy "members read sales product answers" on public.sales_product_answers for select using (public.is_company_member(company_id));
create policy "members write sales product answers" on public.sales_product_answers for all using (public.is_company_member(company_id));
create policy "members read materials" on public.materials for select using (company_id is null or public.is_company_member(company_id));
create policy "admins write materials" on public.materials for all using (company_id is not null and public.is_company_admin(company_id));
create policy "members read conversions" on public.unit_conversions for select using (public.is_company_member(company_id));
create policy "admins write conversions" on public.unit_conversions for all using (public.is_company_admin(company_id));
create policy "members read thickness rules" on public.thickness_rules for select using (public.is_company_member(company_id));
create policy "admins write thickness rules" on public.thickness_rules for all using (public.is_company_admin(company_id));
create policy "members read price lists" on public.price_lists for select using (public.is_company_member(company_id));
create policy "admins write price lists" on public.price_lists for all using (public.is_company_admin(company_id));
create policy "members read price list items" on public.price_list_items for select using (
  exists (
    select 1 from public.price_lists pl
    where pl.id = price_list_id and public.is_company_member(pl.company_id)
  )
);
create policy "admins write price list items" on public.price_list_items for all using (
  exists (
    select 1 from public.price_lists pl
    where pl.id = price_list_id and public.is_company_admin(pl.company_id)
  )
);
create policy "members read product pricing rules" on public.product_pricing_rules for select using (public.is_company_member(company_id));
create policy "admins write product pricing rules" on public.product_pricing_rules for all using (public.is_company_admin(company_id));
create policy "members read coefficient rules" on public.coefficient_rules for select using (public.is_company_member(company_id));
create policy "admins write coefficient rules" on public.coefficient_rules for all using (public.is_company_admin(company_id));
create policy "members read company settings" on public.company_settings for select using (public.is_company_member(company_id));
create policy "admins write company settings" on public.company_settings for all using (public.is_company_admin(company_id));
create policy "members read quote memory sources" on public.quote_memory_sources for select using (public.is_company_member(company_id));
create policy "admins write quote memory sources" on public.quote_memory_sources for all using (public.is_company_admin(company_id));
create policy "members read quote memory items" on public.quote_memory_items for select using (public.is_company_member(company_id));
create policy "admins write quote memory items" on public.quote_memory_items for all using (public.is_company_admin(company_id));
create policy "members read quotation sessions" on public.quotation_sessions for select using (public.is_company_member(company_id));
create policy "members write quotation sessions" on public.quotation_sessions for all using (public.is_company_member(company_id));
create policy "members read quotation items" on public.quotation_items for select using (
  exists (
    select 1 from public.quotation_sessions qs
    where qs.id = session_id and public.is_company_member(qs.company_id)
  )
);
create policy "members write quotation items" on public.quotation_items for all using (
  exists (
    select 1 from public.quotation_sessions qs
    where qs.id = session_id and public.is_company_member(qs.company_id)
  )
);
create policy "members read approvals" on public.quotation_approvals for select using (
  exists (
    select 1 from public.quotation_sessions qs
    where qs.id = session_id and public.is_company_member(qs.company_id)
  )
);
create policy "approvers write approvals" on public.quotation_approvals for all using (
  exists (
    select 1
    from public.quotation_sessions qs
    join public.company_members cm on cm.company_id = qs.company_id
    where qs.id = session_id
      and cm.user_id = auth.uid()
      and cm.active = true
      and cm.role in ('owner', 'admin', 'approver')
  )
);
create policy "members read template mappings" on public.template_mappings for select using (public.is_company_member(company_id));
create policy "admins write template mappings" on public.template_mappings for all using (public.is_company_admin(company_id));
create policy "members read audit events" on public.audit_events for select using (public.is_company_member(company_id));
create policy "admins write audit events" on public.audit_events for all using (public.is_company_admin(company_id));
