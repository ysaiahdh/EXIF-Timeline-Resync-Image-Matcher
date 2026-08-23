# EXIF Timeline Resync & Image Matcher

[English](README.md) | [Français](README.fr.md)

Restaurez les horodatages EXIF et la chronologie de photos téléchargées
depuis des blogs, des sites d'écoles ou de colonies de vacances.

Lorsque vous téléchargez des photos depuis des plateformes web (WordPress,
Blogger, Wix, etc.), les données EXIF d'origine (`DateTimeOriginal`) sont
généralement supprimées. Toutes les photos se retrouvent datées du jour du
téléchargement et perdent leur ordre chronologique.

**EXIF Timeline Resync** résout ce problème en combinant **la structure des
dossiers**, un **planning JSON**, **les descriptions textuelles du blog** et
**la reconnaissance d'images par IA** à partir de vos propres photos de
référence.

Chaque exécution produit également un **rapport de modifications (CSV)** qui
permet de vérifier — ou d'**annuler** complètement — tout ce que l'outil a
écrit.

---

## Fonctionnalités

- **Reconstruction intelligente des heures** : calcule des heures de prise
  de vue réalistes à partir des noms de dossiers datés et de votre planning.
- **Incrémentation séquentielle** : évite les horodatages en double en
  appliquant un pas de temps dynamique entre chaque photo d'un album.
- **Intégration des résumés texte** : lit les fichiers `.txt` de description
  du blog et écrit leur contenu dans le tag EXIF `ImageDescription`.
- **Analyse contextuelle** : ajuste les créneaux horaires selon des mots-clés
  (boom, soiree, apres-midi, safari, etc.).
- **Reconnaissance et alignement d'images (optionnel)** : compare les photos
  téléchargées avec vos propres photos de smartphone (déjà datées) via un
  réseau de neurones ResNet-18 pour aligner les horodatages à la seconde.
- **Garde de cohérence** : valide les correspondances visuelles en vérifiant
  la similarité cosinus (seuil de 85 %) **et** la correspondance du jour dans
  le planning.
- **Mode simulation (--dry-run)** : prévisualise tous les horodatages calculés
  sans modifier le moindre fichier.
- **Rapport de modifications & annulation** : chaque exécution réelle enregistre
  un rapport CSV ancien → nouveau ; restaurez les valeurs d'origine à tout moment
  avec `--undo`.

---

## Prérequis

