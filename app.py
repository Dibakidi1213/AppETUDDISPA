import base64
import io
import os
import sqlite3
import uuid
from datetime import datetime
from functools import wraps

# import pandas as pd  # Moved to functions that use it
import qrcode
from flask import (
    Flask,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from flask_talisman import Talisman

from database import get_connection, init_db
from services.dispatch import distribute_students

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("APP_SECRET", "dev-key-change-me")

# Security headers
Talisman(app, content_security_policy=None)

# Gestionnaire d'erreur global
@app.errorhandler(500)
def handle_500_error(e):
    import traceback
    error_msg = str(e)
    traceback_str = traceback.format_exc()
    print(f"ERROR 500: {error_msg}")
    print(traceback_str)
    
    # Éviter les boucles de redirection en vérifiant la route actuelle
    current_route = request.endpoint if hasattr(request, 'endpoint') else None
    if current_route == 'index' or current_route == 'scan_presence_menu':
        # Si on est déjà sur index ou scan_presence_menu, retourner une page d'erreur simple
        return f"""
        <html>
        <head><title>Erreur</title></head>
        <body>
            <h1>Une erreur s'est produite</h1>
            <p>Veuillez réessayer plus tard ou contacter l'administrateur.</p>
            <p><a href="/login">Retour à la connexion</a></p>
        </body>
        </html>
        """, 500
    
    flash(f"Une erreur s'est produite. Veuillez réessayer.", "danger")
    if 'user_id' in session:
        return redirect(url_for('index'))
    else:
        return redirect(url_for('login'))

@app.errorhandler(404)
def handle_404_error(e):
    flash("Page introuvable.", "warning")
    if 'user_id' in session:
        return redirect(url_for('index'))
    else:
        return redirect(url_for('login'))

init_db()


@app.context_processor
def inject_unread_count():
    """Injecte le compteur de messages non lus dans tous les templates"""
    return {'unread_messages_count': get_unread_messages_count()}


def get_db():
    if "db" not in g:
        g.db = get_connection()
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _get_exams(conn, promotion_id=None):
    cur = conn.cursor()
    if promotion_id:
        cur.execute(
            """SELECT DISTINCT e.id, e.label, e.session_type,
               GROUP_CONCAT(p.name, ', ') as promotion_name
               FROM exams e 
               JOIN exam_promotions ep ON e.id = ep.exam_id
               JOIN promotions p ON ep.promotion_id = p.id
               WHERE ep.promotion_id = ?
               GROUP BY e.id, e.label, e.session_type
               ORDER BY e.session_type ASC, e.label ASC""",
            (promotion_id,)
        )
    else:
        cur.execute(
            """SELECT DISTINCT e.id, e.label, e.session_type,
               GROUP_CONCAT(p.name, ', ') as promotion_name
               FROM exams e 
               LEFT JOIN exam_promotions ep ON e.id = ep.exam_id
               LEFT JOIN promotions p ON ep.promotion_id = p.id
               GROUP BY e.id, e.label, e.session_type
               ORDER BY e.session_type ASC, e.label ASC"""
        )
    return cur.fetchall()


def _get_exam_or_latest(conn, exam_id=None):
    cur = conn.cursor()
    if exam_id:
        cur.execute(
            """SELECT e.id, e.label, e.session_type,
               GROUP_CONCAT(p.name, ', ') as promotion_name
               FROM exams e 
               LEFT JOIN exam_promotions ep ON e.id = ep.exam_id
               LEFT JOIN promotions p ON ep.promotion_id = p.id
               WHERE e.id = ?
               GROUP BY e.id, e.label, e.session_type""",
            (exam_id,)
        )
        return cur.fetchone()
    exams = _get_exams(conn)
    return exams[0] if exams else None


def login_required(f):
    """Décorateur pour protéger les routes nécessitant une authentification"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Vous devez vous connecter pour accéder à cette page.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Décorateur pour protéger les routes nécessitant des droits administrateur"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Vous devez vous connecter pour accéder à cette page.", "warning")
            return redirect(url_for('login'))
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT is_admin FROM users WHERE id = ?", (session['user_id'],))
        user = cur.fetchone()
        if not user or not user['is_admin']:
            flash("Accès refusé. Droits administrateur requis.", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def get_unread_messages_count():
    """Retourne le nombre de messages non lus pour l'utilisateur connecté"""
    if 'user_id' not in session:
        return 0
    
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    is_admin = session.get('is_admin', False)
    
    if is_admin:
        # Admins voient tous les messages non lus
        cur.execute("SELECT COUNT(*) as count FROM messages WHERE is_read = 0")
    else:
        # Surveillants voient les messages non lus des admins et des autres surveillants
        cur.execute("""
            SELECT COUNT(*) as count 
            FROM messages m
            JOIN users u_sender ON m.sender_id = u_sender.id
            WHERE (m.recipient_id = ? AND m.is_read = 0) OR (m.recipient_id IS NULL AND u_sender.is_admin = 1 AND m.is_read = 0)
        """, (user_id,))
    
    result = cur.fetchone()
    return result['count'] if result else 0


def create_notification(user_id, message):
    """Créer une notification pour un utilisateur"""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO notifications (user_id, message)
            VALUES (?, ?)
        """, (user_id, message))
        conn.commit()
    except Exception as e:
        print(f"Erreur lors de la création de notification: {e}")


def get_unread_notifications_count():
    """Retourne le nombre de notifications non lues pour l'utilisateur connecté"""
    if 'user_id' not in session:
        return 0
    
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    
    cur.execute("SELECT COUNT(*) as count FROM notifications WHERE user_id = ? AND is_read = 0", (user_id,))
    result = cur.fetchone()
    return result['count'] if result else 0


def get_recent_notifications(limit=5):
    """Retourne les dernières notifications pour l'utilisateur connecté"""
    if 'user_id' not in session:
        return []
    
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    
    cur.execute("""
        SELECT id, message, created_at, is_read
        FROM notifications 
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (user_id, limit))
    
    notifications_raw = cur.fetchall()
    return [dict(notif) for notif in notifications_raw]


def get_recent_unread_messages(limit=5):
    """Retourne les derniers messages non lus pour l'utilisateur connecté"""
    if 'user_id' not in session:
        return []
    
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    is_admin = session.get('is_admin', False)
    
    if is_admin:
        # Admins voient tous les messages non lus
        cur.execute("""
            SELECT m.id, m.subject, m.message, m.created_at, m.is_read,
                   u_sender.username as sender_name, u_recipient.username as recipient_name,
                   e.label as exam_label, r.name as room_name
            FROM messages m
            JOIN users u_sender ON m.sender_id = u_sender.id
            LEFT JOIN users u_recipient ON m.recipient_id = u_recipient.id
            LEFT JOIN exams e ON m.exam_id = e.id
            LEFT JOIN rooms r ON m.room_id = r.id
            WHERE m.is_read = 0
            ORDER BY m.created_at DESC
            LIMIT ?
        """, (limit,))
    else:
        # Surveillants voient les réponses non lues des admins et des autres surveillants
        cur.execute("""
            SELECT m.id, m.subject, m.message, m.created_at, m.is_read,
                   u_sender.username as sender_name, u_recipient.username as recipient_name,
                   e.label as exam_label, r.name as room_name
            FROM messages m
            JOIN users u_sender ON m.sender_id = u_sender.id
            LEFT JOIN users u_recipient ON m.recipient_id = u_recipient.id
            LEFT JOIN exams e ON m.exam_id = e.id
            LEFT JOIN rooms r ON m.room_id = r.id
            WHERE (m.recipient_id = ? AND m.is_read = 0) OR (m.recipient_id IS NULL AND u_sender.is_admin = 1 AND m.is_read = 0)
            ORDER BY m.created_at DESC
            LIMIT ?
        """, (user_id, limit))
    
    messages_raw = cur.fetchall()
    return [dict(msg) for msg in messages_raw]


@app.context_processor
def inject_unread_count():
    """Injecte le compteur de messages non lus dans tous les templates"""
    return {
        'unread_messages_count': get_unread_messages_count(),
        'recent_unread_messages': get_recent_unread_messages(),
        'unread_notifications_count': get_unread_notifications_count(),
        'recent_notifications': get_recent_notifications()
    }


@app.route("/")
@login_required
def index():
    """Page d'accueil avec statistiques"""
    conn = get_db()
    cur = conn.cursor()
    
    # Calculer les statistiques globales
    stats = {}
    
    try:
        # Total des promotions
        cur.execute("SELECT COUNT(*) as total FROM promotions")
        result = cur.fetchone()
        stats['total_promotions'] = result['total'] if result else 0
        
        # Total des étudiants
        cur.execute("SELECT COUNT(*) as total FROM students")
        result = cur.fetchone()
        stats['total_students'] = result['total'] if result else 0
        
        # Statistiques supplémentaires
        cur.execute("SELECT COUNT(*) as total FROM assignments")
        result = cur.fetchone()
        stats['total_assignments'] = result['total'] if result else 0
        
        cur.execute("SELECT COUNT(*) as total FROM presence WHERE status = 'present'")
        result = cur.fetchone()
        stats['total_present'] = result['total'] if result else 0
    except Exception as e:
        # En cas d'erreur, initialiser avec des valeurs par défaut
        stats = {
            'total_promotions': 0,
            'total_students': 0,
            'total_assignments': 0,
            'total_present': 0
        }
        flash(f"Erreur lors du chargement des statistiques: {str(e)}", "warning")
    
    return render_template("index.html", stats=stats, username=session.get('username'))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        
        if not username or not password:
            flash("Veuillez remplir tous les champs.", "danger")
            return render_template("login.html")
        
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, username, password_hash, is_admin FROM users WHERE username = ?", (username,))
        user = cur.fetchone()
        
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["is_admin"] = bool(user["is_admin"])
            session["is_supervisor"] = not bool(user["is_admin"])  # Si pas admin = surveillant
            flash(f"Bienvenue, {user['username']}!", "success")
            
            # Rediriger vers le tableau de bord approprié
            if user["is_admin"]:
                return redirect(url_for("index"))
            else:
                return redirect(url_for("supervisor_dashboard"))
        else:
            flash("Nom d'utilisateur ou mot de passe incorrect.", "danger")
    
    # Envoyer une réponse simple pour éviter les problèmes de cache
    response = make_response(render_template("login.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/logout")
def logout():
    session.clear()
    flash("Vous avez été déconnecté.", "info")
    return redirect(url_for("login"))


@app.route("/supervisor/dashboard")
@login_required
def supervisor_dashboard():
    """Tableau de bord pour les surveillants - affiche uniquement leurs locaux"""
    if session.get("is_admin"):
        # Les administrateurs ne devraient pas voir cette page
        return redirect(url_for("index"))
    
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get("user_id")
    
    # Récupérer tous les examens affectés à ce surveillant
    cur.execute("""
        SELECT DISTINCT e.id, e.label, e.session_type
        FROM exams e
        INNER JOIN supervisor_assignments sa ON e.id = sa.exam_id
        WHERE sa.user_id = ?
        ORDER BY e.label ASC
    """, (user_id,))
    exams = cur.fetchall()
    
    # Récupérer les locaux et statistiques pour chaque examen
    exams_with_rooms = []
    for exam in exams:
        cur.execute("""
            SELECT DISTINCT r.id, r.name, r.benches, r.students_per_bench
            FROM rooms r
            INNER JOIN supervisor_assignments sa ON r.id = sa.room_id
            WHERE sa.user_id = ? AND sa.exam_id = ?
            ORDER BY r.name ASC
        """, (user_id, exam['id']))
        rooms = cur.fetchall()
        
        rooms_with_stats = []
        for room in rooms:
            # Vérifier si le surveillant est chef de salle pour ce local
            cur.execute("""
                SELECT is_room_leader FROM supervisor_assignments 
                WHERE user_id = ? AND exam_id = ? AND room_id = ?
            """, (user_id, exam['id'], room['id']))
            leader_status = cur.fetchone()
            is_leader = leader_status['is_room_leader'] if leader_status else False
            
            # Statistiques du local
            cur.execute("""
                SELECT 
                    COUNT(DISTINCT a.id) as total_assigned,
                    COUNT(DISTINCT CASE WHEN p.status = 'present' THEN a.id END) as present_count,
                    COUNT(DISTINCT CASE WHEN p.status = 'absent' THEN a.id END) as absent_count
                FROM assignments a
                LEFT JOIN presence p ON a.id = p.assignment_id
                WHERE a.exam_id = ? AND a.room_id = ?
            """, (exam['id'], room['id']))
            stats = cur.fetchone()
            
            room_data = dict(room)
            room_data['total_assigned'] = stats['total_assigned'] or 0
            room_data['present_count'] = stats['present_count'] or 0
            room_data['absent_count'] = stats['absent_count'] or 0
            room_data['is_room_leader'] = is_leader
            rooms_with_stats.append(room_data)
        
        exams_with_rooms.append({
            'exam': exam,
            'rooms': rooms_with_stats
        })
    
    return render_template(
        "supervisor_dashboard.html",
        username=session.get('username'),
        exams_with_rooms=exams_with_rooms
    )


@app.route("/supervisor/room/<int:room_id>/exam/<int:exam_id>/scan", methods=["GET", "POST"])
@login_required
def supervisor_room_scan(room_id, exam_id):
    """Pointage QR pour les surveillants - seulement leur local affecté"""
    if session.get("is_admin"):
        flash("Les administrateurs ne peuvent pas accéder à cette page.", "danger")
        return redirect(url_for("index"))
    
    user_id = session.get("user_id")
    conn = get_db()
    cur = conn.cursor()
    
    # Vérifier que le surveillant est affecté à ce local et cet examen ET qu'il est chef de salle
    cur.execute("""
        SELECT id FROM supervisor_assignments 
        WHERE user_id = ? AND exam_id = ? AND room_id = ? AND is_room_leader = 1
    """, (user_id, exam_id, room_id))
    
    if not cur.fetchone():
        flash("Seul le chef de salle peut marquer la présence dans ce local.", "danger")
        return redirect(url_for("supervisor_dashboard"))
    
    # Récupérer les infos du local et de l'examen
    cur.execute("SELECT * FROM rooms WHERE id = ?", (room_id,))
    room = cur.fetchone()
    
    cur.execute("""
        SELECT e.id, e.label, e.session_type,
               GROUP_CONCAT(p.name, ', ') as promotion_name
        FROM exams e
        LEFT JOIN exam_promotions ep ON e.id = ep.exam_id
        LEFT JOIN promotions p ON ep.promotion_id = p.id
        WHERE e.id = ?
        GROUP BY e.id
    """, (exam_id,))
    exam = cur.fetchone()
    
    if not room or not exam:
        flash("Local ou examen introuvable.", "danger")
        return redirect(url_for("supervisor_dashboard"))
    
    # Récupérer les étudiants affectés à ce local pour cet examen
    cur.execute("""
        SELECT a.id, a.qr_token, a.seat_number, s.full_name, s.matricule,
               COALESCE(p.status, 'absent') as presence_status,
               p.scanned_at
        FROM assignments a
        INNER JOIN students s ON a.student_id = s.id
        LEFT JOIN presence p ON a.id = p.assignment_id
        WHERE a.exam_id = ? AND a.room_id = ?
        ORDER BY a.seat_number ASC
    """, (exam_id, room_id))
    assignments = cur.fetchall()
    
    result = None
    if request.method == "POST":
        qr_token = request.form.get("qr_token", "").strip()
        
        # Chercher l'étudiant par QR token
        cur.execute("""
            SELECT a.id, a.student_id, s.full_name, p.status
            FROM assignments a
            INNER JOIN students s ON a.student_id = s.id
            LEFT JOIN presence p ON a.id = p.assignment_id
            WHERE a.qr_token = ? AND a.exam_id = ? AND a.room_id = ?
        """, (qr_token, exam_id, room_id))
        
        assignment = cur.fetchone()
        
        if assignment:
            # Mettre à jour la présence
            cur.execute("""
                INSERT INTO presence (assignment_id, status, scanned_at, scanned_by)
                VALUES (?, ?, datetime('now'), ?)
                ON CONFLICT(assignment_id) DO UPDATE SET 
                    status = 'present',
                    scanned_at = datetime('now'),
                    scanned_by = ?
            """, (assignment['id'], 'present', user_id, user_id))
            conn.commit()
            
            result = {
                'success': True,
                'student_name': assignment['full_name'],
                'message': f"Présence enregistrée pour {assignment['full_name']}"
            }
            
            # Actualiser la liste
            cur.execute("""
                SELECT a.id, a.qr_token, a.seat_number, s.full_name, s.matricule,
                       COALESCE(p.status, 'absent') as presence_status,
                       p.scanned_at
                FROM assignments a
                INNER JOIN students s ON a.student_id = s.id
                LEFT JOIN presence p ON a.id = p.assignment_id
                WHERE a.exam_id = ? AND a.room_id = ?
                ORDER BY a.seat_number ASC
            """, (exam_id, room_id))
            assignments = cur.fetchall()
        else:
            result = {
                'success': False,
                'message': "Étudiant non trouvé ou absent de ce local."
            }
    
    return render_template(
        "supervisor_room_scan.html",
        room=room,
        exam=exam,
        assignments=assignments,
        result=result,
        username=session.get('username'),
        today=datetime.now().strftime('%Y-%m-%d')
    )


@app.route("/supervisor/room/<int:room_id>/exam/<int:exam_id>/list")
@login_required
def supervisor_room_list(room_id, exam_id):
    """Liste de présence pour les surveillants - seulement leur local affecté"""
    try:
        if session.get("is_admin"):
            flash("Les administrateurs ne peuvent pas accéder à cette page.", "danger")
            return redirect(url_for("index"))
        
        user_id = session.get("user_id")
        conn = get_db()
        cur = conn.cursor()
        
        # Vérifier que le surveillant est affecté à ce local et cet examen
        cur.execute("""
            SELECT id FROM supervisor_assignments 
            WHERE user_id = ? AND exam_id = ? AND room_id = ?
        """, (user_id, exam_id, room_id))
        
        if not cur.fetchone():
            flash("Vous n'êtes pas autorisé à accéder à ce local.", "danger")
            return redirect(url_for("supervisor_dashboard"))
        
        # Récupérer les infos
        cur.execute("SELECT * FROM rooms WHERE id = ?", (room_id,))
        room = cur.fetchone()
        
        cur.execute("""
            SELECT e.id, e.label, e.session_type
            FROM exams e
            WHERE e.id = ?
        """, (exam_id,))
        exam = cur.fetchone()
        
        if not room or not exam:
            flash("Local ou examen introuvable.", "danger")
            return redirect(url_for("supervisor_dashboard"))
        
        # Récupérer les étudiants affectés
        cur.execute("""
            SELECT a.id as assignment_id, a.seat_number, s.full_name, s.matricule, pr.name as promotion_name,
                   COALESCE(p.status, 'absent') as presence_status
            FROM assignments a
            INNER JOIN students s ON a.student_id = s.id
            LEFT JOIN promotions pr ON s.promotion_id = pr.id
            LEFT JOIN presence p ON a.id = p.assignment_id
            WHERE a.exam_id = ? AND a.room_id = ?
            ORDER BY a.seat_number ASC
        """, (exam_id, room_id))
        students = cur.fetchall()
        
        return render_template(
            "supervisor_room_list.html",
            room=room,
            exam=exam,
            students=students,
            username=session.get('username'),
            today=datetime.now().strftime('%Y-%m-%d')
        )
    except Exception as e:
        import traceback
        print(f"Error in supervisor_room_list: {e}")
        print(traceback.format_exc())
        raise


@app.route("/supervisor/room/<int:room_id>/exam/<int:exam_id>/update_presence/<int:assignment_id>", methods=["POST"])
@login_required
def supervisor_update_presence(room_id, exam_id, assignment_id):
    """Mettre à jour la présence d'un étudiant (surveillant seulement)"""
    if session.get("is_admin"):
        return jsonify({"success": False, "message": "Les administrateurs ne peuvent pas accéder à cette page."}), 403
    
    user_id = session.get("user_id")
    conn = get_db()
    cur = conn.cursor()
    
    # Vérifier que le surveillant est affecté à ce local et cet examen ET qu'il est chef de salle
    cur.execute("""
        SELECT id FROM supervisor_assignments 
        WHERE user_id = ? AND exam_id = ? AND room_id = ? AND is_room_leader = 1
    """, (user_id, exam_id, room_id))
    
    if not cur.fetchone():
        return jsonify({"success": False, "message": "Seul le chef de salle peut marquer la présence dans ce local."}), 403
    
    # Récupérer le statut depuis le formulaire
    status = request.form.get("status", "").strip()
    exam_date = request.form.get("exam_date", "").strip()
    if status not in ['present', 'absent']:
        return jsonify({"success": False, "message": "Statut invalide."}), 400
    
    # Gérer la date
    if exam_date:
        try:
            exam_dt = datetime.strptime(exam_date, '%Y-%m-%d')
            scanned_at = exam_dt.isoformat()
            scan_date = exam_date
        except ValueError:
            return jsonify({"success": False, "message": "Date invalide."}), 400
    else:
        now = datetime.utcnow()
        scanned_at = now.isoformat()
        scan_date = now.strftime("%Y-%m-%d")
    
    # Mettre à jour ou insérer la présence
    try:
        cur.execute("""
            INSERT INTO presence (assignment_id, status, scanned_at, scan_date, scanned_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(assignment_id) DO UPDATE SET 
                status = ?,
                scanned_at = ?,
                scan_date = ?,
                scanned_by = ?
        """, (assignment_id, status, scanned_at, scan_date, user_id, status, scanned_at, scan_date, user_id))
        conn.commit()
        return jsonify({"success": True, "message": f"Statut mis à jour: {status.upper()}."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Erreur lors de la mise à jour: {str(e)}"}), 500


@app.route("/supervisor/room/<int:room_id>/exam/<int:exam_id>/save_all_presence", methods=["POST"])
@login_required
def supervisor_save_all_presence(room_id, exam_id):
    """Enregistrer toutes les présences pour une date donnée (superviseur seulement)"""
    try:
        if session.get("is_admin"):
            return jsonify({"success": False, "message": "Les administrateurs ne peuvent pas accéder à cette page."}), 403
        
        user_id = session.get("user_id")
        conn = get_db()
        cur = conn.cursor()
        
        # Vérifier que le surveillant est affecté à ce local et cet examen
        cur.execute("""
            SELECT id FROM supervisor_assignments 
            WHERE user_id = ? AND exam_id = ? AND room_id = ?
        """, (user_id, exam_id, room_id))
        
        if not cur.fetchone():
            return jsonify({"success": False, "message": "Seul le chef de salle peut marquer la présence dans ce local."}), 403
        
        # Récupérer les données JSON
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Données JSON requises."}), 400
        
        exam_date = data.get('exam_date', '').strip()
        presences = data.get('presences', [])
        
        if not exam_date:
            return jsonify({"success": False, "message": "Date d'examen requise."}), 400
        
        if not presences:
            return jsonify({"success": False, "message": "Aucune présence à enregistrer."}), 400
        
        # Valider la date
        try:
            exam_dt = datetime.strptime(exam_date, '%Y-%m-%d')
            scanned_at = exam_dt.isoformat()
            scan_date = exam_date
        except ValueError:
            return jsonify({"success": False, "message": "Date invalide."}), 400
        
        # Vérifier que toutes les présences appartiennent à ce local et cet examen
        assignment_ids = [p['assignment_id'] for p in presences]
        placeholders = ','.join('?' * len(assignment_ids))
        
        cur.execute(f"""
            SELECT id FROM assignments 
            WHERE id IN ({placeholders}) AND room_id = ? AND exam_id = ?
        """, assignment_ids + [room_id, exam_id])
        
        valid_assignments = {row['id'] for row in cur.fetchall()}
        
        if len(valid_assignments) != len(assignment_ids):
            return jsonify({"success": False, "message": "Certaines affectations ne sont pas valides pour ce local."}), 400
        
        # Enregistrer toutes les présences
        saved_count = 0
        try:
            for presence in presences:
                assignment_id = presence['assignment_id']
                status = presence['status']
                
                if status not in ['present', 'absent']:
                    continue
                
                # Insérer ou mettre à jour la présence
                cur.execute("""
                    INSERT INTO presence (assignment_id, status, scanned_at, scan_date, scanned_by)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(assignment_id) DO UPDATE SET 
                        status = ?,
                        scanned_at = ?,
                        scan_date = ?,
                        scanned_by = ?
                """, (assignment_id, status, scanned_at, scan_date, user_id, status, scanned_at, scan_date, user_id))
                saved_count += 1
            
            conn.commit()
            return jsonify({"success": True, "message": f"{saved_count} présences enregistrées.", "saved_count": saved_count})
        except Exception as e:
            conn.rollback()
            return jsonify({"success": False, "message": f"Erreur lors de l'enregistrement: {str(e)}"}), 500
    except Exception as e:
        import traceback
        print(f"Error in supervisor_save_all_presence: {e}")
        print(traceback.format_exc())
        return jsonify({"success": False, "message": "Erreur interne du serveur."}), 500


@app.route("/promotions", methods=["GET", "POST"])
@login_required
def promotions():
    conn = get_db()
    cur = conn.cursor()
    if request.method == "POST" and "promotion_id" not in request.form:
        # Création d'une nouvelle promotion
        name = request.form.get("name", "").strip()
        if name:
            try:
                cur.execute("INSERT INTO promotions (name) VALUES (?)", (name,))
                conn.commit()
                flash("Promotion créée avec succès.", "success")
            except Exception:
                flash("Cette promotion existe déjà.", "danger")
        else:
            flash("Le nom de la promotion est requis.", "danger")
    cur.execute("SELECT id, name FROM promotions ORDER BY name")
    promotions_list = cur.fetchall()
    
    # Compter les étudiants pour chaque promotion
    promotions_with_count = []
    for promo in promotions_list:
        cur.execute("SELECT COUNT(*) as count FROM students WHERE promotion_id = ?", (promo['id'],))
        count = cur.fetchone()['count']
        promotions_with_count.append({
            'id': promo['id'],
            'name': promo['name'],
            'student_count': count
        })
    
    return render_template("promotions.html", promotions=promotions_with_count)


@app.route("/promotions/<int:promotion_id>/students", methods=["GET", "POST"])
@login_required
def promotion_students(promotion_id):
    """Afficher la liste des étudiants d'une promotion et permettre l'ajout manuel"""
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer la promotion
    cur.execute("SELECT id, name FROM promotions WHERE id = ?", (promotion_id,))
    promotion = cur.fetchone()
    
    if not promotion:
        flash("Promotion introuvable.", "danger")
        return redirect(url_for("promotions"))
    
    # Gérer l'ajout d'un étudiant
    if request.method == "POST" and "full_name" in request.form:
        full_name = request.form.get("full_name", "").strip()
        matricule = request.form.get("matricule", "").strip()
        sexe = request.form.get("sexe", "").strip() or None
        
        if not full_name:
            flash("Le nom complet est obligatoire.", "danger")
        else:
            try:
                # Générer un matricule si non fourni
                if not matricule:
                    matricule = f"ETU-{uuid.uuid4().hex[:8].upper()}"
                
                # Insérer l'étudiant
                cur.execute("""
                    INSERT INTO students (matricule, full_name, sexe, promotion_id, section_id)
                    VALUES (?, ?, ?, ?, ?)
                """, (matricule, full_name, sexe, promotion_id, None))
                conn.commit()
                flash(f"Étudiant '{full_name}' ajouté avec succès.", "success")
                return redirect(url_for("promotion_students", promotion_id=promotion_id))
            except sqlite3.IntegrityError:
                flash(f"Le matricule '{matricule}' existe déjà. Veuillez en choisir un autre.", "danger")
            except Exception as e:
                flash(f"Erreur lors de l'ajout: {str(e)}", "danger")
    
    # Récupérer les étudiants de cette promotion
    cur.execute("""
        SELECT id, matricule, full_name, sexe 
        FROM students 
        WHERE promotion_id = ? 
        ORDER BY full_name
    """, (promotion_id,))
    students = cur.fetchall()
    
    return render_template("promotion_students.html", promotion=promotion, students=students)


@app.route("/promotions/<int:promotion_id>/students/clear", methods=["GET", "POST"])
@admin_required
def clear_promotion_students(promotion_id):
    """Effacer tous les étudiants d'une promotion (admin seulement)"""
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer la promotion
    cur.execute("SELECT id, name FROM promotions WHERE id = ?", (promotion_id,))
    promotion = cur.fetchone()
    
    if not promotion:
        flash("Promotion introuvable.", "danger")
        return redirect(url_for("promotions"))
    
    # Compter les étudiants pour l'affichage
    cur.execute("SELECT COUNT(*) as count FROM students WHERE promotion_id = ?", (promotion_id,))
    student_count = cur.fetchone()['count']
    
    if request.method == "POST":
        confirm = request.form.get("confirm", "").strip()
        admin_password = request.form.get("admin_password", "")
        
        # Vérifier la confirmation
        if confirm != "EFFACER":
            flash("Confirmation incorrecte. Tapez 'EFFACER' pour confirmer.", "danger")
            return render_template("clear_promotion_students.html", promotion=promotion, student_count=student_count)
        
        # Vérifier le mot de passe administrateur
        if not admin_password:
            flash("Le mot de passe administrateur est requis.", "danger")
            return render_template("clear_promotion_students.html", promotion=promotion, student_count=student_count)
        
        # Vérifier que le mot de passe est correct
        cur.execute("SELECT password_hash FROM users WHERE id = ?", (session['user_id'],))
        user = cur.fetchone()
        
        if not user or not check_password_hash(user['password_hash'], admin_password):
            flash("Mot de passe administrateur incorrect.", "danger")
            return render_template("clear_promotion_students.html", promotion=promotion, student_count=student_count)
        
        try:
            if student_count == 0:
                flash("Cette promotion ne contient aucun étudiant.", "info")
                return redirect(url_for("promotion_students", promotion_id=promotion_id))
            
            # Supprimer tous les étudiants de cette promotion
            # Les affectations et présences seront supprimées automatiquement grâce aux CASCADE
            cur.execute("DELETE FROM students WHERE promotion_id = ?", (promotion_id,))
            conn.commit()
            
            flash(f"Tous les étudiants ({student_count}) de la promotion '{promotion['name']}' ont été supprimés avec succès.", "success")
            return redirect(url_for("promotion_students", promotion_id=promotion_id))
        except Exception as e:
            conn.rollback()
            flash(f"Erreur lors de la suppression: {str(e)}", "danger")
            return redirect(url_for("promotion_students", promotion_id=promotion_id))
    
    # GET: Afficher le formulaire de confirmation
    return render_template("clear_promotion_students.html", promotion=promotion, student_count=student_count)


@app.route("/promotions/<int:promotion_id>/edit", methods=["GET", "POST"])
@login_required
def edit_promotion(promotion_id):
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer la promotion
    cur.execute("SELECT id, name FROM promotions WHERE id = ?", (promotion_id,))
    promotion = cur.fetchone()
    
    if not promotion:
        flash("Promotion introuvable.", "danger")
        return redirect(url_for("promotions"))
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            try:
                cur.execute("UPDATE promotions SET name = ? WHERE id = ?", (name, promotion_id))
                conn.commit()
                flash("Promotion modifiée avec succès.", "success")
                return redirect(url_for("promotions"))
            except Exception as e:
                flash(f"Erreur lors de la modification: {str(e)}", "danger")
        else:
            flash("Le nom de la promotion est requis.", "danger")
    
    return render_template("edit_promotion.html", promotion=promotion)


@app.route("/promotions/<int:promotion_id>/delete", methods=["GET"])
@login_required
def delete_promotion(promotion_id):
    conn = get_db()
    cur = conn.cursor()
    
    # Vérifier si la promotion existe
    cur.execute("SELECT id, name FROM promotions WHERE id = ?", (promotion_id,))
    promotion = cur.fetchone()
    
    if not promotion:
        flash("Promotion introuvable.", "danger")
        return redirect(url_for("promotions"))
    
    # Vérifier si la promotion est utilisée (étudiants, examens, etc.)
    cur.execute("SELECT COUNT(*) as count FROM students WHERE promotion_id = ?", (promotion_id,))
    students_count = cur.fetchone()["count"]
    
    if students_count > 0:
        flash(f"Impossible de supprimer la promotion '{promotion['name']}' car elle contient {students_count} étudiant(s).", "danger")
        return redirect(url_for("promotions"))
    
    try:
        cur.execute("DELETE FROM promotions WHERE id = ?", (promotion_id,))
        conn.commit()
        flash(f"Promotion '{promotion['name']}' supprimée avec succès.", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")
    
    return redirect(url_for("promotions"))


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload_students():
    import pandas as pd
    
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer toutes les promotions pour le formulaire
    cur.execute("SELECT id, name FROM promotions ORDER BY name")
    promotions = cur.fetchall()
    
    if request.method == "POST":
        promotion_id = request.form.get("promotion_id")
        file = request.files.get("excel")
        
        if not promotion_id:
            flash("Veuillez sélectionner une promotion.", "danger")
            stats_cur = conn.cursor()
            stats_cur.execute("SELECT COUNT(*) as c FROM students")
            total = stats_cur.fetchone()["c"]
            return render_template("upload.html", total=total, promotions=promotions)
            
        if not file:
            flash("Veuillez sélectionner un fichier Excel.", "danger")
            stats_cur = conn.cursor()
            stats_cur.execute("SELECT COUNT(*) as c FROM students")
            total = stats_cur.fetchone()["c"]
            return render_template("upload.html", total=total, promotions=promotions)
            
        try:
            df = pd.read_excel(file)
        except Exception:
            flash("Impossible de lire le fichier. Assurez-vous du format Excel.", "danger")
            stats_cur = conn.cursor()
            stats_cur.execute("SELECT COUNT(*) as c FROM students")
            total = stats_cur.fetchone()["c"]
            return render_template("upload.html", total=total, promotions=promotions)

        required_cols = {"nom"}
        # Convertir toutes les colonnes en string pour éviter les erreurs
        cols = {str(c).lower().strip(): c for c in df.columns}
        if "nom" not in cols:
            flash("Colonne obligatoire: nom. Optionnel: sexe.", "danger")
            stats_cur = conn.cursor()
            stats_cur.execute("SELECT COUNT(*) as c FROM students")
            total = stats_cur.fetchone()["c"]
            return render_template("upload.html", total=total, promotions=promotions)

        promo_id = int(promotion_id)
        # Vérifier que la promotion existe
        cur.execute("SELECT id FROM promotions WHERE id=?", (promo_id,))
        if not cur.fetchone():
            flash("Promotion invalide.", "danger")
            stats_cur = conn.cursor()
            stats_cur.execute("SELECT COUNT(*) as c FROM students")
            total = stats_cur.fetchone()["c"]
            return render_template("upload.html", total=total, promotions=promotions)

        inserted = 0
        for _, row in df.iterrows():
            nom_val = row[cols["nom"]]
            sexe_val = row[cols["sexe"]] if "sexe" in cols else None

            nom = "" if pd.isna(nom_val) else str(nom_val).strip()
            sexe = None if pd.isna(sexe_val) else str(sexe_val).strip()

            if not nom:
                continue

            # Générer un matricule unique si nécessaire
            matricule = f"ETU-{uuid.uuid4().hex[:8].upper()}"

            # Insérer l'étudiant
            cur.execute(
                """
                INSERT INTO students (matricule, full_name, sexe, promotion_id, section_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (matricule, nom, sexe, promo_id, None),
            )
            inserted += 1
        conn.commit()
        flash(f"{inserted} étudiants importés pour cette promotion.", "success")
        return redirect(url_for("upload_students"))
    
    stats_cur = conn.cursor()
    stats_cur.execute("SELECT COUNT(*) as c FROM students")
    total = stats_cur.fetchone()["c"]
    return render_template("upload.html", total=total, promotions=promotions)


@app.route("/rooms", methods=["GET", "POST"])
@login_required
def rooms():
    conn = get_db()
    cur = conn.cursor()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        benches = int(request.form.get("benches", 0) or 0)
        per_bench = int(request.form.get("per_bench", 1) or 1)
        if name and benches > 0 and per_bench > 0:
            try:
                cur.execute(
                    "INSERT INTO rooms (name, benches, students_per_bench) VALUES (?, ?, ?)",
                    (name, benches, per_bench),
                )
                conn.commit()
                flash("Local ajouté.", "success")
            except Exception:
                flash("Ce nom de local existe déjà.", "danger")
        else:
            flash("Complétez le formulaire.", "danger")
    cur.execute("SELECT * FROM rooms ORDER BY name")
    rooms_list = cur.fetchall()
    
    # Récupérer tous les examens pour la sélection
    exams_list = _get_exams(conn)
    exam_id = request.args.get("exam_id")
    exam = _get_exam_or_latest(conn, exam_id) if exam_id else _get_exam_or_latest(conn)
    
    # Calculer les statistiques des locaux
    stats = {}
    
    try:
        # Statistiques globales
        cur.execute("SELECT COUNT(*) as total FROM rooms")
        result = cur.fetchone()
        stats['total_rooms'] = result['total'] if result else 0
        
        cur.execute("SELECT SUM(benches * students_per_bench) as total_capacity FROM rooms")
        result = cur.fetchone()
        total_capacity = result['total_capacity'] if result else None
        stats['total_capacity'] = total_capacity if total_capacity else 0
    except Exception as e:
        stats['total_rooms'] = 0
        stats['total_capacity'] = 0
    
    # Statistiques par local (pour l'examen sélectionné si disponible)
    rooms_with_stats = []
    for room in rooms_list:
        room_stat = dict(room)
        room_stat['capacity'] = room['benches'] * room['students_per_bench']
        
        # Si un examen est sélectionné, calculer les statistiques pour cet examen
        if exam:
            # Nombre d'étudiants affectés dans ce local pour cet examen
            cur.execute("""
                SELECT COUNT(*) as assigned_count
                FROM assignments
                WHERE room_id = ? AND exam_id = ?
            """, (room['id'], exam['id']))
            result = cur.fetchone()
            assigned = result['assigned_count'] if result else 0
            room_stat['assigned_count'] = assigned
            room_stat['available_seats'] = room_stat['capacity'] - assigned
            room_stat['occupancy_rate'] = round((assigned / room_stat['capacity'] * 100) if room_stat['capacity'] > 0 else 0, 1)
            
            # Nombre de présences enregistrées
            cur.execute("""
                SELECT COUNT(*) as present_count
                FROM presence p
                JOIN assignments a ON p.assignment_id = a.id
                WHERE a.room_id = ? AND a.exam_id = ? AND p.status = 'present'
            """, (room['id'], exam['id']))
            result = cur.fetchone()
            present = result['present_count'] if result else 0
            room_stat['present_count'] = present
            room_stat['absent_count'] = assigned - present if assigned >= present else 0
        else:
            # Statistiques globales (tous examens confondus)
            cur.execute("""
                SELECT COUNT(DISTINCT a.id) as total_assignments
                FROM assignments a
                WHERE a.room_id = ?
            """, (room['id'],))
            result = cur.fetchone()
            total_assignments = result['total_assignments'] if result else 0
            room_stat['total_assignments'] = total_assignments
            
            # Nombre d'examens où ce local a été utilisé
            cur.execute("""
                SELECT COUNT(DISTINCT exam_id) as exams_count
                FROM assignments
                WHERE room_id = ?
            """, (room['id'],))
            result = cur.fetchone()
            exams_count = result['exams_count'] if result else 0
            room_stat['exams_count'] = exams_count
        
        rooms_with_stats.append(room_stat)
    
    # Statistiques globales pour l'examen sélectionné
    exam_stats = None
    if exam:
        cur.execute("""
            SELECT 
                COUNT(DISTINCT a.room_id) as rooms_used,
                COUNT(a.id) as total_assigned,
                SUM(r.benches * r.students_per_bench) as total_capacity_used,
                COUNT(p.id) as total_present
            FROM assignments a
            JOIN rooms r ON a.room_id = r.id
            LEFT JOIN presence p ON p.assignment_id = a.id AND p.status = 'present'
            WHERE a.exam_id = ?
        """, (exam['id'],))
        exam_stats_row = cur.fetchone()
        if exam_stats_row:
            exam_stats = dict(exam_stats_row)
            total_capacity_used = exam_stats.get('total_capacity_used') or 0
            total_assigned = exam_stats.get('total_assigned') or 0
            
            if total_capacity_used:
                exam_stats['total_available'] = total_capacity_used - total_assigned
                exam_stats['occupancy_rate'] = round((total_assigned / total_capacity_used * 100) if total_capacity_used > 0 else 0, 1)
            else:
                exam_stats['total_available'] = 0
                exam_stats['occupancy_rate'] = 0
    
    # Récupérer les surveillants assignés pour chaque local (si un examen est sélectionné)
    supervisors_by_room = {}
    cur.execute("SELECT id, username, is_admin FROM users ORDER BY username")
    all_users = cur.fetchall()
    
    if exam:
        # Récupérer les surveillants assignés pour chaque local
        for room in rooms_with_stats:
            cur.execute("""
                SELECT u.id, u.username, sa.id as assignment_id
                FROM supervisor_assignments sa
                JOIN users u ON sa.user_id = u.id
                WHERE sa.room_id = ? AND sa.exam_id = ?
                ORDER BY u.username
            """, (room['id'], exam['id']))
            supervisors_by_room[room['id']] = cur.fetchall()
    
    return render_template("rooms.html", 
                         rooms=rooms_with_stats, 
                         exam=exam, 
                         exams=exams_list,
                         stats=stats,
                         exam_stats=exam_stats,
                         supervisors_by_room=supervisors_by_room,
                         all_users=all_users)


@app.route("/rooms/<int:room_id>/supervisors", methods=["POST"])
@admin_required
def assign_supervisors(room_id):
    """Assigner ou désassigner des surveillants à un local pour un examen"""
    conn = get_db()
    cur = conn.cursor()
    
    exam_id = request.form.get("exam_id")
    action = request.form.get("action")  # "assign" ou "remove"
    user_id = request.form.get("user_id")
    assignment_id = request.form.get("assignment_id")  # Pour la suppression
    
    if not exam_id:
        flash("Examen requis.", "danger")
        return redirect(url_for("rooms"))
    
    try:
        if action == "assign" and user_id:
            # Assigner un surveillant
            cur.execute("""
                INSERT OR IGNORE INTO supervisor_assignments (user_id, exam_id, room_id)
                VALUES (?, ?, ?)
            """, (user_id, exam_id, room_id))
            conn.commit()
            flash("Surveillant assigné avec succès.", "success")
        elif action == "remove" and assignment_id:
            # Retirer un surveillant
            cur.execute("DELETE FROM supervisor_assignments WHERE id = ?", (assignment_id,))
            conn.commit()
            flash("Surveillant retiré avec succès.", "success")
        else:
            flash("Action invalide.", "danger")
    except Exception as e:
        conn.rollback()
        flash(f"Erreur: {str(e)}", "danger")
    
    return redirect(url_for("rooms", exam_id=exam_id))


@app.route("/rooms/<int:room_id>/edit", methods=["GET", "POST"])
@login_required
def edit_room(room_id):
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM rooms WHERE id = ?", (room_id,))
    room = cur.fetchone()
    if not room:
        flash("Local introuvable.", "danger")
        return redirect(url_for("rooms"))
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        benches = int(request.form.get("benches", 0) or 0)
        per_bench = int(request.form.get("per_bench", 1) or 1)
        
        if name and benches > 0 and per_bench > 0:
            try:
                cur.execute(
                    "UPDATE rooms SET name = ?, benches = ?, students_per_bench = ? WHERE id = ?",
                    (name, benches, per_bench, room_id)
                )
                conn.commit()
                flash("Local modifié avec succès.", "success")
                return redirect(url_for("rooms"))
            except Exception as e:
                flash(f"Erreur lors de la modification: {str(e)}", "danger")
        else:
            flash("Complétez le formulaire.", "danger")
    
    return render_template("edit_room.html", room=room)


@app.route("/rooms/<int:room_id>/delete", methods=["GET"])
@login_required
def delete_room(room_id):
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        conn.commit()
        flash("Local supprimé avec succès.", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")
    
    return redirect(url_for("rooms"))


@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def manage_users():
    """Gestion des utilisateurs (admin seulement)"""
    conn = get_db()
    cur = conn.cursor()
    
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        is_admin = request.form.get("is_admin") == "1"
        
        if not username or not password:
            flash("Le nom d'utilisateur et le mot de passe sont obligatoires.", "danger")
        else:
            try:
                password_hash = generate_password_hash(password)
                cur.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                    (username, password_hash, 1 if is_admin else 0)
                )
                conn.commit()
                flash(f"Utilisateur '{username}' créé avec succès.", "success")
            except sqlite3.IntegrityError:
                flash(f"Le nom d'utilisateur '{username}' existe déjà.", "danger")
            except Exception as e:
                flash(f"Erreur lors de la création: {str(e)}", "danger")
    
    # Récupérer tous les utilisateurs
    cur.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY created_at DESC")
    users = cur.fetchall()
    
    return render_template("users.html", users=users)


@app.route("/admin/users/<int:user_id>/delete", methods=["GET"])
@admin_required
def delete_user(user_id):
    """Supprimer un utilisateur (admin seulement)"""
    conn = get_db()
    cur = conn.cursor()
    
    # Ne pas permettre la suppression de son propre compte
    if user_id == session.get('user_id'):
        flash("Vous ne pouvez pas supprimer votre propre compte.", "danger")
        return redirect(url_for('manage_users'))
    
    cur.execute("SELECT username FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()
    
    if not user:
        flash("Utilisateur introuvable.", "danger")
        return redirect(url_for('manage_users'))
    
    try:
        cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        flash(f"Utilisateur '{user['username']}' supprimé avec succès.", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")
    
    return redirect(url_for('manage_users'))


@app.route("/admin/reset", methods=["GET", "POST"])
@admin_required
def reset_data():
    if request.method == "POST":
        confirm = request.form.get("confirm", "").strip()
        admin_password = request.form.get("admin_password", "")
        
        # Vérifier la confirmation
        if confirm != "RESET":
            flash("Confirmation incorrecte. Tapez 'RESET' pour confirmer.", "danger")
            return render_template("reset_data.html")
        
        # Vérifier le mot de passe administrateur
        if not admin_password:
            flash("Le mot de passe administrateur est requis.", "danger")
            return render_template("reset_data.html")
        
        conn = get_db()
        cur = conn.cursor()
        
        # Récupérer le mot de passe hashé de l'utilisateur administrateur connecté
        cur.execute("SELECT password_hash FROM users WHERE id = ?", (session['user_id'],))
        user = cur.fetchone()
        
        if not user or not check_password_hash(user['password_hash'], admin_password):
            flash("Mot de passe administrateur incorrect.", "danger")
            return render_template("reset_data.html")
        
        # Si toutes les vérifications passent, procéder à la réinitialisation sélective
        try:
            deleted_items = []
            
            # Récupérer les sélections de l'utilisateur
            delete_presences = request.form.get("delete_presences") == "on"
            delete_assignments = request.form.get("delete_assignments") == "on"
            delete_exam_promotions = request.form.get("delete_exam_promotions") == "on"
            delete_exams = request.form.get("delete_exams") == "on"
            delete_students = request.form.get("delete_students") == "on"
            delete_sections = request.form.get("delete_sections") == "on"
            delete_promotions = request.form.get("delete_promotions") == "on"
            
            # Vérifier qu'au moins une option est sélectionnée
            if not any([delete_presences, delete_assignments, delete_exam_promotions, 
                       delete_exams, delete_students, delete_sections, delete_promotions]):
                flash("Veuillez sélectionner au moins un type de données à supprimer.", "warning")
                return render_template("reset_data.html")
            
            # Supprimer dans l'ordre pour respecter les contraintes de clés étrangères
            if delete_presences:
                cur.execute("DELETE FROM presence")
                count = cur.rowcount
                deleted_items.append(f"{count} présences")
            
            if delete_assignments:
                cur.execute("DELETE FROM assignments")
                count = cur.rowcount
                deleted_items.append(f"{count} affectations")
            
            if delete_exam_promotions:
                cur.execute("DELETE FROM exam_promotions")
                count = cur.rowcount
                deleted_items.append(f"{count} liens examens-promotions")
            
            if delete_exams:
                cur.execute("DELETE FROM exams")
                count = cur.rowcount
                deleted_items.append(f"{count} examens/sessions")
            
            if delete_students:
                cur.execute("DELETE FROM students")
                count = cur.rowcount
                deleted_items.append(f"{count} étudiants")
            
            if delete_sections:
                cur.execute("DELETE FROM sections")
                count = cur.rowcount
                deleted_items.append(f"{count} sections")
            
            if delete_promotions:
                cur.execute("DELETE FROM promotions")
                count = cur.rowcount
                deleted_items.append(f"{count} promotions")
            
            conn.commit()
            
            if deleted_items:
                flash(f"Suppression réussie : {', '.join(deleted_items)}.", "success")
            else:
                flash("Aucune donnée supprimée.", "info")
                
        except Exception as e:
            conn.rollback()
            flash(f"Erreur lors de la réinitialisation: {str(e)}", "danger")
        return redirect(url_for("index"))
    
    return render_template("reset_data.html")


@app.route("/exams", methods=["GET", "POST"])
@login_required
def exams():
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer toutes les promotions pour le formulaire
    cur.execute("SELECT id, name FROM promotions ORDER BY name")
    promotions = cur.fetchall()
    
    # Récupérer les promotions sélectionnées depuis le formulaire (pour filtrer l'affichage après création)
    selected_promo_ids = []
    if request.method == "POST" and "promotion_ids" in request.form:
        # Utiliser les promotions sélectionnées dans le formulaire pour filtrer l'affichage
        selected_promo_ids = request.form.getlist("promotion_ids")
        # Stocker dans la session pour persister après la redirection
        session['filter_promo_ids'] = selected_promo_ids
    elif 'filter_promo_ids' in session:
        # Utiliser les promotions stockées dans la session
        selected_promo_ids = session['filter_promo_ids']
    
    if request.method == "POST" and "label" in request.form:
        # Création d'une nouvelle session
        label = request.form.get("label", "").strip()
        session_type = request.form.get("session_type", "").strip()
        promotion_ids = request.form.getlist("promotion_ids")  # Liste des promotions sélectionnées
        
        if label and session_type and promotion_ids:
            try:
                # Créer l'examen
                cur.execute(
                    "INSERT INTO exams (label, session_type, promotion_id) VALUES (?, ?, ?)",
                    (label, session_type, int(promotion_ids[0])),  # Garder promotion_id pour compatibilité
                )
                exam_id = cur.lastrowid
                
                # Ajouter les promotions dans la table de liaison
                for promo_id in promotion_ids:
                    cur.execute(
                        "INSERT OR IGNORE INTO exam_promotions (exam_id, promotion_id) VALUES (?, ?)",
                        (exam_id, int(promo_id))
                    )
                
                conn.commit()
                # Récupérer les noms des promotions sélectionnées
                promo_ids_int = [int(pid) for pid in promotion_ids]
                promo_names = ", ".join([p["name"] for p in promotions if p["id"] in promo_ids_int])
                flash(f"Session '{label}' ({session_type}) ajoutée pour {len(promotion_ids)} promotion(s) ({promo_names}).", "success")
            except Exception as e:
                flash(f"Erreur lors de l'ajout de la session: {str(e)}", "danger")
        else:
            flash("Nom de session, période (Matin/Après-midi) et au moins une promotion sont obligatoires.", "danger")
    
    # Récupérer les examens filtrés par promotions si des promotions sont sélectionnées
    # Afficher seulement les sessions qui contiennent EXACTEMENT les promotions sélectionnées
    if selected_promo_ids:
        # Convertir en entiers
        promo_ids_int = [int(pid) for pid in selected_promo_ids]
        placeholders = ",".join("?" * len(promo_ids_int))
        # Requête pour trouver les examens qui contiennent exactement les promotions sélectionnées
        # (ni plus, ni moins)
        cur.execute(
            f"""SELECT DISTINCT e.id, e.label, e.session_type,
               GROUP_CONCAT(p.name, ', ') as promotion_names
               FROM exams e 
               JOIN exam_promotions ep ON e.id = ep.exam_id
               JOIN promotions p ON ep.promotion_id = p.id
               WHERE e.id IN (
                   SELECT exam_id 
                   FROM exam_promotions 
                   GROUP BY exam_id 
                   HAVING COUNT(DISTINCT promotion_id) = ?
                   AND COUNT(DISTINCT CASE WHEN promotion_id IN ({placeholders}) THEN promotion_id END) = ?
               )
               GROUP BY e.id, e.label, e.session_type
               ORDER BY e.session_type ASC, e.label ASC""",
            (len(promo_ids_int),) + tuple(promo_ids_int) + (len(promo_ids_int),)
        )
    else:
        # Récupérer tous les examens avec leurs promotions
        cur.execute(
            """SELECT DISTINCT e.id, e.label, e.session_type,
               GROUP_CONCAT(p.name, ', ') as promotion_names
               FROM exams e 
               LEFT JOIN exam_promotions ep ON e.id = ep.exam_id
               LEFT JOIN promotions p ON ep.promotion_id = p.id
               GROUP BY e.id, e.label, e.session_type
               ORDER BY e.session_type ASC, e.label ASC"""
        )
    exams_list = cur.fetchall()
    return render_template("exams.html", exams=exams_list, promotions=promotions)


# Import surveillants (admin)
@app.route("/admin/import_supervisors", methods=["GET", "POST"])
@admin_required
def import_supervisors():
    conn = get_db()
    cur = conn.cursor()
    import pandas as pd
    total = 0
    cur.execute("SELECT COUNT(*) as c FROM users WHERE is_admin = 0")
    total = cur.fetchone()["c"]
    if request.method == "POST":
        file = request.files.get("excel")
        if not file:
            flash("Veuillez sélectionner un fichier Excel.", "danger")
            return render_template("import_supervisors.html", total=total)
        try:
            df = pd.read_excel(file)
        except Exception:
            flash("Impossible de lire le fichier. Assurez-vous du format Excel.", "danger")
            return render_template("import_supervisors.html", total=total)
        cols = {str(c).lower().strip(): c for c in df.columns}
        if "username" not in cols or "password" not in cols:
            flash("Colonnes obligatoires: username, password.", "danger")
            return render_template("import_supervisors.html", total=total)
        inserted = 0
        for _, row in df.iterrows():
            username_val = row[cols["username"]]
            password_val = row[cols["password"]]
            username = "" if pd.isna(username_val) else str(username_val).strip()
            password = "" if pd.isna(password_val) else str(password_val).strip()
            if not username or not password:
                continue
            # Vérifier si l'utilisateur existe déjà
            cur.execute("SELECT id FROM users WHERE username = ?", (username,))
            if cur.fetchone():
                continue
            password_hash = generate_password_hash(password)
            cur.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 0)",
                (username, password_hash)
            )
            inserted += 1
        conn.commit()
        flash(f"{inserted} surveillants importés.", "success")
        cur.execute("SELECT COUNT(*) as c FROM users WHERE is_admin = 0")
        total = cur.fetchone()["c"]
        return render_template("import_supervisors.html", total=total)
    return render_template("import_supervisors.html", total=total)


@app.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@login_required
def edit_exam(exam_id):
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer toutes les promotions
    cur.execute("SELECT id, name FROM promotions ORDER BY name")
    promotions = cur.fetchall()
    
    # Récupérer l'examen et ses promotions
    exam = _get_exam_or_latest(conn, exam_id)
    if not exam:
        flash("Session introuvable.", "danger")
        return redirect(url_for("exams"))
    
    cur.execute("SELECT promotion_id FROM exam_promotions WHERE exam_id = ?", (exam_id,))
    current_promo_ids = [row["promotion_id"] for row in cur.fetchall()]
    
    if request.method == "POST":
        label = request.form.get("label", "").strip()
        session_type = request.form.get("session_type", "").strip()
        promotion_ids = request.form.getlist("promotion_ids")
        
        if label and session_type and promotion_ids:
            try:
                # Mettre à jour l'examen
                cur.execute(
                    "UPDATE exams SET label = ?, session_type = ? WHERE id = ?",
                    (label, session_type, exam_id)
                )
                
                # Supprimer les anciennes associations
                cur.execute("DELETE FROM exam_promotions WHERE exam_id = ?", (exam_id,))
                
                # Ajouter les nouvelles promotions
                for promo_id in promotion_ids:
                    cur.execute(
                        "INSERT INTO exam_promotions (exam_id, promotion_id) VALUES (?, ?)",
                        (exam_id, int(promo_id))
                    )
                
                conn.commit()
                flash("Session modifiée avec succès.", "success")
                return redirect(url_for("exams"))
            except Exception as e:
                flash(f"Erreur lors de la modification: {str(e)}", "danger")
        else:
            flash("Tous les champs sont obligatoires.", "danger")
    
    return render_template("edit_exam.html", exam=exam, promotions=promotions, current_promo_ids=current_promo_ids)


@app.route("/exams/<int:exam_id>/delete", methods=["GET"])
@login_required
def delete_exam(exam_id):
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Supprimer l'examen (les associations seront supprimées automatiquement grâce à CASCADE)
        cur.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
        conn.commit()
        flash("Session supprimée avec succès.", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression: {str(e)}", "danger")
    
    return redirect(url_for("exams"))


@app.route("/dispatch", methods=["GET", "POST"])
@login_required
def dispatch():
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer toutes les promotions
    cur.execute("SELECT id, name FROM promotions ORDER BY name")
    promotions = cur.fetchall()
    
    promotion_id = request.form.get("promotion_id") or request.args.get("promotion_id")
    
    # Récupérer les examens, éventuellement filtrés par promotion
    try:
        promo_id_int = int(promotion_id) if promotion_id else None
    except (ValueError, TypeError):
        promo_id_int = None
    exams = _get_exams(conn, promo_id_int)
    
    if not exams and promotion_id:
        flash(f"Aucun examen trouvé pour cette promotion.", "warning")
    elif not exams:
        flash("Ajoutez d'abord un examen.", "warning")
        return redirect(url_for("exams"))
    
    selected_id = request.form.get("exam_id") if request.method == "POST" else None
    exam = _get_exam_or_latest(conn, selected_id)
    result = None
    
    # Récupérer les promotions associées à l'examen sélectionné
    exam_promotions = []
    if exam:
        cur.execute(
            """
            SELECT promotion_id 
            FROM exam_promotions 
            WHERE exam_id = ?
            """,
            (exam["id"],)
        )
        exam_promo_rows = cur.fetchall()
        exam_promo_ids = [row["promotion_id"] for row in exam_promo_rows] if exam_promo_rows else []
        exam_promotions = [p for p in promotions if p["id"] in exam_promo_ids]
    
    # Récupérer les nombres d'étudiants par promotion si spécifiés
    promo_counts = {}
    if request.method == "POST" and exam:
        for promo in promotions:
            count_key = f"promo_count_{promo['id']}"
            count_value = request.form.get(count_key, "").strip()
            if count_value:
                try:
                    promo_counts[promo['id']] = int(count_value)
                except ValueError:
                    pass
    
    if request.method == "POST" and exam:
        balanced = request.form.get("balanced") == "1"
        try:
            result = distribute_students(conn, exam["id"], promo_counts if promo_counts else None, balanced)
            promo_names = exam['promotion_name'] if exam['promotion_name'] else 'Non définie'
            flash(f"Affectation terminée: {result['assigned']} étudiants des promotions ({promo_names}) dispatchés.", "success")
        except Exception as e:
            flash(f"Erreur lors de l'affectation: {str(e)}", "danger")
            result = None
    return render_template("dispatch.html", exams=exams, selected_exam=exam, result=result, promotions=promotions, exam_promotions=exam_promotions)


def _qr_base64(content: str):
    img = qrcode.make(content)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# Assignation manuelle surveillants (admin)
@app.route("/admin/assign_supervisors", methods=["GET", "POST"])
@admin_required
def assign_supervisors_manual():
    conn = get_db()
    cur = conn.cursor()
    # Récupérer toutes les données nécessaires
    cur.execute("SELECT id, label, session_type FROM exams ORDER BY label")
    exams = cur.fetchall()
    cur.execute("SELECT id, name FROM rooms ORDER BY name")
    rooms = cur.fetchall()
    cur.execute("SELECT id, username FROM users WHERE is_admin = 0 ORDER BY username")
    users = cur.fetchall()
    assign_date = request.form.get("assign_date") or request.args.get("assign_date")
    exam_id = request.form.get("exam_id") or request.args.get("exam_id")
    room_id = request.form.get("room_id") or request.args.get("room_id")
    assignments = []
    
    # Ajout d'une assignation (peut être multiple)
    if request.method == "POST" and request.form.getlist("user_ids") and not request.form.get("remove_assignment_id"):
        user_ids = request.form.getlist("user_ids")
        room_leader_id = request.form.get("room_leader_id")
        if assign_date and exam_id and room_id and user_ids:
            try:
                count = 0
                skipped = []
                for user_id in user_ids:
                    # Vérifier si le surveillant est déjà assigné à cet examen dans un autre local
                    cur.execute("""
                        SELECT r.name FROM supervisor_assignments sa
                        JOIN rooms r ON sa.room_id = r.id
                        WHERE sa.user_id = ? AND sa.exam_id = ?
                    """, (user_id, exam_id))
                    existing_assignment = cur.fetchone()
                    if existing_assignment:
                        # Récupérer le nom du surveillant
                        cur.execute("SELECT username FROM users WHERE id = ?", (user_id,))
                        user = cur.fetchone()
                        skipped.append(f"{user['username']} (déjà assigné au local {existing_assignment['name']})")
                        continue
                    
                    is_leader = 1 if room_leader_id and str(user_id) == str(room_leader_id) else 0
                    cur.execute("""
                        INSERT OR IGNORE INTO supervisor_assignments (user_id, exam_id, room_id, exam_date, is_room_leader, assigned_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (user_id, exam_id, room_id, assign_date, is_leader, datetime.now().isoformat()))
                    count += 1
                conn.commit()
                leader_name = ""
                if room_leader_id:
                    cur.execute("SELECT username FROM users WHERE id = ?", (room_leader_id,))
                    leader = cur.fetchone()
                    leader_name = f" (Chef: {leader['username']})" if leader else ""
                message = f"{count} affectation(s) enregistrée(s){leader_name}."
                if skipped:
                    message += f" Surveillants ignorés: {', '.join(skipped)}."
                flash(message, "success" if count > 0 else "warning")
            except Exception as e:
                flash(f"Erreur lors de l'enregistrement: {str(e)}", "danger")
    
    # Suppression d'une assignation
    if request.method == "POST" and request.form.get("remove_assignment_id"):
        assignment_id = request.form.get("remove_assignment_id")
        cur.execute("DELETE FROM supervisor_assignments WHERE id = ?", (assignment_id,))
        conn.commit()
        flash("Affectation supprimée.", "info")
    
    # Afficher les affectations pour la date sélectionnée
    if assign_date:
        cur.execute("""
            SELECT sa.id, sa.assigned_at, sa.exam_date, sa.is_room_leader, e.label as exam_label, e.session_type, r.name as room_name, u.username
            FROM supervisor_assignments sa
            JOIN exams e ON sa.exam_id = e.id
            JOIN rooms r ON sa.room_id = r.id
            JOIN users u ON sa.user_id = u.id
            WHERE sa.exam_date = ? OR (sa.exam_date IS NULL AND sa.assigned_at LIKE ?)
            ORDER BY r.name, e.session_type, u.username
        """, (assign_date, assign_date + '%'))
        assignments = cur.fetchall()
    
    return render_template("assign_supervisors.html", exams=exams, rooms=rooms, users=users, assignments=assignments, assign_date=assign_date, exam_id=exam_id, room_id=room_id)


# Gestion globale des affectations de surveillants (admin)
@app.route("/admin/manage_supervisor_assignments", methods=["GET", "POST"])
@admin_required
def manage_supervisor_assignments():
    conn = get_db()
    cur = conn.cursor()
    
    # Suppression d'une assignation
    if request.method == "POST" and request.form.get("remove_assignment_id"):
        assignment_id = request.form.get("remove_assignment_id")
        cur.execute("DELETE FROM supervisor_assignments WHERE id = ?", (assignment_id,))
        conn.commit()
        flash("Affectation supprimée.", "info")
        return redirect(url_for("manage_supervisor_assignments"))
    
    # Récupérer toutes les affectations actuelles
    cur.execute("""
        SELECT sa.id, sa.assigned_at, sa.exam_date, sa.is_room_leader, 
               e.label as exam_label, e.session_type, 
               r.name as room_name, u.username,
               sa.user_id, sa.exam_id, sa.room_id
        FROM supervisor_assignments sa
        JOIN exams e ON sa.exam_id = e.id
        JOIN rooms r ON sa.room_id = r.id
        JOIN users u ON sa.user_id = u.id
        ORDER BY sa.exam_date DESC, r.name, e.session_type, u.username
    """)
    assignments = cur.fetchall()
    
    # Grouper par date d'examen
    assignments_by_date = {}
    for assignment in assignments:
        exam_date = assignment['exam_date'] or 'Non définie'
        if exam_date not in assignments_by_date:
            assignments_by_date[exam_date] = []
        assignments_by_date[exam_date].append(assignment)
    
    return render_template("manage_supervisor_assignments.html", assignments_by_date=assignments_by_date)


# Impression listes surveillants (admin)
@app.route("/admin/print_supervisors", methods=["GET"])
@admin_required
def print_supervisors():
    conn = get_db()
    cur = conn.cursor()
    print_date = request.args.get("date")
    if not print_date:
        flash("Date requise.", "danger")
        return redirect(url_for("assign_supervisors_manual"))
    
    # Récupérer les surveillants par local groupés par examen
    cur.execute("""
        SELECT sa.id, sa.exam_date, e.session_type, e.label as exam_label, r.name as room_name, u.username, u.id as user_id
        FROM supervisor_assignments sa
        JOIN exams e ON sa.exam_id = e.id
        JOIN rooms r ON sa.room_id = r.id
        JOIN users u ON sa.user_id = u.id
        WHERE sa.exam_date = ? OR (sa.exam_date IS NULL AND sa.assigned_at LIKE ?)
        ORDER BY r.name, e.session_type, e.label, u.username
    """, (print_date, print_date + '%'))
    rows = cur.fetchall()
    
    # Grouper par local et par examen
    supervisors_by_room_and_exam = {}
    for row in rows:
        room_name = row['room_name']
        exam_label = row['exam_label']
        session_type = row['session_type']
        
        if room_name not in supervisors_by_room_and_exam:
            supervisors_by_room_and_exam[room_name] = {}
        
        exam_key = f"{session_type} - {exam_label}"
        if exam_key not in supervisors_by_room_and_exam[room_name]:
            supervisors_by_room_and_exam[room_name][exam_key] = []
        
        supervisors_by_room_and_exam[room_name][exam_key].append({
            'username': row['username'],
            'user_id': row['user_id']
        })
    
    return render_template("print_supervisors.html", 
                         supervisors_by_room_and_exam=supervisors_by_room_and_exam, 
                         supervisors_by_room=supervisors_by_room_and_exam,  # Pour compatibilité
                         print_date=print_date)


# Import & assignation surveillants (admin)
@app.route("/admin/import_assign_supervisors", methods=["GET", "POST"])
@admin_required
def import_assign_supervisors():
    import pandas as pd
    
    conn = get_db()
    cur = conn.cursor()
    total = 0
    cur.execute("SELECT COUNT(*) as c FROM users WHERE is_admin = 0")
    total = cur.fetchone()["c"]
    if request.method == "POST":
        file = request.files.get("excel")
        if not file:
            flash("Veuillez sélectionner un fichier Excel.", "danger")
            return render_template("import_assign_supervisors.html", total=total)
        try:
            df = pd.read_excel(file)
        except Exception:
            flash("Impossible de lire le fichier. Assurez-vous du format Excel.", "danger")
            return render_template("import_assign_supervisors.html", total=total)
        cols = {str(c).lower().strip(): c for c in df.columns}
        # Colonnes obligatoires
        required = ["username", "password", "exam_id", "room_id"]
        optional = ["exam_date"]
        
        if not all(col in cols for col in required):
            flash("Colonnes obligatoires: username, password, exam_id, room_id. Optionnel: exam_date", "danger")
            return render_template("import_assign_supervisors.html", total=total)
        
        inserted = 0
        assigned = 0
        for _, row in df.iterrows():
            username_val = row[cols["username"]]
            password_val = row[cols["password"]]
            exam_id_val = row[cols["exam_id"]]
            room_id_val = row[cols["room_id"]]
            
            # Récupérer la date d'examen si fournie
            exam_date_val = None
            if "exam_date" in cols:
                exam_date_val = row[cols["exam_date"]]
                if pd.notna(exam_date_val):
                    exam_date_val = str(exam_date_val).strip()
            
            username = "" if pd.isna(username_val) else str(username_val).strip()
            password = "" if pd.isna(password_val) else str(password_val).strip()
            try:
                exam_id = int(exam_id_val)
                room_id = int(room_id_val)
            except Exception:
                continue
            if not username or not password or not exam_id or not room_id:
                continue
            
            # Vérifier si l'utilisateur existe déjà
            cur.execute("SELECT id FROM users WHERE username = ?", (username,))
            user = cur.fetchone()
            if not user:
                password_hash = generate_password_hash(password)
                cur.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 0)",
                    (username, password_hash)
                )
                user_id = cur.lastrowid
                inserted += 1
            else:
                user_id = user["id"]
            
            # Assigner le surveillant avec la date si disponible
            # Vérifier si le surveillant est déjà assigné à cet examen dans un autre local
            cur.execute("""
                SELECT r.name FROM supervisor_assignments sa
                JOIN rooms r ON sa.room_id = r.id
                WHERE sa.user_id = ? AND sa.exam_id = ?
            """, (user_id, exam_id))
            existing_assignment = cur.fetchone()
            if existing_assignment:
                # Récupérer le nom du surveillant
                cur.execute("SELECT username FROM users WHERE id = ?", (user_id,))
                user = cur.fetchone()
                # Ignorer l'assignation pour ce surveillant
                continue
            
            if exam_date_val:
                cur.execute("""
                    INSERT OR IGNORE INTO supervisor_assignments (user_id, exam_id, room_id, exam_date, assigned_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, exam_id, room_id, exam_date_val, datetime.now().isoformat()))
            else:
                cur.execute("""
                    INSERT OR IGNORE INTO supervisor_assignments (user_id, exam_id, room_id, assigned_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, exam_id, room_id, datetime.now().isoformat()))
            assigned += 1
        
        conn.commit()
        flash(f"{inserted} surveillants créés, {assigned} assignations effectuées.", "success")
        cur.execute("SELECT COUNT(*) as c FROM users WHERE is_admin = 0")
        total = cur.fetchone()["c"]
        return render_template("import_assign_supervisors.html", total=total)
    return render_template("import_assign_supervisors.html", total=total)


@app.route("/rooms/<int:room_id>/badges")
@login_required
def room_badges(room_id):
    conn = get_db()
    cur = conn.cursor()
    
    try:
        exam_id = request.args.get("exam_id")
        exam = _get_exam_or_latest(conn, exam_id)
        if not exam:
            flash("Aucun examen disponible.", "warning")
            return redirect(url_for("dispatch"))
        
        # Récupérer le nom du local
        cur.execute("SELECT name FROM rooms WHERE id = ?", (room_id,))
        room = cur.fetchone()
        room_name = room["name"] if room else "Local inconnu"
        
        cur.execute(
            """
            SELECT a.*, s.full_name, s.matricule, p.name AS promotion_name, sec.name AS section_name,
                   r.name AS room_name
            FROM assignments a
            JOIN students s ON a.student_id = s.id
            JOIN promotions p ON s.promotion_id = p.id
            LEFT JOIN sections sec ON s.section_id = sec.id
            JOIN rooms r ON r.id = a.room_id
            WHERE a.room_id = ? AND a.exam_id = ?
            ORDER BY a.seat_number
            """,
            (room_id, exam["id"]),
        )
        rows = cur.fetchall()
        badges = []
        for row in rows:
            token = row["qr_token"]
            qr = _qr_base64(token)
            badges.append({"row": row, "qr": qr})
        return render_template("badges.html", badges=badges, exam=exam, room_name=room_name, room_id=room_id)
    except Exception as e:
        flash(f"Erreur lors de la génération des badges: {str(e)}", "danger")
        return redirect(url_for("rooms"))


@app.route("/rooms/<int:room_id>/liste")
@login_required
def room_list(room_id):
    conn = get_db()
    cur = conn.cursor()
    
    try:
        exam_id = request.args.get("exam_id")
        exam = _get_exam_or_latest(conn, exam_id)
        if not exam:
            flash("Aucun examen disponible.", "warning")
            return redirect(url_for("rooms"))
        
        # Récupérer le nom du local
        cur.execute("SELECT name FROM rooms WHERE id = ?", (room_id,))
        room = cur.fetchone()
        room_name = room["name"] if room else "Local inconnu"
        
        # Récupérer le surveillant assigné
        cur.execute("""
            SELECT u.username FROM supervisor_assignments sa
            JOIN users u ON sa.user_id = u.id
            WHERE sa.room_id = ? AND sa.exam_id = ?
        """, (room_id, exam["id"]))
        supervisor = cur.fetchone()
        supervisor_name = supervisor["username"] if supervisor else "Non assigné"
        
        cur.execute(
            """
            SELECT a.seat_number, s.full_name, s.matricule, p.name AS promotion_name
            FROM assignments a
            JOIN students s ON a.student_id = s.id
            JOIN promotions p ON s.promotion_id = p.id
            WHERE a.room_id = ? AND a.exam_id = ?
            ORDER BY a.seat_number
            """,
            (room_id, exam["id"]),
        )
        rows = cur.fetchall()
        return render_template("list.html", rows=rows, exam=exam, room_name=room_name, room_id=room_id, supervisor_name=supervisor_name, today=datetime.now().strftime('%Y-%m-%d'))
    except Exception as e:
        flash(f"Erreur lors de la récupération de la liste: {str(e)}", "danger")
        return redirect(url_for("rooms"))


def _is_supervisor_assigned(conn, user_id, exam_id, room_id):
    """Vérifier si un surveillant est assigné à un local pour un examen"""
    cur = conn.cursor()
    # Les admins ont accès à tous les locaux
    cur.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()
    if user and user['is_admin']:
        return True
    
    # Vérifier l'assignation
    cur.execute("""
        SELECT id FROM supervisor_assignments 
        WHERE user_id = ? AND exam_id = ? AND room_id = ?
    """, (user_id, exam_id, room_id))
    return cur.fetchone() is not None


@app.route("/presence/scan", methods=["GET"])
@login_required
def scan_presence_menu():
    """Menu de pointage de présence pour toutes les promotions"""
    promotions_data = []
    error_message = None
    
    try:
        conn = get_db()
        if not conn:
            error_message = "Impossible de se connecter à la base de données"
        else:
            cur = conn.cursor()
            
            # Récupérer toutes les promotions
            try:
                cur.execute("SELECT id, name FROM promotions ORDER BY name")
                promotions_list = cur.fetchall()
            except Exception as e:
                error_message = f"Erreur lors de la récupération des promotions: {str(e)}"
                promotions_list = []
            
            # Pour chaque promotion, récupérer les examens et les locaux
            for promo in promotions_list:
                try:
                    promo_id = promo['id']
                    promo_name = promo['name']
                    
                    # Récupérer les examens associés à cette promotion
                    try:
                        cur.execute("""
                            SELECT DISTINCT e.id, e.label, e.session_type
                            FROM exams e
                            JOIN exam_promotions ep ON e.id = ep.exam_id
                            WHERE ep.promotion_id = ?
                            ORDER BY e.session_type ASC, e.label ASC
                        """, (promo_id,))
                        exams = cur.fetchall()
                    except Exception:
                        exams = []
                    
                    exams_data = []
                    for exam in exams:
                        try:
                            exam_id = exam['id']
                            exam_label = exam['label']
                            exam_session = exam['session_type']
                            
                            # Récupérer les locaux où les étudiants de cette promotion sont assignés pour cet examen
                            # ET où l'utilisateur actuel est surveillant
                            try:
                                user_id = session.get('user_id')
                                # Vérifier si l'utilisateur est admin
                                cur.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
                                user = cur.fetchone()
                                is_admin = user and user['is_admin']
                                
                                if is_admin:
                                    # Les admins voient tous les locaux
                                    cur.execute("""
                                        SELECT DISTINCT r.id, r.name, 
                                               COUNT(DISTINCT a.id) as student_count,
                                               COUNT(DISTINCT CASE WHEN pres.status = 'present' THEN a.id END) as present_count
                                        FROM assignments a
                                        JOIN students s ON a.student_id = s.id
                                        JOIN rooms r ON a.room_id = r.id
                                        LEFT JOIN presence pres ON pres.assignment_id = a.id
                                        WHERE a.exam_id = ? AND s.promotion_id = ?
                                        GROUP BY r.id, r.name
                                        ORDER BY r.name
                                    """, (exam_id, promo_id))
                                else:
                                    # Les surveillants ne voient que leurs locaux assignés
                                    cur.execute("""
                                        SELECT DISTINCT r.id, r.name, 
                                               COUNT(DISTINCT a.id) as student_count,
                                               COUNT(DISTINCT CASE WHEN pres.status = 'present' THEN a.id END) as present_count
                                        FROM assignments a
                                        JOIN students s ON a.student_id = s.id
                                        JOIN rooms r ON a.room_id = r.id
                                        LEFT JOIN presence pres ON pres.assignment_id = a.id
                                        JOIN supervisor_assignments sa ON sa.room_id = r.id AND sa.exam_id = a.exam_id
                                        WHERE a.exam_id = ? AND s.promotion_id = ? AND sa.user_id = ?
                                        GROUP BY r.id, r.name
                                        ORDER BY r.name
                                    """, (exam_id, promo_id, user_id))
                                rooms = cur.fetchall()
                            except Exception as e:
                                print(f"Erreur lors de la récupération des locaux: {str(e)}")
                                rooms = []
                            
                            if rooms:  # Seulement ajouter l'examen s'il y a des locaux
                                exams_data.append({
                                    'id': exam_id,
                                    'label': exam_label,
                                    'session_type': exam_session,
                                    'rooms': rooms
                                })
                        except Exception:
                            continue
                    
                    if exams_data:  # Seulement ajouter la promotion s'il y a des examens avec des locaux
                        promotions_data.append({
                            'id': promo_id,
                            'name': promo_name,
                            'exams': exams_data
                        })
                except Exception:
                    continue
    except Exception as e:
        error_message = f"Erreur générale: {str(e)}"
    
    # Toujours rendre le template, même en cas d'erreur
    if error_message:
        flash(error_message, "warning")
    
    try:
        return render_template("scan_presence.html", promotions=promotions_data)
    except Exception as template_err:
        # Si le template échoue, retourner une réponse simple
        return f"""
        <html>
        <head><title>Pointage de Présence</title></head>
        <body>
            <h1>Pointage de Présence par Promotion</h1>
            <p>Erreur lors du chargement de la page: {str(template_err)}</p>
            <p><a href="{url_for('index')}">Retour à l'accueil</a></p>
        </body>
        </html>
        """, 500


@app.route("/rooms/<int:room_id>/scan", methods=["GET", "POST"])
@login_required
def room_scan(room_id):
    """Interface de pointage par QR code pour les surveillants"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        exam_id = request.args.get("exam_id")
        exam = _get_exam_or_latest(conn, exam_id)
        if not exam:
            flash("Aucun examen disponible.", "warning")
            return redirect(url_for("rooms"))
        
        # Vérifier que l'utilisateur est autorisé à accéder à ce local
        user_id = session.get('user_id')
        if not _is_supervisor_assigned(conn, user_id, exam['id'], room_id):
            flash("Vous n'êtes pas autorisé à accéder à ce local pour cet examen.", "danger")
            return redirect(url_for("scan_presence_menu"))
        
        # Récupérer le nom du local
        cur.execute("SELECT name FROM rooms WHERE id = ?", (room_id,))
        room = cur.fetchone()
        room_name = room["name"] if room else "Local inconnu"
        
        # Récupérer les étudiants assignés avec leur statut de présence
        cur.execute(
            """
            SELECT 
                a.id as assignment_id,
                a.seat_number, 
                s.full_name, 
                s.matricule, 
                p.name AS promotion_name,
                a.qr_token,
                pres.status as presence_status,
                pres.scanned_at,
                pres.scan_date,
                u.username as scanned_by_username
            FROM assignments a
            JOIN students s ON a.student_id = s.id
            JOIN promotions p ON s.promotion_id = p.id
            LEFT JOIN presence pres ON pres.assignment_id = a.id
            LEFT JOIN users u ON pres.scanned_by = u.id
            WHERE a.room_id = ? AND a.exam_id = ?
            ORDER BY a.seat_number
            """,
            (room_id, exam["id"]),
        )
        students = cur.fetchall()
        
        # Statistiques
        total_students = len(students)
        present_count = sum(1 for s in students if s['presence_status'] and s['presence_status'] == 'present')
        absent_count = total_students - present_count
        
        return render_template(
            "room_scan.html", 
            students=students, 
            exam=exam, 
            room_name=room_name,
            room_id=room_id,
            total_students=total_students,
            present_count=present_count,
            absent_count=absent_count
        )
    except Exception as e:
        flash(f"Erreur: {str(e)}", "danger")
        return redirect(url_for("rooms"))


@app.route("/rooms/<int:room_id>/presence")
@login_required
def room_presence(room_id):
    """Liste de présence manuelle avec dates étalées"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        exam_id = request.args.get("exam_id")
        exam = _get_exam_or_latest(conn, exam_id)
        if not exam:
            flash("Aucun examen disponible.", "warning")
            return redirect(url_for("rooms"))
        
        # Paramètres de la période d'examens
        start_date_str = request.args.get("start_date", "")
        end_date_str = request.args.get("end_date", "")
        try:
            num_days = int(request.args.get("num_days", "7"))
        except (ValueError, TypeError):
            num_days = 7  # Valeur par défaut si erreur de conversion
        
        # Récupérer le nom du local
        cur.execute("SELECT name FROM rooms WHERE id = ?", (room_id,))
        room = cur.fetchone()
        room_name = room["name"] if room else "Local inconnu"
        
        # Récupérer les étudiants assignés
        cur.execute(
            """
            SELECT a.id as assignment_id, a.seat_number, s.full_name, s.matricule, p.name AS promotion_name
            FROM assignments a
            JOIN students s ON a.student_id = s.id
            JOIN promotions p ON s.promotion_id = p.id
            WHERE a.room_id = ? AND a.exam_id = ?
            ORDER BY a.seat_number
            """,
            (room_id, exam["id"]),
        )
        rows = cur.fetchall()
        
        # Récupérer les données de présence
        cur.execute("""
            SELECT assignment_id, status, strftime('%d', scanned_at) as scan_day
            FROM presence
            WHERE assignment_id IN (SELECT id FROM assignments WHERE room_id = ? AND exam_id = ?)
        """, (room_id, exam['id']))
        presence_records = cur.fetchall()
        
        # Organiser les données de présence par assignment_id et jour
        presence_data = {}
        for record in presence_records:
            aid = record['assignment_id']
            day = record['scan_day']
            status = record['status']
            if aid not in presence_data:
                presence_data[aid] = {}
            presence_data[aid][day] = status
        
        # Générer les dates (en excluant les dimanches)
        dates = []
        if start_date_str and end_date_str:
            try:
                from datetime import timedelta
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
                current = start_date
                while current <= end_date:
                    # Exclure les dimanches (weekday() retourne 6 pour dimanche)
                    if current.weekday() != 6:  # 0=lundi, 6=dimanche
                        dates.append(current.strftime("%d"))
                    current = current + timedelta(days=1)
            except:
                # Si erreur de parsing, utiliser num_days (en excluant les dimanches)
                from datetime import timedelta
                current = datetime.now()
                count = 0
                while count < num_days:
                    if current.weekday() != 6:  # Exclure les dimanches
                        dates.append(current.strftime("%d"))
                        count += 1
                    current = current + timedelta(days=1)
        else:
            # Utiliser num_days si pas de dates spécifiées (en excluant les dimanches)
            from datetime import timedelta
            current = datetime.now()
            count = 0
            while count < num_days:
                if current.weekday() != 6:  # Exclure les dimanches
                    dates.append(current.strftime("%d"))
                    count += 1
                current = current + timedelta(days=1)
        
        return render_template("presence.html", rows=rows, exam=exam, room_name=room_name, dates=dates, presence_data=presence_data)
    except Exception as e:
        flash(f"Erreur lors de la récupération de la liste de présence: {str(e)}", "danger")
        return redirect(url_for("rooms"))


@app.route("/api/presences/<int:room_id>")
@login_required
def get_room_presences(room_id):
    """API pour récupérer les présences d'un local en temps réel"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        exam_id = request.args.get("exam_id")
        if not exam_id:
            return jsonify({"status": "error", "message": "exam_id requis"}), 400
        
        cur.execute(
            """
            SELECT 
                a.id as assignment_id,
                a.seat_number, 
                s.full_name, 
                s.matricule, 
                p.name AS promotion_name,
                pres.status as presence_status,
                pres.scanned_at,
                pres.scan_date,
                u.username as scanned_by_username
            FROM assignments a
            JOIN students s ON a.student_id = s.id
            JOIN promotions p ON s.promotion_id = p.id
            LEFT JOIN presence pres ON pres.assignment_id = a.id
            LEFT JOIN users u ON pres.scanned_by = u.id
            WHERE a.room_id = ? AND a.exam_id = ?
            ORDER BY a.seat_number
            """,
            (room_id, exam_id),
        )
        students = cur.fetchall()
        
        # Convertir en format JSON
        result = []
        for student in students:
            result.append({
                'assignment_id': student['assignment_id'],
                'seat_number': student['seat_number'],
                'full_name': student['full_name'],
                'matricule': student['matricule'],
                'promotion_name': student['promotion_name'],
                'presence_status': student['presence_status'] or 'absent',
                'scanned_at': student['scanned_at'],
                'scan_date': student['scan_date'],
                'scanned_by_username': student['scanned_by_username']
            })
        
        total = len(result)
        present = sum(1 for s in result if s['presence_status'] == 'present')
        absent = total - present
        
        return jsonify({
            "status": "ok",
            "students": result,
            "stats": {
                "total": total,
                "present": present,
                "absent": absent
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.get("/api/validate/<token>")
@login_required
def validate_presence(token):
    """Valider la présence d'un étudiant via QR code (avec authentification)"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        user_id = session.get('user_id')
        
        cur.execute(
            """
            SELECT a.id as assignment_id, s.full_name, s.matricule, r.name as room_name, 
                   r.id as room_id, e.label, e.session_type, e.id as exam_id,
                   a.seat_number, p.name as promotion_name
            FROM assignments a
            JOIN students s ON a.student_id = s.id
            JOIN rooms r ON a.room_id = r.id
            JOIN exams e ON a.exam_id = e.id
            JOIN promotions p ON s.promotion_id = p.id
            WHERE a.qr_token = ?
            """,
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"status": "error", "message": "QR code inconnu ou invalide"}), 404
        
        # Vérifier que le surveillant est autorisé à scanner ce local (chef de salle uniquement)
        cur.execute("""
            SELECT id FROM supervisor_assignments 
            WHERE user_id = ? AND exam_id = ? AND room_id = ? AND is_room_leader = 1
        """, (user_id, row["exam_id"], row["room_id"]))
        if not cur.fetchone():
            return jsonify({"status": "error", "message": "Seul le chef de salle peut marquer la présence dans ce local."}), 403

        # Vérifier si déjà présent
        cur.execute("SELECT id, scanned_at, scan_date FROM presence WHERE assignment_id = ?", (row["assignment_id"],))
        existing = cur.fetchone()
        
        # Gérer la date d'examen
        exam_date = request.args.get('date')
        if exam_date:
            try:
                exam_dt = datetime.strptime(exam_date, '%Y-%m-%d')
                scanned_at = exam_dt.isoformat()
                scan_date = exam_date
            except ValueError:
                return jsonify({"status": "error", "message": "Date invalide"}), 400
        else:
            now = datetime.utcnow()
            scan_date = now.strftime("%Y-%m-%d")
            scanned_at = now.isoformat()
        user_id = session.get('user_id')
        
        if existing:
            # Mettre à jour la présence existante
            cur.execute(
                """
                UPDATE presence 
                SET status='present', 
                    scanned_at=?,
                    scan_date=?,
                    scanned_by=?
                WHERE assignment_id = ?
                """,
                (scanned_at, scan_date, user_id, row["assignment_id"]),
            )
            message = "Présence mise à jour"
        else:
            # Créer une nouvelle entrée de présence
            cur.execute(
                """
                INSERT INTO presence (assignment_id, status, scanned_at, scan_date, scanned_by)
                VALUES (?, 'present', ?, ?, ?)
                """,
                (row["assignment_id"], scanned_at, scan_date, user_id),
            )
            message = "Présence enregistrée"
        
        conn.commit()
        return jsonify(
            {
                "status": "ok",
                "message": message,
                "student": row["full_name"],
                "matricule": row["matricule"],
                "room": row["room_name"],
                "room_id": row["room_id"],
                "exam": row["label"],
                "exam_id": row["exam_id"],
                "session": row["session_type"],
                "seat_number": row["seat_number"],
                "promotion": row["promotion_name"],
                "scan_date": scan_date,
                "scanned_at": scanned_at
            }
        )
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": f"Erreur: {str(e)}"}), 500


@app.route("/admin/archive", methods=["GET", "POST"])
@admin_required
def archive_assignments():
    """Archiver les affectations d'un examen pour une période donnée"""
    conn = get_db()
    cur = conn.cursor()
    
    if request.method == "POST":
        exam_id = request.form.get("exam_id")
        archive_name = request.form.get("archive_name", "").strip()
        period_start = request.form.get("period_start", "").strip()
        period_end = request.form.get("period_end", "").strip()
        admin_password = request.form.get("admin_password", "")
        
        # Validations
        if not exam_id:
            flash("Veuillez sélectionner un examen.", "danger")
            return redirect(url_for("archive_assignments"))
        
        if not archive_name:
            flash("Le nom de l'archive est requis.", "danger")
            return redirect(url_for("archive_assignments"))
        
        if not period_start or not period_end:
            flash("Les dates de début et de fin de période sont requises.", "danger")
            return redirect(url_for("archive_assignments"))
        
        # Vérifier le mot de passe administrateur
        if not admin_password:
            flash("Le mot de passe administrateur est requis.", "danger")
            return redirect(url_for("archive_assignments"))
        
        cur.execute("SELECT password_hash FROM users WHERE id = ?", (session['user_id'],))
        user = cur.fetchone()
        
        if not user or not check_password_hash(user['password_hash'], admin_password):
            flash("Mot de passe administrateur incorrect.", "danger")
            return redirect(url_for("archive_assignments"))
        
        # Récupérer les informations de l'examen
        cur.execute("SELECT id, label, session_type FROM exams WHERE id = ?", (exam_id,))
        exam = cur.fetchone()
        
        if not exam:
            flash("Examen introuvable.", "danger")
            return redirect(url_for("archive_assignments"))
        
        # Récupérer toutes les affectations de cet examen
        cur.execute("""
            SELECT 
                a.id, a.student_id, a.exam_id, a.room_id, a.seat_number, a.qr_token,
                s.matricule, s.full_name, s.promotion_id, s.section_id,
                p.name as promotion_name,
                sec.name as section_name,
                r.name as room_name
            FROM assignments a
            JOIN students s ON a.student_id = s.id
            JOIN promotions p ON s.promotion_id = p.id
            LEFT JOIN sections sec ON s.section_id = sec.id
            JOIN rooms r ON a.room_id = r.id
            WHERE a.exam_id = ?
            ORDER BY r.name, a.seat_number
        """, (exam_id,))
        
        assignments = cur.fetchall()
        
        if not assignments:
            flash("Aucune affectation trouvée pour cet examen.", "warning")
            return redirect(url_for("archive_assignments"))
        
        # Archiver les affectations
        try:
            archived_count = 0
            for assignment in assignments:
                cur.execute("""
                    INSERT INTO assignment_archives (
                        archive_name, exam_id, exam_label, exam_session_type,
                        period_start_date, period_end_date,
                        student_id, student_matricule, student_full_name,
                        student_promotion_id, student_promotion_name,
                        student_section_id, student_section_name,
                        room_id, room_name, seat_number, qr_token,
                        archived_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    archive_name,
                    exam['id'],
                    exam['label'],
                    exam['session_type'],
                    period_start,
                    period_end,
                    assignment['student_id'],
                    assignment['matricule'],
                    assignment['full_name'],
                    assignment['promotion_id'],
                    assignment['promotion_name'],
                    assignment['section_id'],
                    assignment['section_name'],
                    assignment['room_id'],
                    assignment['room_name'],
                    assignment['seat_number'],
                    assignment['qr_token'],
                    session['user_id']
                ))
                archived_count += 1
            
            conn.commit()
            flash(f"Archive créée avec succès : {archived_count} affectations archivées pour '{archive_name}'.", "success")
            return redirect(url_for("view_archives"))
        except Exception as e:
            conn.rollback()
            flash(f"Erreur lors de l'archivage: {str(e)}", "danger")
    
    # GET: Afficher le formulaire d'archivage
    cur.execute("SELECT id, label, session_type FROM exams ORDER BY label")
    exams = cur.fetchall()
    
    return render_template("archive_assignments.html", exams=exams)


@app.route("/admin/archive_supervisor_assignments", methods=["GET", "POST"])
@admin_required
def archive_supervisor_assignments():
    """Archiver les affectations des surveillants pour une date donnée"""
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer les statistiques pour l'onglet système (toujours, même en cas d'erreur)
    cur.execute("SELECT COUNT(*) as total_promotions FROM promotions")
    total_promotions = cur.fetchone()['total_promotions']
    
    cur.execute("SELECT COUNT(*) as total_students FROM students")
    total_students = cur.fetchone()['total_students']
    
    cur.execute("SELECT COUNT(*) as total_assignments FROM assignments")
    total_assignments = cur.fetchone()['total_assignments']
    
    stats = {
        'total_promotions': total_promotions,
        'total_students': total_students,
        'total_assignments': total_assignments
    }
    
    if request.method == "POST":
        archive_name = request.form.get("archive_name", "").strip()
        exam_date = request.form.get("exam_date", "").strip()
        admin_password = request.form.get("admin_password", "")
        
        # Sanitize archive_name to replace slashes with dashes for URL safety
        archive_name = archive_name.replace("/", "-")
        
        if not archive_name:
            flash("Le nom de l'archive est requis.", "danger")
            return redirect(url_for("archive_supervisor_assignments"))
        
        if not exam_date:
            flash("La date d'examen est requise.", "danger")
            return redirect(url_for("archive_supervisor_assignments"))
        
        # Vérifier le mot de passe administrateur
        if not admin_password:
            flash("Le mot de passe administrateur est requis.", "danger")
            return redirect(url_for("archive_supervisor_assignments"))
        
        cur.execute("SELECT password_hash FROM users WHERE id = ?", (session['user_id'],))
        user = cur.fetchone()
        
        if not user or not check_password_hash(user['password_hash'], admin_password):
            flash("Mot de passe administrateur incorrect.", "danger")
            return redirect(url_for("archive_supervisor_assignments"))
        
        # Récupérer toutes les affectations de surveillants pour cette date
        cur.execute("""
            SELECT 
                sa.id, sa.user_id, sa.exam_id, sa.room_id, sa.exam_date, sa.is_room_leader,
                u.username,
                e.label as exam_label,
                r.name as room_name
            FROM supervisor_assignments sa
            JOIN users u ON sa.user_id = u.id
            LEFT JOIN exams e ON sa.exam_id = e.id
            JOIN rooms r ON sa.room_id = r.id
            WHERE sa.exam_date = ?
            ORDER BY r.name, u.username
        """, (exam_date,))
        
        assignments = cur.fetchall()
        
        if not assignments:
            flash("Aucune affectation de surveillant trouvée pour cette date.", "warning")
            return redirect(url_for("archive_supervisor_assignments"))
        
        # Archiver les affectations
        try:
            archived_count = 0
            for assignment in assignments:
                cur.execute("""
                    INSERT INTO supervisor_archives (
                        archive_name, exam_date, user_id, username, exam_id, exam_label,
                        room_id, room_name, is_room_leader, archived_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    archive_name,
                    exam_date,
                    assignment['user_id'],
                    assignment['username'],
                    assignment['exam_id'],
                    assignment['exam_label'],
                    assignment['room_id'],
                    assignment['room_name'],
                    assignment['is_room_leader'],
                    session['user_id']
                ))
                archived_count += 1
            
            conn.commit()
            flash(f"Archive créée avec succès : {archived_count} affectations de surveillants archivées pour '{archive_name}'.", "success")
            return redirect(url_for("view_supervisor_archives"))
        except Exception as e:
            conn.rollback()
            flash(f"Erreur lors de l'archivage: {str(e)}", "danger")
    
    return render_template("archive_supervisor_assignments.html", stats=stats)


@app.route("/admin/supervisor_archives", methods=["GET"])
@admin_required
def view_supervisor_archives():
    """Consulter les archives des affectations des surveillants"""
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer les statistiques pour l'onglet système
    cur.execute("SELECT COUNT(*) as total_promotions FROM promotions")
    total_promotions = cur.fetchone()['total_promotions']
    
    cur.execute("SELECT COUNT(*) as total_students FROM students")
    total_students = cur.fetchone()['total_students']
    
    cur.execute("SELECT COUNT(*) as total_assignments FROM assignments")
    total_assignments = cur.fetchone()['total_assignments']
    
    stats = {
        'total_promotions': total_promotions,
        'total_students': total_students,
        'total_assignments': total_assignments
    }
    
    # Récupérer les archives groupées par archive_name et exam_date
    cur.execute("""
        SELECT archive_name, exam_date, COUNT(*) as count, MAX(archived_at) as archived_at
        FROM supervisor_archives
        GROUP BY archive_name, exam_date
        ORDER BY archived_at DESC
    """)
    
    archives = cur.fetchall()
    
    return render_template("view_supervisor_archives.html", archives=archives, stats=stats)


@app.route("/admin/supervisor_archive", methods=["GET"])
@admin_required
def view_supervisor_archive_detail():
    """Voir le détail d'une archive de surveillants"""
    archive_name = request.args.get('archive_name')
    exam_date = request.args.get('exam_date')
    
    if not archive_name or not exam_date:
        flash("Paramètres manquants pour afficher l'archive.", "danger")
        return redirect(url_for('view_supervisor_archives'))
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT * FROM supervisor_archives
        WHERE archive_name = ? AND exam_date = ?
        ORDER BY room_name, username
    """, (archive_name, exam_date))
    
    assignments = cur.fetchall()
    
    return render_template("view_supervisor_archive_detail.html", 
                         archive_name=archive_name, exam_date=exam_date, assignments=assignments)


@app.route("/admin/archives", methods=["GET"])
@admin_required
def view_archives():
    """Consulter les archives des affectations"""
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer les paramètres de filtrage
    archive_name_filter = request.args.get("archive_name", "").strip()
    period_start_filter = request.args.get("period_start", "").strip()
    period_end_filter = request.args.get("period_end", "").strip()
    exam_id_filter = request.args.get("exam_id", "").strip()
    
    # Construire la requête avec filtres
    query = """
        SELECT DISTINCT
            archive_name, exam_id, exam_label, exam_session_type,
            period_start_date, period_end_date,
            COUNT(*) as assignment_count,
            MIN(archived_at) as archived_at,
            MAX(archived_at) as last_archived_at
        FROM assignment_archives
        WHERE 1=1
    """
    params = []
    
    if archive_name_filter:
        query += " AND archive_name LIKE ?"
        params.append(f"%{archive_name_filter}%")
    
    if period_start_filter:
        query += " AND period_start_date >= ?"
        params.append(period_start_filter)
    
    if period_end_filter:
        query += " AND period_end_date <= ?"
        params.append(period_end_filter)
    
    if exam_id_filter:
        query += " AND exam_id = ?"
        params.append(exam_id_filter)
    
    query += " GROUP BY archive_name, exam_id, exam_label, exam_session_type, period_start_date, period_end_date"
    query += " ORDER BY archived_at DESC"
    
    try:
        cur.execute(query, params)
        archives = cur.fetchall()
        
        # Récupérer la liste des examens pour le filtre
        cur.execute("SELECT id, label, session_type FROM exams ORDER BY label")
        exams = cur.fetchall()
        
        return render_template("view_archives.html", 
                             archives=archives, 
                             exams=exams,
                             archive_name_filter=archive_name_filter,
                             period_start_filter=period_start_filter,
                             period_end_filter=period_end_filter,
                             exam_id_filter=exam_id_filter)
    except Exception as e:
        flash(f"Erreur lors de la récupération des archives: {str(e)}", "danger")
        return render_template("view_archives.html", 
                             archives=[], 
                             exams=[],
                             archive_name_filter=archive_name_filter,
                             period_start_filter=period_start_filter,
                             period_end_filter=period_end_filter,
                             exam_id_filter=exam_id_filter)


@app.route("/admin/archives/<path:archive_name>/details")
@admin_required
def archive_details(archive_name):
    """Voir les détails d'une archive spécifique"""
    from urllib.parse import unquote
    conn = get_db()
    cur = conn.cursor()
    
    # Décoder le nom de l'archive depuis l'URL
    archive_name = unquote(archive_name)
    
    # Récupérer les paramètres de filtrage
    exam_id = request.args.get("exam_id")
    period_start = request.args.get("period_start")
    period_end = request.args.get("period_end")
    
    query = """
        SELECT 
            archive_name, exam_id, exam_label, exam_session_type,
            period_start_date, period_end_date,
            student_matricule, student_full_name,
            student_promotion_name, student_section_name,
            room_name, seat_number, archived_at
        FROM assignment_archives
        WHERE archive_name = ?
    """
    params = [archive_name]
    
    if exam_id:
        query += " AND exam_id = ?"
        params.append(exam_id)
    
    if period_start:
        query += " AND period_start_date = ?"
        params.append(period_start)
    
    if period_end:
        query += " AND period_end_date = ?"
        params.append(period_end)
    
    query += " ORDER BY room_name, seat_number"
    
    try:
        cur.execute(query, params)
        assignments = cur.fetchall()
        
        if not assignments:
            flash("Archive introuvable.", "danger")
            return redirect(url_for("view_archives"))
        
        # Récupérer les informations de l'archive
        archive_info = {
            'name': assignments[0]['archive_name'],
            'exam_label': assignments[0]['exam_label'],
            'exam_session_type': assignments[0]['exam_session_type'],
            'period_start': assignments[0]['period_start_date'],
            'period_end': assignments[0]['period_end_date'],
            'count': len(assignments)
        }
        
        return render_template("archive_details.html", 
                             assignments=assignments, 
                             archive_info=archive_info)
    except Exception as e:
        flash(f"Erreur lors de la récupération de l'archive: {str(e)}", "danger")
        return redirect(url_for("view_archives"))

# Routes de messagerie
@app.route("/messages")
@login_required
def messages():
    """Page principale de messagerie"""
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    is_admin = session.get('is_admin', False)
    
    # Récupérer les messages reçus
    if is_admin:
        # Admins reçoivent tous les messages envoyés par les surveillants
        cur.execute("""
            SELECT m.id, m.subject, m.message, m.created_at, m.is_read, m.exam_id, m.room_id,
                   u_sender.username as sender_name, u_recipient.username as recipient_name,
                   e.label as exam_label, r.name as room_name
            FROM messages m
            JOIN users u_sender ON m.sender_id = u_sender.id
            LEFT JOIN users u_recipient ON m.recipient_id = u_recipient.id
            LEFT JOIN exams e ON m.exam_id = e.id
            LEFT JOIN rooms r ON m.room_id = r.id
            ORDER BY m.created_at DESC
        """)
    else:
        # Surveillants reçoivent les messages des admins et des autres surveillants
        cur.execute("""
            SELECT m.id, m.subject, m.message, m.created_at, m.is_read, m.exam_id, m.room_id,
                   u_sender.username as sender_name, u_recipient.username as recipient_name,
                   e.label as exam_label, r.name as room_name
            FROM messages m
            JOIN users u_sender ON m.sender_id = u_sender.id
            LEFT JOIN users u_recipient ON m.recipient_id = u_recipient.id
            LEFT JOIN exams e ON m.exam_id = e.id
            LEFT JOIN rooms r ON m.room_id = r.id
            WHERE m.recipient_id = ? OR (m.recipient_id IS NULL AND u_sender.is_admin = 1)
            ORDER BY m.created_at DESC
        """, (user_id,))
    
    received_messages_raw = cur.fetchall()
    received_messages = [dict(msg) for msg in received_messages_raw]
    
    # Récupérer les messages envoyés
    cur.execute("""
        SELECT m.id, m.subject, m.message, m.created_at, m.is_read, m.exam_id, m.room_id,
               u_sender.username as sender_name, u_recipient.username as recipient_name,
               e.label as exam_label, r.name as room_name
        FROM messages m
        JOIN users u_sender ON m.sender_id = u_sender.id
        LEFT JOIN users u_recipient ON m.recipient_id = u_recipient.id
        LEFT JOIN exams e ON m.exam_id = e.id
        LEFT JOIN rooms r ON m.room_id = r.id
        WHERE m.sender_id = ?
        ORDER BY m.created_at DESC
    """, (user_id,))
    
    sent_messages_raw = cur.fetchall()
    sent_messages = [dict(msg) for msg in sent_messages_raw]
    
    # Compter les messages non lus (pour admins)
    unread_count = 0
    if is_admin:
        cur.execute("SELECT COUNT(*) as count FROM messages WHERE is_read = 0")
        unread_count = cur.fetchone()['count']
    
    # Récupérer les notifications
    cur.execute("""
        SELECT id, message, created_at, is_read
        FROM notifications 
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,))
    
    notifications_raw = cur.fetchall()
    notifications = [dict(notif) for notif in notifications_raw]
    
    # Compter les notifications non lues
    unread_notifications_count = len([n for n in notifications if not n['is_read']])
    
    return render_template("messages.html", 
                         received_messages=received_messages,
                         sent_messages=sent_messages,
                         unread_count=unread_count,
                         notifications=notifications,
                         unread_notifications_count=unread_notifications_count,
                         is_admin=is_admin)

@app.route("/messages/send", methods=["GET", "POST"])
@login_required
def send_message():
    """Envoyer un message (surveillants vers admin ou autres surveillants)"""
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    is_admin = session.get('is_admin', False)
    
    # Récupérer les destinataires possibles
    recipients = []
    if is_admin:
        # Admin peut envoyer à tous les surveillants
        cur.execute("SELECT id, username FROM users WHERE is_admin = 0 ORDER BY username")
        recipients_raw = cur.fetchall()
        recipients = [dict(row) for row in recipients_raw]
    else:
        # Surveillant peut envoyer à l'admin et aux autres surveillants
        cur.execute("SELECT id, username FROM users WHERE is_admin = 1 ORDER BY username")
        admin_recipients = cur.fetchall()
        cur.execute("SELECT id, username FROM users WHERE is_admin = 0 AND id != ? ORDER BY username", (user_id,))
        supervisor_recipients = cur.fetchall()
        recipients = [dict(row) for row in admin_recipients] + [dict(row) for row in supervisor_recipients]
    
    # Récupérer les examens et locaux de l'utilisateur (pour le contexte)
    if is_admin:
        # Admin voit tous les examens et locaux
        cur.execute("""
            SELECT DISTINCT e.id, e.label, e.session_type, r.id as room_id, r.name as room_name
            FROM exams e
            CROSS JOIN rooms r
            ORDER BY e.label, r.name
        """)
    else:
        # Surveillant voit ses propres examens et locaux
        cur.execute("""
            SELECT DISTINCT e.id, e.label, e.session_type, r.id as room_id, r.name as room_name
            FROM supervisor_assignments sa
            JOIN exams e ON sa.exam_id = e.id
            JOIN rooms r ON sa.room_id = r.id
            WHERE sa.user_id = ?
            ORDER BY e.label, r.name
        """, (user_id,))
    
    assignments_raw = cur.fetchall()
    assignments = [dict(row) for row in assignments_raw]
    
    if request.method == "POST":
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        recipient_id = request.form.get("recipient_id")
        
        if not is_admin:
            room_id = request.form.get("room_id")
            if not room_id:
                flash("Veuillez sélectionner un local.", "danger")
                return render_template("send_message.html", recipients=recipients, assignments=assignments, is_admin=is_admin)
            
            # Trouver le nom du local
            room_name = None
            for assignment in assignments:
                if str(assignment['room_id']) == room_id:
                    room_name = assignment['room_name']
                    break
            
            if room_name:
                message += f"\n\nLocal affecté: {room_name}"
        
        if not subject or not message or not recipient_id:
            flash("Sujet, message et destinataire sont obligatoires.", "danger")
            return render_template("send_message.html", recipients=recipients, assignments=assignments, is_admin=is_admin)
        
        try:
            cur.execute("""
                INSERT INTO messages (sender_id, recipient_id, subject, message)
                VALUES (?, ?, ?, ?)
            """, (user_id, recipient_id, subject, message))
            conn.commit()
            
            # Créer une notification pour le destinataire
            create_notification(recipient_id, f"Nouveau message de {session.get('username')}: {subject}")
            
            flash("Message envoyé avec succès.", "success")
            return redirect(url_for("messages") + "?tab=sent")
        except Exception as e:
            flash(f"Erreur lors de l'envoi: {str(e)}", "danger")
    
    return render_template("send_message.html", recipients=recipients, assignments=assignments, is_admin=is_admin)

@app.route("/messages/send_admin", methods=["GET", "POST"])
@admin_required
def send_message_admin():
    """Envoyer un message (admin vers surveillant)"""
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer tous les surveillants avec leurs affectations de locaux
    cur.execute("""
        SELECT u.id, u.username,
               GROUP_CONCAT(sa.room_id) as room_assignments
        FROM users u
        LEFT JOIN supervisor_assignments sa ON u.id = sa.user_id
        WHERE u.is_admin = 0
        GROUP BY u.id, u.username
        ORDER BY u.username
    """)
    supervisors_raw = cur.fetchall()
    supervisors = []
    for row in supervisors_raw:
        supervisor = {
            "id": int(row["id"]),
            "username": str(row["username"]),
            "room_assignments": [int(rid) for rid in (row['room_assignments'] or '').split(',') if rid and rid != 'None']
        }
        supervisors.append(supervisor)
    
    # Récupérer tous les locaux distincts
    cur.execute("SELECT DISTINCT id, name FROM rooms ORDER BY name")
    rooms_raw = cur.fetchall()
    rooms = [{"id": int(row["id"]), "name": str(row["name"])} for row in rooms_raw]
    
    if request.method == "POST":
        send_type = request.form.get("send_type", "one")
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()
        
        if not subject or not message:
            flash("Sujet et message sont obligatoires.", "danger")
            return render_template("send_message_admin.html", supervisors=supervisors, rooms=rooms)
        
        try:
            if send_type == "all":
                # Envoyer à tous les surveillants
                recipients = [s['id'] for s in supervisors]
                sent_count = 0
                for recipient_id in recipients:
                    cur.execute("""
                        INSERT INTO messages (sender_id, recipient_id, subject, message)
                        VALUES (?, ?, ?, ?)
                    """, (session['user_id'], recipient_id, subject, message))
                    create_notification(recipient_id, f"Nouveau message de l'administration: {subject}")
                    sent_count += 1
                conn.commit()
                flash(f"Message envoyé à {sent_count} surveillants.", "success")
            else:
                # Envoyer à un surveillant spécifique
                room_id = request.form.get("room_id")
                supervisor_id = request.form.get("supervisor_id")
                
                if not room_id:
                    flash("Veuillez d'abord sélectionner un local.", "danger")
                    return render_template("send_message_admin.html", supervisors=supervisors, rooms=rooms)
                
                if not supervisor_id:
                    flash("Destinataire obligatoire.", "danger")
                    return render_template("send_message_admin.html", supervisors=supervisors, rooms=rooms)
                
                # Vérifier que le surveillant est assigné au local sélectionné
                supervisor = next((s for s in supervisors if str(s['id']) == supervisor_id), None)
                if not supervisor or not supervisor.get('room_assignments') or int(room_id) not in supervisor['room_assignments']:
                    flash("Le surveillant sélectionné n'est pas assigné à ce local.", "danger")
                    return render_template("send_message_admin.html", supervisors=supervisors, rooms=rooms)
                
                cur.execute("""
                    INSERT INTO messages (sender_id, recipient_id, subject, message)
                    VALUES (?, ?, ?, ?)
                """, (session['user_id'], supervisor_id, subject, message))
                conn.commit()
                
                # Créer une notification pour le destinataire
                create_notification(supervisor_id, f"Nouveau message de l'administration: {subject}")
                
                flash("Message envoyé avec succès.", "success")
            
            return redirect(url_for("messages"))
        except Exception as e:
            flash(f"Erreur lors de l'envoi: {str(e)}", "danger")
    
    return render_template("send_message_admin.html", supervisors=supervisors, rooms=rooms)

@app.route("/messages/<int:message_id>/reply", methods=["GET", "POST"])
@admin_required
def reply_message(message_id):
    """Répondre à un message (admin vers surveillant)"""
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer le message original
    cur.execute("""
        SELECT m.*, u_sender.username as sender_name, u_recipient.username as recipient_name
        FROM messages m
        JOIN users u_sender ON m.sender_id = u_sender.id
        LEFT JOIN users u_recipient ON m.recipient_id = u_recipient.id
        WHERE m.id = ?
    """, (message_id,))
    message_raw = cur.fetchone()
    
    if not message_raw:
        flash("Message introuvable.", "danger")
        return redirect(url_for("messages"))
    
    # Convertir en dictionnaire
    message = dict(message_raw)
    
    if request.method == "POST":
        reply_subject = request.form.get("subject", "").strip()
        reply_message = request.form.get("message", "").strip()
        
        if not reply_subject or not reply_message:
            flash("Sujet et message sont obligatoires.", "danger")
            return render_template("reply_message.html", message=message)
        
        try:
            cur.execute("""
                INSERT INTO messages (sender_id, recipient_id, subject, message, exam_id, room_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session['user_id'], message['sender_id'], reply_subject, reply_message, message['exam_id'], message['room_id']))
            conn.commit()
            
            # Créer une notification pour le destinataire
            create_notification(message['sender_id'], f"Réponse de l'administration: {reply_subject}")
            
            flash("Réponse envoyée avec succès.", "success")
            return redirect(url_for("messages"))
        except Exception as e:
            flash(f"Erreur lors de l'envoi: {str(e)}", "danger")
    
    return render_template("reply_message.html", message=message)

@app.route("/messages/<int:message_id>/mark_read", methods=["POST"])
@login_required
def mark_message_read(message_id):
    """Marquer un message comme lu"""
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    is_admin = session.get('is_admin', False)
    
    try:
        # Vérifier que l'utilisateur peut marquer ce message comme lu
        if is_admin:
            # Les admins peuvent marquer tous les messages
            cur.execute("UPDATE messages SET is_read = 1 WHERE id = ?", (message_id,))
        else:
            # Les surveillants ne peuvent marquer que leurs propres messages reçus
            cur.execute("UPDATE messages SET is_read = 1 WHERE id = ? AND recipient_id = ?", (message_id, user_id))
        
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/messages/<int:message_id>/delete", methods=["POST"])
@login_required
def delete_message(message_id):
    """Supprimer un message"""
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    is_admin = session.get('is_admin', False)
    
    try:
        # Vérifier que l'utilisateur peut supprimer ce message
        if is_admin:
            cur.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        else:
            cur.execute("DELETE FROM messages WHERE id = ? AND sender_id = ?", (message_id, user_id))
        
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/notifications")
@login_required
def notifications():
    """Page des notifications"""
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    
    # Récupérer toutes les notifications de l'utilisateur
    cur.execute("""
        SELECT id, message, created_at, is_read
        FROM notifications 
        WHERE user_id = ?
        ORDER BY created_at DESC
    """, (user_id,))
    
    notifications_raw = cur.fetchall()
    notifications_list = [dict(notif) for notif in notifications_raw]
    
    return render_template("notifications.html", notifications=notifications_list)

@app.route("/notifications/<int:notification_id>/mark_read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    """Marquer une notification comme lue"""
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    
    try:
        cur.execute("UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?", (notification_id, user_id))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/notifications/<int:notification_id>/delete", methods=["POST"])
@login_required
def delete_notification(notification_id):
    """Supprimer une notification"""
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    
    try:
        cur.execute("DELETE FROM notifications WHERE id = ? AND user_id = ?", (notification_id, user_id))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/notifications/mark_all_read", methods=["POST"])
@login_required
def mark_all_notifications_read():
    """Marquer toutes les notifications comme lues"""
    conn = get_db()
    cur = conn.cursor()
    user_id = session.get('user_id')
    
    try:
        cur.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ==========================================
# PAIEMENT DES SURVEILLANTS
# ==========================================

@app.route("/admin/payment_rates", methods=["GET", "POST"])
@admin_required
def payment_rates():
    """Gérer les tarifs de paiement des surveillants"""
    conn = get_db()
    cur = conn.cursor()
    
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "update":
            # Handle update
            rate_id = request.form.get("rate_id")
            if rate_id:
                try:
                    name = request.form.get("name")
                    rate_type = request.form.get("rate_type")
                    amount = float(request.form.get("amount", 0))
                    currency = request.form.get("currency", "CDF")
                    is_active = 1 if request.form.get("is_active") else 0
                    
                    cur.execute("""
                        UPDATE payment_rates 
                        SET name = ?, rate_type = ?, amount = ?, currency = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    """, (name, rate_type, amount, currency, is_active, rate_id))
                    conn.commit()
                    
                    flash("Tarif modifié avec succès", "success")
                    
                except Exception as e:
                    conn.rollback()
                    flash(f"Erreur lors de la modification du tarif: {str(e)}", "danger")
            else:
                flash("ID du tarif manquant", "danger")
        else:
            # Handle create
            name = request.form.get("name")
            rate_type = request.form.get("rate_type")
            amount = request.form.get("amount")
            currency = request.form.get("currency", "CDF")
            is_active = 1 if request.form.get("is_active") else 1  # Par défaut actif
            
            if not amount or not rate_type or not name:
                flash("Tous les champs requis doivent être remplis.", "danger")
                return redirect(url_for("payment_rates"))
            
            try:
                amount = float(amount)
                cur.execute("""
                    INSERT INTO payment_rates (name, rate_type, amount, currency, is_active)
                    VALUES (?, ?, ?, ?, ?)
                """, (name, rate_type, amount, currency, is_active))
                conn.commit()
                flash("Tarif de paiement ajouté avec succès.", "success")
            except Exception as e:
                flash(f"Erreur lors de l'ajout du tarif: {str(e)}", "danger")
        
        return redirect(url_for("payment_rates"))
    
    # Récupérer tous les tarifs
    cur.execute("SELECT * FROM payment_rates ORDER BY created_at DESC")
    rates = cur.fetchall()
    
    # Convertir les objets Row en dictionnaires pour la sérialisation JSON
    rates = [dict(rate) for rate in rates]
    
    # S'assurer que tous les champs sont sérialisables
    for rate in rates:
        for key, value in rate.items():
            if value is None:
                rate[key] = ""
    
    return render_template("payment_rates.html", rates=rates)


@app.route("/admin/calculate_payments", methods=["GET", "POST"])
@admin_required
def calculate_payments():
    """Calculer les paiements des surveillants pour une période donnée"""
    conn = get_db()
    cur = conn.cursor()
    
    if request.method == "POST":
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")
        rate_id = request.form.get("rate_id")
        
        if not start_date or not end_date or not rate_id:
            flash("Les dates de début et fin, ainsi qu'un tarif, sont requis.", "danger")
            return redirect(url_for("calculate_payments"))
        
        # Récupérer le tarif sélectionné
        cur.execute("SELECT * FROM payment_rates WHERE id = ? AND is_active = 1", (rate_id,))
        selected_rate = cur.fetchone()
        
        if not selected_rate:
            flash("Tarif sélectionné introuvable ou inactif.", "danger")
            return redirect(url_for("calculate_payments"))
        
        try:
            # Récupérer les surveillances pour la période
            cur.execute("""
                SELECT 
                    sa.user_id, u.username, sa.exam_date, e.session_type,
                    COUNT(*) as surveillance_count,
                    GROUP_CONCAT(DISTINCT r.name) as rooms
                FROM supervisor_assignments sa
                JOIN users u ON sa.user_id = u.id
                JOIN exams e ON sa.exam_id = e.id
                JOIN rooms r ON sa.room_id = r.id
                WHERE sa.exam_date BETWEEN ? AND ?
                GROUP BY sa.user_id, sa.exam_date, e.session_type
                ORDER BY u.username, sa.exam_date
            """, (start_date, end_date))
            
            surveillances = cur.fetchall()
            
            # Calculer les paiements avec le tarif sélectionné
            payments = []
            for surveillance in surveillances:
                if selected_rate['rate_type'] == 'daily':
                    # Pour tarif journalier : compter les jours uniques
                    amount = selected_rate['amount']
                else:
                    # Pour tarif par session : multiplier par le nombre de sessions
                    amount = selected_rate['amount'] * surveillance['surveillance_count']
                
                payment = {
                    'user_id': surveillance['user_id'],
                    'username': surveillance['username'],
                    'exam_date': surveillance['exam_date'],
                    'session_type': surveillance['session_type'],
                    'surveillance_count': surveillance['surveillance_count'],
                    'rooms': surveillance['rooms'],
                    'rate_amount': selected_rate['amount'],
                    'total_amount': amount,
                    'rate_name': selected_rate['name'],
                    'rate_type': selected_rate['rate_type'],
                    'currency': selected_rate['currency']
                }
                payments.append(payment)
            
            # Sauvegarder les calculs de paiement
            for payment in payments:
                cur.execute("""
                    INSERT INTO calculated_payments 
                    (supervisor_id, exam_id, start_date, end_date, total_sessions, total_days,
                     rate_id, amount_per_session, amount_per_day, total_amount, currency, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'calculated')
                """, (
                    payment['user_id'], None, start_date, end_date,
                    payment['surveillance_count'] if selected_rate['rate_type'] == 'session' else 0,
                    1 if selected_rate['rate_type'] == 'daily' else 0,
                    selected_rate['id'],
                    selected_rate['amount'] if selected_rate['rate_type'] == 'session' else 0,
                    selected_rate['amount'] if selected_rate['rate_type'] == 'daily' else 0,
                    payment['total_amount'], selected_rate['currency']
                ))
            
            conn.commit()
            flash(f"Paiements calculés pour {len(payments)} surveillances.", "success")
            
            return render_template("calculate_payments.html", 
                                 calculations=payments, 
                                 start_date=start_date, 
                                 end_date=end_date,
                                 selected_rate=selected_rate,
                                 rate_id=rate_id)
            
        except Exception as e:
            flash(f"Erreur lors du calcul: {str(e)}", "danger")
    
    # Récupérer les tarifs actifs pour le formulaire
    cur.execute("SELECT * FROM payment_rates WHERE is_active = 1 ORDER BY name")
    rates = cur.fetchall()
    
    # Convertir les objets Row en dictionnaires pour la sérialisation JSON
    rates = [dict(rate) for rate in rates]
    
    # S'assurer que tous les champs sont sérialisables
    for rate in rates:
        for key, value in rate.items():
            if value is None:
                rate[key] = ""
    
    return render_template("calculate_payments.html", rates=rates)


@app.route("/admin/payment_history", methods=["GET"])
@admin_required
def payment_history():
    """Voir l'historique des paiements calculés"""
    conn = get_db()
    cur = conn.cursor()
    
    # Récupérer les paramètres de filtrage
    supervisor_id = request.args.get('supervisor_id')
    status = request.args.get('status')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    # Construire la requête avec filtres
    query = """
        SELECT cp.*, u.username as supervisor_name,
               strftime('%d/%m/%Y', cp.start_date) as start_date_formatted,
               strftime('%d/%m/%Y', cp.end_date) as end_date_formatted,
               strftime('%d/%m/%Y %H:%M', cp.calculated_at) as calculated_at_formatted,
               pr.name as rate_name
        FROM calculated_payments cp
        JOIN users u ON cp.supervisor_id = u.id
        JOIN payment_rates pr ON cp.rate_id = pr.id
        WHERE 1=1
    """
    params = []
    
    if supervisor_id:
        query += " AND cp.supervisor_id = ?"
        params.append(supervisor_id)
    
    if status:
        query += " AND cp.status = ?"
        params.append(status)
    
    if start_date:
        query += " AND cp.start_date >= ?"
        params.append(start_date)
    
    if end_date:
        query += " AND cp.end_date <= ?"
        params.append(end_date)
    
    query += " ORDER BY cp.calculated_at DESC"
    
    cur.execute(query, params)
    payments = cur.fetchall()
    
    # Statistiques
    stats = {
        'total_calculated': sum(p['total_amount'] for p in payments if p['status'] == 'calculated'),
        'total_paid': sum(p['total_amount'] for p in payments if p['status'] == 'paid'),
        'total_sessions': sum(p['total_sessions'] for p in payments),
        'total_payments': len(payments)
    }
    
    # Liste des superviseurs pour le filtre
    cur.execute("SELECT id, username FROM users WHERE is_admin = 0 ORDER BY username")
    supervisors = cur.fetchall()
    
    # Convertir les objets Row en dictionnaires
    supervisors = [dict(supervisor) for supervisor in supervisors]
    
    return render_template("payment_history.html", 
                         payments=payments, 
                         stats=stats, 
                         supervisors=supervisors,
                         supervisor_id=supervisor_id,
                         status=status,
                         start_date=start_date,
                         end_date=end_date)


@app.route("/admin/generate_payment_report", methods=["GET", "POST"])
@admin_required
def generate_payment_report():
    """Générer un rapport de paiement pour une période"""
    conn = get_db()
    cur = conn.cursor()
    
    if request.method == "POST":
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")
        user_id = request.form.get("user_id")
        
        query = """
            SELECT cp.*, u.username,
                   strftime('%d/%m/%Y', cp.start_date) as start_date_formatted,
                   strftime('%d/%m/%Y', cp.end_date) as end_date_formatted
            FROM calculated_payments cp
            JOIN users u ON cp.supervisor_id = u.id
            WHERE cp.start_date >= ? AND cp.end_date <= ?
        """
        params = [start_date, end_date]
        
        if user_id:
            query += " AND cp.supervisor_id = ?"
            params.append(user_id)
        
        query += " ORDER BY u.username, cp.calculated_at"
        
        cur.execute(query, params)
        payments = cur.fetchall()
        
        # Calculer les totaux
        total_amount = sum(payment['total_amount'] for payment in payments)
        total_sessions = sum(payment['total_sessions'] for payment in payments)
        
        return render_template("payment_report.html", 
                             payments=payments,
                             start_date=start_date,
                             end_date=end_date,
                             total_amount=total_amount,
                             total_sessions=total_sessions,
                             user_id=user_id)
    
    # Liste des utilisateurs pour le filtre
    cur.execute("SELECT id, username FROM users WHERE is_admin = 0 ORDER BY username")
    users = cur.fetchall()
    
    # Convertir les objets Row en dictionnaires
    users = [dict(user) for user in users]
    
    return render_template("generate_payment_report.html", users=users)


@app.route("/admin/toggle_rate_status/<int:rate_id>", methods=["GET", "POST"])
@admin_required
def toggle_rate_status(rate_id):
    """Activer/désactiver un tarif"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        # Récupérer le statut actuel
        cur.execute("SELECT is_active FROM payment_rates WHERE id = ?", (rate_id,))
        rate = cur.fetchone()
        
        if not rate:
            flash("Tarif introuvable", "danger")
            return redirect(url_for("payment_rates"))
        
        # Inverser le statut
        new_status = 0 if rate['is_active'] else 1
        cur.execute("UPDATE payment_rates SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", 
                   (new_status, rate_id))
        conn.commit()
        
        status_text = "activé" if new_status else "désactivé"
        flash(f"Tarif {status_text} avec succès", "success")
        
    except Exception as e:
        conn.rollback()
        flash(f"Erreur lors de la modification du tarif: {str(e)}", "danger")
    
    return redirect(url_for("payment_rates"))


@app.route("/admin/update_rate/<int:rate_id>", methods=["GET", "POST"])
@admin_required
def update_rate(rate_id):
    """Modifier un tarif existant"""
    conn = get_db()
    cur = conn.cursor()
    
    if request.method == "POST":
        try:
            name = request.form.get("name")
            rate_type = request.form.get("rate_type")
            amount = float(request.form.get("amount", 0))
            currency = request.form.get("currency", "CDF")
            is_active = 1 if request.form.get("is_active") else 0
            
            cur.execute("""
                UPDATE payment_rates 
                SET name = ?, rate_type = ?, amount = ?, currency = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (name, rate_type, amount, currency, is_active, rate_id))
            conn.commit()
            
            flash("Tarif modifié avec succès", "success")
            
        except Exception as e:
            conn.rollback()
            flash(f"Erreur lors de la modification du tarif: {str(e)}", "danger")
    
    return redirect(url_for("payment_rates"))


@app.route("/admin/mark_payment_paid/<int:payment_id>", methods=["POST"])
@admin_required
def mark_payment_paid(payment_id):
    """Marquer un paiement comme payé"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("UPDATE calculated_payments SET status = 'paid' WHERE id = ?", (payment_id,))
        conn.commit()
        flash("Paiement marqué comme payé", "success")
        
    except Exception as e:
        conn.rollback()
        flash(f"Erreur lors de la mise à jour du paiement: {str(e)}", "danger")
    
    return redirect(url_for("payment_history"))


@app.route("/admin/cancel_payment/<int:payment_id>", methods=["POST"])
@admin_required
def cancel_payment(payment_id):
    """Annuler un paiement"""
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("UPDATE calculated_payments SET status = 'cancelled' WHERE id = ?", (payment_id,))
        conn.commit()
        flash("Paiement annulé", "success")
        
    except Exception as e:
        conn.rollback()
        flash(f"Erreur lors de l'annulation du paiement: {str(e)}", "danger")
    
    return redirect(url_for("payment_history"))


#if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)