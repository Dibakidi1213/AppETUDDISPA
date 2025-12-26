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
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for("upload_students"))


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
            return redirect(url_for("upload_students"))
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
    if request.method == "POST":
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
    
    return render_template("rooms.html", rooms=rooms_list, exam=exam, exams=exams_list)


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
        if confirm == "RESET":
            conn = get_db()
            cur = conn.cursor()
            try:
                # Supprimer toutes les données dans l'ordre pour respecter les contraintes
                cur.execute("DELETE FROM presence")
                cur.execute("DELETE FROM assignments")
                cur.execute("DELETE FROM exam_promotions")
                cur.execute("DELETE FROM exams")
                cur.execute("DELETE FROM students")
                cur.execute("DELETE FROM sections")
                cur.execute("DELETE FROM promotions")
                # Les rooms sont conservées (optionnel, on peut les supprimer aussi)
                # cur.execute("DELETE FROM rooms")
                conn.commit()
                flash("Toutes les données ont été supprimées avec succès.", "success")
            except Exception as e:
                flash(f"Erreur lors de la réinitialisation: {str(e)}", "danger")
            return redirect(url_for("index"))
        else:
            flash("Confirmation incorrecte. Tapez 'RESET' pour confirmer.", "danger")
    
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

#if __name__ == "__main__":
 #   app.run(debug=True, host="127.0.0.1", port=5000)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)