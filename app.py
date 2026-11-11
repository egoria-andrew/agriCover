"""AgriCover learning preview: standard-library WSGI app with SQLite storage."""
from __future__ import annotations

import html
import csv
import io
import os
import re
import secrets
import threading
import webbrowser
from http import cookies
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote
from wsgiref.simple_server import WSGIServer, make_server

from database import Database
from security import decode_session, encode_session, hash_password, verify_password

ROOT = Path(__file__).resolve().parent
MAX_BODY_BYTES = 64 * 1024
db = Database()
db.initialize()


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(value: int | str) -> str:
    return f"UGX {int(value):,}"


def normalize_phone(raw: str) -> str:
    value = re.sub(r"[\s-]", "", raw)
    if value.startswith("+256"):
        value = "0" + value[4:]
    elif value.startswith("256") and len(value) == 12:
        value = "0" + value[3:]
    return value


def valid_phone(value: str) -> bool:
    return re.fullmatch(r"0\d{9}", value) is not None


def button(label: str, action: str, token: str, *, kind: str = "primary", extra: str = "") -> str:
    return (f'<form method="post" action="{esc(action)}" class="inline-form">'
            f'<input type="hidden" name="csrf" value="{esc(token)}">'
            f'<button class="button {kind}" type="submit" {extra}>{esc(label)}</button></form>')


def card(title: str, content: str, extra_class: str = "") -> str:
    return f'<section class="card {extra_class}"><h2>{esc(title)}</h2>{content}</section>'


class AgriCoverApplication:
    """Small WSGI application with role-aware routes and signed sessions."""

    def __init__(self, database: Database):
        self.database = database

    def __call__(self, environ, start_response):
        path = unquote(environ.get("PATH_INFO", "/"))
        method = environ.get("REQUEST_METHOD", "GET").upper()
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        cookie = cookies.SimpleCookie()
        try:
            cookie.load(environ.get("HTTP_COOKIE", ""))
            session = decode_session(cookie.get("agricover_session").value if cookie.get("agricover_session") else None)
        except cookies.CookieError:
            session = {}
        session.setdefault("csrf", secrets.token_urlsafe(24))
        form = {}
        if method == "POST":
            try:
                length = int(environ.get("CONTENT_LENGTH") or "0")
            except ValueError:
                length = 0
            if length > MAX_BODY_BYTES:
                return self.respond(start_response, "413 Payload Too Large", "<h1>Request too large</h1>", session)
            raw = environ.get("wsgi.input").read(length) if length else b""
            form = {key: values[-1] for key, values in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}
            if not secrets.compare_digest(str(form.get("csrf", "")), str(session["csrf"])):
                session["flash"] = ("Your page expired. Please try again.", "error")
                return self.redirect(start_response, "/", session)

        user = self.database.one("SELECT * FROM users WHERE id=?", (session.get("uid"),)) if session.get("uid") else None
        session["_current_path"] = path
        try:
            status, body, headers = self.route(path, method, query, form, session, user)
        except Exception as error:  # Keep the preview friendly; details go to server logs.
            print(f"AgriCover request error: {type(error).__name__}: {error}")
            session["flash"] = ("Something went wrong. Please retry or return to the dashboard.", "error")
            status, body, headers = "500 Internal Server Error", self.layout("Something went wrong", '<div class="empty-state">Please try again.</div>', user, session), [("Content-Type", "text/html; charset=utf-8")]
        return self.respond(start_response, status, body, session, headers)

    def respond(self, start_response, status: str, body: str | bytes, session: dict, headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        response_headers = [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(data))),
                            ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                            ("X-Frame-Options", "DENY"), ("Referrer-Policy", "same-origin")]
        if headers:
            overridden = {name.lower() for name, _ in headers}
            response_headers = [(name, value) for name, value in response_headers if name.lower() not in overridden]
            response_headers.extend(headers)
        secure = "; Secure" if os.environ.get("RENDER") or os.environ.get("HTTPS") else ""
        response_headers.append(("Set-Cookie", f"agricover_session={encode_session(session)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=1209600{secure}"))
        start_response(status, response_headers)
        return [data]

    def redirect(self, start_response, target: str, session: dict):
        return self.respond(start_response, "303 See Other", "", session, [("Location", target)])

    def layout(self, title: str, content: str, user, session: dict) -> str:
        notice = session.pop("flash", None)
        notice_html = ""
        if notice:
            message, kind = notice
            notice_html = f'<div class="notice {esc(kind)}" role="status">{esc(message)}</div>'
        token = session.get("csrf", "")
        if user:
            role = user["role"]
            links = [("Dashboard", "/dashboard", "nav-home"), ("Policies", "/policies", "nav-policies"), ("Claims", "/claims", "nav-claims"), ("USSD", "/ussd", "nav-ussd"), ("Messages", "/messages", "nav-inbox")]
            if role == "agent":
                links = [("Agent desk", "/agent", "nav-agent"), ("Clients", "/agent/clients", "nav-clients"), ("Add farmer", "/agent/clients#onboard", "nav-onboard"), ("USSD", "/ussd", "nav-ussd"), ("Messages", "/messages", "nav-inbox")]
            elif role == "admin":
                links = [("Overview", "/admin", "nav-admin"), ("Agent desk", "/agent", "nav-agent"), ("Add farmer", "/agent/clients#onboard", "nav-onboard"), ("USSD", "/ussd", "nav-ussd"), ("Messages", "/messages", "nav-inbox")]
            current_path = session.get("_current_path", "")
            nav_items = []
            for label, href, nav_class in links:
                is_current = current_path == href or (current_path == "/farmer" and href == "/dashboard")
                current_class = " is-current" if is_current else ""
                aria_current = ' aria-current="page"' if is_current else ""
                nav_items.append(f'<a class="menu-link {nav_class}{current_class}" href="{href}"{aria_current}>{esc(label)}</a>')
            nav = "".join(nav_items)
            nav_html = f'<nav class="primary-nav" aria-label="Main menu">{nav}</nav>'
            top_action = button("Sign out", "/logout", token, kind="ghost", extra='aria-label="Sign out"')
            user_label = f'<span class="user-chip">{esc(user["full_name"])} <small>{esc(role.title())}</small></span>'
        else:
            nav_html = '<nav class="primary-nav" aria-label="Main menu"><a class="menu-link" href="/">Sign in</a><a class="menu-link" href="/signup">Join AgriCover</a><a class="menu-link" href="/ussd">USSD</a></nav>'
            top_action = '<a class="button ghost" href="/signup">Create account</a>'
            user_label = '<span class="user-chip">AgriCover Preview</span>'
        return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#183d35"><title>{esc(title)} · AgriCover</title>
