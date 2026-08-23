# EXIF Timeline Resync & Image Matcher

[English](README.md) | [Français](README.fr.md)

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

## Ce qu'il sait faire

- Reconstruire des heures de prise de vue réalistes depuis des noms de
  dossiers comme `01-05 Kayak` et un planning JSON
- Espacer les photos de quelques minutes pour éviter les horodatages identiques
- Stocker les descriptions `.txt` du blog dans `ImageDescription`
- Repérer les mots-clés (`boom`, `soiree`, `animaux`...) et décaler les albums
  du soir ou de l'après-midi en conséquence
- Utiliser les horodatages trouvés dans les noms de fichiers quand ils
  existent (`IMG_20260501_143022.jpg`)
- Comparer vos téléchargements à vos propres photos de référence datées, avec
  un réseau ResNet-18 ou avec un simple hachage perceptuel
- Détecter une horloge d'appareil déréglée (et corriger tout l'album avec une
  seule option)
- Tout prévisualiser avec `--dry-run`, générer une chronologie HTML, repérer
  les doublons entre albums, et annuler n'importe quelle exécution

## Ce qu'il faut

- Python 3.8 ou plus récent
- [ExifTool](https://exiftool.org/). Sous Linux : `sudo apt install
  libimage-exiftool-perl`. Sous macOS : `brew install exiftool`. Sous Windows,
  téléchargez-le sur le site et déposez `exiftool.exe` dans ce dossier.
- Pour la reconnaissance d'images seulement :
  - la voie royale : `pip install torch torchvision Pillow numpy`
    (sur une machine sans GPU,
    `--index-url https://download.pytorch.org/whl/cpu` économise beaucoup de
    disque pour torch)
  - ou la version légère : `pip install Pillow ImageHash`

Vérifiez qu'ExifTool répond avec `exiftool -ver`.

## Installation

Clonez et lancez, c'est tout :

```bash
git clone https://github.com/YsaiahDH/EXIF-Timeline-Resync-Image-Matcher.git
cd EXIF-Timeline-Resync-Image-Matcher
python exif_resync.py --help
```

Une installation classique fonctionne aussi :

```bash
pip install .
exif-resync --help
```

## Démarrage rapide

Rangez vos téléchargements dans des dossiers dont le nom commence par une date
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
fichier. Vous pouvez vérifier votre configuration sans rien toucher :

```bash
python exif_resync.py -d ./photos --check-config
```

Puis simulez, appliquez, et contrôlez :

```bash
python exif_resync.py -d ./photos --dry-run --timeline
python exif_resync.py -d ./photos
open photos/timeline.html      # voir ce qui a été écrit
```

Vous avez changé d'avis ?

```bash
python exif_resync.py --undo ./photos/exif_resync_report.csv
```

L'annulation restaure exactement les valeurs précédentes, y compris la
suppression des tags qui n'existaient pas avant.

## Comment les heures sont choisies

Pour chaque album, l'heure de départ vient de la première règle qui s'applique :

1. `boom` dans le nom du dossier, ou `soiree` / `veillee` dans le texte →
   20h30, 90 secondes entre photos
2. `bis`, `animaux` ou `apres-midi` dans le nom du dossier → 15h00,
   120 secondes
3. une entrée pour ce jour dans votre planning → l'heure configurée,
   210 secondes
4. une heure écrite dans le nom du dossier (`02-05 Safari 14h30`) → cette heure
5. sinon 12h00, espacées de `default_interval_sec`

Chaque photo reçoit ensuite `début + n × intervalle`, donc les horodatages
restent strictement croissants au sein d'un album. Un horodatage trouvé dans
un nom de fichier passe toujours devant tout le reste : l'appareil photo
savait mieux que nous.

## Comparer avec vos propres photos

Si vous étiez là aussi et que vos clichés ont encore des dates EXIF correctes,
montrez-les à l'outil :

```bash
python exif_resync.py -d ./photos -r ./mes_photos_reference
```

Chaque téléchargement est comparé à toutes les photos de référence. Quand la
meilleure correspondance est assez convaincante *et* tombe le même jour que
l'album, son horodatage exact est recopié. Deux moteurs sont disponibles :

