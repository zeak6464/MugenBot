import os
import random
import subprocess
import json
import time
from pathlib import Path
from typing import List, Dict, Optional
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkinter.scrolledtext import ScrolledText
import webbrowser
from PIL import Image, ImageTk
import matplotlib.pyplot as plt
import matplotlib.dates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from datetime import datetime, timedelta
import numpy as np
from twitchio.ext import commands
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

class MugenBattleManager:
    def __init__(self):
        self.mugen_path = Path("mugen.exe")
        self.chars_path = Path("chars")
        self.stages_path = Path("stages")
        
        # Add MugenWatcher initialization
        self.watcher_path = Path("MugenWatcher.exe")
        self.watcher_log = Path("MugenWatcher.Log")
        self.watcher_process = None  # Initialize watcher process as None
        
        # Stats tracking
        self.stats_file = Path("battle_stats.json")
        self.stage_stats_file = Path("stage_stats.json")  # Add separate file for stage stats
        self.character_stats = self.load_stats(self.stats_file)
        self.stage_stats = self.load_stats(self.stage_stats_file)
        
        # Battle settings
        self.settings = {
            "rounds": 1,
            "p2_color": 1,
            "battle_mode": "single",  # single, team, turns
            "team_size": 3,
            "continuous_mode": True
        }
        
        # Character and stage cache
        self.characters = self.scan_characters()
        self.stages = self.scan_stages()
        
        # Added fields
        self.stage_stats = {}  # Track stage usage
        self.settings["enabled_characters"] = set(self.characters)  # Initially enable all characters
        self.settings["enabled_stages"] = set(self.stages)  # Initially enable all stages

        # Add battle history tracking
        self.battle_history_file = Path("battle_history.json")
        self.battle_history = self.load_battle_history()

        self.ensure_watcher_running()  # Add this line to check watcher on startup

    def scan_characters(self) -> List[str]:
        """Scan for available characters"""
        chars = []
        for char_dir in self.chars_path.iterdir():
            if char_dir.is_dir():
                def_file = char_dir / f"{char_dir.name}.def"
                if def_file.exists():
                    chars.append(char_dir.name)
        return chars

    def scan_stages(self) -> List[str]:
        """Scan for available stages"""
        return [f.stem for f in self.stages_path.glob("*.def")]

    def load_stats(self, file_path: Path) -> Dict:
        """Load statistics from JSON"""
        if file_path.exists():
            try:
                with open(file_path) as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def save_stats(self):
        """Save all statistics to JSON"""
        # Save character stats
        with open(self.stats_file, 'w') as f:
            json.dump(self.character_stats, f, indent=2)
        
        # Save stage stats
        with open(self.stage_stats_file, 'w') as f:
            json.dump(self.stage_stats, f, indent=2)

    def update_stats(self, winner: str, loser: str):
        """Update win/loss statistics"""
        # Update character stats
        for char in [winner, loser]:
            if char not in self.character_stats:
                self.character_stats[char] = {"wins": 0, "losses": 0}
        
        self.character_stats[winner]["wins"] += 1
        self.character_stats[loser]["losses"] += 1

        # Save immediately after update
        self.save_stats()

    def update_stage_stats(self, stage: str):
        """Update stage usage statistics"""
        if stage not in self.stage_stats:
            self.stage_stats[stage] = {
                "times_used": 0,
                "last_used": None
            }
        
        self.stage_stats[stage]["times_used"] += 1
        self.stage_stats[stage]["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")
        
        # Save immediately after update
        self.save_stats()

    def get_character_tier(self, char_name: str) -> str:
        """Calculate character tier based on win rate"""
        if char_name not in self.character_stats:
            return "Unranked"
            
        stats = self.character_stats[char_name]
        total_matches = stats["wins"] + stats["losses"]
        if total_matches < 10:
            return "Unranked"
            
        win_rate = stats["wins"] / total_matches
        
        if win_rate >= 0.7: return "S"
        elif win_rate >= 0.6: return "A"
        elif win_rate >= 0.5: return "B"
        elif win_rate >= 0.4: return "C"
        else: return "D"

    def start_battle(self):
        """Start a MUGEN battle with current settings"""
        if not self.settings["enabled_characters"]:
            raise ValueError("No characters are enabled!")
        if not self.settings["enabled_stages"]:
            raise ValueError("No stages are enabled!")

        # Clean up any existing processes
        if self.watcher_process:
            self.watcher_process.terminate()
            self.watcher_process = None
        if self.watcher_log.exists():
            self.watcher_log.unlink()

        enabled_chars = list(self.settings["enabled_characters"])
        enabled_stages = list(self.settings["enabled_stages"])
        stage = random.choice(enabled_stages)

        # Start MUGEN first
        if self.settings["battle_mode"] == "single":
            battle_info = self._start_single_battle(enabled_chars, stage)
        elif self.settings["battle_mode"] == "team":
            battle_info = self._start_team_battle(enabled_chars, stage)
        elif self.settings["battle_mode"] == "turns":
            battle_info = self._start_turns_battle(enabled_chars, stage)
        elif self.settings["battle_mode"] == "simul":
            battle_info = self._start_simul_battle(enabled_chars, stage)
        else:
            raise ValueError(f"Unknown battle mode: {self.settings['battle_mode']}")

        # Wait a moment for MUGEN to start
        time.sleep(1)

        # Start MugenWatcher only if MUGEN is running
        if self._check_mugen_running():
            self.ensure_watcher_running()

        return battle_info

    def _check_mugen_running(self) -> bool:
        """Check if MUGEN is still running"""
        try:
            # Look for mugen.exe in running processes
            output = subprocess.check_output('tasklist', shell=True).decode()
            return "mugen.exe" in output.lower()
        except:
            return False

    def check_battle_result(self) -> Optional[Dict]:
        """Check the result of the current battle"""
        if not hasattr(self, 'current_battle') or not self.current_battle:
            return None

        # Check if MUGEN is still running
        mugen_running = self._check_mugen_running()
        
        if not mugen_running:
            # Try to get final result
            result = self._read_battle_result()
            
            # Clean up watcher
            if self.watcher_process:
                self.watcher_process.terminate()
                self.watcher_process = None

            if result:
                return self._process_battle_result(result)
            
            # Clear current battle state
            self.current_battle = None
            return None

        # MUGEN is still running, check for results
        result = self._read_battle_result()
        if result:
            return self._process_battle_result(result)
        
        return None

    def _process_battle_result(self, result) -> Dict:
        """Process the battle result and update statistics"""
        p1_score, p2_score = result
        
        if self.current_battle["mode"] == "single":
            if p1_score > p2_score:
                winner = self.current_battle["p1"]
                loser = self.current_battle["p2"]
            else:
                winner = self.current_battle["p2"]
                loser = self.current_battle["p1"]
            
            # Update statistics
            self.update_stats(winner, loser)
            self.update_stage_stats(self.current_battle["stage"])

            battle_result = {
                "winner": winner,
                "loser": loser,
                "p1_score": p1_score,
                "p2_score": p2_score,
                "mode": self.current_battle["mode"]
            }

            # Record battle in history
            self.record_battle(battle_result)
            
            # Clean up
            if self.watcher_log.exists():
                self.watcher_log.unlink()
            
            self.current_battle = None
            
            return battle_result
        
        elif self.current_battle["mode"] in ["team", "turns", "simul"]:
            # Handle team battles
            if p1_score > p2_score:
                winner = self.current_battle["p1"]
                loser = self.current_battle["p2"]
            else:
                winner = self.current_battle["p2"]
                loser = self.current_battle["p1"]
            
            # Update team stats
            if isinstance(winner, list):
                for w in winner:
                    for l in loser:
                        self.update_stats(w, l)
            
            self.update_stage_stats(self.current_battle["stage"])

            battle_result = {
                "winner": winner,
                "loser": loser,
                "p1_score": p1_score,
                "p2_score": p2_score,
                "mode": self.current_battle["mode"]
            }

            # Record and clean up
            self.record_battle(battle_result)
            if self.watcher_log.exists():
                self.watcher_log.unlink()
            self.current_battle = None
            
            return battle_result

    def _start_single_battle(self, enabled_chars, stage):
        """Start a single 1v1 battle"""
        p1 = random.choice(enabled_chars)
        p2 = random.choice([c for c in enabled_chars if c != p1])

        cmd = [
            str(self.mugen_path),
            "-rounds", str(self.settings["rounds"]),
            "-p1.ai", "1",
            p1,
            "-p2.ai", "1",
            p2,
            "-p2.color", str(self.settings["p2_color"]),
            "-s", stage
        ]

        self.current_battle = {
            "mode": "single",
            "p1": p1,
            "p2": p2,
            "stage": stage,
            "command": cmd
        }

        subprocess.Popen(cmd)
        return self.current_battle

    def _start_team_battle(self, enabled_chars, stage):
        """Start a team battle where characters fight simultaneously"""
        if len(enabled_chars) < self.settings["team_size"] * 2:
            raise ValueError(f"Not enough characters for {self.settings['team_size']}v{self.settings['team_size']} team battle!")

        # Select teams
        team1 = random.sample(enabled_chars, self.settings["team_size"])
        remaining_chars = [c for c in enabled_chars if c not in team1]
        team2 = random.sample(remaining_chars, self.settings["team_size"])

        cmd = [
            str(self.mugen_path),
            "-rounds", str(self.settings["rounds"]),
            "-p1.ai", "1",
            "-p1.teammember", str(self.settings["team_size"]),
        ]

        # Add team 1 members
        for char in team1:
            cmd.extend([char])

        # Add team 2 configuration
        cmd.extend([
            "-p2.ai", "1",
            "-p2.teammember", str(self.settings["team_size"]),
            "-p2.color", str(self.settings["p2_color"]),
        ])

        # Add team 2 members
        for char in team2:
            cmd.extend([char])

        # Add stage
        cmd.extend(["-s", stage])

        self.current_battle = {
            "mode": "team",
            "p1": team1,
            "p2": team2,
            "stage": stage,
            "command": cmd
        }

        subprocess.Popen(cmd)
        return self.current_battle

    def _start_turns_battle(self, enabled_chars, stage):
        """Start a turns battle where characters fight in sequence"""
        if len(enabled_chars) < self.settings["team_size"] * 2:
            raise ValueError(f"Not enough characters for {self.settings['team_size']}v{self.settings['team_size']} turns battle!")

        # Select teams
        team1 = random.sample(enabled_chars, self.settings["team_size"])
        remaining_chars = [c for c in enabled_chars if c not in team1]
        team2 = random.sample(remaining_chars, self.settings["team_size"])

        cmd = [
            str(self.mugen_path),
            "-rounds", str(self.settings["rounds"]),
            "-p1.ai", "1",
            "-p1.turns", str(self.settings["team_size"]),
        ]

        # Add team 1 members
        for char in team1:
            cmd.extend([char])

        # Add team 2 configuration
        cmd.extend([
            "-p2.ai", "1",
            "-p2.turns", str(self.settings["team_size"]),
            "-p2.color", str(self.settings["p2_color"]),
        ])

        # Add team 2 members
        for char in team2:
            cmd.extend([char])

        # Add stage
        cmd.extend(["-s", stage])

        self.current_battle = {
            "mode": "turns",
            "p1": team1,
            "p2": team2,
            "stage": stage,
            "command": cmd
        }

        subprocess.Popen(cmd)
        return self.current_battle

    def _start_simul_battle(self, enabled_chars, stage):
        """Start a simultaneous battle with different team sizes"""
        # Get team sizes from settings
        team1_size = self.settings.get("team1_size", random.randint(1, 4))
        team2_size = self.settings.get("team2_size", random.randint(1, 4))
        
        total_chars_needed = team1_size + team2_size
        if len(enabled_chars) < total_chars_needed:
            raise ValueError(f"Not enough characters for {team1_size}v{team2_size} simul battle!")

        # Select teams
        team1 = random.sample(enabled_chars, team1_size)
        remaining_chars = [c for c in enabled_chars if c not in team1]
        team2 = random.sample(remaining_chars, team2_size)

        cmd = [
            str(self.mugen_path),
            "-rounds", str(self.settings["rounds"]),
        ]

        # Add team 1 configuration
        cmd.extend([
            "-p1.ai", "1",
            "-p1.simul", str(team1_size),
        ])

        # Add team 1 members with life multipliers for balance
        life_mult = max(1.0, team2_size / team1_size)
        for i, char in enumerate(team1):
            cmd.extend([
                char,
                f"-p1.life.{i+1}", str(life_mult)
            ])

        # Add team 2 configuration
        cmd.extend([
            "-p2.ai", "1",
            "-p2.simul", str(team2_size),
            "-p2.color", str(self.settings["p2_color"]),
        ])

        # Add team 2 members with life multipliers for balance
        life_mult = max(1.0, team1_size / team2_size)
        for i, char in enumerate(team2):
            cmd.extend([
                char,
                f"-p2.life.{i+1}", str(life_mult)
            ])

        # Add stage
        cmd.extend(["-s", stage])

        self.current_battle = {
            "mode": "simul",
            "p1": team1,
            "p2": team2,
            "team1_size": team1_size,
            "team2_size": team2_size,
            "stage": stage,
            "command": cmd
        }

        subprocess.Popen(cmd)
        return self.current_battle

    def _read_battle_result(self) -> Optional[tuple]:
        """Read battle result from MugenWatcher.Log"""
        if not self.watcher_log.exists():
            return None
        
        # Try to read the log file with proper file handling
        for _ in range(3):  # Try up to 3 times
            try:
                # Open with 'r' mode and proper encoding
                with open(self.watcher_log, 'r', encoding='utf-8') as f:
                    line = f.readline().strip()
                    if line:
                        try:
                            # Split by comma and get the scores
                            values = line.split(',')
                            if len(values) >= 4:
                                p1_score = int(values[2])
                                p2_score = int(values[3])
                                
                                # Don't delete the log file - let MugenWatcher handle it
                                return (p1_score, p2_score)
                        except (ValueError, IndexError) as e:
                            print(f"Error parsing battle result: {e}")
                            return None
                return None
            except PermissionError:
                # File is locked, wait a moment and try again
                time.sleep(0.1)
            except Exception as e:
                print(f"Error reading battle result: {e}")
                return None
        
        return None

    def load_battle_history(self) -> Dict:
        """Load battle history from JSON"""
        if self.battle_history_file.exists():
            with open(self.battle_history_file) as f:
                return json.load(f)
        return {
            "battles": [],
            "last_save": None
        }

    def save_battle_history(self):
        """Save battle history to JSON"""
        with open(self.battle_history_file, 'w') as f:
            json.dump(self.battle_history, f, indent=2)

    def record_battle(self, battle_result: Dict):
        """Record battle result in history"""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        battle_record = {
            "timestamp": timestamp,
            "mode": battle_result["mode"],
            "winner": battle_result["winner"],
            "loser": battle_result["loser"],
            "score": f"{battle_result['p1_score']}-{battle_result['p2_score']}",
            "stage": self.current_battle["stage"]
        }
        self.battle_history["battles"].append(battle_record)
        self.battle_history["last_save"] = timestamp
        self.save_battle_history()

    def ensure_watcher_running(self):
        """Ensure MugenWatcher is running and properly initialized"""
        try:
            # Kill any existing watcher process
            if self.watcher_process:
                self.watcher_process.terminate()
                self.watcher_process = None

            # Clean up old log file
            if self.watcher_log.exists():
                self.watcher_log.unlink()

            # Check if MugenWatcher exists
            if not self.watcher_path.exists():
                print("Warning: MugenWatcher.exe not found. Battle results won't be tracked.")
                return False

            # Only start watcher if MUGEN is running
            if self._check_mugen_running():
                self.watcher_process = subprocess.Popen(
                    [str(self.watcher_path)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                time.sleep(0.5)
                return True
            
            return False

        except Exception as e:
            print(f"Error starting MugenWatcher: {e}")
            return False

    def prepare_battle(self):
        """Prepare battle information without starting the battle"""
        if not self.settings["enabled_characters"]:
            raise ValueError("No characters are enabled!")
        if not self.settings["enabled_stages"]:
            raise ValueError("No stages are enabled!")

        enabled_chars = list(self.settings["enabled_characters"])
        enabled_stages = list(self.settings["enabled_stages"])
        stage = random.choice(enabled_stages)

        # Prepare battle info based on mode
        if self.settings["battle_mode"] == "single":
            p1 = random.choice(enabled_chars)
            p2 = random.choice([c for c in enabled_chars if c != p1])
            battle_info = {
                "mode": "single",
                "p1": p1,
                "p2": p2,
                "stage": stage
            }
        elif self.settings["battle_mode"] == "team":
            if len(enabled_chars) < self.settings["team_size"] * 2:
                raise ValueError(f"Not enough characters for {self.settings['team_size']}v{self.settings['team_size']} team battle!")
            team1 = random.sample(enabled_chars, self.settings["team_size"])
            remaining_chars = [c for c in enabled_chars if c not in team1]
            team2 = random.sample(remaining_chars, self.settings["team_size"])
            battle_info = {
                "mode": "team",
                "p1": team1,
                "p2": team2,
                "stage": stage
            }
        elif self.settings["battle_mode"] == "turns":
            if len(enabled_chars) < self.settings["team_size"] * 2:
                raise ValueError(f"Not enough characters for {self.settings['team_size']}v{self.settings['team_size']} turns battle!")
            team1 = random.sample(enabled_chars, self.settings["team_size"])
            remaining_chars = [c for c in enabled_chars if c not in team1]
            team2 = random.sample(remaining_chars, self.settings["team_size"])
            battle_info = {
                "mode": "turns",
                "p1": team1,
                "p2": team2,
                "stage": stage
            }
        elif self.settings["battle_mode"] == "simul":
            team1_size = self.settings.get("team1_size", random.randint(1, 4))
            team2_size = self.settings.get("team2_size", random.randint(1, 4))
            total_chars_needed = team1_size + team2_size
            if len(enabled_chars) < total_chars_needed:
                raise ValueError(f"Not enough characters for {team1_size}v{team2_size} simul battle!")
            team1 = random.sample(enabled_chars, team1_size)
            remaining_chars = [c for c in enabled_chars if c not in team1]
            team2 = random.sample(remaining_chars, team2_size)
            battle_info = {
                "mode": "simul",
                "p1": team1,
                "p2": team2,
                "team1_size": team1_size,
                "team2_size": team2_size,
                "stage": stage
            }
        else:
            raise ValueError(f"Unknown battle mode: {self.settings['battle_mode']}")

        return battle_info


class TwitchBot(commands.Bot):
    def __init__(self, token, channel, battle_gui, bot_name="MugenBattleBot"):
        # Initialize with custom name
        super().__init__(
            token=token,
            prefix='!',
            initial_channels=[channel],
            nick=bot_name,
            initial_nick=bot_name
        )
        self.battle_gui = battle_gui
        self.current_poll = None
        self.betting_open = False
        self.channel_name = channel
        self.points_reward = 100
        self.display_name = bot_name

    async def create_battle_poll(self, title, option1, option2, duration):
        """Create a poll for the battle"""
        try:
            # End any existing poll
            if self.current_poll:
                await self.end_poll()

            # Create new poll
            self.current_poll = await self.create_poll(
                title=title,
                options=[option1, option2],
                duration=duration
            )
            self.betting_open = True
            
            # Announce poll in chat
            channel = self.get_channel(self.channel_name)
            await channel.send(f"New battle poll! Vote for {option1} or {option2}! Winners get {self.points_reward} channel points!")
            
            return True
        except Exception as e:
            print(f"Error creating poll: {e}")
            return False

    async def end_poll(self):
        """End the current poll and reward winners"""
        if self.current_poll and self.betting_open:
            try:
                # Get poll results
                poll_results = await self.current_poll.results()
                winning_option = max(poll_results, key=lambda x: x.votes)
                
                # Get list of users who voted for winning option
                winning_voters = await self.current_poll.voters(winning_option.option)
                
                # Award points to winners
                channel = self.get_channel(self.channel_name)
                for user in winning_voters:
                    try:
                        await channel.add_channel_points(user, self.points_reward)
                    except Exception as e:
                        print(f"Error awarding points to {user}: {e}")

                # Announce winners
                await channel.send(f"Poll ended! {winning_option.option} won! Winners received {self.points_reward} channel points!")
                
                self.betting_open = False
                self.current_poll = None
            except Exception as e:
                print(f"Error ending poll: {e}")

    async def handle_battle_result(self, winner, p1, p2):
        """Handle battle result and award points"""
        if not self.current_poll:
            return

        # Determine which option won
        winning_option = 0 if winner == p1 else 1
        await self.end_poll()

    async def event_ready(self):
        """Called once when the bot goes online."""
        print(f"Bot is ready! Logged in as {self.nick}")
        channel = self.get_channel(self.channel_name)
        await channel.send(f"Bot is now connected! Type !help for commands")
        
        # Update GUI status if available
        if hasattr(self, 'battle_gui'):
            self.battle_gui.update_twitch_status(True, self.nick)

class BattleGUI:
    def __init__(self):
        self.manager = MugenBattleManager()
        self.setup_main_window()
        self.load_theme()
        self.setup_gui()
        self.battle_monitor = None
        self.current_theme = "light"
        
        # Add Twitch bot initialization
        self.twitch_bot = None
        self.betting_duration = 20  # seconds
        self.preview_window = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        
        # Load default config if it exists
        default_config = Path("mugen_battle_config.json")
        if default_config.exists():
            try:
                with open(default_config) as f:
                    config = json.load(f)
                self.load_config_data(config)
            except Exception as e:
                print(f"Failed to load default config: {e}")

    def setup_main_window(self):
        self.root = tk.Tk()
        self.root.title("MUGEN Random AI Battles")
        self.root.geometry("1024x768")
        
        # Set window icon if available
        icon_path = Path("icon.ico")
        if icon_path.exists():
            self.root.iconbitmap(str(icon_path))

        # Create menu bar
        self.create_menu()

    def load_theme(self):
        """Load custom theme colors"""
        self.theme = {
            "light": {
                "bg": "#ffffff",
                "fg": "#000000",
                "select_bg": "#0078d7",
                "select_fg": "#ffffff",
                "button_bg": "#e1e1e1",
                "accent": "#0078d7"
            },
            "dark": {
                "bg": "#1e1e1e",
                "fg": "#ffffff",
                "select_bg": "#0078d7",
                "select_fg": "#ffffff",
                "button_bg": "#333333",
                "accent": "#0078d7"
            }
        }
        self.apply_theme("light")  # Default theme

    def apply_theme(self, theme_name):
        """Apply the selected theme"""
        theme = self.theme[theme_name]
        self.root.configure(bg=theme["bg"])
        style = ttk.Style()
        
        # Configure ttk styles
        style.configure(".", 
                       background=theme["bg"],
                       foreground=theme["fg"],
                       fieldbackground=theme["bg"])
        
        style.configure("Accent.TButton",
                       background=theme["accent"],
                       foreground=theme["fg"])
        
        self.current_theme = theme_name

    def create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File Menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Save Configuration", command=self.save_config)
        file_menu.add_command(label="Load Configuration", command=self.load_config)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)

        # View Menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Light Theme", command=lambda: self.apply_theme("light"))
        view_menu.add_command(label="Dark Theme", command=lambda: self.apply_theme("dark"))

        # Tools Menu
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Export Statistics", command=self.export_stats)
        tools_menu.add_command(label="Reset Statistics", command=self.reset_stats)

        # Help Menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Documentation", command=self.show_documentation)
        help_menu.add_command(label="About", command=self.show_about)

    def setup_gui(self):
        # Create main container
        self.main_container = ttk.Frame(self.root)
        self.main_container.pack(expand=True, fill="both", padx=10, pady=10)

        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.main_container)
        self.notebook.pack(expand=True, fill="both")

        # Create tabs
        self.battle_tab = ttk.Frame(self.notebook)
        self.characters_tab = ttk.Frame(self.notebook)
        self.stages_tab = ttk.Frame(self.notebook)
        self.stats_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.battle_tab, text="Battle Control")
        self.notebook.add(self.characters_tab, text="Characters")
        self.notebook.add(self.stages_tab, text="Stages")
        self.notebook.add(self.stats_tab, text="Statistics")
        self.notebook.add(self.settings_tab, text="Settings")

        self._setup_battle_tab()
        self._setup_characters_tab()
        self._setup_stages_tab()
        self._setup_stats_tab()
        self._setup_settings_tab()

    def _setup_battle_tab(self):
        # Split battle tab into left and right panels
        battle_paned = ttk.PanedWindow(self.battle_tab, orient=tk.HORIZONTAL)
        battle_paned.pack(expand=True, fill="both")

        # Left panel - Battle Controls
        control_frame = ttk.LabelFrame(battle_paned, text="Battle Controls")
        battle_paned.add(control_frame, weight=1)

        # Battle Mode Selection
        mode_frame = ttk.Frame(control_frame)
        mode_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(mode_frame, text="Battle Mode:").pack(side="left")
        self.mode_var = tk.StringVar(value="single")
        modes = [("Single", "single"), ("Team", "team"), 
                ("Turns", "turns"), ("Simul", "simul")]
        for text, mode in modes:
            ttk.Radiobutton(mode_frame, text=text, value=mode, 
                           variable=self.mode_var,
                           command=self._on_mode_change).pack(side="left", padx=5)

        # Simul Mode Settings Frame
        self.simul_frame = ttk.LabelFrame(control_frame, text="Simul Battle Settings")
        
        # Team 1 Size
        team1_frame = ttk.Frame(self.simul_frame)
        team1_frame.pack(fill="x", padx=5, pady=2)
        ttk.Label(team1_frame, text="Team 1 Size:").pack(side="left")
        self.team1_size_var = tk.IntVar(value=2)
        team1_spin = ttk.Spinbox(team1_frame, from_=1, to=4, 
                                textvariable=self.team1_size_var, width=5)
        team1_spin.pack(side="left", padx=5)
        
        # Team 2 Size
        team2_frame = ttk.Frame(self.simul_frame)
        team2_frame.pack(fill="x", padx=5, pady=2)
        ttk.Label(team2_frame, text="Team 2 Size:").pack(side="left")
        self.team2_size_var = tk.IntVar(value=2)
        team2_spin = ttk.Spinbox(team2_frame, from_=1, to=4, 
                                textvariable=self.team2_size_var, width=5)
        team2_spin.pack(side="left", padx=5)
        
        # Random Team Sizes
        self.random_team_sizes_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.simul_frame, text="Random Team Sizes", 
                        variable=self.random_team_sizes_var).pack(anchor="w", padx=5, pady=2)

        # Team Size (for team/turns modes)
        team_frame = ttk.Frame(control_frame)
        team_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(team_frame, text="Team Size:").pack(side="left")
        self.team_size_var = tk.IntVar(value=3)
        team_size_spin = ttk.Spinbox(team_frame, from_=2, to=5, 
                                    textvariable=self.team_size_var, width=5)
        team_size_spin.pack(side="left", padx=5)

        # Rounds
        rounds_frame = ttk.Frame(control_frame)
        rounds_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(rounds_frame, text="Rounds:").pack(side="left")
        self.rounds_var = tk.IntVar(value=1)
        rounds_spin = ttk.Spinbox(rounds_frame, from_=1, to=99, 
                                 textvariable=self.rounds_var, width=5)
        rounds_spin.pack(side="left", padx=5)

        # Battle Options
        options_frame = ttk.LabelFrame(control_frame, text="Options")
        options_frame.pack(fill="x", padx=5, pady=5)
        
        self.continuous_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Continuous Mode", 
                       variable=self.continuous_var).pack(anchor="w", padx=5, pady=2)
        
        self.random_color_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Random Colors", 
                       variable=self.random_color_var).pack(anchor="w", padx=5, pady=2)

        # Control Buttons
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(fill="x", padx=5, pady=10)
        
        ttk.Button(button_frame, text="Start Battle", style="Accent.TButton",
                  command=self._start_battle).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Stop", 
                  command=self._stop_battle).pack(side="left", padx=5)

        # Right panel - Battle Log
        log_frame = ttk.LabelFrame(battle_paned, text="Battle Log")
        battle_paned.add(log_frame, weight=2)

        # Battle Log with timestamps
        self.battle_log = ScrolledText(log_frame, height=10, wrap=tk.WORD)
        self.battle_log.pack(expand=True, fill="both", padx=5, pady=5)

        # Add clear log button
        ttk.Button(log_frame, text="Clear Log", 
                  command=lambda: self.battle_log.delete(1.0, tk.END)).pack(pady=5)

    def _on_mode_change(self):
        """Handle battle mode changes"""
        mode = self.mode_var.get()
        
        # Show/hide simul settings
        if mode == "simul":
            self.simul_frame.pack(fill="x", padx=5, pady=5)
        else:
            self.simul_frame.pack_forget()

    def _setup_characters_tab(self):
        # Create search and filter frame
        filter_frame = ttk.LabelFrame(self.characters_tab, text="Search & Filter")
        filter_frame.pack(fill="x", padx=5, pady=5)

        # Search
        search_frame = ttk.Frame(filter_frame)
        search_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(search_frame, text="Search:").pack(side="left")
        self.char_search_var = tk.StringVar()
        self.char_search_var.trace("w", self._filter_characters)
        search_entry = ttk.Entry(search_frame, textvariable=self.char_search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=5)

        # Filter options
        filter_options = ttk.Frame(filter_frame)
        filter_options.pack(fill="x", padx=5, pady=5)
        
        self.show_unranked_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(filter_options, text="Show Unranked", 
                       variable=self.show_unranked_var,
                       command=self._filter_characters).pack(side="left", padx=5)
        
        # Tier filters
        self.tier_filters = {}
        for tier in ["S", "A", "B", "C", "D"]:
            self.tier_filters[tier] = tk.BooleanVar(value=True)
            ttk.Checkbutton(filter_options, text=f"Tier {tier}", 
                           variable=self.tier_filters[tier],
                           command=self._filter_characters).pack(side="left", padx=5)

        # Character list with checkboxes
        list_frame = ttk.Frame(self.characters_tab)
        list_frame.pack(expand=True, fill="both", padx=5, pady=5)

        # Create Treeview for characters
        columns = ("Character", "Tier", "Win Rate", "Enabled")
        self.char_tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        
        # Set column headings and widths
        for col in columns:
            self.char_tree.heading(col, text=col, 
                                 command=lambda c=col: self._sort_characters(c))
            width = 100 if col != "Character" else 200
            self.char_tree.column(col, width=width)

        # Add scrollbar
        char_scroll = ttk.Scrollbar(list_frame, orient="vertical", 
                                  command=self.char_tree.yview)
        self.char_tree.configure(yscrollcommand=char_scroll.set)

        # Pack everything
        self.char_tree.pack(side="left", expand=True, fill="both")
        char_scroll.pack(side="right", fill="y")

        # Populate character list
        self._populate_character_list()

        # Quick selection buttons
        selection_frame = ttk.Frame(self.characters_tab)
        selection_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Button(selection_frame, text="Select All", 
                  command=self._select_all_chars).pack(side="left", padx=5)
        ttk.Button(selection_frame, text="Deselect All", 
                  command=self._deselect_all_chars).pack(side="left", padx=5)
        ttk.Button(selection_frame, text="Invert Selection", 
                  command=self._invert_char_selection).pack(side="left", padx=5)

    def _setup_stages_tab(self):
        """Setup the stages management tab"""
        # Create search and filter frame
        filter_frame = ttk.LabelFrame(self.stages_tab, text="Search & Filter")
        filter_frame.pack(fill="x", padx=5, pady=5)

        # Search
        search_frame = ttk.Frame(filter_frame)
        search_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(search_frame, text="Search:").pack(side="left")
        self.stage_search_var = tk.StringVar()
        self.stage_search_var.trace("w", self._filter_stages)
        search_entry = ttk.Entry(search_frame, textvariable=self.stage_search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=5)

        # Stage list with usage statistics
        list_frame = ttk.Frame(self.stages_tab)
        list_frame.pack(expand=True, fill="both", padx=5, pady=5)

        # Create Treeview for stages
        columns = ("Stage", "Times Used", "Last Used", "Enabled")
        self.stage_tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        
        # Set column headings and widths
        for col in columns:
            self.stage_tree.heading(col, text=col, 
                                  command=lambda c=col: self._sort_stages(c))
            width = 200 if col == "Stage" else 100
            self.stage_tree.column(col, width=width)

        # Add scrollbar
        stage_scroll = ttk.Scrollbar(list_frame, orient="vertical", 
                                   command=self.stage_tree.yview)
        self.stage_tree.configure(yscrollcommand=stage_scroll.set)

        # Pack everything
        self.stage_tree.pack(side="left", expand=True, fill="both")
        stage_scroll.pack(side="right", fill="y")

        # Populate stage list
        self._populate_stage_list()

        # Quick selection buttons
        selection_frame = ttk.Frame(self.stages_tab)
        selection_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Button(selection_frame, text="Select All", 
                  command=self._select_all_stages).pack(side="left", padx=5)
        ttk.Button(selection_frame, text="Deselect All", 
                  command=self._deselect_all_stages).pack(side="left", padx=5)
        ttk.Button(selection_frame, text="Invert Selection", 
                  command=self._invert_stage_selection).pack(side="left", padx=5)

    def _populate_stage_list(self):
        """Populate the stage list with current stages"""
        # Clear existing items
        for item in self.stage_tree.get_children():
            self.stage_tree.delete(item)
        
        # Add stages
        for stage in sorted(self.manager.stages):
            usage = self.manager.stage_stats.get(stage, {"times_used": 0, "last_used": "Never"})
            enabled = "✓" if stage in self.manager.settings["enabled_stages"] else "✗"
            
            self.stage_tree.insert("", "end", values=(
                stage,
                usage["times_used"],
                usage["last_used"],
                enabled
            ))
        
        # Bind double-click to toggle enabled status
        self.stage_tree.bind("<Double-1>", self._toggle_stage_status)

    def _filter_stages(self, *args):
        """Filter stages based on search text"""
        search_text = self.stage_search_var.get().lower()
        
        # Clear existing items
        for item in self.stage_tree.get_children():
            self.stage_tree.delete(item)
        
        # Add filtered stages
        for stage in sorted(self.manager.stages):
            if search_text in stage.lower():
                usage = self.manager.stage_stats.get(stage, {"times_used": 0, "last_used": "Never"})
                enabled = "✓" if stage in self.manager.settings["enabled_stages"] else "✗"
                
                self.stage_tree.insert("", "end", values=(
                    stage,
                    usage["times_used"],
                    usage["last_used"],
                    enabled
                ))

    def _sort_stages(self, column):
        """Sort stages by the selected column"""
        items = [(self.stage_tree.set(item, column), item) for item in self.stage_tree.get_children("")]
        
        # Sort items
        items.sort(reverse=self.stage_tree.heading(column).get("reverse", False))
        
        # Rearrange items in sorted positions
        for index, (_, item) in enumerate(items):
            self.stage_tree.move(item, "", index)
        
        # Reverse sort next time
        self.stage_tree.heading(column, 
                              command=lambda: self._sort_stages(column),
                              reverse=not self.stage_tree.heading(column).get("reverse", False))

    def _toggle_stage_status(self, event):
        """Toggle the enabled status of a stage"""
        region = self.stage_tree.identify("region", event.x, event.y)
        if region == "cell":
            item = self.stage_tree.selection()[0]
            stage = self.stage_tree.item(item)["values"][0]
            
            if stage in self.manager.settings["enabled_stages"]:
                self.manager.settings["enabled_stages"].remove(stage)
            else:
                self.manager.settings["enabled_stages"].add(stage)
            
            self._populate_stage_list()

    def _select_all_stages(self):
        """Enable all stages"""
        self.manager.settings["enabled_stages"] = set(self.manager.stages)
        self._populate_stage_list()

    def _deselect_all_stages(self):
        """Disable all stages"""
        self.manager.settings["enabled_stages"] = set()
        self._populate_stage_list()

    def _invert_stage_selection(self):
        """Invert the selection of stages"""
        current = self.manager.settings["enabled_stages"]
        self.manager.settings["enabled_stages"] = set(s for s in self.manager.stages if s not in current)
        self._populate_stage_list()

    def _populate_character_list(self):
        """Populate the character list with current characters"""
        # Clear existing items
        for item in self.char_tree.get_children():
            self.char_tree.delete(item)
        
        # Add characters
        for char in sorted(self.manager.characters):
            stats = self.manager.character_stats.get(char, {"wins": 0, "losses": 0})
            total_matches = stats["wins"] + stats["losses"]
            win_rate = f"{(stats['wins']/total_matches)*100:.1f}%" if total_matches > 0 else "N/A"
            tier = self.manager.get_character_tier(char)
            enabled = "✓" if char in self.manager.settings["enabled_characters"] else "✗"
            
            self.char_tree.insert("", "end", values=(
                char,
                tier,
                win_rate,
                enabled
            ))
        
        # Bind double-click to toggle enabled status
        self.char_tree.bind("<Double-1>", self._toggle_character_status)

    def _filter_characters(self, *args):
        """Filter characters based on search text and tier filters"""
        search_text = self.char_search_var.get().lower()
        
        # Clear existing items
        for item in self.char_tree.get_children():
            self.char_tree.delete(item)
        
        # Add filtered characters
        for char in sorted(self.manager.characters):
            tier = self.manager.get_character_tier(char)
            
            # Check if character should be shown based on filters
            if (search_text in char.lower() and
                (tier == "Unranked" and self.show_unranked_var.get() or
                 tier != "Unranked" and self.tier_filters[tier].get())):
                
                stats = self.manager.character_stats.get(char, {"wins": 0, "losses": 0})
                total_matches = stats["wins"] + stats["losses"]
                win_rate = f"{(stats['wins']/total_matches)*100:.1f}%" if total_matches > 0 else "N/A"
                enabled = "✓" if char in self.manager.settings["enabled_characters"] else "✗"
                
                self.char_tree.insert("", "end", values=(
                    char,
                    tier,
                    win_rate,
                    enabled
                ))

    def _sort_characters(self, column):
        """Sort characters by the selected column"""
        items = [(self.char_tree.set(item, column), item) for item in self.char_tree.get_children("")]
        
        # Sort items
        items.sort(reverse=self.char_tree.heading(column).get("reverse", False))
        
        # Rearrange items in sorted positions
        for index, (_, item) in enumerate(items):
            self.char_tree.move(item, "", index)
        
        # Reverse sort next time
        self.char_tree.heading(column, 
                              command=lambda: self._sort_characters(column),
                              reverse=not self.char_tree.heading(column).get("reverse", False))

    def _toggle_character_status(self, event):
        """Toggle the enabled status of a character"""
        region = self.char_tree.identify("region", event.x, event.y)
        if region == "cell":
            item = self.char_tree.selection()[0]
            char = self.char_tree.item(item)["values"][0]
            
            if char in self.manager.settings["enabled_characters"]:
                self.manager.settings["enabled_characters"].remove(char)
            else:
                self.manager.settings["enabled_characters"].add(char)
            
            self._populate_character_list()

    def _select_all_chars(self):
        """Enable all characters"""
        self.manager.settings["enabled_characters"] = set(self.manager.characters)
        self._populate_character_list()

    def _deselect_all_chars(self):
        """Disable all characters"""
        self.manager.settings["enabled_characters"] = set()
        self._populate_character_list()

    def _invert_char_selection(self):
        """Invert the selection of characters"""
        current = self.manager.settings["enabled_characters"]
        self.manager.settings["enabled_characters"] = set(c for c in self.manager.characters if c not in current)
        self._populate_character_list()

    def _setup_stats_tab(self):
        # Create statistics view with multiple sections
        
        # Top panel - Summary statistics
        summary_frame = ttk.LabelFrame(self.stats_tab, text="Summary")
        summary_frame.pack(fill="x", padx=5, pady=5)

        # Grid for summary stats
        self.summary_labels = {}
        summary_stats = [
            ("Total Battles", "total_battles"),
            ("Most Winning Character", "top_winner"),
            ("Most Used Stage", "top_stage"),
            ("Average Battle Duration", "avg_duration")
        ]
        
        for i, (label, key) in enumerate(summary_stats):
            ttk.Label(summary_frame, text=f"{label}:").grid(row=i//2, column=i%2*2, padx=5, pady=5, sticky="e")
            self.summary_labels[key] = ttk.Label(summary_frame, text="---")
            self.summary_labels[key].grid(row=i//2, column=i%2*2+1, padx=5, pady=5, sticky="w")

        # Character Statistics
        char_stats_frame = ttk.LabelFrame(self.stats_tab, text="Character Statistics")
        char_stats_frame.pack(expand=True, fill="both", padx=5, pady=5)

        # Create Treeview for detailed stats
        columns = ("Character", "Wins", "Losses", "Win Rate", "Tier", "Most Defeated", "Most Lost To")
        self.stats_tree = ttk.Treeview(char_stats_frame, columns=columns, show="headings")
        
        # Set column headings and widths
        for col in columns:
            self.stats_tree.heading(col, text=col, 
                                  command=lambda c=col: self._sort_stats(c))
            width = 100 if col not in ["Character", "Most Defeated", "Most Lost To"] else 150
            self.stats_tree.column(col, width=width)

        # Add scrollbar
        stats_scroll = ttk.Scrollbar(char_stats_frame, orient="vertical", 
                                   command=self.stats_tree.yview)
        self.stats_tree.configure(yscrollcommand=stats_scroll.set)

        # Pack everything
        self.stats_tree.pack(side="left", expand=True, fill="both")
        stats_scroll.pack(side="right", fill="y")

        # Battle History Visualization
        graph_frame = ttk.LabelFrame(self.stats_tab, text="Battle History")
        graph_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Create matplotlib figure
        self.fig, (self.ax1, self.ax2) = plt.subplots(2, 1, figsize=(10, 8))
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # Add control buttons
        controls_frame = ttk.Frame(graph_frame)
        controls_frame.pack(fill="x", padx=5, pady=5)

        # Time range selection
        ttk.Label(controls_frame, text="Time Range:").pack(side="left")
        self.time_range = ttk.Combobox(controls_frame, 
                                     values=["Last 24 Hours", "Last Week", "Last Month", "All Time"],
                                     state="readonly")
        self.time_range.set("Last Week")
        self.time_range.pack(side="left", padx=5)
        self.time_range.bind("<<ComboboxSelected>>", self._update_battle_history)

        # Refresh button
        ttk.Button(controls_frame, text="Refresh", 
                  command=self._update_battle_history).pack(side="right", padx=5)

        # Initial update
        self._update_battle_history()

    def _update_battle_history(self, event=None):
        """Update battle history visualization"""
        # Clear previous plots
        self.ax1.clear()
        self.ax2.clear()

        battles = self.manager.battle_history["battles"]
        if not battles:
            self.ax1.text(0.5, 0.5, "No battle history available", 
                         ha='center', va='center')
            self.canvas.draw()
            return

        # Convert timestamps to datetime objects
        timestamps = [datetime.strptime(b["timestamp"], "%Y-%m-%d %H:%M:%S") 
                     for b in battles]

        # Filter based on selected time range
        now = datetime.now()
        if self.time_range.get() == "Last 24 Hours":
            start_time = now - timedelta(days=1)
        elif self.time_range.get() == "Last Week":
            start_time = now - timedelta(weeks=1)
        elif self.time_range.get() == "Last Month":
            start_time = now - timedelta(days=30)
        else:  # All Time
            start_time = min(timestamps)

        # Filter battles within time range
        filtered_battles = [(t, b) for t, b in zip(timestamps, battles) 
                          if t >= start_time]
        if not filtered_battles:
            self.ax1.text(0.5, 0.5, "No battles in selected time range", 
                         ha='center', va='center')
            self.canvas.draw()
            return

        timestamps, battles = zip(*filtered_battles)

        # Plot 1: Battle Activity Over Time
        dates = matplotlib.dates.date2num(timestamps)
        self.ax1.hist(dates, bins=min(len(dates), 50), 
                     color='skyblue', edgecolor='black')
        self.ax1.xaxis.set_major_formatter(matplotlib.dates.DateFormatter('%Y-%m-%d'))
        self.ax1.set_title("Battle Activity Over Time")
        self.ax1.set_xlabel("Date")
        self.ax1.set_ylabel("Number of Battles")
        plt.setp(self.ax1.xaxis.get_majorticklabels(), rotation=45)

        # Plot 2: Win Distribution (Top 10 Characters)
        char_wins = {}
        for battle in battles:
            winner = battle["winner"]
            if isinstance(winner, list):  # Team battle
                for char in winner:
                    char_wins[char] = char_wins.get(char, 0) + 1
            else:  # Single battle
                char_wins[winner] = char_wins.get(winner, 0) + 1

        # Get top 10 winners
        top_chars = sorted(char_wins.items(), key=lambda x: x[1], reverse=True)[:10]
        if top_chars:
            chars, wins = zip(*top_chars)
            y_pos = np.arange(len(chars))
            
            self.ax2.barh(y_pos, wins, color='lightgreen')
            self.ax2.set_yticks(y_pos)
            self.ax2.set_yticklabels(chars)
            self.ax2.invert_yaxis()
            self.ax2.set_title("Top 10 Winning Characters")
            self.ax2.set_xlabel("Number of Wins")

        # Adjust layout and draw
        self.fig.tight_layout()
        self.canvas.draw()

    def _setup_settings_tab(self):
        # General Settings
        general_frame = ttk.LabelFrame(self.settings_tab, text="General Settings")
        general_frame.pack(fill="x", padx=5, pady=5)

        # Auto-save settings
        self.autosave_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(general_frame, text="Auto-save statistics", 
                       variable=self.autosave_var).pack(anchor="w", padx=5, pady=2)

        # Backup settings
        backup_frame = ttk.Frame(general_frame)
        backup_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(backup_frame, text="Backup Frequency:").pack(side="left")
        self.backup_freq = ttk.Combobox(backup_frame, values=["Never", "Daily", "Weekly", "Monthly"],
                                      state="readonly")
        self.backup_freq.set("Weekly")
        self.backup_freq.pack(side="left", padx=5)

        # Display Settings
        display_frame = ttk.LabelFrame(self.settings_tab, text="Display Settings")
        display_frame.pack(fill="x", padx=5, pady=5)

        # Theme selection
        theme_frame = ttk.Frame(display_frame)
        theme_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(theme_frame, text="Theme:").pack(side="left")
        self.theme_combo = ttk.Combobox(theme_frame, values=["Light", "Dark"], state="readonly")
        self.theme_combo.set("Light")
        self.theme_combo.pack(side="left", padx=5)
        self.theme_combo.bind("<<ComboboxSelected>>", 
                            lambda e: self.apply_theme(self.theme_combo.get().lower()))

        # Font size
        font_frame = ttk.Frame(display_frame)
        font_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(font_frame, text="Font Size:").pack(side="left")
        self.font_size = ttk.Spinbox(font_frame, from_=8, to=16, width=5)
        self.font_size.set(10)
        self.font_size.pack(side="left", padx=5)

        # Advanced Settings
        advanced_frame = ttk.LabelFrame(self.settings_tab, text="Advanced Settings")
        advanced_frame.pack(fill="x", padx=5, pady=5)

        # MUGEN executable path
        mugen_frame = ttk.Frame(advanced_frame)
        mugen_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(mugen_frame, text="MUGEN Path:").pack(side="left")
        self.mugen_path_var = tk.StringVar(value=str(self.manager.mugen_path))
        path_entry = ttk.Entry(mugen_frame, textvariable=self.mugen_path_var)
        path_entry.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(mugen_frame, text="Browse", 
                  command=self._browse_mugen_path).pack(side="left")

        # Twitch Integration Settings
        twitch_frame = ttk.LabelFrame(self.settings_tab, text="Twitch Integration")
        twitch_frame.pack(fill="x", padx=5, pady=5)

        # Bot Display Name
        bot_name_frame = ttk.Frame(twitch_frame)
        bot_name_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(bot_name_frame, text="Bot Display Name:").pack(side="left")
        self.twitch_botname_var = tk.StringVar(value="MugenBattleBot")
        bot_name_entry = ttk.Entry(bot_name_frame, textvariable=self.twitch_botname_var)
        bot_name_entry.pack(side="left", fill="x", expand=True, padx=5)

        # Twitch OAuth Token
        token_frame = ttk.Frame(twitch_frame)
        token_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(token_frame, text="OAuth Token:").pack(side="left")
        self.twitch_token_var = tk.StringVar()
        token_entry = ttk.Entry(token_frame, textvariable=self.twitch_token_var, show="*")
        token_entry.pack(side="left", fill="x", expand=True, padx=5)

        # Channel Name
        channel_frame = ttk.Frame(twitch_frame)
        channel_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(channel_frame, text="Channel:").pack(side="left")
        self.twitch_channel_var = tk.StringVar()
        channel_entry = ttk.Entry(channel_frame, textvariable=self.twitch_channel_var)
        channel_entry.pack(side="left", fill="x", expand=True, padx=5)

        # Points Settings
        points_frame = ttk.Frame(twitch_frame)
        points_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(points_frame, text="Points Reward:").pack(side="left")
        self.points_reward_var = tk.IntVar(value=100)
        points_spin = ttk.Spinbox(points_frame, from_=10, to=1000, 
                                 textvariable=self.points_reward_var)
        points_spin.pack(side="left", padx=5)

        # Betting Duration
        duration_frame = ttk.Frame(twitch_frame)
        duration_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Label(duration_frame, text="Betting Duration (seconds):").pack(side="left")
        self.betting_duration_var = tk.IntVar(value=20)
        duration_spin = ttk.Spinbox(duration_frame, from_=15, to=30, 
                                   textvariable=self.betting_duration_var)
        duration_spin.pack(side="left", padx=5)

        # Connect button
        ttk.Button(twitch_frame, text="Connect to Twitch",
                   command=self._connect_twitch).pack(pady=5)

        # Add status label to Twitch frame
        self.twitch_status_label = ttk.Label(
            twitch_frame, 
            text="Status: Disconnected",
            foreground="red"
        )
        self.twitch_status_label.pack(pady=5)

    def _browse_mugen_path(self):
        path = filedialog.askopenfilename(
            title="Select MUGEN Executable",
            filetypes=[("Executable files", "*.exe"), ("All files", "*.*")]
        )
        if path:
            self.mugen_path_var.set(path)
            self.manager.mugen_path = Path(path)

    def save_config(self):
        """Save current configuration to file"""
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save Configuration"
        )
        if path:
            config = {
                # Battle settings
                "battle_settings": {
                    "rounds": self.rounds_var.get(),
                    "battle_mode": self.mode_var.get(),
                    "team_size": self.team_size_var.get(),
                    "continuous_mode": self.continuous_var.get(),
                    "random_color": self.random_color_var.get(),
                    "team1_size": self.team1_size_var.get(),
                    "team2_size": self.team2_size_var.get(),
                    "random_team_sizes": self.random_team_sizes_var.get()
                },
                # Character and stage settings
                "enabled_characters": list(self.manager.settings["enabled_characters"]),
                "enabled_stages": list(self.manager.settings["enabled_stages"]),
                
                # Display settings
                "theme": self.current_theme,
                "font_size": self.font_size.get(),
                
                # General settings
                "autosave": self.autosave_var.get(),
                "backup_frequency": self.backup_freq.get(),
                
                # Twitch settings
                "twitch": {
                    "bot_name": self.twitch_botname_var.get(),
                    "channel": self.twitch_channel_var.get(),
                    "points_reward": self.points_reward_var.get(),
                    "betting_duration": self.betting_duration_var.get()
                },
                
                # Paths
                "mugen_path": str(self.manager.mugen_path)
            }
            
            try:
                with open(path, 'w') as f:
                    json.dump(config, f, indent=2)
                messagebox.showinfo("Success", "Configuration saved successfully!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save configuration: {str(e)}")

    def load_config(self):
        """Load configuration from file"""
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Load Configuration"
        )
        if path:
            try:
                with open(path) as f:
                    config = json.load(f)
                
                # Load battle settings
                battle_settings = config.get("battle_settings", {})
                self.rounds_var.set(battle_settings.get("rounds", 1))
                self.mode_var.set(battle_settings.get("battle_mode", "single"))
                self.team_size_var.set(battle_settings.get("team_size", 3))
                self.continuous_var.set(battle_settings.get("continuous_mode", True))
                self.random_color_var.set(battle_settings.get("random_color", True))
                self.team1_size_var.set(battle_settings.get("team1_size", 2))
                self.team2_size_var.set(battle_settings.get("team2_size", 2))
                self.random_team_sizes_var.set(battle_settings.get("random_team_sizes", True))
                
                # Load character and stage settings
                self.manager.settings["enabled_characters"] = set(config.get("enabled_characters", []))
                self.manager.settings["enabled_stages"] = set(config.get("enabled_stages", []))
                
                # Load display settings
                self.apply_theme(config.get("theme", "light"))
                self.font_size.set(config.get("font_size", 10))
                
                # Load general settings
                self.autosave_var.set(config.get("autosave", True))
                self.backup_freq.set(config.get("backup_frequency", "Weekly"))
                
                # Load Twitch settings
                twitch_settings = config.get("twitch", {})
                self.twitch_botname_var.set(twitch_settings.get("bot_name", "MugenBattleBot"))
                self.twitch_channel_var.set(twitch_settings.get("channel", ""))
                self.points_reward_var.set(twitch_settings.get("points_reward", 100))
                self.betting_duration_var.set(twitch_settings.get("betting_duration", 20))
                
                # Load paths
                mugen_path = config.get("mugen_path")
                if mugen_path:
                    self.manager.mugen_path = Path(mugen_path)
                    self.mugen_path_var.set(mugen_path)
                
                # Update all views
                self._refresh_all_views()
                self._on_mode_change()  # Update battle mode UI
                
                messagebox.showinfo("Success", "Configuration loaded successfully!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load configuration: {str(e)}")

    def _refresh_all_views(self):
        """Refresh all GUI elements after configuration changes"""
        self._populate_character_list()
        self._update_stats_view()
        self._populate_stage_list()

    def run(self):
        self.root.mainloop()

    def export_stats(self):
        """Export statistics to a file"""
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Statistics"
        )
        if path:
            try:
                with open(path, 'w') as f:
                    # Write header
                    f.write("Character,Wins,Losses,Win Rate,Tier,Most Defeated,Most Lost To\n")
                    
                    # Write character stats
                    for char in sorted(self.manager.characters):
                        stats = self.manager.character_stats.get(char, {"wins": 0, "losses": 0})
                        total_matches = stats["wins"] + stats["losses"]
                        win_rate = f"{(stats['wins']/total_matches)*100:.1f}%" if total_matches > 0 else "N/A"
                        tier = self.manager.get_character_tier(char)
                        
                        # TODO: Implement most defeated/lost to tracking
                        most_defeated = "N/A"
                        most_lost_to = "N/A"
                        
                        f.write(f"{char},{stats['wins']},{stats['losses']},{win_rate},{tier},{most_defeated},{most_lost_to}\n")
                        
                messagebox.showinfo("Success", "Statistics exported successfully!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to export statistics: {str(e)}")

    def reset_stats(self):
        """Reset all statistics"""
        if messagebox.askyesno("Confirm Reset", "Are you sure you want to reset all statistics? This cannot be undone."):
            self.manager.character_stats = {}
            self.manager.stage_stats = {}
            self.manager.save_stats()
            self._refresh_all_views()
            messagebox.showinfo("Success", "Statistics reset successfully!")

    def show_documentation(self):
        """Show documentation"""
        doc_text = """
MUGEN Random AI Battles

Controls:
- Battle Tab: Configure and start AI battles
- Characters Tab: Enable/disable and filter characters
- Stages Tab: Enable/disable and manage stages
- Statistics Tab: View battle statistics and rankings
- Settings Tab: Configure application settings

Features:
- Single, Team, and Turns battle modes
- Character tier rankings based on performance
- Stage usage tracking
- Battle result logging
- Configuration saving/loading
- Statistics export

For more information, visit: [Documentation URL]
"""
        doc_window = tk.Toplevel(self.root)
        doc_window.title("Documentation")
        doc_window.geometry("600x400")
        
        text_widget = ScrolledText(doc_window, wrap=tk.WORD)
        text_widget.pack(expand=True, fill="both", padx=10, pady=10)
        text_widget.insert("1.0", doc_text)
        text_widget.config(state="disabled")

    def show_about(self):
        """Show about dialog"""
        about_text = """
MUGEN Random AI Battles
Version 1.0.0

A modern GUI for managing MUGEN AI battles.
Features character statistics, battle modes,
and comprehensive battle management.

Created by: [Your Name]
Based on the original batch script by Inktrebuchet
and Chickenbone's modifications.

© 2024 All rights reserved.
"""
        messagebox.showinfo("About", about_text)

    def _update_stats_view(self):
        """Update the statistics view"""
        # Clear existing items
        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)
        
        # Update summary statistics
        total_battles = sum(
            stats["wins"] + stats["losses"]
            for stats in self.manager.character_stats.values()
        )
        self.summary_labels["total_battles"].config(text=str(total_battles))
        
        # Find top winner
        if self.manager.character_stats:
            top_winner = max(
                self.manager.character_stats.items(),
                key=lambda x: x[1]["wins"]
            )[0]
            self.summary_labels["top_winner"].config(text=top_winner)
        
        # Find most used stage
        if self.manager.stage_stats:
            top_stage = max(
                self.manager.stage_stats.items(),
                key=lambda x: x[1]["times_used"]
            )[0]
            self.summary_labels["top_stage"].config(text=top_stage)
        
        # Add character statistics
        for char in sorted(self.manager.characters):
            stats = self.manager.character_stats.get(char, {"wins": 0, "losses": 0})
            total_matches = stats["wins"] + stats["losses"]
            win_rate = f"{(stats['wins']/total_matches)*100:.1f}%" if total_matches > 0 else "N/A"
            tier = self.manager.get_character_tier(char)
            
            # TODO: Implement most defeated/lost to tracking
            most_defeated = "N/A"
            most_lost_to = "N/A"
            
            self.stats_tree.insert("", "end", values=(
                char,
                stats["wins"],
                stats["losses"],
                win_rate,
                tier,
                most_defeated,
                most_lost_to
            ))

    def _sort_stats(self, column):
        """Sort statistics by the selected column"""
        items = [(self.stats_tree.set(item, column), item) for item in self.stats_tree.get_children("")]
        
        # Sort items
        items.sort(reverse=self.stats_tree.heading(column).get("reverse", False))
        
        # Rearrange items in sorted positions
        for index, (_, item) in enumerate(items):
            self.stats_tree.move(item, "", index)
        
        # Reverse sort next time
        self.stats_tree.heading(column, 
                              command=lambda: self._sort_stats(column),
                              reverse=not self.stats_tree.heading(column).get("reverse", False))

    def _start_battle(self):
        """Modified battle start to include betting period"""
        try:
            # Prepare battle info
            battle_info = self.manager.prepare_battle()
            
            # Show preview and start betting period
            self.show_battle_preview(battle_info)
            
            # Create Twitch prediction
            if self.twitch_bot:
                asyncio.run_coroutine_threadsafe(
                    self._create_twitch_prediction(battle_info),
                    self.twitch_bot.loop
                )
            
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _start_battle_after_betting(self):
        """Start the actual battle after betting period"""
        try:
            battle_info = self.manager.start_battle()
            
            # Log battle start
            timestamp = time.strftime("%H:%M:%S")
            if battle_info['mode'] == "single":
                battle_text = f"[{timestamp}] Starting battle: {battle_info['p1']} vs {battle_info['p2']}"
            else:
                team1 = " & ".join(battle_info['p1'])
                team2 = " & ".join(battle_info['p2'])
                battle_text = f"[{timestamp}] Starting {self.manager.settings['battle_mode']} battle:\nTeam 1: {team1}\nTeam 2: {team2}"
            
            battle_text += f"\nStage: {battle_info['stage']}\n"
            self.battle_log.insert(tk.END, battle_text)
            self.battle_log.see(tk.END)
            
            # Start monitoring for results
            if not self.battle_monitor:
                self.battle_monitor = self.root.after(1000, self._check_battle_result)

        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _check_battle_result(self):
        """Modified to handle Twitch poll results"""
        result = self.manager.check_battle_result()
        if result:
            # Update Twitch poll
            if self.twitch_bot and self.twitch_bot.current_poll:
                asyncio.run_coroutine_threadsafe(
                    self.twitch_bot.handle_battle_result(
                        result['winner'],
                        self.manager.current_battle['p1'],
                        self.manager.current_battle['p2']
                    ),
                    self.twitch_bot.loop
                )

            # Format and display result
            timestamp = time.strftime("%H:%M:%S")
            if result["mode"] == "single":
                result_text = f"[{timestamp}] Battle Result:\n{result['winner']} defeated {result['loser']}\nScore: {result['p1_score']}-{result['p2_score']}\n"
            else:
                winner_team = " & ".join(result['winner'])
                loser_team = " & ".join(result['loser'])
                result_text = f"[{timestamp}] Battle Result:\nTeam {winner_team} defeated Team {loser_team}\nScore: {result['p1_score']}-{result['p2_score']}\n"
            
            self.battle_log.insert(tk.END, result_text)
            self.battle_log.see(tk.END)
            
            # Update statistics view
            self._update_stats_view()
            
            # If continuous mode, start next battle
            if self.continuous_var.get():
                self._start_battle()
            else:
                self.battle_monitor = None
                return
        
        # Continue monitoring
        self.battle_monitor = self.root.after(1000, self._check_battle_result)

    def _connect_twitch(self):
        """Connect to Twitch"""
        token = self.twitch_token_var.get()
        channel = self.twitch_channel_var.get()
        bot_name = self.twitch_botname_var.get()
        
        if not token or not channel:
            messagebox.showerror("Error", "Please enter both OAuth token and channel name")
            return
        
        try:
            self.betting_duration = self.betting_duration_var.get()
            self.setup_twitch_bot(token, channel, bot_name)
            messagebox.showinfo("Success", f"Connected to Twitch as {bot_name}!")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to connect to Twitch: {str(e)}")

    def setup_twitch_bot(self, token, channel, bot_name):
        """Initialize Twitch bot with credentials and custom name"""
        self.twitch_bot = TwitchBot(token, channel, self, bot_name)
        threading.Thread(target=self._run_twitch_bot, daemon=True).start()

    def _run_twitch_bot(self):
        """Run Twitch bot in a separate thread"""
        asyncio.set_event_loop(asyncio.new_event_loop())
        self.twitch_bot.run()

    def show_battle_preview(self, battle_info):
        """Show preview window with stage and fighters"""
        self.preview_window = tk.Toplevel(self.root)
        self.preview_window.title("Upcoming Battle Preview")
        self.preview_window.geometry("800x600")

        # Create preview layout
        preview_frame = ttk.Frame(self.preview_window)
        preview_frame.pack(expand=True, fill="both", padx=10, pady=10)

        # Stage preview
        stage_frame = ttk.LabelFrame(preview_frame, text="Stage")
        stage_frame.pack(fill="x", pady=5)
        
        stage_path = self.manager.stages_path / f"{battle_info['stage']}.png"
        if stage_path.exists():
            stage_img = Image.open(stage_path)
            stage_img.thumbnail((400, 225))
            stage_photo = ImageTk.PhotoImage(stage_img)
            stage_label = ttk.Label(stage_frame, image=stage_photo)
            stage_label.image = stage_photo
            stage_label.pack(pady=5)
        
        ttk.Label(stage_frame, text=battle_info['stage']).pack()

        # Fighters preview
        fighters_frame = ttk.LabelFrame(preview_frame, text="Fighters")
        fighters_frame.pack(fill="x", pady=5)

        # Create fighter displays based on battle mode
        if battle_info['mode'] == "single":
            self._create_fighter_preview(fighters_frame, battle_info['p1'], "Player 1")
            ttk.Label(fighters_frame, text="VS").pack(side="left", padx=20)
            self._create_fighter_preview(fighters_frame, battle_info['p2'], "Player 2")
        else:
            # Handle team battles
            self._create_team_preview(fighters_frame, battle_info['p1'], "Team 1")
            ttk.Label(fighters_frame, text="VS").pack(side="left", padx=20)
            self._create_team_preview(fighters_frame, battle_info['p2'], "Team 2")

        # Betting timer
        timer_frame = ttk.Frame(preview_frame)
        timer_frame.pack(fill="x", pady=10)
        
        self.timer_label = ttk.Label(timer_frame, text=f"Betting closes in: {self.betting_duration}")
        self.timer_label.pack()

        # Start betting timer
        self._update_betting_timer(self.betting_duration)

    def _create_fighter_preview(self, parent, fighter, label):
        """Create preview for a single fighter"""
        frame = ttk.Frame(parent)
        frame.pack(side="left", padx=10)
        
        # Try to load fighter portrait
        portrait_path = self.manager.chars_path / fighter / "portrait.png"
        if portrait_path.exists():
            img = Image.open(portrait_path)
            img.thumbnail((150, 150))
            photo = ImageTk.PhotoImage(img)
            img_label = ttk.Label(frame, image=photo)
            img_label.image = photo
            img_label.pack()
        
        ttk.Label(frame, text=f"{label}\n{fighter}").pack()

    def _create_team_preview(self, parent, team, label):
        """Create preview for a team of fighters"""
        frame = ttk.Frame(parent)
        frame.pack(side="left", padx=10)
        
        ttk.Label(frame, text=label).pack()
        
        for fighter in team:
            self._create_fighter_preview(frame, fighter, "")

    def _update_betting_timer(self, remaining):
        """Update the betting timer and handle timeout"""
        try:
            if not hasattr(self, 'preview_window') or not self.preview_window.winfo_exists():
                # Preview window was closed, clean up and stop timer
                return
            
            if remaining > 0:
                self.timer_label.config(text=f"Betting closes in: {remaining}")
                self.root.after(1000, self._update_betting_timer, remaining - 1)
            else:
                if hasattr(self, 'preview_window') and self.preview_window.winfo_exists():
                    self.preview_window.destroy()
                self._start_battle_after_betting()
        except Exception as e:
            print(f"Timer update error: {e}")
            # If there's an error, try to clean up
            if hasattr(self, 'preview_window') and self.preview_window.winfo_exists():
                self.preview_window.destroy()

    async def _create_twitch_prediction(self, battle_info):
        """Create Twitch prediction for the battle"""
        if not self.twitch_bot:
            return

        if battle_info['mode'] == "single":
            title = f"{battle_info['p1']} vs {battle_info['p2']}"
            outcome1 = battle_info['p1']
            outcome2 = battle_info['p2']
        else:
            title = "Team Battle"
            outcome1 = "Team 1"
            outcome2 = "Team 2"

        await self.twitch_bot.create_poll(
            title=title,
            option1=outcome1,
            option2=outcome2,
            duration=self.betting_duration
        )

    def _stop_battle(self):
        """Stop the current battle and clean up"""
        try:
            # Stop battle monitoring
            if self.battle_monitor:
                self.root.after_cancel(self.battle_monitor)
                self.battle_monitor = None

            # Close preview window if open
            if hasattr(self, 'preview_window') and self.preview_window:
                self.preview_window.destroy()
                self.preview_window = None

            # End Twitch poll if active
            if self.twitch_bot and self.twitch_bot.current_poll:
                asyncio.run_coroutine_threadsafe(
                    self.twitch_bot.end_poll(),
                    self.twitch_bot.loop
                )

            # Find and terminate MUGEN process
            try:
                subprocess.run('taskkill /F /IM mugen.exe', shell=True)
            except:
                pass

            # Clean up MugenWatcher
            if self.manager.watcher_process:
                self.manager.watcher_process.terminate()
                self.manager.watcher_process = None

            # Clear current battle state
            self.manager.current_battle = None

            # Log battle stop
            timestamp = time.strftime("%H:%M:%S")
            self.battle_log.insert(tk.END, f"[{timestamp}] Battle stopped by user\n")
            self.battle_log.see(tk.END)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop battle: {str(e)}")

    def update_twitch_status(self, connected: bool, bot_name: str = None):
        """Update Twitch connection status in GUI"""
        timestamp = time.strftime("%H:%M:%S")
        if connected:
            status_text = f"[{timestamp}] Connected to Twitch as {bot_name}\n"
            # Update status label in settings tab if it exists
            if hasattr(self, 'twitch_status_label'):
                self.twitch_status_label.config(
                    text=f"Status: Connected as {bot_name}",
                    foreground="green"
                )
        else:
            status_text = f"[{timestamp}] Disconnected from Twitch\n"
            if hasattr(self, 'twitch_status_label'):
                self.twitch_status_label.config(
                    text="Status: Disconnected",
                    foreground="red"
                )
        
        # Log to battle log
        self.battle_log.insert(tk.END, status_text)
        self.battle_log.see(tk.END)

    def auto_save_config(self):
        """Auto-save configuration to default file"""
        if not self.autosave_var.get():
            return
        
        config_path = Path("mugen_battle_config.json")
        config = {
            "battle_settings": {
                "rounds": self.rounds_var.get(),
                "battle_mode": self.mode_var.get(),
                "team_size": self.team_size_var.get(),
                "continuous_mode": self.continuous_var.get(),
                "random_color": self.random_color_var.get(),
                "team1_size": self.team1_size_var.get(),
                "team2_size": self.team2_size_var.get(),
                "random_team_sizes": self.random_team_sizes_var.get()
            },
            "enabled_characters": list(self.manager.settings["enabled_characters"]),
            "enabled_stages": list(self.manager.settings["enabled_stages"]),
            "theme": self.current_theme,
            "font_size": self.font_size.get(),
            "autosave": self.autosave_var.get(),
            "backup_frequency": self.backup_freq.get(),
            "twitch": {
                "bot_name": self.twitch_botname_var.get(),
                "channel": self.twitch_channel_var.get(),
                "points_reward": self.points_reward_var.get(),
                "betting_duration": self.betting_duration_var.get()
            },
            "mugen_path": str(self.manager.mugen_path)
        }
        
        try:
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Auto-save failed: {e}")

if __name__ == "__main__":
    gui = BattleGUI()
    gui.run() 