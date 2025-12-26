import math
import random
import secrets
from collections import deque, defaultdict


def _room_capacity(room):
    return room["benches"] * room["students_per_bench"]


def equitable_order(students):
    """Round-robin students by promotion to keep rooms balanced."""
    by_promo = defaultdict(deque)
    for stu in students:
        by_promo[stu["promotion_id"]].append(stu)

    promo_ids = list(by_promo.keys())
    ordered = []
    keep_looping = True
    while keep_looping:
        keep_looping = False
        for promo_id in promo_ids:
            if by_promo[promo_id]:
                ordered.append(by_promo[promo_id].popleft())
                keep_looping = True
    return ordered


def distribute_students(conn, exam_id, promo_counts=None):
    """
    Distribue les étudiants dans les locaux.
    
    Args:
        conn: Connexion à la base de données
        exam_id: ID de l'examen
        promo_counts: Dictionnaire {promotion_id: nombre} pour limiter le nombre d'étudiants par promotion.
                     Si None, prend tous les étudiants.
    """
    cur = conn.cursor()
    
    # Récupérer toutes les promotions associées à cet examen
    cur.execute(
        """
        SELECT promotion_id 
        FROM exam_promotions 
        WHERE exam_id = ?
        """,
        (exam_id,)
    )
    promotion_rows = cur.fetchall()
    
    if not promotion_rows:
        # Fallback: utiliser promotion_id de l'examen (ancien système)
        cur.execute("SELECT promotion_id FROM exams WHERE id = ?", (exam_id,))
        exam_row = cur.fetchone()
        if not exam_row or not exam_row["promotion_id"]:
            return {"assigned": 0, "rooms": 0}
        promotion_ids = [exam_row["promotion_id"]]
    else:
        promotion_ids = [row["promotion_id"] for row in promotion_rows]
    
    # Récupérer tous les étudiants par promotion (sans limite pour l'instant)
    students_by_promo = {}
    for promo_id in promotion_ids:
        cur.execute(
            """
            SELECT s.*, p.name as promotion_name, sec.name as section_name
            FROM students s
            JOIN promotions p ON s.promotion_id = p.id
            LEFT JOIN sections sec ON s.section_id = sec.id
            WHERE s.promotion_id = ?
            ORDER BY s.full_name
            """,
            (promo_id,)
        )
        promo_students = list(cur.fetchall())
        students_by_promo[promo_id] = promo_students
    
    # Si promo_counts est spécifié, limiter le nombre total par promotion
    if promo_counts:
        for promo_id in students_by_promo:
            if promo_id in promo_counts:
                max_count = promo_counts[promo_id]
                students_by_promo[promo_id] = students_by_promo[promo_id][:max_count]

    cur.execute("SELECT * FROM rooms ORDER BY name")
    rooms = cur.fetchall()

    if not rooms:
        return {"assigned": 0, "rooms": 0}
    
    # Vérifier qu'il y a des étudiants
    total_students = sum(len(students) for students in students_by_promo.values())
    if total_students == 0:
        return {"assigned": 0, "rooms": len(rooms)}

    cur.execute("DELETE FROM assignments WHERE exam_id = ?", (exam_id,))
    cur.execute(
        "DELETE FROM sqlite_sequence WHERE name='assignments'"
    )  # keep ids small after refresh

    seat_map = []
    rooms_used = 0
    
    # Créer des queues pour chaque promotion pour faciliter le round-robin
    promo_queues = {}
    for promo_id, students in students_by_promo.items():
        promo_queues[promo_id] = deque(students)
    
    promo_ids_list = list(promo_queues.keys())
    
    # Pour chaque local, remplir banc par banc en évitant les promotions identiques sur le même banc
    for room in rooms:
        benches = room["benches"]
        students_per_bench = room["students_per_bench"]
        capacity = _room_capacity(room)
        
        if capacity <= 0 or benches <= 0 or students_per_bench <= 0:
            continue
        
        # Vérifier s'il reste des étudiants
        has_students = any(promo_queues[pid] for pid in promo_ids_list)
        if not has_students:
            continue
        
        rooms_used += 1
        seat_number = 1  # Numéro de place global dans le local
        
        # Pour chaque banc dans le local
        for bench_num in range(1, benches + 1):
            bench_students = []
            bench_promo_index = 0
            
            # Mélanger l'ordre des promotions pour ce banc pour éviter les patterns répétitifs
            shuffled_promo_order = list(promo_ids_list)
            random.shuffle(shuffled_promo_order)
            
            # Remplir ce banc avec des étudiants de promotions différentes (round-robin)
            # Priorité : éviter au maximum les promotions identiques sur le même banc
            attempts = 0
            max_attempts = students_per_bench * len(promo_ids_list) * 3
            
            while len(bench_students) < students_per_bench and attempts < max_attempts:
                # Obtenir les promotions déjà présentes sur ce banc
                bench_promos = [s[1] for s in bench_students]
                
                # Trouver les promotions disponibles (qui ont encore des étudiants)
                available_promos = [pid for pid in shuffled_promo_order if promo_queues[pid]]
                
                if not available_promos:
                    break
                
                # Prioriser les promotions qui ne sont pas encore sur ce banc
                preferred_promos = [pid for pid in available_promos if pid not in bench_promos]
                
                # Si on a des promotions non encore utilisées sur ce banc, les utiliser en priorité
                if preferred_promos:
                    # Utiliser round-robin parmi les promotions préférées
                    promo_id = preferred_promos[bench_promo_index % len(preferred_promos)]
                else:
                    # Si toutes les promotions disponibles sont déjà sur le banc,
                    # utiliser round-robin parmi toutes les promotions disponibles
                    # (cas où une promotion a beaucoup plus d'étudiants)
                    promo_id = available_promos[bench_promo_index % len(available_promos)]
                
                # Prendre un étudiant de cette promotion
                student = promo_queues[promo_id].popleft()
                bench_students.append((student, promo_id))
                bench_promo_index += 1
                attempts += 1
            
            # Assigner les places dans ce banc
            for student, _ in bench_students:
                qr_token = f"ASSIGN-{secrets.token_urlsafe(12)}"
                seat_map.append(
                    (
                        student["id"],
                        exam_id,
                        room["id"],
                        seat_number,
                        qr_token,
                    )
                )
                seat_number += 1

    cur.executemany(
        """
        INSERT INTO assignments (student_id, exam_id, room_id, seat_number, qr_token)
        VALUES (?, ?, ?, ?, ?)
        """,
        seat_map,
    )
    conn.commit()
    return {"assigned": len(seat_map), "rooms": rooms_used}





