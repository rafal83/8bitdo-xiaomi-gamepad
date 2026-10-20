# 8BitDo USB v1 + Xiaomi Mi Gamepad

Configurateur expérimental pour utiliser la **Xiaomi Mi Bluetooth Gamepad** sur
Nintendo Switch avec le **8BitDo USB Wireless Adapter orange, première génération**.
Il construit un firmware personnalisé à partir du firmware officiel **2.07** fourni
par l'utilisateur. Les autres modèles et versions ne sont pas pris en charge.

Le configurateur propose le remappage des boutons, les dispositions Xiaomi et
Nintendo, l'inversion des axes, l'échange des sticks, Mi → Home et les correctifs
de reconnexion et de batterie. L'accéléromètre et les vibrations sont activés
par défaut, avec une page de test sur PC.
La génération vérifie le firmware avant de proposer
son flash dans la même fenêtre. **Le flash nécessite un clic explicite.**

Les boutons, Mi/Home, la reconnexion et les moteurs de vibration ont été confirmés sur le matériel par
l'utilisateur. L'évolution de l'indicateur de batterie pendant la décharge reste
à vérifier. Les tests logiciels ne remplacent pas ces essais sur matériel.

Le transfert et la restauration utilisent uniquement l'updater intégré, via le
pilote HID de Windows. L'identification de l'adaptateur en version 2.07 est
confirmée sur matériel.

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

## Accéléromètre et vibrations

Dans **Options → Mouvement et vibrations**, deux cases permettent d'activer
séparément les capteurs et les moteurs. Elles sont activées par défaut dans les
nouveaux profils et les deux exemples fournis. Les profils déjà enregistrés
conservent leurs réglages ; les anciens profils sans ces champs gardent les deux
options désactivées pour préserver leur firmware à l'identique.

