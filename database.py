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
    
    # Migration: ajouter la colonne scanned_by à la table presence
    try:
        cur.execute("PRAGMA table_info(presence)")
        columns = [row[1] for row in cur.fetchall()]
        if "scanned_by" not in columns:
            cur.execute("ALTER TABLE presence ADD COLUMN scanned_by INTEGER")
            cur.execute("ALTER TABLE presence ADD COLUMN scan_date TEXT")
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_presence_scanned_by 
                ON presence(scanned_by)
            """)
            conn.commit()
            print("Migration: colonne scanned_by ajoutée à la table presence")
    except sqlite3.OperationalError as e:
        print(f"Migration presence scanned_by warning: {e}")
        pass
    
    # Migration: créer la table supervisor_assignments pour associer les surveillants aux locaux
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supervisor_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                exam_id INTEGER NOT NULL,
                room_id INTEGER NOT NULL,
                exam_date TEXT,
                is_room_leader INTEGER NOT NULL DEFAULT 0,
                assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, exam_id, room_id, exam_date),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE,
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_supervisor_assignments_user_exam 
            ON supervisor_assignments(user_id, exam_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_supervisor_assignments_exam_date 
            ON supervisor_assignments(exam_date, room_id)
        """)
        conn.commit()
        print("Migration: table supervisor_assignments créée")
    except sqlite3.OperationalError as e:
        # La table existe peut-être déjà, essayons d'ajouter la colonne exam_date et is_room_leader
        try:
            cur.execute("PRAGMA table_info(supervisor_assignments)")
            columns = [row[1] for row in cur.fetchall()]
            if "exam_date" not in columns:
                cur.execute("ALTER TABLE supervisor_assignments ADD COLUMN exam_date TEXT")
                # Migrer les données existantes : utiliser assigned_at[:10] comme exam_date
                cur.execute("""
                    UPDATE supervisor_assignments 
                    SET exam_date = SUBSTR(assigned_at, 1, 10)
                    WHERE exam_date IS NULL
                """)
                # Modifier l'unicité (SQLite ne supporte pas directement ALTER UNIQUE)
                # On laisse juste la colonne pour compatibilité
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_supervisor_assignments_exam_date 
                    ON supervisor_assignments(exam_date, room_id)
                """)
                conn.commit()
                print("Migration: colonne exam_date ajoutée à supervisor_assignments")
            
            if "is_room_leader" not in columns:
                cur.execute("ALTER TABLE supervisor_assignments ADD COLUMN is_room_leader INTEGER NOT NULL DEFAULT 0")
                conn.commit()
                print("Migration: colonne is_room_leader ajoutée à supervisor_assignments")
        except Exception as e2:
            print(f"Migration exam_date/is_room_leader warning: {e2}")
    
    # Migration: ajouter is_room_leader si elle n'existe pas
    try:
        cur.execute("ALTER TABLE supervisor_assignments ADD COLUMN is_room_leader INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        print("Migration: colonne is_room_leader ajoutée à supervisor_assignments")
    except sqlite3.OperationalError:
        # La colonne existe déjà
        pass
    
    # Créer la table d'archives pour les surveillants
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supervisor_archives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                archive_name TEXT NOT NULL,
                exam_date TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                exam_id INTEGER,
                exam_label TEXT,
                room_id INTEGER NOT NULL,
                room_name TEXT NOT NULL,
                is_room_leader INTEGER NOT NULL DEFAULT 0,
                archived_at TEXT DEFAULT CURRENT_TIMESTAMP,
                archived_by INTEGER NOT NULL,
                FOREIGN KEY (archived_by) REFERENCES users(id)
            )
        """)
        conn.commit()
        print("Migration: table supervisor_archives créée")
    except sqlite3.OperationalError as e:
        print(f"Migration warning supervisor_archives: {e}")
        pass
    
    # Migration: créer la table messages pour la messagerie surveillant-admin
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                recipient_id INTEGER,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                exam_id INTEGER,
                room_id INTEGER,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (recipient_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE SET NULL,
                FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE SET NULL
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_sender 
            ON messages(sender_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_recipient 
            ON messages(recipient_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_created_at 
            ON messages(created_at DESC)
        """)
        conn.commit()
        print("Migration: table messages créée pour la messagerie")
    except sqlite3.OperationalError as e:
        print(f"Migration messages warning: {e}")
        pass
    
    # Migration: créer la table notifications pour les notifications en temps réel
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_user 
            ON notifications(user_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_notifications_created_at 
            ON notifications(created_at DESC)
        """)
        conn.commit()
        print("Migration: table notifications créée pour les notifications")
    except sqlite3.OperationalError as e:
        print(f"Migration notifications warning: {e}")
        pass
    
    # Migration: créer la table payment_rates pour les tarifs de paiement
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payment_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                rate_type TEXT NOT NULL CHECK (rate_type IN ('daily', 'session')),
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'CDF',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payment_rates_active 
            ON payment_rates(is_active)
        """)
        conn.commit()
        print("Migration: table payment_rates créée pour les tarifs de paiement")
    except sqlite3.OperationalError as e:
        print(f"Migration payment_rates warning: {e}")
        pass
    
    # Migration: créer la table calculated_payments pour l'historique des paiements calculés
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS calculated_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supervisor_id INTEGER NOT NULL,
                exam_id INTEGER,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                total_sessions INTEGER NOT NULL DEFAULT 0,
                total_days INTEGER NOT NULL DEFAULT 0,
                rate_id INTEGER NOT NULL,
                amount_per_session REAL NOT NULL DEFAULT 0,
                amount_per_day REAL NOT NULL DEFAULT 0,
                total_amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'CDF',
                calculated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'calculated' CHECK (status IN ('calculated', 'paid', 'cancelled')),
                notes TEXT,
                FOREIGN KEY (supervisor_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE SET NULL,
                FOREIGN KEY (rate_id) REFERENCES payment_rates(id) ON DELETE CASCADE
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_calculated_payments_supervisor 
            ON calculated_payments(supervisor_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_calculated_payments_dates 
            ON calculated_payments(start_date, end_date)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_calculated_payments_status 
            ON calculated_payments(status)
        """)
        conn.commit()
        print("Migration: table calculated_payments créée pour l'historique des paiements")
    except sqlite3.OperationalError as e:
        print(f"Migration calculated_payments warning: {e}")
        pass
