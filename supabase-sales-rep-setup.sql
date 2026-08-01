-- ClientSniper Sales Rep Portal — run in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS rep_applications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  linkedin TEXT,
  experience TEXT,
  country TEXT,
  why_join TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  applied_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sales_reps (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  referral_code TEXT NOT NULL UNIQUE,
  commission_rate NUMERIC NOT NULL DEFAULT 0.20,
  tier TEXT NOT NULL DEFAULT 'bronze',
  status TEXT NOT NULL DEFAULT 'active',
  payout_info TEXT,
  joined_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS referrals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rep_id UUID NOT NULL REFERENCES sales_reps(id) ON DELETE CASCADE,
  customer_email TEXT NOT NULL,
  customer_name TEXT,
  plan TEXT,
  monthly_value NUMERIC DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  stripe_subscription_id TEXT,
  stripe_customer_id TEXT,
  referred_at TIMESTAMPTZ DEFAULT NOW(),
  renewal_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS commissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rep_id UUID NOT NULL REFERENCES sales_reps(id) ON DELETE CASCADE,
  referral_id UUID NOT NULL REFERENCES referrals(id) ON DELETE CASCADE,
  amount NUMERIC NOT NULL,
  invoice_amount NUMERIC,
  period TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  earned_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payouts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rep_id UUID NOT NULL REFERENCES sales_reps(id) ON DELETE CASCADE,
  amount NUMERIC NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  method TEXT,
  reference TEXT,
  paid_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS announcements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  emoji TEXT DEFAULT '📢',
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rep_support_tickets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rep_id UUID NOT NULL REFERENCES sales_reps(id) ON DELETE CASCADE,
  subject TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS
ALTER TABLE rep_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_reps ENABLE ROW LEVEL SECURITY;
ALTER TABLE referrals ENABLE ROW LEVEL SECURITY;
ALTER TABLE commissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE payouts ENABLE ROW LEVEL SECURITY;
ALTER TABLE announcements ENABLE ROW LEVEL SECURITY;
ALTER TABLE rep_support_tickets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "apply_insert" ON rep_applications FOR INSERT WITH CHECK (true);
CREATE POLICY "rep_select_own" ON sales_reps FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "rep_update_own" ON sales_reps FOR UPDATE USING (auth.uid() = user_id);
CREATE POLICY "ref_select_own" ON referrals FOR SELECT USING (rep_id IN (SELECT id FROM sales_reps WHERE user_id = auth.uid()));
CREATE POLICY "comm_select_own" ON commissions FOR SELECT USING (rep_id IN (SELECT id FROM sales_reps WHERE user_id = auth.uid()));
CREATE POLICY "payout_select_own" ON payouts FOR SELECT USING (rep_id IN (SELECT id FROM sales_reps WHERE user_id = auth.uid()));
CREATE POLICY "announcements_read" ON announcements FOR SELECT USING (true);
CREATE POLICY "ticket_insert" ON rep_support_tickets FOR INSERT WITH CHECK (rep_id IN (SELECT id FROM sales_reps WHERE user_id = auth.uid()));
CREATE POLICY "ticket_select_own" ON rep_support_tickets FOR SELECT USING (rep_id IN (SELECT id FROM sales_reps WHERE user_id = auth.uid()));

INSERT INTO announcements (emoji, title, body) VALUES
  ('👋', 'Welcome to the ClientSniper Sales Rep Portal', 'This is your hub for tracking referrals, commissions, and accessing sales resources. Welcome aboard!'),
  ('💰', '20% Recurring Commission', 'You earn 20% of every subscription payment made by customers you refer — every single month they stay active. Commissions are paid monthly.');
