import os
import re
import json
import uuid
import smtplib
import time
import threading
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from html import escape

from flask import Flask, request, jsonify, send_from_directory, abort
from dotenv import load_dotenv
import stripe

try:
    import resend as _resend_sdk
    _resend_available = True
except ImportError:
    _resend_available = False

try:
    from supabase import create_client as _sb_create
    _sb_available = True
except ImportError:
    _sb_available = False

load_dotenv()

app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(32).hex())

# ─── Configuration ──────────────────────────────────────
stripe.api_key = os.getenv('STRIPE_SECRET_KEY', '')
WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')

PRICE_MAP = {
    'starter_monthly': os.getenv('STRIPE_PRICE_STARTER_MONTHLY', ''),
    'starter_yearly':  os.getenv('STRIPE_PRICE_STARTER_YEARLY', ''),
    'growth_monthly':  os.getenv('STRIPE_PRICE_GROWTH_MONTHLY', ''),
    'growth_yearly':   os.getenv('STRIPE_PRICE_GROWTH_YEARLY', ''),
    'topup':           os.getenv('STRIPE_PRICE_TOPUP', ''),
}

ALLOWED_ORIGINS = {
    'https://clientsniper.com',
    'https://www.clientsniper.com',
}

RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
if RESEND_API_KEY and _resend_available:
    _resend_sdk.api_key = RESEND_API_KEY

SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASS = os.getenv('SMTP_PASS', '')
FROM_EMAIL = os.getenv('FROM_EMAIL', f'ClientSniper <{SMTP_USER}>' if SMTP_USER else 'ClientSniper <noreply@clientsniper.com>')
CONTACT_EMAIL_TO = os.getenv('CONTACT_EMAIL_TO', 'clientsniper.official@gmail.com')
SITE_URL = os.getenv('SITE_URL', 'https://clientsniper.com')

SUPABASE_URL         = os.getenv('SUPABASE_URL', 'https://jxcahqkkcnqkmjjqdnuu.supabase.co')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY', '')

