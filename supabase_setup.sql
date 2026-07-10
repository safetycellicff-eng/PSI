-- =============================================================================
-- Plant Safety Inspection Tracker — Supabase setup
-- =============================================================================
-- Run this whole file once in your Supabase project:
--   Supabase dashboard -> SQL Editor -> New query -> paste -> Run.
--
-- It creates:
--   1. the `records` table that the app reads/writes
--   2. the private `photos` storage bucket for the site photos
--   3. (optional) Row Level Security policies
--
-- The app connects with the SERVICE ROLE key (Settings -> API -> service_role),
-- which BYPASSES Row Level Security. So section 3 is only needed if you also
-- use the anon key elsewhere. Keep the service_role key server-side only.
-- =============================================================================


-- 1. Records table ------------------------------------------------------------
-- One row per violation / hazard. Column names must match the app exactly
-- (see SupabaseStorage.COLUMN_MAP in storage.py).

create table if not exists public.records (
    id            text primary key,   -- e.g. 20260709-73567F
    created_on    text,               -- "dd/mm/YYYY HH:MM" when logged
    safety_officer text,              -- officer who raised/uploaded the point
    location      text,               -- location / shop
    description   text,               -- description of violation / hazard
    first_appeared text,              -- date it first appeared (dd/mm/YYYY)
    action_by     text,               -- responsible officer, e.g. Dy.CEE/M
    remarks       text,               -- inspector's remarks
    category      text default 'SV',  -- SV / UA / UC / NM
    status        text default 'Pending',  -- Pending / Completed
    pdc           text,               -- Probable Date of Completion (dd/mm/YYYY)
    action_remarks text,              -- action owner's remarks (compliance app)
    photo_count   integer default 0
);

-- If you created the table with an earlier version, add the newer columns.
-- (Safe to run repeatedly.)
alter table public.records add column if not exists safety_officer text;
alter table public.records add column if not exists pdc text;
alter table public.records add column if not exists action_remarks text;

-- Helpful index for the Records tab (filter by status).
create index if not exists records_status_idx on public.records (status);


-- 2. Photos storage bucket ----------------------------------------------------
-- Photos are stored as objects named "<record_id>_<before|after>_<n>.jpg".
-- Private bucket (public = false); the app downloads via the service role.

insert into storage.buckets (id, name, public)
values ('photos', 'photos', false)
on conflict (id) do nothing;


-- 3. (OPTIONAL) Row Level Security --------------------------------------------
-- Uncomment this section only if you connect with the anon key instead of the
-- service_role key. These policies allow full access to authenticated users.
-- WARNING: never expose the service_role key in a browser/client.

-- alter table public.records enable row level security;

-- create policy "authenticated full access to records"
--   on public.records
--   for all
--   to authenticated
--   using (true)
--   with check (true);

-- create policy "authenticated read photos"
--   on storage.objects for select
--   to authenticated
--   using (bucket_id = 'photos');

-- create policy "authenticated write photos"
--   on storage.objects for insert
--   to authenticated
--   with check (bucket_id = 'photos');

-- create policy "authenticated delete photos"
--   on storage.objects for delete
--   to authenticated
--   using (bucket_id = 'photos');
