import io
import wave
import threading
import re
import csv
import configparser
import numpy as np
import sounddevice as sd
from piper import PiperVoice
import tkinter as tk
from tkinter import ttk
import sys
import os

class PiperTTSGUI:
    def __init__(self, root, config_path="voice.cfg", csv_path="custom_pronunciation.csv"):
        self.root = root
        self.root.title("Piper TTS - Text to Speech")
        self.root.geometry("600x120")
        self.root.minsize(400, 120)
        
        # Load configuration (only model path now)
        self.config = self._load_config(config_path)
        
        # Extract voice model path
        model_path = self.config.get('voice', 'model_path', fallback="./en_US-kristin-medium.onnx")
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Voice model not found: {model_path}")
        
        # Load voice model once at startup
        print(f"Loading voice model: {model_path}")
        self.voice = PiperVoice.load(model_path)
        print("Voice model loaded successfully!")
        
        # Load custom pronunciations
        self.pronunciation_dict = self._load_pronunciations(csv_path)
        
        # Threading control
        self.speech_thread = None
        self.stop_event = threading.Event()
        
        self._setup_gui()
        
    def _load_config(self, config_path):
        """Load configuration from .cfg file with fallback defaults"""
        config = configparser.ConfigParser()
        
        if not os.path.exists(config_path):
            print(f"Config file '{config_path}' not found. Creating default configuration...")
            self._create_default_config(config_path)
        
        config.read(config_path)
        return config
    
    def _create_default_config(self, config_path):
        """Create a default configuration file with only model path"""
        config = configparser.ConfigParser()
        config['voice'] = {
            'model_path': './en_US-kristin-medium.onnx'
        }
        
        with open(config_path, 'w') as f:
            config.write(f)
        
        print(f"Default configuration written to {config_path}")
    
    def _load_pronunciations(self, csv_path):
        """Load custom pronunciation dictionary from CSV file"""
        pron_dict = {}
        
        if not os.path.exists(csv_path):
            print(f"Pronunciation file '{csv_path}' not found. Creating empty template...")
            self._create_empty_csv(csv_path)
            return pron_dict
        
        try:
            with open(csv_path, 'r', newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    word = row.get('Word', '').strip().lower()
                    ipa = row.get('IPA', '').strip()
                    if word and ipa:
                        pron_dict[word] = ipa
            
            print(f"Loaded {len(pron_dict)} custom pronunciations from {csv_path}")
        except Exception as e:
            print(f"Error loading pronunciation file: {e}")
        
        return pron_dict
    
    def _create_empty_csv(self, csv_path):
        """Create an empty CSV template with headers"""
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Word', 'IPA'])
        print(f"Empty pronunciation template created at {csv_path}")
    
    def _apply_custom_pronunciation(self, text):
        """Replace words with custom IPA pronunciations using case-insensitive matching"""
        if not self.pronunciation_dict:
            return text
        
        def replace_word(match):
            word = match.group(0)
            word_lower = word.lower()
            if word_lower in self.pronunciation_dict:
                return f"[[ {self.pronunciation_dict[word_lower]} ]]"
            return word
        
        # Pattern matches words (including apostrophes) for replacement
        pattern = r"\b[\w']+\b"
        return re.sub(pattern, replace_word, text)
        
    def _setup_gui(self):
        # Configure main window grid
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        main_frame.grid_columnconfigure(0, weight=1)
        
        # Text entry
        self.text_var = tk.StringVar()
        self.text_entry = ttk.Entry(
            main_frame, 
            textvariable=self.text_var, 
            font=('Segoe UI', 11)
        )
        self.text_entry.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        self.text_entry.focus()
        
        # Bind Enter key to speak
        self.text_entry.bind('<Return>', lambda e: self.speak_text())
        
        # Button frame
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        # Speak button
        self.speak_button = ttk.Button(
            button_frame, 
            text="Speak", 
            command=self.speak_text
        )
        self.speak_button.pack(side=tk.LEFT, expand=False)
        
        # Stop button
        self.stop_button = ttk.Button(
            button_frame,
            text="Stop",
            command=self.stop_speech,
            state="disabled"  # Disabled initially
        )
        self.stop_button.pack(side=tk.LEFT, padx=(5, 0), expand=False)
        
        # Status label
        self.status_label = ttk.Label(
            button_frame, 
            text="Ready", 
            foreground="green"
        )
        self.status_label.pack(side=tk.LEFT, padx=(15, 0))
        
    def speak_text(self):
        """Handle speak button click or Enter key press"""
        message = self.text_var.get().strip()
        
        if not message:
            return  # Don't process empty messages
            
        # Apply custom pronunciation replacements
        processed_message = self._apply_custom_pronunciation(message)
        print(f"Original text: '{message}'")
        print(f"Processed text: '{processed_message}'")
        
        # Disable controls and update status
        self._set_controls_state(False)
        self.status_label.config(text="Synthesizing and speaking...", foreground="orange")
        
        # Clear stop event
        self.stop_event.clear()
        
        # Run synthesis in separate thread to avoid GUI freeze
        self.speech_thread = threading.Thread(target=self._synthesize_and_play, args=(processed_message,), daemon=True)
        self.speech_thread.start()
        
    def stop_speech(self):
        """Stop current speech playback"""
        print("Stopping speech...")
        self.stop_event.set()
        sd.stop()  # Stop audio playback immediately
        
    def _synthesize_and_play(self, message):
        """Perform synthesis and playback in a background thread"""
        error_msg = None
        try:
            # Collect all audio chunks
            audio_chunks = []
            for audio_chunk in self.voice.synthesize(message):
                # Check if stop was requested
                if self.stop_event.is_set():
                    print("Synthesis stopped by user")
                    return
                
                audio_chunks.append(audio_chunk.audio_int16_bytes)
            
            # Check if stop was requested before playback
            if self.stop_event.is_set():
                print("Playback stopped by user before starting")
                return
            
            # Combine all chunks
            audio_data = b''.join(audio_chunks)
            
            # Convert to numpy array and play
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            sd.play(audio_array, samplerate=self.voice.config.sample_rate)
            sd.wait()
            
        except Exception as e:
            error_msg = str(e)
            print(f"Error during synthesis or playback: {error_msg}")
        finally:
            # Cleanup must run on main thread
            self.root.after(0, lambda msg=error_msg: self._cleanup_after_speaking(msg))
            
    def _cleanup_after_speaking(self, error_msg=None):
        """Re-enable controls and clear text after speaking or stopping"""
        if error_msg:
            self.status_label.config(text=f"Error: {error_msg}", foreground="red")
        else:
            self.status_label.config(text="Ready", foreground="green")
        
        # Clear text and re-enable controls
        self.text_var.set("")
        self.text_entry.focus()
        self._set_controls_state(True)
        
    def _set_controls_state(self, enabled):
        """Enable or disable GUI controls"""
        state = "normal" if enabled else "disabled"
        self.text_entry.config(state=state)
        self.speak_button.config(state=state)
        self.stop_button.config(state="disabled" if enabled else "normal")

def main():
    # Configuration file paths (can be overridden via command line)
    CONFIG_PATH = "voice.cfg"
    CSV_PATH = "custom_pronunciation.csv"
    
    # Check for command line arguments
    if len(sys.argv) > 1:
        CONFIG_PATH = sys.argv[1]
    if len(sys.argv) > 2:
        CSV_PATH = sys.argv[2]
    
    # Create and run GUI
    root = tk.Tk()
    try:
        app = PiperTTSGUI(root, CONFIG_PATH, CSV_PATH)
        root.mainloop()
    except Exception as e:
        print(f"Failed to start application: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()