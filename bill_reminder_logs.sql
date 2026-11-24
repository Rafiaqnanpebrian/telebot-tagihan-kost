CREATE TABLE IF NOT EXISTS public.bill_reminder_logs (
    bill_id text NOT NULL,
    reminder_type text NOT NULL
        CHECK (reminder_type IN ('h_minus_3', 'due_date', 'h_plus_1')),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_attempt_at timestamptz,
    sent_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bill_id, reminder_type)
);

ALTER TABLE public.bill_reminder_logs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.bill_reminder_logs FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.bill_reminder_logs TO service_role;
