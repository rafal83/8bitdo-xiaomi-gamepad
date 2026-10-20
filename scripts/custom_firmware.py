"""Offline, strictly bounded Xiaomi firmware profiles. No USB or flashing API."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import argparse
import json
import re
import uuid

from patch_xiaomi import ROOT, BASE, REPORT, NAME, END, assemble, digest
import patch_xiaomi_home as v2
from rebuild_dat import rebuild, load_original, ORIGINAL_DAT

BUNDLE_ROOT = ROOT / 'local/generated'
BUTTON_BITS = {'A': 0, 'B': 1, 'X': 3, 'Y': 4, 'L': 6, 'R': 7,
               'Back': 10, 'Start': 11, 'L3': 13, 'R3': 14}
TARGET_BITS = {'A': 13, 'B': 12, 'X': 4, 'Y': 5, 'L': 10, 'R': 11,
               'ZL': 14, 'ZR': 15,
               'Moins': 3, 'Plus': 0, 'L3': 1, 'R3': 2, 'Home': 17,
               'Capture': 22, 'Aucun': None}
TRIGGER_OFFSETS = {'ZL': 12, 'ZR': 13}
TRIGGER_THRESHOLD = 85
AXES = ('LX', 'LY', 'RX', 'RY')
DEFAULT_PROFILE = {
    'schema': 1, 'name': 'Xiaomi - reglages actuels',
    'buttons': {'A': 'B', 'B': 'A', 'X': 'Y', 'Y': 'X', 'L': 'L', 'R': 'R',
                'ZL': 'ZL', 'ZR': 'ZR', 'Back': 'Moins', 'Start': 'Plus',
                'L3': 'L3', 'R3': 'R3', 'Mi': 'Home'},
    'invert_axes': [], 'swap_sticks': False, 'mi_pulse': True,
    'mi_pulse_ms': 120, 'battery': True, 'reconnect': True,
    'accelerometer': True, 'rumble': True,
    'switch_rumble_gain': 200,
    'switch_rumble_minimum': 0,
}


def validate_profile(value):
    """Reject ambiguous or forward-incompatible profiles before assembling anything."""
    if type(value) is dict and 'switch_rumble_minimum' not in value:
        value = {**value, 'switch_rumble_minimum': 0}
    if type(value) is dict and 'switch_rumble_gain' not in value:
        # Existing profiles retain their original motor strengths.
        value = {**value, 'switch_rumble_gain': 100}
    if type(value) is dict and set(value) == set(DEFAULT_PROFILE) - {'accelerometer', 'rumble'}:
        # Preserve legacy firmware exactly, independently of new-profile defaults.
        value = {**value, 'accelerometer': False, 'rumble': False}
    if type(value) is not dict or set(value) != set(DEFAULT_PROFILE):
        raise ValueError('Champs de profil inconnus ou manquants.')
    if type(value['schema']) is not int or value['schema'] != 1:
        raise ValueError('Version de profil non prise en charge.')
    if type(value['name']) is not str or not 1 <= len(value['name'].strip()) <= 80:
        raise ValueError('Le nom doit contenir de 1 a 80 caracteres.')
    if any(ord(c) < 32 for c in value['name']):
        raise ValueError('Le nom contient un caractere de controle.')
    buttons = value['buttons']
    if type(buttons) is not dict:
        raise ValueError('Liste de boutons incorrecte.')
    expected_buttons = set(DEFAULT_PROFILE['buttons'])
    if set(buttons) == expected_buttons - set(TRIGGER_OFFSETS):
        # Existing profiles and manifests keep their original trigger behavior.
        buttons = {**buttons, 'ZL': 'ZL', 'ZR': 'ZR'}
    elif set(buttons) != expected_buttons:
        raise ValueError('Liste de boutons incorrecte.')
    if any(type(v) is not str or v not in TARGET_BITS for v in buttons.values()):
        raise ValueError('Destination de bouton inconnue.')
    axes = value['invert_axes']
    if (type(axes) is not list or any(type(a) is not str or a not in AXES for a in axes)
            or len(set(axes)) != len(axes)):
        raise ValueError('Axes inconnus ou dupliques.')
    for key in ('swap_sticks', 'mi_pulse', 'battery', 'reconnect', 'accelerometer', 'rumble'):
        if type(value[key]) is not bool:
            raise ValueError(f'{key} doit etre un booleen.')
    if type(value['mi_pulse_ms']) is not int or not 40 <= value['mi_pulse_ms'] <= 1000:
        raise ValueError('La duree Mi doit etre un entier de 40 a 1000 ms.')
    gain = value['switch_rumble_gain']
    if type(gain) is not int or not 100 <= gain <= 300 or gain % 25:
        raise ValueError('L’intensite des vibrations Switch doit aller de 100 a 300 %, par pas de 25 %.')
    minimum = value['switch_rumble_minimum']
    if type(minimum) is not int or not 0 <= minimum <= 100:
        raise ValueError('Le minimum des vibrations Switch doit etre un entier de 0 a 100 %.')
    result = deepcopy(value)
    result['buttons'] = deepcopy(buttons)
    result['name'] = result['name'].strip()
    result['invert_axes'] = [a for a in AXES if a in axes]
    return result


def load_profile(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Champ JSON duplique : {key}')
            result[key] = value
        return result
    return validate_profile(json.loads(Path(path).read_text(encoding='utf-8-sig'),
                                       object_pairs_hook=unique_object))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def mapping_summary(profile):
    return ' / '.join(f"{name} -> {profile['buttons'].get(name, name)}" for name in ('A', 'B', 'X', 'Y', 'ZL', 'ZR'))


def triggers_remapped(profile):
    buttons = profile['buttons']
    return (any(buttons.get(name, name) != name for name in TRIGGER_OFFSETS)
            or any(dest in TRIGGER_OFFSETS for name, dest in buttons.items() if name not in TRIGGER_OFFSETS))


def decoder_source(profile):
    source = v2.report_source()
    start = source.index('ldrb r4, [r0, #2]')
    end = source.index('ldrb r4, [r0, #21]')
    digital_triggers = triggers_remapped(profile)
    mapping = '''movs r3, #0
ldrh r4, [r0, #2]
adr.w r6, button_map
movs r5, #0
map_loop:
lsrs r4, r4, #1
bcc next_button
ldrb r1, [r6, r5]
cmp r1, #255
beq next_button
movs r7, #1
lsls r1, r7, r1
orrs r3, r1
next_button:
adds r5, #1
cmp r5, #16
blo map_loop
'''
    if digital_triggers:
        # Prefix two virtual input bits: ZR at bit 0 and ZL at bit 1.
        # ADC packs the comparison carry without modifying the HID packet.
        # A register shift by 255 yields zero, so disabled entries need no branch.
        mapping = f'''movs r3, #0
ldrh r4, [r0, #2]
ldrb r5, [r0, #12]
cmp r5, #{TRIGGER_THRESHOLD}
adcs r4, r4
ldrb r5, [r0, #13]
cmp r5, #{TRIGGER_THRESHOLD}
adcs r4, r4
adr.w r6, button_map
movs r7, #1
map_loop:
lsrs r4, r4, #1
bcc next_button
ldrb r1, [r6]
lsls r1, r7, r1
orrs r3, r1
next_button:
adds r6, #1
cmp r4, #0
bne map_loop
'''
        # The stock caller ORs trigger bits from nonzero analog bytes. Clear
        # those bytes in digital mode to prevent the original command leaking.
        source = source.replace('ldrh r3, [r0, #12]', 'movs r3, #0')
    source = source[:start] + mapping + source[end:]
    source = source.replace('push {r4, r5, r6, lr}', 'push {r4, r5, r6, r7, lr}')
    source = source.replace('pop {r4, r5, r6, pc}', 'pop {r4, r5, r6, r7, pc}')
    mi = TARGET_BITS[profile['buttons']['Mi']]
    source = source.replace('orr r3, r3, r4, lsl #17',
                            '' if mi is None else f'orr r3, r3, r4, lsl #{mi}')
    axes = ''
    if profile['swap_sticks']:
        axes += 'ror r3, r3, #16\n'
    mask = sum(0xff << (i * 8) for i, a in enumerate(AXES) if a in profile['invert_axes'])
    if mask:
        axes += f'ldr r6, ={mask:#x}\neors r3, r6\n'
    source = source.replace('str r3, [r2, #4]', axes + 'str r3, [r2, #4]')
    table = [255] * 16
    for name, bit in BUTTON_BITS.items():
        dest = TARGET_BITS[profile['buttons'][name]]
        table[bit] = 255 if dest is None else dest
    if digital_triggers:
        triggers = [TARGET_BITS[profile['buttons'][name]] for name in ('ZR', 'ZL')]
        table = [255 if dest is None else dest for dest in triggers] + table
    return source + '\n.align 2\nbutton_map:\n.byte ' + ','.join(map(str, table)) + '\n'


def pulse_source(profile):
    # Replay the stock PUSH first. Its saved r0 becomes the stretched argument;
    # the original epilogue restores the original callee-saved registers and LR.
    source = (ROOT / 'scripts/firmware/xiaomi_switch_home_hook.s').read_text()
    original_push = 'push.w {r0, r1, r2, r3, r4, r5, r6, r7, r8, r9, r10, r11, r12, lr}'
    source = source.replace('push {r4, r5, r6, lr}', original_push)
    source = source.replace('pop.w {r4, r5, r6, lr}\n' + original_push, 'str r0, [sp]')
    # MOVW accepts every integer in the advertised interval, unlike CMP's
    # modified immediate encoding. r6 is free after the timestamp subtraction.
    source = source.replace('cmp r5, #120', f"movw r6, #{profile['mi_pulse_ms']}\ncmp r5, r6")
    bit = TARGET_BITS[profile['buttons']['Mi']]
    source = source.replace('orr r0, r0, #0x20000', f'orr r0, r0, #{1 << bit:#x}')
    return source


def build_profile(original, value):
    profile = validate_profile(value)
    # Reuse all exact-stock, startup, cave, prologue and reserved-RAM guards.
    _, baseline = v2.build(original)
    report = assemble(decoder_source(profile), REPORT)
    pulse = profile['mi_pulse'] and profile['buttons']['Mi'] != 'Aucun'
    home = assemble(pulse_source(profile), v2.HOME) if pulse else b''
    if len(report) > v2.HOME - REPORT or len(home) > v2.RECONNECT - v2.HOME:
        raise ValueError(f'Espace firmware insuffisant : decodeur={len(report)}, Mi={len(home)}.')
    excluded = {REPORT, v2.HOME}
    if not pulse:
        excluded.add(0x08007204)
    if not profile['battery']:
        excluded.update((0x0800a7b2, v2.BATTERY))
    if not profile['reconnect']:
        excluded.update((0x0801017c, v2.RECONNECT))
    edits = [(int(e['address'], 16), bytes.fromhex(e['new']), e['reason'])
             for e in baseline['edits'] if int(e['address'], 16) not in excluded]
    edits.append((REPORT, report, 'Xiaomi profile buttons, axes, Mi and battery cache'))
    if pulse:
        edits.append((v2.HOME, home, 'Xiaomi output pulse for the configured Mi destination'))
    image = bytearray(original)
    occupied = set()
    journal = []
    for address, new, reason in edits:
        offset = address - BASE
        span = set(range(offset, offset + len(new)))
        if offset < 0 or address + len(new) > END or occupied & span:
            raise ValueError('Modification hors limites ou chevauchement.')
        occupied |= span
        image[offset:offset+len(new)] = new
        journal.append({'address': hex(address), 'offset': hex(offset), 'length': len(new),
                        'old': original[offset:offset+len(new)].hex(), 'new': new.hex(), 'reason': reason})
    if len(image) != len(original) or image[:0xec] != original[:0xec]:
        raise ValueError('Taille ou vecteurs modifies.')
    from xiaomi_feedback import extend
    image, feedback_edits = extend(bytes(image), original, profile, decoder_source(profile))
    # Entry trampolines intentionally replace the first four bytes of an
    # existing hook. Describe the final image with disjoint, accurate ranges.
    by_address = {entry['address']: entry for entry in journal}
    for entry in feedback_edits:
        previous = by_address.get(entry['address'])
        if previous is not None:
            size = max(previous['length'], entry['length'])
            offset = int(entry['offset'], 16)
            entry = {**previous, **entry, 'length': size, 'new': image[offset:offset+size].hex()}
        by_address[entry['address']] = entry
    journal = list(by_address.values())
    occupied = set()
    for entry in journal:
        offset = int(entry['offset'], 16)
        span = set(range(offset, offset+entry['length']))
        if occupied & span or image[offset:offset+entry['length']].hex() != entry['new']:
            raise ValueError('Journal de modifications incohérent.')
        occupied |= span
    return bytes(image), {'schema': 1, 'profile': profile, 'original_sha256': digest(original),
                          'patched_sha256': digest(image), 'edits': journal,
                          'injected_lengths': {'report': len(report), 'mi_pulse': len(home)},
                          'hardware_validated': False}


def compile_profile(profile):
    original = load_original()
    image, journal = build_profile(original, profile)
    dat = rebuild(ORIGINAL_DAT.read_bytes(), original, image)
    return image, dat, journal


def bundle_instructions(profile, checks):
    instructions = (
        f"Profil : {profile['name']}\nTouches : {mapping_summary(profile)}\n"
        f"Tests hors materiel : {checks} controles reussis.\n\n"
        "1. Lancer Configurer_Xiaomi.cmd a la racine du projet.\n"
        "2. Ouvrir le fichier profile.json de ce dossier et cliquer sur Preparer le firmware.\n"
        "3. Fermer l'application 8BitDo, debrancher les autres adaptateurs 8BitDo,\n"
        "   puis brancher l'adaptateur cible en maintenant son bouton d'appairage.\n"
        "4. Cliquer sur Flasher ce profil dans le configurateur.\n\n"
        "Le configurateur ne flashe jamais automatiquement. Garder le dossier du projet en place.\n"
        "Restaurer_Officiel.cmd a la racine restaure l'original.\n"
        "Sur Switch, remettre le remappage des touches a zero avant d'evaluer ce profil.\n"
        "La batterie pendant la decharge reste a valider sur votre materiel.\n")
    if profile['accelerometer'] or profile['rumble']:
        instructions += (
            f"\nMouvement et vibrations : accelerometre={profile['accelerometer']}, vibrations={profile['rumble']}.\n"
            f"Intensite des vibrations Switch : {profile['switch_rumble_gain']} % (100 % = niveau d'origine).\n"
            f"Minimum Switch pour un effet non nul : {profile['switch_rumble_minimum']} % (0 % = aucun minimum).\n"
            "L'image occupe 1 Kio supplementaire ; la limite basse du stockage interne est relevee.\n"
            "Des reglages memorises par le 8BitDo peuvent etre perdus lors du premier flash.\n"
            "Les donnees d'acceleration sont brutes, sans gyroscope. Echelle et orientation a valider.\n"
            "Les valeurs du capteur restent a valider sur materiel.\n")
    return instructions


def generate_bundle(value, progress=lambda message: None):
    profile = validate_profile(value)
    progress('Assemblage du firmware et verification du conteneur…')
    image, dat, _ = compile_profile(profile)
    progress('Tests ARM : boutons, axes, Mi, batterie et reconnexion…')
    from validate_custom_firmware import validate_image
    validation = validate_image(image, profile)
    slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', profile['name']).strip('_')[:40] or 'profil'
    folder = BUNDLE_ROOT / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + slug + '_' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'firmware.bin').write_bytes(image)
    (folder / 'firmware.dat').write_bytes(dat)
    write_json(folder / 'profile.json', profile)
    write_json(folder / 'manifest.json', {'schema': 1, 'profile': profile,
               'bin_sha256': digest(image), 'dat_sha256': digest(dat),
               'validation_checks': validation['total_checks'], 'hardware_validated': False})
    (folder / 'README.txt').write_text(
        bundle_instructions(profile, validation['total_checks']), encoding='utf-8')
    progress('Reconstruction independante et comparaison des fichiers…')
    verify_bundle(folder / 'manifest.json')
    return folder, validation


def verify_bundle(manifest_path):
    """Rebuild the trusted recipe, never accept a DAT solely by a supplied hash."""
    path = Path(manifest_path).resolve(strict=True)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    keys = {'schema', 'profile', 'bin_sha256', 'dat_sha256', 'validation_checks', 'hardware_validated'}
    if (type(manifest) is not dict or set(manifest) != keys or type(manifest['schema']) is not int
            or manifest['schema'] != 1 or manifest['hardware_validated'] is not False):
        raise ValueError('Manifeste de profil incorrect.')
    profile = load_profile(path.parent / 'profile.json')
    if profile != validate_profile(manifest['profile']):
        raise ValueError('Le profil ne correspond pas au manifeste.')
    image, dat, _ = compile_profile(profile)
    for name, expected, key in [('firmware.bin', image, 'bin_sha256'), ('firmware.dat', dat, 'dat_sha256')]:
        if (path.parent / name).read_bytes() != expected or manifest[key] != digest(expected):
            raise ValueError(f'{name} ne correspond pas au firmware reconstruit. Regenerer le profil.')
    from validate_custom_firmware import validate_image
    validation = validate_image(image, profile)
    if type(manifest['validation_checks']) is not int or manifest['validation_checks'] != validation['total_checks']:
        raise ValueError('Nombre de tests du manifeste incorrect.')
    return {'status': 'verified', 'firmware': str(path.parent / 'firmware.dat'),
            'sha256': digest(dat), 'profile_name': profile['name'], 'checks': validation['total_checks'],
            'mapping_summary': mapping_summary(profile)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--profile', type=Path, help='Generate and verify an offline bundle')
    group.add_argument('--manifest', type=Path, help='Rebuild and verify an existing bundle')
    group.add_argument('--write-default', type=Path)
    args = parser.parse_args()
    try:
        if args.write_default:
            write_json(args.write_default, DEFAULT_PROFILE)
            result = {'profile': str(args.write_default)}
        elif args.manifest:
            result = verify_bundle(args.manifest)
        else:
            folder, validation = generate_bundle(load_profile(args.profile))
            result = {'folder': str(folder), 'checks': validation['total_checks']}
        print(json.dumps(result, ensure_ascii=True))
    except Exception as error:
        parser.exit(1, f'ECHEC : {error}\n')


if __name__ == '__main__':
    main()
