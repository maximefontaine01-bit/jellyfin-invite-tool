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
JELLYSEERR_URL = os.getenv("JELLYSEERR_URL")
JELLYSEERR_API_KEY = os.getenv("JELLYSEERR_API_KEY")

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

def notify_discord(username, email=None):
    if not DISCORD_WEBHOOK:
        return
    msg = f"🎬 Nouveau compte Jellyfin créé : **{username}**"
    if email:
        msg += f" ({email})"
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=5)
    except requests.exceptions.RequestException:
        pass

def apply_policy(user_id, expire_days=None):
    policy = {
        "EnableAllFolders": False,
        "EnabledFolders": os.getenv("ALLOWED_LIBRARY_IDS", "").split(","),
        "IsAdministrator": False,
        "EnableRemoteAccess": True
    }
    try:
        requests.post(f"{JELLYFIN_URL}/Users/{user_id}/Policy", headers=jf_headers(), json=policy, timeout=5)
    except requests.exceptions.RequestException:
        pass

def set_avatar_from_bytes(user_id, image_bytes, content_type):
    headers = {
        "Authorization": f'MediaBrowser Token={JELLYFIN_API_KEY}',
        "Content-Type": content_type
    }
    b64_data = base64.b64encode(image_bytes)
    try:
        requests.post(f"{JELLYFIN_URL}/Users/{user_id}/Images/Primary", headers=headers, data=b64_data, timeout=8)
    except requests.exceptions.RequestException:
        pass

def import_user_to_jellyseerr(jellyfin_user_id):
    try:
        res = requests.post(
            f"{JELLYSEERR_URL}/api/v1/user/import-from-jellyfin",
            headers={"X-Api-Key": JELLYSEERR_API_KEY, "Content-Type": "application/json"},
            json={"jellyfinUserIds": [jellyfin_user_id]},
            timeout=8
        )
        print(f"[SEERR IMPORT] status={res.status_code} body={res.text}")
        if res.status_code == 200:
            results = res.json()
            if isinstance(results, list) and results:
                return results[0].get("id")
    except requests.exceptions.RequestException as e:
        print(f"[SEERR IMPORT ERROR] {e}")
    return None

def set_jellyseerr_email(jellyseerr_user_id, email):
    try:
        r1 = requests.put(
            f"{JELLYSEERR_URL}/api/v1/user/{jellyseerr_user_id}",
            headers={"X-Api-Key": JELLYSEERR_API_KEY, "Content-Type": "application/json"},
            json={"email": email},
            timeout=5
        )
        print(f"[SEERR EMAIL] status={r1.status_code} body={r1.text}")

        r2 = requests.post(
            f"{JELLYSEERR_URL}/api/v1/user/{jellyseerr_user_id}/settings/notifications",
            headers={"X-Api-Key": JELLYSEERR_API_KEY, "Content-Type": "application/json"},
            json={"notificationTypes": {"email": 1}},
            timeout=5
        )
        print(f"[SEERR NOTIF] status={r2.status_code} body={r2.text}")
    except requests.exceptions.RequestException as e:
        print(f"[SEERR EMAIL ERROR] {e}")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect("/admin")
        return render_template("admin_login.html", error="Mot de passe incorrect")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("logged_in", None)
    return redirect("/admin/login")

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
    email = request.form.get("email", "").strip()

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
            set_avatar_from_bytes(user_id, image_bytes, content_type)

        if email and JELLYSEERR_URL and JELLYSEERR_API_KEY:
            jellyseerr_id = import_user_to_jellyseerr(user_id)
            if jellyseerr_id:
                set_jellyseerr_email(jellyseerr_id, email)

        inv["used_count"] += 1
        notify_discord(username, email)
        return render_template("success.html", username=username)

    return render_template("invite.html", token=token, error=f"Erreur : {res.text}")

@app.route("/admin/stats")
@login_required
def admin_stats():
    stats = {
        "jellyfin_users": None, "active_sessions": [], "libraries": [],
        "jellyseerr": None, "jellyseerr_error": None, "jellyfin_error": None
    }
    try:
        users_res = requests.get(f"{JELLYFIN_URL}/Users", headers=jf_headers(), timeout=5)
        stats["jellyfin_users"] = len(users_res.json())

        sessions_res = requests.get(f"{JELLYFIN_URL}/Sessions", headers=jf_headers(), timeout=5)
        sessions = sessions_res.json()
        stats["active_sessions"] = [
            {"user": s.get("UserName", "Inconnu"),
             "item": s.get("NowPlayingItem", {}).get("Name") if s.get("NowPlayingItem") else None,
             "device": s.get("DeviceName", ""), "client": s.get("Client", "")}
            for s in sessions if s.get("NowPlayingItem")
        ]

        libs_res = requests.get(f"{JELLYFIN_URL}/Library/MediaFolders", headers=jf_headers(), timeout=5)
        libs = libs_res.json().get("Items", [])
        for lib in libs:
            count_res = requests.get(f"{JELLYFIN_URL}/Items", headers=jf_headers(),
                params={"ParentId": lib["Id"], "Recursive": "true", "IncludeItemTypes": "Movie,Series"}, timeout=5)
            stats["libraries"].append({"name": lib["Name"], "count": count_res.json().get("TotalRecordCount", 0)})
    except requests.exceptions.RequestException as e:
        stats["jellyfin_error"] = str(e)

    if JELLYSEERR_URL and JELLYSEERR_API_KEY:
        try:
            js_res = requests.get(f"{JELLYSEERR_URL}/api/v1/request/count",
                headers={"X-Api-Key": JELLYSEERR_API_KEY}, timeout=5)
            stats["jellyseerr"] = js_res.json()
        except requests.exceptions.RequestException as e:
            stats["jellyseerr_error"] = str(e)

    return render_template("stats.html", stats=stats)

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