| Mode | Prérequis | Remarques |
|---|---|---|
| `--matcher resnet` | torch, torchvision, Pillow | meilleure qualité, ~45 Mo de poids téléchargés au premier usage |
| `--matcher hash` | Pillow, ImageHash | minuscule et rapide, repère les cas évidents |
| `--matcher auto` | l'un des deux | choisit resnet si installé, sinon hash |
| `--matcher off` | rien | mode planning seul |

En bonus, les comparaisons confrontent aussi les albums entre eux et écrivent
`duplicates_report.csv` quand deux photos de dossiers différents sont
identiques.

Les photos de référence ne sont analysées qu'une fois et mises en cache à
côté de leur dossier, les exécutions suivantes démarrent instantanément.

## Horloges d'appareil déréglées

Il arrive que les photos qui ont *conservé* leur EXIF contredisent le
planning, parce que l'horloge de l'appareil était fausse. L'outil le remarque :
si au moins deux photos d'un album portent des horodatages décalés de manière
cohérente par rapport aux heures calculées, il vous dit de combien. Rien ne
bouge sans votre accord :

```bash
python exif_resync.py -d ./photos --sync-clocks
```

Cette option décale tout l'album de l'écart médian détecté. Les horodatages
issus des noms de fichiers ne sont pas touchés.

## Rapports et filets de sécurité

Chaque exécution réelle enregistre `exif_resync_report.csv` : anciennes
valeurs, nouvelles valeurs, provenance de chaque horodatage (`filename`,
match IA, match hash ou plan), et correction de dérive éventuelle. Gardez ces
fichiers : `--undo` en a besoin.

`--dry-run` ne touche à aucun fichier et marche même avant l'installation
d'ExifTool. `--timeline` produit une petite page HTML autonome montrant chaque
album, chaque photo, son heure attribuée et d'où elle vient.

## Toutes les options

```
python exif_resync.py [-d DOSSIER] [-c CONFIG] [-r REFERENCE]
                      [--matcher {auto,resnet,hash,off}] [--sync-clocks]
                      [--dry-run] [--timeline] [--year ANNEE] [--report CHEMIN]
                      [--undo RAPPORT_CSV] [--check-config] [--verbose]
```

| Option | Effet |
|---|---|
| `-d, --directory` | dossier contenant les albums datés (défaut : ici) |
| `-c, --config` | chemin du JSON de configuration (défaut : `config_planning.json`) |
| `-r, --reference` | vos photos correctement datées, active la comparaison |
| `--matcher` | `auto`, `resnet`, `hash` ou `off` |
| `--sync-clocks` | applique la dérive d'horloge détectée à tout l'album |
| `--dry-run` | prévisualise sans rien écrire |
| `--timeline` | génère `timeline.html` |
| `--year` | remplace l'année de toutes les dates reconstruites |
| `--report` | chemin personnalisé pour le rapport CSV |
| `--undo` | restaure les originaux depuis un rapport précédent |
| `--check-config` | valide la config, affiche le planning résolu, puis quitte |
| `--verbose` | détails photo par photo |

## En cas de problème

**« ExifTool not found »** : installez-le (voir plus haut). Sous Windows, le
plus simple reste de poser `exiftool.exe` à côté du script.

**Tout a été ignoré** : vos noms de dossiers doivent contenir un motif
`JJ-MM` (ou `J-M`) reconnaissable. Un dossier comme `divers` est ignoré,
c'est voulu.

**Mauvaise année partout** : définissez `year` (ou `annee`) dans la config,
ou passez `--year`.

**Une correspondance visuelle attendue n'a pas eu lieu** : il faut passer deux
barres, une forte similarité *et* le même jour que l'album. Vérifiez d'abord
la date EXIF de la photo de référence elle-même ; si elle est fausse, la garde
fait bien son travail en refusant.

**Seuls .jpg/.jpeg/.png sont traités.** Les RAW restent intacts, et les
vidéos sont hors périmètre pour l'instant.

## Contribuer

Les retours de bugs et les pull requests sont bienvenus. Il y a une suite de
tests (`pip install pytest && python -m pytest tests/`) et la CI lance ruff
ainsi que les tests sous Linux et Windows ; joignez donc un test quand vous
corrigez ou ajoutez quelque chose. Jetez un œil à `explain.md` pour comprendre
les entrailles avant de vous lancer.

## Licence

MIT, voir [LICENSE](LICENSE).
