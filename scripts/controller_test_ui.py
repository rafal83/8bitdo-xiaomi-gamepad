"""Live inputs and brief motor tests, separate from firmware preparation."""
import time
import tkinter as tk
from tkinter import ttk

from controller_io import enumerate_controllers, open_controller


class ControllerTestPanel:
    def __init__(self, app, frame):
        self.app, self.root = app, app.root
        self.session = None
        self.devices = []
        self.poll_id = self.motor_id = None
        self.samples = 0
        self.last_sample = None
        self.device_name = tk.StringVar()
        self.info = tk.StringVar(value='Branchez l’adaptateur normalement et connectez la Xiaomi, puis actualisez la liste.')
        self.sensor = tk.StringVar(value='Accéléromètre : en attente de données.')
        self.force = tk.DoubleVar(value=25)
        self.output_status = tk.StringVar(value='Impulsion de 0,5 seconde. Le firmware installé doit transmettre les vibrations.')
        connection = app.card(frame, 'Connexion de test · Maintenir 3 s : Back + L → Switch · Back + Haut → XInput')
        connection.configure(padding=12)
        connection.pack(fill='x', pady=(0, 10))
        row = ttk.Frame(connection, style='Card.TFrame'); row.pack(fill='x', pady=(8, 0))
        self.combo = ttk.Combobox(row, textvariable=self.device_name, state='readonly', width=35)
        self.combo.pack(side='left', fill='x', expand=True)
        self.refresh_button = ttk.Button(row, text='Actualiser', command=self.refresh)
        self.refresh_button.pack(side='left', padx=8)
        self.start_button = ttk.Button(row, text='Démarrer', command=self.start)
        self.start_button.pack(side='left')
        self.stop_button = ttk.Button(row, text='Arrêter', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=(8, 0))
        ttk.Label(connection, textvariable=self.info, style='CardMuted.TLabel', wraplength=790).pack(anchor='w', pady=(8, 0))

        inputs = app.card(frame, 'Entrées reçues sur le PC')
        inputs.configure(padding=12)
        inputs.pack(fill='x', pady=(0, 10))
        buttons = ttk.Frame(inputs, style='Card.TFrame'); buttons.pack(fill='x', pady=(10, 6))
        self.button_labels = {}
        names = ('A', 'B', 'X', 'Y', 'L', 'R', 'ZL', 'ZR', 'L3', 'R3',
                 'Moins', 'Plus', 'Home', 'Capture', 'Haut', 'Bas', 'Gauche', 'Droite')
        for index, name in enumerate(names):
            label = tk.Label(buttons, text=name, width=7, padx=3, pady=2,
                             bg='#EEF2F4', fg=app.COLORS['muted'], font=('Segoe UI', 9))
            label.grid(row=index//9, column=index%9, padx=2, pady=2, sticky='ew')
            self.button_labels[name] = label
        sticks = ttk.Frame(inputs, style='Card.TFrame'); sticks.pack(fill='x')
        self.sticks = []
        for title in ('Stick gauche', 'Stick droit'):
            holder = ttk.Frame(sticks, style='Card.TFrame'); holder.pack(side='left', padx=(0, 30))
            ttk.Label(holder, text=title, style='CardMuted.TLabel').pack()
            canvas = tk.Canvas(holder, width=150, height=70, bg='white', highlightthickness=0)
            canvas.pack()
            self.sticks.append(canvas)
        self.trigger_values = tk.StringVar(value='ZL : —    ZR : —')
        ttk.Label(sticks, textvariable=self.trigger_values, style='Card.TLabel').pack(side='left', padx=5)
        ttk.Label(inputs, textvariable=self.sensor, style='CardMuted.TLabel').pack(anchor='w', pady=(4, 0))
        self.draw_sticks(None)

        outputs = app.card(frame, 'Sorties · vibrations')
        outputs.configure(padding=12)
        outputs.pack(fill='x')
        row = ttk.Frame(outputs, style='Card.TFrame'); row.pack(fill='x', pady=(8, 0))
        ttk.Label(row, text='Intensité demandée (%)', style='Card.TLabel').pack(side='left')
        ttk.Spinbox(row, from_=0, to=50, increment=5, textvariable=self.force, width=5).pack(side='left', padx=10)
        self.motor_buttons = []
        for title, motor in (('Petit moteur', 'small'), ('Gros moteur', 'big'), ('Les deux', 'both')):
            button = ttk.Button(row, text=title, command=lambda m=motor: self.pulse(m), state='disabled')
            button.pack(side='left', padx=4); self.motor_buttons.append(button)
        ttk.Label(outputs, textvariable=self.output_status,
                  style='CardMuted.TLabel').pack(anchor='w', pady=(6, 0))

    def draw_sticks(self, axes):
        for index, canvas in enumerate(self.sticks):
            canvas.delete('all')
            canvas.create_oval(48, 5, 102, 59, outline='#CDDADD')
            canvas.create_line(48, 32, 102, 32, fill='#E2E9EC')
            canvas.create_line(75, 5, 75, 59, fill='#E2E9EC')
            if axes is not None:
                x, y = axes[2*index:2*index+2]
                px, py = 75+max(-1, min(1, x))*25, 32+max(-1, min(1, y))*25
                canvas.create_oval(px-5, py-5, px+5, py+5, fill='#087F72', outline='')
                canvas.create_text(75, 64, text=f'X {x:+.2f}   Y {y:+.2f}', fill='#637783', font=('Segoe UI', 8))

    def refresh(self):
        if self.app.busy or self.session is not None:
            return
        try:
            self.devices = enumerate_controllers()
            self.combo.configure(values=[d['label'] for d in self.devices])
            if self.devices:
                self.combo.current(0)
                self.info.set('Choisissez le périphérique à tester et cliquez sur Démarrer.')
            else:
                self.device_name.set('')
                self.info.set('Aucune manette connectée. Allumez la Xiaomi et vérifiez son appairage au 8BitDo.')
        except OSError as error:
            self.info.set(str(error))

    def start(self):
        index = self.combo.current()
        if self.app.busy or self.session is not None:
            return
        if not 0 <= index < len(self.devices):
            self.info.set('Actualisez la liste puis choisissez une manette.')
            return
        try:
            self.session = open_controller(self.devices[index])
            self.samples = 0; self.last_sample = time.monotonic()
            self.refresh_button.configure(state='disabled')
            self.start_button.configure(state='disabled')
            self.combo.configure(state='disabled')
            self.stop_button.configure(state='normal')
            for button in self.motor_buttons:
                button.configure(state='normal')
            self.sensor.set('XInput : Home/Capture et capteurs indisponibles. Back + L (3 s) → HID Switch.'
                            if self.devices[index]['kind'] == 'xinput' else 'Accéléromètre : attente d’un rapport capteur.')
            self.poll()
        except (OSError, ValueError) as error:
            self.stop()
            self.info.set(f'Test impossible : {error}')

    def render(self, sample):
        self.samples += 1; self.last_sample = time.monotonic()
        for name, label in self.button_labels.items():
            active = name in sample['buttons']
            label.configure(bg='#087F72' if active else '#EEF2F4', fg='white' if active else '#637783')
        self.draw_sticks(sample['axes'])
        self.trigger_values.set('ZL : {:3.0%}    ZR : {:3.0%}'.format(*sample['triggers']))
        if sample['accel'] is not None:
            x, y, z = sample['accel']
            self.sensor.set(f'Accéléromètre brut   X {x:+6d}   Y {y:+6d}   Z {z:+6d}')
        self.info.set(f'Lecture active · {self.samples} rapports reçus. Les boutons affichés sont ceux reçus par le PC.')

    def poll(self):
        self.poll_id = None
        if self.session is None:
            return
        try:
            sample = self.session.read()
            if sample is not None:
                self.render(sample)
            elif time.monotonic() - self.last_sample > 3:
                self.info.set('Aucun rapport récent. Vérifiez la connexion et le mode de l’adaptateur.')
        except (OSError, ValueError) as error:
            self.stop()
            self.info.set(f'Test interrompu : {error}')
            return
        self.poll_id = self.root.after(33, self.poll)

    def pulse(self, motor):
        if self.app.busy or self.session is None:
            return
        try:
            level = float(self.force.get())
            if not 0 <= level <= 50:
                raise ValueError('Choisissez une intensité entre 0 et 50 %.')
            if self.motor_id is not None:
                self.root.after_cancel(self.motor_id)
            self.session.rumble(level/100 if motor in ('small', 'both') else 0,
                                level/100 if motor in ('big', 'both') else 0)
            self.output_status.set('Commande envoyée · arrêt automatique des moteurs dans 0,5 seconde.')
            self.motor_id = self.root.after(500, self.stop_rumble)
        except (OSError, ValueError, tk.TclError) as error:
            self.stop()
            self.info.set(f'Test moteur interrompu : {error}')

    def stop_rumble(self):
        self.motor_id = None
        if self.session is not None:
            try:
                self.session.rumble(0, 0)
                self.output_status.set('Commande d’arrêt envoyée aux moteurs.')
            except OSError as error:
                self.stop()
                self.info.set(f'Arrêt moteur non confirmé : {error}')

    def stop(self):
        for timer in (self.poll_id, self.motor_id):
            if timer is not None:
                self.root.after_cancel(timer)
        self.poll_id = self.motor_id = None
        session, self.session = self.session, None
        error = None
        if session is not None:
            try:
                session.close()
            except OSError as exception:
                error = exception
            self.output_status.set(f'Arrêt non confirmé : {error}' if error else 'Test arrêté · commande d’arrêt envoyée aux moteurs.')
        self.refresh_button.configure(state='normal')
        self.start_button.configure(state='normal')
        self.combo.configure(state='readonly')
        self.stop_button.configure(state='disabled')
        for button in self.motor_buttons:
            button.configure(state='disabled')
        self.info.set(f'Arrêt moteur non confirmé : {error}' if error else 'Test arrêté.')
