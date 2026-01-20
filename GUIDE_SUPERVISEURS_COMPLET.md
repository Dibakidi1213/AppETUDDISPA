# 📋 Guide Complet - Gestion des Surveillants par Date et Local

## Vue d'ensemble

Ce guide vous explique comment affecter les surveillants pour chaque local par rapport à une date spécifique, et comment imprimer les listes de surveillance pour chaque date et chaque local.

## 🎯 Objectifs du Système

✅ **Affecter les surveillants** à des locaux spécifiques pour une date donnée  
✅ **Gérer les exams** avec session type (Matin/Après-midi)  
✅ **Imprimer les listes** de surveillants formatées et prêtes à l'affichage  
✅ **Importer en masse** les affectations via Excel  

---

## 🔑 Accès aux Fonctionnalités

Connectez-vous en tant qu'**administrateur** et allez au menu:

```
Menu Admin → Gestion Surveillants → Affecter surveillants (tableau)
```

Ou cliquez directement sur: **Affecter surveillants (tableau)**

---

## 1️⃣ Affectation Manuelle des Surveillants

### Interface: `/admin/assign_supervisors`

### Étapes:

1. **Sélectionner une Date**
   - Cliquez sur le champ "Date de l'examen"
   - Choisissez la date (format: YYYY-MM-DD)

2. **Choisir un Examen/Session**
   - Sélectionnez dans la liste des exams
   - Format affiché: [Session] Nom de l'examen
   - Exemple: [Matin] Mathématiques

3. **Sélectionner un Local**
   - Choisissez le local (salle de classe)
   - Les locaux disponibles s'affichent dans la liste

4. **Choisir un Surveillant**
   - Sélectionnez parmi les surveillants disponibles
   - Assurez-vous que l'utilisateur n'est pas administrateur

5. **Assigner**
   - Cliquez sur le bouton "➕ Assigner"
   - Une confirmation "Affectation enregistrée" s'affiche

### Tableau des Affectations

Après avoir sélectionné une date, un tableau affiche:

| Colonne | Description |
|---------|-------------|
| **Local** | Nom du local assigné |
| **Session** | Type de session (Matin/Après-midi) |
| **Examen** | Nom de l'examen |
| **Surveillant** | Nom de l'utilisateur |
| **Actions** | Bouton pour retirer l'assignation |

### Actions Disponibles

- **Retirer**: Supprime une affectation spécifique
- **Imprimer**: Génère et imprime les listes pour la date sélectionnée

---

## 2️⃣ Import en Masse des Assignations

### Accès: `/admin/import_assign_supervisors`

Menu → **Import & assignation surveillants**

### Format du Fichier Excel

Créez un fichier Excel (.xlsx ou .xls) avec les colonnes:

| Colonne | Type | Obligatoire | Description |
|---------|------|-------------|-------------|
| **username** | Texte | ✅ | Identifiant du surveillant |
| **password** | Texte | ✅ | Mot de passe (créé s'il n'existe pas) |
| **exam_id** | Nombre | ✅ | ID de l'examen |
| **room_id** | Nombre | ✅ | ID du local |
| **exam_date** | Date | ❌ | Date de l'examen (YYYY-MM-DD) |

### Exemple de Fichier Excel

```
username        | password | exam_id | room_id | exam_date
sup_ali         | pass123  | 1       | 5       | 2026-01-15
sup_fatima      | pass456  | 1       | 6       | 2026-01-15
sup_hassan      | pass789  | 2       | 5       | 2026-01-16
sup_amina       | pass000  | 2       | 6       | 2026-01-16
```

### Processus d'Import

1. Cliquez sur "Choisir le fichier"
2. Sélectionnez votre fichier Excel
3. Cliquez sur "📥 Importer & Assigner"
4. Les surveillants seront créés (s'ils n'existent pas)
5. Les affectations seront enregistrées

### Messages de Confirmation

```
"X surveillants créés, Y assignations effectuées."
```

---

## 3️⃣ Impression des Listes de Surveillance

### Accès: `/admin/print_supervisors`

### Deux Moyens d'Imprimer

#### Méthode 1: Via le Tableau d'Affectation
1. Allez à "Affecter surveillants (tableau)"
2. Sélectionnez une date
3. Cliquez sur "🖨️ Imprimer pour [DATE]"

#### Méthode 2: Via URL directe
```
/admin/print_supervisors?date=2026-01-15
```

### Format d'Impression

Les listes sont formatées de manière professionnelle:

