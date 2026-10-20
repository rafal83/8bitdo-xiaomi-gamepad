"""Native Xiaomi profile editor; flashing requires an explicit button click."""
import os
import math
from pathlib import Path
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from custom_firmware import (ROOT, AXES, TARGET_BITS, DEFAULT_PROFILE, generate_bundle,
                             load_profile, mapping_summary, validate_profile, write_json, triggers_remapped)


def flash_command(manifest):
    return updater_command('Flasher') + ['-Variante', 'Profil', '-ProfilManifest', str(manifest)]


def updater_command(action):
    shell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return [str(shell), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(ROOT / 'scripts/flash_8bitdo.ps1'), '-Action', action, '-Utf8Console']


class Configurator:
    COLORS = {'bg': '#F3F6F7', 'card': '#FFFFFF', 'ink': '#172C36',
              'muted': '#637783', 'line': '#DBE4E8', 'accent': '#087F72',
              'soft': '#E4F4EF', 'nav': '#172C36', 'nav_text': '#C2D1D9'}
    PAGE_TITLES = [('Boutons', 'Choisissez une disposition ou cliquez sur un bouton de la manette.'),
                   ('Sticks', 'Adaptez le sens des axes à votre façon de jouer.'),
                   ('Options', 'Réglez le bouton Mi, la batterie et la reconnexion.'),
                   ('Installer le profil', 'Préparez votre adaptateur, puis lancez le transfert.')]
    BUTTON_NAMES = {'Back': 'Back / Select', 'Start': 'Start', 'L3': 'Clic du stick gauche',
                    'R3': 'Clic du stick droit', 'Mi': 'Bouton central Mi',
                    'ZL': 'Gâchette gauche', 'ZR': 'Gâchette droite'}
    PHOTO_CONTROLS = {'L': (97, 8, 34, 8), 'R': (399, 8, 34, 8),
                      'L3': (90, 101, 36, 36), 'R3': (330, 192, 40, 40),
                      'Back': (194, 75, 19, 13), 'Start': (306, 75, 20, 13),
                      'Mi': (249, 102, 24, 24), 'Y': (407, 65, 20, 20),
                      'X': (369, 102, 21, 21), 'B': (446, 102, 21, 21),
                      'A': (407, 140, 21, 21)}

    def __init__(self, root):
        self.root = root
        self.busy = False
        self.operation = None
        self.disabled_widgets = []
        self.output_folder = None
        self.generated_profile = None
        self.last_flash_exit_code = None
        self.last_probe_exit_code = None
        self.events = queue.Queue()
        self.setup_style()
        root.title('Xiaomi · Configurateur 8BitDo')
        width = min(1180, root.winfo_screenwidth() - 50)
        height = min(850, root.winfo_screenheight() - 80)
        root.geometry(f'{width}x{height}')
        root.minsize(min(980, width), min(700, height))
        root.configure(background=self.COLORS['bg'])
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)
        self.name = tk.StringVar()
        self.buttons = {name: tk.StringVar() for name in DEFAULT_PROFILE['buttons']}
        self.axes = {name: tk.BooleanVar() for name in AXES}
        self.swap = tk.BooleanVar()
        self.pulse = tk.BooleanVar()
        self.duration = tk.StringVar()
        self.battery = tk.BooleanVar()
        self.reconnect = tk.BooleanVar()
        self.mapping_text = tk.StringVar()
        self.selected_button = tk.StringVar(value='A')
        self.selected_title = tk.StringVar()
        self.selected_description = tk.StringVar()
        self.mapping_mode = tk.StringVar()
        self.page_title = tk.StringVar()
        self.page_subtitle = tk.StringVar()
        self.phase = tk.StringVar(value='À préparer')
        self.flash_info = tk.StringVar(value='Votre profil apparaîtra ici après sa préparation.')
        self.status = tk.StringVar(value='Choisissez vos réglages, puis préparez le firmware.')
        self.sidebar_summary = tk.StringVar()
        self.ready_text = tk.StringVar(value='Préparez le firmware avec le bouton en bas de la fenêtre.')
        self.log_visible = False
        self.controller_photo = None
        self.controller_render = None
        self.controller_render_size = None
        self.controller_hitboxes = {}
        self.load_controller_photo()

        self.build_sidebar()
        header = ttk.Frame(root, padding=(24, 20, 24, 8))
        header.grid(row=0, column=1, sticky='ew')
        header.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(header)
        toolbar.grid(row=0, column=0, sticky='ew', pady=(0, 18))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text='MON PROFIL', style='Small.TLabel').grid(row=0, column=0, padx=(0, 12))
        ttk.Entry(toolbar, textvariable=self.name, width=24).grid(row=0, column=1, sticky='ew')
        ttk.Button(toolbar, text='Ouvrir', command=self.open_profile).grid(row=0, column=2, padx=(10, 6))
        ttk.Button(toolbar, text='Enregistrer', command=self.save_profile).grid(row=0, column=3)
        ttk.Label(header, textvariable=self.page_title, style='Title.TLabel').grid(row=1, column=0, sticky='w')
        ttk.Label(header, textvariable=self.page_subtitle, style='Muted.TLabel', wraplength=690).grid(row=2, column=0, sticky='w', pady=(5, 0))

        self.notebook = ttk.Notebook(root, style='Workspace.TNotebook')
        self.notebook.grid(row=1, column=1, sticky='nsew', padx=8)
        self.canvases = []
        self.pages = [self.add_page(title) for title, _ in self.PAGE_TITLES]
        self.build_buttons_tab(self.pages[0])
        self.build_sticks_tab(self.pages[1])
        self.build_options_tab(self.pages[2])
        self.build_flash_tab(self.pages[3])
        self.notebook.bind('<<NotebookTabChanged>>', self.on_page_changed)
        self.build_footer()
        self.selected_button.trace_add('write', self.on_button_selected)
        self.set_profile(DEFAULT_PROFILE)
        variables = [self.name, *self.buttons.values(), *self.axes.values(), self.swap,
                     self.pulse, self.duration, self.battery, self.reconnect]
        for variable in variables:
            variable.trace_add('write', self.on_profile_changed)
        self.on_button_selected()
        self.on_profile_changed()
        self.show_page(0)
        root.bind('<MouseWheel>', self.scroll_page, add='+')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll_events)

    def setup_style(self):
        c = self.COLORS
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), background=c['bg'], foreground=c['ink'])
        style.configure('TFrame', background=c['bg'])
        style.configure('TLabel', background=c['bg'])
        style.configure('Muted.TLabel', foreground=c['muted'])
        style.configure('Small.TLabel', foreground=c['muted'], font=('Segoe UI', 9))
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'))
        style.configure('Card.TFrame', background=c['card'])
        style.configure('Card.TLabel', background=c['card'])
        style.configure('CardTitle.TLabel', background=c['card'], font=('Segoe UI', 12, 'bold'))
        style.configure('CardMuted.TLabel', background=c['card'], foreground=c['muted'])
        style.configure('Selected.TLabel', background=c['soft'], foreground=c['accent'], font=('Segoe UI', 23, 'bold'), padding=12)
        style.configure('TButton', padding=(12, 8), background=c['card'], borderwidth=1,
                        bordercolor=c['line'], focusthickness=2, focuscolor=c['accent'])
        style.map('TButton', background=[('active', '#EAF0F2'), ('disabled', '#EDF1F2')],
                  foreground=[('disabled', '#8B9BA4')])
        style.configure('Accent.TButton', background=c['accent'], foreground='white', bordercolor=c['accent'])
        style.map('Accent.TButton', background=[('disabled', '#DCE5E7'), ('active', '#066B61')],
                  foreground=[('disabled', '#81949C'), ('!disabled', 'white')])
        style.configure('Preset.TButton', anchor='w', padding=(16, 12))
        style.configure('Chosen.TButton', anchor='w', padding=(16, 12), background=c['soft'],
                        foreground=c['accent'], bordercolor=c['accent'])
        style.map('Chosen.TButton', background=[('active', '#D6EDE5')])
        style.configure('Nav.TButton', anchor='w', background=c['nav'], foreground=c['nav_text'],
                        borderwidth=0, padding=(16, 13), focusthickness=0)
        style.map('Nav.TButton', background=[('active', '#263F4A')], foreground=[('active', 'white')])
        style.configure('ActiveNav.TButton', anchor='w', background='#284A52', foreground='white',
                        borderwidth=0, padding=(16, 13), focusthickness=0)
        style.map('ActiveNav.TButton', background=[('active', '#315A61')])
        style.configure('TEntry', padding=7, fieldbackground=c['card'], bordercolor=c['line'])
        style.configure('TCombobox', padding=7, fieldbackground=c['card'], bordercolor=c['line'], arrowsize=14)
        style.map('TCombobox', fieldbackground=[('readonly', c['card'])], selectbackground=[('readonly', c['card'])],
                  selectforeground=[('readonly', c['ink'])])
        style.configure('TSpinbox', padding=6, fieldbackground=c['card'])
        style.configure('Card.TCheckbutton', background=c['card'], padding=(0, 4))
        style.map('Card.TCheckbutton', background=[('active', c['card'])])
        self.toggle_images = []
        for selected, disabled in ((False, False), (True, False), (False, True), (True, True)):
            image = tk.PhotoImage(master=self.root, width=46, height=22)
            track = '#C8D4D9' if not selected else c['accent']
            if disabled:
                track = '#E2E8EB'
            for y in range(20):
                inset = math.ceil(10 - math.sqrt(max(0, 100 - (y - 9.5)**2)))
                image.put(track, to=(inset, y+1, 34-inset, y+2))
            center = 24 if selected else 10
            for y in range(3, 17):
                half = math.floor(math.sqrt(max(0, 49 - (y - 9.5)**2)))
                image.put('white', to=(center-half, y+1, center+half+1, y+2))
            self.toggle_images.append(image)
        off, on, disabled_off, disabled_on = self.toggle_images
        style.element_create('Switch.indicator', 'image', off, ('disabled', 'selected', disabled_on),
                             ('disabled', disabled_off), ('selected', on))
        style.layout('Card.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
            ('Switch.indicator', {'side': 'left'}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
        style.configure('Horizontal.TProgressbar', troughcolor='#DCE6E8', background=c['accent'],
                        bordercolor=c['bg'], lightcolor=c['accent'], darkcolor=c['accent'], borderwidth=0, thickness=5)
        style.configure('Workspace.TNotebook', borderwidth=0, background=c['bg'], lightcolor=c['bg'], darkcolor=c['bg'], bordercolor=c['bg'])
        style.layout('Workspace.TNotebook.Tab', [])

    def build_sidebar(self):
        c = self.COLORS
        sidebar = tk.Frame(self.root, background=c['nav'], width=196)
        sidebar.grid(row=0, column=0, rowspan=3, sticky='ns')
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(4, weight=1)
        brand = tk.Frame(sidebar, background=c['nav'])
        brand.grid(row=0, column=0, sticky='ew', padx=20, pady=(28, 25))
        tk.Label(brand, text='mi', font=('Segoe UI', 24, 'bold'), foreground='#7EE0C1', background=c['nav']).pack(anchor='w')
        tk.Label(brand, text='GAMEPAD STUDIO', font=('Segoe UI', 10, 'bold'), foreground='white', background=c['nav']).pack(anchor='w', pady=(3, 0))
        tk.Label(brand, text='Xiaomi → Nintendo Switch', font=('Segoe UI', 9), foreground=c['nav_text'], background=c['nav']).pack(anchor='w', pady=5)
        tk.Label(sidebar, text='PERSONNALISER', font=('Segoe UI', 8, 'bold'), foreground='#87A6B2', background=c['nav']).grid(row=1, column=0, sticky='w', padx=25, pady=(0, 8))
        navigation = tk.Frame(sidebar, background=c['nav'])
        navigation.grid(row=2, column=0, sticky='ew', padx=10)
        self.nav_buttons = []
        for index, title in enumerate(('01   Boutons', '02   Sticks', '03   Options', '04   Installer')):
            button = ttk.Button(navigation, text=title, style='Nav.TButton', command=lambda i=index: self.show_page(i))
            button.pack(fill='x', pady=3)
            self.nav_buttons.append(button)
        info = tk.Frame(sidebar, background='#213C47', padx=14, pady=14)
        info.grid(row=5, column=0, sticky='ew', padx=14, pady=18)
        tk.Label(info, text='VOTRE CONFIGURATION', foreground='#8DADB8', background='#213C47', font=('Segoe UI', 8, 'bold')).pack(anchor='w')
        tk.Label(info, textvariable=self.sidebar_summary, foreground='white', background='#213C47',
                 font=('Segoe UI', 9), wraplength=150, justify='left').pack(anchor='w', pady=(10, 0))
        tk.Label(sidebar, text='8BitDo USB v1 · FW 2.07', foreground='#87A6B2', background=c['nav'], font=('Segoe UI', 8)).grid(row=6, column=0, pady=(0, 20))

    def build_footer(self):
        footer = ttk.Frame(self.root, padding=(24, 10, 24, 18))
        footer.grid(row=2, column=1, sticky='ew')
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.phase, foreground=self.COLORS['accent'], font=('Segoe UI', 10, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(footer, textvariable=self.status, style='Small.TLabel', wraplength=700).grid(row=1, column=0, columnspan=3, sticky='w', pady=(4, 10))
        self.progress = ttk.Progressbar(footer, mode='determinate', value=0)
        self.progress.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(0, 14))
        self.open_button = ttk.Button(footer, text='Fichiers du profil', command=self.open_output, state='disabled')
        self.open_button.grid(row=3, column=0, sticky='w')
        self.generate_button = ttk.Button(footer, text='1. Préparer le firmware', command=self.generate)
        self.generate_button.grid(row=3, column=1, padx=(10, 8))
        self.flash_button = ttk.Button(footer, text='2. Flasher ce profil', style='Accent.TButton', command=self.flash, state='disabled')
        self.flash_button.grid(row=3, column=2)

    def add_page(self, title):
        page = ttk.Frame(self.notebook)
        page.rowconfigure(0, weight=1)
        page.columnconfigure(0, weight=1)
        canvas = tk.Canvas(page, highlightthickness=0, borderwidth=0, background=self.COLORS['bg'], height=360)
        scroll = ttk.Scrollbar(page, orient='vertical', command=canvas.yview)
        def update_scroll(first, last):
            scroll.set(first, last)
            needed = float(first) > 0 or float(last) < 1
            if needed and not scroll.grid_info():
                scroll.grid(row=0, column=1, sticky='ns')
            elif not needed and scroll.grid_info():
                scroll.grid_remove()
        canvas.configure(yscrollcommand=update_scroll)
        canvas.grid(row=0, column=0, sticky='nsew')
        content = ttk.Frame(canvas, padding=(16, 8, 8, 8))
        window = canvas.create_window(0, 0, anchor='nw', window=content)
        content.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        self.canvases.append(canvas)
        self.notebook.add(page, text=title)
        return content

    def show_page(self, index):
        self.notebook.select(index)
        self.on_page_changed()

    def on_page_changed(self, event=None):
        index = self.notebook.index('current')
        title, subtitle = self.PAGE_TITLES[index]
        self.page_title.set(title)
        self.page_subtitle.set(subtitle)
        for i, button in enumerate(self.nav_buttons):
            button.configure(style='ActiveNav.TButton' if i == index else 'Nav.TButton')

    def scroll_page(self, event):
        if isinstance(event.widget, (ttk.Combobox, ttk.Spinbox, tk.Text)):
            return
        for canvas in self.canvases:
            if (canvas.winfo_ismapped() and canvas.winfo_rootx() <= event.x_root < canvas.winfo_rootx()+canvas.winfo_width()
                    and canvas.winfo_rooty() <= event.y_root < canvas.winfo_rooty()+canvas.winfo_height()):
                if canvas.bbox('all') and canvas.bbox('all')[3] > canvas.winfo_height():
                    canvas.yview_scroll(-int(event.delta / 120), 'units')
                return 'break'

    def card(self, parent, title, subtitle=None):
        card = ttk.Frame(parent, style='Card.TFrame', padding=18)
        ttk.Label(card, text=title, style='CardTitle.TLabel').pack(anchor='w')
        if subtitle:
            ttk.Label(card, text=subtitle, style='CardMuted.TLabel', wraplength=620, justify='left').pack(anchor='w', pady=(6, 12))
        return card

    def build_buttons_tab(self, frame):
        frame.columnconfigure(0, weight=1)
        presets = ttk.Frame(frame)
        presets.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        presets.columnconfigure((0, 1), weight=1, uniform='presets')
        self.position_preset = ttk.Button(presets, text='Positions Nintendo\nA → B  ·  B → A  ·  X → Y  ·  Y → X', style='Preset.TButton', command=lambda: self.preset(False))
        self.position_preset.grid(row=0, column=0, sticky='ew', padx=(0, 6))
        self.letters_preset = ttk.Button(presets, text='Respecter les lettres Xiaomi\nA → A  ·  B → B  ·  X → X  ·  Y → Y', style='Preset.TButton', command=lambda: self.preset(True))
        self.letters_preset.grid(row=0, column=1, sticky='ew', padx=(6, 0))
        layout = ttk.Frame(frame)
        layout.grid(row=1, column=0, sticky='ew')
        layout.columnconfigure(0, weight=3)
        layout.columnconfigure(1, weight=2, minsize=232)
        preview = self.card(layout, 'Votre manette Xiaomi')
        preview.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        self.controller = tk.Canvas(preview, background=self.COLORS['card'], height=225, width=420, highlightthickness=0)
        self.controller.pack(fill='both', expand=True)
        self.controller.bind('<Configure>', self.draw_controller)
        self.controller.bind('<Button-1>', self.on_controller_click)
        self.controller.bind('<Motion>', self.on_controller_motion)
        self.controller.bind('<Leave>', lambda e: self.controller.configure(cursor=''))
        ttk.Label(preview, text='Cliquez sur un bouton ou une gâchette pour le remapper.', style='CardMuted.TLabel', wraplength=360).pack(anchor='w', pady=(0, 6))
        editor = self.card(layout, 'Remapper un bouton', 'Bouton Xiaomi → commande Switch')
        editor.grid(row=0, column=1, sticky='nsew')
        ttk.Label(editor, text='Bouton sur la manette', style='CardMuted.TLabel').pack(anchor='w', pady=(6, 5))
        ttk.Combobox(editor, textvariable=self.selected_button, values=list(self.buttons), state='readonly', width=17).pack(fill='x')
        ttk.Label(editor, textvariable=self.selected_description, style='CardMuted.TLabel').pack(anchor='w', pady=(5, 0))
        ttk.Label(editor, text='Commande sur la Switch', style='CardMuted.TLabel').pack(anchor='w', pady=(12, 5))
        self.target_combo = ttk.Combobox(editor, values=list(TARGET_BITS), state='readonly', width=17)
        self.target_combo.pack(fill='x')
        ttk.Label(editor, textvariable=self.selected_title, style='Selected.TLabel', anchor='center',
                  font=('Segoe UI', 20, 'bold'), padding=8, wraplength=190, justify='center').pack(fill='x', pady=(8, 0))
        ttk.Button(editor, text='Rétablir ce bouton', command=self.reset_selected_button).pack(fill='x', pady=(8, 0))
        ttk.Label(frame, textvariable=self.mapping_mode, style='Muted.TLabel').grid(row=2, column=0, sticky='w', pady=(12, 3))
        ttk.Label(frame, text='Le remappage de la Switch s’ajoute à ces réglages : remettez-le à zéro pour les tester.',
                  style='Small.TLabel', wraplength=700).grid(row=3, column=0, sticky='w')

    def select_button(self, name):
        if not self.busy:
            self.selected_button.set(name)

    def on_button_selected(self, *args):
        name = self.selected_button.get()
        self.target_combo.configure(textvariable=self.buttons[name])
        self.selected_title.set(f'{name} → {self.buttons[name].get()}')
        self.selected_description.set(self.BUTTON_NAMES.get(name, f'Bouton {name}'))
        self.draw_controller()

    def reset_selected_button(self):
        if not self.busy:
            name = self.selected_button.get()
            self.buttons[name].set(DEFAULT_PROFILE['buttons'][name])

    def load_controller_photo(self):
        path = ROOT / 'assets/mi-gamepad.png'
        if path.is_file():
            try:
                from PIL import Image
                with Image.open(path) as image:
                    self.controller_photo = image.copy()
            except (ImportError, OSError, ValueError):
                # Keep the controls usable if the image cannot be loaded.
                self.controller_photo = None

    def controller_button_at(self, x, y):
        for name, (cx, cy, rx, ry) in self.controller_hitboxes.items():
            if ((x-cx) / rx)**2 + ((y-cy) / ry)**2 <= 1:
                return name
        return None

    def on_controller_click(self, event):
        name = self.controller_button_at(event.x, event.y)
        if name is not None:
            self.select_button(name)

    def on_controller_motion(self, event):
        name = self.controller_button_at(event.x, event.y)
        self.controller.configure(cursor='hand2' if name is not None and not self.busy else '')

    def draw_controller_photo(self, width, height):
        from PIL import Image, ImageTk
        original = self.controller_photo
        scale = min(max(1, width-16) / original.width, max(1, height-48) / original.height)
        size = (max(1, round(original.width*scale)), max(1, round(original.height*scale)))
        if size != self.controller_render_size:
            self.controller_render = ImageTk.PhotoImage(original.resize(size, Image.Resampling.LANCZOS), master=self.root)
            self.controller_render_size = size
        ox, oy = (width-size[0])/2, 36+(height-44-size[1])/2
        self.controller.create_image(ox, oy, image=self.controller_render, anchor='nw', tags=('controller_photo',))
        sx, sy = size[0]/500, size[1]/332
        for name, (x, y, rx, ry) in self.PHOTO_CONTROLS.items():
            cx, cy, ax, ay = ox+x*sx, oy+y*sy, rx*sx, ry*sy
            self.controller_hitboxes[name] = (cx, cy, max(8, ax), max(8, ay))
            chosen = name == self.selected_button.get()
            self.controller.create_oval(cx-ax, cy-ay, cx+ax, cy+ay,
                outline='#27DBAD' if chosen else '', width=3, tags=('button_' + name,))
            if name in ('L', 'R'):
                self.controller.create_text(cx, cy, text=name, fill='white', font=('Segoe UI', 8, 'bold'))
        # The rear triggers are not visible in a front photo: use named callouts.
        for name, x in (('ZL', 97), ('ZR', 399)):
            cx, cy = ox+x*sx, 15
            self.controller_hitboxes[name] = (cx, cy, 26, 12)
            chosen = name == self.selected_button.get()
            self.controller.create_line(cx, cy+12, cx, oy, fill=self.COLORS['line'])
            self.controller.create_rectangle(cx-26, cy-12, cx+26, cy+12,
                fill=self.COLORS['soft'] if chosen else self.COLORS['card'],
                outline=self.COLORS['accent'] if chosen else self.COLORS['line'],
                width=2 if chosen else 1, tags=('button_' + name,))
            self.controller.create_text(cx, cy, text=name, font=('Segoe UI', 10, 'bold'),
                fill=self.COLORS['accent'], tags=('button_' + name,))

    def draw_controller(self, event=None):
        canvas = self.controller
        canvas.delete('all')
        self.controller_hitboxes = {}
        width, height = max(canvas.winfo_width(), 100), max(canvas.winfo_height(), 100)
        if self.controller_photo is not None:
            self.draw_controller_photo(width, height)
            return
        scale = min(width / 640, height / 300)
        ox, oy = (width - 640 * scale) / 2, (height - 300 * scale) / 2
        def coords(values):
            return [v * scale + (ox if i % 2 == 0 else oy) for i, v in enumerate(values)]
        def oval(values, **kw):
            return canvas.create_oval(*coords(values), **kw)
        def text(x, y, value, size=13, **kw):
            return canvas.create_text(*coords((x, y)), text=value, font=('Segoe UI', max(8, round(size * scale)), 'bold'), **kw)
        body = [140, 65, 500, 65, 550, 100, 588, 243, 570, 280, 524, 275,
                468, 214, 172, 214, 116, 275, 70, 280, 52, 243, 90, 100]
        canvas.create_polygon(*coords(body), smooth=True, splinesteps=24, fill='#E2E9EC', outline='#CAD7DC', width=2)
        # Decorative D-pad and stick wells; only supported remappable controls react.
        canvas.create_polygon(*coords([153, 162, 175, 162, 175, 181, 194, 181, 194, 203,
                                      175, 203, 175, 222, 153, 222, 153, 203, 134, 203, 134, 181, 153, 181]), fill='#69818D')
        selected = self.selected_button.get()
        controls = {'L': (145, 57, 34, 13), 'R': (495, 57, 34, 13),
                    'ZL': (145, 21, 25, 12), 'ZR': (495, 21, 25, 12),
                    'L3': (193, 117, 27, 27), 'R3': (369, 199, 27, 27),
                    'Back': (276, 132, 26, 13), 'Start': (350, 132, 26, 13),
                    'Mi': (313, 85, 18, 18), 'Y': (473, 106, 18, 18),
                    'X': (435, 144, 18, 18), 'B': (511, 144, 18, 18), 'A': (473, 182, 18, 18)}
        for name, (x, y, rx, ry) in controls.items():
            tag = 'button_' + name
            chosen = name == selected
            cx, cy = coords((x, y))
            self.controller_hitboxes[name] = (cx, cy, rx*scale, ry*scale)
            oval((x-rx-5, y-ry-5, x+rx+5, y+ry+5), fill=self.COLORS['soft'] if chosen else '#E2E9EC',
                 outline=self.COLORS['accent'] if chosen else '#E2E9EC', width=2, tags=(tag,))
            oval((x-rx, y-ry, x+rx, y+ry), fill=self.COLORS['accent'] if chosen else '#344F5B', outline='', tags=(tag,))
            text(x, y, name, size=11 if len(name) > 1 else 16, fill='white', tags=(tag,))
        text(320, 253, 'XIAOMI MI GAMEPAD', size=10, fill='#758C96')

    def build_sticks_tab(self, frame):
        swap = self.card(frame, 'Échanger les sticks', 'Le stick gauche contrôle le droit, et inversement.')
        swap.pack(fill='x', pady=(0, 14))
        ttk.Checkbutton(swap, text='Échanger gauche et droit', variable=self.swap, style='Card.TCheckbutton').pack(anchor='w')
        columns = ttk.Frame(frame)
        columns.pack(fill='x')
        columns.columnconfigure((0, 1), weight=1, uniform='sticks')
        self.stick_summaries = {}
        for index, (prefix, title) in enumerate((('L', 'Stick gauche'), ('R', 'Stick droit'))):
            card = self.card(columns, title)
            card.grid(row=0, column=index, sticky='nsew', padx=(0, 7) if index == 0 else (7, 0))
            ttk.Label(card, text='↔   Horizontal', style='CardMuted.TLabel').pack(anchor='w', pady=(14, 5))
            ttk.Checkbutton(card, text='Inverser gauche / droite', variable=self.axes[prefix+'X'], style='Card.TCheckbutton').pack(anchor='w')
            ttk.Label(card, text='↕   Vertical', style='CardMuted.TLabel').pack(anchor='w', pady=(14, 5))
            ttk.Checkbutton(card, text='Inverser haut / bas', variable=self.axes[prefix+'Y'], style='Card.TCheckbutton').pack(anchor='w')
            summary = self.stick_summaries[prefix] = tk.StringVar()
            ttk.Label(card, textvariable=summary, style='CardMuted.TLabel', wraplength=275).pack(anchor='w', pady=(18, 0))
        ttk.Label(frame, text='Les inversions concernent les sticks envoyés à la console, après leur échange éventuel.',
                  style='Small.TLabel', wraplength=690).pack(anchor='w', pady=16)

    def build_options_tab(self, frame):
        mi = self.card(frame, 'Bouton central Mi',
                       'La commande du bouton Mi reste modifiable dans la page Boutons.')
        mi.pack(fill='x', pady=(0, 12))
        ttk.Checkbutton(mi, text='Rendre l’appui Mi détectable par la Switch', variable=self.pulse, style='Card.TCheckbutton').pack(anchor='w')
        duration = ttk.Frame(mi, style='Card.TFrame')
        duration.pack(anchor='w', pady=(8, 0))
        ttk.Label(duration, text='Durée de l’appui', style='CardMuted.TLabel').pack(side='left', padx=(0, 12))
        self.duration_input = ttk.Spinbox(duration, from_=40, to=1000, increment=10, textvariable=self.duration, width=6)
        self.duration_input.pack(side='left')
        ttk.Label(duration, text='ms · conseillé : 120', style='CardMuted.TLabel').pack(side='left', padx=10)
        battery = self.card(frame, 'Niveau de batterie', 'Affichage par paliers. L’évolution pendant la décharge reste à vérifier.')
        battery.pack(fill='x', pady=(0, 12))
        ttk.Checkbutton(battery, text='Afficher la batterie de la manette sur Switch', variable=self.battery, style='Card.TCheckbutton').pack(anchor='w')
        reconnect = self.card(frame, 'Retrouver sa manette')
        reconnect.pack(fill='x')
        ttk.Checkbutton(reconnect, text='Activer la correction de reconnexion Xiaomi', variable=self.reconnect, style='Card.TCheckbutton').pack(anchor='w', pady=(8, 0))
        ttk.Label(reconnect, text='Pour se reconnecter après avoir éteint puis rallumé la manette.', style='CardMuted.TLabel', wraplength=620).pack(anchor='w', pady=(6, 0))

    def preset(self, letters):
        # Face-button presets preserve custom Mi, shoulder and menu mappings.
        if self.busy:
            return
        for name in ('A', 'B', 'X', 'Y'):
            self.buttons[name].set(name if letters else DEFAULT_PROFILE['buttons'][name])
        if self.name.get() in (DEFAULT_PROFILE['name'], 'Xiaomi - lettres identiques'):
            self.name.set('Xiaomi - lettres identiques' if letters else DEFAULT_PROFILE['name'])

    def build_flash_tab(self, frame):
        profile = self.card(frame, 'Votre profil à installer')
        profile.pack(fill='x', pady=(0, 12))
        ttk.Label(profile, textvariable=self.ready_text, style='Card.TLabel', wraplength=650).pack(anchor='w', pady=(8, 0))
        steps = self.card(frame, 'Brancher l’adaptateur')
        steps.pack(fill='x', pady=(0, 12))
        for number, title, detail in [
            ('1', 'Fermez l’utilitaire officiel 8BitDo', 'Débranchez aussi les autres adaptateurs 8BitDo.'),
            ('2', 'Maintenez le bouton de l’adaptateur', 'Tout en le maintenant, branchez l’adaptateur sur le PC.'),
            ('3', 'Cliquez sur « Flasher ce profil »', 'Attendez la fin du transfert avant de débrancher.')]:
            row = ttk.Frame(steps, style='Card.TFrame')
            row.pack(fill='x', pady=7)
            ttk.Label(row, text=number, style='Selected.TLabel', font=('Segoe UI', 11, 'bold'), padding=(11, 5)).pack(side='left', padx=(0, 12))
            description = ttk.Frame(row, style='Card.TFrame')
            description.pack(side='left', fill='x')
            ttk.Label(description, text=title, style='Card.TLabel', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
            ttk.Label(description, text=detail, style='CardMuted.TLabel', wraplength=560).pack(anchor='w', pady=(2, 0))
        self.probe_button = ttk.Button(steps, text='Vérifier la connexion', command=self.check_connection)
        self.probe_button.pack(anchor='w', pady=(8, 0))
        ttk.Label(steps, text='Lit la version de l’adaptateur sans envoyer de firmware.',
                  style='CardMuted.TLabel').pack(anchor='w', pady=(5, 0))
        self.log_toggle = ttk.Button(frame, text='Afficher le journal technique', command=self.toggle_log)
        self.log_toggle.pack(anchor='w', pady=(0, 8))
        self.log_container = ttk.Frame(frame)
        self.flash_log = ScrolledText(self.log_container, height=9, wrap='word', state='disabled',
                                      font=('Consolas', 9), background='#172C36', foreground='#D4E8ED',
                                      relief='flat', padx=12, pady=12)
        self.flash_log.pack(fill='both', expand=True)

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_container.pack(fill='both', expand=True)
        else:
            self.log_container.pack_forget()
        self.log_toggle.configure(text='Masquer le journal technique' if self.log_visible else 'Afficher le journal technique')

    def current_matches_output(self):
        try:
            return self.output_folder is not None and self.get_profile() == self.generated_profile
        except ValueError:
            return False

    def refresh_actions(self):
        ready = not self.busy and self.current_matches_output()
        self.generate_button.configure(state='disabled' if self.busy else 'normal')
        self.generate_button.configure(style='TButton' if ready else 'Accent.TButton')
        self.open_button.configure(state='normal' if ready else 'disabled')
        self.flash_button.configure(state='normal' if ready else 'disabled')
        if self.busy:
            self.phase.set({'flash': 'Installation en cours', 'probe': 'Identification en cours'}.get(
                self.operation, 'Préparation en cours'))
        else:
            self.phase.set('Prêt à installer' if ready else 'Réglages à préparer')
            self.duration_input.configure(state='normal' if self.pulse.get() else 'disabled')

    def on_profile_changed(self, *args):
        self.mapping_text.set(mapping_summary({'buttons': {k: v.get() for k, v in self.buttons.items()}}))
        letters = all(self.buttons[k].get() == k for k in ('A', 'B', 'X', 'Y'))
        positions = all(self.buttons[k].get() == DEFAULT_PROFILE['buttons'][k] for k in ('A', 'B', 'X', 'Y'))
        self.position_preset.configure(style='Chosen.TButton' if positions else 'Preset.TButton')
        self.letters_preset.configure(style='Chosen.TButton' if letters else 'Preset.TButton')
        self.mapping_mode.set('Disposition : ' + ('lettres Xiaomi' if letters else 'positions Nintendo' if positions else 'personnalisée'))
        self.sidebar_summary.set(
            f'A → {self.buttons["A"].get()}   B → {self.buttons["B"].get()}\n'
            f'X → {self.buttons["X"].get()}   Y → {self.buttons["Y"].get()}\n\n'
            f'ZL → {self.buttons["ZL"].get()}   ZR → {self.buttons["ZR"].get()}\n'
            f'Mi → {self.buttons["Mi"].get()}\n'
            + ('Sticks échangés' if self.swap.get() else 'Sticks à leur place'))
        for prefix, summary in self.stick_summaries.items():
            changed = [label for axis, label in [('X', 'horizontal'), ('Y', 'vertical')] if self.axes[prefix+axis].get()]
            summary.set('Axes inversés : ' + ', '.join(changed) if changed else 'Sens habituel sur les deux axes')
        self.on_button_selected()
        self.refresh_actions()
        self.ready_text.set('Préparez le firmware avec le bouton en bas de la fenêtre.')
        if not self.busy and self.output_folder is not None:
            if self.current_matches_output():
                self.status.set('Votre firmware est vérifié. Branchez l’adaptateur pour installer ce profil.')
                self.flash_info.set(f'Profil : {self.generated_profile["name"]}\n'
                                    f'Touches : {mapping_summary(self.generated_profile)}\n'
                                    f'Fichier : {self.output_folder / "firmware.dat"}')
                self.ready_text.set(f'{self.generated_profile["name"]}\n'
                                    f'{mapping_summary(self.generated_profile).replace(" -> ", " → ")}\n'
                                    'Firmware généré et vérifié. Prêt à installer.'
                                    + ('\nGâchettes remappées : mode numérique sur Switch.'
                                       if triggers_remapped(self.generated_profile) else ''))
            else:
                self.status.set('Vos réglages ont changé. Préparez à nouveau le firmware avant de l’installer.')
                self.flash_info.set('Les réglages ont changé. Le dernier fichier généré ne correspond plus à ce profil.')
                self.ready_text.set('Vos réglages ont changé. Préparez un nouveau firmware pour installer ce profil.')

    def start_job(self, operation):
        self.busy = True
        self.operation = operation
        self.disabled_widgets = []
        def disable(parent):
            for widget in parent.winfo_children():
                if isinstance(widget, (ttk.Button, ttk.Entry, ttk.Checkbutton)):
                    self.disabled_widgets.append((widget, str(widget.cget('state'))))
                    widget.configure(state='disabled')
                disable(widget)
        disable(self.root)
        self.refresh_actions()
        self.progress.configure(mode='indeterminate', value=0)
        self.progress.start(12)

    def finish_job(self):
        self.busy = False
        self.operation = None
        self.progress.stop()
        for widget, state in self.disabled_widgets:
            widget.configure(state=state)
        self.disabled_widgets = []
        self.refresh_actions()

    def append_log(self, line):
        self.flash_log.configure(state='normal')
        self.flash_log.insert('end', line.rstrip() + '\n')
        self.flash_log.see('end')
        self.flash_log.configure(state='disabled')

    def flash(self):
        if self.busy or not self.current_matches_output():
            return
        command = flash_command(self.output_folder / 'manifest.json')
        summary = mapping_summary(self.generated_profile)
        self.start_job('flash')
        self.last_flash_exit_code = None
        self.show_page(3)
        self.flash_log.configure(state='normal')
        self.flash_log.delete('1.0', 'end')
        self.flash_log.configure(state='disabled')
        self.append_log(f'Profil : {self.generated_profile["name"]}\nTouches : {summary}')
        self.status.set('Vérification du profil et de l’adaptateur… Ne débranchez pas l’adaptateur.')
        self.run_updater(command, 'flash_done', summary)

    def check_connection(self):
        if self.busy:
            return
        self.start_job('probe')
        self.last_probe_exit_code = None
        self.show_page(3)
        self.flash_log.configure(state='normal')
        self.flash_log.delete('1.0', 'end')
        self.flash_log.configure(state='disabled')
        self.status.set('Identification de l’adaptateur… Aucun firmware envoyé.')
        if not self.log_visible:
            self.toggle_log()
        self.run_updater(updater_command('Identifier'), 'probe_done')

    def run_updater(self, command, completion, summary=None):
        def work():
            try:
                environment = dict(os.environ, PYTHONIOENCODING='utf-8')
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding='utf-8', errors='replace', bufsize=1, env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                with process:
                    for line in process.stdout:
                        self.events.put(('flash_line', line))
                    code = process.wait()
                self.events.put((completion, (code, summary)))
            except Exception as error:
                self.events.put(('flash_line', f'ECHEC : {error}'))
                self.events.put((completion, (1, summary)))
        # Keep Python alive until the integrated updater returns. Closing the UI
        # during a job is also blocked by close(); no forced process cancellation.
        threading.Thread(target=work, daemon=False).start()

    def set_profile(self, value):
        profile = validate_profile(value)
        self.name.set(profile['name'])
        for name, variable in self.buttons.items():
            variable.set(profile['buttons'][name])
        for axis, variable in self.axes.items():
            variable.set(axis in profile['invert_axes'])
        self.swap.set(profile['swap_sticks'])
        self.pulse.set(profile['mi_pulse'])
        self.duration.set(str(profile['mi_pulse_ms']))
        self.battery.set(profile['battery'])
        self.reconnect.set(profile['reconnect'])

    def get_profile(self):
        try:
            duration = int(self.duration.get())
        except ValueError:
            raise ValueError('La durée Mi doit être un entier de 40 à 1000 ms.') from None
        return validate_profile({'schema': 1, 'name': self.name.get(),
            'buttons': {name: variable.get() for name, variable in self.buttons.items()},
            'invert_axes': [a for a, var in self.axes.items() if var.get()],
            'swap_sticks': self.swap.get(), 'mi_pulse': self.pulse.get(),
            'mi_pulse_ms': duration, 'battery': self.battery.get(), 'reconnect': self.reconnect.get()})

    def open_profile(self):
        path = filedialog.askopenfilename(parent=self.root, title='Ouvrir un profil Xiaomi',
                initialdir=ROOT / 'profiles', filetypes=[('Profil JSON', '*.json')])
        if path:
            try:
                self.set_profile(load_profile(path))
                self.status.set(f'Profil chargé : {Path(path).name}')
            except Exception as error:
                messagebox.showerror('Profil incorrect', str(error), parent=self.root)

    def save_profile(self):
        try:
            profile = self.get_profile()
        except ValueError as error:
            messagebox.showerror('Réglage incorrect', str(error), parent=self.root)
            return
        path = filedialog.asksaveasfilename(parent=self.root, title='Enregistrer le profil Xiaomi',
                initialdir=ROOT / 'profiles', initialfile='mon_profil.json',
                defaultextension='.json', filetypes=[('Profil JSON', '*.json')])
        if path:
            try:
                write_json(path, profile)
                self.status.set(f'Profil enregistré : {Path(path).name}')
            except OSError as error:
                messagebox.showerror('Enregistrement impossible', str(error), parent=self.root)

    def generate(self):
        if self.busy:
            return
        try:
            profile = self.get_profile()
        except ValueError as error:
            messagebox.showerror('Réglage incorrect', str(error), parent=self.root)
            return
        self.start_job('generation')
        self.status.set(f'Génération du profil « {profile["name"]} »…')
        def work():
            try:
                folder, validation = generate_bundle(profile, lambda msg: self.events.put(('progress', msg)))
                self.events.put(('done', (folder, validation, profile)))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    self.status.set(value)
                    continue
                if kind == 'flash_line':
                    self.append_log(value)
                    match = re.search(r'Progression\s*:\s*(\d+)\s*%', value)
                    if match:
                        percent = min(100, int(match.group(1)))
                        self.progress.stop()
                        self.progress.configure(mode='determinate', value=percent)
                        self.status.set(f'Transfert : {percent} % — ne débranchez pas l’adaptateur.' if percent < 100
                                        else 'Finalisation du transfert… Ne débranchez pas l’adaptateur.')
                    continue
                if kind == 'flash_done':
                    code, summary = value
                    self.last_flash_exit_code = code
                    self.finish_job()
                    if code == 0:
                        self.progress.configure(mode='determinate', value=100)
                        self.phase.set('Installation terminée')
                        self.status.set('Flash terminé. Rebranchez l’adaptateur normalement et testez votre manette sur Switch.')
                    else:
                        self.progress.configure(mode='determinate', value=0)
                        self.phase.set('Installation interrompue')
                        self.status.set('Échec de l’installation. Le journal ci-dessus donne le détail du problème.')
                        if not self.log_visible:
                            self.toggle_log()
                    continue
                if kind == 'probe_done':
                    self.last_probe_exit_code = value[0]
                    self.finish_job()
                    self.progress.configure(mode='determinate', value=0)
                    self.phase.set('Connexion vérifiée' if value[0] == 0 else 'Connexion à vérifier')
                    self.status.set('Adaptateur identifié. Aucun firmware envoyé. La version figure dans le journal.'
                                    if value[0] == 0 else 'Identification impossible. Consultez le journal et le branchement.')
                    continue
                if kind == 'done':
                    self.output_folder, validation, self.generated_profile = value
                    self.finish_job()
                    self.progress.configure(mode='determinate', value=100)
                    summary = mapping_summary(self.generated_profile)
                    self.flash_info.set(f'Profil : {self.generated_profile["name"]}\nTouches : {summary}\n'
                                        f'Fichier : {self.output_folder / "firmware.dat"}')
                    self.on_profile_changed()
                    if self.current_matches_output():
                        self.status.set(f'{validation["total_checks"]:,} contrôles réussis. Votre profil est prêt à installer.'.replace(',', ' '))
                else:
                    self.finish_job()
                    self.progress.configure(mode='determinate', value=0)
                    self.status.set('Génération interrompue : aucun flash lancé.')
                    messagebox.showerror('Génération impossible', value, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    def open_output(self):
        if not self.busy and self.current_matches_output():
            os.startfile(str(self.output_folder))

    def close(self):
        if self.busy:
            self.status.set('Opération en cours : attendez sa fin avant de fermer la fenêtre.')
        else:
            self.root.destroy()


def main():
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root = tk.Tk()
    Configurator(root)
    root.mainloop()


if __name__ == '__main__':
    main()
