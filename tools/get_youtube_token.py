"""YouTube refresh token alır (yalnızca standart kütüphane; hiçbir şey kurmaz).

Kullanım:  python tools/get_youtube_token.py <CLIENT_ID> <CLIENT_SECRET>
Google Cloud'da "Masaüstü uygulaması" (Desktop) türünde OAuth istemcisi gerekir.
Tarayıcı açılır, hesabı seçersin; terminalde REFRESH TOKEN yazılır → GitHub Secret olarak kaydet.
Not: Şirket ağı yerel porta/Google'a izin vermiyorsa SETUP.md'deki "OAuth Playground" yöntemini kullan.
"""
import http.server
import json
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPES = " ".join([
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
])
PORT = 8765


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    cid, secret = sys.argv[1], sys.argv[2]
    redirect = f"http://127.0.0.1:{PORT}/"
    result = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = (q.get("code") or [None])[0]
            result["error"] = (q.get("error") or [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Tamam, bu sekmeyi kapatabilirsin.</h2>".encode())
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *a):
            pass

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent"})
    srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
    print("Tarayıcı açılıyor. Açılmazsa bu adresi kopyala:\n" + url)
    webbrowser.open(url)
    srv.serve_forever()
    if not result.get("code"):
        sys.exit(f"Yetkilendirme alınamadı: {result.get('error')}")
    data = urllib.parse.urlencode({"code": result["code"], "client_id": cid, "client_secret": secret,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data)) as r:
        tok = json.loads(r.read())
    if not tok.get("refresh_token"):
        sys.exit("refresh_token gelmedi. Hesabın uygulama erişimini kaldırıp tekrar dene: myaccount.google.com/permissions")
    print("\nREFRESH TOKEN (gizli tut, GitHub Secret'a yaz):\n" + tok["refresh_token"])


if __name__ == "__main__":
    main()