<link rel="stylesheet" href="/static/style.css"></head><body>
<header class="topbar"><a class="brand" href="/dashboard"><span class="brand-mark">A</span><span>AgriCover<small>Farmer protection, made local</small></span></a>{nav_html}<div class="top-actions">{user_label}{top_action}</div></header>
<main class="page-wrap">{notice_html}{content}<footer class="site-footer">AgriCover · illustrative experience only · no real insurance, payments, SMS or telecom USSD</footer></main>
<script src="/static/app.js" defer></script></body></html>'''

    def page(self, title, content, user, session, status="200 OK", headers=None):
        return status, self.layout(title, content, user, session), headers or []

    def require_user(self, user, session):
        if user:
            return None
        session["flash"] = ("Sign in to continue.", "error")
        return "/"

    def require_role(self, user, role, session):
        redirect = self.require_user(user, session)
        if redirect:
            return redirect
        if user["role"] != role and not (role == "agent" and user["role"] == "admin"):
            session["flash"] = ("That page is only available to its assigned staff role.", "error")
            return "/dashboard"
        return None

    def route(self, path, method, query, form, session, user):
        if path == "/static/style.css" and method == "GET":
            return self.static("style.css")
        if path == "/static/app.js" and method == "GET":
            return self.static("app.js", "text/javascript; charset=utf-8")
        if path == "/favicon.ico":
            return "204 No Content", b"", [("Content-Type", "image/x-icon")]
        if path == "/" and method == "GET":
            if user:
                return self.redirect_response(self.destination(user["role"]), session)
            return self.login_page(user, session)
        if path == "/signup" and method == "GET":
            if user:
                return self.redirect_response(self.destination(user["role"]), session)
            return self.signup_page(user, session)
        if path == "/login" and method == "POST":
            return self.login(form, session)
        if path == "/signup" and method == "POST":
            return self.signup(form, session)
        if path == "/logout" and method == "POST":
            session.clear(); session["csrf"] = secrets.token_urlsafe(24)
            session["flash"] = ("You have signed out.", "success")
            return self.redirect_response("/", session)
        if path == "/dashboard" and method == "GET":
            if not user:
                return self.redirect_response("/", session)
            return self.redirect_response(self.destination(user["role"]), session)
        if path == "/farmer" and method == "GET":
            if redirect := self.require_role(user, "farmer", session):
                return self.redirect_response(redirect, session)
            return self.farmer_dashboard(user, session)
        if path == "/policies" and method == "GET":
            if redirect := self.require_user(user, session):
                return self.redirect_response(redirect, session)
            return self.policies_page(user, session)
        if path == "/claims" and method == "GET":
            if redirect := self.require_role(user, "farmer", session):
                return self.redirect_response(redirect, session)
            return self.claims_page(user, session)
        if path == "/policies/create" and method == "POST":
            if redirect := self.require_role(user, "farmer", session):
                return self.redirect_response(redirect, session)
            return self.create_policy(user, form, session)
        if path.startswith("/policies/") and path.endswith("/simulate-payment") and method == "POST":
            if redirect := self.require_role(user, "farmer", session):
                return self.redirect_response(redirect, session)
            return self.simulate_payment(user, path, session)
        if path == "/claims/create" and method == "POST":
            if redirect := self.require_role(user, "farmer", session):
                return self.redirect_response(redirect, session)
            return self.create_claim(user, form, session)
        if path == "/agent" and method == "GET":
            if redirect := self.require_role(user, "agent", session):
                return self.redirect_response(redirect, session)
            return self.agent_dashboard(user, session)
        if path == "/agent/clients" and method == "GET":
            if redirect := self.require_role(user, "agent", session):
                return self.redirect_response(redirect, session)
            return self.agent_clients_page(user, session)
        if path == "/agent/clients/create" and method == "POST":
            if redirect := self.require_role(user, "agent", session):
                return self.redirect_response(redirect, session)
            return self.onboard_farmer(form, session)
        if path.startswith("/agent/policy/") and method == "POST":
            if redirect := self.require_role(user, "agent", session):
                return self.redirect_response(redirect, session)
            return self.review_policy(user, path, form, session)
        if path.startswith("/agent/claim/") and method == "POST":
            if redirect := self.require_role(user, "agent", session):
                return self.redirect_response(redirect, session)
            return self.review_claim(user, path, form, session)
        if path == "/admin" and method == "GET":
            if redirect := self.require_role(user, "admin", session):
                return self.redirect_response(redirect, session)
            return self.admin_dashboard(user, session)
        if path == "/admin/role" and method == "POST":
            if redirect := self.require_role(user, "admin", session):
                return self.redirect_response(redirect, session)
            return self.update_role(form, user, session)
        if path == "/admin/product" and method == "POST":
            if redirect := self.require_role(user, "admin", session):
                return self.redirect_response(redirect, session)
            return self.update_product(form, session)
        if path == "/admin/export.csv" and method == "GET":
            if redirect := self.require_role(user, "admin", session):
                return self.redirect_response(redirect, session)
            return self.export_report(session)
        if path == "/messages" and method == "GET":
            if redirect := self.require_user(user, session):
                return self.redirect_response(redirect, session)
            return self.messages_page(user, session)
        if path == "/messages/send" and method == "POST":
            if redirect := self.require_user(user, session):
                return self.redirect_response(redirect, session)
            if user["role"] not in ("agent", "admin"):
                session["flash"] = ("Only staff can send preview inbox messages.", "error")
                return self.redirect_response("/messages", session)
            return self.send_message(user, form, session)
        if path == "/messages/read" and method == "POST":
            if redirect := self.require_user(user, session):
                return self.redirect_response(redirect, session)
            self.database.run("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
            session["flash"] = ("Inbox marked as read.", "success")
            return self.redirect_response("/messages", session)
        if path == "/ussd" and method == "GET":
            return self.ussd_page(user, session)
        if path == "/health" and method == "GET":
            return "200 OK", "ok", [("Content-Type", "text/plain; charset=utf-8")]
        return self.page("Page not found", '<div class="empty-state"><div class="empty-icon">🌱</div><h2>Page not found</h2><p>That AgriCover page is not available.</p><a class="button primary" href="/dashboard">Go to dashboard</a></div>', user, session, "404 Not Found")

    def redirect_response(self, target: str, session: dict):
        return "303 See Other", "", [("Location", target)]

    def static(self, filename: str, content_type: str = "text/css; charset=utf-8"):
        file_path = ROOT / "static" / filename
        try:
            return "200 OK", file_path.read_bytes(), [("Content-Type", content_type), ("Cache-Control", "no-cache, max-age=0, must-revalidate")]
        except OSError:
            return "404 Not Found", b"", [("Content-Type", "text/plain")]

    def destination(self, role: str) -> str:
        return {"admin": "/admin", "agent": "/agent"}.get(role, "/farmer")

    def login_page(self, user, session):
        token = esc(session["csrf"])
        if os.environ.get("RENDER"):
            demo = '''<section class="card demo-credentials"><div class="eyebrow">PREVIEW ACCESS</div><p>Sample role accounts are available for the project walkthrough.</p><small>Ask the project owner for access details. Use fictional information only.</small></section>'''
        else:
            demo = '''<section class="card demo-credentials"><div class="eyebrow">PREVIEW ACCESS</div><p><b>Farmer</b> · 0700000003 · Farm#123</p><p><b>Field agent</b> · 0700000002 · Agent#12</p><p><b>Administrator</b> · 0700000001 · Admin#12</p><small>Shared local preview accounts · avoid entering real farmer information.</small></section>'''
        content = f'''<div class="auth-grid"><section class="hero-panel"><div class="eyebrow">GULU · NORTHERN UGANDA</div><h1>Resilience for every growing season.</h1><p>Explore farmer enrolment, cover requests, agent support and claim handling in AgriCover.</p><div class="hero-stats"><div><b>2</b><span>cover options</span></div><div><b>3</b><span>service roles</span></div><div><b>UGX</b><span>illustrative pricing</span></div></div></section><section class="card auth-card"><div class="eyebrow">WELCOME BACK</div><h2>Sign in to AgriCover</h2><p class="muted">Sign in with a preview account or create a farmer profile.</p><form method="post" action="/login" class="stack-form"><input type="hidden" name="csrf" value="{token}"><label>Phone number<input name="phone" type="tel" inputmode="tel" autocomplete="username" placeholder="0700000003" required></label><label>Password<input name="password" type="password" autocomplete="current-password" placeholder="Enter your password" required></label><button class="button primary full" type="submit">Sign in <span>→</span></button></form><p class="auth-foot">New to AgriCover? <a href="/signup">Create a farmer account</a></p></section></div>{demo}'''
        return self.page("Welcome", content, user, session)

    def signup_page(self, user, session):
        token = esc(session["csrf"])
        districts = ["Gulu", "Amuru", "Nwoya", "Omoro", "Kitgum", "Pader", "Agago", "Lira"]
        options = "".join(f'<option>{esc(name)}</option>' for name in districts)
        content = f'''<section class="card form-card narrow"><a class="back-link" href="/">← Back to sign in</a><div class="eyebrow">FARMER ENROLMENT</div><h1>Create your account</h1><p class="muted">Farmers can create their own profiles. Agent and administrator access is assigned by an administrator.</p><form method="post" action="/signup" class="stack-form"><input type="hidden" name="csrf" value="{token}"><label>Full name<input name="full_name" maxlength="80" autocomplete="name" required></label><label>Phone number<input name="phone" type="tel" inputmode="tel" maxlength="14" placeholder="07… or +256…" required></label><div class="form-row"><label>Village<input name="village" maxlength="80" required></label><label>District<select name="district" required><option value="">Choose district</option>{options}</select></label></div><label>Password<input name="password" type="password" minlength="8" autocomplete="new-password" required><small>Use at least 8 characters.</small></label><label class="check-row"><input type="checkbox" name="consent" value="yes" required><span>I agree that this learning preview may save the details above. I understand it is not a real insurance service.</span></label><button class="button primary full" type="submit">Create farmer account <span>→</span></button></form></section>'''
        return self.page("Create account", content, user, session)

    def login(self, form, session):
        phone = normalize_phone(form.get("phone", ""))
        person = self.database.one("SELECT * FROM users WHERE phone=?", (phone,))
        if not valid_phone(phone) or not person or not verify_password(form.get("password", ""), person["password_hash"]):
            session["flash"] = ("Phone number or password did not match.", "error")
            return self.redirect_response("/", session)
        session.clear()
        session.update({"uid": person["id"], "csrf": secrets.token_urlsafe(24), "flash": (f"Welcome, {person['full_name']}.", "success")})
        return self.redirect_response(self.destination(person["role"]), session)

    def signup(self, form, session):
        name = form.get("full_name", "").strip()
        phone = normalize_phone(form.get("phone", ""))
        village = form.get("village", "").strip()
        district = form.get("district", "").strip()
        password = form.get("password", "")
        if len(name) < 2 or len(name) > 80 or not valid_phone(phone) or not village or not district or len(password) < 8 or form.get("consent") != "yes":
            session["flash"] = ("Check your details, password length, and consent, then try again.", "error")
            return self.redirect_response("/signup", session)
        try:
            new_id = self.database.run("INSERT INTO users(full_name,phone,password_hash,village,district,role,consent) VALUES (?,?,?,?,?,'farmer',1)",
                                       (name, phone, hash_password(password), village, district))
            self.database.run("INSERT INTO notifications(user_id,title,body) VALUES (?,?,?)", (new_id, "Welcome to AgriCover", "Your farmer account is ready. Product details and payment steps are illustrative only."))
        except Exception:
            session["flash"] = ("That phone number is already registered. Sign in or use another number.", "error")
            return self.redirect_response("/signup", session)
        session.clear()
        session.update({"uid": new_id, "csrf": secrets.token_urlsafe(24), "flash": ("Farmer account created.", "success")})
        return self.redirect_response("/farmer", session)

    def farmer_dashboard(self, user, session):
        products = self.database.all("SELECT * FROM products WHERE active=1 ORDER BY id")
        policies = self.database.all("SELECT p.*,x.name AS product_name FROM policies p JOIN products x ON x.id=p.product_id WHERE p.farmer_id=? ORDER BY p.id DESC LIMIT 4", (user["id"],))
        notices = self.database.one("SELECT COUNT(*) AS count FROM notifications WHERE user_id=? AND is_read=0", (user["id"],))["count"]
        product_cards = []
        for product in products:
            product_cards.append(f'''<article class="product-card"><div class="product-icon">{'🌽' if product['code']=='crop' else '🐐'}</div><div class="eyebrow">PROPOSED COVER · ILLUSTRATIVE TERMS</div><h3>{esc(product['name'])}</h3><p>{esc(product['summary'])}</p><div class="price-row"><span>Illustrative premium</span><b>{money(product['premium_ugx'])}</b></div><small>per {esc(product['billing_unit'])} · insurer to confirm actual price and cover</small><form method="post" action="/policies/create" class="request-form"><input type="hidden" name="csrf" value="{esc(session['csrf'])}"><input type="hidden" name="product_id" value="{product['id']}"><label>Number of units<input type="number" name="units" min="1" max="10" value="1" required></label><button class="button primary full" type="submit">Request cover <span>→</span></button></form></article>''')
        policy_rows = self.policy_rows(policies, user, session)
        content = f'''<div class="page-heading"><div><div class="eyebrow">FARMER HOME · {esc(user['district']).upper()}</div><h1>Welcome, {esc(user['full_name'].split()[0])}</h1><p class="muted">Your farm, your plans, and a little more peace of mind.</p></div><a href="/messages" class="inbox-shortcut">✉ <span>{int(notices)} unread</span></a></div><div class="demo-banner"><span>✦</span><div><b>AgriCover Preview</b><p>Sample premiums only. No money moves and no cover becomes active without agent review.</p></div></div><div class="section-heading"><div><div class="eyebrow">START HERE</div><h2>Explore sample cover</h2></div></div><div class="product-grid">{''.join(product_cards)}</div><div class="section-heading"><div><div class="eyebrow">YOUR ACTIVITY</div><h2>Recent requests</h2></div><a href="/policies" class="text-link">View all →</a></div>{policy_rows}'''
        return self.page("Farmer dashboard", content, user, session)

    def policy_rows(self, rows, user, session):
        if not rows:
            return '<div class="empty-state"><div class="empty-icon">🌱</div><h3>No policy requests yet</h3><p>Choose one of the sample products above to follow the request and review process.</p></div>'
        pieces = []
        for row in rows:
            status_label = {"active_demo": "Preview active", "awaiting_payment": "Awaiting payment", "pending_review": "Awaiting review"}.get(row["status"], row["status"].replace("_", " ").title())
            pay_action = ""
            if row["status"] == "awaiting_payment":
                pay_action = f'<form method="post" action="/policies/{row["id"]}/simulate-payment"><input type="hidden" name="csrf" value="{esc(session["csrf"])}"><button class="button soft" type="submit">Simulate payment</button></form>'
            pieces.append(f'''<article class="activity-row"><div class="activity-icon">🧾</div><div class="activity-copy"><b>{esc(row['product_name'])}</b><p>{int(row['insured_units'])} unit(s) · {money(row['premium_ugx'])} · {esc(row['created_at'][:10])}</p><span class="status {esc(row['status'])}">{esc(status_label)}</span></div><div class="activity-action">{pay_action}</div></article>''')
        return '<div class="activity-list">' + "".join(pieces) + '</div>'

    def policies_page(self, user, session):
        if user["role"] != "farmer":
            return self.page("Policy requests", '<div class="empty-state"><div class="empty-icon">🧾</div><h2>Policy requests</h2><p>Use the agent desk to review farmer requests.</p><a class="button primary" href="/agent">Open agent desk</a></div>', user, session)
        rows = self.database.all("SELECT p.*,x.name AS product_name FROM policies p JOIN products x ON x.id=p.product_id WHERE p.farmer_id=? ORDER BY p.id DESC", (user["id"],))
        content = f'<div class="page-heading"><div><div class="eyebrow">FARMER ACCOUNT</div><h1>My policy requests</h1><p class="muted">Illustrative requests only; no record here represents active insurance cover.</p></div></div>{self.policy_rows(rows,user,session)}'
        return self.page("My requests", content, user, session)

    def create_policy(self, user, form, session):
        try:
            product_id, units = int(form.get("product_id", "")), int(form.get("units", "1"))
        except ValueError:
            product_id, units = 0, 0
        product = self.database.one("SELECT * FROM products WHERE id=? AND active=1", (product_id,))
        if not product or not 1 <= units <= 10:
            session["flash"] = ("Choose a listed cover option and 1–10 units.", "error")
            return self.redirect_response("/farmer", session)
        policy_id = self.database.run("INSERT INTO policies(farmer_id,product_id,insured_units,premium_ugx,status,payment_status) VALUES (?,?,?,?, 'awaiting_payment','not_started')",
                                      (user["id"], product_id, units, product["premium_ugx"] * units))
        session["flash"] = (f"Demo request #{policy_id} created. Continue with the payment simulation if you want to show the next step.", "success")
        return self.redirect_response("/policies", session)

    def simulate_payment(self, user, path, session):
        match = re.fullmatch(r"/policies/(\d+)/simulate-payment", path)
        if not match:
            return self.redirect_response("/policies", session)
        policy_id = int(match.group(1))
        policy = self.database.one("SELECT * FROM policies WHERE id=? AND farmer_id=?", (policy_id, user["id"]))
        if not policy or policy["status"] != "awaiting_payment":
            session["flash"] = ("That request cannot be changed from this screen.", "error")
            return self.redirect_response("/policies", session)
        ref = "DEMO-" + secrets.token_hex(3).upper()
        self.database.run("UPDATE policies SET payment_status='simulated',status='pending_review',demo_reference=? WHERE id=? AND farmer_id=?",
                          (ref, policy_id, user["id"]))
        self.database.run("INSERT INTO notifications(user_id,title,body) VALUES (?,?,?)", (user["id"], "Demo payment step complete", f"Reference {ref}: no funds moved. Your request is waiting for an agent. No insurance cover is active."))
        session["flash"] = (f"Demo payment recorded as a simulation ({ref}). No money moved and no cover is active.", "success")
        return self.redirect_response("/policies", session)

    def create_claim(self, user, form, session):
        try:
            policy_id = int(form.get("policy_id", ""))
        except ValueError:
            policy_id = 0
        description = form.get("description", "").strip()[:600]
        policy = self.database.one("SELECT id FROM policies WHERE id=? AND farmer_id=? AND status='active_demo'", (policy_id, user["id"]))
        if not policy or len(description) < 8:
            session["flash"] = ("Select a reviewed request and add a short description.", "error")
            return self.redirect_response("/claims", session)
        claim_id = self.database.run("INSERT INTO claims(farmer_id,policy_id,description,status) VALUES (?,?,?,'pending_review')", (user["id"], policy_id, description))
        session["flash"] = (f"Loss report #{claim_id} sent to the agent review queue. This is not a real claim.", "success")
        return self.redirect_response("/claims", session)

    def claims_page(self, user, session):
        policies = self.database.all("SELECT p.id,p.premium_ugx,x.name AS product_name FROM policies p JOIN products x ON x.id=p.product_id WHERE p.farmer_id=? AND p.status='active_demo' ORDER BY p.id DESC", (user["id"],))
        claims = self.database.all("SELECT c.*,x.name AS product_name FROM claims c JOIN policies p ON p.id=c.policy_id JOIN products x ON x.id=p.product_id WHERE c.farmer_id=? ORDER BY c.id DESC", (user["id"],))
        if policies:
            options = "".join(f'<option value="{p["id"]}">Request #{p["id"]} · {esc(p["product_name"])}</option>' for p in policies)
            form_html = f'''<section class="card"><div class="eyebrow">LOSS REPORT · SIMULATED</div><h2>Start a claim review</h2><p class="muted">An agent will review this sample report. Photo analysis and payouts are not connected.</p><form class="stack-form" method="post" action="/claims/create"><input type="hidden" name="csrf" value="{esc(session['csrf'])}"><label>Policy request<select name="policy_id" required><option value="">Choose request</option>{options}</select></label><label>What happened?<textarea name="description" rows="4" maxlength="600" minlength="8" required placeholder="Describe the crop or livestock loss"></textarea></label><button class="button primary" type="submit">Send for review</button></form></section>'''
        else:
            form_html = '<section class="card claim-empty"><div class="eyebrow">LOSS REPORT</div><h2>Start a claim review</h2><p class="muted">A reviewed policy request is needed before you can submit a loss report. Start a cover request or check the status of an existing one.</p><div class="claim-actions"><a class="button primary" href="/farmer">Explore cover</a><a class="button soft" href="/policies">View my requests</a></div></section>'
        if claims:
            claim_items = []
            for claim in claims:
                note_html = f'<small>Agent note: {esc(claim["review_note"])}</small>' if claim["review_note"] else ""
                claim_items.append(f'<article class="activity-row"><div class="activity-icon">🛟</div><div class="activity-copy"><b>{esc(claim["product_name"])}</b><p>Claim #{claim["id"]} · {esc(claim["created_at"][:10])}</p><span class="status {esc(claim["status"])}">{esc(claim["status"].replace("_", " "))}</span><p>{esc(claim["description"])}</p>{note_html}</div></article>')
            items = "".join(claim_items)
            history = '<div class="activity-list">' + items + '</div>'
        else:
            history = self.empty("No claim reports yet", "When you submit a report, it will appear here for agent review.")
        content = f'''<div class="page-heading"><div><div class="eyebrow">CLAIM SUPPORT · PREVIEW</div><h1>Loss reports</h1><p class="muted">Report a sample loss and follow its manual review status.</p></div><span class="large-icon">🛟</span></div><div class="claim-grid">{form_html}<section><div class="section-heading"><div><div class="eyebrow">YOUR HISTORY</div><h2>Recent reports</h2></div></div>{history}</section></div><div class="inline-alert">No real claim is being filed. This app does not assess images or send insurance payouts.</div>'''
        return self.page("Claims", content, user, session)

    def agent_dashboard(self, user, session):
        pending_policies = self.database.all("SELECT p.*,u.full_name,u.district,x.name AS product_name FROM policies p JOIN users u ON u.id=p.farmer_id JOIN products x ON x.id=p.product_id WHERE p.status='pending_review' ORDER BY p.id")
        pending_claims = self.database.all("SELECT c.*,u.full_name,u.district,x.name AS product_name FROM claims c JOIN users u ON u.id=c.farmer_id JOIN policies p ON p.id=c.policy_id JOIN products x ON x.id=p.product_id WHERE c.status='pending_review' ORDER BY c.id")
        p_cards = []
        for row in pending_policies:
            p_cards.append(f'''<article class="review-card"><div class="review-top"><div><div class="eyebrow">POLICY REQUEST #{row['id']}</div><h3>{esc(row['product_name'])}</h3></div><span class="status pending_review">Awaiting review</span></div><p class="muted">{esc(row['full_name'])} · {esc(row['district'])} · {int(row['insured_units'])} unit(s) · {money(row['premium_ugx'])}</p><p class="small-note">Payment step: simulated. No money moved. Approving activates a demo record only.</p>{self.review_form('policy',row['id'],session['csrf'])}</article>''')
        c_cards = []
        for row in pending_claims:
            c_cards.append(f'''<article class="review-card"><div class="review-top"><div><div class="eyebrow">CLAIM REQUEST #{row['id']}</div><h3>{esc(row['product_name'])}</h3></div><span class="status pending_review">Awaiting review</span></div><p class="muted">{esc(row['full_name'])} · {esc(row['district'])}</p><p class="claim-description">“{esc(row['description'])}”</p><p class="small-note">Prototype review is manual. No AI image analysis or payout is connected.</p>{self.review_form('claim',row['id'],session['csrf'])}</article>''')
        content = f'''<div class="page-heading"><div><div class="eyebrow">FIELD OPERATIONS</div><h1>Agent review desk</h1><p class="muted">Review farmer requests, record a decision, and leave a note for the farmer.</p><a href="/agent/clients#onboard" class="button primary onboard-cta">＋ Onboard a farmer</a></div><span class="large-icon">🧑🏽‍🌾</span></div><div class="demo-banner"><span>✓</span><div><b>Human review</b><p>All decisions update preview records only; no insurer or payment provider is contacted.</p></div></div><div class="review-columns"><section><div class="section-heading"><div><div class="eyebrow">ENROLMENT</div><h2>Policy requests <span class="count">{len(pending_policies)}</span></h2></div></div>{''.join(p_cards) if p_cards else self.empty("No policy requests waiting", "Farmer requests will appear here after the simulated payment step.")}</section><section><div class="section-heading"><div><div class="eyebrow">LOSS REPORTS</div><h2>Claim requests <span class="count">{len(pending_claims)}</span></h2></div></div>{''.join(c_cards) if c_cards else self.empty("No claims waiting", "Claims from reviewed policies will appear here.")}</section></div>'''
        return self.page("Agent review", content, user, session)

    def agent_clients_page(self, user, session):
        people = self.database.all("SELECT id,full_name,phone,village,district,created_at FROM users WHERE role='farmer' ORDER BY id DESC LIMIT 50")
        districts = ["Gulu", "Amuru", "Nwoya", "Omoro", "Kitgum", "Pader", "Agago", "Lira"]
        options = "".join(f'<option value="{esc(district)}">{esc(district)}</option>' for district in districts)
        form_html = f'''<section class="card onboard-card" id="onboard"><div class="eyebrow">FIELD ENROLMENT</div><h2>Onboard a farmer</h2><p class="muted">Complete this with the farmer present. Their consent is required; share the sign-in details in person. No SMS is sent.</p><form method="post" action="/agent/clients/create" class="stack-form"><input type="hidden" name="csrf" value="{esc(session['csrf'])}"><div class="form-row"><label>Farmer's full name<input name="full_name" maxlength="80" autocomplete="name" required></label><label>Phone number<input name="phone" type="tel" maxlength="14" inputmode="tel" placeholder="07… or +256…" required></label></div><div class="form-row"><label>Village<input name="village" maxlength="80" required></label><label>District<select name="district" required><option value="">Choose district</option>{options}</select></label></div><label>Temporary sign-in password<input name="password" type="password" minlength="8" autocomplete="new-password" required><small>Set this with the farmer present; ask them to keep it private.</small></label><label class="check-row"><input type="checkbox" name="consent" value="yes" required><span>The farmer agrees to have these details saved for this learning preview.</span></label><button class="button primary" type="submit">Create farmer profile <span>→</span></button></form></section>'''
        if people:
            rows = "".join(f'<tr><td><b>{esc(person["full_name"])}</b><small>{esc(person["phone"])}</small></td><td>{esc(person["village"])}</td><td>{esc(person["district"])}</td><td>{esc(person["created_at"][:10])}</td></tr>' for person in people)
            list_html = f'<div class="table-wrap"><table><thead><tr><th>Farmer</th><th>Village</th><th>District</th><th>Joined</th></tr></thead><tbody>{rows}</tbody></table></div>'
        else:
            list_html = self.empty("No farmer profiles yet", "Use the enrolment form to create the first farmer account.")
        content = f'''<div class="page-heading"><div><div class="eyebrow">FIELD OPERATIONS</div><h1>Farmer relationships</h1><p class="muted">Enrol farmers with consent, then help them access the service.</p></div><span class="large-icon">🤝</span></div><div class="clients-grid">{form_html}<section class="card"><div class="section-heading"><div><div class="eyebrow">CLIENT DIRECTORY</div><h2>Recent farmer profiles <span class="count">{len(people)}</span></h2></div></div>{list_html}</section></div><div class="security-note">Learning preview · farmer profiles and passwords are stored locally in this app. Do not enter real client information.</div>'''
        return self.page("Farmer relationships", content, user, session)

    def onboard_farmer(self, form, session):
        name = form.get("full_name", "").strip()
        phone = normalize_phone(form.get("phone", ""))
        village = form.get("village", "").strip()
        district = form.get("district", "").strip()
        password = form.get("password", "")
        if len(name) < 2 or len(name) > 80 or not valid_phone(phone) or not village or not district or len(password) < 8 or form.get("consent") != "yes":
            session["flash"] = ("Check the farmer details, consent and password, then try again.", "error")
            return self.redirect_response("/agent/clients#onboard", session)
        try:
            farmer_id = self.database.run("INSERT INTO users(full_name,phone,password_hash,village,district,role,consent) VALUES (?,?,?,?,?,'farmer',1)",
                                          (name, phone, hash_password(password), village, district))
            self.database.run("INSERT INTO notifications(user_id,title,body) VALUES (?,?,?)", (farmer_id, "Welcome to AgriCover", "Your farmer profile is ready. This is a learning preview; no real cover or payment is active."))
        except Exception:
            session["flash"] = ("That phone number is already registered. Check the number or open the existing farmer profile.", "error")
            return self.redirect_response("/agent/clients#onboard", session)
        session["flash"] = (f"Farmer profile created for {name}. Share the sign-in details with them in person; no SMS was sent.", "success")
        return self.redirect_response("/agent/clients", session)

    def review_form(self, kind, item_id, token):
        return f'''<form method="post" action="/agent/{kind}/{item_id}" class="review-form"><input type="hidden" name="csrf" value="{esc(token)}"><label>Review note<input name="note" maxlength="240" placeholder="Add a short reason or follow-up"></label><div class="button-row"><button class="button primary" name="decision" value="approve">Approve</button><button class="button danger" name="decision" value="reject">Decline</button></div></form>'''

    def review_policy(self, agent, path, form, session):
        match = re.fullmatch(r"/agent/policy/(\d+)", path)
        if not match:
            return self.redirect_response("/agent", session)
        item_id, decision = int(match.group(1)), form.get("decision")
        row = self.database.one("SELECT farmer_id FROM policies WHERE id=? AND status='pending_review'", (item_id,))
        if decision not in ("approve", "reject") or not row:
            session["flash"] = ("That policy request is no longer waiting for review.", "error")
            return self.redirect_response("/agent", session)
        next_status = "active_demo" if decision == "approve" else "rejected"
        note = form.get("note", "").strip()[:240] or ("Approved in the preview workflow." if decision == "approve" else "Please contact the agent for details.")
        self.database.run("UPDATE policies SET status=? WHERE id=?", (next_status, item_id))
        self.database.run("INSERT INTO notifications(user_id,title,body) VALUES (?,?,?)", (row["farmer_id"], "Demo policy request updated", f"Request #{item_id} was {decision}d in the preview workflow. {note} This is not a real insurance policy."))
        session["flash"] = ("Policy request decision saved.", "success")
        return self.redirect_response("/agent", session)

    def review_claim(self, agent, path, form, session):
        match = re.fullmatch(r"/agent/claim/(\d+)", path)
        if not match:
            return self.redirect_response("/agent", session)
        item_id, decision = int(match.group(1)), form.get("decision")
        row = self.database.one("SELECT farmer_id FROM claims WHERE id=? AND status='pending_review'", (item_id,))
        if decision not in ("approve", "reject") or not row:
            session["flash"] = ("That claim is no longer waiting for review.", "error")
            return self.redirect_response("/agent", session)
        next_status = "approved_demo" if decision == "approve" else "declined"
        note = form.get("note", "").strip()[:240] or "Reviewed in the preview workflow; no payout is connected."
        self.database.run("UPDATE claims SET status=?,review_note=? WHERE id=?", (next_status, note, item_id))
        self.database.run("INSERT INTO notifications(user_id,title,body) VALUES (?,?,?)", (row["farmer_id"], "Demo claim review completed", f"Claim #{item_id} was marked {decision}d in the preview workflow. No payment or insurer decision is represented."))
        session["flash"] = ("Claim review saved as a demo decision.", "success")
        return self.redirect_response("/agent", session)

    def admin_dashboard(self, user, session):
        counts = {table: self.database.one(f"SELECT COUNT(*) AS n FROM {table}")["n"] for table in ("users", "policies", "claims", "notifications")}
        people = self.database.all("SELECT id,full_name,phone,district,role,created_at FROM users ORDER BY role,full_name")
        rows = []
        for person in people:
            if person["id"] == user["id"]:
                role_control = f'<span class="status active_demo">{esc(person["role"])}</span>'
            else:
                role_control = f'''<form method="post" action="/admin/role" class="role-form"><input type="hidden" name="csrf" value="{esc(session['csrf'])}"><input type="hidden" name="user_id" value="{person['id']}"><select name="role" aria-label="Role for {esc(person['full_name'])}"><option value="farmer" {'selected' if person['role']=='farmer' else ''}>Farmer</option><option value="agent" {'selected' if person['role']=='agent' else ''}>Agent</option><option value="admin" {'selected' if person['role']=='admin' else ''}>Admin</option></select><button class="button soft" type="submit">Save</button></form>'''
            rows.append(f'<tr><td><b>{esc(person["full_name"])}</b><small>{esc(person["phone"])}</small></td><td>{esc(person["district"])}</td><td>{role_control}</td></tr>')
        content = f'''<div class="page-heading"><div><div class="eyebrow">SYSTEM OVERVIEW</div><h1>Admin dashboard</h1><p class="muted">Manage users, product listings, reports and in-app messages.</p></div><span class="large-icon">📊</span></div><div class="metric-grid">{self.metric('People',counts['users'],'👥')}{self.metric('Policy requests',counts['policies'],'🧾')}{self.metric('Claim reports',counts['claims'],'🛟')}{self.metric('Inbox notes',counts['notifications'],'✉')}</div><div class="admin-grid">{card('Send an in-app message',f'''<p class="muted">Use the in-app inbox as a free alternative to paid SMS for this preview. It only appears inside this website.</p><form method="post" action="/messages/send" class="stack-form"><input type="hidden" name="csrf" value="{esc(session['csrf'])}"><label>Send to<select name="recipient"><option value="all">All farmers</option>{''.join(f'<option value="{p["id"]}">{esc(p["full_name"])} · {esc(p["district"])}</option>' for p in people if p['role']=='farmer')}</select></label><label>Title<input name="title" maxlength="80" required placeholder="Season reminder"></label><label>Message<textarea name="body" maxlength="400" rows="3" required placeholder="Write a short update"></textarea></label><button class="button primary" type="submit">Send to inbox</button></form>''')}{card('People and roles',f'<div class="table-wrap"><table><thead><tr><th>Account</th><th>District</th><th>Role</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')}</div><div class="security-note">Administrator workspace · role changes are stored in this app database. Do not reuse the seeded credentials or enter real customer information.</div>'''
        content += self.admin_management_panel(session)
        return self.page("Admin overview", content, user, session)

    def metric(self, label, value, icon):
        return f'<article class="metric-card"><span>{icon}</span><div><b>{int(value)}</b><small>{esc(label)}</small></div></article>'

    def update_role(self, form, admin, session):
        try:
            user_id = int(form.get("user_id", ""))
        except ValueError:
            user_id = 0
        role = form.get("role", "")
        if user_id == admin["id"] or role not in ("farmer", "agent", "admin"):
            session["flash"] = ("Choose a valid role for another account.", "error")
            return self.redirect_response("/admin", session)
        self.database.run("UPDATE users SET role=? WHERE id=?", (role, user_id))
        session["flash"] = ("Account role updated.", "success")
        return self.redirect_response("/admin", session)

    def admin_management_panel(self, session):
        products = self.database.all("SELECT * FROM products ORDER BY id")
        product_rows = []
        for product in products:
            product_rows.append(f'''<article class="product-admin-row"><div><b>{esc(product['name'])}</b><small>{esc(product['billing_unit'])}</small></div><form method="post" action="/admin/product" class="product-admin-form"><input type="hidden" name="csrf" value="{esc(session['csrf'])}"><input type="hidden" name="product_id" value="{product['id']}"><label>Sample price<input type="number" name="premium" min="1000" max="1000000" step="1000" value="{int(product['premium_ugx'])}" required></label><label>Listing<select name="active"><option value="1" {'selected' if product['active'] else ''}>Visible</option><option value="0" {'selected' if not product['active'] else ''}>Hidden</option></select></label><button class="button soft" type="submit">Save</button></form></article>''')
        districts = self.database.all("SELECT district,COUNT(*) AS farmers FROM users WHERE role='farmer' GROUP BY district ORDER BY farmers DESC,district")
        district_rows = "".join(f'<div class="district-row"><span>{esc(item["district"])}</span><div class="bar-track"><i style="width:{max(5, min(100, int(item["farmers"])*16))}%"></i></div><b>{int(item["farmers"])}</b></div>' for item in districts) or '<p class="muted">Farmer locations will appear here.</p>'
        policy_states = self.database.all("SELECT status,COUNT(*) AS total FROM policies GROUP BY status ORDER BY total DESC")
        claim_states = self.database.all("SELECT status,COUNT(*) AS total FROM claims GROUP BY status ORDER BY total DESC")
        states = "".join(f'<span class="status {esc(item["status"])}">{esc(item["status"].replace("_"," "))} · {int(item["total"])}</span>' for item in policy_states + claim_states) or '<span class="muted">No activity recorded yet.</span>'
        return f'''<div class="admin-feature-grid"><section class="card"><div class="eyebrow">CATALOGUE CONTROL</div><h2>Product listings</h2><p class="muted">Edit sample prices and hide or show products. These are preview values, not approved premiums.</p>{''.join(product_rows)}</section><section class="card"><div class="eyebrow">PORTFOLIO SNAPSHOT</div><h2>Farmer reach</h2>{district_rows}<div class="section-heading compact"><div><div class="eyebrow">WORKFLOW STATUS</div><h2>Requests and claims</h2></div></div><div class="status-wrap">{states}</div><a class="button primary export-button" href="/admin/export.csv">Download activity report <span>↓</span></a></section></div>'''

    def update_product(self, form, session):
        try:
            product_id, premium, active = int(form.get("product_id", "")), int(form.get("premium", "")), int(form.get("active", ""))
        except ValueError:
            product_id, premium, active = 0, 0, -1
        if not self.database.one("SELECT id FROM products WHERE id=?", (product_id,)) or not 1000 <= premium <= 1_000_000 or active not in (0, 1):
            session["flash"] = ("Choose a valid sample price and listing status.", "error")
            return self.redirect_response("/admin", session)
        self.database.run("UPDATE products SET premium_ugx=?,active=? WHERE id=?", (premium, active, product_id))
        session["flash"] = ("Product listing updated.", "success")
        return self.redirect_response("/admin", session)

    def export_report(self, session):
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["type", "reference", "farmer", "phone", "district", "details", "status", "amount_ugx", "created_at"])
        policies = self.database.all("SELECT p.id,u.full_name,u.phone,u.district,x.name,p.insured_units,p.status,p.premium_ugx,p.created_at FROM policies p JOIN users u ON u.id=p.farmer_id JOIN products x ON x.id=p.product_id ORDER BY p.id")
        for row in policies:
            writer.writerow(["policy", row["id"], row["full_name"], row["phone"], row["district"], f'{row["name"]} ({row["insured_units"]} units)', row["status"], row["premium_ugx"], row["created_at"]])
        claims = self.database.all("SELECT c.id,u.full_name,u.phone,u.district,x.name,c.description,c.status,p.premium_ugx,c.created_at FROM claims c JOIN users u ON u.id=c.farmer_id JOIN policies p ON p.id=c.policy_id JOIN products x ON x.id=p.product_id ORDER BY c.id")
        for row in claims:
            writer.writerow(["claim", row["id"], row["full_name"], row["phone"], row["district"], f'{row["name"]}: {row["description"]}', row["status"], "", row["created_at"]])
        return "200 OK", output.getvalue(), [("Content-Type", "text/csv; charset=utf-8"), ("Content-Disposition", 'attachment; filename="agricover-activity-report.csv"')]

    def messages_page(self, user, session):
        messages = self.database.all("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 40", (user["id"],))
        if messages:
            items = "".join(f'<article class="message-card {"unread" if not m["is_read"] else ""}"><div class="message-head"><h3>{esc(m["title"])}</h3><time>{esc(m["created_at"][:16])}</time></div><p>{esc(m["body"])}</p>{"<span class=\"unread-tag\">NEW</span>" if not m["is_read"] else ""}</article>' for m in messages)
        else:
            items = self.empty("Your inbox is quiet", "AgriCover messages will appear here.")
        mark = button("Mark all as read", "/messages/read", session["csrf"], kind="soft") if any(not m["is_read"] for m in messages) else ""
        compose = ""
        if user["role"] in ("agent", "admin"):
            farmers = self.database.all("SELECT id,full_name,district FROM users WHERE role='farmer' ORDER BY full_name")
            options = "".join(f'<option value="{p["id"]}">{esc(p["full_name"])} · {esc(p["district"])}</option>' for p in farmers)
            compose = f'''<section class="card message-compose"><div class="eyebrow">SEND AN UPDATE</div><h2>Write a message</h2><p class="muted">It will appear in the farmer's AgriCover messages.</p><form method="post" action="/messages/send" class="stack-form"><input type="hidden" name="csrf" value="{esc(session['csrf'])}"><label>Send to<select name="recipient"><option value="all">All farmers</option>{options}</select></label><label>Subject<input name="title" maxlength="80" placeholder="Season update" required></label><label>Message<textarea name="body" maxlength="400" rows="4" placeholder="Write a useful update" required></textarea></label><button class="button primary" type="submit">Send message</button></form><small class="compose-note">In-app only. No SMS is sent.</small></section>'''
        content = f'<div class="page-heading"><div><div class="eyebrow">MESSAGES</div><h1>Messages</h1><p class="muted">Updates from AgriCover, collected in one place.</p></div>{mark}</div><div class="inline-alert message-note">Farmers receive these inside the app. This does not send a text message.</div><div class="message-page-grid">{compose}<section class="message-list">{items}</section></div>'
        return self.page("Inbox", content, user, session)

    def send_message(self, sender, form, session):
        title, body, recipient = form.get("title", "").strip()[:80], form.get("body", "").strip()[:400], form.get("recipient", "all")
        if len(title) < 2 or len(body) < 3:
            session["flash"] = ("Add a title and message before sending.", "error")
            return self.redirect_response("/admin" if sender["role"] == "admin" else "/messages", session)
        if recipient == "all":
            people = self.database.all("SELECT id FROM users WHERE role='farmer'")
        else:
            person = self.database.one("SELECT id FROM users WHERE id=? AND role='farmer'", (recipient,))
            people = [person] if person else []
        self.database.run_many("INSERT INTO notifications(user_id,title,body) VALUES (?,?,?)", [(p["id"], title, body) for p in people])
        session["flash"] = (f"Message added to {len(people)} farmer inbox(es). No SMS was sent.", "success")
        return self.redirect_response("/admin" if sender["role"] == "admin" else "/messages", session)

    def ussd_page(self, user, session):
        content = '''<div class="page-heading"><div><div class="eyebrow">BASIC-PHONE ACCESS</div><h1>USSD menu</h1><p class="muted">Try the menu below to see how a short-code service might work.</p></div><span class="large-icon">📟</span></div><div class="ussd-layout"><section class="ussd-phone"><div class="ussd-status"><span>AgriCover</span><span>Menu preview</span></div><div class="ussd-screen"><div class="ussd-brand">AgriCover</div><div id="ussdText" aria-live="polite"></div></div><form id="ussdForm" class="ussd-input"><label for="ussdChoice">Enter a number from the menu</label><div><input id="ussdChoice" inputmode="numeric" maxlength="1" placeholder="1" autocomplete="off"><button class="button primary" type="submit">Continue</button></div><p id="ussdError" class="field-error" role="status"></p></form><button class="button ghost full" id="ussdRestart" type="button">Start again</button></section><aside class="card ussd-aside"><h2>How to use it</h2><p>Choose an option on the screen and enter its number. This is a local interaction only: it does not dial a short code or connect to MTN or Airtel.</p><div class="inline-alert">No phone number, PIN, or payment is requested.</div></aside></div>'''
        return self.page("USSD simulator", content, user, session)

    def empty(self, title, text):
        return f'<div class="empty-state"><div class="empty-icon">🌾</div><h3>{esc(title)}</h3><p>{esc(text)}</p></div>'


