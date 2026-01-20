#!/usr/bin/env python3
"""
Script de test pour vérifier le système de surveillants
"""

import sqlite3
import os
from werkzeug.security import generate_password_hash

DB_PATH = "app.db"

def test_supervisor_system():
    """Teste les principales fonctionnalités du système de surveillants"""
    print("🧪 Test du système de surveillants\n")
    print("=" * 60)
    
    # Vérifier la base de données
    if not os.path.exists(DB_PATH):
        print("❌ Base de données non trouvée!")
        return False
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    tests_passed = 0
    tests_failed = 0
    
    # Test 1: Table supervisor_assignments existe
    print("\n1️⃣  Vérification de la table supervisor_assignments...")
    try:
        cur.execute("SELECT COUNT(*) FROM supervisor_assignments")
        count = cur.fetchone()[0]
        print(f"   ✅ Table existe ({count} affectations actuelles)")
        tests_passed += 1
    except Exception as e:
        print(f"   ❌ Erreur: {e}")
        tests_failed += 1
    
    # Test 2: Table users existe et a des colonnes is_admin
    print("\n2️⃣  Vérification de la table users...")
    try:
        cur.execute("PRAGMA table_info(users)")
        columns = {row[1]: row[2] for row in cur.fetchall()}
        if 'is_admin' in columns:
            print(f"   ✅ Colonne 'is_admin' existe")
            tests_passed += 1
        else:
            print(f"   ❌ Colonne 'is_admin' manquante")
            tests_failed += 1
    except Exception as e:
        print(f"   ❌ Erreur: {e}")
        tests_failed += 1
    
    # Test 3: Compte admin par défaut existe
    print("\n3️⃣  Vérification du compte administrateur par défaut...")
    try:
        cur.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
        admin_count = cur.fetchone()[0]
        if admin_count > 0:
            cur.execute("SELECT username FROM users WHERE is_admin = 1 LIMIT 1")
            admin_user = cur.fetchone()
            print(f"   ✅ Compte admin trouvé: '{admin_user['username']}'")
            tests_passed += 1
        else:
            print(f"   ❌ Aucun compte administrateur")
            tests_failed += 1
    except Exception as e:
        print(f"   ❌ Erreur: {e}")
        tests_failed += 1
    
    # Test 4: Créer un compte surveillant de test
    print("\n4️⃣  Création d'un compte surveillant de test...")
    try:
        username_test = "supervisor_test"
        password_hash = generate_password_hash("test123")
        
        # D'abord, supprimer si existe
        cur.execute("DELETE FROM users WHERE username = ?", (username_test,))
        conn.commit()
        
        # Créer le compte
        cur.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username_test, password_hash, 0)
        )
        conn.commit()
        
        cur.execute("SELECT id FROM users WHERE username = ?", (username_test,))
        user_id = cur.fetchone()['id']
        print(f"   ✅ Compte surveillant créé (ID: {user_id})")
        tests_passed += 1
    except Exception as e:
        print(f"   ❌ Erreur: {e}")
        tests_failed += 1
    
    # Test 5: Affecter le surveillant de test à un local/exam
    print("\n5️⃣  Affectation du surveillant à un local...")
    try:
        cur.execute("SELECT id FROM exams LIMIT 1")
        exam_result = cur.fetchone()
        
        cur.execute("SELECT id FROM rooms LIMIT 1")
        room_result = cur.fetchone()
        
        if exam_result and room_result:
            exam_id = exam_result['id']
            room_id = room_result['id']
            
            # Supprimer l'affectation si elle existe
            cur.execute(
                "DELETE FROM supervisor_assignments WHERE user_id = ? AND exam_id = ? AND room_id = ?",
                (user_id, exam_id, room_id)
            )
            conn.commit()
            
            # Créer l'affectation
            cur.execute(
                "INSERT INTO supervisor_assignments (user_id, exam_id, room_id) VALUES (?, ?, ?)",
                (user_id, exam_id, room_id)
            )
            conn.commit()
            print(f"   ✅ Surveillant assigné à l'examen {exam_id}, local {room_id}")
            tests_passed += 1
        else:
            print(f"   ⚠️  Pas d'examen ou de local trouvé pour le test")
            tests_failed += 1
    except Exception as e:
        print(f"   ❌ Erreur: {e}")
        tests_failed += 1
    
    # Test 6: Vérifier les affectations du surveillant
    print("\n6️⃣  Vérification des affectations...")
    try:
        cur.execute("""
            SELECT COUNT(*) FROM supervisor_assignments 
            WHERE user_id = ?
        """, (user_id,))
        assignment_count = cur.fetchone()[0]
        print(f"   ✅ Surveillant a {assignment_count} affectation(s)")
        tests_passed += 1
    except Exception as e:
        print(f"   ❌ Erreur: {e}")
        tests_failed += 1
    
    # Test 7: Vérifier les locaux du surveillant
    print("\n7️⃣  Récupération des locaux du surveillant...")
    try:
        cur.execute("""
            SELECT DISTINCT r.id, r.name
            FROM rooms r
            INNER JOIN supervisor_assignments sa ON r.id = sa.room_id
            WHERE sa.user_id = ?
        """, (user_id,))
        rooms = cur.fetchall()
        if rooms:
            print(f"   ✅ Locaux accessibles:")
            for room in rooms:
                print(f"      • {room['name']} (ID: {room['id']})")
            tests_passed += 1
        else:
            print(f"   ⚠️  Aucun local assigné")
    except Exception as e:
        print(f"   ❌ Erreur: {e}")
        tests_failed += 1
    
    # Nettoyage
    try:
        cur.execute("DELETE FROM supervisor_assignments WHERE user_id = ?", (user_id,))
        cur.execute("DELETE FROM users WHERE username = ?", (username_test,))
        conn.commit()
    except:
        pass
    
    conn.close()
    
    # Résumé
    print("\n" + "=" * 60)
    print(f"\n📊 Résumé des tests:")
    print(f"   ✅ Réussis: {tests_passed}")
    print(f"   ❌ Échoués: {tests_failed}")
    print(f"   📈 Total: {tests_passed + tests_failed}")
    
    if tests_failed == 0:
        print("\n🎉 Tous les tests sont passés!")
        return True
    else:
        print(f"\n⚠️  {tests_failed} test(s) ont échoué")
        return False

if __name__ == "__main__":
    success = test_supervisor_system()
    exit(0 if success else 1)
