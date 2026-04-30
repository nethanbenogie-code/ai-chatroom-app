from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from database import (
    init_db, create_user, get_user, get_user_by_email,
    update_last_seen, update_profile, increment_message_count,
    get_all_users, get_all_users_full, get_rooms, create_room, delete_room,
    save_message, get_room_messages, get_dm_messages,
    search_messages, toggle_reaction, get_reactions_for_messages,
    set_admin, set_badge_manual, ban_user, unban_user, is_user_banned,
    get_bans, delete_message, get_messages_by_user, get_admin_stats,
    create_reset_token, verify_reset_token, reset_password, get_user_by_email_only
 
)
import re
import secrets
import os

app = Flask(__name__)

# Session configuration
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

socketio = SocketIO(app, cors_allowed_origins="*")

init_db()

# sid -> {username, room, color, badge}
active_sessions = {}
# room -> set of usernames
online_in_room = {}

AVATAR_COLORS = [
    '#6C63FF', '#FF6584', '#43B97F', '#F4A261',
    '#2A9D8F', '#E76F51', '#457B9D', '#E63946',
    '#9C6ADE', '#00BFA5', '#FF7043', '#5C6BC0'
]

# ── Auth routes ──────────────────────────────────────────
@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login_page'))
    
    user = get_user(session['username'])
    if not user:
        session.clear()
        return redirect(url_for('login_page'))
   
    if user.get('is_banned', 0):
        session.clear()
        return render_template('banned.html')
    
    return render_template('app.html', username=session['username'])



#---------------------------------------------------------------
# Get all messages for admin panel
@app.route('/api/messages/all')
def api_all_messages():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    limit = request.args.get('limit', 200, type=int)
    messages = get_all_messages(limit)
    return jsonify(messages)

# Get bans list
@app.route('/api/admin/bans')
def api_bans():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    return jsonify(get_bans())

@app.route('/auth')
def login_page():
    if 'username' in session:
        return redirect(url_for('index'))
    return render_template('auth.html')

@app.route('/api/signup', methods=['POST'])
def signup():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'ok': False, 'msg': 'Invalid request'})
        
        username = data.get('username', '').strip()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')

        if not username or not email or not password:
            return jsonify({'ok': False, 'msg': 'All fields required'})
        if len(username) < 3:
            return jsonify({'ok': False, 'msg': 'Username must be at least 3 characters'})
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            return jsonify({'ok': False, 'msg': 'Username: letters, numbers, underscore only'})
        if len(password) < 6:
            return jsonify({'ok': False, 'msg': 'Password must be at least 6 characters'})
        if '@' not in email:
            return jsonify({'ok': False, 'msg': 'Invalid email address'})

        import random
        color = AVATAR_COLORS[random.randint(0, len(AVATAR_COLORS)-1)]
        pw_hash = generate_password_hash(password)
        ok, msg = create_user(username, email, pw_hash, color)
        if not ok:
            return jsonify({'ok': False, 'msg': msg})

        session['username'] = username
        session.permanent = True
        return jsonify({'ok': True, 'redirect': '/'})
    except Exception as e:
        print(f"Signup error: {e}")
        return jsonify({'ok': False, 'msg': 'Server error, please try again'})

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'ok': False, 'msg': 'Invalid request'})
        
        identifier = data.get('identifier', '').strip()
        password = data.get('password', '')

        if not identifier or not password:
            return jsonify({'ok': False, 'msg': 'All fields required'})

        user = get_user(identifier) or get_user_by_email(identifier.lower())
        if not user:
            return jsonify({'ok': False, 'msg': 'Invalid username/email or password'})
        
        if not check_password_hash(user['password_hash'], password):
            return jsonify({'ok': False, 'msg': 'Invalid username/email or password'})
        
        if user.get('is_banned', 0):
            return jsonify({'ok': False, 'msg': 'Your account has been banned'})

        session['username'] = user['username']
        session.permanent = True
        update_last_seen(user['username'])
        return jsonify({'ok': True, 'redirect': '/'})
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({'ok': False, 'msg': 'Server error, please try again'})

@app.route('/api/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/api/check_session')
def check_session():
    return jsonify({
        'logged_in': 'username' in session,
        'username': session.get('username'),
        'session_id': request.cookies.get('session')
    })

@app.route('/api/profile', methods=['POST'])
def save_profile():
    if 'username' not in session:
        return jsonify({'ok': False, 'msg': 'Not logged in'})
    data = request.get_json()
    bio = data.get('bio', '')[:200]
    color = data.get('color', '#6C63FF')
    update_profile(session['username'], bio, color)
    return jsonify({'ok': True})

