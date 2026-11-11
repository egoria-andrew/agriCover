# AgriCover Preview

AgriCover is a polished, mobile-friendly learning prototype for a proposed farmer insurance service in Northern Uganda. It uses Python's standard library, SQLite, HTML, CSS, and JavaScript. No Node.js, npm, or pip packages are required.

## Start on Windows

1. Open this folder in File Explorer.
2. Double-click `start-demo.bat`.
3. Keep the server window open. The browser opens at the address shown, normally **http://127.0.0.1:5501**. Port 5501 keeps AgriCover separate from Campus Market Hub on port 5500. If needed, the launcher tries ports 5502–5510.
4. Press Ctrl+C in the server window to stop the app.

You can also open PowerShell in this folder and run `py app.py`.

## Preview accounts

| Role | Phone | Password |
| --- | --- | --- |
| Farmer | `0700000003` | `Farm#123` |
| Field agent | `0700000002` | `Agent#12` |
| Administrator | `0700000001` | `Admin#12` |

These local preview passwords are 8 characters long. They are for walkthroughs only. Do not enter real client details or reuse these credentials elsewhere. On restart, the app updates these three seeded accounts in the local SQLite file to match the defaults above.

## Create an agent or administrator account

1. Create the person's account through **Join AgriCover** or let an agent onboard them. New accounts start as farmers.
2. Sign in with the administrator account above and open **Overview**.
3. In **People and roles**, find the account, choose **Agent** or **Admin**, and press **Save**.

There is one seeded administrator account. Use it to promote another account if you want a second administrator. Agents can onboard farmers, but only an administrator can change account roles.

## A quick walkthrough

1. Sign in as the farmer and request one of the sample cover options.
2. Open **Policies** and choose **Simulate payment**. No charge is made; the request moves to agent review.
3. Sign in as the field agent. Use **＋ Onboard a farmer** to create a client profile with their consent, or review the pending request.
4. Return to the farmer account, submit a sample loss report, then review it as the agent.
5. Use **Inbox** for in-app messages. Administrators can send a message to one farmer or all farmers.
6. Open the USSD simulator to explore a basic-phone menu flow without connecting to a mobile network.
7. As administrator, manage account roles, update or hide cover listings, review district reach and workflow totals, and download an activity report.

## Included

- Farmer sign-up and sign-in, with hashed passwords, signed sessions, CSRF protection, and role-based pages.
- SQLite records for farmer profiles, sample products, cover requests, loss reports, and in-app notifications.
- Agent client onboarding with consent, farmer directory, manual request and claim review, and review notes.
- Administrator controls for roles, product prices and visibility, activity summary, and CSV report download.
- Mobile-first pages with a compact header menu and agent onboarding shortcut.
- Interactive browser-based USSD menu simulator and seeded preview accounts.

## Boundaries

This is an educational prototype, not a real insurance service. Product terms and prices are illustrative and require insurer agreement. Payments are simulated; there is no active cover, underwriting, insurer decision, payout, or regulator approval. The app does not send SMS, contact MTN/Airtel, or connect to a live USSD short code. It also has no AI/image assessment, photo upload, production account recovery, or production security audit. The inbox is the free in-app alternative to paid SMS APIs.

Use fictional information only. The seeded database is for walkthroughs and may reset on free hosting.

## Free Render hosting

1. Put the contents of this folder in a **private GitHub repository**. Keep `instance/` and any local database out of it.
2. In Render choose **New → Blueprint**, connect that repository, and apply `render.yaml`.
3. Render creates the `agricover-preview` web service and generates its signing key and three preview account passwords. Find them in the service's **Environment** settings. To use shorter passwords, replace `FARMER_PASSWORD`, `AGENT_PASSWORD`, and `ADMIN_PASSWORD` there with your own values (at least 8 characters), save, and deploy. The hosted sign-in screen does not publish the passwords.
4. When the deploy finishes, open the `onrender.com` address shown in the service overview.

Render's free web services sleep after 15 minutes without traffic and can take around a minute to wake. The free plan has an ephemeral filesystem, and it does not support persistent disks, so the SQLite data and any changes can be lost after a restart, redeploy, or spin-down. This is suitable for a temporary walkthrough, not ongoing farmer records. Use fictional information only.

## How it is organized

- `app.py` contains the request routes, role checks, and page rendering.
- `database.py` sets up SQLite and seeds preview accounts and products.
- `security.py` handles password hashing and signed sessions.
- `static/style.css` contains the responsive design.
- `static/app.js` powers the USSD menu simulation.
- `render.yaml` describes the free-host setup.

The included Python WSGI server keeps setup simple for a local walkthrough. It is not a production deployment architecture.
