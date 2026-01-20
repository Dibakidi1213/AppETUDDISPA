# Exemple d'Import Excel - Superviseurs

## Format d'Importation

Créez un fichier Excel avec exactement ces colonnes (respect de la casse):

```
username | password | exam_id | room_id | exam_date
```

## Explication des Colonnes

### Colonnes Obligatoires

1. **username** (Texte)
   - Identifiant unique du surveillant
   - Ne peut contenir que des caractères alphanumériques
   - Exemple: `sup_ali`, `surveillant_1`, `fatima`

2. **password** (Texte)
   - Mot de passe initial du surveillant
   - Sera hashé immédiatement après l'import
   - Exemple: `Pass123@`, `secret_pwd`, `123456`

3. **exam_id** (Nombre entier)
   - Identifiant de l'examen
   - Doit exister dans la table `exams`
   - Exemple: `1`, `2`, `3`

4. **room_id** (Nombre entier)
   - Identifiant du local
   - Doit exister dans la table `rooms`
   - Exemple: `5`, `6`, `10`

### Colonne Optionnelle

5. **exam_date** (Date, optionnel)
   - Date de l'examen au format YYYY-MM-DD
   - Si non fournie, utilise la date actuelle
   - Exemple: `2026-01-15`, `2026-02-20`

---

## Exemples Pratiques

### Exemple 1: Simple (sans date)

```
username      password  exam_id  room_id
ali           pass123   1        5
fatima        pass456   1        6
hassan        pass789   2        5
amina         pass000   2        6
```

**Résultat:**
- Ali surveille l'exam 1 au local 5
- Fatima surveille l'exam 1 au local 6
- Hassan surveille l'exam 2 au local 5
- Amina surveille l'exam 2 au local 6

### Exemple 2: Avec Dates

```
username      password  exam_id  room_id  exam_date
ali           pass123   1        5        2026-01-15
fatima        pass456   1        6        2026-01-15
hassan        pass789   2        5        2026-01-16
amina         pass000   2        6        2026-01-16
khalid        pass111   1        7        2026-01-15
```

**Résultat:**
- 15 janvier: Ali, Fatima et Khalid surveillent
- 16 janvier: Hassan et Amina surveillent

### Exemple 3: Même Surveillant Plusieurs Dates

```
username      password  exam_id  room_id  exam_date
ali           pass123   1        5        2026-01-15
ali           pass123   2        6        2026-01-16
fatima        pass456   1        6        2026-01-15
fatima        pass456   2        5        2026-01-16
```

**Résultat:**
- Ali: 15 janv (Exam1 Local5) + 16 janv (Exam2 Local6)
- Fatima: 15 janv (Exam1 Local6) + 16 janv (Exam2 Local5)

### Exemple 4: Université Complète

```
username              password  exam_id  room_id  exam_date
sup_math_morning      pwd001    1        1        2026-01-15
sup_math_afternoon    pwd002    3        1        2026-01-15
sup_french_morning    pwd003    2        2        2026-01-15
sup_french_afternoon  pwd004    4        2        2026-01-15
sup_english_morning   pwd005    5        3        2026-01-16
sup_english_afternoon pwd006    6        3        2026-01-16
sup_history_morning   pwd007    7        4        2026-01-16
sup_history_afternoon pwd008    8        4        2026-01-16
```

---

## Procédure d'Import

1. **Préparer le fichier**
   - Ouvrir Excel ou LibreOffice Calc
   - Créer les colonnes: username, password, exam_id, room_id, exam_date
   - Remplir les données
   - Sauvegarder en format `.xlsx` ou `.xls`

2. **Vérifier les données**
   - Tous les usernames sont uniques
   - Tous les exam_id existent dans la base
   - Tous les room_id existent dans la base
   - Les dates sont au format YYYY-MM-DD