@socketio.on('typing')
def handle_typing(data):
    # Broadcast to everyone else in the room that this user is typing
    socketio.emit('display_typing', data, room=data['room'], include_self=False)

@socketio.on('stop_typing')
def handle_stop_typing(data):
    socketio.emit('hide_typing', data, room=data['room'], include_self=False)

@app.route('/api/rooms')
def api_rooms():
    return jsonify(get_rooms())

@app.route('/api/rooms/create', methods=['POST'])
def api_create_room():
    if 'username' not in session:
        return jsonify({'ok': False, 'msg': 'Not logged in'})
    data = request.get_json()
    name = data.get('name', '').strip()
    desc = data.get('description', '').strip()
    if not name or len(name) < 2:
        return jsonify({'ok': False, 'msg': 'Room name too short'})
    ok, msg = create_room(name, desc, session['username'])
    if ok:
        socketio.emit('new_room', {'name': name, 'description': desc, 'created_by': session['username']})
    return jsonify({'ok': ok, 'msg': msg})

@app.route('/api/members')
def api_members():
    return jsonify(get_all_users())

@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').strip()
    room = request.args.get('room', None)
    if not q or len(q) < 2:
        return jsonify([])
    return jsonify(search_messages(q, room))

@app.route('/api/me')
def api_me():
    if 'username' not in session:
        return jsonify({})
    user = get_user(session['username'])
    if user:
        user = {k: v for k, v in user.items() if k not in ['password_hash']}
    return jsonify(user or {})

# ── Password Reset Routes ──────────────────────────────────
@app.route('/forgot-password')
def forgot_password_page():
    return render_template('forgot_password.html')

@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    
    if not email:
        return jsonify({'ok': False, 'msg': 'Email is required'})
    
    token, error = create_reset_token(email)
    
    if error:
        return jsonify({'ok': True, 'msg': 'If an account exists with that email, you will receive reset instructions.'})
    
    reset_link = f"/reset-password?token={token}"
    
    print(f"\n{'='*50}")
    print(f"🔐 PASSWORD RESET REQUEST")
    print(f"Email: {email}")
    print(f"Reset link: http://localhost:5000{reset_link}")
    print(f"{'='*50}\n")
    
    return jsonify({
        'ok': True, 
        'msg': 'Reset link sent to your email',
        'reset_link': reset_link
    })

@app.route('/reset-password')
def reset_password_page():
    token = request.args.get('token', '')
    if not token:
        return redirect(url_for('login_page'))
    return render_template('reset_password.html', token=token)

@app.route('/api/reset-password', methods=['POST'])
def reset_password_api():
    data = request.get_json()
    token = data.get('token', '')
    new_password = data.get('new_password', '')
    confirm_password = data.get('confirm_password', '')
    
    if not token:
        return jsonify({'ok': False, 'msg': 'Invalid request'})
    
    if len(new_password) < 6:
        return jsonify({'ok': False, 'msg': 'Password must be at least 6 characters'})
    
    if new_password != confirm_password:
        return jsonify({'ok': False, 'msg': 'Passwords do not match'})
    
    email, error = verify_reset_token(token)
    if error:
        return jsonify({'ok': False, 'msg': error})
    
    if reset_password(email, new_password):
        return jsonify({'ok': True, 'msg': 'Password reset successful! You can now login.'})
    
    return jsonify({'ok': False, 'msg': 'Failed to reset password'})

@app.route('/api/remind-username', methods=['POST'])
def remind_username():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    
    if not email:
        return jsonify({'ok': False, 'msg': 'Email is required'})
    
    user = get_user_by_email_only(email)
    
    if user:
        print(f"\n{'='*50}")
        print(f"📧 USERNAME REMINDER")
        print(f"Email: {email}")
        print(f"Username: {user['username']}")
        print(f"{'='*50}\n")
        
        return jsonify({
            'ok': True,
            'username': user['username'],
            'msg': f'Your username is: {user["username"]}'
        })
    else:
        return jsonify({
            'ok': True,
            'msg': 'If an account exists with that email, your username will be sent.'
        })

# ── Admin API routes ──────────────────────────────────────
def require_admin():
    if 'username' not in session:
        return None
    user = get_user(session['username'])
    if not user or not user.get('is_admin', 0):
        return None
    return user

@app.route('/admin')
def admin_dashboard():
    user = require_admin()
    if not user:
        return redirect(url_for('index'))
    
    stats = get_admin_stats()
    users = get_all_users_full()
    bans = get_bans()
    rooms = get_rooms()
    
    return render_template('admin.html', 
                         stats=stats, 
                         users=users, 
                         bans=bans, 
                         rooms=rooms,
                         admin_user=user)

