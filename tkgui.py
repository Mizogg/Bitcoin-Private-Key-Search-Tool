#!/usr/bin/env python3
import sys
import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser, simpledialog
import threading
import queue
import datetime
import locale
import configparser
from settings import DEFAULT_START_HEX, DEFAULT_STOP_HEX, ADDRESSES_FILE
import json

IMAGES_MAIN = "images/"

# Initialize locale for number formatting
try:
    locale.setlocale(locale.LC_ALL, '')
except locale.Error:
    print("Warning: Could not set locale for number formatting.")

class SearchThread(threading.Thread):
    def __init__(self, command, output_queue, progress_queue):
        super().__init__()
        self.command = command
        self.output_queue = output_queue
        self.progress_queue = progress_queue
        self.process = None
        self.is_running = False
        self.should_stop = False
        self.key_check_complete = False
        self.key_check_buffer = ""
        self.is_key_check = "--check-key" in command
        self.daemon = True  # Thread will be killed when main program exits

    def run(self):
        if self.should_stop:
            return

        try:
            # Set up environment for unbuffered output
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            
            self.process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
                env=env
            )
            self.is_running = True

            # Create threads to read stdout and stderr
            stdout_thread = threading.Thread(target=self._read_stream, args=(self.process.stdout,))
            stderr_thread = threading.Thread(target=self._read_stream, args=(self.process.stderr,))
            
            stdout_thread.daemon = True
            stderr_thread.daemon = True
            
            stdout_thread.start()
            stderr_thread.start()

            # Wait for process to complete
            self.process.wait()
            
            # Wait for reading threads to complete
            stdout_thread.join()
            stderr_thread.join()
            
            self.is_running = False
            self.output_queue.put(("FINISHED", "Search completed"))

        except Exception as e:
            self.output_queue.put(("ERROR", f"Error in search thread: {str(e)}"))
            self.is_running = False

    def _read_stream(self, stream):
        """Read from a stream and handle the output"""
        try:
            for line in iter(stream.readline, ''):
                if self.should_stop:
                    break
                if line:
                    self.handle_output(line)
        except Exception as e:
            self.output_queue.put(("ERROR", f"Error reading stream: {str(e)}"))

    def handle_output(self, output):
        if self.should_stop:
            return

        try:
            # Handle key check output
            if self.is_key_check:
                if "===== Private Key Check =====" in output:
                    self.key_check_buffer = output
                elif self.key_check_buffer:
                    self.key_check_buffer += output
                    if "============================" in output:
                        self.key_check_complete = True
                        self.output_queue.put(("KEY_CHECK", self.key_check_buffer))
                        self.key_check_buffer = ""
                return

            # Handle progress updates
            if "%" in output:
                import re
                matches = re.findall(r'(\d+)%', output)
                if matches:
                    self.progress_queue.put(int(matches[-1]))
            elif "keys" in output.lower() and "/" in output:
                import re
                matches = re.findall(r'(\d+)/(\d+)', output)
                if matches:
                    current, total = matches[-1]
                    percentage = int((int(current) / int(total)) * 100)
                    self.progress_queue.put(percentage)

            # Queue other output
            if output.strip():
                if self.is_key_check:
                    self.output_queue.put(("KEY_CHECK", output.strip()))
                else:
                    self.output_queue.put(("OUTPUT", output.strip()))

        except Exception as e:
            self.output_queue.put(("ERROR", f"Error handling output: {str(e)}"))

    def stop(self):
        self.should_stop = True
        if self.process:
            try:
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)], 
                                 capture_output=True)
                else:
                    subprocess.run(['pkill', '-9', '-P', str(self.process.pid)], 
                                 capture_output=True)
            except Exception:
                pass

class MainWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Mizogg Bitcoin Private Key Search")
        self.root.minsize(1000, 600)
        
        # Initialize variables
        self.search_thread = None
        self.output_queue = queue.Queue()
        self.progress_queue = queue.Queue()
        self.force_quit = False
        self.output_clear_timer = None  # Add timer variable
        
        # Initialize theme-related attributes
        self.bg_color = None
        self.text_color = None
        self.button_color = None
        self.theme_combo = None
        
        # Initialize style
        self.style = ttk.Style()
        
        # Create menu
        self.create_menu()
        
        # Create main notebook (tabs)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill='both', padx=5, pady=5)
        
        # Create tabs
        self.create_search_tab()
        self.create_checking_tab()
        self.create_output_tab()
        
        # Initialize timers
        self.scan_start_time = datetime.datetime.now()
        self.scan_start_time_str = self.scan_start_time.strftime('%Y-%m-%d %H:%M:%S')
        self.scan_start_time_label.config(text=f"Scan Started: {self.scan_start_time_str}")
        self.update_timers()
        
        # Load and apply theme after UI is created
        self.root.after(100, self.load_and_apply_theme)
        
        # Start periodic updates
        self.root.after(100, self.check_queues)
        self.root.after(1000, self.update_elapsed_time)

    def load_and_apply_theme(self):
        """Load and apply theme from config file"""
        config = configparser.ConfigParser()
        config_file = 'config.ini'
        
        if os.path.exists(config_file):
            try:
                config.read(config_file)
                if config.has_section('Theme'):
                    colors = {
                        'bg': config.get('Theme', 'background', fallback='#000000'),
                        'text': config.get('Theme', 'text', fallback='#FFFFFF'),
                        'button': config.get('Theme', 'button', fallback='#404040')
                    }
                    self.apply_theme(colors, save=False)
                else:
                    colors = self.get_theme_colors("Default Dark")
                    self.apply_theme(colors, save=False)
            except Exception as e:
                print(f"Error loading theme: {str(e)}")
                colors = self.get_theme_colors("Default Dark")
                self.apply_theme(colors, save=False)
        else:
            colors = self.get_theme_colors("Default Dark")
            self.apply_theme(colors, save=False)

    def apply_theme(self, colors, save=True):
        """Apply theme colors to all widgets"""
        # Configure ttk styles
        self.style.configure('.',  # Apply to all ttk widgets
            background=colors['bg'],
            foreground=colors['text'],
            fieldbackground=colors['bg'])
            
        self.style.configure('TButton',
            background=colors['button'],
            foreground=colors['text'])
            
        self.style.configure('TLabel',
            background=colors['bg'],
            foreground=colors['text'])
            
        self.style.configure('TFrame',
            background=colors['bg'])
            
        self.style.configure('TLabelframe',
            background=colors['bg'])
            
        self.style.configure('TLabelframe.Label',
            background=colors['bg'],
            foreground=colors['text'])
            
        self.style.configure('TNotebook',
            background=colors['bg'])
            
        self.style.configure('TNotebook.Tab',
            background=colors['bg'],
            foreground=colors['text'])
            
        self.style.configure('TEntry',
            fieldbackground=colors['bg'],
            foreground=colors['text'])
            
        self.style.configure('TCombobox',
            fieldbackground=colors['bg'],
            foreground=colors['text'],
            background=colors['button'])
        
        # Configure root window
        self.root.configure(bg=colors['bg'])
        
        # Apply to all existing widgets
        for widget in self.root.winfo_children():
            if isinstance(widget, tk.Text):
                # For text widgets, use a slightly lighter background for better contrast
                bg_color = colors['bg']
                if bg_color.startswith('#'):
                    # Convert hex to RGB
                    r = int(bg_color[1:3], 16)
                    g = int(bg_color[3:5], 16)
                    b = int(bg_color[5:7], 16)
                    # Make background slightly lighter
                    r = min(255, r + 20)
                    g = min(255, g + 20)
                    b = min(255, b + 20)
                    bg_color = f'#{r:02x}{g:02x}{b:02x}'
                widget.configure(
                    bg=bg_color,
                    fg=colors['text'],
                    insertbackground=colors['text'],  # Cursor color
                    selectbackground=colors['button'],  # Selection background
                    selectforeground=colors['text']  # Selection text color
                )
            elif isinstance(widget, ttk.Notebook):
                for tab in widget.winfo_children():
                    if isinstance(tab, tk.Text):
                        # Apply same text widget styling to notebook tabs
                        bg_color = colors['bg']
                        if bg_color.startswith('#'):
                            r = int(bg_color[1:3], 16)
                            g = int(bg_color[3:5], 16)
                            b = int(bg_color[5:7], 16)
                            r = min(255, r + 20)
                            g = min(255, g + 20)
                            b = min(255, b + 20)
                            bg_color = f'#{r:02x}{g:02x}{b:02x}'
                        tab.configure(
                            bg=bg_color,
                            fg=colors['text'],
                            insertbackground=colors['text'],
                            selectbackground=colors['button'],
                            selectforeground=colors['text']
                        )
                    elif isinstance(tab, ttk.Frame):
                        # Recursively apply theme to all widgets in frames
                        self._apply_theme_to_widgets(tab, colors)
        
        # Update preview if it exists
        if hasattr(self, 'preview_text'):
            bg_color = colors['bg']
            if bg_color.startswith('#'):
                r = int(bg_color[1:3], 16)
                g = int(bg_color[3:5], 16)
                b = int(bg_color[5:7], 16)
                r = min(255, r + 20)
                g = min(255, g + 20)
                b = min(255, b + 20)
                bg_color = f'#{r:02x}{g:02x}{b:02x}'
            self.preview_text.configure(
                bg=bg_color,
                fg=colors['text'],
                insertbackground=colors['text'],
                selectbackground=colors['button'],
                selectforeground=colors['text']
            )
            
        # Save the current theme only if requested
        if save:
            self.save_theme_to_config(colors)

    def _apply_theme_to_widgets(self, parent, colors):
        """Recursively apply theme colors to all widgets in a container"""
        for widget in parent.winfo_children():
            if isinstance(widget, tk.Text):
                widget.configure(bg=colors['bg'], fg=colors['text'])
            elif isinstance(widget, (ttk.Frame, ttk.LabelFrame)):
                self._apply_theme_to_widgets(widget, colors)
            elif isinstance(widget, ttk.Label):
                widget.configure(style='TLabel')
            elif isinstance(widget, ttk.Button):
                widget.configure(style='TButton')
            elif isinstance(widget, ttk.Entry):
                widget.configure(style='TEntry')
            elif isinstance(widget, ttk.Combobox):
                widget.configure(style='TCombobox')

    def create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="New Window", command=self.new_window)
        file_menu.add_separator()
        file_menu.add_command(label="Settings", command=self.open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.exit_app)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.about)

    def create_search_tab(self):
        search_frame = ttk.Frame(self.notebook)
        self.notebook.add(search_frame, text="Search")
        
        # Key Space Configuration
        keyspace_frame = ttk.LabelFrame(search_frame, text="Key Space Configuration")
        keyspace_frame.pack(fill='x', padx=5, pady=5)
        
        # Keyspace Entry
        keyspace_label = ttk.Label(keyspace_frame, text="Key Space (Hex):")
        keyspace_label.pack(side='left', padx=5)
        self.keyspace_entry = ttk.Entry(keyspace_frame)
        self.keyspace_entry.insert(0, f"{DEFAULT_START_HEX}:{DEFAULT_STOP_HEX}")
        self.keyspace_entry.pack(side='left', fill='x', expand=True, padx=5)
        
        # Bits Slider
        bits_frame = ttk.Frame(keyspace_frame)
        bits_frame.pack(fill='x', padx=5, pady=5)
        
        self.bits_slider = ttk.Scale(bits_frame, from_=1, to=256, orient='horizontal', command=self.update_bits)
        self.bits_slider.set(71)
        self.bits_slider.pack(side='left', fill='x', expand=True)
        
        bits_label = ttk.Label(bits_frame, text="Bits:")
        bits_label.pack(side='left', padx=5)
        self.bits_entry = ttk.Entry(bits_frame, width=5)
        self.bits_entry.insert(0, "71")
        self.bits_entry.pack(side='left', padx=5)
        
        # Bind events for bits entry
        self.bits_entry.bind('<KeyRelease>', self.on_bits_entry_change)
        self.bits_entry.bind('<Return>', self.update_from_entry)
        self.bits_entry.bind('<FocusOut>', self.update_from_entry)
        
        # Initialize validation timer
        self.bits_validation_timer = None
        
        # Target Frame
        target_frame = ttk.LabelFrame(search_frame, text="Target")
        target_frame.pack(fill='x', padx=5, pady=5)
        
        # Addresses File
        file_frame = ttk.Frame(target_frame)
        file_frame.pack(fill='x', padx=5, pady=5)
        
        ttk.Label(file_frame, text="Addresses File:").pack(side='left')
        self.addresses_entry = ttk.Entry(file_frame)
        self.addresses_entry.insert(0, ADDRESSES_FILE)
        self.addresses_entry.pack(side='left', fill='x', expand=True, padx=5)
        
        browse_button = ttk.Button(file_frame, text="Browse", command=self.browse_addresses_file)
        browse_button.pack(side='left')
        
        # Address count label
        self.address_count_label = ttk.Label(target_frame, text="")
        self.address_count_label.pack(pady=5)
        
        # Search Options
        options_frame = ttk.LabelFrame(search_frame, text="Search Options")
        options_frame.pack(fill='x', padx=5, pady=5)
        
        # Search mode
        self.search_mode = tk.StringVar(value="sequence")
        ttk.Radiobutton(options_frame, text="Sequential Scanning", 
                       variable=self.search_mode, value="sequence").pack(anchor='w')
        ttk.Radiobutton(options_frame, text="Random Scanning", 
                       variable=self.search_mode, value="random").pack(anchor='w')
        ttk.Radiobutton(options_frame, text="Dance Scanning", 
                       variable=self.search_mode, value="dance").pack(anchor='w')
        
        # Address format
        format_frame = ttk.Frame(options_frame)
        format_frame.pack(fill='x', pady=5)
        ttk.Label(format_frame, text="Address Format:").pack(side='left')
        
        self.address_format = tk.StringVar(value="compressed")
        ttk.Radiobutton(format_frame, text="Compressed", 
                       variable=self.address_format, value="compressed").pack(side='left')
        ttk.Radiobutton(format_frame, text="Uncompressed", 
                       variable=self.address_format, value="uncompressed").pack(side='left')
        ttk.Radiobutton(format_frame, text="Both", 
                       variable=self.address_format, value="both").pack(side='left')
        
        # CPU cores
        cpu_frame = ttk.Frame(options_frame)
        cpu_frame.pack(fill='x', pady=5)
        ttk.Label(cpu_frame, text="CPU Cores:").pack(side='left')
        self.cpu_spinbox = ttk.Spinbox(cpu_frame, from_=1, to=os.cpu_count() or 1)
        self.cpu_spinbox.set(os.cpu_count() or 1)
        self.cpu_spinbox.pack(side='left', padx=5)
        
        # Progress bar
        self.progress_bar = ttk.Progressbar(search_frame, mode='determinate')
        self.progress_bar.pack(fill='x', padx=5, pady=5)
        
        # Buttons
        button_frame = ttk.Frame(search_frame)
        button_frame.pack(fill='x', padx=5, pady=5)
        
        self.start_button = ttk.Button(button_frame, text="Start Search", 
                                     command=self.start_search)
        self.start_button.pack(side='left', padx=5)
        
        self.stop_button = ttk.Button(button_frame, text="Stop Search", 
                                    command=self.stop_search, state='disabled')
        self.stop_button.pack(side='left', padx=5)
        
        help_button = ttk.Button(button_frame, text="Help: Search Modes", 
                               command=self.show_search_help)
        help_button.pack(side='right', padx=5)

    def create_checking_tab(self):
        checking_frame = ttk.Frame(self.notebook)
        self.notebook.add(checking_frame, text="Checking Keys and Files")
        
        # File Check Frame
        file_check_frame = ttk.LabelFrame(checking_frame, text="Check Files")
        file_check_frame.pack(fill='x', padx=5, pady=5)
        
        ttk.Button(file_check_frame, text="Progressing File",
                  command=lambda: self.check_file("scan_progress.json")).pack(pady=5)
        ttk.Button(file_check_frame, text="Check Found Keys",
                  command=lambda: self.check_file("found_keys.txt")).pack(pady=5)
        
        # Key Check Frame
        key_check_frame = ttk.LabelFrame(checking_frame, text="Check Private Key")
        key_check_frame.pack(fill='x', padx=5, pady=5)
        
        key_input_frame = ttk.Frame(key_check_frame)
        key_input_frame.pack(fill='x', padx=5, pady=5)
        
        ttk.Label(key_input_frame, text="Private Key (hex):").pack(side='left')
        self.key_input = ttk.Entry(key_input_frame)
        self.key_input.pack(side='left', fill='x', expand=True, padx=5)
        
        self.check_button = ttk.Button(key_check_frame, text="Check Key",
                                     command=self.check_key)
        self.check_button.pack(pady=5)
        
        # Address Check Frame
        address_check_frame = ttk.LabelFrame(checking_frame, text="Check Address")
        address_check_frame.pack(fill='x', padx=5, pady=5)
        
        address_input_frame = ttk.Frame(address_check_frame)
        address_input_frame.pack(fill='x', padx=5, pady=5)
        
        ttk.Label(address_input_frame, text="Bitcoin Address:").pack(side='left')
        self.address_input = ttk.Entry(address_input_frame)
        self.address_input.pack(side='left', fill='x', expand=True, padx=5)
        
        self.check_address_button = ttk.Button(address_check_frame, text="Check Address",
                                             command=self.check_address)
        self.check_address_button.pack(pady=5)
        
        # Range Tools Frame
        range_frame = ttk.LabelFrame(checking_frame, text="Range Tools")
        range_frame.pack(fill='x', padx=5, pady=5)
        
        # Range Input Section
        input_frame = ttk.Frame(range_frame)
        input_frame.pack(fill='x', pady=2)
        
        # Start Range
        start_frame = ttk.Frame(input_frame)
        start_frame.pack(fill='x', pady=2)
        ttk.Label(start_frame, text="Start Range:").pack(side='left', padx=5)
        start_entry = ttk.Entry(start_frame, width=50)
        start_entry.pack(side='left', padx=5, fill='x', expand=True)
        
        # End Range
        end_frame = ttk.Frame(input_frame)
        end_frame.pack(fill='x', pady=2)
        ttk.Label(end_frame, text="End Range:").pack(side='left', padx=5)
        end_entry = ttk.Entry(end_frame, width=50)
        end_entry.pack(side='left', padx=5, fill='x', expand=True)
        
        # Keys per second input
        kps_frame = ttk.Frame(input_frame)
        kps_frame.pack(fill='x', pady=2)
        ttk.Label(kps_frame, text="Keys per second:").pack(side='left', padx=5)
        kps_entry = ttk.Entry(kps_frame, width=10)
        kps_entry.insert(0, "1000000")  # Default 1M keys/sec
        kps_entry.pack(side='left', padx=5)
        
        # Buttons Frame
        button_frame = ttk.Frame(range_frame)
        button_frame.pack(fill='x', pady=5)
        
        def check_range():
            try:
                start = int(start_entry.get(), 16)
                end = int(end_entry.get(), 16)
                if start >= end:
                    messagebox.showerror("Error", "Start range must be less than end range")
                    return
                
                # Get keys per second
                try:
                    keys_per_second = int(kps_entry.get().replace(',', ''))
                    if keys_per_second <= 0:
                        raise ValueError("Keys per second must be positive")
                except ValueError:
                    messagebox.showerror("Error", "Invalid keys per second value")
                    return
                
                # Calculate range size
                range_size = end - start + 1  # Add 1 to include both start and end
                bits = (range_size).bit_length()
                
                # Update result text
                self.checking_results.delete(1.0, tk.END)
                self.checking_results.insert(tk.END, f"Range Size: {range_size:,} keys\n")
                self.checking_results.insert(tk.END, f"Bits: {bits}\n")
                self.checking_results.insert(tk.END, f"Start: {hex(start)}\n")
                self.checking_results.insert(tk.END, f"End: {hex(end)}\n")
                
                # Calculate estimated time
                estimated_seconds = range_size / keys_per_second
                days = int(estimated_seconds // (24 * 3600))
                hours = int((estimated_seconds % (24 * 3600)) // 3600)
                minutes = int((estimated_seconds % 3600) // 60)
                
                self.checking_results.insert(tk.END, f"\nEstimated Time (at {keys_per_second:,} keys/sec):\n")
                self.checking_results.insert(tk.END, f"{days:,} days, {hours} hours, {minutes} minutes\n")
                
            except ValueError as e:
                messagebox.showerror("Error", "Invalid range format. Please use hexadecimal values.")
        
        def split_range():
            try:
                start = int(start_entry.get(), 16)
                end = int(end_entry.get(), 16)
                if start >= end:
                    messagebox.showerror("Error", "Start range must be less than end range")
                    return
                
                # Get number of splits
                num_splits = simpledialog.askinteger("Split Range", "Enter number of splits:", 
                                                   minvalue=2, maxvalue=100)
                if not num_splits:
                    return
                
                # Calculate splits
                total_range = end - start
                chunk_size = total_range // num_splits
                remainder = total_range % num_splits
                
                # Update result text
                self.checking_results.delete(1.0, tk.END)
                self.checking_results.insert(tk.END, f"Range split into {num_splits} parts:\n\n")
                
                current_start = start
                for i in range(num_splits):
                    extra = 1 if i < remainder else 0
                    current_end = current_start + chunk_size + extra - 1
                    if i == num_splits - 1:
                        current_end = end
                    
                    self.checking_results.insert(tk.END, f"Part {i+1}:\n")
                    self.checking_results.insert(tk.END, f"Start: {hex(current_start)}\n")
                    self.checking_results.insert(tk.END, f"End: {hex(current_end)}\n")
                    self.checking_results.insert(tk.END, f"Size: {current_end - current_start + 1:,} keys\n\n")
                    
                    current_start = current_end + 1
                
            except ValueError as e:
                messagebox.showerror("Error", "Invalid range format. Please use hexadecimal values.")
        
        def load_current_range():
            try:
                current_range = self.keyspace_entry.get().strip()
                if current_range:
                    start, end = current_range.split(':')
                    start_entry.delete(0, tk.END)
                    start_entry.insert(0, start)
                    end_entry.delete(0, tk.END)
                    end_entry.insert(0, end)
            except Exception as e:
                messagebox.showerror("Error", "Failed to load current range")
        
        # Add buttons
        ttk.Button(button_frame, text="Check Range", command=check_range).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Split Range", command=split_range).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Load Current Range", command=load_current_range).pack(side='left', padx=5)
        
        # Results
        results_frame = ttk.LabelFrame(checking_frame, text="Results")
        results_frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        self.checking_results = tk.Text(results_frame, height=10, wrap='word')
        scrollbar = ttk.Scrollbar(results_frame, orient="vertical", command=self.checking_results.yview)
        self.checking_results.configure(yscrollcommand=scrollbar.set)
        
        self.checking_results.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        clear_button = ttk.Button(checking_frame, text="Clear Results",
                                command=self.clear_checking_results)
        clear_button.pack(pady=5)
        
        # Load current range if available
        load_current_range()

    def create_output_tab(self):
        output_frame = ttk.Frame(self.notebook)
        self.notebook.add(output_frame, text="Output")
        
        # Output text
        self.output_text = tk.Text(output_frame, wrap='word')
        self.output_text.pack(fill='both', expand=True, padx=5, pady=5)
        
        # Buttons and time information
        button_frame = ttk.Frame(output_frame)
        button_frame.pack(fill='x', padx=5, pady=5)
        
        clear_button = ttk.Button(button_frame, text="Clear Output",
                                command=self.clear_output)
        clear_button.pack(side='left', padx=5)
        
        # Time information frame
        time_frame = ttk.Frame(button_frame)
        time_frame.pack(side='left', fill='x', expand=True, padx=5)
        
        self.current_time_label = ttk.Label(time_frame, text="Current Time: --:--:--")
        self.current_time_label.pack(side='left', padx=5)
        
        self.scan_start_time_label = ttk.Label(time_frame, text="Scan Started: --:--:--")
        self.scan_start_time_label.pack(side='left', padx=5)
        
        self.elapsed_time_label = ttk.Label(time_frame, text="Elapsed Time: 00:00:00")
        self.elapsed_time_label.pack(side='left', padx=5)
        
        self.output_stop_button = ttk.Button(button_frame, text="Stop Search",
                                           command=self.stop_search, state='disabled')
        self.output_stop_button.pack(side='right', padx=5)

    def update_timers(self):
        self.scan_start_time = datetime.datetime.now()
        self.scan_start_time_str = self.scan_start_time.strftime('%Y-%m-%d %H:%M:%S')
        self.scan_start_time_label.config(text=f"Scan Started: {self.scan_start_time_str}")
        self.update_elapsed_time()

    def check_queues(self):
        """Check output and progress queues for updates"""
        try:
            while True:
                try:
                    msg_type, msg = self.output_queue.get_nowait()
                    if msg_type == "OUTPUT":
                        self.output_text.insert('end', msg + '\n')
                        self.output_text.see('end')
                    elif msg_type == "KEY_CHECK":
                        self.checking_results.insert('end', msg + '\n')
                        self.checking_results.see('end')
                    elif msg_type == "ERROR":
                        if self.notebook.index(self.notebook.select()) == 1:  # If on checking tab
                            self.checking_results.insert('end', f"\n[ERROR] {msg}\n")
                            self.checking_results.see('end')
                        else:
                            self.output_text.insert('end', f"\n[ERROR] {msg}\n")
                            self.output_text.see('end')
                    elif msg_type == "FINISHED":
                        self.search_finished()
                        # Re-enable the check button when search is finished
                        self.enable_check_button()
                except queue.Empty:
                    break
        except Exception as e:
            print(f"Error checking queues: {e}")
            # Re-enable the check button in case of error
            self.enable_check_button()

        try:
            while True:
                try:
                    progress = self.progress_queue.get_nowait()
                    self.progress_bar['value'] = progress
                except queue.Empty:
                    break
        except Exception as e:
            print(f"Error checking progress queue: {e}")

        # Schedule next check
        self.root.after(50, self.check_queues)

    def update_elapsed_time(self):
        """Update the elapsed time display and current time"""
        if hasattr(self, 'scan_start_time'):
            # Update elapsed time
            elapsed_time = datetime.datetime.now() - self.scan_start_time
            total_seconds = int(elapsed_time.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            self.elapsed_time_label.config(
                text=f"Elapsed Time: {hours:02d}:{minutes:02d}:{seconds:02d}")
            
            # Update current time
            current_time = datetime.datetime.now()
            self.current_time_label.config(
                text=f"Current Time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Update scan start time (only if it hasn't been set)
            if not hasattr(self, 'scan_start_time_str'):
                self.scan_start_time_str = self.scan_start_time.strftime('%Y-%m-%d %H:%M:%S')
                self.scan_start_time_label.config(
                    text=f"Scan Started: {self.scan_start_time_str}")
        
        # Schedule next update
        self.root.after(1000, self.update_elapsed_time)

    def build_command(self):
        command = ["python", "main.py"]
        
        if self.search_mode.get() == "random":
            command.extend(["--random"])
        elif self.search_mode.get() == "dance":
            command.extend(["--dance"])
        
        # Get start and stop hex from the keyspace entry
        keyspace_range = self.keyspace_entry.get()
        try:
            start_hex, stop_hex = keyspace_range.split(":")
            command.extend(["--start", start_hex.strip()])
            command.extend(["--stop", stop_hex.strip()])
        except ValueError:
            messagebox.showerror("Input Error", 
                               "Invalid key space format. Please use start_hex:stop_hex.")
            return None

        command.extend(["--addresses-file", self.addresses_entry.get()])

        # Add address format argument
        if self.address_format.get() == "uncompressed":
            command.append("--uc")
        elif self.address_format.get() == "both":
            command.append("--both")

        # Add CPU argument
        cpu_cores = int(self.cpu_spinbox.get())
        if cpu_cores > 0 and (cpu_cores != (os.cpu_count() or 1)):
            command.extend(["--cpu", str(cpu_cores)])

        return command

    def start_periodic_output_clear(self):
        """Start periodic clearing of output window"""
        if self.output_clear_timer:
            self.root.after_cancel(self.output_clear_timer)
        
        def clear_output_periodically():
            if self.search_thread and self.search_thread.is_running:
                self.output_text.delete('1.0', 'end')
                self.output_text.insert('end', "[Output cleared to prevent memory issues]\n")
                self.output_text.insert('end', "Scanning continues...\n")
                self.output_text.insert('end', "="*50 + "\n")
                # Schedule next clear
                self.output_clear_timer = self.root.after(120000, clear_output_periodically)  # 120000 ms = 2 minutes
        
        # Start the periodic clearing
        self.output_clear_timer = self.root.after(120000, clear_output_periodically)

    def stop_periodic_output_clear(self):
        """Stop periodic clearing of output window"""
        if self.output_clear_timer:
            self.root.after_cancel(self.output_clear_timer)
            self.output_clear_timer = None

    def start_search(self):
        if self.search_thread and self.search_thread.is_running:
            self.stop_search()
            self.root.after(1000, self._do_start_search)
        else:
            self._do_start_search()

    def _do_start_search(self):
        self.start_button.config(state='disabled')
        self.stop_button.config(state='normal')
        self.output_stop_button.config(state='normal')
        self.progress_bar['value'] = 0
        
        command = self.build_command()
        if command:
            self.output_text.insert('end', f"Running command: {' '.join(command)}\n")
            self.search_thread = SearchThread(command, self.output_queue, self.progress_queue)
            self.search_thread.start()
            self.notebook.select(2)  # Switch to output tab
            self.update_timers()
            # Start periodic output clearing
            self.start_periodic_output_clear()
        else:
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.output_stop_button.config(state='disabled')
            self.output_text.insert('end', "\n[ERROR] Command not built successfully\n")

    def stop_search(self):
        if self.search_thread and self.search_thread.is_running:
            self.output_text.insert('end', "\n[STOPPING] Search is being terminated...\n")
            self.force_quit = True
            self.search_thread.stop()
            # Stop periodic output clearing
            self.stop_periodic_output_clear()
            self.root.after(1000, self.verify_process_termination)

    def verify_process_termination(self):
        if self.force_quit and self.search_thread and self.search_thread.is_running:
            if sys.platform == 'win32':
                try:
                    subprocess.run(['taskkill', '/F', '/IM', 'python.exe'], 
                                 capture_output=True)
                except Exception:
                    pass
            else:
                subprocess.run(['pkill', '-9', '-f', 'python'], 
                             capture_output=True)
            
            self.force_quit = False
            self.start_button.config(state='normal')
            self.stop_button.config(state='disabled')
            self.output_stop_button.config(state='disabled')
            self.output_text.insert('end', "\n[FORCE STOPPED] Search was forcefully terminated\n")
            self.search_thread = None

    def search_finished(self):
        self.start_button.config(state='normal')
        self.stop_button.config(state='disabled')
        self.output_stop_button.config(state='disabled')
        self.output_text.insert('end', "\n[FINISHED] Search completed\n")
        # Stop periodic output clearing
        self.stop_periodic_output_clear()

    def check_file(self, filename):
        if filename == "scan_progress.json" and os.path.exists(filename):
            # Create a custom dialog window
            dialog = tk.Toplevel(self.root)
            dialog.title("Scan Progress File")
            dialog.geometry("300x150")
            dialog.transient(self.root)  # Make dialog modal
            dialog.grab_set()  # Make dialog modal
            
            # Center the dialog
            dialog.update_idletasks()
            width = dialog.winfo_width()
            height = dialog.winfo_height()
            x = (dialog.winfo_screenwidth() // 2) - (width // 2)
            y = (dialog.winfo_screenheight() // 2) - (height // 2)
            dialog.geometry(f'{width}x{height}+{x}+{y}')
            
            # Create message
            msg_frame = ttk.Frame(dialog, padding="10")
            msg_frame.pack(fill='both', expand=True)
            
            ttk.Label(msg_frame, text=f"The file {filename} exists.\nWhat would you like to do?").pack(pady=10)
            
            # Create buttons frame
            button_frame = ttk.Frame(msg_frame)
            button_frame.pack(pady=10)
            
            def view_file():
                dialog.destroy()
                try:
                    with open(filename, 'r') as f:
                        content = f.read()
                    self.checking_results.insert('end', f"\n=== Contents of {filename} ===\n")
                    self.checking_results.insert('end', content)
                    self.checking_results.insert('end', "\n" + "="*50 + "\n")
                except Exception as e:
                    self.checking_results.insert('end', f"\nError reading {filename}: {str(e)}\n")
            
            def delete_file():
                dialog.destroy()
                try:
                    os.remove(filename)
                    self.checking_results.insert('end', f"\nFile {filename} deleted successfully.\n")
                except Exception as e:
                    self.checking_results.insert('end', f"\nError deleting {filename}: {str(e)}\n")
            
            def cancel():
                dialog.destroy()
            
            # Add buttons
            ttk.Button(button_frame, text="View", command=view_file).pack(side='left', padx=5)
            ttk.Button(button_frame, text="Delete", command=delete_file).pack(side='left', padx=5)
            ttk.Button(button_frame, text="Cancel", command=cancel).pack(side='left', padx=5)
            
            # Apply theme to dialog
            if hasattr(self, 'bg_color') and self.bg_color:
                dialog.configure(bg=self.bg_color)
                for widget in [msg_frame, button_frame]:
                    widget.configure(style='TFrame')
            
            return
        elif filename == "found_keys.txt" and os.path.exists(filename):
            # Create a custom dialog window for found keys
            dialog = tk.Toplevel(self.root)
            dialog.title("Found Keys File")
            dialog.geometry("400x200")
            dialog.transient(self.root)
            dialog.grab_set()
            
            # Center the dialog
            dialog.update_idletasks()
            width = dialog.winfo_width()
            height = dialog.winfo_height()
            x = (dialog.winfo_screenwidth() // 2) - (width // 2)
            y = (dialog.winfo_screenheight() // 2) - (height // 2)
            dialog.geometry(f'{width}x{height}+{x}+{y}')
            
            # Create message
            msg_frame = ttk.Frame(dialog, padding="10")
            msg_frame.pack(fill='both', expand=True)
            
            # Get file size and last modified time
            try:
                size_bytes = os.path.getsize(filename)
                size_kb = size_bytes / 1024
                size_mb = size_kb / 1024
                if size_mb >= 1:
                    size_str = f"{size_mb:.2f} MB"
                elif size_kb >= 1:
                    size_str = f"{size_kb:.2f} KB"
                else:
                    size_str = f"{size_bytes} bytes"
                
                timestamp = os.path.getmtime(filename)
                dt_object = datetime.datetime.fromtimestamp(timestamp)
                modified_str = dt_object.strftime('%Y-%m-%d %H:%M:%S')
                
                # Count number of keys
                with open(filename, 'r') as f:
                    key_count = sum(1 for line in f if line.strip())
                
                message = f"Found Keys File Detected!\n\n"
                message += f"File Size: {size_str}\n"
                message += f"Last Modified: {modified_str}\n"
                message += f"Number of Keys: {key_count:,}\n\n"
                message += "What would you like to do?"
                
            except Exception as e:
                message = f"Found Keys File Detected!\n\nError getting file details: {str(e)}\n\nWhat would you like to do?"
            
            ttk.Label(msg_frame, text=message).pack(pady=10)
            
            # Create buttons frame
            button_frame = ttk.Frame(msg_frame)
            button_frame.pack(pady=10)
            
            def view_file():
                dialog.destroy()
                try:
                    with open(filename, 'r') as f:
                        content = f.read()
                    self.checking_results.insert('end', f"\n=== Found Keys ({key_count:,} keys) ===\n")
                    self.checking_results.insert('end', content)
                    self.checking_results.insert('end', "\n" + "="*50 + "\n")
                except Exception as e:
                    self.checking_results.insert('end', f"\nError reading {filename}: {str(e)}\n")
            
            def delete_file():
                dialog.destroy()
                try:
                    os.remove(filename)
                    self.checking_results.insert('end', f"\nFile {filename} deleted successfully.\n")
                except Exception as e:
                    self.checking_results.insert('end', f"\nError deleting {filename}: {str(e)}\n")
            
            def cancel():
                dialog.destroy()
            
            # Add buttons
            ttk.Button(button_frame, text="View Keys", command=view_file).pack(side='left', padx=5)
            ttk.Button(button_frame, text="Delete File", command=delete_file).pack(side='left', padx=5)
            ttk.Button(button_frame, text="Cancel", command=cancel).pack(side='left', padx=5)
            
            # Apply theme to dialog
            if hasattr(self, 'bg_color') and self.bg_color:
                dialog.configure(bg=self.bg_color)
                for widget in [msg_frame, button_frame]:
                    widget.configure(style='TFrame')
            
            return

        try:
            if os.path.exists(filename):
                with open(filename, 'r') as f:
                    content = f.read()
                self.checking_results.insert('end', f"\n=== Contents of {filename} ===\n")
                self.checking_results.insert('end', content)
                self.checking_results.insert('end', "\n" + "="*50 + "\n")
            else:
                self.checking_results.insert('end', f"\nFile {filename} not found.\n")
        except Exception as e:
            self.checking_results.insert('end', f"\nError reading {filename}: {str(e)}\n")
        
        self.notebook.select(1)  # Switch to checking tab

    def clear_checking_results(self):
        self.checking_results.delete('1.0', 'end')

    def check_key(self):
        key = self.key_input.get().strip()
        if not key:
            messagebox.showerror("Input Error", "Please enter a private key")
            return
        
        # Disable the check button while processing
        self.check_button.config(state='disabled')
        
        if key.lower().startswith("0x"):
            key = key[2:]
        
        command = ["python", "main.py", "--check-key", key]
        command.extend(["--addresses-file", self.addresses_entry.get()])
        
        # Clear previous results
        self.checking_results.delete('1.0', 'end')
        self.checking_results.insert('end', f"Checking key: {key}\n")
        
        # Create and start the search thread
        self.search_thread = SearchThread(command, self.output_queue, self.progress_queue)
        self.search_thread.start()
        
        # Switch to checking tab
        self.notebook.select(1)
        
        # Re-enable the check button after a short delay
        self.root.after(1000, self.enable_check_button)
        
    def enable_check_button(self):
        """Re-enable the check button"""
        self.check_button.config(state='normal')

    def clear_output(self):
        self.output_text.delete('1.0', 'end')

    def show_search_help(self):
        help_text = """Bitcoin Private Key Search Tool - Help Guide

1. Key Space Configuration:
   • Enter the range in hexadecimal format (e.g., 1:FFFFFFFF)
   • Use the Bits slider to quickly set the range size
   • Larger ranges will take longer to scan

2. Search Modes:
   • Sequential Scanning:
     - Scans keys in order from start to end
     - Best for systematic searching
     - Can resume from last position
     - Good for targeted ranges

   • Random Scanning:
     - Scans keys in random order
     - Better for large ranges
     - No resuming capability
     - Good for lottery-style searching

   • Dance Scanning:
     - Alternates between sequential and random
     - Combines benefits of both modes
     - Some processes use sequential, others random
     - Good for balanced searching

3. Address Formats:
   • Compressed:
     - Standard Bitcoin address format
     - Starts with 1 (Note: This program only searches for addresses starting with 1)
     - Most common format
     - Recommended for most searches

   • Uncompressed:
     - Legacy Bitcoin address format
     - Starts with 1
     - Less common but still used
     - Check if needed

   • Both:
     - Checks both compressed and uncompressed
     - Slower but more thorough
     - Use when unsure of format
     - Recommended for important searches

4. CPU Configuration:
   • Number of CPU Cores:
     - Use all available cores for maximum speed
     - Reduce cores if system becomes unresponsive
     - More cores = faster scanning
     - Each core works on a portion of the range

5. Target Addresses:
   • Load addresses from file:
     - Supports .txt and .bf (Bloom Filter) files
     - One address per line in .txt files
     - Bloom filters are more efficient for large lists
     - File size and address count are displayed
     - Note: Only addresses starting with "1" will be checked

6. Tips for Effective Searching:
   • Start with smaller ranges to test
   • Use appropriate address format
   • Monitor system resources
   • Save progress regularly
   • Use appropriate search mode for your needs
   • Remember: Only addresses starting with "1" are supported

7. Progress and Results:
   • Progress bar shows completion
   • Elapsed time is displayed
   • Found keys are saved automatically
   • Check the Output tab for details

For more information or support, visit the project documentation."""
        
        # Create a custom help window with scrollable text
        help_window = tk.Toplevel(self.root)
        help_window.title("Search Modes Help")
        help_window.geometry("800x600")
        
        # Get current theme colors with fallback values
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            if config.has_section('Theme'):
                bg_color = config.get('Theme', 'background', fallback='#1E1E1E')
                text_color = config.get('Theme', 'text', fallback='#E0E0E0')
            else:
                bg_color = '#1E1E1E'
                text_color = '#E0E0E0'
        except Exception:
            bg_color = '#1E1E1E'
            text_color = '#E0E0E0'
        
        # Create a frame for the text and scrollbar
        frame = ttk.Frame(help_window)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create a text widget with scrollbar
        text_widget = tk.Text(
            frame,
            wrap=tk.WORD,
            padx=10,
            pady=10,
            bg=bg_color,
            fg=text_color,
            insertbackground=text_color,  # Cursor color
            selectbackground=text_color,  # Selection background
            selectforeground=bg_color,    # Selection text color
            font=('TkDefaultFont', 10)    # Use system default font
        )
        
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        
        # Pack the widgets
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Insert the help text
        text_widget.insert(tk.END, help_text)
        text_widget.configure(state='disabled')  # Make text read-only
        
        # Add a close button
        close_button = ttk.Button(help_window, text="Close", command=help_window.destroy)
        close_button.pack(pady=10)
        
        # Apply theme to the help window
        help_window.configure(bg=bg_color)
        frame.configure(style='TFrame')
        close_button.configure(style='TButton')
        
        # Make the window modal
        help_window.transient(self.root)
        help_window.grab_set()
        
        # Center the window
        help_window.update_idletasks()
        width = help_window.winfo_width()
        height = help_window.winfo_height()
        x = (help_window.winfo_screenwidth() // 2) - (width // 2)
        y = (help_window.winfo_screenheight() // 2) - (height // 2)
        help_window.geometry(f'{width}x{height}+{x}+{y}')

    def browse_addresses_file(self):
        file_path = filedialog.askopenfilename(
            title="Select Addresses File",
            filetypes=[("Text Files", "*.bf"), ("All Files", "*.*")]
        )
        if file_path:
            self.addresses_entry.delete(0, 'end')
            self.addresses_entry.insert(0, file_path)
            self.update_address_count_label()

    def update_address_count_label(self):
        file_path = self.addresses_entry.get()
        if not file_path:
            self.address_count_label.config(text="No file selected.")
            return

        if os.path.exists(file_path):
            file_info_parts = []
            try:
                # Get file size
                size_bytes = os.path.getsize(file_path)
                size_kb = size_bytes / 1024
                size_mb = size_kb / 1024

                if size_mb >= 1:
                    file_info_parts.append(f"Size: {size_mb:.2f} MB")
                elif size_kb >= 1:
                    file_info_parts.append(f"Size: {size_kb:.2f} KB")
                else:
                    file_info_parts.append(f"Size: {size_bytes} bytes")

                # Get last modified date
                timestamp = os.path.getmtime(file_path)
                dt_object = datetime.datetime.fromtimestamp(timestamp)
                file_info_parts.append(f"Modified: {dt_object.strftime('%Y-%m-%d %H:%M:%S')}")

            except Exception as e:
                file_info_parts.append(f"Error getting file info: {str(e)}")

            file_info_str = f" ({', '.join(file_info_parts)})" if file_info_parts else ""

            try:
                # Attempt to load the file and get the count
                addfind = None
                if file_path.lower().endswith(".bf"):
                    from bloomfilter import BloomFilter
                    with open(file_path, "rb") as fp:
                        addfind = BloomFilter.load(fp)
                elif file_path.lower().endswith(".txt"):
                    with open(file_path, "r", encoding='utf-8', errors='ignore') as file:
                        addresses = file.read().split()
                        addfind = [addr for addr in addresses if addr.strip()]
                else:
                    self.address_count_label.config(text=f"Unsupported file type.{file_info_str}")
                    return

                if addfind is not None:
                    count = len(addfind)
                    count_message = f"{locale.format_string('%d', count, grouping=True)} addresses loaded."
                    self.address_count_label.config(text=f"{count_message}{file_info_str}")
                else:
                    self.address_count_label.config(text=f"Could not load addresses.{file_info_str}")

            except Exception as e:
                error_message = f"Error loading addresses: {str(e)}"
                self.address_count_label.config(text=f"{error_message}{file_info_str}")

        else:
            self.address_count_label.config(text="File not found.")

    def new_window(self):
        new_window = MainWindow()
        new_window.root.mainloop()

    def open_settings(self):
        # Create a new top-level window
        settings_window = tk.Toplevel(self.root)
        settings_window.title("Settings")
        settings_window.geometry("500x400")
        
        # Create main frame
        main_frame = ttk.Frame(settings_window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Theme Section
        theme_frame = ttk.LabelFrame(main_frame, text="Theme Settings", padding="5")
        theme_frame.pack(fill=tk.X, pady=5)
        
        # Theme Selection
        theme_select_frame = ttk.Frame(theme_frame)
        theme_select_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(theme_select_frame, text="Select Theme:").pack(side=tk.LEFT, padx=5)
        self.theme_combo = ttk.Combobox(theme_select_frame, width=20)
        self.theme_combo['values'] = [
            "Default Dark",
            "Default Light",
            "Blue Dark",
            "Blue Light",
            "Green Dark",
            "Green Light",
            "Purple Dark",
            "Purple Light",
            "Solarized Dark",
            "Solarized Light",
            "Monokai",
            "Nord",
            "Custom"
        ]
        self.theme_combo.set("Default Dark")
        self.theme_combo.pack(side=tk.LEFT, padx=5)
        self.theme_combo.bind('<<ComboboxSelected>>', self.preview_theme)
        
        # Custom Theme Colors
        custom_frame = ttk.LabelFrame(theme_frame, text="Custom Theme Colors", padding="5")
        custom_frame.pack(fill=tk.X, pady=5)
        
        # Background Color
        bg_frame = ttk.Frame(custom_frame)
        bg_frame.pack(fill=tk.X, pady=2)
        ttk.Label(bg_frame, text="Background:").pack(side=tk.LEFT, padx=5)
        self.bg_color = ttk.Entry(bg_frame, width=10)
        self.bg_color.insert(0, "#000000")
        self.bg_color.pack(side=tk.LEFT, padx=5)
        ttk.Button(bg_frame, text="Pick", command=lambda: self.pick_color("bg")).pack(side=tk.LEFT, padx=5)
        
        # Text Color
        text_frame = ttk.Frame(custom_frame)
        text_frame.pack(fill=tk.X, pady=2)
        ttk.Label(text_frame, text="Text:").pack(side=tk.LEFT, padx=5)
        self.text_color = ttk.Entry(text_frame, width=10)
        self.text_color.insert(0, "#FFFFFF")
        self.text_color.pack(side=tk.LEFT, padx=5)
        ttk.Button(text_frame, text="Pick", command=lambda: self.pick_color("text")).pack(side=tk.LEFT, padx=5)
        
        # Button Color
        button_frame = ttk.Frame(custom_frame)
        button_frame.pack(fill=tk.X, pady=2)
        ttk.Label(button_frame, text="Button:").pack(side=tk.LEFT, padx=5)
        self.button_color = ttk.Entry(button_frame, width=10)
        self.button_color.insert(0, "#404040")
        self.button_color.pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Pick", command=lambda: self.pick_color("button")).pack(side=tk.LEFT, padx=5)
        
        # Preview Section
        preview_frame = ttk.LabelFrame(main_frame, text="Theme Preview", padding="5")
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.preview_text = tk.Text(preview_frame, wrap=tk.WORD, height=5, bg='black', fg='white')
        self.preview_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.preview_text.insert(tk.END, "This is a preview of how the theme will look.\n")
        self.preview_text.insert(tk.END, "The console output will use these colors.")
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=5)
        
        def apply_current_theme():
            theme = self.theme_combo.get()
            if theme == "Custom":
                colors = {
                    'bg': self.bg_color.get(),
                    'text': self.text_color.get(),
                    'button': self.button_color.get()
                }
            else:
                colors = self.get_theme_colors(theme)
            self.apply_theme(colors)
        
        def save_current_theme():
            theme = self.theme_combo.get()
            if theme == "Custom":
                colors = {
                    'bg': self.bg_color.get(),
                    'text': self.text_color.get(),
                    'button': self.button_color.get()
                }
            else:
                colors = self.get_theme_colors(theme)
            self.save_theme_to_config(colors)
            self.apply_theme(colors)
            messagebox.showinfo("Theme Saved", f"{theme} theme has been saved!")
        
        def reset_to_default():
            self.theme_combo.set("Default Dark")
            colors = self.get_theme_colors("Default Dark")
            self.preview_theme()
            self.apply_theme(colors)
        
        ttk.Button(button_frame, text="Apply Theme", command=apply_current_theme).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Save Theme", command=save_current_theme).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Reset to Default", command=reset_to_default).pack(side=tk.LEFT, padx=5)
        
        # Load current theme
        self.load_and_apply_theme()

    def pick_color(self, color_type):
        color = colorchooser.askcolor(title="Choose Color")[1]
        if color:
            if color_type == "bg":
                self.bg_color.delete(0, tk.END)
                self.bg_color.insert(0, color)
            elif color_type == "text":
                self.text_color.delete(0, tk.END)
                self.text_color.insert(0, color)
            elif color_type == "button":
                self.button_color.delete(0, tk.END)
                self.button_color.insert(0, color)
            self.preview_theme()

    def preview_theme(self, event=None):
        theme = self.theme_combo.get()
        if theme == "Custom":
            colors = {
                'bg': self.bg_color.get(),
                'text': self.text_color.get(),
                'button': self.button_color.get()
            }
        else:
            colors = self.get_theme_colors(theme)
        
        self.preview_text.configure(bg=colors['bg'], fg=colors['text'])
        self.style.configure('TButton', background=colors['button'])
        self.style.configure('TLabel', background=colors['bg'], foreground=colors['text'])
        self.style.configure('TFrame', background=colors['bg'])
        self.style.configure('TLabelframe', background=colors['bg'])
        self.style.configure('TLabelframe.Label', background=colors['bg'], foreground=colors['text'])

    def get_theme_colors(self, theme):
        themes = {
            "Default Dark": {
                'bg': '#1E1E1E',  # Dark gray
                'text': '#E0E0E0',  # Light gray
                'button': '#2D2D2D'  # Slightly lighter gray
            },
            "Default Light": {
                'bg': '#F5F5F5',  # Light gray
                'text': '#2D2D2D',  # Dark gray
                'button': '#E0E0E0'  # Medium gray
            },
            "Blue Dark": {
                'bg': '#0D1117',  # GitHub dark blue
                'text': '#58A6FF',  # Bright blue
                'button': '#1F6FEB'  # Medium blue
            },
            "Blue Light": {
                'bg': '#F0F8FF',  # Alice blue
                'text': '#0066CC',  # Deep blue
                'button': '#B0E0E6'  # Powder blue
            },
            "Green Dark": {
                'bg': '#0A1929',  # Dark navy
                'text': '#00FF9D',  # Bright mint
                'button': '#00CC7E'  # Medium mint
            },
            "Green Light": {
                'bg': '#F0FFF0',  # Honeydew
                'text': '#228B22',  # Forest green
                'button': '#98FB98'  # Pale green
            },
            "Purple Dark": {
                'bg': '#1A1B26',  # Dark navy
                'text': '#BB9AF7',  # Light purple
                'button': '#7AA2F7'  # Medium purple
            },
            "Purple Light": {
                'bg': '#F8F0FF',  # Light lavender
                'text': '#663399',  # Rebecca purple
                'button': '#E6E6FA'  # Lavender
            },
            "Solarized Dark": {
                'bg': '#002B36',  # Dark teal
                'text': '#93A1A1',  # Light gray
                'button': '#073642'  # Darker teal
            },
            "Solarized Light": {
                'bg': '#FDF6E3',  # Light cream
                'text': '#657B83',  # Dark gray
                'button': '#EEE8D5'  # Light cream
            },
            "Monokai": {
                'bg': '#272822',  # Dark gray
                'text': '#F8F8F2',  # Off-white
                'button': '#3E3D32'  # Medium gray
            },
            "Nord": {
                'bg': '#2E3440',  # Dark blue-gray
                'text': '#ECEFF4',  # Light gray
                'button': '#3B4252'  # Medium blue-gray
            }
        }
        return themes.get(theme, themes["Default Dark"])

    def save_theme(self):
        theme = self.theme_combo.get()
        if theme == "Custom":
            colors = {
                'bg': self.bg_color.get(),
                'text': self.text_color.get(),
                'button': self.button_color.get()
            }
        else:
            colors = self.get_theme_colors(theme)
        self.save_theme_to_config(colors)
        self.apply_theme(colors)
        messagebox.showinfo("Theme Saved", f"{theme} theme has been saved!")

    def save_theme_to_config(self, colors):
        config = configparser.ConfigParser()
        config_file = 'config.ini'
        
        print(f"Saving theme to {config_file}")
        print(f"Colors to save - BG: {colors['bg']}, Text: {colors['text']}, Button: {colors['button']}")
        
        if os.path.exists(config_file):
            config.read(config_file)
        
        if not config.has_section('Theme'):
            config.add_section('Theme')
        
        config['Theme']['background'] = colors['bg']
        config['Theme']['text'] = colors['text']
        config['Theme']['button'] = colors['button']
        
        try:
            with open(config_file, 'w') as f:
                config.write(f)
            print("Theme saved successfully")
        except Exception as e:
            print(f"Error saving theme: {str(e)}")

    def reset_theme(self):
        self.theme_combo.set("Default Dark")
        colors = self.get_theme_colors("Default Dark")
        self.preview_theme()
        self.apply_theme(colors)

    def exit_app(self):
        if self.search_thread and self.search_thread.is_running:
            self.stop_search()
            self.root.after(1000, self.root.destroy)
        else:
            self.root.destroy()

    def about(self):
        messagebox.showinfo("About",
            "Bitcoin Private Key Search\n\n"
            "A powerful tool for searching Bitcoin private keys.\n\n"
            "Features:\n"
            "• Default Scanning\n"
            "• Random Search\n"
            "• Brute Force Sequential Search\n"
            "• Private Key Verification\n"
            "• Target Address Checking (Addresses starting with '1' only)\n\n"
            "Note: This program only searches for and verifies Bitcoin addresses\n"
            "that start with '1' (both compressed and uncompressed formats).\n\n"
            "Version 1.0\n"
            "© 2025 Mizogg")

    def update_bits(self, value):
        """Update the bits entry and keyspace range based on slider value"""
        if not hasattr(self, 'bits_entry'):
            return
            
        try:
            bits = int(float(value))
            self.bits_entry.delete(0, 'end')
            self.bits_entry.insert(0, str(bits))
            
            # Calculate the range
            if bits == 256:
                start_hex = "8000000000000000000000000000000000000000000000000000000000000000"
                end_hex = "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364140"
            else:
                start_range = 2 ** (bits - 1)
                end_range = 2 ** bits - 1
                start_hex = f"{start_range:X}"
                end_hex = f"{end_range:X}"
            
            # Update the keyspace entry
            self.keyspace_entry.delete(0, 'end')
            self.keyspace_entry.insert(0, f"{start_hex}:{end_hex}")
        except Exception as e:
            print(f"Error updating bits: {e}")

    def on_bits_entry_change(self, event):
        """Handle real-time changes to the bits entry field"""
        # Cancel any pending validation
        if self.bits_validation_timer:
            self.root.after_cancel(self.bits_validation_timer)
        
        # Schedule new validation after 500ms
        self.bits_validation_timer = self.root.after(500, self.validate_and_update_bits)

    def validate_and_update_bits(self):
        """Validate and update bits after typing delay"""
        try:
            text = self.bits_entry.get()
            if text:  # Only process if there's text
                bits = int(text)
                # Validate bits range
                bits = max(1, min(bits, 256))
                
                # Update slider
                self.bits_slider.set(bits)
                
                # Calculate the range
                if bits == 256:
                    start_hex = "8000000000000000000000000000000000000000000000000000000000000000"
                    end_hex = "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364140"
                else:
                    start_range = 2 ** (bits - 1)
                    end_range = 2 ** bits - 1
                    start_hex = f"{start_range:X}"
                    end_hex = f"{end_range:X}"
                
                # Update the keyspace entry
                self.keyspace_entry.delete(0, 'end')
                self.keyspace_entry.insert(0, f"{start_hex}:{end_hex}")
                
                # Update bits entry with validated value
                self.bits_entry.delete(0, 'end')
                self.bits_entry.insert(0, str(bits))
        except ValueError:
            # Don't update if invalid input
            pass

    def update_from_entry(self, event=None):
        """Update the slider and keyspace range based on bits entry value"""
        try:
            bits = int(self.bits_entry.get())
            # Validate bits range
            bits = max(1, min(bits, 256))
            
            # Update slider
            self.bits_slider.set(bits)
            
            # Calculate the range
            if bits == 256:
                start_hex = "8000000000000000000000000000000000000000000000000000000000000000"
                end_hex = "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364140"
            else:
                start_range = 2 ** (bits - 1)
                end_range = 2 ** bits - 1
                start_hex = f"{start_range:X}"
                end_hex = f"{end_range:X}"
            
            # Update the keyspace entry
            self.keyspace_entry.delete(0, 'end')
            self.keyspace_entry.insert(0, f"{start_hex}:{end_hex}")
            
            # Update bits entry with validated value
            self.bits_entry.delete(0, 'end')
            self.bits_entry.insert(0, str(bits))
            
        except ValueError:
            # Reset to default if invalid value
            self.bits_entry.delete(0, 'end')
            self.bits_entry.insert(0, "71")
            self.bits_slider.set(71)
            self.update_bits(71)

    def check_address(self):
        """Check if an address exists in the loaded address file"""
        address = self.address_input.get().strip()
        if not address:
            messagebox.showerror("Input Error", "Please enter a Bitcoin address")
            return
        
        # Validate address format
        if not address.startswith('1'):
            messagebox.showerror("Input Error", "Only addresses starting with '1' are supported")
            return
        
        file_path = self.addresses_entry.get()
        if not file_path or not os.path.exists(file_path):
            messagebox.showerror("Error", "No address file loaded or file not found")
            return
        
        # Disable the check button while processing
        self.check_address_button.config(state='disabled')
        
        try:
            # Clear previous results
            self.checking_results.delete('1.0', 'end')
            self.checking_results.insert('end', f"Checking address: {address}\n")
            
            # Check if address exists in file
            found = False
            if file_path.lower().endswith(".bf"):
                from bloomfilter import BloomFilter
                with open(file_path, "rb") as fp:
                    addfind = BloomFilter.load(fp)
                    found = address in addfind
            elif file_path.lower().endswith(".txt"):
                with open(file_path, "r", encoding='utf-8', errors='ignore') as file:
                    addresses = file.read().split()
                    found = address in addresses
            else:
                self.checking_results.insert('end', "Unsupported file type. Please use .bf or .txt files.\n")
                return
            
            # Display result
            if found:
                self.checking_results.insert('end', f"\n✅ Address FOUND in the loaded file!\n")
            else:
                self.checking_results.insert('end', f"\n❌ Address NOT found in the loaded file.\n")
            
            # Add file information
            size_bytes = os.path.getsize(file_path)
            size_kb = size_bytes / 1024
            size_mb = size_kb / 1024
            
            if size_mb >= 1:
                size_str = f"{size_mb:.2f} MB"
            elif size_kb >= 1:
                size_str = f"{size_kb:.2f} KB"
            else:
                size_str = f"{size_bytes} bytes"
            
            self.checking_results.insert('end', f"\nFile Information:\n")
            self.checking_results.insert('end', f"File: {os.path.basename(file_path)}\n")
            self.checking_results.insert('end', f"Size: {size_str}\n")
            
        except Exception as e:
            self.checking_results.insert('end', f"\nError checking address: {str(e)}\n")
        
        # Re-enable the check button
        self.check_address_button.config(state='normal')

if __name__ == "__main__":
    app = MainWindow()
    app.root.mainloop() 
