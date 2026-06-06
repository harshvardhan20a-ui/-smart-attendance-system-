"""
University Attendance Management System - Flask Backend
Install: pip install flask flask-mysqldb werkzeug
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_mysqldb import MySQL
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, date
import os

app = Flask(__name__)
app.secret_key = 'university_attendance_secret_key_2024'

# ─── MySQL Configuration ───────────────────────────────────────────────────────
app.config['MYSQL_HOST']        = 'localhost'
app.config['MYSQL_USER']        = 'root'
app.config['MYSQL_PASSWORD']    = 'mysql'   # ← change this
app.config['MYSQL_DB']          = 'university_attendance'
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'

mysql = MySQL(app)


# ─── Jinja2 globals ────────────────────────────────────────────────────────────
app.jinja_env.globals['now']     = datetime.now   # fixes {{ now() }} in templates
app.jinja_env.globals['enumerate'] = enumerate    # fixes {% for i,s in enumerate(...) %}


# ─── Auth helpers ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('role') not in roles:
                flash('Access denied.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def get_db():
    return mysql.connection.cursor()


def notify(user_id, title, message, ntype='info'):
    cur = get_db()
    try:
        cur.execute(
            "INSERT INTO notifications (user_id, title, message, type) VALUES (%s,%s,%s,%s)",
            (user_id, title, message, ntype)
        )
        mysql.connection.commit()
    finally:
        cur.close()


# ─── Auth routes ───────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        cur = get_db()
        try:
            cur.execute("SELECT * FROM users WHERE email=%s AND is_active=1", (email,))
            user = cur.fetchone()
        finally:
            cur.close()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id']   = user['id']
            session['role']      = user['role']
            session['full_name'] = user['full_name']
            session['email']     = user['email']
            cur2 = get_db()
            try:
                cur2.execute("UPDATE users SET last_login=NOW() WHERE id=%s", (user['id'],))
                mysql.connection.commit()
            finally:
                cur2.close()
            return redirect(url_for('dashboard'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        full_name   = request.form.get('full_name', '').strip()
        email       = request.form.get('email', '').strip()
        password    = request.form.get('password', '')
        role        = request.form.get('role', 'student')
        department  = request.form.get('department', '').strip()
        roll_number = request.form.get('roll_number', '').strip()
        employee_id = request.form.get('employee_id', '').strip()
        semester    = request.form.get('semester', None)
        phone       = request.form.get('phone', '').strip()

        if not all([full_name, email, password]):
            flash('Please fill in all required fields.', 'danger')
            return render_template('signup.html')

        pw_hash = generate_password_hash(password)
        cur = get_db()
        try:
            cur.execute(
                """INSERT INTO users (full_name, email, password_hash, role, department,
                   roll_number, employee_id, semester, phone)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (full_name, email, pw_hash, role, department or None,
                 roll_number or None, employee_id or None,
                 int(semester) if semester else None, phone or None)
            )
            mysql.connection.commit()
            flash('Account created! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            mysql.connection.rollback()
            flash('Registration failed: email or ID already exists.', 'danger')
        finally:
            cur.close()
    return render_template('signup.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ─── Dashboard ─────────────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    role = session['role']
    uid  = session['user_id']
    cur  = get_db()
    stats = {}

    try:
        if role == 'student':
            cur.execute("""
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN a.status IN ('present','late') THEN 1 ELSE 0 END) AS present_count
                FROM attendance a
                JOIN sessions s ON a.session_id = s.id
                WHERE a.student_id = %s
            """, (uid,))
            row = cur.fetchone()
            total   = int(row['total'] or 0)
            present = int(row['present_count'] or 0)
            stats['attendance_pct'] = round((present / total) * 100, 1) if total else 0
            stats['total_classes']  = total
            stats['present']        = present

            cur.execute("""
                SELECT c.name, c.code,
                  COUNT(DISTINCT s.id) AS total_sessions,
                  SUM(CASE WHEN a.status IN ('present','late') THEN 1 ELSE 0 END) AS attended
                FROM enrollments e
                JOIN courses c ON e.course_id = c.id
                LEFT JOIN sessions s ON s.course_id = c.id
                LEFT JOIN attendance a ON a.session_id = s.id AND a.student_id = %s
                WHERE e.student_id = %s
                GROUP BY c.id, c.name, c.code
            """, (uid, uid))
            stats['courses'] = cur.fetchall()

            cur.execute("""
                SELECT s.session_date, s.start_time, s.end_time, s.topic, s.room,
                       c.name AS course_name, c.code
                FROM sessions s
                JOIN courses c ON s.course_id = c.id
                JOIN enrollments e ON e.course_id = c.id AND e.student_id = %s
                WHERE s.session_date >= CURDATE() AND s.is_cancelled = 0
                ORDER BY s.session_date, s.start_time
                LIMIT 5
            """, (uid,))
            stats['upcoming'] = cur.fetchall()

        elif role == 'teacher':
            cur.execute("""
                SELECT COUNT(DISTINCT e.student_id) AS cnt
                FROM teacher_courses tc
                JOIN enrollments e ON e.course_id = tc.course_id
                WHERE tc.teacher_id = %s
            """, (uid,))
            stats['total_students'] = cur.fetchone()['cnt'] or 0

            cur.execute("SELECT COUNT(*) AS cnt FROM teacher_courses WHERE teacher_id=%s", (uid,))
            stats['total_courses'] = cur.fetchone()['cnt']

            cur.execute("""
                SELECT s.id, s.session_date, s.start_time, s.end_time, s.topic,
                       c.name AS course_name, c.code,
                       (SELECT COUNT(*) FROM attendance a WHERE a.session_id=s.id AND a.status='present') AS present_count,
                       (SELECT COUNT(*) FROM attendance a WHERE a.session_id=s.id) AS total_marked
                FROM sessions s
                JOIN courses c ON s.course_id = c.id
                JOIN teacher_courses tc ON tc.course_id = c.id AND tc.teacher_id = %s
                ORDER BY s.session_date DESC, s.start_time DESC
                LIMIT 5
            """, (uid,))
            stats['recent_sessions'] = cur.fetchall()

            cur.execute("""
                SELECT c.name, c.code, c.id,
                  COUNT(DISTINCT e.student_id) AS enrolled
                FROM teacher_courses tc
                JOIN courses c ON tc.course_id = c.id
                LEFT JOIN enrollments e ON e.course_id = c.id
                WHERE tc.teacher_id = %s
                GROUP BY c.id, c.name, c.code
            """, (uid,))
            stats['my_courses'] = cur.fetchall()

        elif role == 'admin':
            cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE role='student' AND is_active=1")
            stats['total_students'] = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE role='teacher' AND is_active=1")
            stats['total_teachers'] = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) AS cnt FROM courses")
            stats['total_courses'] = cur.fetchone()['cnt']

            cur.execute("SELECT COUNT(*) AS cnt FROM sessions WHERE session_date = CURDATE()")
            stats['todays_sessions'] = cur.fetchone()['cnt']

            cur.execute("""
                SELECT ROUND(
                  100.0 * SUM(CASE WHEN status IN ('present','late') THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0),
                1) AS pct
                FROM attendance
            """)
            r = cur.fetchone()
            stats['overall_attendance'] = r['pct'] if r and r['pct'] else 0

            cur.execute("""
                SELECT u.full_name, u.roll_number, u.department,
                  ROUND(100.0 * SUM(CASE WHEN a.status IN ('present','late') THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 1) AS pct
                FROM users u
                JOIN attendance a ON a.student_id = u.id
                WHERE u.role='student'
                GROUP BY u.id, u.full_name, u.roll_number, u.department
                HAVING pct < 75
                ORDER BY pct ASC
                LIMIT 10
            """)
            stats['low_attendance'] = cur.fetchall()

        # Notifications for all roles
        cur.execute("""
            SELECT * FROM notifications WHERE user_id=%s AND is_read=0
            ORDER BY created_at DESC LIMIT 5
        """, (uid,))
        notifications = cur.fetchall()

    finally:
        cur.close()

    return render_template('dashboard.html', stats=stats, notifications=notifications)


# ─── Sessions ─────────────────────────────────────────────────────────────────
@app.route('/sessions')
@login_required
def sessions_list():
    uid  = session['user_id']
    role = session['role']
    cur  = get_db()
    try:
        if role == 'teacher':
            cur.execute("""
                SELECT s.*, c.name AS course_name, c.code,
                  (SELECT COUNT(*) FROM attendance a WHERE a.session_id=s.id AND a.status='present') AS present_count,
                  (SELECT COUNT(*) FROM enrollments e WHERE e.course_id=s.course_id) AS enrolled_count
                FROM sessions s
                JOIN courses c ON s.course_id = c.id
                JOIN teacher_courses tc ON tc.course_id = c.id AND tc.teacher_id = %s
                ORDER BY s.session_date DESC, s.start_time DESC
            """, (uid,))
            sessions = cur.fetchall()
            cur.execute("""
                SELECT c.id, c.name, c.code FROM courses c
                JOIN teacher_courses tc ON tc.course_id = c.id
                WHERE tc.teacher_id = %s
            """, (uid,))
            courses = cur.fetchall()

        elif role == 'admin':
            cur.execute("""
                SELECT s.*, c.name AS course_name, c.code, u.full_name AS teacher_name
                FROM sessions s
                JOIN courses c ON s.course_id = c.id
                JOIN users u ON s.teacher_id = u.id
                ORDER BY s.session_date DESC, s.start_time DESC
            """)
            sessions = cur.fetchall()
            cur.execute("SELECT id, name, code FROM courses ORDER BY name")
            courses = cur.fetchall()

        else:  # student
            cur.execute("""
                SELECT s.*, c.name AS course_name, c.code,
                  COALESCE(a.status,'absent') AS my_status
                FROM sessions s
                JOIN courses c ON s.course_id = c.id
                JOIN enrollments e ON e.course_id = c.id AND e.student_id = %s
                LEFT JOIN attendance a ON a.session_id = s.id AND a.student_id = %s
                ORDER BY s.session_date DESC, s.start_time DESC
            """, (uid, uid))
            sessions = cur.fetchall()
            courses  = []
    finally:
        cur.close()

    return render_template('sessions.html', sessions=sessions, courses=courses)


@app.route('/sessions/create', methods=['POST'])
@login_required
@role_required('teacher', 'admin')
def create_session():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data received'})

    cur = get_db()
    try:
        start_time = data.get('start_time') or '09:00'
        end_time   = data.get('end_time')   or '10:00'

        cur.execute("""
            INSERT INTO sessions (course_id, teacher_id, session_date, start_time, end_time, topic, room)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            data['course_id'], session['user_id'],
            data['session_date'], start_time, end_time,
            data.get('topic', ''), data.get('room', '')
        ))
        mysql.connection.commit()
        session_id = cur.lastrowid

        # Auto-create attendance records (absent by default)
        cur.execute("SELECT student_id FROM enrollments WHERE course_id=%s", (data['course_id'],))
        students = cur.fetchall()
        for s in students:
            cur.execute(
                "INSERT IGNORE INTO attendance (session_id, student_id, status) VALUES (%s,%s,'absent')",
                (session_id, s['student_id'])
            )
        mysql.connection.commit()
        return jsonify({'success': True, 'session_id': session_id})
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        cur.close()


# ─── Attendance ────────────────────────────────────────────────────────────────
@app.route('/attendance/mark/<int:session_id>', methods=['GET'])
@login_required
@role_required('teacher', 'admin')
def mark_attendance(session_id):
    cur = get_db()
    try:
        cur.execute("""
            SELECT s.*, c.name AS course_name, c.code
            FROM sessions s JOIN courses c ON s.course_id = c.id
            WHERE s.id = %s
        """, (session_id,))
        sess = cur.fetchone()
        if not sess:
            flash('Session not found.', 'danger')
            return redirect(url_for('sessions_list'))

        cur.execute("""
            SELECT u.id, u.full_name, u.roll_number,
              COALESCE(a.status,'absent') AS status, a.remarks
            FROM enrollments e
            JOIN users u ON e.student_id = u.id
            LEFT JOIN attendance a ON a.session_id = %s AND a.student_id = u.id
            WHERE e.course_id = %s
            ORDER BY u.full_name
        """, (session_id, sess['course_id']))
        students = cur.fetchall()
    finally:
        cur.close()

    return render_template('mark_attendance.html', sess=sess, students=students)


@app.route('/attendance/save', methods=['POST'])
@login_required
@role_required('teacher', 'admin')
def save_attendance():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data received'})

    session_id = data.get('session_id')
    records    = data.get('records', [])
    cur = get_db()
    try:
        for rec in records:
            cur.execute("""
                INSERT INTO attendance (session_id, student_id, status, remarks, marked_by)
                VALUES (%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE status=VALUES(status), remarks=VALUES(remarks),
                  marked_by=VALUES(marked_by), marked_at=NOW()
            """, (
                session_id, rec['student_id'],
                rec.get('status', 'absent'),
                rec.get('remarks', '') or '',
                session['user_id']
            ))
        mysql.connection.commit()
        return jsonify({'success': True})
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        cur.close()


@app.route('/attendance/report')
@login_required
def attendance_report():
    uid  = session['user_id']
    role = session['role']

    course_id  = request.args.get('course_id', '').strip()
    student_id = request.args.get('student_id', '').strip()
    from_date  = request.args.get('from_date', '').strip()
    to_date    = request.args.get('to_date', '').strip()

    conditions = []
    params     = []

    if role == 'student':
        conditions.append("a.student_id = %s")
        params.append(uid)
    elif role == 'teacher':
        conditions.append("s.teacher_id = %s")
        params.append(uid)

    if course_id:
        conditions.append("c.id = %s")
        params.append(int(course_id))
    if student_id and role != 'student':
        conditions.append("a.student_id = %s")
        params.append(int(student_id))
    if from_date:
        conditions.append("s.session_date >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("s.session_date <= %s")
        params.append(to_date)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    cur = get_db()
    try:
        cur.execute(f"""
            SELECT u.full_name, u.roll_number, c.name AS course_name, c.code,
                   s.session_date, s.start_time, a.status, a.remarks
            FROM attendance a
            JOIN sessions s ON a.session_id = s.id
            JOIN courses c ON s.course_id = c.id
            JOIN users u ON a.student_id = u.id
            {where}
            ORDER BY s.session_date DESC, u.full_name
        """, tuple(params))
        records = cur.fetchall()

        cur.execute(f"""
            SELECT u.full_name, u.roll_number, c.name AS course_name, c.code,
                   COUNT(*) AS total,
                   SUM(CASE WHEN a.status IN ('present','late') THEN 1 ELSE 0 END) AS present_count,
                   ROUND(100.0 * SUM(CASE WHEN a.status IN ('present','late') THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 1) AS pct
            FROM attendance a
            JOIN sessions s ON a.session_id = s.id
            JOIN courses c ON s.course_id = c.id
            JOIN users u ON a.student_id = u.id
            {where}
            GROUP BY u.id, u.full_name, u.roll_number, c.id, c.name, c.code
            ORDER BY pct ASC
        """, tuple(params))
        summary = cur.fetchall()

        courses  = []
        students = []
        if role in ('teacher', 'admin'):
            if role == 'teacher':
                cur.execute("""
                    SELECT c.id, c.name, c.code FROM courses c
                    JOIN teacher_courses tc ON tc.course_id=c.id WHERE tc.teacher_id=%s
                """, (uid,))
            else:
                cur.execute("SELECT id, name, code FROM courses ORDER BY name")
            courses = cur.fetchall()

            cur.execute("""
                SELECT id, full_name, roll_number FROM users
                WHERE role='student' AND is_active=1 ORDER BY full_name
            """)
            students = cur.fetchall()
    finally:
        cur.close()

    return render_template('report.html',
        records=records, summary=summary,
        courses=courses, students=students,
        filters={
            'course_id':  course_id,
            'student_id': student_id,
            'from_date':  from_date,
            'to_date':    to_date
        }
    )


# ─── Leave Requests ────────────────────────────────────────────────────────────
@app.route('/leave', methods=['GET'])
@login_required
def leave_list():
    uid  = session['user_id']
    role = session['role']
    cur  = get_db()
    try:
        if role == 'student':
            cur.execute("""
                SELECT lr.*, c.name AS course_name
                FROM leave_requests lr
                LEFT JOIN courses c ON lr.course_id=c.id
                WHERE lr.student_id=%s ORDER BY lr.created_at DESC
            """, (uid,))
        else:
            cur.execute("""
                SELECT lr.*, u.full_name AS student_name, u.roll_number,
                       c.name AS course_name
                FROM leave_requests lr
                JOIN users u ON lr.student_id=u.id
                LEFT JOIN courses c ON lr.course_id=c.id
                ORDER BY lr.created_at DESC
            """)
        leaves = cur.fetchall()

        courses = []
        if role == 'student':
            cur.execute("""
                SELECT c.id, c.name FROM enrollments e
                JOIN courses c ON e.course_id=c.id WHERE e.student_id=%s
            """, (uid,))
            courses = cur.fetchall()
    finally:
        cur.close()

    return render_template('leave.html', leaves=leaves, courses=courses)


@app.route('/leave/apply', methods=['POST'])
@login_required
@role_required('student')
def apply_leave():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data'})
    cur = get_db()
    try:
        course_id = data.get('course_id') or None
        if course_id == '':
            course_id = None
        cur.execute("""
            INSERT INTO leave_requests (student_id, course_id, from_date, to_date, reason)
            VALUES (%s,%s,%s,%s,%s)
        """, (
            session['user_id'], course_id,
            data['from_date'], data['to_date'], data['reason']
        ))
        mysql.connection.commit()
        return jsonify({'success': True})
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        cur.close()


@app.route('/leave/review/<int:leave_id>', methods=['POST'])
@login_required
@role_required('teacher', 'admin')
def review_leave(leave_id):
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data'})
    status = data.get('status')
    cur = get_db()
    try:
        cur.execute("""
            UPDATE leave_requests SET status=%s, reviewed_by=%s, reviewed_at=NOW()
            WHERE id=%s
        """, (status, session['user_id'], leave_id))
        mysql.connection.commit()

        cur.execute("SELECT student_id FROM leave_requests WHERE id=%s", (leave_id,))
        row = cur.fetchone()
        if row:
            notify(
                row['student_id'],
                f'Leave {status.capitalize()}',
                f'Your leave request has been {status}.',
                'success' if status == 'approved' else 'danger'
            )
        return jsonify({'success': True})
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        cur.close()


# ─── Users Management (Admin) ──────────────────────────────────────────────────
@app.route('/users')
@login_required
@role_required('admin')
def users_list():
    cur = get_db()
    try:
        cur.execute("SELECT * FROM users ORDER BY role, full_name")
        users = cur.fetchall()
    finally:
        cur.close()
    return render_template('users.html', users=users)


@app.route('/users/toggle/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin')
def toggle_user(user_id):
    cur = get_db()
    try:
        cur.execute("UPDATE users SET is_active = NOT is_active WHERE id=%s", (user_id,))
        mysql.connection.commit()
    finally:
        cur.close()
    return jsonify({'success': True})


# ─── Notifications ─────────────────────────────────────────────────────────────
@app.route('/notifications/read/<int:nid>', methods=['POST'])
@login_required
def read_notification(nid):
    cur = get_db()
    try:
        cur.execute(
            "UPDATE notifications SET is_read=1 WHERE id=%s AND user_id=%s",
            (nid, session['user_id'])
        )
        mysql.connection.commit()
    finally:
        cur.close()
    return jsonify({'success': True})


# ─── API endpoints ─────────────────────────────────────────────────────────────
@app.route('/api/attendance/chart')
@login_required
def api_chart():
    uid  = session['user_id']
    role = session['role']
    cur  = get_db()
    try:
        if role == 'student':
            cur.execute("""
                SELECT c.name AS course_name,
                  COUNT(*) AS total,
                  SUM(CASE WHEN a.status IN ('present','late') THEN 1 ELSE 0 END) AS present_count
                FROM attendance a
                JOIN sessions s ON a.session_id=s.id
                JOIN courses c ON s.course_id=c.id
                WHERE a.student_id=%s
                GROUP BY c.id, c.name
            """, (uid,))
        else:
            cur.execute("""
                SELECT c.name AS course_name,
                  COUNT(*) AS total,
                  SUM(CASE WHEN a.status IN ('present','late') THEN 1 ELSE 0 END) AS present_count
                FROM attendance a
                JOIN sessions s ON a.session_id=s.id
                JOIN courses c ON s.course_id=c.id
                GROUP BY c.id, c.name
            """)
        rows = cur.fetchall()
    finally:
        cur.close()

    labels = [r['course_name'] for r in rows]
    pcts   = [round((float(r['present_count'] or 0) / float(r['total'] or 1)) * 100, 1) for r in rows]
    return jsonify({'labels': labels, 'data': pcts})


@app.route('/api/attendance/trend')
@login_required
def api_trend():
    uid = session['user_id']
    cur = get_db()
    try:
        cur.execute("""
            SELECT DATE_FORMAT(s.session_date, '%%Y-%%m') AS month,
              ROUND(100.0 * SUM(CASE WHEN a.status IN ('present','late') THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 1) AS pct
            FROM attendance a
            JOIN sessions s ON a.session_id=s.id
            WHERE a.student_id=%s
            GROUP BY month
            ORDER BY month DESC
            LIMIT 6
        """, (uid,))
        rows = cur.fetchall()
    finally:
        cur.close()

    rows = list(reversed(rows))
    return jsonify({
        'labels': [r['month'] for r in rows],
        'data':   [float(r['pct'] or 0) for r in rows]
    })


# ─── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', code=404, message='Page not found'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500, message='Internal server error'), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