1. **Python 3.8+**
2. **ExifTool** : requis pour lire et écrire les tags EXIF.
   - **Windows** : téléchargez la distribution complète depuis
     [exiftool.org](https://exiftool.org/) et placez `exiftool.exe` à la
     racine du projet (ou ajoutez-le à votre `PATH`).
   - **Linux** : `sudo apt install libimage-exiftool-perl`
     (ou `sudo dnf install perl-Image-ExifTool`)
   - **macOS** : `brew install exiftool`
3. *(Optionnel)* **PyTorch + torchvision** : nécessaire uniquement pour la
   reconnaissance d'images avec `-r/--reference`. Voir `requirements.txt`.

Vérifiez qu'ExifTool fonctionne :

```bash
exiftool -ver
```

---

## Installation

```bash
git clone https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher.git
cd EXIF-Timeline-Resync-Image-Matcher

# Optionnel : activer la reconnaissance d'images par IA
pip install -r requirements.txt
```

> Sous Windows, pensez à placer `exiftool.exe` dans ce dossier s'il n'est pas
> dans votre `PATH`.

---

## Démarrage rapide

### 1. Préparez vos photos

Rangez les téléchargements dans des dossiers dont le nom contient une date
jour-mois (`JJ-MM`, éventuellement suivie d'une heure comme `14h30`) :

```
photos/
├── 01-05 Parc aquatique/
│   ├── IMG_001.jpg
│   ├── IMG_002.jpg
│   └── resume.txt          <- optionnel : stocké dans ImageDescription
├── 02-05 Safari 14h30/
│   └── ...
└── 03-05 Soiree boom/
    └── ...
```

### 2. Créez votre planning

```bash
cp config.example.json config_planning.json
```

```json
{
  "year": 2026,
  "default_interval_sec": 180,
  "schedule": {
    "01-05": "09:00",
    "02-05": "08:30",
    "03-05": "10:00"
  }
}
```

| Clé anglaise | Clé française | Description |
|---|---|---|
| `year` | `annee` | Année appliquée à toutes les dates reconstruites. |
| `default_interval_sec` | `intervalle_defaut_sec` | Secondes ajoutées entre deux photos consécutives quand aucune règle spéciale ne s'applique (défaut : `180`). |
| `schedule` | `planning` | Associe chaque date `"JJ-MM"` de dossier à l'heure de début de l'activité `"HH:MM"`. |

Les clés anglaises et françaises sont acceptées, mais ne les mélangez pas
au sein d'un même fichier.

### 3. Simulez, puis appliquez

Commencez toujours par une simulation :

```bash
python exif_resync.py -d ./photos --dry-run
```

Puis écrivez réellement les métadonnées :

```bash
python exif_resync.py -d ./photos
```

Si quelque chose cloche, annulez :

```bash
python exif_resync.py --undo ./photos/exif_resync_report.csv
```

---

## Comment les heures sont choisies

Pour chaque dossier album, une heure de début est choisie selon cet ordre de
priorité :

1. **Mots-clés de soirée** (`boom` dans le nom du dossier, `soiree`/`veillee`
   dans la description texte) → départ à **20h30**, 90 s entre photos.
2. **Mots-clés d'après-midi** (`bis`, `animaux`, `apres-midi` dans le nom du
   dossier) → départ à **15h00**, 120 s entre photos.
3. **Votre planning** (entrée `schedule` pour ce jour) → départ à l'heure
   configurée, 210 s entre photos.
4. **Heure dans le nom du dossier** (`02-05 Safari 14h30` → 14h30).
5. **Par défaut** → 12h00, avec `default_interval_sec` entre photos.

Chaque photo suivante reçoit `heure_début + n × intervalle`, garantissant des
horodatages strictement croissants et sans doublon au sein d'un album.

---

## Reconnaissance d'images par IA (optionnel)

Avez-vous *vos propres* photos des mêmes événements, prises au téléphone ou
à l'appareil photo (donc encore correctement datées) ? Placez-les dans un
dossier et lancez :

```bash
python exif_resync.py -d ./photos -r ./mes_photos_reference
```

Fonctionnement :

1. Chaque photo de référence est transformée en vecteur de caractéristiques
   par un réseau ResNet-18 (pré-entraîné sur ImageNet).
2. Chaque photo téléchargée est transformée de la même manière.
3. Le vecteur de référence le plus proche est trouvé par similarité cosinus.
4. Une correspondance n'est acceptée que si la similarité ≥ **0,85** *et* si
   la référence a été prise à ±1 jour de la date de l'album — cette double
   vérification évite les faux positifs.
5. Les correspondances acceptées copient l'horodatage exact de la référence
   (à la seconde près).

Remarques :

- La première exécution télécharge les poids ResNet-18 (~45 Mo), un accès
  internet est donc nécessaire une fois.
- Sans PyTorch installé, l'outil bascule automatiquement en mode planning seul.
- La reconnaissance tourne par défaut sur CPU, à raison d'environ 5 à
  10 images/seconde.

---

## Rapport de modifications & annulation

Chaque exécution réelle (hors --dry-run) écrit
`<dossier>/exif_resync_report.csv` contenant, par photo :

| Colonne | Signification |
|---|---|
| `path` | Chemin absolu de la photo. |
| `old_datetimeoriginal` / `old_createdate` / `old_modifydate` | Dates EXIF précédentes (`-` = aucune). |
| `old_imagedescription` | Description précédente (`-` = aucune). |
| `new_datetime` | Horodatage écrit par l'outil. |
| `new_imagedescription` | Description écrite par l'outil. |
| `match_score` | Similarité cosinus de la correspondance visuelle (vide si non utilisée). |

Pour restaurer l'état précédent :

```bash
python exif_resync.py --undo ./photos/exif_resync_report.csv
```

Conservez un rapport par session — chaque nouvelle exécution écrase le nom de
rapport par défaut, ou utilisez `--report ma_session.csv` pour le contrôler
vous-même.

---

## Référence des commandes

```
python exif_resync.py [-d DOSSIER] [-c CONFIG] [-r REFERENCE]
                      [--dry-run] [--year ANNEE] [--report CHEMIN]
                      [--undo RAPPORT_CSV]
```

| Option | Description |
|---|---|
| `-d, --directory` | Dossier contenant les sous-dossiers datés (défaut : courant). |
| `-c, --config` | Chemin du fichier JSON de configuration (défaut : `config_planning.json`). |
| `-r, --reference` | Dossier de vos photos de référence correctement datées (active la reconnaissance IA). |
| `--dry-run` | Prévisualise tous les changements calculés sans rien écrire. |
| `--year ANNEE` | Remplace l'année utilisée pour toutes les dates reconstruites. |
| `--report CHEMIN` | Chemin personnalisé pour le rapport CSV de modifications. |
| `--undo RAPPORT_CSV` | Restaure les valeurs EXIF d'origine enregistrées dans le rapport indiqué. |

---

## Dépannage

**`ERROR: ExifTool not found`**
Installez ExifTool (voir Prérequis). Sous Windows, vous pouvez déposer
`exiftool.exe` à côté de `exif_resync.py`.

**Toutes mes photos ont été ignorées**
Les noms de dossiers doivent contenir un motif `JJ-MM` (ou `J-M`)
reconnaissable, ex. `05-06 Kayak`. Les dossiers sans date sont ignorés.

**Tous les horodatages utilisent la mauvaise année**
Définissez `"year"` (ou `"annee"`) dans votre config, ou passez `--year 2025`.

**Le fichier de config semble ignoré**
Assurez-vous qu'il s'agit de JSON valide utilisant soit les clés anglaises
(`year`, `default_interval_sec`, `schedule`), soit les clés françaises
(`annee`, `intervalle_defaut_sec`, `planning`). L'outil affiche un avertissement
lorsque le fichier de config est introuvable ou illisible.

**Une correspondance visuelle a été rejetée bien que les photos soient
identiques**
La correspondance doit passer les deux vérifications : similarité cosinus
≥ 0,85 et date de prise de vue à ±1 jour de la date reconstruite de l'album.
Vérifiez que le `DateTimeOriginal` propre à la photo de référence est correct.

---

## FAQ

**Est-ce sûr ? Cela va-t-il détruire mes fichiers ?**
L'outil ne réécrit que des champs de métadonnées EXIF (`AllDates`,
`ImageDescription`) et ne touche jamais aux pixels de l'image. Utilisez
`--dry-run` d'abord, et conservez le rapport CSV — `--undo` restaure
exactement les valeurs précédentes, y compris « aucune valeur ».

**Renomme-t-il ou déplace-t-il des fichiers ?**
Non. Les noms et emplacements de fichiers ne changent jamais.

**Quels formats sont pris en charge ?**
`.jpg`, `.jpeg` et `.png`. Les formats RAW (CR2, NEF, ARW…) ne sont pas
analysés mais restent intacts.

**Puis-je l'utiliser sur des vidéos ?**
Non. Seuls `.jpg` / `.jpeg` / `.png` sont traités.

## Licence

[MIT](LICENSE)
