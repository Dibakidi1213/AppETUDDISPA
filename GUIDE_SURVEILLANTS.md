# 📋 Guide d'utilisation - Système de Surveillants

## 🎯 Vue d'ensemble

L'application JNC_DispaExam supporte maintenant un système complet pour les surveillants (superviseurs). Les surveillants peuvent se connecter avec leurs propres credentials et n'accéder qu'aux locaux auxquels ils sont affectés pour effectuer le pointage de présence.

---

## 👥 Types d'utilisateurs

### 1. **Administrateur** (👑)
- Accès complet à l'application
- Peut gérer les promotions, étudiants, examens, locaux
- Peut créer et supprimer des utilisateurs
- Peut archiver les affectations
- Peut affecter les surveillants aux locaux

### 2. **Surveillant** (👁️)
- Accès limité au tableau de bord personnel
- Peut voir uniquement les locaux auxquels il est affecté
- Peut pointer la présence des étudiants par QR code
- Peut consulter la liste d'appel de ses locaux
- Peut imprimer la liste d'appel

---

## 🔧 Configuration des Surveillants

### Étape 1: Créer des comptes utilisateurs

1. **Se connecter en tant qu'administrateur**
2. Aller à **Administration → Gérer les utilisateurs**
3. Dans le formulaire "Créer un nouvel utilisateur":
   - Entrer le **Nom d'utilisateur** (ex: `jean.dupont`)
   - Entrer un **Mot de passe** sécurisé
   - **NE PAS cocher** "Administrateur" (les surveillants ne sont pas admin)
4. Cliquer sur **✓ Créer l'utilisateur**

```
Exemple:
Username: julie.martin
Password: Secure@Pass123
Administrateur: ☐ (décoché)
```

### Étape 2: Affecter un surveillant à un local

1. Aller à **Locaux** et sélectionner un **Examen/Session**
2. Dans le tableau des locaux, à droite d'un local, cliquer sur **+ Ajouter** (Surveillants)
3. Dans la modal qui apparaît:
   - Sélectionner le surveillant dans la liste déroulante
   - Cliquer sur **Assigner**
4. Le surveillant apparaît dans la liste des surveillants de ce local

**Notes:**
- ✅ Plusieurs surveillants peuvent être affectés au même local
- ✅ Un surveillant peut être affecté à plusieurs locaux différents
- ✅ L'affectation est par examen (un surveillant pour l'examen A au local 1, et l'examen B au local 2)

---

## 👨‍✔️ Utilisation par le Surveillant

### Connexion
1. Accéder à l'application
2. Entrer les identifiants du surveillant:
   - **Utilisateur**: le nom d'utilisateur créé par l'admin
   - **Mot de passe**: le mot de passe défini par l'admin
3. Cliquer sur **Se connecter**
4. Vous êtes automatiquement redirigé vers votre **tableau de bord personnel**

### Tableau de Bord
Le tableau de bord affiche:
- ✅ Tous les examens/sessions auxquels vous êtes assigné
- ✅ Pour chaque session, tous les locaux où vous êtes affecté
- ✅ Les statistiques pour chaque local:
  - 📊 Nombre d'étudiants affectés
  - 🟢 Nombre de présents
  - 🔴 Nombre d'absents

### Pointage de Présence par QR Code

#### Option 1: Scanner les codes
1. Depuis le tableau de bord, cliquer sur **📱 Scanner QR** dans un local
2. Vous êtes redirigé vers l'interface de scan
3. **Scanner le code QR** de la badge de l'étudiant avec un lecteur compatible
4. Le système enregistre automatiquement la présence
5. L'interface affiche la confirmation

#### Option 2: Voir la liste d'appel
1. Depuis le tableau de bord, cliquer sur **📋 Liste d'appel** dans un local
2. La liste montre tous les étudiants du local avec leur statut:
   - ✅ Présent (en vert)
   - ❌ Absent (en rouge)
   - ⏳ En attente (en gris)
