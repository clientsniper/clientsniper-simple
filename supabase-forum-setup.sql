-- ClientSniper Forum — run this once in Supabase SQL Editor

-- Tables
create table if not exists forum_threads (
  id          uuid        primary key default gen_random_uuid(),
  title       text        not null,
  body        text        not null,
  author      text        not null,
  is_team     boolean     default false,
  category    text        default 'general',
  tags        text[]      default '{}',
  pinned      boolean     default false,
  created_at  timestamptz default now(),
  votes       integer     default 0,
  reply_count integer     default 0
);

create table if not exists forum_replies (
  id          uuid        primary key default gen_random_uuid(),
  thread_id   uuid        references forum_threads(id) on delete cascade not null,
  body        text        not null,
  author      text        not null,
  is_team     boolean     default false,
  created_at  timestamptz default now(),
  votes       integer     default 0
);

-- Auto-update reply_count on forum_threads
create or replace function update_reply_count()
returns trigger language plpgsql as $$
begin
  if TG_OP = 'INSERT' then
    update forum_threads set reply_count = reply_count + 1 where id = NEW.thread_id;
  elsif TG_OP = 'DELETE' then
    update forum_threads set reply_count = reply_count - 1 where id = OLD.thread_id;
  end if;
  return null;
end;
$$;

drop trigger if exists trg_reply_count on forum_replies;
create trigger trg_reply_count
after insert or delete on forum_replies
for each row execute function update_reply_count();

-- Vote RPCs (callable from JS with db.rpc(...))
create or replace function vote_on_thread(p_id uuid, p_delta integer)
returns void language plpgsql as $$
begin
  update forum_threads set votes = votes + p_delta where id = p_id;
end;
$$;

create or replace function vote_on_reply(p_id uuid, p_delta integer)
returns void language plpgsql as $$
begin
  update forum_replies set votes = votes + p_delta where id = p_id;
end;
$$;

-- Hot sort view (used when sort=hot in forum.html)
create or replace view forum_threads_hot as
select *,
  (votes * 2 + reply_count)::float /
  power(extract(epoch from (now() - created_at)) / 3600 + 2, 1.5) as hot_score
from forum_threads;

-- Row Level Security — allow anyone to read, anyone to insert (no account needed for forum)
alter table forum_threads enable row level security;
alter table forum_replies  enable row level security;

drop policy if exists "read threads"   on forum_threads;
drop policy if exists "insert threads" on forum_threads;
drop policy if exists "read replies"   on forum_replies;
drop policy if exists "insert replies" on forum_replies;

create policy "read threads"   on forum_threads for select using (true);
create policy "insert threads" on forum_threads for insert with check (true);
create policy "read replies"   on forum_replies for select using (true);
create policy "insert replies" on forum_replies for insert with check (true);
