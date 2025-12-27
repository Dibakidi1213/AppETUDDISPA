import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("APP_DB_PATH", "app.db"))


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            promotion_id INTEGER NOT NULL,
            UNIQUE(name, promotion_id),
            FOREIGN KEY (promotion_id) REFERENCES promotions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            matricule TEXT,
            full_name TEXT NOT NULL,
            sexe TEXT,
            promotion_id INTEGER NOT NULL,
            section_id INTEGER,
            UNIQUE(matricule),
            FOREIGN KEY (promotion_id) REFERENCES promotions(id) ON DELETE CASCADE,
            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            benches INTEGER NOT NULL DEFAULT 0,
            students_per_bench INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            session_type TEXT NOT NULL,
            promotion_id INTEGER NOT NULL,
            FOREIGN KEY (promotion_id) REFERENCES promotions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            exam_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            seat_number INTEGER NOT NULL,
            qr_token TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, exam_id),
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
            FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS presence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            scanned_at TEXT,
            FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS exam_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            promotion_id INTEGER NOT NULL,
            UNIQUE(exam_id, promotion_id),
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
            FOREIGN KEY (promotion_id) REFERENCES promotions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    # Migration: ajouter la colonne sexe si elle n'existe pas
    try:
        cur.execute("ALTER TABLE students ADD COLUMN sexe TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        # La colonne existe déjà, c'est OK
        pass
    
    # Migration: créer la table exam_promotions si elle n'existe pas déjà
    # Cette table permet plusieurs promotions par examen
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            promotion_id INTEGER NOT NULL,
            UNIQUE(exam_id, promotion_id),
            FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
            FOREIGN KEY (promotion_id) REFERENCES promotions(id) ON DELETE CASCADE
        )
    """)
    
    # Migration: migrer la table exams vers le nouveau schéma (sans exam_date, start_time, end_time)
    try:
        cur.execute("PRAGMA table_info(exams)")
        columns = [row[1] for row in cur.fetchall()]
        
        # Si la table a encore exam_date, on doit recréer la table
        if "exam_date" in columns:
            # Sauvegarder les données existantes
            cur.execute("SELECT id, label, promotion_id FROM exams")
            old_exams = cur.fetchall()
            
            # Créer la nouvelle table
            cur.execute("DROP TABLE IF EXISTS exams_new")
            cur.execute("""
                CREATE TABLE exams_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    session_type TEXT NOT NULL,
                    promotion_id INTEGER NOT NULL,
                    FOREIGN KEY (promotion_id) REFERENCES promotions(id) ON DELETE CASCADE
                )
            """)
            
            # Migrer les données : déterminer session_type basé sur start_time si disponible
            if "start_time" in columns:
                cur.execute("SELECT id, label, promotion_id, start_time FROM exams")
                old_exams_with_time = cur.fetchall()
                for exam in old_exams_with_time:
                    session_type = "Matin"  # Par défaut
                    if exam["start_time"]:
                        try:
                            hour = int(exam["start_time"].split(":")[0])
                            if hour < 12:
                                session_type = "Matin"
                            elif hour < 18:
                                session_type = "Après-midi"
                            else:
                                session_type = "Matin"  # Par défaut
                        except:
                            pass
                    cur.execute(
                        "INSERT INTO exams_new (id, label, session_type, promotion_id) VALUES (?, ?, ?, ?)",
                        (exam["id"], exam["label"], session_type, exam["promotion_id"])
                    )
            else:
                # Pas de start_time, on met Matin par défaut
                for exam in old_exams:
                    cur.execute(
                        "INSERT INTO exams_new (id, label, session_type, promotion_id) VALUES (?, ?, ?, ?)",
                        (exam["id"], exam["label"], "Matin", exam["promotion_id"])
                    )
            
            conn.commit()
            
            # Supprimer l'ancienne table et renommer la nouvelle
            cur.execute("DROP TABLE exams")
            cur.execute("ALTER TABLE exams_new RENAME TO exams")
            
            # Migrer les données existantes vers exam_promotions si nécessaire
            cur.execute("""
                INSERT OR IGNORE INTO exam_promotions (exam_id, promotion_id)
                SELECT id, promotion_id FROM exams WHERE promotion_id IS NOT NULL
            """)
            
            conn.commit()
        elif "session_type" not in columns:
            # La table n'a pas exam_date mais n'a pas non plus session_type
            # Ajouter session_type (sans NOT NULL car SQLite ne le supporte pas dans ALTER TABLE)
            cur.execute("ALTER TABLE exams ADD COLUMN session_type TEXT")
            cur.execute("UPDATE exams SET session_type = 'Matin' WHERE session_type IS NULL")
            conn.commit()
    except sqlite3.OperationalError as e:
        # En cas d'erreur, on continue
        print(f"Migration warning: {e}")
        pass
    
    # Migration: créer la table users si elle n'existe pas
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        
        # Créer un compte administrateur par défaut si aucun utilisateur n'existe
        cur.execute("SELECT COUNT(*) as count FROM users")
        user_count = cur.fetchone()["count"]
        if user_count == 0:
            from werkzeug.security import generate_password_hash
            default_password = generate_password_hash("admin")
            cur.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                ("admin", default_password, 1)
            )
            conn.commit()
            print("Compte administrateur créé par défaut: username='admin', password='admin'")
    except sqlite3.OperationalError as e:
        print(f"Migration users warning: {e}")
        pass
    
    # Migration: créer la table assignment_archives pour archiver les affectations
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS assignment_archives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                archive_name TEXT NOT NULL,
                exam_id INTEGER NOT NULL,
                exam_label TEXT NOT NULL,
                exam_session_type TEXT,
                period_start_date TEXT NOT NULL,
                period_end_date TEXT NOT NULL,
                student_id INTEGER NOT NULL,
                student_matricule TEXT,
                student_full_name TEXT NOT NULL,
                student_promotion_id INTEGER,
                student_promotion_name TEXT,
                student_section_id INTEGER,
                student_section_name TEXT,
                room_id INTEGER NOT NULL,
                room_name TEXT NOT NULL,
                seat_number INTEGER NOT NULL,
                qr_token TEXT,
                archived_at TEXT DEFAULT CURRENT_TIMESTAMP,
                archived_by INTEGER,
                FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE SET NULL,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE SET NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE SET NULL,
                FOREIGN KEY (archived_by) REFERENCES users(id) ON DELETE SET NULL
            )
        """)
        conn.commit()
    except sqlite3.OperationalError as e:
        print(f"Migration assignment_archives warning: {e}")
        pass
    
    conn.commit()
    conn.close()





