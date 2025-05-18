import os
import sys
import re
import queue
import threading
import subprocess
import hcl2
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

class ConsoleOutput:
    def __init__(self, console_widget, ansi_styles):
        self.console = console_widget
        self.ansi_styles = ansi_styles
        self.line_buffer = ""
        self.current_tags = []

    def write(self, text):
        self.line_buffer += text
        while "\n" in self.line_buffer or "\r" in self.line_buffer:
            line, sep, self.line_buffer = self.line_buffer.partition("\n")
            line = line.replace("\r", "")
            self._process_line(line + sep)

    def _process_line(self, line):
        segments = re.split(r'(\x1b\[[0-9;]*m)', line)
        current_tags = []
        
        if "\r" in line:
            self.console.delete("end-2l", "end-1c")
        
        for segment in segments:
            if segment.startswith('\x1b['):
                codes = segment[2:-1].split(';')
                current_tags = []
                for code in codes:
                    if code in self.ansi_styles:
                        current_tags.append(f'ansi_{code}')
                    elif code == '0':
                        current_tags = ['ansi_0']
            else:
                self.console.insert("end", segment, tuple(current_tags))
        self.console.see("end")

class TerraformGUI:
    def __init__(self, root, tf_dir):
        self.root = root
        self.root.title("Terraform GUI Manager")
        self.tf_dir = tf_dir
        self.var_widgets = {}
        self.tfvars_path = os.path.join(self.tf_dir, "gui_auto.tfvars")
        self.output_queue = queue.Queue()
        self.process = None
        self.after_id = None
        self.ansi_styles = {}
        self.observer = None

        if not self.validate_terraform_dir():
            messagebox.showerror("Error", "Selected directory must contain variables.tf")
            self.root.destroy()
            return

        self.create_widgets()
        self.setup_ansi_tags()
        self.load_variables()
        self.setup_file_watcher()

    def validate_terraform_dir(self):
        return os.path.isfile(os.path.join(self.tf_dir, "variables.tf"))

    def create_widgets(self):
        # Create paned window for resizable UI
        self.paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        self.paned.pack(fill=tk.BOTH, expand=True)

        # Top panel for variables
        variables_frame = ttk.Frame(self.paned)
        self.paned.add(variables_frame, weight=3)

        # Scrollable canvas
        self.canvas = tk.Canvas(variables_frame)
        self.scrollbar = ttk.Scrollbar(variables_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind("<Configure>", 
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        # Bottom panel for console and buttons
        console_frame = ttk.Frame(self.paned)
        self.paned.add(console_frame, weight=1)
        
        # Configure grid layout for console frame
        console_frame.grid_rowconfigure(2, weight=1)  # Console will expand
        console_frame.grid_columnconfigure(0, weight=1)

        # Add bold black separator line above buttons
        separator = tk.Frame(console_frame, height=2, background='black')
        separator.grid(row=0, column=0, sticky='ew', pady=1)
        
        # Button bar
        btn_frame = ttk.Frame(console_frame)
        btn_frame.grid(row=1, column=0, sticky='ew', padx=10, pady=2)
        
        ttk.Button(btn_frame, text="Init", command=self.run_init).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Apply-Plan", command=self.run_plan).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Apply", command=self.run_apply).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Destroy-Plan", command=self.run_destroy_plan).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Destroy", command=self.run_destroy).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Refresh", command=self.load_variables).pack(side=tk.RIGHT, padx=2)

        # Console setup
        self.console = tk.Text(
            console_frame,
            height=15,
            wrap=tk.WORD,
            bg='black',
            fg='white',
            insertbackground='white',
            font=('Consolas', 10)
        )
        self.console.grid(row=2, column=0, sticky='nsew', padx=10, pady=(0, 5))
        self.console_output = ConsoleOutput(self.console, self.ansi_styles)

    def setup_ansi_tags(self):
        self.ansi_styles = {
            '0': {'foreground': 'white', 'background': 'black'},
            '30': {'foreground': 'black'}, '31': {'foreground': '#FF0000'},
            '32': {'foreground': '#00FF00'}, '33': {'foreground': '#FFFF00'},
            '34': {'foreground': '#0000FF'}, '35': {'foreground': '#FF00FF'},
            '36': {'foreground': '#00FFFF'}, '37': {'foreground': 'white'},
            '1': {'font': ('Consolas', 10, 'bold')}, '4': {'underline': True}
        }
        for code, style in self.ansi_styles.items():
            style['background'] = 'black'
            self.console.tag_configure(f'ansi_{code}', **style)

    def load_variables(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        try:
            with open(os.path.join(self.tf_dir, "variables.tf"), 'r', encoding='utf-8') as f:
                variables_config = hcl2.load(f)
            
            for var in variables_config.get('variable', []):
                var_name = list(var.keys())[0]
                var_config = var[var_name]
                self.create_var_widget(var_name, var_config)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load variables: {str(e)}")

    def create_var_widget(self, var_name, var_config):
        frame = ttk.Frame(self.scrollable_frame)
        frame.pack(fill=tk.X, pady=2)

        label = ttk.Label(frame, text=f"{var_name}:", width=25)
        label.pack(side=tk.LEFT)

        var_type = var_config.get('type', 'string')
        default = var_config.get('default', '')

        if 'string' in var_type:
            widget = ttk.Entry(frame)
            widget.insert(0, default)
        elif 'number' in var_type:
            widget = ttk.Spinbox(frame, from_=-999999, to=999999)
            widget.set(default)
        elif 'bool' in var_type:
            widget = ttk.Combobox(frame, values=['true', 'false'], state="readonly")
            widget.set(str(default).lower())
        else:
            widget = ttk.Entry(frame)

        widget.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.var_widgets[var_name] = widget
    def get_variables(self):
        """Get current values from input widgets"""
        variables = {}
        for var_name, widget in self.var_widgets.items():
            value = widget.get()
            if value:  # Only include variables with values
                variables[var_name] = value
        return variables

    def save_tfvars(self):
        with open(self.tfvars_path, 'w', encoding='utf-8') as f:
            for k, v in self.get_variables().items():
                f.write(f'{k} = "{v}"\n')

    def _read_output(self, stream, output_type):
        try:
            for line in iter(stream.readline, ''):
                self.output_queue.put((line, output_type))
        finally:
            stream.close()

    def _process_output(self):
        while not self.output_queue.empty():
            line, output_type = self.output_queue.get_nowait()
            self.console_output.write(line)
        self.after_id = self.root.after(50, self._process_output)

    def execute_command(self, command, args=[]):
        self.save_tfvars()
        try:
            if self.process and self.process.poll() is None:
                self.process.terminate()

            cmd = ['terraform', command] + args + ['-var-file', self.tfvars_path]
            self.process = subprocess.Popen(
                cmd,
                cwd=self.tf_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace'
            )

            threading.Thread(target=self._read_output, args=(self.process.stdout, 'stdout'), daemon=True).start()
            threading.Thread(target=self._read_output, args=(self.process.stderr, 'stderr'), daemon=True).start()
            self._process_output()

        except Exception as e:
            self.console_output.write(f"Error: {str(e)}\n")

    def run_init(self):
        self.execute_command('init')

    def run_plan(self):
        self.execute_command('plan')

    def run_apply(self):
        self.execute_command('apply', ['-auto-approve'])

    def run_destroy_plan(self):
        self.execute_command('plan', ['-destroy'])

    def run_destroy(self):
        self.execute_command('destroy', ['-auto-approve'])

    def setup_file_watcher(self):
        self.observer = Observer()
        handler = FileSystemEventHandler()
        handler.on_modified = lambda e: self.on_file_modified(e)
        self.observer.schedule(handler, path=self.tf_dir, recursive=False)
        self.observer.start()

    def on_file_modified(self, event):
        if os.path.basename(event.src_path) == 'variables.tf':
            self.root.after(0, self.load_variables)

    def __del__(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()

if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()

    tf_dir = filedialog.askdirectory(
        title="Select Terraform Directory",
        mustexist=True
    )

    if not tf_dir:
        sys.exit()

    if not os.path.isfile(os.path.join(tf_dir, "variables.tf")):
        messagebox.showerror("Error", "Selected directory must contain variables.tf")
        sys.exit()

    root.deiconify()
    app = TerraformGUI(root, tf_dir)
    root.mainloop()