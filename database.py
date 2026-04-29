import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime, timedelta
import secrets
import hashlib

# Render provides this automatically once you link the Postgres database to your Web Service
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db():
    # Connects to Render Postgres with SSL required for security
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        avatar_color TEXT DEFAULT '#6C63FF',
        bio TEXT DEFAULT '',
        badge TEXT DEFAULT 'Member',
        message_count INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        joined_at TIMESTAMP NOT NULL,
        last_seen TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS rooms (
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        description TEXT DEFAULT '',
        created_by TEXT,
        created_at TIMESTAMP NOT NULL,
        is_default INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        room TEXT NOT NULL,
        username TEXT NOT NULL,
        text TEXT NOT NULL,
        sent_at TIMESTAMP NOT NULL,
        is_dm INTEGER DEFAULT 0,
        dm_to TEXT DEFAULT NULL,
        is_deleted INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS reactions (
        id SERIAL PRIMARY KEY,
        message_id INTEGER NOT NULL,
        username TEXT NOT NULL,
        emoji TEXT NOT NULL,
        UNIQUE(message_id, username, emoji)
    );

    CREATE TABLE IF NOT EXISTS bans (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL,
        banned_by TEXT NOT NULL,
        reason TEXT,
        banned_at TIMESTAMP NOT NULL,
        expires_at TIMESTAMP
    );
    
    CREATE TABLE IF NOT EXISTS password_resets (
        id SERIAL PRIMARY KEY,
        email TEXT NOT NULL,
        token TEXT NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        used INTEGER DEFAULT 0
    );
    """)

    # Default rooms setup
    defaults = [
        ('General', 'General chat for everyone', 'system', 1),
        ('Tech Talk', 'Programming, tech news & projects', 'system', 1),
        ('Random', 'Off-topic fun and memes', 'system', 1),
        ('Study Group', 'Learning together', 'system', 1),
    ]
    for name, desc, creator, is_def in defaults:
        cur.execute("""
            INSERT INTO rooms (name, description, created_by, created_at, is_default) 
            VALUES (%s, %s, %s, %s, %s) 
            ON CONFLICT (name) DO NOTHING""",
            (name, desc, creator, datetime.now(), is_def))

    conn.commit()
    cur.close()
    conn.close()

# ── User Operations ──────────────────────────────────────
def create_user(username, email, password_hash, color):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, email, password_hash, avatar_color, joined_at) VALUES (%s,%s,%s,%s,%s)",
            (username, email, password_hash, color, datetime.now())
        )
        conn.commit()
        return True, "ok"
    except psycopg2.IntegrityError as e:
        conn.rollback()
        if 'username' in str(e):
            return False, "Username already taken"
        return False, "Email already registered"
    finally:
        cur.close()
        conn.close()

def get_user(username):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE username=%s", (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None

def get_user_by_email(email):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE email=%s", (email,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None

def get_user_by_email_only(email):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT username, email FROM users WHERE email=%s", (email,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None

def update_last_seen(username):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_seen=%s WHERE username=%s", (datetime.now(), username))
    conn.commit()
    cur.close()
    conn.close()

def update_profile(username, bio, color):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET bio=%s, avatar_color=%s WHERE username=%s", (bio, color, username))
    conn.commit()
    cur.close()
    conn.close()

def increment_message_count(username):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET message_count = message_count + 1 WHERE username=%s", (username,))
    cur.execute("SELECT message_count FROM users WHERE username=%s", (username,))
    row = cur.fetchone()
    if row:
        count = row[0]
        badge = 'Veteran' if count >= 100 else ('Active' if count >= 20 else 'Member')
        cur.execute("UPDATE users SET badge=%s WHERE username=%s", (badge, username))
    conn.commit()
    cur.close()
    conn.close()

def get_all_users():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT username, avatar_color, badge, message_count, last_seen, is_admin, is_banned FROM users ORDER BY message_count DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_all_users_full():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users ORDER BY joined_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def set_admin(username, is_admin):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_admin=%s WHERE username=%s", (1 if is_admin else 0, username))
    conn.commit()
    cur.close()
    conn.close()

def set_badge_manual(username, badge):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET badge=%s WHERE username=%s", (badge, username))
    conn.commit()
    cur.close()
    conn.close()

def ban_user(username, banned_by, reason, expires_at=None):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO bans (username, banned_by, reason, banned_at, expires_at) VALUES (%s,%s,%s,%s,%s)",
            (username, banned_by, reason, datetime.now(), expires_at)
        )
        cur.execute("UPDATE users SET is_banned=1 WHERE username=%s", (username,))
        conn.commit()
        return True
    except:
        conn.rollback()
        return False
    finally:
        cur.close()
        conn.close()

def unban_user(username):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM bans WHERE username=%s", (username,))
    cur.execute("UPDATE users SET is_banned=0 WHERE username=%s", (username,))
    conn.commit()
    cur.close()
    conn.close()

def is_user_banned(username):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM bans WHERE username=%s AND (expires_at IS NULL OR expires_at > %s)", (username, datetime.now()))
    ban = cur.fetchone()
    cur.close()
    conn.close()
    return ban is not None

def get_bans():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM bans ORDER BY banned_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def delete_message(message_id, username):
    conn = get_db()
    cur = conn.cursor()
    user = get_user(username)
    if user and user.get('is_admin', 0):
        cur.execute("UPDATE messages SET is_deleted=1, text='[Message deleted by admin]' WHERE id=%s", (message_id,))
    else:
        cur.execute("UPDATE messages SET is_deleted=1, text='[Message deleted]' WHERE id=%s AND username=%s", (message_id, username))
    conn.commit()
    cur.close()
    conn.close()

def get_messages_by_user(username, limit=50):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT * FROM messages WHERE username=%s AND is_dm=0 ORDER BY sent_at DESC LIMIT %s",
        (username, limit)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

# ── Room Operations ──────────────────────────────────────
def get_rooms():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM rooms ORDER BY is_default DESC, name")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def create_room(name, description, created_by):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO rooms (name, description, created_by, created_at, is_default) VALUES (%s,%s,%s,%s,0)",
            (name, description, created_by, datetime.now())
        )
        conn.commit()
        return True, "ok"
    except psycopg2.IntegrityError:
        conn.rollback()
        return False, "Room already exists"
    finally:
        cur.close()
        conn.close()

def delete_room(room_name):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT is_default FROM rooms WHERE name=%s", (room_name,))
    room = cur.fetchone()
    if room and room[0] == 1:
        cur.close()
        conn.close()
        return False, "Cannot delete default room"
    cur.execute("DELETE FROM rooms WHERE name=%s", (room_name,))
    conn.commit()
    cur.close()
    conn.close()
    return True, "Room deleted"

# ── Message Operations ───────────────────────────────────
def save_message(room, username, text, is_dm=False, dm_to=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO messages (room, username, text, sent_at, is_dm, dm_to) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (room, username, text, datetime.now(), int(is_dm), dm_to)
    )
    msg_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return msg_id

def get_room_messages(room, limit=60):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT m.id, m.username, m.text, m.sent_at, m.is_deleted,
                  u.avatar_color, u.badge
           FROM messages m
           LEFT JOIN users u ON m.username = u.username
           WHERE m.room=%s AND m.is_dm=0
           ORDER BY m.id DESC LIMIT %s""",
        (room, limit)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in reversed(rows)]