La Xiaomi fournit trois axes d'accélération, sans gyroscope, et deux moteurs
classiques. Le firmware active le capteur avec le rapport `31 01 08`,
transmet ses valeurs signées dans les rapports Switch et laisse les vitesses
angulaires à zéro. L'échelle et l'orientation des axes restent à mesurer sur
matériel. Il ne reproduit pas les fonctions gyroscopiques d'une manette Switch.
Les demandes de vibration Switch et XInput sont traduites vers les deux moteurs
Xiaomi ; les effets HD Rumble ne sont pas reproduits fidèlement.
Le réglage **Intensité Switch (%)** amplifie les niveaux issus de cette conversion,
de 100 à 300 % par pas de 25 %, avec 200 % pour les nouveaux profils. 100 % garde le niveau
d'origine. Chaque moteur reste limité à 255 et une commande d'arrêt reste à zéro.
Ce gain ne modifie pas les vibrations XInput et ne garantit pas une force physique
proportionnelle au pourcentage. Les anciens profils sans ce réglage gardent 100 %.
Après un changement, préparer et flasher le profil pour l'appliquer à l'adaptateur.
Cette version corrige aussi le retour au code d'origine pour les autres manettes
dans les chemins de vibration. Les firmwares avec vibrations préparés par une
version précédente doivent être régénérés avant leur flash ; leurs profils JSON
restent lisibles.
Référence du protocole Xiaomi : [irungentoo/Xiaomi_gamepad](https://github.com/irungentoo/Xiaomi_gamepad).

**Ces options agrandissent l'image de 1 Kio. Les valeurs du capteur restent à valider sur matériel.**
Elle occupe `0x0801BC00..0x0801BFFF` et relève la limite basse de la première
banque de réglages internes de `0x0801B000` à `0x0801C000`. D'anciens réglages
internes 8BitDo présents dans cette page peuvent être perdus au premier flash.
La seconde banque (`0x0801E000..0x0801FFFF`) reste inchangée. Le test ARM exécute
l'allocateur réel et vérifie qu'il ne parcourt plus la zone du nouveau code.
Les profils JSON enregistrés sur le PC ne sont pas concernés.

La génération et la vérification du manifeste reconstruisent également cette
extension. L'updater intégré accepte ses 101 404 octets seulement après ces
vérifications. Sans ces options, le DAT conserve sa taille de 100 380 octets.

## Tester les entrées et sorties

1. Brancher le 8BitDo **normalement**, sans maintenir son bouton, et connecter la Xiaomi.
2. Ouvrir **Tester**, cliquer sur **Actualiser**, choisir le périphérique et **Démarrer**.
3. Appuyer sur les boutons et déplacer les sticks/gâchettes. L'écran montre les
   commandes réellement reçues par le PC, après les remappages du firmware installé.
4. Tester **Petit moteur**, **Gros moteur** ou **Les deux**. L'impulsion dure
   0,5 seconde, avec une intensité réglable de 0 à 50 %. La vibration doit être
   activée dans le firmware installé pour fonctionner à travers le 8BitDo.

| Connexion | Boutons et sticks | Accéléromètre | Sortie vibration |
| --- | --- | --- | --- |
| Adaptateur en mode XInput | Commandes Xbox ; Home/Capture absents | Non transporté par XInput | Oui, si le nouveau firmware est installé |
| Adaptateur en mode HID Switch | Commandes Switch | Avec l'option capteur du nouveau firmware | Avec l'option vibration |
| Xiaomi appairée directement au Bluetooth du PC | Commandes physiques Xiaomi ; Home désigne Mi | Valeurs brutes du capteur | Commande directe des moteurs |

Le mode test ne change pas l'appairage ni le mode XInput/Switch de l'adaptateur.
Avec les affectations par défaut, maintenir **Back + L pendant 3 secondes**
permet de passer manuellement en mode Switch sur PC ; **Back + Haut** revient
en XInput. Ces raccourcis sont ceux du [manuel 8BitDo](https://support.8bitdo.com/Manual/USB_Adapter/switchpro_windows.html),
appliqués aux commandes Moins/L de la Xiaomi. Un remappage de Back ou L modifie
donc les boutons à utiliser. Le mode manuel persiste : sélectionner Switch
pour rejouer sur la console. Arrêter le test et actualiser la liste après
un changement de mode.
En HID Switch, il demande temporairement les rapports de capteur ; cela ne
modifie pas le firmware. **Arrêter**, changer de page, préparer un firmware ou
fermer la fenêtre arrête les moteurs et ferme la connexion de test.
Le mode test n'envoie aucune commande de flash ou de calibration de la Xiaomi.

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
.\.venv\Scripts\python.exe tests/test_controller_io.py
```

Le premier test couvre 24 profils, 180 cas de
remappage des gâchettes jusqu'aux octets USB Switch, les profils invalides et les
fichiers altérés. Chaque profil passe les contrôles ARM de ses options et de
ses remappages. Les 64 combinaisons de disposition des sticks et
de mode des gâchettes sont également assemblées et vérifiées.
Le second vérifie le configurateur et simule le processus
de flash et d'identification. Le troisième couvre 18 scénarios du protocole HID
avec un faux transport : ordre et contenu des blocs, identifiants, refus de
commandes, délais dépassés et finalisation. Il vérifie aussi les DAT invalides,
le contrôle SHA-256 avant ouverture USB et les structures Windows 32/64 bits.
**Ces tests ne flashent aucun adaptateur.** Les tests Tkinter nécessitent une
session graphique Windows et suppriment leurs firmwares temporaires à la fin.
Le quatrième couvre les rapports d'entrée/sortie, l'arrêt automatique des moteurs,
les déconnexions, les variantes du firmware et leurs hooks ARM. Il vérifie les
neuf gains Switch de 100 à 300 %, les 256 niveaux de chaque moteur, la saturation,
l'arrêt à zéro et les retours au code d'origine pour les autres manettes. Les protocoles et
processus sont simulés dans ces tests. Les résultats s'affichent dans le terminal.

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
