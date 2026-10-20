# 8BitDo USB v1 + Xiaomi Mi Gamepad

Configurateur expérimental pour utiliser la **Xiaomi Mi Bluetooth Gamepad** sur
Nintendo Switch avec le **8BitDo USB Wireless Adapter orange, première génération**.
Il construit un firmware personnalisé à partir du firmware officiel **2.07** fourni
par l'utilisateur. Les autres modèles et versions ne sont pas pris en charge.

Le configurateur propose le remappage des boutons, les dispositions Xiaomi et
Nintendo, l'inversion des axes, l'échange des sticks, Mi → Home et les correctifs
de reconnexion et de batterie. La génération vérifie le firmware avant de proposer
son flash dans la même fenêtre. **Le flash nécessite un clic explicite.**

Les boutons, Mi/Home et la reconnexion ont été confirmés sur le matériel par
l'utilisateur. L'évolution de l'indicateur de batterie pendant la décharge reste
à vérifier. Les tests logiciels ne remplacent pas ces essais sur matériel.

Le transfert et la restauration utilisent uniquement l'updater intégré, via le
pilote HID de Windows. L'identification de l'adaptateur en version 2.07 est
confirmée sur matériel ; le transfert avec ce nouvel updater reste à y tester.

## Installation sous Windows

Installer Python **3.10 ou plus récent, 64 bits**, avec Tkinter et l'accès à la commande `python`,
puis ouvrir un terminal dans le dossier du projet :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
New-Item -ItemType Directory -Path local -Force
```

Le dépôt ne distribue **aucun firmware ni outil binaire propriétaire**. Fournir
soi-même le fichier suivant, obtenu auprès de 8BitDo, et le placer sous
ce nom dans `local/`. Seule l'empreinte exacte ci-dessous est acceptée :

| Fichier local | Origine attendue | SHA-256 |
| --- | --- | --- |
| `local/original.dat` | Firmware officiel 2.07, 100 380 octets | `0dc85f34bb0beba631076683c8ef3acc3e0d5babcfe62c5333b1af9acaee3798` |

La version 2.07 seule ne garantit pas une empreinte identique. Le programme
refuse les fichiers différents. Le BIN original est extrait du DAT en mémoire ;
il n'est pas nécessaire de le fournir séparément.

Le transfert utilise Windows PowerShell 5.1 (32 ou 64 bits) et les bibliothèques
système Windows. Aucune bibliothèque de l'utilitaire officiel n'est utilisée.
La présence d'un adaptateur n'est pas nécessaire
pour préparer et vérifier un profil.

## Utilisation

1. Lancer [Configurer_Xiaomi.cmd](Configurer_Xiaomi.cmd).
2. Choisir un préréglage ou cliquer sur un bouton de la manette pour changer sa
   commande Switch. Les pages **Sticks** et **Options** regroupent les autres réglages.
3. Cliquer sur **1. Préparer le firmware** pour générer et vérifier le profil.
4. Fermer l'application officielle 8BitDo et débrancher les autres adaptateurs
   8BitDo. Brancher l'adaptateur cible en maintenant son bouton d'appairage.
5. Depuis la page **Installer**, **Vérifier la connexion** permet de lire la version
   de l'adaptateur sans envoyer de firmware. Cliquer sur **2. Flasher ce profil** et attendre la fin du transfert avant de
   débrancher l'adaptateur.

Pour respecter les lettres imprimées sur la manette, choisir le préréglage
**Lettres Xiaomi : A→A, B→B, X→X, Y→Y**. Remettre le remappage système de la
Switch à zéro pour éviter le cumul. Tout changement de profil nécessite une
nouvelle génération et un nouveau flash.

**ZL et ZR** sont disponibles comme boutons à remapper et comme commandes de
destination. Les deux zones au-dessus de la photo représentent ces gâchettes,
situées à l'arrière et peu visibles sur une vue de face. Par exemple : ZL → A,
A → ZR, ZL ↔ ZR ou ZR → Aucun.

Dès qu'un remappage implique ZL/ZR, les deux gâchettes utilisent des appuis
numériques, déclenchés à partir de 85/255 de leur course (environ un tiers).
Leurs pressions analogiques ne sont alors plus transmises. Sans ce remappage,
elles conservent leur fonctionnement d'origine. Les anciens profils JSON restent
compatibles : ZL → ZL et ZR → ZR sont ajoutés automatiquement à la lecture.

- [Verifier_Firmware.cmd](Verifier_Firmware.cmd) vérifie les fichiers officiels et
  l'inventaire USB, sans envoyer de commande USB.
- [Restaurer_Officiel.cmd](Restaurer_Officiel.cmd) déclenche la restauration du DAT
  officiel fourni, avec les mêmes conditions de branchement.
- Les profils JSON se sauvegardent et se rechargent dans le configurateur.
  Deux exemples se trouvent dans [profiles/](profiles/).

## Fichiers

| Dossier | Contenu |
| --- | --- |
| `scripts/` | Configurateur, construction, vérification, transfert et sources assembleur. |
| `profiles/` | Deux profils d'exemple, sans firmware. |
| `assets/` | Photo de la Xiaomi Mi Gamepad utilisée par l'interface. |
| `tests/` | Tests logiciels et transfert simulé. |
| `third_party/8cryptdo/` | Codec DAT Python sous licence MIT, avec sa licence d'origine. |
| `local/` | Entrées privées, firmwares générés, journaux et résultats de tests ; exclu de Git. |

Les nouveaux firmwares sont générés dans `local/generated/`, avec leur profil,
leur manifeste et un fichier d'instructions. Le flash se lance depuis le configurateur. Les journaux de
transfert vont dans `local/flash_logs/`. Ces fichiers dépendent du dossier du
projet ; conserver celui-ci en place.

La photo incluse dans `assets/mi-gamepad.png` sert d'aperçu cliquable. Les zones
de sélection sont calées sur ses vrais boutons et suivent le redimensionnement
de la fenêtre. Le configurateur adapte l'image avec Pillow, sans modifier le
fichier. Si elle est absente ou illisible, le dessin reste disponible.

Le `.gitignore` exclut `local/`, les environnements et caches Python, les
firmwares, les bibliothèques binaires et les archives générées. Les sources,
photos, profils JSON et documents restent visibles pour Git. Ne pas utiliser
`git add -f` pour y ajouter les fichiers privés.

## Développement et tests

Après l'installation et l'ajout du DAT officiel :

```powershell
.\.venv\Scripts\python.exe tests/test_custom_firmware.py
.\.venv\Scripts\python.exe tests/test_configurator_ui.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/test_native_flasher.ps1
```

Le premier test couvre 24 profils, soit 181 314 contrôles ARM, 180 cas de
remappage des gâchettes jusqu'aux octets USB Switch, les profils invalides et les
fichiers altérés. Chaque profil passe 3 156 contrôles sans remappage de gâchettes,
ou 9 366 lorsqu'il l'utilise. Les 64 combinaisons de disposition des sticks et
de mode des gâchettes sont également assemblées et vérifiées.
Le second vérifie le configurateur et simule le processus
de flash et d'identification. Le troisième couvre 17 scénarios du protocole HID
avec un faux transport : ordre et contenu des blocs, identifiants, refus de
commandes, délais dépassés et finalisation. Il vérifie aussi les DAT invalides,
le contrôle SHA-256 avant ouverture USB et les structures Windows 32/64 bits.
**Ces tests ne flashent aucun adaptateur.** Les tests Tkinter nécessitent une
session graphique Windows et suppriment leurs firmwares temporaires à la fin.
Les résultats du test des profils sont écrits dans `local/test_results/`.

L'updater est une implémentation indépendante du protocole décrit dans le
[plugin 8BitDo de fwupd](https://github.com/fwupd/fwupd/tree/main/plugins/ebitdo).
Il vérifie l'identité USB, la version 2.07 et les acquittements du bootloader.
Un bloc en erreur n'est jamais renvoyé automatiquement.

## Licence

Le code du projet est sous [licence MIT](LICENSE), copyright 2026 Raph.
La photo de la manette a été fournie par l'utilisateur et déclarée libre de droits ;
elle est distincte du code couvert par la licence MIT.
Le codec tiers provient de `AeroModes/8cryptdo`, révision
`aaffcebafa481631de9c1d722a5693d02234e43a`. Sa licence MIT et son crédit d'origine
sont conservés dans [third_party/8cryptdo/LICENSE](third_party/8cryptdo/LICENSE).
Cette licence ne s'applique pas au firmware ou aux outils propriétaires fournis
séparément par l'utilisateur.