application = AgriCoverApplication(db)


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


if __name__ == "__main__":
    on_render = bool(os.environ.get("RENDER"))
    configured_port = int(os.environ["PORT"]) if on_render else int(os.environ.get("LOCAL_PORT", "5501"))
    # Locally, keep AgriCover separate from the Campus Market Hub app on 5500.
    # Render supplies PORT and requires that exact port, so do not scan there.
    ports = [configured_port] if on_render or "LOCAL_PORT" in os.environ else range(configured_port, configured_port + 10)
    bind_host = "0.0.0.0" if on_render else "127.0.0.1"
    server = None
    last_error = None
    for port in ports:
        try:
            server = make_server(bind_host, port, application, server_class=ThreadedWSGIServer)
            break
        except OSError as error:
            last_error = error
            if getattr(error, "winerror", None) != 10048 and getattr(error, "errno", None) not in (98, 10048):
                raise
    if server is None:
        raise SystemExit(f"Could not start AgriCover on ports {configured_port}-{configured_port + 9}: {last_error}")
    address = f"http://127.0.0.1:{server.server_port}"
    print(f"AgriCover Preview running at {address}", flush=True)
    print("Your browser should open automatically. Keep this window open while using AgriCover. Press Ctrl+C to stop.", flush=True)
    if not on_render:
        threading.Timer(1.0, webbrowser.open, args=(address,)).start()
    with server:
        server.serve_forever()