def get_dm_messages(user1, user2, limit=60):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT m.id, m.username, m.text, m.sent_at, m.is_deleted,
                  u.avatar_color, u.badge
           FROM messages m
           LEFT JOIN users u ON m.username = u.username
           WHERE m.is_dm=1
             AND ((m.username=%s AND m.dm_to=%s) OR (m.username=%s AND m.dm_to=%s))
           ORDER BY m.id DESC LIMIT %s""",
        (user1, user2, user2, user1, limit)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in reversed(rows)]

def search_messages(query, room=None):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if room:
        cur.execute(
            """SELECT m.id, m.room, m.username, m.text, m.sent_at, u.avatar_color
               FROM messages m LEFT JOIN users u ON m.username=u.username
               WHERE m.is_dm=0 AND m.is_deleted=0 AND m.room=%s AND m.text ILIKE %s
               ORDER BY m.id DESC LIMIT 30""",
            (room, f'%{query}%')
        )
    else:
        cur.execute(
            """SELECT m.id, m.room, m.username, m.text, m.sent_at, u.avatar_color
               FROM messages m LEFT JOIN users u ON m.username=u.username
               WHERE m.is_dm=0 AND m.is_deleted=0 AND m.text ILIKE %s
               ORDER BY m.id DESC LIMIT 30""",
            (f'%{query}%',)
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

# ── Reactions ────────────────────────────────────────────
def toggle_reaction(message_id, username, emoji):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM reactions WHERE message_id=%s AND username=%s AND emoji=%s",
        (message_id, username, emoji)
    )
    existing = cur.fetchone()
    if existing:
        cur.execute("DELETE FROM reactions WHERE id=%s", (existing[0],))
        action = 'removed'
    else:
        cur.execute("INSERT INTO reactions (message_id, username, emoji) VALUES (%s,%s,%s)",
                     (message_id, username, emoji))
        action = 'added'
    conn.commit()
    cur.execute(
        "SELECT emoji, COUNT(*) as cnt FROM reactions WHERE message_id=%s GROUP BY emoji",
        (message_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return action, {r[0]: r[1] for r in rows}

def get_reactions_for_messages(message_ids):
    if not message_ids:
        return {}
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT message_id, emoji, COUNT(*) as cnt FROM reactions WHERE message_id IN %s GROUP BY message_id, emoji",
        (tuple(message_ids),)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = {}
    for r in rows:
        mid = r[0]
        if mid not in result:
            result[mid] = {}
        result[mid][r[1]] = r[2]
    return result

# ── Stats & Password Resets ──────────────────────────────
def get_admin_stats():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT 
            (SELECT COUNT(*) FROM users) as total_users,
            (SELECT COUNT(*) FROM users WHERE is_banned=1) as banned_users,
            (SELECT COUNT(*) FROM messages WHERE is_dm=0 AND is_deleted=0) as total_messages,
            (SELECT COUNT(*) FROM messages WHERE is_deleted=1) as deleted_messages,
            (SELECT COUNT(*) FROM rooms) as total_rooms,
            (SELECT COUNT(*) FROM rooms WHERE is_default=0) as custom_rooms,
            (SELECT COUNT(*) FROM users WHERE last_seen > NOW() - INTERVAL '1 day') as active_today
    """)
    stats = cur.fetchone()
    cur.close()
    conn.close()
    return dict(stats)

def create_reset_token(email):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM users WHERE email=%s", (email,))
    user = cur.fetchone()
    if not user:
        cur.close()
        conn.close()
        return None, "No account found"
    
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now() + timedelta(hours=24)
    
    cur.execute("DELETE FROM password_resets WHERE email=%s", (email,))
    cur.execute(
        "INSERT INTO password_resets (email, token, expires_at) VALUES (%s,%s,%s)",
        (email, token_hash, expires_at)
    )
    conn.commit()
    cur.close()
    conn.close()
    return raw_token, None

def verify_reset_token(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT email, expires_at FROM password_resets WHERE token=%s AND used=0",
        (token_hash,)
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return None, "Invalid or expired token"
    
    if datetime.now() > row['expires_at']:
        cur.close()
        conn.close()
        return None, "Token has expired"
    
    cur.close()
    return row['email'], None

def reset_password(email, new_password):
    from werkzeug.security import generate_password_hash
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE password_resets SET used=1 WHERE email=%s", (email,))
    password_hash = generate_password_hash(new_password)
    cur.execute("UPDATE users SET password_hash=%s WHERE email=%s", (password_hash, email))
    conn.commit()
    affected = cur.rowcount
    cur.close()
    conn.close()
    return affected > 0