@app.route('/api/admin/users')
def api_admin_users():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    return jsonify(get_all_users_full())

@app.route('/api/admin/set_admin', methods=['POST'])
def api_set_admin():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    
    data = request.get_json()
    target = data.get('username')
    is_admin = data.get('is_admin', False)
    
    set_admin(target, is_admin)
    return jsonify({'ok': True})

@app.route('/api/admin/set_badge', methods=['POST'])
def api_set_badge():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    
    data = request.get_json()
    target = data.get('username')
    badge = data.get('badge')
    
    valid_badges = ['Member', 'Active', 'Veteran', 'Moderator', 'Admin']
    if badge not in valid_badges:
        return jsonify({'ok': False, 'msg': 'Invalid badge'})
    
    set_badge_manual(target, badge)
    return jsonify({'ok': True})

@app.route('/api/admin/ban', methods=['POST'])
def api_ban():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    
    data = request.get_json()
    target = data.get('username')
    reason = data.get('reason', 'No reason provided')
    duration_days = data.get('duration_days', None)
    
    expires_at = None
    if duration_days:
        expires_at = (datetime.now() + timedelta(days=int(duration_days))).isoformat()
    
    if ban_user(target, user['username'], reason, expires_at):
        for sid, sess in list(active_sessions.items()):
            if sess['username'] == target:
                socketio.emit('banned', {'reason': reason}, to=sid)
                socketio.server.disconnect(sid)
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'msg': 'Failed to ban user'})

@app.route('/api/admin/unban', methods=['POST'])
def api_unban():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    
    data = request.get_json()
    target = data.get('username')
    
    unban_user(target)
    return jsonify({'ok': True})

@app.route('/api/admin/kick', methods=['POST'])
def api_kick():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    
    data = request.get_json()
    target = data.get('username')
    room = data.get('room')
    reason = data.get('reason', 'Kicked by admin')
    
    for sid, sess in list(active_sessions.items()):
        if sess['username'] == target and sess['room'] == room:
            socketio.emit('kicked', {'reason': reason, 'room': room}, to=sid)
            socketio.server.disconnect(sid)
            return jsonify({'ok': True})
    
    return jsonify({'ok': False, 'msg': 'User not found in room'})

@app.route('/api/admin/delete_message', methods=['POST'])
def api_delete_message():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    
    data = request.get_json()
    message_id = data.get('message_id')
    room = data.get('room')
    
    delete_message(message_id, user['username'])
    socketio.emit('message_deleted', {'message_id': message_id}, to=room)
    return jsonify({'ok': True})

@app.route('/api/admin/delete_room', methods=['POST'])
def api_delete_room():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    
    data = request.get_json()
    room_name = data.get('room_name')
    
    ok, msg = delete_room(room_name)
    if ok:
        socketio.emit('room_deleted', {'room_name': room_name})
    return jsonify({'ok': ok, 'msg': msg})

@app.route('/api/admin/user_messages/<username>')
def api_user_messages(username):
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    
    return jsonify(get_messages_by_user(username))

@app.route('/api/admin/stats')
def api_admin_stats():
    user = require_admin()
    if not user:
        return jsonify({'ok': False, 'msg': 'Unauthorized'}), 403
    
    return jsonify(get_admin_stats())

# ── Socket events ────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    if 'username' not in session:
        return False
    
    if is_user_banned(session['username']):
        return False
    
    return True

@socketio.on('join_room_req')
def on_join_room(data):
    username = session.get('username')
    if not username:
        return
    room = data.get('room', 'General')
    user = get_user(username)
    if not user:
        return

    active_sessions[request.sid] = {
        'username': username,
        'room': room,
        'color': user['avatar_color'],
        'badge': user['badge']
    }

    if room not in online_in_room:
        online_in_room[room] = set()
    online_in_room[room].add(username)
    join_room(room)

    msgs = get_room_messages(room)
    msg_ids = [m['id'] for m in msgs]
    reactions = get_reactions_for_messages(msg_ids)
    for m in msgs:
        m['reactions'] = reactions.get(m['id'], {})
        m['time'] = _fmt_time(m['sent_at'])

    emit('history', {'messages': msgs})
    emit('room_presence', {
        'room': room,
        'count': len(online_in_room[room]),
        'users': list(online_in_room[room])
    }, to=room)