_sb = None
if _sb_available and SUPABASE_SERVICE_KEY:
    try:
        _sb = _sb_create(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception as _e:
        print(f'Supabase init error: {_e}')

EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')

DATA_DIR = '/tmp/csdata' if os.getenv('VERCEL') else os.path.join(os.path.dirname(__file__), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

NEWSLETTER_FILE = os.path.join(DATA_DIR, 'newsletter.json')
USERS_FILE      = os.path.join(DATA_DIR, 'users.json')
FORUM_FILE      = os.path.join(DATA_DIR, 'forum.json')
FORUM_TEAM_PASS = os.getenv('FORUM_TEAM_PASSWORD', '')

_file_lock = threading.Lock()


def _read_json(path):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except (json.JSONDecodeError, ValueError):
            return []


def _write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── Rate Limiting (simple in-memory) ──────────────────
_rate_store = {}


def rate_limit(max_requests, window_seconds):
    """Simple per-IP rate limiter."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip = request.remote_addr or '0.0.0.0'
            key = f'{f.__name__}:{ip}'
            now = time.time()
            window = _rate_store.get(key, [])
            window = [t for t in window if now - t < window_seconds]
            if len(window) >= max_requests:
                return jsonify({'error': 'Too many requests. Please try again later.'}), 429
            window.append(now)
            _rate_store[key] = window
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ─── Email Helper ───────────────────────────────────────
def send_email(to, subject, text_body, html_body=None, reply_to=None):
    # Prefer Resend if API key is set, fall back to SMTP
    if RESEND_API_KEY and _resend_available:
        params = {
            'from': FROM_EMAIL,
            'to': [to],
            'subject': subject,
            'text': text_body,
        }
        if html_body:
            params['html'] = html_body
        if reply_to:
            params['reply_to'] = [reply_to]
        _resend_sdk.Emails.send(params)
        return True

    if not SMTP_USER or not SMTP_PASS:
        print(f'[EMAIL SKIPPED - no credentials configured] To: {to}, Subject: {subject}')
        return True

    msg = MIMEMultipart('alternative')
    msg['From'] = FROM_EMAIL
    msg['To'] = to
    msg['Subject'] = subject
    if reply_to:
        msg['Reply-To'] = reply_to

    msg.attach(MIMEText(text_body, 'plain'))
    if html_body:
        msg.attach(MIMEText(html_body, 'html'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
    return True


# ─── Security + CORS Headers ───────────────────────────
@app.after_request
def security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    origin = request.headers.get('Origin', '')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Stripe-Signature'
        response.headers['Vary'] = 'Origin'
    return response


@app.route('/api/<path:path>', methods=['OPTIONS'])
def options_handler(path):
    response = jsonify({})
    origin = request.headers.get('Origin', '')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response, 204


# ─── Health Check ──────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'clientsniper'}), 200


# ─── Static Pages ──────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


# API and webhook routes are defined below, then the static catch-all at the end


# ─── Stripe Checkout ───────────────────────────────────
@app.route('/create-checkout-session', methods=['POST'])
@rate_limit(max_requests=30, window_seconds=900)
def create_checkout_session():
    # Support both JSON and form-encoded
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form.to_dict()

    lookup_key = data.get('lookup_key', '').strip()
    email = data.get('email', '').strip()
    name = data.get('name', '').strip()

    if not lookup_key or not email or not name:
        return jsonify({'error': 'Missing required fields.'}), 400

    if not EMAIL_RE.match(email):
        return jsonify({'error': 'Invalid email address.'}), 400

    price_id = PRICE_MAP.get(lookup_key)
    if not price_id:
        return jsonify({'error': 'Invalid plan selected.'}), 400

    try:
        session = stripe.checkout.Session.create(
            mode='subscription',
            payment_method_types=['card'],
            customer_email=email,
            metadata={'name': name[:100], 'lookup_key': lookup_key},
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            subscription_data={
                'metadata': {'name': name[:100], 'lookup_key': lookup_key},
            },
            success_url=f'{SITE_URL}/payment.html?session_id={{CHECKOUT_SESSION_ID}}&status=success',
            cancel_url=f'{SITE_URL}/payment.html?status=cancelled',
        )
        return jsonify({'url': session.url})
    except stripe.error.StripeError as e:
        print(f'Stripe error: {e}')
        return jsonify({'error': 'Could not create checkout session.'}), 500


# ─── Stripe Webhook ────────────────────────────────────
@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data()
    sig = request.headers.get('Stripe-Signature', '')

    try:
        event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        print(f'Webhook error: {e}')
        return jsonify({'error': str(e)}), 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        email = session.get('customer_email', '')
        meta = session.get('metadata', {})
        name = meta.get('name', '')
        plan = meta.get('lookup_key', '')
        subscription_id = session.get('subscription', '')
        ref_code = session.get('client_reference_id', '') or meta.get('ref_code', '')
        customer_id = session.get('customer', '')
        print(f"✅ Payment successful for {email} — Session: {session['id']}")
        _provision_user(email, name, plan, session['id'], subscription_id)
        if ref_code:
            _track_referral(ref_code, email, name, plan, subscription_id, customer_id)

    elif event['type'] == 'customer.subscription.deleted':
        sub = event['data']['object']
        print(f"❌ Subscription cancelled: {sub['id']}")
        _cancel_user(sub.get('id', ''))

    elif event['type'] == 'invoice.paid':
        invoice = event['data']['object']
        sub_id  = invoice.get('subscription', '')
        amount_paid = invoice.get('amount_paid', 0)  # in pence
        period  = invoice.get('period_end', 0)
        print(f"💰 Invoice paid: sub={sub_id} amount={amount_paid}")
        _record_commission(sub_id, amount_paid, period)

    elif event['type'] == 'invoice.payment_failed':
        invoice = event['data']['object']
        email = invoice.get('customer_email', '')
        attempt = invoice.get('attempt_count', 1)
        print(f"⚠️ Payment failed for {email} — attempt {attempt}")
        _handle_payment_failed(email, attempt)

    elif event['type'] == 'customer.subscription.trial_will_end':
        sub = event['data']['object']
        print(f"⏰ Trial ending soon: {sub['id']}")
        _handle_trial_ending(sub)

    else:
        print(f"Unhandled event: {event['type']}")

    return jsonify({'received': True})


# ─── User Provisioning ─────────────────────────────────
def _provision_user(email, name, plan, session_id, subscription_id=''):
    now = datetime.now(timezone.utc).isoformat()
    with _file_lock:
        users = _read_json(USERS_FILE)
        existing = next((u for u in users if u.get('email') == email), None)
        if existing:
            existing.update({'plan': plan, 'status': 'active', 'session_id': session_id, 'updated_at': now})
        else:
            users.append({
                'email': email,
                'name': name,
                'plan': plan,
                'status': 'active',
                'session_id': session_id,
                'subscription_id': subscription_id,
                'trial_started_at': now,
                'created_at': now,
                'updated_at': now,
            })
        _write_json(USERS_FILE, users)

    plan_labels = {
        'starter_monthly': 'Starter (Monthly)',
        'starter_yearly':  'Starter (Annual)',
        'growth_monthly':  'Growth (Monthly)',
        'growth_yearly':   'Growth (Annual)',
        'topup':           'Credit Top-up',
    }
    plan_name = plan_labels.get(plan, 'ClientSniper')
    first = escape(name.split()[0]) if name else 'there'
    plan_name_safe = escape(plan_name)

    try:
        send_email(
            to=email,
            subject='Welcome to ClientSniper — you\'re all set',
            text_body=(
                f'Hi {first},\n\n'
                f'Your ClientSniper {plan_name} subscription is now active.\n\n'
                f'You also have 500 bonus leads waiting in your account — on us.\n\n'
                f'Head to {SITE_URL} to get started.\n\n'
                f'If you have any questions, just reply to this email.\n\n'
                f'Best,\nThe ClientSniper Team'
            ),
            html_body=f'''
                <div style="font-family:sans-serif;max-width:600px;color:#333;background:#030507;padding:40px;border-radius:4px">
                  <h2 style="color:#C9A465;font-family:Georgia,serif;margin-bottom:8px">Welcome to ClientSniper</h2>
                  <p style="color:#F0EBE0;margin-bottom:16px">Hi {first},</p>
                  <p style="color:#8a8578;line-height:1.7">Your <strong style="color:#F0EBE0">{plan_name_safe}</strong> subscription is now active.</p>
                  <p style="color:#8a8578;line-height:1.7">You also have <strong style="color:#C9A465">500 bonus leads</strong> waiting in your account — on us.</p>
                  <a href="{SITE_URL}" style="display:inline-block;margin-top:24px;padding:14px 32px;background:#C9A465;color:#030507;
                     font-weight:600;letter-spacing:1.5px;text-transform:uppercase;font-size:13px;text-decoration:none">
                    Start Finding Leads &rarr;
                  </a>
                  <p style="color:#8a8578;font-size:13px;margin-top:32px">
                    Cancel any time from your dashboard — no questions asked.
                  </p>
                  <p style="color:#8a8578;font-size:13px;margin-top:24px">
                    Best,<br><strong style="color:#F0EBE0">The ClientSniper Team</strong>
                  </p>
                </div>
            ''',
        )
    except Exception as e:
        print(f'Welcome email error: {e}')


def _cancel_user(subscription_id):
    now = datetime.now(timezone.utc).isoformat()
    cancelled_user = None
    with _file_lock:
        users = _read_json(USERS_FILE)
        for u in users:
            if u.get('subscription_id') == subscription_id:
                u['status'] = 'cancelled'
                u['cancelled_at'] = now
                cancelled_user = u
                break
        _write_json(USERS_FILE, users)

    if cancelled_user:
        first = escape(cancelled_user.get('name', '').split()[0]) if cancelled_user.get('name') else 'there'
        try:
            send_email(
                to=cancelled_user['email'],
                subject='Your ClientSniper subscription has been cancelled',
                text_body=(
                    f'Hi {first},\n\n'
                    f'Your ClientSniper subscription has been cancelled. You had access until the end of your billing period.\n\n'
                    f'If you cancelled by mistake or want to come back, just visit {SITE_URL}/payment.html.\n\n'
                    f'We\'d love to know why you left — just reply to this email.\n\n'
                    f'Best,\nThe ClientSniper Team'
                ),
                html_body=f'''
                    <div style="font-family:sans-serif;max-width:600px;color:#333;background:#030507;padding:40px">
                      <h2 style="color:#C9A465;font-family:Georgia,serif">Subscription Cancelled</h2>
                      <p style="color:#F0EBE0">Hi {first},</p>
                      <p style="color:#8a8578;line-height:1.7">Your ClientSniper subscription has been cancelled. You had access until the end of your current billing period.</p>
                      <p style="color:#8a8578;line-height:1.7">Changed your mind? You can resubscribe any time.</p>
                      <a href="{SITE_URL}/payment.html" style="display:inline-block;margin-top:24px;padding:14px 32px;background:#C9A465;color:#030507;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;font-size:13px;text-decoration:none">Resubscribe &rarr;</a>
                      <p style="color:#8a8578;font-size:13px;margin-top:32px">We'd love to know what we could have done better — just reply to this email.<br><strong style="color:#F0EBE0">The ClientSniper Team</strong></p>
                    </div>
                ''',
            )
        except Exception as e:
            print(f'Cancellation email error: {e}')


def _handle_payment_failed(email, attempt_count):
    if not email:
        return
    try:
        send_email(
            to=email,
            subject='Action needed — ClientSniper payment failed',
            text_body=(
                f'Hi,\n\n'
                f'We couldn\'t process your ClientSniper payment (attempt {attempt_count}).\n\n'
                f'Please update your payment details to keep your account active.\n\n'
                f'Update payment: {SITE_URL}/payment.html\n\n'
                f'If you need help, just reply to this email.\n\n'
                f'The ClientSniper Team'
            ),
            html_body=f'''
                <div style="font-family:sans-serif;max-width:600px;color:#333;background:#030507;padding:40px">
                  <h2 style="color:#E04545;font-family:Georgia,serif">Payment Failed</h2>
                  <p style="color:#F0EBE0">Hi,</p>
                  <p style="color:#8a8578;line-height:1.7">We couldn't process your ClientSniper payment (attempt {attempt_count}). Please update your payment details to keep your account active.</p>
                  <a href="{SITE_URL}/payment.html" style="display:inline-block;margin-top:24px;padding:14px 32px;background:#C9A465;color:#030507;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;font-size:13px;text-decoration:none">Update Payment &rarr;</a>
                  <p style="color:#8a8578;font-size:13px;margin-top:32px">Questions? Just reply to this email.<br><strong style="color:#F0EBE0">The ClientSniper Team</strong></p>
                </div>
            ''',
        )
    except Exception as e:
        print(f'Payment failed email error: {e}')


def _handle_trial_ending(subscription):
    customer_id = subscription.get('customer', '')
    if not customer_id:
        return
    with _file_lock:
        users = _read_json(USERS_FILE)
        user = next((u for u in users if u.get('subscription_id') == subscription.get('id', '')), None)
    if not user:
        return
    email = user.get('email', '')
    first = escape(user.get('name', '').split()[0]) if user.get('name') else 'there'
    plan_labels = {
        'starter_monthly': 'Starter', 'starter_yearly': 'Starter',
        'growth_monthly':  'Growth',  'growth_yearly':  'Growth',
    }
    plan_name = plan_labels.get(user.get('plan', ''), 'ClientSniper')
    try:
        send_email(
            to=email,
            subject='Your ClientSniper renewal is coming up',
            text_body=(
                f'Hi {first},\n\n'
                f'Just a heads up — your ClientSniper {plan_name} subscription renews in 3 days.\n\n'
                f'To cancel before then: {SITE_URL}/payment.html\n\n'
                f'Questions? Just reply to this email.\n\n'
                f'The ClientSniper Team'
            ),
            html_body=f'''
                <div style="font-family:sans-serif;max-width:600px;color:#333;background:#030507;padding:40px">
                  <h2 style="color:#C9A465;font-family:Georgia,serif">Renewal reminder</h2>
                  <p style="color:#F0EBE0">Hi {first},</p>
                  <p style="color:#8a8578;line-height:1.7">Your <strong style="color:#F0EBE0">ClientSniper {plan_name}</strong> subscription renews in 3 days. We just wanted to give you a heads up.</p>
                  <p style="color:#8a8578;line-height:1.7">If you'd like to cancel before then, you can do so below — no questions asked.</p>
                  <a href="{SITE_URL}/payment.html" style="display:inline-block;margin-top:24px;padding:14px 32px;background:#C9A465;color:#030507;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;font-size:13px;text-decoration:none">Manage Subscription &rarr;</a>
                  <p style="color:#8a8578;font-size:13px;margin-top:32px">Thanks for being a ClientSniper customer.<br><strong style="color:#F0EBE0">The ClientSniper Team</strong></p>
                </div>
            ''',
        )
    except Exception as e:
        print(f'Trial ending email error: {e}')


# ─── Sales Rep Referral Tracking ──────────────────────
def _track_referral(ref_code, customer_email, customer_name, plan, subscription_id, stripe_customer_id):
    if not _sb:
        print(f'[SUPABASE NOT CONFIGURED] Referral {ref_code} → {customer_email}')
        return
    try:
        res = _sb.table('sales_reps').select('id').eq('referral_code', ref_code).eq('status', 'active').maybe_single().execute()
        if not res.data:
            print(f'Referral code {ref_code} not found or rep inactive.')
            return
        rep_id = res.data['id']
        plan_values = {
            'starter_monthly': 25.00, 'starter_yearly': 20.00,
            'growth_monthly': 45.00,  'growth_yearly': 36.00,
        }
        monthly_value = plan_values.get(plan, 0)
        _sb.table('referrals').insert({
            'rep_id': rep_id,
            'customer_email': customer_email,
            'customer_name': customer_name or customer_email,
            'plan': plan,
            'monthly_value': monthly_value,
            'status': 'active',
            'stripe_subscription_id': subscription_id,
            'stripe_customer_id': stripe_customer_id,
        }).execute()
        print(f'✅ Referral tracked: rep={rep_id} customer={customer_email}')
    except Exception as e:
        print(f'Referral tracking error: {e}')


def _record_commission(subscription_id, amount_paid_pence, period_end_ts):
    if not _sb:
        return
    try:
        res = _sb.table('referrals').select('id,rep_id').eq('stripe_subscription_id', subscription_id).eq('status', 'active').maybe_single().execute()
        if not res.data:
            return
        referral_id = res.data['id']
        rep_id      = res.data['rep_id']
        invoice_gbp = amount_paid_pence / 100
        commission  = round(invoice_gbp * 0.20, 2)
        from datetime import datetime, timezone
        period_str = datetime.fromtimestamp(period_end_ts, tz=timezone.utc).strftime('%b %Y') if period_end_ts else ''
        _sb.table('commissions').insert({
            'rep_id':        rep_id,
            'referral_id':   referral_id,
            'amount':        commission,
            'invoice_amount': invoice_gbp,
            'period':        period_str,
            'status':        'pending',
        }).execute()
        print(f'✅ Commission recorded: rep={rep_id} amount=£{commission}')
    except Exception as e:
        print(f'Commission recording error: {e}')


# ─── Forum Helpers ─────────────────────────────────────
def _read_forum():
    raw = _read_json(FORUM_FILE)
    if isinstance(raw, dict) and 'threads' in raw:
        return raw
    return {'threads': []}


def _write_forum(data):
    with open(FORUM_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _hot_score(t):
    votes = t.get('votes', 0)
    try:
        dt = datetime.fromisoformat(t.get('created_at', '').replace('Z', '+00:00'))
        age_hours = max(0.5, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)
    except Exception:
        age_hours = 24
    pin_bonus = 1000 if t.get('pinned') else 0
    return pin_bonus + votes / (age_hours + 2) ** 1.5


def _thread_summary(t):
    return {
        'id':          t.get('id'),
        'title':       t.get('title', ''),
        'body':        t.get('body', '')[:300],
        'author':      t.get('author', ''),
        'is_team':     t.get('is_team', False),
        'category':    t.get('category', 'general'),
        'tags':        t.get('tags', []),
        'pinned':      t.get('pinned', False),
        'created_at':  t.get('created_at', ''),
        'votes':       t.get('votes', 0),
        'reply_count': len(t.get('replies', [])),
    }


# ─── Forum API ──────────────────────────────────────────
@app.route('/api/forum/threads', methods=['GET'])
def forum_list():
    cat   = request.args.get('cat', 'all')
    sort  = request.args.get('sort', 'hot')
    q     = request.args.get('q', '').lower().strip()
    page  = max(1, int(request.args.get('page', 1) or 1))
    per   = 15

    data    = _read_forum()
    threads = data.get('threads', [])

    if cat != 'all':
        threads = [t for t in threads if t.get('category') == cat]
    if q:
        threads = [t for t in threads if q in t.get('title', '').lower() or q in t.get('body', '').lower()]

    if sort == 'new':
        threads = sorted(threads, key=lambda t: t.get('created_at', ''), reverse=True)
    elif sort == 'top':
        threads = sorted(threads, key=lambda t: t.get('votes', 0), reverse=True)
    elif sort == 'unanswered':
        threads = [t for t in threads if len(t.get('replies', [])) == 0]
        threads = sorted(threads, key=lambda t: t.get('created_at', ''), reverse=True)
    else:
        threads = sorted(threads, key=_hot_score, reverse=True)

    total = len(threads)
    page_threads = threads[(page - 1) * per: page * per]

    all_threads = data.get('threads', [])
    cat_counts = {}
    for t in all_threads:
        c = t.get('category', 'general')
        cat_counts[c] = cat_counts.get(c, 0) + 1

    return jsonify({
        'threads':   [_thread_summary(t) for t in page_threads],
        'total':     total,
        'page':      page,
        'pages':     max(1, (total + per - 1) // per),
        'cat_counts': cat_counts,
        'stats': {
            'threads': len(all_threads),
            'replies': sum(len(t.get('replies', [])) for t in all_threads),
        },
    })


@app.route('/api/forum/threads', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=3600)
def forum_create():
    d       = request.get_json(silent=True) or {}
    title   = d.get('title',     '').strip()[:200]
    body    = d.get('body',      '').strip()[:5000]
    author  = d.get('author',    '').strip()[:60]
    cat     = d.get('category',  'general').strip()
    tags    = [t.strip()[:30] for t in d.get('tags', [])[:5] if t.strip()]
    tpass   = d.get('team_pass', '').strip()

    if not title or not body or not author:
        return jsonify({'error': 'Title, body, and your name are required.'}), 400

    valid = {'general', 'maps', 'outreach', 'strategy', 'showcase', 'feature'}
    if cat not in valid:
        cat = 'general'

    is_team = bool(FORUM_TEAM_PASS and tpass == FORUM_TEAM_PASS)
    now = datetime.now(timezone.utc).isoformat()

    thread = {
        'id':         str(uuid.uuid4()),
        'title':      escape(title),
        'body':       escape(body),
        'author':     escape(author),
        'is_team':    is_team,
        'category':   cat,
        'tags':       [escape(t) for t in tags],
        'pinned':     False,
        'created_at': now,
        'votes':      0,
        'replies':    [],
    }

    with _file_lock:
        data = _read_forum()
        data['threads'].append(thread)
        _write_forum(data)

    return jsonify({'id': thread['id'], 'success': True}), 201


@app.route('/api/forum/threads/<thread_id>', methods=['GET'])
def forum_get(thread_id):
    data   = _read_forum()
    thread = next((t for t in data['threads'] if t.get('id') == thread_id), None)
    if not thread:
        return jsonify({'error': 'Thread not found'}), 404
    return jsonify(thread)


@app.route('/api/forum/threads/<thread_id>/reply', methods=['POST'])
@rate_limit(max_requests=20, window_seconds=3600)
def forum_reply(thread_id):
    d      = request.get_json(silent=True) or {}
    body   = d.get('body',      '').strip()[:3000]
    author = d.get('author',    '').strip()[:60]
    tpass  = d.get('team_pass', '').strip()

    if not body or not author:
        return jsonify({'error': 'Your name and a message are required.'}), 400

    is_team = bool(FORUM_TEAM_PASS and tpass == FORUM_TEAM_PASS)
    now = datetime.now(timezone.utc).isoformat()

    reply = {
        'id':         str(uuid.uuid4()),
        'body':       escape(body),
        'author':     escape(author),
        'is_team':    is_team,
        'created_at': now,
        'votes':      0,
    }

    with _file_lock:
        data   = _read_forum()
        thread = next((t for t in data['threads'] if t.get('id') == thread_id), None)
        if not thread:
            return jsonify({'error': 'Thread not found'}), 404
        thread.setdefault('replies', []).append(reply)
        _write_forum(data)

    return jsonify({'id': reply['id'], 'success': True}), 201


@app.route('/api/forum/vote', methods=['POST'])
@rate_limit(max_requests=60, window_seconds=3600)
def forum_vote():
    d         = request.get_json(silent=True) or {}
    thread_id = d.get('thread_id', '').strip()
    reply_id  = d.get('reply_id',  '').strip()
    direction = d.get('direction', 'up')
    delta     = 1 if direction == 'up' else -1

    with _file_lock:
        data   = _read_forum()
        thread = next((t for t in data['threads'] if t.get('id') == thread_id), None)
        if not thread:
            return jsonify({'error': 'Thread not found'}), 404
        if reply_id:
            reply = next((r for r in thread.get('replies', []) if r.get('id') == reply_id), None)
            if not reply:
                return jsonify({'error': 'Reply not found'}), 404
            reply['votes'] = max(0, reply.get('votes', 0) + delta)
            votes = reply['votes']
        else:
            thread['votes'] = max(0, thread.get('votes', 0) + delta)
            votes = thread['votes']
        _write_forum(data)

    return jsonify({'votes': votes})


@app.route('/api/forum/pin', methods=['POST'])
def forum_pin():
    d         = request.get_json(silent=True) or {}
    thread_id = d.get('thread_id', '').strip()
    tpass     = d.get('team_pass', '').strip()
    pinned    = bool(d.get('pinned', True))

    if not FORUM_TEAM_PASS or tpass != FORUM_TEAM_PASS:
        return jsonify({'error': 'Unauthorised'}), 403

    with _file_lock:
        data   = _read_forum()
        thread = next((t for t in data['threads'] if t.get('id') == thread_id), None)
        if not thread:
            return jsonify({'error': 'Thread not found'}), 404
        thread['pinned'] = pinned
        _write_forum(data)

    return jsonify({'success': True})


# ─── Student Discount Verification ────────────────────
STUDENT_COUPON = os.getenv('STUDENT_COUPON', 'STUDENT40')

FREE_EMAIL_DOMAINS = {
    'gmail.com','yahoo.com','hotmail.com','outlook.com','live.com','icloud.com',
    'me.com','mac.com','aol.com','protonmail.com','proton.me','mail.com',
    'ymail.com','googlemail.com','msn.com','hotmail.co.uk','yahoo.co.uk',
    'live.co.uk','btinternet.com','sky.com','virginmedia.com',
}

@app.route('/api/student-verify', methods=['POST'])
@rate_limit(max_requests=3, window_seconds=3600)
def student_verify():
    data  = request.get_json(silent=True) or {}
    email = data.get('email', '').strip().lower()
    name  = data.get('name',  '').strip()[:80]

    if not email or not EMAIL_RE.match(email):
        return jsonify({'error': 'Please enter a valid email address.'}), 400

    domain = email.split('@')[-1]

    if domain in FREE_EMAIL_DOMAINS:
        return jsonify({'error': 'Please use your university or college email address, not a personal email.'}), 400

    first = escape(name.split()[0]) if name else 'there'
    coupon = STUDENT_COUPON

    try:
        send_email(
            to=email,
            subject='Your ClientSniper student discount code',
            text_body=(
                f'Hi {first},\n\n'
                f'Here is your student discount code for ClientSniper:\n\n'
                f'  {coupon}\n\n'
                f'Enter this code at checkout to get 40% off any plan.\n\n'
                f'Go to: {SITE_URL}/payment.html\n\n'
                f'The discount applies to your first payment and every renewal.\n\n'
                f'Best,\nThe ClientSniper Team'
            ),
            html_body=f'''
                <div style="font-family:sans-serif;max-width:600px;background:#030507;padding:40px;color:#F0EBE0">
                  <h2 style="color:#C9A465;font-family:Georgia,serif;margin-bottom:8px">Your Student Discount</h2>
                  <p style="color:#8a8578;margin-bottom:24px">Hi {first}, here&#39;s your exclusive student discount code:</p>
                  <div style="background:#0a0d10;border:1px solid #C9A465;padding:24px;text-align:center;margin-bottom:24px">
                    <div style="font-family:Georgia,serif;font-size:2rem;color:#C9A465;letter-spacing:4px">{coupon}</div>
                    <div style="color:#8a8578;font-size:.8rem;margin-top:8px;letter-spacing:1px">40% OFF · ENTER AT CHECKOUT</div>
                  </div>
                  <p style="color:#8a8578;line-height:1.7;font-size:.9rem">This discount applies to every billing cycle — not just the first payment. Use it on either the Starter or Growth plan.</p>
                  <a href="{SITE_URL}/payment.html" style="display:inline-block;margin-top:24px;padding:14px 32px;background:#C9A465;color:#030507;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;font-size:13px;text-decoration:none">Choose a Plan &rarr;</a>
                  <p style="color:#8a8578;font-size:12px;margin-top:32px">The ClientSniper Team</p>
                </div>
            ''',
        )
    except Exception as e:
        print(f'Student verify email error: {e}')
        return jsonify({'error': 'Could not send email. Please try again.'}), 500

    return jsonify({'success': True})


# ─── Contact Form ──────────────────────────────────────
@app.route('/api/contact', methods=['POST'])
@rate_limit(max_requests=5, window_seconds=3600)
def contact():
    data = request.get_json(silent=True) or {}

    name = data.get('name', '').strip()[:100]
    email = data.get('email', '').strip()
    subject = data.get('subject', '').strip()[:200] or 'No subject'
    message = data.get('message', '').strip()[:5000]

    if not name or not email or not message:
        return jsonify({'error': 'Name, email, and message are required.'}), 400

    if not EMAIL_RE.match(email):
        return jsonify({'error': 'Invalid email address.'}), 400

    name_s = escape(name)
    email_s = escape(email)
    subject_s = escape(subject)
    message_s = escape(message)

    try:
        # Send to support
        send_email(
            to=CONTACT_EMAIL_TO,
            subject=f'[ClientSniper Contact] {subject}',
            text_body=f'Name: {name}\nEmail: {email}\nSubject: {subject}\n\nMessage:\n{message}',
            html_body=f'''
                <div style="font-family:sans-serif;max-width:600px">
                  <h2 style="color:#C9A465;border-bottom:1px solid #eee;padding-bottom:8px">New Contact Message</h2>
                  <p><strong>Name:</strong> {name_s}</p>
                  <p><strong>Email:</strong> {email_s}</p>
                  <p><strong>Subject:</strong> {subject_s}</p>
                  <hr style="border:none;border-top:1px solid #eee;margin:16px 0">
                  <p style="white-space:pre-wrap">{message_s}</p>
                </div>
            ''',
            reply_to=email,
        )

        # Auto-reply to sender
        send_email(
            to=email,
            subject='We got your message — ClientSniper',
            text_body=f'Hi {name},\n\nThanks for reaching out! We\'ve received your message and will get back to you within 24 hours.\n\nBest,\nThe ClientSniper Team',
            html_body=f'''
                <div style="font-family:sans-serif;max-width:600px;color:#333">
                  <h2 style="color:#C9A465">Thanks for reaching out!</h2>
                  <p>Hi {name_s},</p>
                  <p>We\'ve received your message and will get back to you within 24 hours.</p>
                  <p style="margin-top:24px">Best,<br><strong>The ClientSniper Team</strong></p>
                </div>
            ''',
        )

        return jsonify({'success': True, 'message': 'Message sent successfully.'})
    except Exception as e:
        print(f'Contact form error: {e}')
        return jsonify({'error': 'Could not send message. Please try again later.'}), 500


# ─── Newsletter ────────────────────────────────────────
@app.route('/api/newsletter', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=3600)
def newsletter():
    data = request.get_json(silent=True) or {}
    email = data.get('email', '').strip()

    if not email or not EMAIL_RE.match(email):
        return jsonify({'error': 'Valid email is required.'}), 400

    now = datetime.now(timezone.utc).isoformat()
    with _file_lock:
        signups = _read_json(NEWSLETTER_FILE)
        if not any(s.get('email') == email for s in signups):
            signups.append({'email': email, 'subscribed_at': now})
            _write_json(NEWSLETTER_FILE, signups)
    print(f'📬 Newsletter signup: {email}')
    return jsonify({'success': True, 'message': 'Subscribed!'})


# ─── SEO Files ─────────────────────────────────────────
@app.route('/robots.txt')
def robots():
    return send_from_directory('.', 'robots.txt', mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory('.', 'sitemap.xml', mimetype='application/xml')


# ─── Static File Catch-All (must be AFTER all API routes) ──
@app.route('/<path:filename>')
def static_files(filename):
    allowed_ext = {'.html', '.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp', '.woff', '.woff2', '.ttf', '.mp4', '.webm'}
    ext = os.path.splitext(filename)[1].lower()
    if ext in allowed_ext or '.' not in filename:
        filepath = os.path.join('.', filename)
        if os.path.isfile(filepath):
            return send_from_directory('.', filename)
    abort(404)


# ─── Error Handlers ────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return send_from_directory('.', 'index.html'), 404


@app.errorhandler(429)
def rate_limited(e):
    return jsonify({'error': 'Too many requests. Please try again later.'}), 429


# ─── Start ─────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.getenv('PORT', 3000))
    print(f'\n🎯 ClientSniper server running on http://localhost:{port}\n')
    app.run(host='0.0.0.0', port=port, debug=True)