3. Vous pouvez **imprimer la liste** en cliquant sur **🖨️ Imprimer**

---

## 🔒 Sécurité et Permissions

### Ce qu'un surveillant NE PEUT PAS faire:
- ❌ Accéder aux locaux non assignés
- ❌ Modifier des données (promotions, étudiants, etc.)
- ❌ Créer ou supprimer des utilisateurs
- ❌ Voir le tableau de bord administrateur
- ❌ Voir les other locaux/examens

### Ce qu'un surveillant PEUT faire:
- ✅ Se connecter avec son compte personnel
- ✅ Consulter ses locaux assignés
- ✅ Pointer la présence des étudiants
- ✅ Imprimer les listes d'appel
- ✅ Se déconnecter

### Protection:
- Chaque route de surveillant vérifie que l'utilisateur est assigné au local
- Les données sont filtrées par utilisateur connecté
- Les sessions sont validées à chaque requête

---

## 📱 Flux d'une Session de Pointage

```
1. Surveillant se connecte
         ↓
2. Voit son tableau de bord avec ses locaux
         ↓
3. Clique sur "Scanner QR" pour un local
         ↓
4. Scanner les codes QR des badges des étudiants
         ↓
5. Système enregistre les présences en temps réel
         ↓
6. Peut consulter la liste d'appel et imprimer
         ↓
7. Se déconnecte
```

---

## ✅ Exemple Concret

### Scénario: Examen de Mathématiques
- **Examen:** Mathématiques - Session Matin
- **Locaux:** Local A, Local B, Local C
- **Surveillants:**
  - Alice → assignée au Local A
  - Bob → assigné au Local B
  - Charlie → assigné au Local C

### Au moment de l'examen:
1. **Alice se connecte** → voit uniquement le Local A
2. Elle scan les codes QR des étudiants du Local A
3. **Bob se connecte** → voit uniquement le Local B
4. Il imprime la liste d'appel du Local B
5. **Charlie se connecte** → voit uniquement le Local C
6. Il scan les codes et consulte les statuts en temps réel

L'administrateur peut voir tout et suivre le pointage de tous les locaux en temps réel.

---

## 🆘 Dépannage

### Le surveillant ne peut pas se connecter
- ✅ Vérifier que l'utilisateur a bien été créé par l'admin
- ✅ Vérifier l'orthographe du nom d'utilisateur et du mot de passe
- ✅ S'assurer que le compte n'a pas été supprimé

### Le surveillant n'a pas de local affecté
- ✅ L'administrateur doit affecter le surveillant à au moins un local
- ✅ Vérifier que l'examen est sélectionné avant d'assigner

### Le scan QR ne fonctionne pas
- ✅ Vérifier que le code QR appartient à un étudiant du bon local/examen
- ✅ S'assurer que le lecteur/caméra est bien configuré
- ✅ Essayer de rafraîchir la page

### Problème de permissions
- ✅ L'erreur "Vous n'êtes pas autorisé" signifie que le surveillant tente d'accéder à un local non assigné
- ✅ Vérifier l'affectation dans l'administration

---

## 🔐 Gestion des Permissions (Base de Données)

La table `supervisor_assignments` contrôle les affectations:
```sql
supervisor_assignments (
  user_id,      -- ID du surveillant
  exam_id,      -- ID de l'examen
  room_id,      -- ID du local
  assigned_at   -- Date d'affectation
)
```

Une ligne = Un surveillant assigné à un local pour un examen spécifique

---

## 📊 Données de Présence

Quand un surveillant enregistre une présence:
- La table `presence` est mise à jour avec:
  - `status`: 'present' ou 'absent'
  - `scanned_at`: date/heure du scan
  - `scanned_by`: ID du surveillant qui a scanné
  
Cela permet de tracer qui a enregistré chaque présence.

---

**Version:** 1.0  
**Mise à jour:** Janvier 2026  
**Support:** Contactez l'administrateur du système
