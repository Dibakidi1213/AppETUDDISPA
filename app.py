import base64
import io
import os
import sqlite3
import uuid
from datetime import datetime
from functools import wraps

import pandas as pd
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

from database import get_connection, init_db
from services.dispatch import distribute_students

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("APP_SECRET", "dev-key-change-me")

init_db()


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


@app.route("/")
@login_required
def index():
    """Page d'accueil avec statistiques"""
    conn = get_db()
    cur = conn.cursor()
    
    # Calculer les statistiques globales
    stats = {}
    
    # Total des promotions
    cur.execute("SELECT COUNT(*) as total FROM promotions")
    stats['total_promotions'] = cur.fetchone()['total']
    
    # Total des étudiants
    cur.execute("SELECT COUNT(*) as total FROM students")
    stats['total_students'] = cur.fetchone()['total']
    
    # Total des sections
    cur.execute("SELECT COUNT(*) as total FROM sections")
    stats['total_sections'] = cur.fetchone()['total']
    
    # Total des examens
    cur.execute("SELECT COUNT(*) as total FROM exams")
    stats['total_exams'] = cur.fetchone()['total']
    
    # Répartition par sexe
    cur.execute("SELECT sexe, COUNT(*) as count FROM students WHERE sexe IS NOT NULL GROUP BY sexe")
    sex_stats = cur.fetchall()
    stats['male_count'] = next((s['count'] for s in sex_stats if s['sexe'] == 'M'), 0)
    stats['female_count'] = next((s['count'] for s in sex_stats if s['sexe'] == 'F'), 0)
    
    # Statistiques supplémentaires
    cur.execute("SELECT COUNT(*) as total FROM rooms")
    stats['total_rooms'] = cur.fetchone()['total']
    
    cur.execute("SELECT COUNT(*) as total FROM assignments")
    stats['total_assignments'] = cur.fetchone()['total']
    
    cur.execute("SELECT COUNT(*) as total FROM presence WHERE status = 'present'")
    stats['total_present'] = cur.fetchone()['total']
    
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
            flash(f"Bienvenue, {user['username']}!", "success")
            return redirect(url_for("index"))
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
    
    # Statistiques globales
    cur.execute("SELECT COUNT(*) as total FROM rooms")
    stats['total_rooms'] = cur.fetchone()['total']
    
    cur.execute("SELECT SUM(benches * students_per_bench) as total_capacity FROM rooms")
    total_capacity = cur.fetchone()['total_capacity']
    stats['total_capacity'] = total_capacity if total_capacity else 0
    
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
            assigned = cur.fetchone()['assigned_count']
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
            present = cur.fetchone()['present_count']
            room_stat['present_count'] = present
            room_stat['absent_count'] = assigned - present if assigned >= present else 0
        else:
            # Statistiques globales (tous examens confondus)
            cur.execute("""
                SELECT COUNT(DISTINCT a.id) as total_assignments
                FROM assignments a
                WHERE a.room_id = ?
            """, (room['id'],))
            total_assignments = cur.fetchone()['total_assignments']
            room_stat['total_assignments'] = total_assignments
            
            # Nombre d'examens où ce local a été utilisé
            cur.execute("""
                SELECT COUNT(DISTINCT exam_id) as exams_count
                FROM assignments
                WHERE room_id = ?
            """, (room['id'],))
            exams_count = cur.fetchone()['exams_count']
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
    
    return render_template("rooms.html", 
                         rooms=rooms_with_stats, 
                         exam=exam, 
                         exams=exams_list,
                         stats=stats,
                         exam_stats=exam_stats)


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
    exams = _get_exams(conn, int(promotion_id) if promotion_id else None)
    
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
        result = distribute_students(conn, exam["id"], promo_counts if promo_counts else None)
        promo_names = exam['promotion_name'] if exam['promotion_name'] else 'Non définie'
        flash(f"Affectation terminée: {result['assigned']} étudiants des promotions ({promo_names}) dispatchés.", "success")
    return render_template("dispatch.html", exams=exams, selected_exam=exam, result=result, promotions=promotions, exam_promotions=exam_promotions)


def _qr_base64(content: str):
    img = qrcode.make(content)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@app.route("/rooms/<int:room_id>/badges")
@login_required
def room_badges(room_id):
    conn = get_db()
    exam_id = request.args.get("exam_id")
    exam = _get_exam_or_latest(conn, exam_id)
    if not exam:
        flash("Aucun examen disponible.", "warning")
        return redirect(url_for("dispatch"))
    cur = conn.cursor()
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
    return render_template("badges.html", badges=badges, exam=exam, room_name=room_name)


@app.route("/rooms/<int:room_id>/liste")
@login_required
def room_list(room_id):
    conn = get_db()
    exam_id = request.args.get("exam_id")
    exam = _get_exam_or_latest(conn, exam_id)
    cur = conn.cursor()
    # Récupérer le nom du local
    cur.execute("SELECT name FROM rooms WHERE id = ?", (room_id,))
    room = cur.fetchone()
    room_name = room["name"] if room else "Local inconnu"
    
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
    return render_template("list.html", rows=rows, exam=exam, room_name=room_name)


@app.route("/rooms/<int:room_id>/presence")
@login_required
def room_presence(room_id):
    """Liste de présence manuelle avec dates étalées"""
    conn = get_db()
    exam_id = request.args.get("exam_id")
    exam = _get_exam_or_latest(conn, exam_id)
    
    # Paramètres de la période d'examens
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    try:
        num_days = int(request.args.get("num_days", "7"))
    except (ValueError, TypeError):
        num_days = 7  # Valeur par défaut si erreur de conversion
    
    cur = conn.cursor()
    # Récupérer le nom du local
    cur.execute("SELECT name FROM rooms WHERE id = ?", (room_id,))
    room = cur.fetchone()
    room_name = room["name"] if room else "Local inconnu"
    
    # Récupérer les étudiants assignés
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
    
    return render_template("presence.html", rows=rows, exam=exam, room_name=room_name, dates=dates)


@app.get("/api/validate/<token>")
def validate_presence(token):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.id as assignment_id, s.full_name, r.name as room_name, e.label, e.session_type
        FROM assignments a
        JOIN students s ON a.student_id = s.id
        JOIN rooms r ON a.room_id = r.id
        JOIN exams e ON a.exam_id = e.id
        WHERE a.qr_token = ?
        """,
        (token,),
    )
    row = cur.fetchone()
    if not row:
        return jsonify({"status": "error", "message": "QR inconnu"}), 404

    cur.execute(
        """
        INSERT INTO presence (assignment_id, status, scanned_at)
        VALUES (?, 'present', ?)
        ON CONFLICT(assignment_id) DO UPDATE SET
            status='present',
            scanned_at=excluded.scanned_at
        """,
        (row["assignment_id"], datetime.utcnow().isoformat()),
    )
    conn.commit()
    return jsonify(
        {
            "status": "ok",
            "student": row["full_name"],
            "room": row["room_name"],
            "exam": row["label"],
            "session": row["session_type"],
        }
    )


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

#if __name__ == "__main__":
 #   app.run(debug=True, host="127.0.0.1", port=5000)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)