3. **Importer via l'interface**
   - Aller à: Menu Admin → Import & assignation surveillants
   - Cliquer sur "Choisir le fichier"
   - Sélectionner votre fichier Excel
   - Cliquer sur "📥 Importer & Assigner"

4. **Vérifier le résultat**
   - Message de confirmation: "X surveillants créés, Y assignations effectuées"
   - Aller à "Affecter surveillants (tableau)"
   - Vérifier que les affectations s'affichent correctement

---

## Gestion des Erreurs

### Erreur: "Colonnes obligatoires manquantes"

**Cause:** Les noms des colonnes ne correspondent pas exactement

**Solution:** Vérifiez:
- ✅ Exactement: `username`, `password`, `exam_id`, `room_id`
- ✅ Pas d'espaces inutiles
- ✅ Pas d'accents
- ✅ Minuscules

### Erreur: "Impossible de lire le fichier"

**Cause:** Format d'fichier incorrect

**Solution:** 
- ✅ Utilisez Excel (.xlsx) ou Calc (.xls)
- ✅ Pas de format ODS
- ✅ Pas de fichier corrompu

### Erreur: Ligne ignorée (aucun message)

**Cause:** Une ou plusieurs données manquent

**Solution:** Vérifiez pour cette ligne:
- ✅ username n'est pas vide
- ✅ password n'est pas vide
- ✅ exam_id est un nombre
- ✅ room_id est un nombre
- ✅ exam_date (si fournie) au format YYYY-MM-DD

### Erreur: "0 assignations effectuées"

**Cause:** Les IDs d'exam ou de room n'existent pas

**Solution:**
1. Créez d'abord les exams et les rooms dans l'application
2. Récupérez leurs IDs
3. Utilisez ces IDs dans l'Excel

---

## Cas d'Usage Pratiques

### Scénario 1: Changement de Surveillant Dernier Moment

Quelqu'un ne peut pas venir, vous avez un remplaçant.

**Action:**
1. Créez une nouvelle ligne avec le remplaçant
2. Importez juste cette ligne (oui, c'est possible en sélectionnant juste cette partie)
3. L'application le crée et l'assigne

### Scénario 2: Exams Échelonnés sur Deux Semaines

Vous avez 20 exams, 40 surveill ants, 10 locaux sur 2 semaines.

**Stratégie:**
1. Créez un Excel avec toutes les combinaisons
2. Une ligne par assignment
3. Import une seule fois
4. Générez les listes par date

### Scénario 3: Mutation de Personnels

Des surveillants changent de local/exams.

**Action:**
1. Exportez les anciennes assignations
2. Modifiez les room_id ou exam_id
3. Supprimez les anciennes dans l'interface
4. Importez les nouvelles

---

## Conseils pour Organisateurs

✅ **Faites des backups**: Sauvegardez votre Excel avant import  
✅ **Testez d'abord**: Importez 5 lignes d'abord, puis le reste  
✅ **Dates cohérentes**: Utilisez toujours le même format  
✅ **Noms simples**: Pas d'accents ni caractères spéciaux  
✅ **Vérification**: Imprimez après import pour vérifier  
✅ **Archives**: Gardez l'Excel d'import pour l'historique  

---

## Template Excel à Télécharger

Demandez à l'administrateur le fichier template:
- `supervisors_template.xlsx`

Contient:
- En-têtes pré-formatés
- Exemples de données
- Validations de plages
- Instructions en commentaires

---

## Statistiques

Après un import réussi, vous verrez:

```
"5 surveillants créés, 12 assignations effectuées."
```

Cela signifie:
- ✅ 5 nouveaux utilisateurs ont été créés
- ✅ 12 affectations (assignations à des exam/room/date) ont été enregistrées

**Note:** Un utilisateur existant qui est ré-assigné ne compte que dans "assignations", pas "créés".

---

**Dernière mise à jour:** Janvier 2026  
**Format Excel supporté:** .xlsx, .xls  
**Encodage:** UTF-8 recommandé
