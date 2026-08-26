import os, uuid, requests, base64
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, session
from dotenv import load_dotenv
from functools import wraps

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "changeme")

JELLYFIN_URL = os.getenv("JELLYFIN_URL")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

invites = {}

def jf_headers():
    return {"Authorization": f'MediaBrowser Token={JELLYFIN_API_KEY}', "Content-Type": "application/json"}

def login_required(f):
    @wraps(f)
    def decorated(*a, **kw):
        if not session.get("logged_in"):
            return redirect("/admin/login")
        return f(*a, **kw)
    return decorated

def notify_discord(username):
    if not DISCORD_WEBHOOK:
        return
    requests.post(DISCORD_WEBHOOK, json={"content": f"🎬 Nouveau compte Jellyfin créé : **{username}**"})

def apply_policy(user_id, expire_days=None):
    policy = {
        "EnableAllFolders": False,
        "EnabledFolders": os.getenv("ALLOWED_LIBRARY_IDS", "").split(","),
        "IsAdministrator": False,
        "EnableRemoteAccess": True
    }
    requests.post(f"{JELLYFIN_URL}/Users/{user_id}/Policy", headers=jf_headers(), json=policy)

def set_avatar(user_id, image_bytes, content_type):
    headers = {
        "Authorization": f'MediaBrowser Token={JELLYFIN_API_KEY}',
        "Content-Type": content_type
    }
    b64_data = base64.b64encode(image_bytes)
    try:
        requests.post(f"{JELLYFIN_URL}/Users/{user_id}/Images/Primary", headers=headers, data=b64_data)
    except requests.exceptions.RequestException:
        pass

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect("/admin")
        return render_template("admin_login.html", error="Mot de passe incorrect")
    return render_template("admin_login.html")

@app.route("/admin")
@login_required
def admin():
    return render_template("admin.html", invites=invites, new_link=None)

@app.route("/admin/create", methods=["POST"])
@login_required
def admin_create():
    token = str(uuid.uuid4())[:8]
    duration_hours = int(request.form.get("duration_hours", 24))
    max_uses = int(request.form.get("max_uses", 1))
    expire_days = request.form.get("expire_days") or None

    invites[token] = {
        "used_count": 0,
        "max_uses": max_uses,
        "expires_at": datetime.now() + timedelta(hours=duration_hours),
        "account_expire_days": int(expire_days) if expire_days else None
    }

    full_link = request.host_url + "invite/" + token
    return render_template("admin.html", invites=invites, new_link=full_link)

def invite_valid(token):
    inv = invites.get(token)
    if not inv:
        return False
    if datetime.now() > inv["expires_at"]:
        return False
    if inv["used_count"] >= inv["max_uses"]:
        return False
    return True

@app.route("/invite/<token>", methods=["GET"])
def show_invite(token):
    if not invite_valid(token):
        return render_template("invite.html", error="Lien invalide, expiré ou déjà utilisé.")
    return render_template("invite.html", token=token)

@app.route("/invite/<token>", methods=["POST"])
def process_invite(token):
    if not invite_valid(token):
        return render_template("invite.html", error="Lien invalide, expiré ou déjà utilisé.")

    username = request.form.get("username")
    password = request.form.get("password")

    res = requests.post(f"{JELLYFIN_URL}/Users/New", headers=jf_headers(),
                         json={"Name": username, "Password": password})

    if res.status_code == 200:
        user_id = res.json()["Id"]
        inv = invites[token]
        apply_policy(user_id, inv["account_expire_days"])

        avatar_file = request.files.get("avatar")
        if avatar_file and avatar_file.filename:
            image_bytes = avatar_file.read()
            content_type = avatar_file.content_type or "image/png"
            set_avatar(user_id, image_bytes, content_type)

        inv["used_count"] += 1
        notify_discord(username)
        return render_template("success.html", username=username)

    return render_template("invite.html", token=token, error=f"Erreur : {res.text}")

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
