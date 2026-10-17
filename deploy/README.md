# Deploying Smart Inbox on a VM (Google Cloud, or any Ubuntu server)

You need: a VM (Ubuntu 22.04, 2 vCPU / 4 GB RAM, ports 80 and 443 open, a static IP), a domain name pointing at it
(a free `yourname.duckdns.org` works), and the Google client ID/secret from Google Cloud.

## 1. Point a domain at the VM
On duckdns.org create a name (e.g. `myinbox`) and set its IP to the VM's static external IP.

## 2. Google Cloud (once)
- **Clients > your Web client > Authorized redirect URIs**: add exactly `https://myinbox.duckdns.org/auth/callback`
- **Audience**: app is in *Testing*, your Gmail is a *test user*.  **Data Access**: scope `gmail.modify`.
- **APIs & Services > Library > Gmail API > Enable**.

## 3. On the VM (browser SSH button is fine)
```bash
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER   # then log out and back in
git clone <your-repo-url> smart-inbox && cd smart-inbox && git checkout claude/new-session-apraa9
cd deploy
# Tablet/no paste? In Google Cloud download the OAuth client as JSON, press UPLOAD FILE in the SSH window,
# and configure.sh reads the Client ID and secret from it. Otherwise it asks for them.
./configure.sh           # asks 4 questions (domain, Gmail, Client ID, Client secret) and writes .env;
                         # it also generates TOKEN_KEY + SESSION_SECRET on this machine
docker compose up -d --build        # first build takes several minutes
docker compose exec backend python -m app.doctor --model     # plain-language check
```
Open `https://myinbox.duckdns.org`, press **Sign in with Google** (Google warns the app is unverified: *Advanced > Go to ...*),
then run `docker compose exec backend python -m app.doctor --live`.

## First-run checklist (tell me which step fails and paste the doctor output, never the secrets)
1. `doctor --model` shows no red lines.
2. The site opens over https and shows the sign-in page.
3. Sign in completes and you land on the dashboard.
4. `doctor --live` says Gmail works.
5. **Sync now**: the progress bar moves, then the counts appear.
6. After a few minutes the **Topic map** shows clusters.
7. Open one mail; trash one mail and press Undo.
Keep `ALLOW_PERMANENT_DELETE=false` until you have looked at your real clusters.

## Everyday
- Logs: `docker compose logs -f backend`
- Update: `git pull && docker compose up -d --build`
- Backup: `./backup.sh`
- Stop: `docker compose down` (your data stays; add `-v` only if you want to erase it)
- In Google's *Testing* mode the sign-in expires every 7 days; just sign in again.

## Safety
Only the account in `OWNER_EMAIL` can sign in. The Google token is stored encrypted with `TOKEN_KEY`. The backend is not
exposed to the internet, only Caddy is (ports 80/443). Keep `.env` private (`chmod 600`, already done by `init-env.sh`).
