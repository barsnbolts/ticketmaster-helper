create table if not exists public.hospitals (
  id text primary key,
  name text not null,
  city text not null,
  system_name text not null,
  source_url text not null,
  cadence_minutes int not null default 15,
  created_at timestamptz not null default now()
);

create table if not exists public.ed_wait_snapshots (
  id uuid primary key default gen_random_uuid(),
  hospital_id text not null references public.hospitals(id) on delete cascade,
  wait_minutes int,
  total_patients int,
  waiting_patients int,
  source_reported_at timestamptz,
  source_time_label text,
  retrieved_at timestamptz not null default now(),
  source_tier text not null default 'official',
  source_url text not null,
  is_valid boolean not null default true,
  invalid_reason text,
  http_status int,
  response_ms int,
  parser_version text,
  payload_hash text,
  validation_flags jsonb not null default '[]'::jsonb
);

create index if not exists ed_wait_snapshots_hospital_time_idx
  on public.ed_wait_snapshots (hospital_id, retrieved_at desc);
create index if not exists ed_wait_snapshots_source_time_idx
  on public.ed_wait_snapshots (hospital_id, source_reported_at desc);
create unique index if not exists ed_wait_snapshots_dedupe_idx
  on public.ed_wait_snapshots (
    hospital_id,
    coalesce(source_reported_at, retrieved_at),
    coalesce(wait_minutes, -1),
    coalesce(total_patients, -1),
    coalesce(waiting_patients, -1),
    coalesce(payload_hash, '')
  );

alter table public.hospitals enable row level security;
alter table public.ed_wait_snapshots enable row level security;

drop policy if exists "Hospitals publicly readable" on public.hospitals;
create policy "Hospitals publicly readable" on public.hospitals
  for select to anon, authenticated using (true);

drop policy if exists "Snapshots publicly readable" on public.ed_wait_snapshots;
create policy "Snapshots publicly readable" on public.ed_wait_snapshots
  for select to anon, authenticated using (true);

grant select on public.hospitals to anon, authenticated;
grant select on public.ed_wait_snapshots to anon, authenticated;
grant all on public.hospitals to service_role;
grant all on public.ed_wait_snapshots to service_role;

insert into public.hospitals (id, name, city, system_name, source_url, cadence_minutes) values
  ('cvh', 'Credit Valley Hospital', 'Mississauga', 'Trillium Health Partners', 'https://www.thp.ca/emergency/A/visit.html', 5),
  ('milton', 'Milton District Hospital', 'Milton', 'Halton Healthcare', 'https://www.haltonhealthcare.on.ca/emergency-department', 15),
  ('otmh', 'Oakville Trafalgar Memorial Hospital', 'Oakville', 'Halton Healthcare', 'https://www.haltonhealthcare.on.ca/emergency-department', 15)
on conflict (id) do update set
  name = excluded.name,
  city = excluded.city,
  system_name = excluded.system_name,
  source_url = excluded.source_url,
  cadence_minutes = excluded.cadence_minutes;
