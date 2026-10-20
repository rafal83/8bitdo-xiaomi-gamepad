"""GUI regression tests. The updater process is mocked: no USB write is possible."""
import json
from pathlib import Path
import struct
import sys
import threading
import tempfile
import time
from unittest.mock import patch
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import custom_firmware as c
from configure_xiaomi import Configurator, flash_command, updater_command
from firmware_emu import FirmwareEmu


def main():
    temporary = tempfile.TemporaryDirectory(prefix='8bitdo_ui_')
    old_bundle_root = c.BUNDLE_ROOT
    c.BUNDLE_ROOT = Path(temporary.name)
    root = tk.Tk()
    root.withdraw()
    app = Configurator(root)
    assertions = 0
    def check(value):
        nonlocal assertions
        assert value
        assertions += 1
    def pump_until(condition):
        deadline = time.monotonic() + 30
        while not condition() and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        check(condition())
    def enabled(widget):
        return str(widget.cget('state')) != 'disabled'

    try:
        with patch('configure_xiaomi.subprocess.Popen') as spawn:
            check(not enabled(app.flash_button))
            app.flash()
            check(spawn.call_count == 0)
            # The visual editor must bind to the selected physical button only.
            for name in app.buttons:
                before = app.get_profile()['buttons']
                app.select_button(name)
                check(str(app.target_combo.cget('textvariable')) == str(app.buttons[name]))
                app.target_combo.set('Capture')
                after = app.get_profile()['buttons']
                check(after[name] == 'Capture' and all(after[k] == v for k, v in before.items() if k != name))
                check(app.selected_title.get() == f'{name} → Capture')
                app.reset_selected_button()
                check(app.buttons[name].get() == c.DEFAULT_PROFILE['buttons'][name])
            for index, (title, _) in enumerate(app.PAGE_TITLES):
                app.show_page(index)
                check(app.page_title.get() == title and app.notebook.index('current') == index)
            app.show_page(0)
            app.pulse.set(False)
            check(not enabled(app.duration_input))
            app.pulse.set(True)
            check(enabled(app.duration_input))
            app.start_job('generation')
            selected = app.selected_button.get()
            app.select_button('A' if selected != 'A' else 'B')
            check(app.selected_button.get() == selected)
            check(not enabled(app.target_combo))
            app.finish_job()
            app.buttons['Mi'].set('Capture')
            app.buttons['R'].set('L3')
            app.buttons['ZL'].set('A')
            app.buttons['ZR'].set('Capture')
            app.preset(True)
            check(app.buttons['Mi'].get() == 'Capture' and app.buttons['R'].get() == 'L3')
            check(app.buttons['ZL'].get() == 'A' and app.buttons['ZR'].get() == 'Capture')
            check(all(app.buttons[k].get() == k for k in ('A', 'B', 'X', 'Y')))
            app.set_profile(c.load_profile(c.ROOT / 'profiles/xiaomi_lettres.json'))
            expected = app.get_profile()
            check(not enabled(app.flash_button))
            app.generate()
            check(app.busy and not enabled(app.generate_button))
            check(not enabled(app.flash_button))
            pump_until(lambda: not app.busy)
            check(spawn.call_count == 0)  # Generate must never start a flash.
            check(enabled(app.flash_button))
            folder = app.output_folder
            check(c.load_profile(folder / 'profile.json') == expected)
            check('A -> A' in app.flash_info.get())

            # Editing, loading or changing presets must invalidate the old output.
            app.buttons['A'].set('B')
            check(not enabled(app.flash_button) and not enabled(app.open_button))
            app.flash()
            check(spawn.call_count == 0)
            app.buttons['A'].set('A')
            check(enabled(app.flash_button) and 'A -> A' in app.flash_info.get())
            app.preset(False)
            check(not enabled(app.flash_button))
            app.preset(True)
            check(enabled(app.flash_button))

            # Even a programmatic change during generation cannot enable stale flash.
            app.start_job('generation')
            app.buttons['A'].set('B')
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            validation = {'total_checks': manifest['validation_checks']}
            app.events.put(('done', (folder, validation, expected)))
            pump_until(lambda: not app.busy)
            check(not enabled(app.flash_button))
            app.buttons['A'].set('A')
            check(enabled(app.flash_button))

        # Verify actual decoder -> Switch USB bytes, independent of menu labels.
        emu = FirmwareEmu((folder / 'firmware.bin').read_bytes())
        emu.uc.mem_write(0x20001222, struct.pack('<HH', 24, 24))
        emu.uc.mem_write(0x20000000, b'\0')
        emu.uc.mem_write(0x20000044, b'\x30')
        outputs = {}
        for name, physical_bit, switch_mask in [('A', 0, 8), ('B', 1, 4), ('X', 3, 2), ('Y', 4, 1)]:
            packet = bytearray(22)
            packet[:2] = b'\xa1\x04'; packet[5] = 15; packet[6:10] = b'\x80' * 4
            packet[2] = 1 << physical_bit
            emu.uc.mem_write(0x20010000, bytes(packet))
            emu.call(0x0800db1c, 0x20010000, 22, 0x20010400)
            state = struct.unpack('<IIH', emu.uc.mem_read(0x20010400, 10))
            emu.call(0x08007204, *state, 0x20010800)
            raw = bytes(emu.uc.mem_read(0x20010800, 14))
            check(raw[4] & 15 == switch_mask)
            outputs[name] = {'physical_bit': physical_bit, 'Switch_button_byte': raw[4]}

        class FakeProcess:
            def __init__(self, lines, code, release=None):
                self.stdout = iter(lines)
                self.code = code
                self.release = release
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def wait(self):
                if self.release is not None:
                    if not self.release.wait(20):
                        raise TimeoutError('Test did not release fake updater')
                return self.code

        # Identification works without a generated profile and cannot launch flash.
        saved_folder, saved_profile = app.output_folder, app.generated_profile
        app.output_folder = app.generated_profile = None
        app.refresh_actions()
        release_probe = threading.Event()
        try:
            with patch('configure_xiaomi.subprocess.Popen', return_value=FakeProcess(
                    ['Firmware 2.07 ; identifiant materiel recu.\n'], 0, release_probe)) as spawn:
                app.check_connection()
                pump_until(lambda: spawn.call_count == 1)
                check(app.busy and app.operation == 'probe' and not enabled(app.probe_button))
                app.flash()
                app.check_connection()
                check(spawn.call_count == 1)
                app.close()
                check(bool(root.winfo_exists()))
                release_probe.set()
                pump_until(lambda: not app.busy)
                check(app.last_probe_exit_code == 0 and not enabled(app.flash_button))
                command = spawn.call_args.args[0]
                check(command == updater_command('Identifier'))
                check('Flasher' not in command and '-ProfilManifest' not in command)
                check('Firmware 2.07' in app.flash_log.get('1.0', 'end'))
        finally:
            release_probe.set()
        app.output_folder, app.generated_profile = saved_folder, saved_profile
        app.refresh_actions()
        with patch('configure_xiaomi.subprocess.Popen', return_value=FakeProcess(['ECHEC : absent\n'], 1)):
            app.check_connection()
            pump_until(lambda: not app.busy)
            check(app.last_probe_exit_code == 1 and 'impossible' in app.status.get())
            check(app.output_folder == saved_folder and app.generated_profile == saved_profile)
            check(enabled(app.flash_button) and enabled(app.probe_button))

        release = threading.Event()
        fake = FakeProcess(['Progression : 25 %\n', 'Progression : 100 %\n'], 0, release)
        try:
            with patch('configure_xiaomi.subprocess.Popen', return_value=fake) as spawn:
                app.flash()
                pump_until(lambda: float(app.progress['value']) == 100)
                check(app.busy and app.last_flash_exit_code is None)
                check(not enabled(app.flash_button) and not enabled(app.generate_button))
                app.close()
                check(bool(root.winfo_exists()))
                release.set()
                pump_until(lambda: not app.busy)
                check(app.last_flash_exit_code == 0 and 'Flash terminé' in app.status.get())
                check(spawn.call_count == 1)
                args, kwargs = spawn.call_args
                check(args[0] == flash_command(folder / 'manifest.json'))
                check('-Utf8Console' in args[0] and kwargs.get('shell', False) is False)
                check(str(folder / 'manifest.json') in args[0])
        finally:
            release.set()

        # 100% followed by a process error is still an error, never success.
        fake = FakeProcess(['Progression : 100 %\n', 'ECHEC : test simule\n'], 1)
        with patch('configure_xiaomi.subprocess.Popen', return_value=fake):
            app.flash()
            pump_until(lambda: not app.busy)
            check(app.last_flash_exit_code == 1 and 'Échec' in app.status.get())
            check(float(app.progress['value']) == 0)
            check('test simule' in app.flash_log.get('1.0', 'end'))
            check(app.log_visible)
        with patch('configure_xiaomi.subprocess.Popen', side_effect=OSError('processus simule absent')):
            app.flash()
            pump_until(lambda: not app.busy)
            check(app.last_flash_exit_code == 1)
            check('processus simule absent' in app.flash_log.get('1.0', 'end'))
        check(enabled(app.generate_button) and enabled(app.flash_button))

        result = {'status': 'passed', 'assertions': assertions,
                  'letters_bundle': str(folder), 'profile': expected,
                  'physical_to_Switch_USB': outputs,
                  'flash_tests': ['no_automatic_flash', 'stale_profile_blocked', 'exact_manifest',
                                  'progress_stream', '100_percent_waits_for_completion',
                                  'success', 'failure_after_100_percent', 'process_start_error',
                                  'close_blocked_during_flash'],
                  'connection_tests': ['no_firmware_required', 'identification_only', 'busy_blocks_flash',
                                       'success', 'failure_preserves_profile'],
                  'hardware_flashed': False}
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        root.destroy()
        c.BUNDLE_ROOT = old_bundle_root
        temporary.cleanup()


if __name__ == '__main__':
    main()
