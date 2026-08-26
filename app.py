import os
import uuid
import requests
from flask import Flask, request, jsonify, render_template_string
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

JELLYFIN_URL = os.getenv("JELLYFIN_URL")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY")

invites = {}

INVITE_FORM = """
<!DOCTYPE html>
<html>
<head><title>Invitation Jellyfin</title></head>
<body>
<h2>Créer votre compte Jellyfin</h2>
<form method="POST" action="/invite/{{ token }}">
<input type="text" name="username" placeholder="Nom d'utilisateur" required><br><br>
<input type="password" name="password" placeholder="Mot de passe" required><br><br>
<button type="submit">Créer mon compte</button>
</form>
</body>
</html>
"""

def jf_headers():
    return {
        "Authorization": f'MediaBrowser Token={JELLYFIN_API_KEY}',
        "Content-Type": "application/json"
    }

@app.route("/create-invite", methods=["POST"])
def create_invite():
    token = str(uuid.uuid4())[:8]
    invites[token] = {"used": False}
    return jsonify({"invite_url": f"/invite/{token}"})

@app.route("/invite/<token>", methods=["GET"])
def show_invite(token):
    if token not in invites or invites[token]["used"]:
        return "Lien invalide ou déjà utilisé.", 404
    return render_template_string(INVITE_FORM, token=token)

@app.route("/invite/<token>", methods=["POST"])
def process_invite(token):
    if token not in invites or invites[token]["used"]:
        return "Lien invalide ou déjà utilisé.", 404

    username = request.form.get("username")
    password = request.form.get("password")

    res = requests.post(
        f"{JELLYFIN_URL}/Users/New",
        headers=jf_headers(),
        json={"Name": username, "Password": password}
    )

    if res.status_code == 200:
        invites[token]["used"] = True
        return "Compte créé avec succès ! Vous pouvez fermer cette page."
    else:
        return f"Erreur lors de la création : {res.text}", 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
