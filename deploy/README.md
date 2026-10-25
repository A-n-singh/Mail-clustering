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
git clone <your-repo-url> smart-inbox && cd smart-inbox        # the default branch, main, is the one to deploy
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
5. **Sync now**: the progress bar moves, and groups and topics start to appear and grow while the first load is still running (25 mails per batch, newest 1,000 mails kept).
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

## If a sync fails with "rateLimitExceeded"
Gmail limits how fast one account can be read. The app now paces its calls (`GMAIL_RATE_PER_SEC`, default 8 per second),
waits and retries when Gmail says "slow down", and a first sync that stops resumes where it stopped. Just press **Sync now** again.

## Stopping a sync
Press **Stop** (next to "Syncing…") at any time. Mails already read are kept and grouped; the next **Sync now** continues
where it stopped. Stopping also skips the long topic build that normally follows a sync.

## Optional heading model (not in the Docker image)
Topic headings work without any model: mixed topics are named by their most typical real subject. The optional local heading model (Qwen2.5-1.5B-Instruct, about
1.1 GB, plus `llama-cpp-python`) is **not installed in the backend image**, so on this deployment `TITLE_MODEL_PATH` stays empty. To use it you would add
`requirements-naming.txt` to the image, put the `.gguf` file on the `data` volume and set `TITLE_MODEL_PATH`; it needs roughly 2 GB of free memory (an estimate, not measured on the server) while topics are being
named. `docker compose exec backend python -m app.doctor` shows the status of the heading model and of the stored vectors.

## Changing the embedding model
Set `EMBED_MODEL` in `.env` and restart. The next sync recomputes every vector and rebuilds the topics (a few minutes). The new model is downloaded on first use (the server needs internet
then); the Dockerfile bakes in `BAAI/bge-small-en-v1.5` only.
