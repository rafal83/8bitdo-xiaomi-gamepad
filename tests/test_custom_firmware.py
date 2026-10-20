"""Offline profile regression tests; never invokes the updater."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import time
import uuid
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import custom_firmware as c
from validate_custom_firmware import validate_image
from firmware_emu import FirmwareEmu
import struct


def main():
    result = {'status': 'running', 'profiles': [], 'invalid_profiles_rejected': 0,
              'cave_layouts_checked': 0, 'tampered_bundles_rejected': 0}
    original = c.load_original()
    v2_image, _ = c.v2.build(original)
    assert c.digest(v2_image) == '390a49ae6b952a5020506c8434aaa9493a05eacc0bd53d347a018a078b13098a'
    for key, invalid in [('schema', True), ('mi_pulse_ms', True), ('mi_pulse_ms', 39),
                         ('mi_pulse_ms', 1001), ('name', ''), ('name', 'a\nb'),
                         ('invert_axes', ['LX', 'LX']), ('invert_axes', ['foo']),
                         ('swap_sticks', 1), ('battery', 'yes'), ('reconnect', None)]:
        profile = deepcopy(c.DEFAULT_PROFILE); profile[key] = invalid
        try:
            c.validate_profile(profile)
        except ValueError:
            result['invalid_profiles_rejected'] += 1
        else:
            raise AssertionError(f'Invalid field accepted: {key}={invalid}')
    for transform in [lambda p: p.update(extra=True), lambda p: p['buttons'].update(A='Inconnu'),
                      lambda p: p['buttons'].pop('Mi')]:
        profile = deepcopy(c.DEFAULT_PROFILE); transform(profile)
        try:
            c.validate_profile(profile)
        except ValueError:
            result['invalid_profiles_rejected'] += 1
        else:
            raise AssertionError('Invalid mapping/schema accepted')

    # Every axis selection and stick order must fit its fixed code slot.
    for digital in (False, True):
        for selection in range(16):
            for swap in (False, True):
                profile = deepcopy(c.DEFAULT_PROFILE)
                if digital:
                    profile['buttons']['ZL'] = 'A'
                profile['invert_axes'] = [a for i, a in enumerate(c.AXES) if selection & (1 << i)]
                profile['swap_sticks'] = swap
                profile['mi_pulse_ms'] = 1000
                _, journal = c.build_profile(original, profile)
                assert journal['injected_lengths']['report'] <= 208
                assert journal['injected_lengths']['mi_pulse'] <= 64
                result['cave_layouts_checked'] += 1

    # Old JSON profiles continue to produce the exact hardware-tested firmware.
    letters = c.load_profile(c.ROOT / 'profiles/xiaomi_lettres.json')
    letters.update(accelerometer=False, rumble=False)
    legacy = deepcopy(letters)
    del legacy['accelerometer']; del legacy['rumble']
    del legacy['buttons']['ZL']; del legacy['buttons']['ZR']
    assert c.validate_profile(legacy) == letters
    assert c.digest(c.compile_profile(legacy)[1]) == '87c30630799b873073d7b80bfc9c2949408f3f4bce427dedcc18bbc9b93ff278'
    result['legacy_profile_compatible'] = True

    # Assert actual Switch USB bytes for every trigger destination. Include the
    # stock caller's OR of pressure bits before executing the stock encoder.
    usb_masks = {'A': (0, 8), 'B': (0, 4), 'X': (0, 2), 'Y': (0, 1),
                 'L': (2, 64), 'R': (0, 64), 'ZL': (2, 128), 'ZR': (0, 128),
                 'Moins': (1, 1), 'Plus': (1, 2), 'L3': (1, 8), 'R3': (1, 4),
                 'Home': (1, 16), 'Capture': (1, 32), 'Aucun': None}
    result['trigger_USB_cases'] = 0
    for source, offset in c.TRIGGER_OFFSETS.items():
        for destination, usb_mask in usb_masks.items():
            profile = deepcopy(c.DEFAULT_PROFILE)
            profile['buttons'][source] = destination
            image, _ = c.build_profile(original, profile)
            emu = FirmwareEmu(image)
            fixture = bytes(emu.uc.mem_read(0x20000000, 0x4000))
            for pressure in (0, 1, 84, 85, 86, 255):
                emu.uc.mem_write(0x20000000, fixture)
                emu.uc.mem_write(0x20001222, struct.pack('<HH', 24, 24))
                emu.uc.mem_write(0x20000044, b'\x30')
                packet = bytearray(22)
                packet[:2] = b'\xa1\x04'; packet[5] = 15; packet[6:10] = b'\x80' * 4
                packet[offset] = pressure
                emu.uc.mem_write(0x20010000, bytes(packet))
                emu.call(0x0800db1c, 0x20010000, 22, 0x20010400)
                assert emu.saved_ok and emu.stack_ok
                buttons, axes, triggers = struct.unpack('<IIH', emu.uc.mem_read(0x20010400, 10))
                if triggers & 255:
                    buttons |= 1 << 14
                if triggers >> 8:
                    buttons |= 1 << 15
                emu.call(0x08007204, buttons, axes, triggers, 0x20010800)
                assert emu.saved_ok and emu.stack_ok
                expected = bytearray(3)
                if pressure >= 85 and usb_mask is not None:
                    expected[usb_mask[0]] = usb_mask[1]
                assert bytes(emu.uc.mem_read(0x20010804, 3)) == expected, (source, destination, pressure)
                result['trigger_USB_cases'] += 1

    profiles = [deepcopy(c.DEFAULT_PROFILE)]
    targets = list(c.TARGET_BITS)
    for i, target in enumerate(targets):
        profile = deepcopy(c.DEFAULT_PROFILE)
        profile.update(name=f'Test Mi {target}', swap_sticks=bool(i & 1),
                       invert_axes=[a for bit, a in enumerate(c.AXES) if i & (1 << bit)],
                       mi_pulse_ms=[40, 120, 255, 256, 513, 1000][i % 6],
                       battery=bool(i & 2), reconnect=bool(i & 4))
        for j, button in enumerate(c.BUTTON_BITS):
            profile['buttons'][button] = targets[(i+j) % len(targets)]
        profile['buttons']['Mi'] = target
        profiles.append(profile)
    for pulse, battery, reconnect in [(False, False, False), (False, True, True)]:
        profile = deepcopy(c.DEFAULT_PROFILE)
        profile.update(name='Test sans prolongation', mi_pulse=pulse, battery=battery,
                       reconnect=reconnect, invert_axes=list(c.AXES), swap_sticks=True)
        profiles.append(profile)
    for mapping in ({'ZL': 'A'}, {'ZR': 'Aucun'}, {'ZL': 'ZR', 'ZR': 'ZL'},
                    {'ZL': 'Capture', 'ZR': 'Home'}, {'ZL': 'A', 'ZR': 'A', 'A': 'ZR'},
                    {'ZL': 'Aucun', 'ZR': 'Aucun', 'A': 'ZL', 'Mi': 'ZR'}):
        profile = deepcopy(c.DEFAULT_PROFILE)
        profile['name'] = 'Test gachettes ' + ', '.join(f'{k}={v}' for k, v in mapping.items())
        profile['buttons'].update(mapping)
        profile['invert_axes'] = list(c.AXES); profile['swap_sticks'] = True
        profiles.append(profile)
    for profile in profiles:
        image, _, journal = c.compile_profile(profile)
        validation = validate_image(image, profile)
        result['profiles'].append({'name': profile['name'], 'checks': validation['total_checks'],
                                   'lengths': journal['injected_lengths']})
        print(f"PASS {profile['name']}: {validation['total_checks']}", flush=True)

    folder, validation = c.generate_bundle(c.DEFAULT_PROFILE)
    result['example_bundle'] = str(folder)
    assert c.verify_bundle(folder / 'manifest.json')['status'] == 'verified'
    # Create isolated copies containing no flash launcher, then prove modified
    # payloads are refused even if the manifest's SHA is updated to match them.
    tests = c.BUNDLE_ROOT / 'cases' / uuid.uuid4().hex[:8]
    tests.mkdir(parents=True)
    for kind in ('dat', 'bin', 'profile', 'manifest_hash', 'dat_and_hash', 'checks'):
        case = tests / kind
        case.mkdir()
        for name in ('firmware.bin', 'firmware.dat', 'profile.json', 'manifest.json'):
            shutil.copyfile(folder / name, case / name)
        if kind in ('dat', 'bin', 'dat_and_hash'):
            name = 'firmware.bin' if kind == 'bin' else 'firmware.dat'
            data = bytearray((case / name).read_bytes()); data[-1] ^= 0x80
            (case / name).write_bytes(data)
        if kind == 'profile':
            profile = c.load_profile(case / 'profile.json')
            profile['buttons']['A'] = 'Capture'
            c.write_json(case / 'profile.json', profile)
        manifest = json.loads((case / 'manifest.json').read_text(encoding='utf-8'))
        if kind == 'manifest_hash':
            manifest['dat_sha256'] = '0' * 64
        if kind == 'dat_and_hash':
            manifest['dat_sha256'] = c.digest((case / 'firmware.dat').read_bytes())
        if kind == 'checks':
            manifest['validation_checks'] = 1
        c.write_json(case / 'manifest.json', manifest)
        try:
            c.verify_bundle(case / 'manifest.json')
        except ValueError:
            result['tampered_bundles_rejected'] += 1
        else:
            raise AssertionError(f'Modified bundle accepted: {kind}')

    # Instantiate Tk without showing a window; test presets, JSON round-trip and
    # asynchronous Generate through the actual UI event handler.
    import tkinter as tk
    from configure_xiaomi import Configurator
    root = tk.Tk(); root.withdraw()
    app = Configurator(root)
    assert app.get_profile() == c.DEFAULT_PROFILE
    app.preset(True)
    assert all(app.get_profile()['buttons'][name] == name for name in ('A', 'B', 'X', 'Y'))
    app.set_profile(c.DEFAULT_PROFILE)
    app.swap.set(True); app.axes['LY'].set(True)
    app.buttons['Mi'].set('Capture'); app.duration.set('513')
    app.name.set('Profil interface test')
    expected = app.get_profile()
    path = tests / 'gui_profile.json'
    c.write_json(path, expected)
    app.set_profile(c.load_profile(path))
    assert app.get_profile() == expected
    app.generate()
    assert app.busy
    deadline = time.monotonic() + 30
    while app.busy and time.monotonic() < deadline:
        root.update(); time.sleep(0.02)
    assert not app.busy and app.output_folder is not None, 'GUI generation did not finish'
    assert c.load_profile(app.output_folder / 'profile.json') == expected
    result['gui_bundle'] = str(app.output_folder)
    root.destroy()
    result['gui_profile_roundtrip_and_generation'] = 'passed'
    result['total_ARM_checks'] = sum(p['checks'] for p in result['profiles'])
    result['status'] = 'offline_passed'
    result['hardware_flashed'] = False
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='8bitdo_profiles_') as directory:
        c.BUNDLE_ROOT = Path(directory)
        main()
