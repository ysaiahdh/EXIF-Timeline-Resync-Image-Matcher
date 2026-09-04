# 📸 EXIF Timeline Resync & Image Matcher

[![CI](https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher/actions/workflows/ci.yml/badge.svg)](https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher/actions)
[![Python](https://img.shields.io/badge/python-%3E%3D3.9-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> *Vos photos téléchargées ont perdu la notion du temps. Cet outil la leur
> rend — des horodatages EXIF réalistes, reconstruits depuis les noms de
> dossiers, un planning, les noms de fichiers et la comparaison visuelle.*

**[English](README.md) | [Français](README.fr.md)**

---

## 📖 Sommaire

- [L'histoire](#-lhistoire)
- [Ce qu'il sait faire](#-ce-quil-sait-faire)
- [Ce qu'il faut](#-ce-quil-faut)
- [Installation](#-installation)
- [Mode guidé](#-mode-guidé--commencez-ici)
- [Démarrage rapide](#-démarrage-rapide)
- [Référence de configuration](#-référence-de-configuration)
- [Comment les heures sont choisies](#-comment-les-heures-sont-choisies)
- [Comparer avec vos propres photos](#-comparer-avec-vos-propres-photos)
- [Horloges d'appareil déréglées](#-horloges-dappareil-déréglées)
- [Rapports et filets de sécurité](#-rapports-et-filets-de-sécurité)
- [Toutes les options](#-toutes-les-options)
- [En cas de problème](#-en-cas-de-problème)
- [Contribuer](#-contribuer)
- [Licence](#-licence)

---

## 📷 L'histoire

J'ai écrit cet outil après avoir téléchargé quelques centaines de photos
depuis le site d'une colonie de vacances. Toutes étaient datées du jour du
téléchargement. Les articles du blog racontaient le séjour jour par jour, les
noms de dossiers contenaient les dates, mais les fichiers eux-mêmes avaient
perdu toute notion du temps. Les trier à la main n'était pas envisageable.

Ce script remet la chronologie en place. Il lit les dates cachées dans vos
noms de dossiers, les combine avec un petit fichier de planning et les
descriptions texte sauvegardées à côté des photos, puis réécrit des
horodatages EXIF corrects dans chaque image. Et si vous avez aussi pris vos
propres photos pendant ces événements (avec un téléphone ou un appareil dont
l'heure est juste), il peut même comparer les images visuellement et copier
la seconde exacte de la prise de vue.

Comme il modifie des métadonnées, tout ce qu'il fait est consigné dans un
rapport CSV que vous pouvez rejouer avec `--undo` si le résultat ne vous
plaît pas.

```
  Avant                                     Après
  ──────────────────────────────────────────────────────────────────
  IMG_001.jpg  📥 juin 2026 (téléchargement)  →  📅 01/05/2026 09:00:00 · plan
  IMG_002.jpg  📥 juin 2026 (téléchargement)  →  📅 01/05/2026 09:03:30 · plan
  IMG_003.jpg  📥 juin 2026 (téléchargement)  →  📅 01/05/2026 14:30:22 · nom 🤖
```

## ✨ Ce qu'il sait faire

- 🕰️ **Reconstruire des heures de prise de vue réalistes** depuis des noms de
  dossiers comme `01-05 Kayak` et un planning JSON
- ↔️ **Espacer les photos de quelques minutes** pour éviter les horodatages
  identiques
- 📝 **Stocker les descriptions `.txt` du blog** dans `ImageDescription`
  (seulement quand un `.txt` existe ; les descriptions existantes sont sinon
  conservées)
- 🌙 **Repérer les mots-clés** (`boom`, `soiree`, `animaux`...) et décaler les
  albums du soir ou de l'après-midi en conséquence — insensible aux accents,
  mots entiers uniquement : `soirée` correspond, mais pas `bison`
- 🔢 **Utiliser les horodatages trouvés dans les noms de fichiers** quand ils
  existent (`IMG_20260501_143022.jpg`)
- 🧠 **Comparer vos téléchargements à vos propres photos de référence
  datées**, avec un réseau ResNet-18 ou un simple hachage perceptuel
- ⏱️ **Détecter une horloge d'appareil déréglée** — et corriger tout l'album
  avec une seule option
- 🔍 **Tout prévisualiser** avec `--dry-run`, générer une chronologie HTML,
  repérer les doublons dans et entre les albums, et annuler n'importe quelle
  exécution

## 📦 Ce qu'il faut

- Python 3.9 ou plus récent
- [ExifTool](https://exiftool.org/)
  - Linux : `sudo apt install libimage-exiftool-perl`
  - macOS : `brew install exiftool`
  - Windows : téléchargez-le sur le site et déposez `exiftool.exe` dans ce dossier
- Pour la reconnaissance d'images seulement :
  - 💪 la voie royale : `pip install torch torchvision Pillow numpy`
  - 🪶 la version légère : `pip install Pillow ImageHash`

  > [!TIP]
  > Sur une machine sans GPU, `--index-url
  > https://download.pytorch.org/whl/cpu` économise beaucoup de disque pour
  > torch.

Vérifiez qu'ExifTool répond avec `exiftool -ver`.

## 🚀 Installation

Clonez et lancez, c'est tout :

```bash
git clone https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher.git
cd EXIF-Timeline-Resync-Image-Matcher
python -m exif_resync --help
```

Une installation classique fonctionne aussi :

```bash
pip install .
exif-resync --help
```

## 🧙 Mode guidé — commencez ici

Pas envie de retenir les options ? Lancez l'assistant interactif :

```bash
python -m exif_resync --wizard
```

Il vous guide en cinq étapes :

1. 📁 dossier photo
2. 🗓️ planning (en créant le fichier de configuration *avec vous* s'il manque)
3. 🖼️ photos de référence optionnelles
4. 👀 aperçu sans écriture avec barre de progression
5. ✅ confirmation finale avant toute modification

Le même menu propose aussi la vérification de configuration et la
restauration `--undo` avec aperçu préalable.

> [!NOTE]
> Sans aucune dépendance — il faut un terminal interactif ; couleurs et barre
> de progression se désactivent automatiquement quand la sortie est redirigée,
> et `Ctrl-C` annule proprement à tout moment.

## ⚡ Démarrage rapide

Rangez vos téléchargements dans des dossiers dont le nom contient une date
jour-mois :

```
photos/
├── 01-05 Parc aquatique/
│   ├── IMG_001.jpg
│   ├── IMG_002.jpg
│   └── resume.txt        <- optionnel, devient ImageDescription
├── 02-05 Safari 14h30/
└── 03-05 Soiree boom/
```

Copiez la config d'exemple et adaptez-la :

```bash
cp config.example.json config_planning.json
```

```json
{
  "year": 2026,
  "default_interval_sec": 180,
  "schedule": {
    "01-05": "09:00",
    "02-05": "08:30"
  }
}
```

Les clés françaises fonctionnent aussi (`annee`, `intervalle_defaut_sec`,
`planning`) ; évitez simplement de mélanger les deux langues dans un même
fichier (vous aurez un avertissement). Les clés et valeurs du planning sont
souples : `1-5` vaut `01-05`, et `9:30` vaut `09:30`. Tout le reste (date
invalide, `25:00`, intervalle nul) est rejeté d'emblée avec un message clair
plutôt qu'un plantage en pleine exécution.

Puis simulez, appliquez, et contrôlez :

```bash
python -m exif_resync -d ./photos --check-config   # vérification, ne touche à rien
python -m exif_resync -d ./photos --dry-run --timeline
python -m exif_resync -d ./photos
open photos/timeline.html      # voir ce qui a été écrit
```

Vous avez changé d'avis ?

```bash
python -m exif_resync --undo ./photos/exif_resync_report.csv
```

L'annulation restaure exactement les valeurs précédentes, y compris la
suppression des balises qui n'existaient pas avant.

## ⚙️ Référence de configuration

| Clé (EN / FR) | Défaut | Règles |
|---|---|---|
| `year` / `annee` | année en cours (avec un avertissement) | année à quatre chiffres, p. ex. `2026` |
| `default_interval_sec` / `intervalle_defaut_sec` | `180` | nombre de secondes positif entre les photos quand rien d'autre ne s'applique |
| `schedule` / `planning` | `{}` | objet associant `"JJ-MM"` à `"HH:MM"` (24h), p. ex. `"01-05": "09:00"` |

## 🕰️ Comment les heures sont choisies

Pour chaque album, l'heure de départ vient de la première règle qui s'applique :

| # | Règle | Début | Pas |
|---|---|---|---|
| 1 | 🌙 Mot-clé du soir (`boom`, `soiree`/`soirée`, `veillee`/`veillée`) dans le nom du dossier ou le texte de l'album | 20h30 | 90 s |
| 2 | ☀️ Mot-clé de l'après-midi (`bis`, `animaux`, `apres-midi`/`après-midi`) dans le nom du dossier | 15h00 | 120 s |
| 3 | 🗓️ Une entrée pour ce jour dans votre planning | l'heure configurée | 210 s |
| 4 | 🕑 Une heure dans le nom du dossier (`02-05 Safari 14h30`) | cette heure | `default_interval_sec` |
| 5 | 💤 Rien ne correspond | 12h00 | `default_interval_sec` |

Les mots-clés ne correspondent qu'à des mots entiers : un dossier `Bisons`
ne sera pas confondu avec un album de l'après-midi.

Chaque photo reçoit ensuite `début + n × intervalle`, donc les horodatages
restent strictement croissants au sein d'un album. Un horodatage trouvé dans
un nom de fichier passe toujours devant tout le reste : l'appareil photo
savait mieux que nous.

## 🧠 Comparer avec vos propres photos

Si vous étiez là aussi et que vos clichés ont encore des dates EXIF correctes,
montrez-les à l'outil :

```bash
python -m exif_resync -d ./photos -r ./mes_photos_reference
```

Chaque téléchargement est comparé à toutes les photos de référence. Quand la
meilleure correspondance est assez convaincante *et* tombe à un jour près de
la date de l'album, son horodatage exact est recopié. Deux moteurs sont disponibles :

| Mode | Prérequis | Remarques |
|---|---|---|
| `--matcher resnet` | torch, torchvision, Pillow | meilleure qualité, ~45 Mo de poids téléchargés au premier usage |
| `--matcher hash` | Pillow, ImageHash | minuscule et rapide, repère les cas évidents |
| `--matcher auto` | l'un des deux | choisit resnet si installé, sinon hash |
| `--matcher off` | rien | mode planning seul |

En bonus, les comparaisons confrontent aussi les photos entre elles et écrivent
`duplicates_report.csv` quand deux photos se ressemblent comme deux gouttes
d'eau — dans un même album comme entre dossiers différents. Pas de photos de
référence sous la main ? `--duplicates` lance juste cette comparaison :

```bash
python -m exif_resync -d ./photos --duplicates --matcher hash
```

Les photos de référence ne sont analysées qu'une fois et mises en cache à
côté de leur dossier, les exécutions suivantes démarrent instantanément.

## ⏱️ Horloges d'appareil déréglées

Il arrive que les photos qui ont *conservé* leur EXIF contredisent le
planning, parce que l'horloge de l'appareil était fausse. L'outil le remarque :
si au moins deux photos d'un album portent des horodatages décalés de manière
cohérente par rapport aux heures calculées, il vous dit de combien. La dérive
est aussi signalée pendant les prévisualisations `--dry-run` dès qu'ExifTool
est installé. Rien ne bouge sans votre accord :

```bash
python -m exif_resync -d ./photos --sync-clocks
```

Cette option décale tout l'album de l'écart médian détecté. Les horodatages
issus des noms de fichiers ne sont pas touchés.

## 🛡️ Rapports et filets de sécurité

Chaque exécution réelle enregistre `exif_resync_report.csv` : anciennes
valeurs, nouvelles valeurs, provenance de chaque horodatage (`filename`,
correspondance IA, correspondance par hachage, ou plan), et correction de
dérive éventuelle. Gardez ces fichiers : `--undo` en a besoin. `--undo`
accepte aussi `--dry-run` (aperçu de ce qui serait restauré) et `--quiet`
(résumé seul).

> [!NOTE]
> `--dry-run` ne touche à aucun fichier et marche même avant l'installation
> d'ExifTool.

`--timeline` produit une petite page HTML autonome montrant chaque album,
chaque photo, son heure attribuée et d'où elle vient. Combiné avec `--report`,
la chronologie est écrite à côté du rapport plutôt que dans le dossier photo.

> [!WARNING]
> `--sync-mtime` met aussi à jour la date de modification du fichier — et
> contrairement aux balises EXIF, celle-ci n'est **pas** annulée par `--undo`.

## 🧰 Toutes les options

<details>
<summary><b>Cliquez pour déplier la liste complète des options</b></summary>

```
python -m exif_resync [-d DOSSIER] [-c CONFIG] [-r REFERENCE]
                      [--matcher {auto,resnet,hash,off}] [--match-threshold FLOTTANT]
                      [--duplicates] [--duplicate-threshold FLOTTANT] [--recursive]
                      [--sync-clocks] [--sync-mtime]
                      [--dry-run] [--timeline] [--year ANNEE] [--report CHEMIN]
                      [--undo RAPPORT_CSV] [--check-config] [--verbose] [--quiet]
                      [--wizard]
```

| Option | Effet |
|---|---|
| `-d, --directory` | dossier contenant les albums datés (défaut : ici) |
| `-c, --config` | chemin du JSON de configuration (défaut : `config_planning.json`) |
| `-r, --reference` | vos photos correctement datées, active la comparaison |
| `--matcher` | `auto`, `resnet`, `hash` ou `off` |
| `--match-threshold` | remplace le seuil de similarité accepté, entre (0, 1] |
| `--duplicates` | détecte les quasi-doublons même sans `--reference` |
| `--duplicate-threshold` | similarité à partir de laquelle deux photos sont doublons (défaut : 0,95) |
| `--recursive` | explore les dossiers d'albums et de référence récursivement |
| `--sync-clocks` | applique la dérive d'horloge détectée à tout l'album |
| `--sync-mtime` | met aussi à jour la date de modification fichier (non annulée par `--undo`) |
| `--dry-run` | prévisualise sans rien écrire (marche aussi avec `--undo`) |
| `--timeline` | génère `timeline.html` |
| `--year` | remplace l'année de toutes les dates reconstruites |
| `--report` | chemin personnalisé pour le rapport CSV |
| `--undo` | restaure les originaux depuis un rapport précédent |
| `--check-config` | valide la config, affiche le planning résolu, puis quitte |
| `--verbose` | détails photo par photo |
| `--quiet` | avertissements, erreurs et résumé seulement |
| `--wizard` | mode interactif guidé (questions + aperçu + confirmation) |

</details>

## 🆘 En cas de problème

**« ExifTool not found »** → installez-le (voir plus haut). Sous Windows, le
plus simple reste de poser `exiftool.exe` à côté du script.

**Tout a été ignoré** → vos noms de dossiers doivent contenir un motif
`JJ-MM` (ou `J-M`) reconnaissable. Un dossier comme `divers` est ignoré,
c'est voulu.

**Mauvaise année partout** → définissez `year` (ou `annee`) dans la config,
ou passez `--year`.

**Une correspondance visuelle attendue n'a pas eu lieu** → il faut satisfaire
deux conditions, une forte similarité *et* une date à un jour près de celle de
l'album. Vérifiez d'abord la date EXIF de la photo de référence elle-même ;
si elle est fausse, la garde fait bien son travail en refusant. Vous pouvez
assouplir le seuil de similarité avec `--match-threshold`.

**Seuls .jpg/.jpeg/.png sont traités.** Les RAW restent intacts, et les
vidéos sont hors périmètre pour l'instant.

> [!NOTE]
> Les dates des PNG sont stockées en XMP plutôt qu'en vrais enregistrements
> EXIF.

## 🤝 Contribuer

Les retours de bugs et les pull requests sont bienvenus ! Lisez d'abord
[CONTRIBUTING.md](CONTRIBUTING.md) — installation dev, modèles d'issues et de
PR, et règles pour garder les deux README synchronisés.

Vérification rapide avant de pousser :

```bash
python -m pytest tests/
ruff check exif_resync tests/
ruff format --check exif_resync tests/
```

> [!NOTE]
> Une faille de sécurité ? N'ouvrez **pas** d'issue publique — voir
> [SECURITY.md](SECURITY.md) pour un signalement privé.

## 📄 Licence

MIT, voir [LICENSE](LICENSE).