```
╔════════════════════════════════════════════════╗
║        MINISTÈRE DE L'ENSEIGNEMENT SUPÉRIEUR   ║
║                                                ║
║           LISTE DES SURVEILLANTS               ║
║                                                ║
║        LOCAL: AMPHI A                          ║
║        Date: 2026-01-15                        ║
╠════════════════════════════════════════════════╣
║ [Matin] - Mathématiques                        ║
║ ─────────────────────────────────────────────  ║
║ N° │ Surveillant                               ║
║ ── │ ────────────────────────────────────────  ║
║  1 │ Superviseur Ali                           ║
║  2 │ Superviseur Fatima                        ║
║ ─────────────────────────────────────────────  ║
║                                                ║
║ Signature du responsable: _______________     ║
╚════════════════════════════════════════════════╝
```

### Options d'Impression

- **Imprimer depuis navigateur**: Cliquez sur "🖨️ Imprimer" ou Ctrl+P
- **Sauvegarder en PDF**: Fichier → Imprimer → Sauvegarder en PDF
- **Une page par local**: Les locaux différents s'impriment sur des pages séparées

---

## 📊 Structure de la Base de Données

### Table: `supervisor_assignments`

```sql
CREATE TABLE supervisor_assignments (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,           -- Référence au surveillant
    exam_id INTEGER NOT NULL,           -- Référence à l'examen
    room_id INTEGER NOT NULL,           -- Référence au local
    exam_date TEXT,                     -- Date de l'examen
    assigned_at TEXT DEFAULT CURRENT_TIMESTAMP  -- Date d'affectation
);
```

### Clés Étrangères et Contraintes

- **UNIQUE(user_id, exam_id, room_id, exam_date)**: Évite les doublons
- **Indices**: Optimisent les requêtes par date et room

---

## 🔍 Cas d'Usage Pratiques

### Cas 1: Exams sur Plusieurs Jours

**Jour 1 (2026-01-15):**
- Matin: Mathématiques
- Après-midi: Français

**Jour 2 (2026-01-16):**
- Matin: Anglais
- Après-midi: Histoire

**Solution:**
1. Créez 2 exams pour chaque jour
2. Affectez les surveillants par date
3. Imprimez une liste par jour

### Cas 2: Même Surveillant sur Plusieurs Locaux

**Ali doit surveiller:**
- Amphi A - Exam 1 - 2026-01-15
- Amphi B - Exam 2 - 2026-01-15

**Solution:**
1. Affectez Ali à Amphi A
2. Affectez Ali à Amphi B
3. Les deux affectations s'afficheront ensemble dans les listes

### Cas 3: Import en Masse d'une Université

1. Créez un Excel avec tous les surveill ants et leurs affectations
2. Incluez les dates d'examen
3. Importer une seule fois
4. Générez les listes pour chaque date

---

## ⚙️ Configuration Technique

### Variables d'Environnement

```bash
APP_DB_PATH=app.db              # Chemin de la base de données
APP_SECRET=your-secret-key       # Clé de session
```

### Indices pour Performance

Les requêtes sont optimisées avec:
- Index sur `(exam_date, room_id)` pour l'affichage
- Index sur `(user_id, exam_id)` pour la vérification de doublons

---

## 🐛 Dépannage

### Problème: "Affectation enregistrée" mais elle n'apparaît pas

**Cause**: La date n'a pas été sélectionnée correctement
**Solution**: Cliquez sur "Filtrer par date" après avoir assigné

### Problème: L'impression affiche des caractères spéciaux

**Cause**: Problème d'encodage
**Solution**: Assurez-vous que le fichier est en UTF-8

### Problème: L'import échoue

**Vérifiez:**
- ✅ Les colonnes obligatoires existent (username, password, exam_id, room_id)
- ✅ Les IDs d'exam et de local existent dans la base de données
- ✅ Le format du fichier est bien .xlsx ou .xls
- ✅ Les dates sont au format YYYY-MM-DD (si fournies)

---

## 📞 Support et Questions

Pour toute question ou problème:
1. Vérifiez ce guide
2. Consultez les logs de l'application
3. Vérifiez la connexion à la base de données

---

## 📝 Notes Importantes

⚠️ **Sécurité**: Les mots de passe importés sont hash és immédiatement  
⚠️ **Doublons**: Le système empêche les affectations en doublon  
⚠️ **Suppression**: Retirer une affectation ne supprime pas l'utilisateur  
⚠️ **Dates**: Utilisez toujours le format YYYY-MM-DD pour cohérence  

---

**Dernière mise à jour**: Janvier 2026  
**Version**: 2.0 - Avec support des dates par affectation