@socketio.on('send_message')
def on_message(data):
    s = active_sessions.get(request.sid)
    if not s:
        return
    text = data.get('text', '').strip()
    if not text or len(text) > 1000:
        return
    
    if is_user_banned(s['username']):
        emit('error', {'msg': 'You are banned from sending messages'})
        return

    msg_id = save_message(s['room'], s['username'], text)
    increment_message_count(s['username'])
    user = get_user(s['username'])

    msg = {
        'id': msg_id,
        'username': s['username'],
        'color': s['color'],
        'badge': user['badge'] if user else s['badge'],
        'text': text,
        'time': datetime.now().strftime('%I:%M %p'),
        'reactions': {}
    }
    emit('new_message', msg, to=s['room'])

@socketio.on('send_dm')
def on_dm(data):
    s = active_sessions.get(request.sid)
    if not s:
        return
    to_user = data.get('to', '').strip()
    text = data.get('text', '').strip()
    if not text or not to_user or to_user == s['username']:
        return

    target = get_user(to_user)
    if not target:
        emit('dm_error', {'msg': f'User {to_user} not found'})
        return
    
    if target.get('is_banned', 0):
        emit('dm_error', {'msg': f'{to_user} is banned'})
        return

    msg_id = save_message('__dm__', s['username'], text, is_dm=True, dm_to=to_user)
    msg = {
        'id': msg_id,
        'username': s['username'],
        'color': s['color'],
        'badge': s['badge'],
        'text': text,
        'time': datetime.now().strftime('%I:%M %p'),
        'to': to_user,
        'reactions': {}
    }

    emit('dm_message', msg)
    for sid, sess in active_sessions.items():
        if sess['username'] == to_user:
            socketio.emit('dm_message', msg, to=sid)
            break

@socketio.on('load_dm')
def on_load_dm(data):
    s = active_sessions.get(request.sid)
    if not s:
        return
    other = data.get('user', '')
    if not other:
        return
    msgs = get_dm_messages(s['username'], other)
    msg_ids = [m['id'] for m in msgs]
    rxns = get_reactions_for_messages(msg_ids)
    for m in msgs:
        m['reactions'] = rxns.get(m['id'], {})
        m['time'] = _fmt_time(m['sent_at'])
    emit('dm_history', {'messages': msgs, 'with': other})

@socketio.on('react')
def on_react(data):
    s = active_sessions.get(request.sid)
    if not s:
        return
    msg_id = data.get('message_id')
    emoji = data.get('emoji', '')
    if not msg_id or not emoji:
        return
    action, counts = toggle_reaction(msg_id, s['username'], emoji)
    emit('reaction_update', {'message_id': msg_id, 'reactions': counts}, to=s['room'])

@socketio.on('switch_room')
def on_switch_room(data):
    s = active_sessions.get(request.sid)
    if not s:
        return
    old_room = s['room']
    new_room = data.get('room', 'General')

    if old_room in online_in_room:
        online_in_room[old_room].discard(s['username'])
    leave_room(old_room)
    emit('room_presence', {
        'room': old_room,
        'count': len(online_in_room.get(old_room, set())),
        'users': list(online_in_room.get(old_room, set()))
    }, to=old_room)

    s['room'] = new_room
    if new_room not in online_in_room:
        online_in_room[new_room] = set()
    online_in_room[new_room].add(s['username'])
    join_room(new_room)

    msgs = get_room_messages(new_room)
    msg_ids = [m['id'] for m in msgs]
    reactions = get_reactions_for_messages(msg_ids)
    for m in msgs:
        m['reactions'] = reactions.get(m['id'], {})
        m['time'] = _fmt_time(m['sent_at'])

    emit('history', {'messages': msgs})
    emit('room_presence', {
        'room': new_room,
        'count': len(online_in_room[new_room]),
        'users': list(online_in_room[new_room])
    }, to=new_room)
    emit('room_switched', {'room': new_room})

@socketio.on('typing')
def on_typing():
    s = active_sessions.get(request.sid)
    if s:
        emit('user_typing', {'username': s['username']}, to=s['room'], include_self=False)

@socketio.on('disconnect')
def on_disconnect():
    s = active_sessions.pop(request.sid, None)
    if s:
        room = s['room']
        if room in online_in_room:
            online_in_room[room].discard(s['username'])
        update_last_seen(s['username'])
        emit('room_presence', {
            'room': room,
            'count': len(online_in_room.get(room, set())),
            'users': list(online_in_room.get(room, set()))
        }, to=room)

def _fmt_time(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime('%I:%M %p')
    except:
        return ''

if __name__ == '__main__':
    # Get the port from Render's environment, or use 8080 for local testing
    port = int(os.environ.get("PORT", 8080))
    
    # host='0.0.0.0' is required for the app to be reachable on the web
    # allow_unsafe_werkzeug must be False in production
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=False)
