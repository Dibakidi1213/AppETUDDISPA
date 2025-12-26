# Application de Dispatch des Étudiants

Application Python Flask pour gérer le dispatch des étudiants dans les locaux d'examens.

## Installation

1. Installer les dépendances :
```bash
pip install -r requirements.txt
```

## Lancement

Pour démarrer l'application :

```bash
python app.py
```

L'application sera accessible sur : http://127.0.0.1:5000

## Utilisation

### 1. Créer une promotion
- Aller dans "Promotions" dans le menu
- Créer une nouvelle promotion avec un nom

### 2. Importer les étudiants
- Aller dans "Import étudiants"
- Sélectionner la promotion pour laquelle importer
- Importer un fichier Excel avec les colonnes :
  - **nom** (obligatoire)
  - **sexe** (optionnel)

### 3. Configurer les locaux
- Aller dans "Locaux"
- Ajouter les locaux avec le nombre de bancs et d'étudiants par banc

### 4. Créer un examen
- Aller dans "Examens"
- Ajouter un examen avec date et horaires

### 5. Affecter les étudiants
- Aller dans "Affectation"
- Sélectionner l'examen
- Cliquer sur "Lancer l'affectation"

### 6. Imprimer les badges et listes
- Aller dans "Locaux"
- Pour chaque local, cliquer sur "Badges" ou "Liste" pour imprimer

## API Mobile

Pour valider la présence d'un étudiant via QR code :
```
GET /api/validate/<token>
```

Retourne un JSON avec les informations de l'étudiant et marque sa présence.

