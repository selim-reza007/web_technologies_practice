import http.server
from http.cookies import SimpleCookie
import sqlite3
import re
import urllib.parse
import hashlib
import secrets
import mimetypes
import os

DB_FILE = "blog.db"

# ==========================================
# 1. DATABASE SETUP
# ==========================================
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)
        conn.commit()

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# ==========================================
# 2. TEMPLATE RENDERER
# ==========================================
def render_template(title: str, content: str, user: dict = None) -> str:
    auth_nav = (
        f'<span>Hello, <b>{user["username"]}</b></span> | '
        f'<a href="/create">New Post</a> | '
        f'<a href="/logout">Logout</a>'
    ) if user else '<a href="/login">Login</a> | <a href="/register">Register</a>'

    template_path = os.path.join("templates", "layout.html")
    with open(template_path, "r", encoding="utf-8") as f:
        layout = f.read()

    return layout.format(title=title, auth_nav=auth_nav, content=content)

# ==========================================
# 3. HTTP REQUEST HANDLER & ROUTER
# ==========================================
class BlogHandler(http.server.BaseHTTPRequestHandler):

    def serve_static(self, path: str):
        # Prevent directory traversal attacks
        safe_path = os.path.normpath(path.lstrip("/"))
        if not safe_path.startswith("static"):
            self.send_error(403, "Access Denied")
            return

        if os.path.exists(safe_path) and os.path.isfile(safe_path):
            mime_type, _ = mimetypes.guess_type(safe_path)
            self.send_response(200)
            self.send_header("Content-Type", mime_type or "application/octet-stream")
            self.end_headers()
            with open(safe_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, "File Not Found")

    def get_current_user(self):
        cookie_header = self.headers.get('Cookie')
        if not cookie_header:
            return None
        cookie = SimpleCookie(cookie_header)
        if 'session' not in cookie:
            return None
        token = cookie['session'].value
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT users.id, users.username FROM sessions "
                "JOIN users ON sessions.user_id = users.id WHERE sessions.token = ?", 
                (token,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def parse_form_data(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8')
        parsed = urllib.parse.parse_qs(body)
        return {k: v[0] for k, v in parsed.items()}

    def send_html(self, html_str: str, status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(html_str.encode('utf-8'))

    def redirect(self, location: str, headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()

    # --- GET ENDPOINTS ---
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path

        # Static Asset Handler (.css, .js, images)
        if path.startswith("/static/"):
            self.serve_static(path)
            return

        user = self.get_current_user()

        if path == "/":
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT posts.id, posts.title, posts.content, posts.created_at, users.username "
                    "FROM posts LEFT JOIN users ON posts.user_id = users.id ORDER BY posts.id DESC"
                )
                posts = cur.fetchall()
            
            content = "<h1>Recent Posts</h1>"
            if not posts:
                content += "<p>No posts yet. Register/Login to write the first one!</p>"
            for post in posts:
                content += f"""
                <div class="card">
                    <h3 style="margin-top:0;"><a href="/post/{post['id']}">{post['title']}</a></h3>
                    <div class="meta">By {post['username'] or 'Anonymous'} on {post['created_at']}</div>
                    <p>{post['content'][:200]}...</p>
                </div>"""
            self.send_html(render_template("Home", content, user))

        elif m := re.match(r"^/post/(?P<id>\d+)$", path):
            post_id = m.group("id")
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT posts.title, posts.content, posts.created_at, users.username "
                    "FROM posts LEFT JOIN users ON posts.user_id = users.id WHERE posts.id = ?", 
                    (post_id,)
                )
                post = cur.fetchone()

            if not post:
                self.send_html(render_template("404", "<h1>404 Post Not Found</h1>", user), status=404)
                return

            content = f"""
            <div class="card">
                <h1 style="margin-top:0;">{post['title']}</h1>
                <div class="meta">By {post['username'] or 'Anonymous'} on {post['created_at']}</div>
                <p style="white-space: pre-wrap;">{post['content']}</p>
            </div>
            <p><a href="/">&larr; Back to home</a></p>
            """
            self.send_html(render_template(post['title'], content, user))

        elif path == "/login":
            content = """
            <div class="card">
                <h2 style="margin-top:0;">Login</h2>
                <form method="POST" action="/login">
                    <label>Username</label>
                    <input type="text" name="username" required>
                    <label>Password</label>
                    <input type="password" name="password" required>
                    <button type="submit">Log In</button>
                </form>
            </div>"""
            self.send_html(render_template("Login", content, user))

        elif path == "/register":
            content = """
            <div class="card">
                <h2 style="margin-top:0;">Register</h2>
                <form method="POST" action="/register">
                    <label>Username</label>
                    <input type="text" name="username" required>
                    <label>Password</label>
                    <input type="password" name="password" required>
                    <button type="submit">Sign Up</button>
                </form>
            </div>"""
            self.send_html(render_template("Register", content, user))

        elif path == "/create":
            if not user:
                self.redirect("/login")
                return
            content = """
            <div class="card">
                <h2 style="margin-top:0;">Create New Post</h2>
                <form method="POST" action="/create">
                    <label>Title</label>
                    <input type="text" name="title" required>
                    <label>Content</label>
                    <textarea name="content" rows="8" required></textarea>
                    <button type="submit">Publish Post</button>
                </form>
            </div>"""
            self.send_html(render_template("New Post", content, user))

        elif path == "/logout":
            cookie_header = self.headers.get('Cookie')
            if cookie_header:
                cookie = SimpleCookie(cookie_header)
                if 'session' in cookie:
                    token = cookie['session'].value
                    with get_db() as conn:
                        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
                        conn.commit()
            self.redirect("/", headers={"Set-Cookie": "session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT"})

        else:
            self.send_html(render_template("404", "<h1>404 Page Not Found</h1>", user), status=404)

    # --- POST ENDPOINTS ---
    def do_POST(self):
        user = self.get_current_user()
        path = urllib.parse.urlparse(self.path).path
        data = self.parse_form_data()

        if path == "/register":
            username = data.get("username", "").strip()
            password = data.get("password", "")
            if not username or not password:
                self.send_html(render_template("Error", "<h3>Username and password required</h3>", user), status=400)
                return
            pwd_hash = hash_password(password)
            try:
                with get_db() as conn:
                    conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, pwd_hash))
                    conn.commit()
                self.redirect("/login")
            except sqlite3.IntegrityError:
                self.send_html(render_template("Error", "<h3>Username already taken</h3>", user), status=400)

        elif path == "/login":
            username = data.get("username", "").strip()
            password = data.get("password", "")
            pwd_hash = hash_password(password)
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id FROM users WHERE username = ? AND password_hash = ?", (username, pwd_hash))
                row = cur.fetchone()

            if row:
                user_id = row['id']
                token = secrets.token_hex(16)
                with get_db() as conn:
                    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
                    conn.commit()
                self.redirect("/", headers={"Set-Cookie": f"session={token}; Path=/; HttpOnly"})
            else:
                self.send_html(render_template("Error", "<h3>Invalid credentials</h3>", user), status=401)

        elif path == "/create":
            if not user:
                self.redirect("/login")
                return
            title = data.get("title", "").strip()
            content = data.get("content", "").strip()
            if title and content:
                with get_db() as conn:
                    conn.execute("INSERT INTO posts (title, content, user_id) VALUES (?, ?, ?)", (title, content, user['id']))
                    conn.commit()
            self.redirect("/")

# ==========================================
# 4. SERVER INITIALIZATION
# ==========================================
def run(port=8000):
    init_db()
    server_address = ('', port)
    httpd = http.server.ThreadingHTTPServer(server_address, BlogHandler)
    print(f"🚀 Blog App running on http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")

if __name__ == '__main__':
    run()