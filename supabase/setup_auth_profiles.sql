-- Run this after creating both accounts under Supabase Dashboard > Authentication > Users.
-- This script contains no passwords or API keys.

DO $$
BEGIN
    IF to_regclass('public.trips') IS NOT NULL THEN
        ALTER TABLE public.trips
            ADD COLUMN IF NOT EXISTS motorista_user_id uuid
            REFERENCES auth.users(id) ON DELETE SET NULL;
        CREATE INDEX IF NOT EXISTS ix_trips_motorista_user_id
            ON public.trips (motorista_user_id);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS public.user_profiles (
    user_id uuid PRIMARY KEY,
    email varchar(320) NOT NULL UNIQUE,
    role varchar(20) NOT NULL CHECK (role IN ('gestor', 'motorista'))
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'user_profiles_auth_user_fk'
          AND conrelid = 'public.user_profiles'::regclass
    ) THEN
        ALTER TABLE public.user_profiles
            ADD CONSTRAINT user_profiles_auth_user_fk
            FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
    END IF;
END
$$;

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['routes', 'trips', 'user_profiles', 'vehicles'] LOOP
        IF to_regclass(format('public.%I', table_name)) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', table_name);
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon, authenticated', table_name);
        END IF;
    END LOOP;
END
$$;

INSERT INTO public.user_profiles (user_id, email, role)
SELECT id, email, 'gestor'
FROM auth.users
WHERE lower(email) = lower('conrado.heric@gmail.com')
ON CONFLICT (user_id) DO UPDATE
SET email = EXCLUDED.email, role = EXCLUDED.role;

INSERT INTO public.user_profiles (user_id, email, role)
SELECT id, email, 'motorista'
FROM auth.users
WHERE lower(email) = lower('valdicesar2026@gmail.com')
ON CONFLICT (user_id) DO UPDATE
SET email = EXCLUDED.email, role = EXCLUDED.role;
