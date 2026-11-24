CREATE OR REPLACE FUNCTION public.generate_monthly_bills()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    target_month date := date_trunc('month', CURRENT_DATE + interval '1 month')::date;
    inserted_count integer;
BEGIN
    PERFORM pg_advisory_xact_lock(7172018, 1);

    IF EXISTS (
        SELECT 1
        FROM public.users AS u
        LEFT JOIN public.rooms AS r ON r.room_id = u.room_id
        WHERE u.tenant_status = 'active'
          AND (
              u.check_in_date IS NULL
              OR r.room_id IS NULL
              OR r.price IS NULL
          )
    ) THEN
        RAISE EXCEPTION
            'Cannot generate monthly bills: an active tenant has a missing check_in_date, room, or room price.';
    END IF;

    INSERT INTO public.bills (
        user_id,
        room_id,
        bill_date,
        amount,
        bill_status,
        due_date,
        created_at
    )
    SELECT
        u.user_id,
        u.room_id,
        make_date(
            extract(year FROM target_month)::integer,
            extract(month FROM target_month)::integer,
            least(
                extract(day FROM u.check_in_date)::integer,
                extract(
                    day FROM target_month + interval '1 month - 1 day'
                )::integer
            )
        ),
        r.price,
        'unpaid',
        make_date(
            extract(year FROM target_month)::integer,
            extract(month FROM target_month)::integer,
            least(
                extract(day FROM u.check_in_date)::integer,
                extract(
                    day FROM target_month + interval '1 month - 1 day'
                )::integer
            )
        ),
        CURRENT_TIMESTAMP
    FROM public.users AS u
    JOIN public.rooms AS r ON r.room_id = u.room_id
    WHERE u.tenant_status = 'active'
      AND NOT EXISTS (
          SELECT 1
          FROM public.bills AS b
          WHERE b.user_id = u.user_id
            AND b.due_date >= target_month
            AND b.due_date < target_month + interval '1 month'
      );

    GET DIAGNOSTICS inserted_count = ROW_COUNT;
    RETURN inserted_count;
END;
$function$;

REVOKE ALL ON FUNCTION public.generate_monthly_bills() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.generate_monthly_bills() TO service_role;
