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
from PIL import Image, ImageTk, ImageDraw, ImageFont
import matplotlib.pyplot as plt
import matplotlib.dates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from datetime import datetime, timedelta
import numpy as np
from twitchio.ext import commands
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
import traceback
import msvcrt  # For Windows file locking
import psutil
import shutil
import sys

class MugenBattleManager:
    def __init__(self):
        self.mugen_path = Path("mugen.exe").resolve()  # Get absolute path
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
        self.stage_stats = self.load_stats(self.stage_stats_file) or {}
        
        # Character and stage cache - MOVED UP
        self.characters = self.scan_characters()
        self.stages = self.scan_stages()  # Initialize stages before using them
        
        # Initialize current battle
        self.current_battle = None
        
        # Initialize stage stats for all stages
        for stage in self.stages:
            if stage not in self.stage_stats:
                self.stage_stats[stage] = {
                    "times_used": 0,
                    "last_used": "Never",
                    "total_duration": 0
                }
        
        # Battle settings
        self.settings = {
            "rounds": 1,
            "p2_color": 1,
            "battle_mode": "single",  # single, team, turns
            "team_size": 3,
            "continuous_mode": True,
            "enabled_characters": set(self.characters),  # Initially enable all characters
            "enabled_stages": set(self.stages)  # Initially enable all stages
        }
        
        # Add battle history tracking
        self.battle_history_file = Path("battle_history.json")
        self.battle_history = self.load_battle_history()

        self.ensure_watcher_running()  # Add this line to check watcher on startup
        
        # Add battle duration tracking
        self.battle_start_time = None
        self.battle_durations = []  # List to store battle durations

    def scan_characters(self) -> List[str]:
        """Scan for available characters"""
        chars = []
        for char_dir in self.chars_path.iterdir():
            if char_dir.is_dir():
                def_file = char_dir / f"{char_dir.name}.def"
                if def_file.exists():
                    chars.append(char_dir.name)
        return chars

    def scan_stages(self):
        """Scan for available stages"""
        stages = []
        stages_path = Path("stages")  # Root stages folder
        if stages_path.exists():
            # Scan for .def files directly in stages directory and subdirectories
            for stage_file in stages_path.glob("**/*.def"):
                # Get the stage name without path or extension
                stage_name = stage_file.stem
                # For stages in subdirectories, include the subdirectory name
                if stage_file.parent != stages_path:
                    stage_name = f"{stage_file.parent.name}/{stage_name}"
                stages.append(stage_name)
        
        print("Found stages:", stages)  # Debug print
        return stages

    def load_stats(self, file_path: Path) -> Dict:
        """Load statistics with validation and repair"""
        try:
            if not file_path.exists():
                print("No stats file found, starting fresh")
                return {}  # Return empty dict instead of None
            
            # Try to load main file
            try:
                with open(file_path, 'r') as f:
                    stats = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                print(f"Error loading stats file: {e}")
                # Try backup
                backup_path = file_path.with_suffix('.json.bak')
                if backup_path.exists():
                    print("Attempting to load from backup...")
                    with open(backup_path, 'r') as f:
                        stats = json.load(f)
                else:
                    print("No backup found, starting fresh")
                    return {}  # Return empty dict instead of None
            
            # Validate and repair stats
            if 'character_stats' in stats:
                self.character_stats = self._validate_character_stats(stats['character_stats'])
            if 'stage_stats' in stats:
                self.stage_stats = self._validate_stage_stats(stats['stage_stats'])
            if 'battle_durations' in stats:
                self.battle_durations = stats['battle_durations'][-1000:]  # Keep last 1000
                
            return stats  # Return the loaded stats
                
        except Exception as e:
            print(f"Error loading stats: {e}")
            traceback.print_exc()
            # Start fresh if all else fails
            return {}
    
    def _validate_character_stats(self, stats):
        """Validate and repair character statistics"""
        validated = {}
        for char, data in stats.items():
            if not isinstance(data, dict):
                data = {'wins': 0, 'losses': 0}
            if 'wins' not in data or not isinstance(data['wins'], int):
                data['wins'] = 0
            if 'losses' not in data or not isinstance(data['losses'], int):
                data['losses'] = 0
            validated[char] = data
        return validated
    
    def _validate_stage_stats(self, stats):
        """Validate and repair stage statistics"""
        validated = {}
        for stage, data in stats.items():
            if not isinstance(data, dict):
                data = {
                    "times_used": 0,
                    "last_used": "Never",
                    "total_duration": 0
                }
            if "times_used" not in data or not isinstance(data["times_used"], int):
                data["times_used"] = 0
            if "last_used" not in data:
                data["last_used"] = "Never"
            if "total_duration" not in data or not isinstance(data["total_duration"], (int, float)):
                data["total_duration"] = 0
            validated[stage] = data
        return validated

    def save_stats(self):
        """Save statistics with backup mechanism"""
        try:
            # Create backup of existing stats
            if self.stats_file.exists():
                backup_path = self.stats_file.with_suffix('.json.bak')
                import shutil
                shutil.copy2(self.stats_file, backup_path)
            
            # Save current stats with atomic write
            temp_path = self.stats_file.with_suffix('.json.tmp')
            stats_data = {
                'character_stats': self.character_stats,
                'stage_stats': self.stage_stats,
                'battle_durations': self.battle_durations[-1000:],  # Keep last 1000 battles
                'last_save': time.strftime("%Y-%m-%d %H:%M:%S")
            }
            
            # Write to temporary file first
            with open(temp_path, 'w') as f:
                json.dump(stats_data, f, indent=2)
            
            # Atomic rename
            import os
            os.replace(temp_path, self.stats_file)
            
        except Exception as e:
            print(f"Error saving stats: {e}")
            traceback.print_exc()
            # Try to restore from backup
            try:
                backup_path = self.stats_file.with_suffix('.json.bak')
                if backup_path.exists():
                    import shutil
                    shutil.copy2(backup_path, self.stats_file)
                    print("Restored stats from backup")
            except Exception as be:
                print(f"Error restoring backup: {be}")

    def update_stats(self, winner: str, loser: str):
        """Update win/loss statistics and matchup tracking"""
        # Update character stats
        for char in [winner, loser]:
            if char not in self.character_stats:
                self.character_stats[char] = {
                    "wins": 0,
                    "losses": 0,
                    "matchups": {},  # Store matchup data
                    "most_defeated": {},  # Track wins against specific characters
                    "most_lost_to": {}    # Track losses against specific characters
                }
        
        # Update overall wins/losses
        self.character_stats[winner]["wins"] += 1
        self.character_stats[loser]["losses"] += 1
        
        # Update matchup data for winner
        if loser not in self.character_stats[winner]["matchups"]:
            self.character_stats[winner]["matchups"][loser] = {"wins": 0, "losses": 0}
        self.character_stats[winner]["matchups"][loser]["wins"] += 1
        
        # Update most_defeated counter for winner
        if loser not in self.character_stats[winner]["most_defeated"]:
            self.character_stats[winner]["most_defeated"][loser] = 0
        self.character_stats[winner]["most_defeated"][loser] += 1
        
        # Update matchup data for loser
        if winner not in self.character_stats[loser]["matchups"]:
            self.character_stats[loser]["matchups"][winner] = {"wins": 0, "losses": 0}
        self.character_stats[loser]["matchups"][winner]["losses"] += 1
        
        # Update most_lost_to counter for loser
        if winner not in self.character_stats[loser]["most_lost_to"]:
            self.character_stats[loser]["most_lost_to"][winner] = 0
        self.character_stats[loser]["most_lost_to"][winner] += 1

        # Save immediately after update
        self.save_stats()

    def get_character_matchups(self, char_name: str) -> Dict:
        """Get detailed matchup statistics for a character"""
        if char_name not in self.character_stats:
            return {}
            
        stats = self.character_stats[char_name]
        matchups = stats.get("matchups", {})
        
        # Calculate win rates for each matchup
        detailed_matchups = {}
        for opponent, data in matchups.items():
            total_matches = data["wins"] + data["losses"]
            win_rate = (data["wins"] / total_matches * 100) if total_matches > 0 else 0
            detailed_matchups[opponent] = {
                "wins": data["wins"],
                "losses": data["losses"],
                "total_matches": total_matches,
                "win_rate": f"{win_rate:.1f}%"
            }
            
        return detailed_matchups

    def get_most_defeated_opponent(self, char_name: str) -> str:
        """Get the opponent that this character has defeated the most"""
        if char_name not in self.character_stats:
            return "N/A"
            
        most_defeated = self.character_stats[char_name].get("most_defeated", {})
        if not most_defeated:
            return "N/A"
            
        return max(most_defeated.items(), key=lambda x: x[1])[0]

    def get_most_lost_to_opponent(self, char_name: str) -> str:
        """Get the opponent that this character has lost to the most"""
        if char_name not in self.character_stats:
            return "N/A"
            
        most_lost_to = self.character_stats[char_name].get("most_lost_to", {})
        if not most_lost_to:
            return "N/A"
            
        return max(most_lost_to.items(), key=lambda x: x[1])[0]

    def update_stage_stats(self, stage: str):
        """Update stage usage statistics"""
        # Initialize stage stats if not exists or missing keys
        if stage not in self.stage_stats:
            self.stage_stats[stage] = {
                "times_used": 0,
                "last_used": "Never",
                "total_duration": 0
            }
        else:
            # Ensure all required keys exist
            if "times_used" not in self.stage_stats[stage]:
                self.stage_stats[stage]["times_used"] = 0
            if "last_used" not in self.stage_stats[stage]:
                self.stage_stats[stage]["last_used"] = "Never"
            if "total_duration" not in self.stage_stats[stage]:
                self.stage_stats[stage]["total_duration"] = 0
        
        # Update usage count and last used timestamp
        self.stage_stats[stage]["times_used"] += 1
        self.stage_stats[stage]["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")
        
        # Update duration if available
        if self.battle_start_time is not None:
            duration = time.time() - self.battle_start_time
            self.stage_stats[stage]["total_duration"] += duration
            self.battle_durations.append(duration)
            self.battle_start_time = None  # Reset for next battle
        
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

    def start_battle(self, battle_info=None):
        """Start a MUGEN battle with current settings and proper process management"""
        if not self.settings["enabled_characters"]:
            raise ValueError("No characters are enabled!")
        if not self.settings["enabled_stages"]:
            raise ValueError("No stages are enabled!")

        # Clean up any existing processes first
        try:
            subprocess.run('taskkill /F /IM mugen.exe', shell=True, stderr=subprocess.DEVNULL)
            time.sleep(0.5)  # Wait for process cleanup
        except:
            pass

        if self.watcher_process:
            try:
                self.watcher_process.terminate()
                time.sleep(0.5)
            except:
                pass
            self.watcher_process = None

        # Clean up log file
        if self.watcher_log.exists():
            try:
                self.watcher_log.unlink()
            except PermissionError:
                print("Warning: Could not delete old log file")
                time.sleep(0.5)
            try:
                self.watcher_log.unlink()
            except:
                pass

        # Start MugenWatcher before battle
        if not self.ensure_watcher_running():
            raise RuntimeError("Failed to start MugenWatcher")

        # Use provided battle info or prepare new one
        if battle_info is None:
            battle_info = self.prepare_battle()
        
        print("Starting battle with:", battle_info)

        # Note: Round time must be configured in MUGEN's system.def or fight.def files
        # Command line time parameter is not supported by MUGEN
        
        # Base command with MUGEN path and rounds
        cmd = [
            str(self.mugen_path),
            "-rounds", str(self.settings["rounds"])
        ]

        # Add character commands based on battle mode
        if battle_info['mode'] == "single":
            # Single mode: Each character has 2 rounds
            cmd.extend([
                "-p1", f"chars/{battle_info['p1']}/{battle_info['p1']}.def",
                "-p1.ai", "1",
                "-p2", f"chars/{battle_info['p2']}/{battle_info['p2']}.def",
                "-p2.ai", "1",
                "-p2.color", str(self.settings["p2_color"])
            ])
        elif battle_info['mode'] == "simul":
            # Simul mode: Characters fight simultaneously (max 2 per team)
            # First character of team 1
            cmd.extend([
                "-p1", f"chars/{battle_info['p1'][0]}/{battle_info['p1'][0]}.def",
                "-p1.ai", "1"
            ])
            
            # First character of team 2
            cmd.extend([
                "-p2", f"chars/{battle_info['p2'][0]}/{battle_info['p2'][0]}.def",
                "-p2.ai", "1",
                "-p2.color", str(self.settings["p2_color"])
            ])
            
            # Additional team 1 members
            for i, char in enumerate(battle_info['p1'][1:], 3):
                cmd.extend([
                    f"-p{i}", f"chars/{char}/{char}.def",
                    f"-p{i}.ai", "1"
                ])
            
            # Additional team 2 members
            for i, char in enumerate(battle_info['p2'][1:], 4):
                cmd.extend([
                    f"-p{i}", f"chars/{char}/{char}.def",
                    f"-p{i}.ai", "1",
                    f"-p{i}.color", str(self.settings["p2_color"])
                ])

        # Add stage with just the stage name (no path or extension)
        if battle_info['stage'].startswith('stages/'):
            stage_name = battle_info['stage'][7:]  # Remove 'stages/' prefix
        else:
            stage_name = battle_info['stage']
        cmd.extend(["-s", stage_name])

        # Convert command list to string with proper quoting
        cmd_str = f'"{cmd[0]}"'  # Quote the executable path
        for arg in cmd[1:]:
            # Quote any argument that contains spaces
            if ' ' in str(arg):
                cmd_str += f' "{arg}"'
            else:
                cmd_str += f' {arg}'

        print("Running command:", cmd_str)

        try:
            # Start MUGEN process
            process = subprocess.Popen(cmd_str, shell=True, cwd=str(self.mugen_path.parent))
            
            # Give more time for the process to start and be detected
            start_time = time.time()
            max_wait = 10  # Wait up to 10 seconds
            
            while time.time() - start_time < max_wait:
                if self._check_mugen_running():
                    # Process found, record battle start time and info
                    self.battle_start_time = time.time()
                    self.current_battle = battle_info
                    return battle_info
                time.sleep(0.2)  # Longer sleep between checks
            
            # If we get here, check one last time before giving up
            if self._check_mugen_running():
                self.battle_start_time = time.time()
                self.current_battle = battle_info
                return battle_info
                
            # Only raise error if process really isn't running
            if process.poll() is not None:  # Process has terminated
                raise RuntimeError("MUGEN process failed to start")
            else:
                # Process is running but not detected, proceed anyway
                print("Warning: MUGEN process not detected but seems to be running")
                self.battle_start_time = time.time()
                self.current_battle = battle_info
                return battle_info
                
        except Exception as e:
            print(f"Error starting battle: {e}")
            traceback.print_exc()
            # Clean up on error
            if process:
                try:
                    process.terminate()
                except:
                    pass
            raise

    def _check_mugen_running(self):
        """Check if any MUGEN process is running"""
        try:
            # Check for all possible MUGEN executables
            mugen_exes = ['mugen.exe', 'Mugen.exe', '3v3.exe', '4v4.exe']  # Added Mugen.exe variant
            for exe in mugen_exes:
                result = subprocess.run(
                    f'tasklist /FI "IMAGENAME eq {exe}" /NH', 
                    shell=True, 
                    capture_output=True, 
                    text=True
                )
                if exe.lower() in result.stdout.lower():  # Case-insensitive check
                    return True
            return False
        except Exception as e:
            print(f"Error checking MUGEN process: {e}")
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
        # If there's no current battle or the result was already processed, skip
        if not self.current_battle or getattr(self, '_battle_processed', False):
            return None
            
        p1_score, p2_score = result
        print(f"Processing battle result: P1={p1_score}, P2={p2_score}")  # Debug print
        
        # Set processed flag
        self._battle_processed = True
        
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
            self._cleanup_battle()
            
            print(f"Processed battle result: {battle_result}")  # Debug print
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
            self._cleanup_battle()
            
            print(f"Processed team battle result: {battle_result}")  # Debug print
            return battle_result

    def _cleanup_battle(self):
        """Clean up after a battle is complete"""
        if self.watcher_log.exists():
            try:
                self.watcher_log.unlink()
            except PermissionError:
                print("Warning: Could not delete watcher log")
                time.sleep(0.5)  # Wait briefly and try again
                try:
                    self.watcher_log.unlink()
                except:
                    pass
        
        self.current_battle = None
        self._battle_processed = False  # Reset processed flag for next battle

    def _start_single_battle(self, enabled_chars, stage):
        """Start a single 1v1 battle"""
        # Clear any existing watcher log
        if self.watcher_log.exists():
            try:
                self.watcher_log.unlink()
            except:
                pass

        p1 = random.choice(enabled_chars)
        p2 = random.choice([c for c in enabled_chars if c != p1])

        # Create stage path with proper escaping for spaces
        stage_path = f"stages/{stage}/{stage}.def"
        if " " in stage_path:
            stage_path = f'"{stage_path}"'

        cmd = [
            str(self.mugen_path),
            "-rounds", str(self.settings["rounds"]),
            "-p1.ai", "1",
            f"chars/{p1}/{p1}.def",
            "-p2.ai", "1",
            f"chars/{p2}/{p2}.def",
            "-p2.color", str(self.settings["p2_color"]),
            "-s", stage_path
        ]

        self.current_battle = {
            "mode": "single",
            "p1": p1,
            "p2": p2,
            "stage": stage,
            "command": cmd
        }

        # Start MUGEN process
        try:
            subprocess.Popen(cmd)
            print(f"Started MUGEN process with command: {cmd}")
            
            # Start watcher process if not already running
            if not self.watcher_process or self.watcher_process.poll() is not None:
                watcher_cmd = ["MugenWatcher.exe"]
                self.watcher_process = subprocess.Popen(watcher_cmd)
                print("Started MugenWatcher process")
        except Exception as e:
            print(f"Error starting battle: {e}")
            return None

        return self.current_battle

    def _start_team_battle(self, enabled_chars, stage):
        """Start a team battle where characters fight simultaneously"""
        if len(enabled_chars) < self.settings["team_size"] * 2:
            raise ValueError(f"Not enough characters for {self.settings['team_size']}v{self.settings['team_size']} team battle!")

        # Select teams
        team1 = random.sample(enabled_chars, self.settings["team_size"])
        remaining_chars = [c for c in enabled_chars if c not in team1]
        team2 = random.sample(remaining_chars, self.settings["team_size"])

        # Create stage path with proper escaping for spaces
        stage_path = f"stages/{stage}/{stage}.def"
        if " " in stage_path:
            stage_path = f'"{stage_path}"'

        cmd = [
            str(self.mugen_path),
            "-rounds", str(self.settings["rounds"]),
            "-p1.ai", "1",
            "-p1.teammember", str(self.settings["team_size"]),
        ]

        # Add team 1 members
        for char in team1:
            cmd.extend([f"chars/{char}/{char}.def"])

        # Add team 2 configuration
        cmd.extend([
            "-p2.ai", "1",
            "-p2.teammember", str(self.settings["team_size"]),
            "-p2.color", str(self.settings["p2_color"]),
        ])

        # Add team 2 members
        for char in team2:
            cmd.extend([f"chars/{char}/{char}.def"])

        # Add stage with proper path
        cmd.extend(["-s", stage_path])

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
        """Start a turns battle where characters fight one at a time"""
        if len(enabled_chars) < self.settings["team_size"] * 2:
            raise ValueError(f"Not enough characters for {self.settings['team_size']}v{self.settings['team_size']} turns battle!")

        # Select teams
        team1 = random.sample(enabled_chars, self.settings["team_size"])
        remaining_chars = [c for c in enabled_chars if c not in team1]
        team2 = random.sample(remaining_chars, self.settings["team_size"])

        # Create stage path with proper escaping for spaces
        stage_path = f"stages/{stage}/{stage}.def"
        if " " in stage_path:
            stage_path = f'"{stage_path}"'

        cmd = [
            str(self.mugen_path),
            "-rounds", str(self.settings["rounds"]),
            "-p1.ai", "1",
            "-p1.teammember", str(self.settings["team_size"]),
            "-tmode", "turns"
        ]

        # Add team 1 members
        for char in team1:
            cmd.extend([f"chars/{char}/{char}.def"])

        # Add team 2 configuration
        cmd.extend([
            "-p2.ai", "1",
            "-p2.teammember", str(self.settings["team_size"]),
            "-p2.color", str(self.settings["p2_color"]),
        ])

        # Add team 2 members
        for char in team2:
            cmd.extend([f"chars/{char}/{char}.def"])

        # Add stage with proper path
        cmd.extend(["-s", stage_path])

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
                f"chars/{char}/{char}.def",
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
                f"chars/{char}/{char}.def",
                f"-p2.life.{i+1}", str(life_mult)
            ])

        # Add stage - fix path format
        cmd.extend(["-s", f"{stage}.def"])

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

    def _read_battle_result(self) -> Optional[Dict]:
        """Read battle result from watcher log with Windows file locking"""
        if not self.watcher_log.exists():
            return None
            
        try:
            with open(self.watcher_log, 'r') as f:
                # Acquire lock for reading
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    lines = f.readlines()
                finally:
                    # Release lock
                    try:
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except:
                        pass
                
                if not lines:
                    return None
                
                # Process the last line for result
                last_line = lines[-1].strip()
                if not last_line:
                    return None
                
                try:
                    # Try parsing as JSON first
                    return json.loads(last_line)
                except json.JSONDecodeError:
                    # If not JSON, try parsing as comma-separated values
                    try:
                        values = last_line.split(',')
                        if len(values) >= 4:
                            # Format: timestamp, process_id, p1_score, p2_score
                            p1_score = int(values[2])
                            p2_score = int(values[3])
                            return [p1_score, p2_score]
                    except (ValueError, IndexError) as e:
                        print(f"Error parsing battle result values: {e}")
                    return None
                
        except IOError as e:
            if "Permission denied" in str(e):
                print("File is locked by another process")
                return None
            print(f"Error reading battle result: {e}")
            return None
        except Exception as e:
            print(f"Error reading battle result: {e}")
            traceback.print_exc()
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
                try:
                    self.watcher_process.terminate()
                    # Wait up to 3 seconds for process to terminate
                    for _ in range(30):
                        if self.watcher_process.poll() is not None:
                            break
                        time.sleep(0.1)
                    if self.watcher_process.poll() is None:
                        self.watcher_process.kill()  # Force kill if not terminated
                except Exception as e:
                    print(f"Error terminating watcher: {e}")
                self.watcher_process = None

            # Clean up old log file
            if self.watcher_log.exists():
                try:
                    self.watcher_log.unlink()
                except PermissionError:
                    print("Warning: Could not delete old log file")
                    time.sleep(0.5)  # Wait and try again
                    try:
                        self.watcher_log.unlink()
                    except:
                        pass

            # Check if MugenWatcher exists
            if not self.watcher_path.exists():
                print("Warning: MugenWatcher.exe not found. Battle results won't be tracked.")
                return False

            # Start MugenWatcher with timeout
            try:
                self.watcher_process = subprocess.Popen(
                    [str(self.watcher_path)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                # Wait up to 5 seconds for watcher to initialize
                for _ in range(50):
                    if self.watcher_log.exists():
                        break
                    time.sleep(0.1)
                return True
            except Exception as e:
                print(f"Error starting MugenWatcher: {e}")
                return False

        except Exception as e:
            print(f"Error in ensure_watcher_running: {e}")
            traceback.print_exc()
            return False

    def prepare_battle(self):
        """Prepare battle information based on current settings"""
        # Get enabled characters and stages
        enabled_chars = list(self.settings["enabled_characters"])
        enabled_stages = list(self.settings["enabled_stages"])
        
        if not enabled_chars:
            raise ValueError("No characters are enabled!")
        if not enabled_stages:
            raise ValueError("No stages are enabled!")
            
        # Select random stage
        stage = random.choice(enabled_stages)
        
        # Get battle mode from settings
        battle_mode = self.settings.get("battle_mode", "single")
        
        # Prepare battle info based on mode
        if battle_mode == "single":
            # Select two different characters
            p1 = random.choice(enabled_chars)
            p2 = random.choice([c for c in enabled_chars if c != p1])
            battle_info = {
                "mode": "single",
                "p1": p1,
                "p2": p2,
                "stage": stage
            }
        elif battle_mode == "simul":
            # For simul mode, limit to 2 players per team maximum
            team_size = min(2, self.settings.get("team_size", 2))
            
            if len(enabled_chars) < team_size * 2:
                raise ValueError(f"Not enough characters for {team_size}v{team_size} simul battle!")
            
            team1 = random.sample(enabled_chars, team_size)
            remaining_chars = [c for c in enabled_chars if c not in team1]
            team2 = random.sample(remaining_chars, team_size)
            
            battle_info = {
                "mode": "simul",
                "p1": team1,
                "p2": team2,
                "team1_size": team_size,
                "team2_size": team_size,
                "stage": stage
            }
        else:
            raise ValueError(f"Unknown battle mode: {battle_mode}")

        print("Prepared battle info:", battle_info)
        return battle_info

    def get_average_battle_duration(self) -> str:
        """Get the average battle duration as a formatted string"""
        if not self.battle_durations:
            return "No data"
        
        avg_duration = sum(self.battle_durations) / len(self.battle_durations)
        minutes = int(avg_duration // 60)
        seconds = int(avg_duration % 60)
        return f"{minutes}m {seconds}s"


class TwitchBot(commands.Bot):
    def __init__(self, token, channel, battle_gui, bot_name):
        # Initialize file paths
        self.user_points_file = Path("twitch_user_points.json")
        
        # Store instance variables
        self.channel_name = channel
        self.battle_gui = battle_gui
        self.betting_active = False
        self.current_bets = {"1": {}, "2": {}}
        self.user_points = self.load_user_points()
        self.commands_list = [
            "!bet [team] [amount] - Place a bet on team 1 or 2",
            "!points - Check your points",
            "!help - Show this help message",
            "!stats - Show current battle stats"
        ]
        
        # Add connection state tracking
        self.connected = False
        self.connection_retries = 0
        self.MAX_RETRIES = 3
        self.last_connection_attempt = 0
        self.RETRY_DELAY = 60  # seconds between retry attempts

        # Initialize the bot
        try:
            super().__init__(token=token, prefix='!', initial_channels=[channel], nick=bot_name)
            self.channel = None
        except Exception as e:
            print(f"Failed to initialize Twitch bot: {e}")
            traceback.print_exc()
            raise

    async def event_ready(self):
        """Called once when the bot goes online."""
        try:
            print(f"Bot is ready! Logged in as {self.nick}")
            
            # Wait for WebSocket connection
            await asyncio.sleep(2)
            
            # Get channel from connection
            if hasattr(self, '_connection') and self._connection:
                self.channel = self._connection._cache.get(self.channel_name.lower())
            
            if self.channel:
                print(f"Successfully connected to channel: {self.channel_name}")
                self.connected = True
                self.connection_retries = 0
                if self.battle_gui:
                    self.battle_gui.root.after(0, self.battle_gui.update_twitch_status, True)
                await self._connection.send(f"PRIVMSG #{self.channel_name} :MugenBattleBot connected! Type !help for commands")
            else:
                print(f"Could not connect to channel: {self.channel_name}")
                self.connected = False
                if self.battle_gui:
                    self.battle_gui.root.after(0, self.battle_gui.update_twitch_status, False)
                    
        except Exception as e:
            print(f"Error in event_ready: {e}")
            traceback.print_exc()
            self.connected = False
            if self.battle_gui:
                self.battle_gui.root.after(0, self.battle_gui.update_twitch_status, False)

    async def event_error(self, error: Exception, data: Optional[str] = None):
        """Handle connection errors"""
        print(f"Twitch bot error: {error}")
        traceback.print_exc()
        
        self.connected = False
        if self.battle_gui:
            self.battle_gui.root.after(0, self.battle_gui.update_twitch_status, False)
        
        # Attempt reconnection if appropriate
        current_time = time.time()
        if (current_time - self.last_connection_attempt > self.RETRY_DELAY and 
            self.connection_retries < self.MAX_RETRIES):
            self.connection_retries += 1
            self.last_connection_attempt = current_time
            print(f"Attempting reconnection (attempt {self.connection_retries}/{self.MAX_RETRIES})")
            try:
                await self.connect()
            except Exception as e:
                print(f"Reconnection failed: {e}")

    async def create_battle_poll(self, title, option1, option2, duration):
        """Start betting period with preview and error handling"""
        try:
            if not self.connected:
                print("Cannot create poll: Bot not connected")
                return False
                
            # Reset and initialize betting
            self.betting_active = True
            self.current_bets = {"1": {}, "2": {}}
            
            # Announce battle and betting with timeout
            async with asyncio.timeout(5):  # 5 second timeout for announcements
                await self._connection.send(f"PRIVMSG #{self.channel_name} :🎲 NEW BATTLE BETTING STARTED! 🎲")
                await self._connection.send(f"PRIVMSG #{self.channel_name} :⚔️ {title}")
                await self._connection.send(f"PRIVMSG #{self.channel_name} :Team 1: {option1}")
                await self._connection.send(f"PRIVMSG #{self.channel_name} :Team 2: {option2}")
                await self._connection.send(f"PRIVMSG #{self.channel_name} :Type !bet 1 <amount> to bet on Team 1")
                await self._connection.send(f"PRIVMSG #{self.channel_name} :Type !bet 2 <amount> to bet on Team 2")
                await self._connection.send(f"PRIVMSG #{self.channel_name} :New users get 1000 points! Betting closes in {duration} seconds!")
            
            return True
            
        except asyncio.TimeoutError:
            print("Timeout while creating battle poll")
            self.betting_active = False
            self.current_bets = {"1": {}, "2": {}}
            return False
        except Exception as e:
            print(f"Error creating betting: {e}")
            traceback.print_exc()
            self.betting_active = False
            self.current_bets = {"1": {}, "2": {}}
            return False

    async def handle_battle_result(self, winner, p1, p2):
        """Handle battle result and distribute points with error handling"""
        try:
            if not self.connected:
                print("Cannot handle result: Bot not connected")
                return
                
            # Format winner announcement
            if isinstance(winner, list):
                winner_text = "Team " + " & ".join(winner)
                loser_text = "Team " + " & ".join(p2 if winner == p1 else p1)
            else:
                winner_text = winner
                loser_text = p2 if winner == p1 else p1

            # Determine winning team
            winning_team = "1" if winner == p1 else "2"
            losing_team = "2" if winning_team == "1" else "1"
            
            # Calculate total pools
            winning_pool = sum(self.current_bets[winning_team].values())
            losing_pool = sum(self.current_bets[losing_team].values())
            total_pool = winning_pool + losing_pool
            
            # Track winners and their earnings
            winners_earnings = []
            
            # Announce results with timeout
            async with asyncio.timeout(5):
                await self._connection.send(f"PRIVMSG #{self.channel_name} :🏆 Battle Results! 🏆")
                await self._connection.send(f"PRIVMSG #{self.channel_name} :Winner: {winner_text}")
                await self._connection.send(f"PRIVMSG #{self.channel_name} :Defeated: {loser_text}")
            
            if total_pool > 0:
                # Calculate payout ratio
                if winning_pool > 0:
                    payout_ratio = (total_pool / winning_pool) if winning_pool > 0 else 2.0
                else:
                    payout_ratio = 2.0  # Default 2x payout if no winners
                
                # Distribute winnings with timeout
                async with asyncio.timeout(10):
                    for user, bet in self.current_bets[winning_team].items():
                        winnings = int(bet * payout_ratio)
                        self.user_points[user] = self.user_points.get(user, 0) + winnings
                        winners_earnings.append((user, winnings, bet))
                        await self._connection.send(
                            f"PRIVMSG #{self.channel_name} :💰 {user} won {winnings:,} points! "
                            f"(Bet: {bet:,}, Payout: {payout_ratio:.2f}x)"
                        )
                
                # Save updated points
                self.save_user_points()
            else:
                await self._connection.send(f"PRIVMSG #{self.channel_name} :No bets were placed on this battle!")
            
            # Announce top winners with timeout
            if winners_earnings:
                winners_earnings.sort(key=lambda x: x[1], reverse=True)
                async with asyncio.timeout(5):
                    await self._connection.send(f"PRIVMSG #{self.channel_name} :🎰 Top Winners 🎰")
                    for i, (user, winnings, bet) in enumerate(winners_earnings[:3], 1):
                        profit = winnings - bet
                        if i == 1:
                            await self._connection.send(
                                f"PRIVMSG #{self.channel_name} :🥇 {user} - Won: {winnings:,} points "
                                f"(Profit: {profit:,}) 🎊"
                            )
                        elif i == 2:
                            await self._connection.send(
                                f"PRIVMSG #{self.channel_name} :🥈 {user} - Won: {winnings:,} points "
                                f"(Profit: {profit:,})"
                            )
                        else:
                            await self._connection.send(
                                f"PRIVMSG #{self.channel_name} :🥉 {user} - Won: {winnings:,} points "
                                f"(Profit: {profit:,})"
                            )
            
        except asyncio.TimeoutError:
            print("Timeout while handling battle result")
        except Exception as e:
            print(f"Error handling battle result: {e}")
            traceback.print_exc()
        finally:
            # Always reset betting state
            self.betting_active = False
            self.current_bets = {"1": {}, "2": {}}

    def load_user_points(self) -> dict:
        """Load user points from file"""
        try:
            if self.user_points_file.exists():
                with open(self.user_points_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading user points: {e}")
        return {}

    def save_user_points(self):
        """Save user points to file"""
        try:
            with open(self.user_points_file, 'w') as f:
                json.dump(self.user_points, f, indent=2)
        except Exception as e:
            print(f"Error saving user points: {e}")

    @commands.command(name="bet")
    async def bet_command(self, ctx, team: str, amount: str):
        """Handle betting command"""
        if not self.betting_active:
            await ctx.send("No active betting at the moment!")
            return

        try:
            # Convert amount to integer
            bet_amount = int(amount)
            if bet_amount <= 0:
                await ctx.send("Bet amount must be positive!")
                return

            # Initialize new users with 1000 points
            if ctx.author.name not in self.user_points:
                self.user_points[ctx.author.name] = 1000
                await ctx.send(f"Welcome {ctx.author.name}! You received 1000 starting points!")
                self.save_user_points()

            # Check if user has enough points
            if bet_amount > self.user_points[ctx.author.name]:
                await ctx.send(f"Not enough points! You have {self.user_points[ctx.author.name]} points.")
                return

            # Place bet
            if team in ["1", "2"]:
                # Remove any existing bet
                for team_bets in self.current_bets.values():
                    if ctx.author.name in team_bets:
                        old_bet = team_bets.pop(ctx.author.name)
                        self.user_points[ctx.author.name] += old_bet

                # Place new bet
                self.current_bets[team][ctx.author.name] = bet_amount
                self.user_points[ctx.author.name] -= bet_amount
                await ctx.send(f"{ctx.author.name} bet {bet_amount} points on Team {team}!")
                self.save_user_points()

                # Update bet totals in GUI
                team1_total = sum(self.current_bets["1"].values())
                team2_total = sum(self.current_bets["2"].values())

                # Update preview tab totals
                if hasattr(self.battle_gui, 'preview_team1_total'):
                    self.battle_gui.preview_team1_total.config(text=f"{team1_total:,}")
                if hasattr(self.battle_gui, 'preview_team2_total'):
                    self.battle_gui.preview_team2_total.config(text=f"{team2_total:,}")
            else:
                await ctx.send("Invalid team! Use 1 or 2.")

        except ValueError:
            await ctx.send("Invalid bet amount!")

    @commands.command(name="points")
    async def points_command(self, ctx):
        """Show user's points"""
        points = self.user_points.get(ctx.author.name, 1000)
        if ctx.author.name not in self.user_points:
            self.user_points[ctx.author.name] = points
            await ctx.send(f"Welcome {ctx.author.name}! You received 1000 starting points!")
        else:
            await ctx.send(f"{ctx.author.name}, you have {points} points!")

    @commands.command(name="help")
    async def help_command(self, ctx):
        """Show available commands"""
        help_text = "Available commands:\n" + "\n".join(self.commands_list)
        await ctx.send(help_text)

    @commands.command(name="stats")
    async def stats_command(self, ctx):
        """Show current battle statistics"""
        if self.battle_gui.manager.current_battle:
            battle = self.battle_gui.manager.current_battle
            if battle["mode"] == "single":
                await ctx.send(f"Current Battle: {battle['p1']} vs {battle['p2']} on {battle['stage']}")
            else:
                team1 = " & ".join(battle['p1'])
                team2 = " & ".join(battle['p2'])
                await ctx.send(f"Current Battle: Team {team1} vs Team {team2} on {battle['stage']}")
        else:
            await ctx.send("No battle in progress")

    async def end_poll(self):
        """End the current betting period"""
        try:
            if self.betting_active:
                await self._connection.send(f"PRIVMSG #{self.channel_name} :⚠️ BETTING IS NOW CLOSED! ⚠️")
                self.betting_active = False
        except Exception as e:
            print(f"Error ending poll: {e}")
            traceback.print_exc()
            self.betting_active = False

class BattleGUI:
    def __init__(self, manager):
        """Initialize the GUI with the given battle manager"""
        self.manager = manager
        
        # Create root window first
        self.root = tk.Tk()
        self.root.title("MUGEN Battle Manager")
        self.root.geometry("1024x768")
        
        # Initialize battle tracking
        self.current_battle = None
        self.battle_monitor = None
        
        # Initialize settings dictionary
        self.settings = {
            "tab_order": ["Preview", "Battle", "Characters", "Stages", "Stats", "Tournament", "Settings"],
            "autosave": True,
            "theme": "light",
            "font_size": 10,
            "backup_frequency": "Weekly"
        }
        
        # Set window icon if available
        icon_path = Path("icon.ico")
        if icon_path.exists():
            self.root.iconbitmap(str(icon_path))

        # Initialize variables after root window is created
        self.mode_var = tk.StringVar(value="single")
        self.rounds_var = tk.StringVar(value="1")
        self.time_var = tk.StringVar(value="99")
        self.continuous_var = tk.BooleanVar(value=False)
        self.random_color_var = tk.BooleanVar(value=True)
        
        # Initialize battle settings variables
        self.team_size_var = tk.IntVar(value=3)
        self.team1_size_var = tk.IntVar(value=2)
        self.team2_size_var = tk.IntVar(value=2)
        self.random_team_sizes_var = tk.BooleanVar(value=True)
        
        # Initialize Twitch-related variables
        self.twitch_bot = None
        self.betting_duration = 20  # seconds
        self.preview_window = None
        self.twitch_token_var = tk.StringVar()
        self.twitch_channel_var = tk.StringVar()
        self.twitch_botname_var = tk.StringVar(value="MugenBattleBot")
        self.betting_duration_var = tk.IntVar(value=20)
        self.points_reward_var = tk.IntVar(value=100)
        
        # Initialize theme and style
        self.current_theme = "light"
        self.load_theme()
        
        # Create placeholder images
        self._create_placeholder_images()
        
        # Initialize battle log and status label
        self.battle_log = None
        self.twitch_status_label = None
        
        # Create menu
        self.create_menu()
        
        # Setup main GUI
        self.setup_gui()
        
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

        # Bind keyboard shortcuts
        self.root.bind('<F5>', lambda e: self._start_battle())
        self.root.bind('<F6>', lambda e: self.stop_battle())
        self.root.bind('<F7>', lambda e: self._quick_rematch())
        self.root.bind('<F8>', lambda e: self._force_next_round())
        self.root.bind('<F9>', lambda e: self._reset_battle_scores())
        self.root.bind('<F10>', lambda e: self._change_random_stage())

    def _create_placeholder_images(self):
        """Create placeholder images for portraits and stages"""
        # Create portrait placeholder
        portrait_size = (200, 200)
        portrait = Image.new('RGB', portrait_size, 'gray')
        draw = ImageDraw.Draw(portrait)
        draw.text((100, 100), "No\nPortrait", fill='white', anchor='mm', align='center')
        self.placeholder_portrait = ImageTk.PhotoImage(portrait)
        
        # Create stage placeholder
        stage_size = (400, 200)
        stage = Image.new('RGB', stage_size, 'darkgray')
        draw = ImageDraw.Draw(stage)
        draw.text((200, 100), "No Stage Preview", fill='white', anchor='mm')
        self.placeholder_stage = ImageTk.PhotoImage(stage)

    def setup_gui(self):
        """Setup the main GUI components"""
        # Configure styles for preview tab
        style = ttk.Style()
        style.configure("Preview.TFrame", background="black")
        style.configure("Name.TLabel", font=("Arial Black", 16, "bold"), background="black", foreground="white")
        style.configure("Stats.TLabel", font=("Arial", 12), background="black", foreground="white")
        style.configure("Dark.TFrame", background="black")
        
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Initialize tabs dictionary
        self.tabs = {}
        
        # Create all main tabs
        tab_names = ["Preview", "Battle", "Characters", "Stages", "Stats", "Tournament", "Settings"]
        for tab_name in tab_names:
            self.tabs[tab_name] = ttk.Frame(self.notebook)
            self.notebook.add(self.tabs[tab_name], text=tab_name)
        
        # Setup individual tabs
        self._setup_preview_tab()
        self._setup_battle_tab()
        self._setup_characters_tab()
        self._setup_stages_tab()
        self._setup_stats_tab()
        self._setup_tournament_tab()
        self._setup_settings_tab()
        
        # Setup draggable tabs
        self._setup_draggable_tabs()
        
        # Load saved tab order
        self._load_tab_order()
        
        # Configure auto-save
        self.auto_save_timer = None
        self.start_auto_save_timer()

    def _setup_tournament_tab(self):
        """Setup the tournament tab"""
        tournament_frame = ttk.Frame(self.notebook)
        self.notebook.add(tournament_frame, text='Tournament')
        
        # Create control frame
        control_frame = ttk.Frame(tournament_frame)
        control_frame.pack(fill='x', padx=5, pady=5)
        
        # Tournament settings
        settings_frame = ttk.LabelFrame(control_frame, text='Tournament Settings')
        settings_frame.pack(fill='x', padx=5, pady=5)
        
        # Bracket size selection
        size_frame = ttk.Frame(settings_frame)
        size_frame.pack(fill='x', padx=5, pady=5)
        
        ttk.Label(size_frame, text='Bracket Size:').pack(side='left', padx=5)
        self.bracket_size_var = tk.IntVar(value=8)  # Default to 8 players
        sizes = [4, 8, 16, 32, 64]
        bracket_size_menu = ttk.OptionMenu(size_frame, self.bracket_size_var, 8, *sizes)
        bracket_size_menu.pack(side='left', padx=5)
        
        # Tournament mode selection
        mode_frame = ttk.Frame(settings_frame)
        mode_frame.pack(fill='x', padx=5, pady=5)
        
        ttk.Label(mode_frame, text='Mode:').pack(side='left', padx=5)
        self.tournament_mode_var = tk.StringVar(value='single')
        modes = ['single', 'team', 'turns', 'simul']
        mode_menu = ttk.OptionMenu(mode_frame, self.tournament_mode_var, 'single', *modes)
        mode_menu.pack(side='left', padx=5)
        
        # Tournament controls
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(fill='x', padx=5, pady=5)
        
        self.create_tournament_btn = ttk.Button(
            button_frame, 
            text='Create Tournament',
            command=self._create_tournament
        )
        self.create_tournament_btn.pack(side='left', padx=5)
        
        self.start_tournament_btn = ttk.Button(
            button_frame,
            text='Start/Continue',
            command=self._start_tournament,
            state='disabled'
        )
        self.start_tournament_btn.pack(side='left', padx=5)
        
        self.reset_tournament_btn = ttk.Button(
            button_frame,
            text='Reset Tournament',
            command=self._reset_tournament,
            state='disabled'
        )
        self.reset_tournament_btn.pack(side='left', padx=5)
        
        # Create tournament bracket display
        bracket_frame = ttk.Frame(tournament_frame)
        bracket_frame.pack(fill='both', expand=True, padx=5, pady=5)
        
        # Create tournament tree
        self.tournament_tree = ttk.Treeview(
            bracket_frame,
            columns=('Match', 'Player 1', 'Player 2', 'Winner'),
            show='headings',
            selectmode='browse'
        )
        
        # Configure columns
        self.tournament_tree.heading('Match', text='Match')
        self.tournament_tree.heading('Player 1', text='Player 1')
        self.tournament_tree.heading('Player 2', text='Player 2')
        self.tournament_tree.heading('Winner', text='Winner')
        
        # Set column widths
        self.tournament_tree.column('Match', width=150)
        self.tournament_tree.column('Player 1', width=200)
        self.tournament_tree.column('Player 2', width=200)
        self.tournament_tree.column('Winner', width=150)
        
        # Add scrollbar
        tournament_scroll = ttk.Scrollbar(
            bracket_frame,
            orient='vertical',
            command=self.tournament_tree.yview
        )
        self.tournament_tree.configure(yscrollcommand=tournament_scroll.set)
        
        # Pack tournament tree and scrollbar
        self.tournament_tree.pack(side='left', fill='both', expand=True)
        tournament_scroll.pack(side='right', fill='y')
        
        # Create tournament log
        log_frame = ttk.LabelFrame(tournament_frame, text='Tournament Log')
        log_frame.pack(fill='x', padx=5, pady=5)
        
        self.tournament_log = tk.Text(log_frame, height=4, wrap='word')
        log_scroll = ttk.Scrollbar(log_frame, orient='vertical', command=self.tournament_log.yview)
        self.tournament_log.configure(yscrollcommand=log_scroll.set)
        
        self.tournament_log.pack(side='left', fill='both', expand=True)
        log_scroll.pack(side='right', fill='y')

    def _create_tournament(self):
        """Create a new tournament bracket"""
        try:
            # Get enabled characters - check both settings and current state
            enabled_chars = []
            
            # Get all items from character tree
            for item in self.char_tree.get_children():
                values = self.char_tree.item(item)['values']
                char_name = values[0]  # Character name is in first column
                enabled = values[-1]   # Enabled status is in last column
                if enabled == '✓':     # Check for checkmark
                    enabled_chars.append(char_name)
            
            # Update settings to match current state
            self.manager.settings["enabled_chars"] = set(enabled_chars)
            
            if not enabled_chars:
                messagebox.showerror("Error", "No characters enabled! Enable at least two characters.")
                return
                
            if len(enabled_chars) < 2:
                messagebox.showerror("Error", "Not enough characters! Enable at least two characters.")
                return

            # Get tournament settings
            bracket_size = self.bracket_size_var.get()
            tournament_mode = self.tournament_mode_var.get()
            
            # Validate bracket size
            if bracket_size > len(enabled_chars):
                messagebox.showerror("Error", f"Not enough characters for {bracket_size}-player tournament! Enable more characters or reduce bracket size.")
                return
                
            # Randomly select characters if needed
            if bracket_size < len(enabled_chars):
                tournament_chars = random.sample(enabled_chars, bracket_size)
            else:
                tournament_chars = enabled_chars.copy()
                
            # Shuffle the characters
            random.shuffle(tournament_chars)
            
            # Calculate number of rounds
            num_rounds = (bracket_size - 1).bit_length()  # log2 ceiling
            
            # Create tournament structure
            self.tournament_data = {
                'mode': tournament_mode,
                'bracket_size': bracket_size,
                'current_round': 1,
                'total_rounds': num_rounds,
                'matches': [],
                'winners': [],
                'active_match': None
            }
            
            # Create first round matches
            matches = []
            for i in range(0, len(tournament_chars), 2):
                if i + 1 < len(tournament_chars):
                    matches.append({
                        'round': 1,
                        'p1': tournament_chars[i],
                        'p2': tournament_chars[i + 1],
                        'winner': None,
                        'completed': False
                    })
                else:
                    # Handle bye matches for odd numbers
                    matches.append({
                        'round': 1,
                        'p1': tournament_chars[i],
                        'p2': None,  # Bye
                        'winner': tournament_chars[i],
                        'completed': True
                    })
            
            self.tournament_data['matches'] = matches
            
            # Clear and populate tournament tree
            for item in self.tournament_tree.get_children():
                self.tournament_tree.delete(item)
                
            # Add matches to tree
            for i, match in enumerate(matches, 1):
                p1 = match['p1'] if match['p1'] else "BYE"
                p2 = match['p2'] if match['p2'] else "BYE"
                self.tournament_tree.insert('', 'end', values=(
                    f"Round 1 - Match {i}",
                    p1,
                    p2,
                    match['winner'] if match['winner'] else "Pending"
                ))
            
            # Enable tournament controls
            self.start_tournament_btn.config(state='normal')
            self.reset_tournament_btn.config(state='normal')
            
            # Log tournament creation
            if hasattr(self, 'battle_log'):
                timestamp = time.strftime("%H:%M:%S")
                log_text = f"[{timestamp}] Created {bracket_size}-player tournament in {tournament_mode} mode\n"
                self.battle_log.insert(tk.END, log_text)
                self.battle_log.see(tk.END)
            
            messagebox.showinfo("Success", f"Created {bracket_size}-player tournament!")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to create tournament: {str(e)}")
            print(f"Error creating tournament: {e}")
            traceback.print_exc()
            
            # Reset tournament data
            self.tournament_data = None
            
            # Disable tournament controls
            self.start_tournament_btn.config(state='disabled')
            self.reset_tournament_btn.config(state='disabled')

    def _start_tournament(self):
        """Start or continue tournament matches"""
        try:
            # Validate tournament data exists
            if not hasattr(self, 'tournament_data') or not self.tournament_data:
                messagebox.showerror("Error", "No tournament created! Create a tournament first.")
                return
                
            # Check if a battle is already in progress
            if hasattr(self, 'battle_monitor') and self.battle_monitor:
                messagebox.showwarning("Warning", "A battle is already in progress!")
                return
                
            # Check if tournament is complete
            if self.tournament_data['current_round'] > self.tournament_data['total_rounds']:
                messagebox.showinfo("Tournament Complete", "Tournament is finished!")
                return
                
            # Find next unfinished match
            current_match = None
            for match in self.tournament_data['matches']:
                if match['round'] == self.tournament_data['current_round'] and not match['completed']:
                    current_match = match
                    break
                    
            if not current_match:
                # All matches in current round complete, advance to next round
                self.tournament_data['current_round'] += 1
                self._create_next_round_matches()
                return
                
            # Set active match
            self.tournament_data['active_match'] = current_match
            
            # Prepare battle information
            battle_info = {
                'mode': self.tournament_data['mode'],
                'p1': current_match['p1'],
                'p2': current_match['p2'],
                'stage': random.choice(list(self.manager.settings["enabled_stages"]))
            }
            
            # Update preview tab
            self._update_preview_tab(battle_info)
            
            # Start the battle
            self.manager.start_battle(battle_info)
            
            # Start monitoring battle
            self.battle_monitor = self.root.after(1000, self._check_tournament_battle)
            
            # Log match start
            if hasattr(self, 'battle_log'):
                timestamp = time.strftime("%H:%M:%S")
                log_text = f"[{timestamp}] Tournament Round {self.tournament_data['current_round']} - {battle_info['p1']} vs {battle_info['p2']}\n"
                self.battle_log.insert(tk.END, log_text)
                self.battle_log.see(tk.END)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to start tournament match: {str(e)}")
            print(f"Error starting tournament match: {e}")
            traceback.print_exc()
            
            # Reset battle monitor
            if hasattr(self, 'battle_monitor') and self.battle_monitor:
                self.root.after_cancel(self.battle_monitor)
                self.battle_monitor = None

    def _check_tournament_battle(self):
        """Check the result of the current tournament battle"""
        try:
            if not self.tournament_data or not self.tournament_data['active_match']:
                return
                
            result = self.manager.check_battle_result()
            if not result:
                # Battle still ongoing, check again in 1 second
                self.battle_monitor = self.root.after(1000, self._check_tournament_battle)
                return
                
            # Process battle result
            winner = result['winner']
            active_match = self.tournament_data['active_match']
            
            # Update match data
            active_match['winner'] = winner
            active_match['completed'] = True
            
            # Update tournament tree
            for item in self.tournament_tree.get_children():
                values = self.tournament_tree.item(item)['values']
                if values[1] == active_match['p1'] and values[2] == active_match['p2']:
                    self.tournament_tree.item(item, values=(
                        values[0],  # Round/Match info
                        values[1],  # P1
                        values[2],  # P2
                        winner      # Winner
                    ))
                    break
            
            # Log match result
            if hasattr(self, 'tournament_log'):
                timestamp = time.strftime("%H:%M:%S")
                log_text = f"[{timestamp}] Tournament Match Result: {winner} wins!\n"
                self.tournament_log.insert(tk.END, log_text)
                self.tournament_log.see(tk.END)
            
            # Clear battle monitor and active match
            self.battle_monitor = None
            self.tournament_data['active_match'] = None
            
            # Check if current round is complete
            current_round = self.tournament_data['current_round']
            current_matches = [m for m in self.tournament_data['matches'] 
                             if m['round'] == current_round]
            
            all_complete = all(m['completed'] for m in current_matches)
            
            if all_complete:
                if current_round == self.tournament_data['total_rounds']:
                    # Tournament complete
                    messagebox.showinfo("Tournament Complete", f"Tournament Winner: {winner}!")
                    self.tournament_data = None
                    self.start_tournament_btn.config(state='disabled')
                    self.reset_tournament_btn.config(state='disabled')
                else:
                    # Log round completion
                    if hasattr(self, 'tournament_log'):
                        timestamp = time.strftime("%H:%M:%S")
                        log_text = f"[{timestamp}] Round {current_round} complete! Starting Round {current_round + 1}\n"
                        self.tournament_log.insert(tk.END, log_text)
                        self.tournament_log.see(tk.END)
                    
                    # Advance to next round
                    self.tournament_data['current_round'] += 1
                    self.root.after(2000, self._create_next_round_matches)
            else:
                # Continue with next match in current round
                self.root.after(2000, self._start_tournament)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to check tournament battle: {str(e)}")
            print(f"Error checking tournament battle: {e}")
            traceback.print_exc()
            
            # Clear battle monitor on error
            self.battle_monitor = None
            if self.tournament_data:
                self.tournament_data['active_match'] = None

    def _create_next_round_matches(self):
        """Create matches for the next tournament round"""
        try:
            current_round = self.tournament_data['current_round']
            
            # Get winners from previous round
            previous_matches = [m for m in self.tournament_data['matches'] 
                             if m['round'] == current_round - 1 and m['completed']]
            
            if not previous_matches:
                messagebox.showerror("Error", "No completed matches found from previous round!")
                return
                
            # Clear existing matches for the current round (prevent duplicates)
            self.tournament_data['matches'] = [m for m in self.tournament_data['matches'] 
                                             if m['round'] != current_round]
            
            # Create new matches from winners of previous round
            new_matches = []
            for i in range(0, len(previous_matches), 2):
                p1 = previous_matches[i]['winner']
                p2 = previous_matches[i + 1]['winner'] if i + 1 < len(previous_matches) else None
                
                new_match = {
                    'round': current_round,
                    'p1': p1,
                    'p2': p2,
                    'winner': None,
                    'completed': False if p2 else True  # Auto-complete if bye
                }
                
                if not p2:  # Handle bye
                    new_match['winner'] = p1
                
                new_matches.append(new_match)
            
            # Add new matches to tournament data
            self.tournament_data['matches'].extend(new_matches)
            
            # Update tournament tree
            # First remove all matches from current round
            for item in self.tournament_tree.get_children():
                values = self.tournament_tree.item(item)['values']
                if values[0].startswith(f"Round {current_round}"):
                    self.tournament_tree.delete(item)
            
            # Add new matches
            for i, match in enumerate(new_matches, 1):
                self.tournament_tree.insert('', 'end', values=(
                    f"Round {current_round} - Match {i}",
                    match['p1'],
                    match['p2'] if match['p2'] else "BYE",
                    match['winner'] if match['winner'] else "Pending"
                ))
            
            # Log round start
            if hasattr(self, 'tournament_log'):
                timestamp = time.strftime("%H:%M:%S")
                log_text = f"[{timestamp}] Starting Round {current_round}\n"
                self.tournament_log.insert(tk.END, log_text)
                self.tournament_log.see(tk.END)
            
            # Start next match if there are actual matches (not just byes)
            if any(not m['completed'] for m in new_matches):
                self.root.after(2000, self._start_tournament)
            elif current_round == self.tournament_data['total_rounds']:
                # Tournament complete with final bye
                winner = new_matches[0]['winner']
                messagebox.showinfo("Tournament Complete", f"Tournament Winner: {winner}!")
                self.tournament_data = None
                self.start_tournament_btn.config(state='disabled')
                self.reset_tournament_btn.config(state='disabled')

        except Exception as e:
            messagebox.showerror("Error", f"Failed to create next round: {str(e)}")
            print(f"Error creating next round: {e}")
            traceback.print_exc()

    def _reset_tournament(self):
        """Reset the tournament to initial state"""
        try:
            # Clear tournament tree
            for item in self.tournament_tree.get_children():
                self.tournament_tree.delete(item)
            
            # Reset tournament data
            self.tournament_data = None
            
            # Reset button states
            self.start_tournament_btn.config(state='disabled')
            self.reset_tournament_btn.config(state='disabled')
            
            # Log reset
            if hasattr(self, 'tournament_log'):
                timestamp = time.strftime("%H:%M:%S")
                log_text = f"[{timestamp}] Tournament reset\n"
                self.tournament_log.insert(tk.END, log_text)
                self.tournament_log.see(tk.END)
            
            # Enable tournament creation
            self.create_tournament_btn.config(state='normal')

        except Exception as e:
            messagebox.showerror("Error", f"Failed to check battle result: {str(e)}")
            print(f"Error checking battle result: {e}")
            traceback.print_exc()
            
            # Clear battle monitor on error
            self.battle_monitor = None
            
            # Enable controls
            if hasattr(self, 'start_tournament_btn'):
                self.start_tournament_btn.config(state='normal')
            if hasattr(self, 'start_battle_btn'):
                self.start_battle_btn.config(state='normal')

    def _quick_rematch(self):
        """Start a rematch using the same characters and stage"""
        try:
            # Check if there's a previous battle to rematch
            if not hasattr(self, 'prepared_battle_info') or not self.prepared_battle_info:
                messagebox.showwarning("Warning", "No previous battle to rematch!")
                return

            # Check if a battle is already running
            if self.battle_monitor:
                messagebox.showwarning("Warning", "A battle is already in progress!")
                return

            # Store the previous battle info
            previous_battle = self.prepared_battle_info.copy()

            # Update preview tab with battle info
            self._update_preview_tab(previous_battle)

            # If Twitch bot is connected and active, start betting period
            if hasattr(self, 'twitch_bot') and self.twitch_bot and self.twitch_bot.connected:
                # Format battle title based on mode
                if previous_battle['mode'] == "single":
                    title = f"REMATCH: {previous_battle['p1']} vs {previous_battle['p2']}"
                    team1 = previous_battle['p1']
                    team2 = previous_battle['p2']
                else:
                    team1 = " & ".join(previous_battle['p1'])
                    team2 = " & ".join(previous_battle['p2'])
                    title = f"REMATCH: Team {team1} vs Team {team2}"

                # Start betting period
                betting_duration = self.betting_duration_var.get()
                asyncio.run_coroutine_threadsafe(
                    self.twitch_bot.create_battle_poll(title, team1, team2, betting_duration),
                    self.twitch_bot.loop
                )
                
                # Start betting timer
                self._update_betting_timer(betting_duration)
            else:
                # Start battle immediately if no Twitch integration
                self.prepared_battle_info = previous_battle
                self._start_actual_battle()

            # Log rematch
            if hasattr(self, 'battle_log'):
                timestamp = time.strftime("%H:%M:%S")
                log_text = f"[{timestamp}] Starting rematch...\n"
                self.battle_log.insert(tk.END, log_text)
                self.battle_log.see(tk.END)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to start rematch: {str(e)}")
            print(f"Error starting rematch: {e}")
            traceback.print_exc()

    def _force_next_round(self):
        """Force the current battle to proceed to the next round"""
        try:
            # Check if a battle is running
            if not self.battle_monitor:
                messagebox.showwarning("Warning", "No battle in progress!")
                return

            # Check if MUGEN is running
            if not self.manager._check_mugen_running():
                messagebox.showwarning("Warning", "MUGEN is not running!")
                return

            # Try to force the next round by simulating a round end
            try:
                # Kill the current MUGEN process
                subprocess.run('taskkill /F /IM mugen.exe', shell=True, stderr=subprocess.DEVNULL)
                time.sleep(0.5)  # Wait for process cleanup
                
                # Restart the battle with the same info
                if hasattr(self, 'prepared_battle_info') and self.prepared_battle_info:
                    self._start_actual_battle()
                    
                    # Log the forced round
                    if hasattr(self, 'battle_log'):
                        timestamp = time.strftime("%H:%M:%S")
                        log_text = f"[{timestamp}] Forced next round\n"
                        self.battle_log.insert(tk.END, log_text)
                        self.battle_log.see(tk.END)
                else:
                    messagebox.showerror("Error", "No battle information available!")
                    
            except Exception as e:
                messagebox.showerror("Error", f"Failed to force next round: {str(e)}")
                print(f"Error forcing next round: {e}")
                traceback.print_exc()

        except Exception as e:
            messagebox.showerror("Error", f"Failed to force next round: {str(e)}")
            print(f"Error in force_next_round: {e}")
            traceback.print_exc()

    def _reset_battle_scores(self):
        """Reset all battle scores and statistics"""
        try:
            # Confirm with user before resetting
            if not messagebox.askyesno("Confirm Reset", 
                "Are you sure you want to reset all battle scores and statistics? This cannot be undone."):
                return

            # Reset character statistics
            self.manager.character_stats = {}
            for char in self.manager.characters:
                self.manager.character_stats[char] = {
                    "wins": 0,
                    "losses": 0,
                    "matchups": {},
                    "most_defeated": {},
                    "most_lost_to": {}
                }

            # Reset stage statistics
            self.manager.stage_stats = {}
            for stage in self.manager.stages:
                self.manager.stage_stats[stage] = {
                    "times_used": 0,
                    "last_used": "Never",
                    "total_duration": 0
                }

            # Reset battle durations
            self.manager.battle_durations = []

            # Reset battle history
            self.manager.battle_history = {
                "battles": [],
                "last_save": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            # Save the reset stats
            self.manager.save_stats()
            self.manager.save_battle_history()

            # Update the UI
            self._update_stats_view()

            # Log the reset
            if hasattr(self, 'battle_log'):
                timestamp = time.strftime("%H:%M:%S")
                log_text = f"[{timestamp}] Reset all battle scores and statistics\n"
                self.battle_log.insert(tk.END, log_text)
                self.battle_log.see(tk.END)

            messagebox.showinfo("Success", "All battle scores and statistics have been reset!")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to reset scores: {str(e)}")
            print(f"Error resetting scores: {e}")
            traceback.print_exc()

    def _change_random_stage(self):
        """Change to a random stage during battle"""
        try:
            # Check if a battle is in progress
            if not self.battle_monitor:
                messagebox.showwarning("Warning", "No battle in progress!")
                return

            # Check if there are enabled stages
            enabled_stages = list(self.manager.settings["enabled_stages"])
            if not enabled_stages:
                messagebox.showerror("Error", "No stages enabled! Enable at least one stage.")
                return

            # Select a random stage
            new_stage = random.choice(enabled_stages)
            
            # Update battle info
            if hasattr(self, 'prepared_battle_info') and self.prepared_battle_info:
                self.prepared_battle_info['stage'] = new_stage
                
                # Restart the battle with new stage
                self._start_actual_battle()
                
                # Log the stage change
                if hasattr(self, 'battle_log'):
                    timestamp = time.strftime("%H:%M:%S")
                    log_text = f"[{timestamp}] Changed stage to: {new_stage}\n"
                    self.battle_log.insert(tk.END, log_text)
                    self.battle_log.see(tk.END)
            else:
                messagebox.showerror("Error", "No battle information available!")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to change stage: {str(e)}")
            print(f"Error changing stage: {e}")
            traceback.print_exc()

    def _toggle_ai_level(self):
        """Toggle AI difficulty level"""
        try:
            # Check if a battle is in progress
            if not self.battle_monitor:
                messagebox.showwarning("Warning", "No battle in progress!")
                return

            # Toggle AI level (1-8 are standard MUGEN AI levels)
            current_level = getattr(self, 'current_ai_level', 1)
            new_level = current_level + 1 if current_level < 8 else 1
            self.current_ai_level = new_level

            # Update battle info with new AI level
            if hasattr(self, 'prepared_battle_info') and self.prepared_battle_info:
                self.prepared_battle_info['ai_level'] = new_level
                
                # Restart the battle with new AI level
                self._start_actual_battle()
                
                # Log the AI level change
                if hasattr(self, 'battle_log'):
                    timestamp = time.strftime("%H:%M:%S")
                    log_text = f"[{timestamp}] Changed AI level to: {new_level}\n"
                    self.battle_log.insert(tk.END, log_text)
                    self.battle_log.see(tk.END)
            else:
                messagebox.showerror("Error", "No battle information available!")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to toggle AI level: {str(e)}")
            print(f"Error toggling AI level: {e}")
            traceback.print_exc()

    def _start_battle_after_betting(self):
        """Force start battle immediately after current betting period"""
        try:
            # Check if betting is active
            if hasattr(self, 'twitch_bot') and self.twitch_bot and self.twitch_bot.betting_active:
                # End betting period
                asyncio.run_coroutine_threadsafe(
                    self.twitch_bot.end_poll(),
                    self.twitch_bot.loop
                )
                
                # Start battle immediately
                self._start_actual_battle()
                
                # Log the forced start
                if hasattr(self, 'battle_log'):
                    timestamp = time.strftime("%H:%M:%S")
                    log_text = f"[{timestamp}] Forced battle start after betting\n"
                    self.battle_log.insert(tk.END, log_text)
                    self.battle_log.see(tk.END)
            else:
                messagebox.showinfo("Info", "No active betting period to end!")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to force battle start: {str(e)}")
            print(f"Error forcing battle start: {e}")
            traceback.print_exc()

    def _connect_twitch(self):
        """Connect or disconnect from Twitch chat"""
        try:
            if hasattr(self, 'twitch_bot') and self.twitch_bot and self.twitch_bot.connected:
                # Disconnect if already connected
                if hasattr(self.twitch_bot, 'loop') and self.twitch_bot.loop:
                    self.twitch_bot.loop.stop()
                self.twitch_bot = None
                self.update_twitch_status(False)
                messagebox.showinfo("Success", "Disconnected from Twitch chat")
                return

            # Validate Twitch settings
            token = self.twitch_token_var.get().strip()
            channel = self.twitch_channel_var.get().strip().lower()
            bot_name = self.twitch_botname_var.get().strip().lower()

            if not all([token, channel, bot_name]):
                messagebox.showerror("Error", "Please fill in all Twitch settings (Token, Channel, Bot Name)")
                return

            try:
                # Create and initialize Twitch bot
                self.twitch_bot = TwitchBot(
                    token=token,
                    channel=channel,
                    battle_gui=self,
                    bot_name=bot_name
                )

                # Start bot in a separate thread
                def run_bot():
                    try:
                        self.twitch_bot.run()
                    except Exception as e:
                        print(f"Twitch bot error: {e}")
                        messagebox.showerror("Error", f"Twitch bot error: {str(e)}")
                        self.twitch_bot = None
                        self.update_twitch_status(False)

                threading.Thread(target=run_bot, daemon=True).start()

                # Save Twitch settings
                self.save_config()

                messagebox.showinfo("Success", f"Connected to Twitch channel: {channel}")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to connect to Twitch: {str(e)}")
                self.twitch_bot = None
                self.update_twitch_status(False)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to handle Twitch connection: {str(e)}")
            print(f"Error handling Twitch connection: {e}")
            traceback.print_exc()
            self.update_twitch_status(False)

    def stop_battle(self):
        """Stop the current battle"""
        if self.battle_monitor:
            self.root.after_cancel(self.battle_monitor)
            self.battle_monitor = None
        
        if self.current_battle:
            self.current_battle = None
            
        self.manager.stop_battle()

    def update_twitch_status(self, connected: bool):
        """Update Twitch connection status in GUI"""
        timestamp = time.strftime("%H:%M:%S")
        if connected:
            bot_name = self.twitch_bot.nick if self.twitch_bot else "Unknown"
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
        if hasattr(self, 'battle_log'):
            self.battle_log.insert(tk.END, status_text)
            self.battle_log.see(tk.END)

    def auto_save_config(self):
        """Auto-save configuration to default file"""
        if not self.autosave_var.get():
            return
        
        config = {
            "battle_settings": {
                "rounds": self.rounds_var.get(),
                "time": self.time_var.get(),  # Add time setting
                "battle_mode": self.mode_var.get(),
                "team_size": self.team_size_var.get(),
                "continuous_mode": self.continuous_var.get(),
                "enabled_characters": set(self.characters),  # Initially enable all characters
                "enabled_stages": set(self.stages)  # Initially enable all stages
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

    def _update_preview_tab(self, battle_info):
        """Update the preview tab with battle information"""
        try:
            # Clear existing preview display
            for widget in self.preview_display.winfo_children():
                widget.destroy()

            # Create new team frames
            team1_frame = ttk.Frame(self.preview_display, style="Preview.TFrame")
            team1_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

            vs_frame = ttk.Frame(self.preview_display, style="Preview.TFrame")
            vs_frame.pack(side=tk.LEFT, padx=20)
            ttk.Label(vs_frame, text="VS", style="Name.TLabel").pack(pady=5)

            team2_frame = ttk.Frame(self.preview_display, style="Preview.TFrame")
            team2_frame.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH)

            # Display fighters based on battle mode
            if battle_info['mode'] == "single":
                self._create_fighter_display(team1_frame, battle_info['p1'], "Player 1", "left")
                self._create_fighter_display(team2_frame, battle_info['p2'], "Player 2", "right")
            else:
                self._create_team_display(team1_frame, battle_info['p1'], "Team 1", "left")
                self._create_team_display(team2_frame, battle_info['p2'], "Team 2", "right")

            # Update stage preview
            stage_path = self.manager.stages_path / battle_info['stage'] / "preview.png"
            try:
                if stage_path.exists():
                    img = Image.open(stage_path)
                    img.thumbnail((400, 225))  # 16:9 aspect ratio
                    photo = ImageTk.PhotoImage(img)
                    self.stage_preview.configure(image=photo)
                    self.stage_preview.image = photo
            except Exception as e:
                print(f"Error loading stage preview: {e}")
                self.stage_preview.configure(image=self.placeholder_stage)
                self.stage_preview.image = self.placeholder_stage

        except Exception as e:
            print(f"Error updating preview tab: {e}")
            traceback.print_exc()

    def _setup_battle_tab(self):
        """Setup the battle tab with all controls"""
        battle_frame = ttk.Frame(self.tabs["Battle"])
        battle_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Top control panel
        control_frame = ttk.LabelFrame(battle_frame, text="Battle Controls")
        control_frame.pack(fill=tk.X, padx=5, pady=5)

        # Create three columns for controls
        left_frame = ttk.Frame(control_frame)
        left_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        middle_frame = ttk.Frame(control_frame)
        middle_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        right_frame = ttk.Frame(control_frame)
        right_frame.pack(side="left", fill="both", expand=True, padx=5, pady=5)

        # Left column - Main Controls
        ttk.Label(left_frame, text="Main Controls", font=("Segoe UI", 10, "bold")).pack(pady=5)
        
        ttk.Button(
            left_frame,
            text="▶ Start Battle (F5)",
            command=self._start_battle,
            style="Accent.TButton"
        ).pack(fill="x", pady=2)

        ttk.Button(
            left_frame,
            text="⏹ Stop Battle (F6)",
            command=self.stop_battle
        ).pack(fill="x", pady=2)

        ttk.Button(
            left_frame,
            text="⟳ Quick Rematch (F7)",
            command=self._quick_rematch
        ).pack(fill="x", pady=2)

        # Middle column - Battle Flow
        ttk.Label(middle_frame, text="Battle Flow", font=("Segoe UI", 10, "bold")).pack(pady=5)
        
        ttk.Button(
            middle_frame,
            text="⏭ Force Next Round (F8)",
            command=self._force_next_round
        ).pack(fill="x", pady=2)

        ttk.Button(
            middle_frame,
            text="↺ Reset Scores (F9)",
            command=self._reset_battle_scores
        ).pack(fill="x", pady=2)

        ttk.Button(
            middle_frame,
            text="🎲 Random Stage (F10)",
            command=self._change_random_stage
        ).pack(fill="x", pady=2)

        # Right column - Additional Controls
        ttk.Label(right_frame, text="Additional Controls", font=("Segoe UI", 10, "bold")).pack(pady=5)
        
        ttk.Button(
            right_frame,
            text="⚡ Force Start (F11)",
            command=lambda: self._start_battle_after_betting()
        ).pack(fill="x", pady=2)

        ttk.Button(
            right_frame,
            text="🎯 Toggle AI Level (F12)",
            command=self._toggle_ai_level
        ).pack(fill="x", pady=2)

        # Battle Mode Selection with icons
        mode_frame = ttk.LabelFrame(battle_frame, text="Battle Mode")
        mode_frame.pack(fill="x", padx=10, pady=5)

        self.mode_var = tk.StringVar(value="single")
        modes = [
            ("👤 Single Battle", "single"),
            ("⚔ Simul Battle", "simul")
        ]
        
        for text, mode in modes:
            ttk.Radiobutton(
                mode_frame,
                text=text,
                value=mode,
                variable=self.mode_var,
                command=self._update_manager_settings
            ).pack(side="left", padx=10, pady=5)

        # Battle Settings with better organization
        settings_frame = ttk.LabelFrame(battle_frame, text="Battle Settings")
        settings_frame.pack(fill="x", padx=10, pady=5)

        # Settings grid
        settings_grid = ttk.Frame(settings_frame)
        settings_grid.pack(fill="x", padx=5, pady=5)

        # Row 1: Rounds and Time settings
        ttk.Label(settings_grid, text="Rounds:").grid(row=0, column=0, padx=5)
        rounds_spinbox = ttk.Spinbox(
            settings_grid,
            from_=1,
            to=9,
            width=5,
            textvariable=self.rounds_var,
            command=self._update_manager_settings
        )
        rounds_spinbox.grid(row=0, column=1, padx=5)

        # Row 2: Team Size (for simul mode)
        ttk.Label(settings_grid, text="Team Size:").grid(row=1, column=0, padx=5)
        team_size_spinbox = ttk.Spinbox(
            settings_grid,
            from_=1,
            to=2,
            width=5,
            textvariable=self.team_size_var,
            command=self._update_manager_settings
        )
        team_size_spinbox.grid(row=1, column=1, padx=5)

        # Row 3: Checkboxes
        options_frame = ttk.Frame(settings_frame)
        options_frame.pack(fill="x", padx=5, pady=5)

        ttk.Checkbutton(
            options_frame,
            text="🔁 Continuous Mode",
            variable=self.continuous_var,
            command=self._update_manager_settings
        ).pack(side="left", padx=10)

        ttk.Checkbutton(
            options_frame,
            text="🎨 Random Colors",
            variable=self.random_color_var,
            command=self._update_manager_settings
        ).pack(side="left", padx=10)

        # Battle Log Frame
        log_frame = ttk.LabelFrame(battle_frame, text="Battle Log")
        log_frame.pack(expand=True, fill="both", padx=10, pady=5)

        # Create and configure the battle log text widget
        self.battle_log = tk.Text(log_frame, height=10, wrap=tk.WORD)
        self.battle_log.pack(side="left", expand=True, fill="both", padx=5, pady=5)
        
        # Add scrollbar for battle log
        log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.battle_log.yview)
        log_scrollbar.pack(side="right", fill="y")
        self.battle_log.configure(yscrollcommand=log_scrollbar.set)

        # Initialize settings
        self._update_manager_settings()

    def _update_manager_settings(self, *args):
        """Update the manager's settings based on GUI controls"""
        try:
            self.manager.settings.update({
                "battle_mode": self.mode_var.get(),
                "rounds": self.rounds_var.get(),
                "continuous_mode": self.continuous_var.get(),
                "random_color": self.random_color_var.get(),
                "team_size": self.team_size_var.get(),
                "team1_size": self.team1_size_var.get(),
                "team2_size": self.team2_size_var.get(),
                "random_team_sizes": self.random_team_sizes_var.get()
            })
            print("Updated manager settings:", self.manager.settings)  # Debug print
        except Exception as e:
            print(f"Error updating manager settings: {e}")
            traceback.print_exc()

    def _setup_characters_tab(self):
        # Create search and filter frame
        filter_frame = ttk.LabelFrame(self.tabs["Characters"], text="Search & Filter")
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
        list_frame = ttk.Frame(self.tabs["Characters"])
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
        selection_frame = ttk.Frame(self.tabs["Characters"])
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
        filter_frame = ttk.LabelFrame(self.tabs["Stages"], text="Search & Filter")
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
        list_frame = ttk.Frame(self.tabs["Stages"])
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
        selection_frame = ttk.Frame(self.tabs["Stages"])
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
        """Sort stage list by column"""
        try:
            # Get current sort column and order
            current_sort = getattr(self, '_stage_sort_column', None)
            current_order = getattr(self, '_stage_sort_order', 'asc')
            
            # Update sort order
            if current_sort == column:
                new_order = 'desc' if current_order == 'asc' else 'asc'
            else:
                new_order = 'asc'
            
            # Store new sort settings
            self._stage_sort_column = column
            self._stage_sort_order = new_order
            
            # Update column header
            for col in self.stage_tree['columns']:
                if col == column:
                    self.stage_tree.heading(col, text=f"{col} {'↓' if new_order == 'desc' else '↑'}")
                else:
                    self.stage_tree.heading(col, text=col)
            
            # Get all items
            item_list = [(self.stage_tree.set(item, column), item) for item in self.stage_tree.get_children('')]
            
            # Sort items
            item_list.sort(reverse=(new_order == 'desc'))
            
            # Rearrange items in sorted order
            for index, (_, item) in enumerate(item_list):
                self.stage_tree.move(item, '', index)

        except Exception as e:
            print(f"Error sorting stages: {e}")
            traceback.print_exc()

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
            # Get character stats and calculate win rate
            stats = self.manager.character_stats.get(char, {"wins": 0, "losses": 0})
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            total_matches = wins + losses
            win_rate = f"{(wins/total_matches)*100:.1f}%" if total_matches > 0 else "N/A"
            
            # Get character tier
            tier = self.manager.get_character_tier(char)
            if tier == "":  # Handle empty tier case
                tier = "Unranked"
                
            # Get enabled status
            enabled = "✓" if char in self.manager.settings["enabled_characters"] else "✗"
            
            # Insert character data
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
            # Get character stats and calculate win rate
            stats = self.manager.character_stats.get(char, {"wins": 0, "losses": 0})
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            total_matches = wins + losses
            win_rate = f"{(wins/total_matches)*100:.1f}%" if total_matches > 0 else "N/A"
            
            # Get character tier
            tier = self.manager.get_character_tier(char)
            if tier == "":  # Handle empty tier case
                tier = "Unranked"
            
            # Check if character should be shown based on filters
            if (search_text in char.lower() and
                (tier == "Unranked" and self.show_unranked_var.get() or
                 tier != "Unranked" and self.tier_filters.get(tier, tk.BooleanVar(value=True)).get())):
                
                enabled = "✓" if char in self.manager.settings["enabled_characters"] else "✗"
                
                self.char_tree.insert("", "end", values=(
                    char,
                    tier,
                    win_rate,
                    enabled
                ))

    def _sort_characters(self, column):
        """Sort character list by column"""
        try:
            # Get current sort column and order
            current_sort = getattr(self, '_char_sort_column', None)
            current_order = getattr(self, '_char_sort_order', 'asc')
            
            # Update sort order
            if current_sort == column:
                new_order = 'desc' if current_order == 'asc' else 'asc'
            else:
                new_order = 'asc'
            
            # Store new sort settings
            self._char_sort_column = column
            self._char_sort_order = new_order
            
            # Update column header
            for col in self.char_tree['columns']:
                if col == column:
                    self.char_tree.heading(col, text=f"{col} {'↓' if new_order == 'desc' else '↑'}")
                else:
                    self.char_tree.heading(col, text=col)
            
            # Get all items
            item_list = [(self.char_tree.set(item, column), item) for item in self.char_tree.get_children('')]
            
            # Sort items
            item_list.sort(reverse=(new_order == 'desc'))
            
            # Rearrange items in sorted order
            for index, (_, item) in enumerate(item_list):
                self.char_tree.move(item, '', index)

        except Exception as e:
            print(f"Error sorting characters: {e}")
            traceback.print_exc()

    def _toggle_character_status(self, event):
        """Toggle character enabled/disabled status"""
        try:
            # Get selected item
            selection = self.char_tree.selection()
            if not selection:
                return
                
            item = selection[0]
            values = list(self.char_tree.item(item)['values'])
            
            # Toggle status (last column)
            values[-1] = '✓' if values[-1] != '✓' else ''
            
            # Update tree
            self.char_tree.item(item, values=values)
            
            # Update settings
            char_name = values[0]  # Character name is first column
            enabled_chars = self.manager.settings.get("enabled_chars", set())
            
            if values[-1] == '✓':
                enabled_chars.add(char_name)
            else:
                enabled_chars.discard(char_name)
                
            self.manager.settings["enabled_chars"] = enabled_chars
            
            # Save settings
            self.save_config()
            
        except Exception as e:
            print(f"Error toggling character status: {e}")
            traceback.print_exc()

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
        """Create statistics view with multiple sections"""
        # Create main frame for stats tab
        stats_frame = ttk.Frame(self.tabs["Stats"])
        stats_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Create summary section at the top
        summary_frame = ttk.LabelFrame(stats_frame, text="Summary Statistics")
        summary_frame.pack(fill=tk.X, padx=5, pady=5)

        # Initialize summary labels dictionary
        self.summary_labels = {}

        # Create summary statistics grid
        summary_grid = ttk.Frame(summary_frame)
        summary_grid.pack(fill=tk.X, padx=5, pady=5)

        # Total Battles
        ttk.Label(summary_grid, text="Total Battles:").grid(row=0, column=0, padx=5, pady=2, sticky="e")
        self.summary_labels["total_battles"] = ttk.Label(summary_grid, text="0")
        self.summary_labels["total_battles"].grid(row=0, column=1, padx=5, pady=2, sticky="w")

        # Top Winner
        ttk.Label(summary_grid, text="Top Winner:").grid(row=0, column=2, padx=5, pady=2, sticky="e")
        self.summary_labels["top_winner"] = ttk.Label(summary_grid, text="N/A")
        self.summary_labels["top_winner"].grid(row=0, column=3, padx=5, pady=2, sticky="w")

        # Most Used Stage
        ttk.Label(summary_grid, text="Most Used Stage:").grid(row=1, column=0, padx=5, pady=2, sticky="e")
        self.summary_labels["top_stage"] = ttk.Label(summary_grid, text="N/A")
        self.summary_labels["top_stage"].grid(row=1, column=1, padx=5, pady=2, sticky="w")

        # Average Battle Duration
        ttk.Label(summary_grid, text="Avg. Duration:").grid(row=1, column=2, padx=5, pady=2, sticky="e")
        self.summary_labels["avg_duration"] = ttk.Label(summary_grid, text="N/A")
        self.summary_labels["avg_duration"].grid(row=1, column=3, padx=5, pady=2, sticky="w")

        # Configure grid columns to expand evenly
        for i in range(4):
            summary_grid.columnconfigure(i, weight=1)

        # Create notebook for different stat views
        stat_notebook = ttk.Notebook(stats_frame)
        stat_notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        # Overall Stats Tab
        overall_frame = ttk.Frame(stat_notebook)
        stat_notebook.add(overall_frame, text="Overall Stats")

        # Create Treeview for overall stats
        columns = ("Character", "Wins", "Losses", "Win Rate", "Tier")
        self.stats_tree = ttk.Treeview(overall_frame, columns=columns, show="headings")
        
        # Configure columns
        for col in columns:
            self.stats_tree.heading(col, text=col, command=lambda c=col: self._sort_stats(c))
            width = 150 if col == "Character" else 100
            self.stats_tree.column(col, width=width)

        # Add scrollbar
        stats_scroll = ttk.Scrollbar(overall_frame, orient=tk.VERTICAL, command=self.stats_tree.yview)
        self.stats_tree.configure(yscrollcommand=stats_scroll.set)

        # Pack treeview and scrollbar
        self.stats_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        stats_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Matchup Stats Tab
        matchup_frame = ttk.Frame(stat_notebook)
        stat_notebook.add(matchup_frame, text="Matchup Stats")

        # Character selection for matchup view
        char_select_frame = ttk.Frame(matchup_frame)
        char_select_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(char_select_frame, text="Select Character:").pack(side=tk.LEFT)
        self.matchup_char_var = tk.StringVar()
        self.matchup_char_combo = ttk.Combobox(char_select_frame, textvariable=self.matchup_char_var)
        self.matchup_char_combo['values'] = sorted(self.manager.characters)
        self.matchup_char_combo.pack(side=tk.LEFT, padx=5)
        self.matchup_char_combo.bind('<<ComboboxSelected>>', self._update_matchup_view)

        # Create Treeview for matchup stats
        columns = ("Opponent", "Wins", "Losses", "Win Rate", "Total Matches")
        self.matchup_tree = ttk.Treeview(matchup_frame, columns=columns, show="headings")
        
        # Configure columns
        for col in columns:
            self.matchup_tree.heading(col, text=col)
            width = 150 if col == "Opponent" else 100
            self.matchup_tree.column(col, width=width)

        # Add scrollbar
        matchup_scroll = ttk.Scrollbar(matchup_frame, orient=tk.VERTICAL, command=self.matchup_tree.yview)
        self.matchup_tree.configure(yscrollcommand=matchup_scroll.set)

        # Pack treeview and scrollbar
        self.matchup_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        matchup_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Most Common Matchups Tab
        common_frame = ttk.Frame(stat_notebook)
        stat_notebook.add(common_frame, text="Common Matchups")

        # Create Treeview for most common matchups
        columns = ("Character", "Most Defeated", "Most Lost To")
        self.common_tree = ttk.Treeview(common_frame, columns=columns, show="headings")
        
        # Configure columns
        for col in columns:
            self.common_tree.heading(col, text=col)
            self.common_tree.column(col, width=150)

        # Add scrollbar
        common_scroll = ttk.Scrollbar(common_frame, orient=tk.VERTICAL, command=self.common_tree.yview)
        self.common_tree.configure(yscrollcommand=common_scroll.set)

        # Pack treeview and scrollbar
        self.common_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        common_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Stage Stats Tab
        stage_frame = ttk.Frame(stat_notebook)
        stat_notebook.add(stage_frame, text="Stage Stats")

        # Create Treeview for stage stats
        columns = ("Stage", "Times Used", "Last Used")
        self.stage_tree = ttk.Treeview(stage_frame, columns=columns, show="headings")
        
        # Configure columns
        for col in columns:
            self.stage_tree.heading(col, text=col)
            width = 200 if col == "Stage" else 100
            self.stage_tree.column(col, width=width)

        # Add scrollbar
        stage_scroll = ttk.Scrollbar(stage_frame, orient=tk.VERTICAL, command=self.stage_tree.yview)
        self.stage_tree.configure(yscrollcommand=stage_scroll.set)

        # Pack treeview and scrollbar
        self.stage_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        stage_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Add refresh button at the bottom
        refresh_btn = ttk.Button(stats_frame, text="Refresh Stats", command=self._update_stats_view)
        refresh_btn.pack(pady=5)

        # Initial update of all views
        self._update_stats_view()

    def _update_matchup_view(self, event=None):
        """Update the matchup statistics view"""
        selected_char = self.matchup_char_var.get()
        if not selected_char:
            return

        # Clear existing items
        for item in self.matchup_tree.get_children():
            self.matchup_tree.delete(item)

        # Get matchup data
        matchups = self.manager.get_character_matchups(selected_char)
        
        # Insert matchup data
        for opponent, data in matchups.items():
            self.matchup_tree.insert("", tk.END, values=(
                opponent,
                data["wins"],
                data["losses"],
                data["win_rate"],
                data["total_matches"]
            ))

    def _update_stats_view(self):
        """Update all statistics views"""
        # Update overall stats tree
        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)
        
        # Calculate total battles
        total_battles = sum(
            stats.get("wins", 0) + stats.get("losses", 0)
            for stats in self.manager.character_stats.values()
            if isinstance(stats, dict)
        ) // 2
        self.summary_labels["total_battles"].config(text=str(total_battles))
        
        # Find top winner
        top_winner = "N/A"
        max_wins = 0
        for char, stats in self.manager.character_stats.items():
            if isinstance(stats, dict):
                wins = stats.get("wins", 0)
                if wins > max_wins:
                    max_wins = wins
                    top_winner = char
        self.summary_labels["top_winner"].config(text=top_winner)
        
        # Find most used stage
        if self.manager.stage_stats:
            try:
                top_stage = max(
                    self.manager.stage_stats.items(),
                    key=lambda x: x[1].get("times_used", 0)
                )[0]
                self.summary_labels["top_stage"].config(text=top_stage)
            except Exception as e:
                print(f"Error finding top stage: {e}")
                self.summary_labels["top_stage"].config(text="N/A")
        
        # Update overall stats
        for char in sorted(self.manager.characters):
            stats = self.manager.character_stats.get(char, {})
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            total_matches = wins + losses
            win_rate = f"{(wins/total_matches)*100:.1f}%" if total_matches > 0 else "N/A"
            tier = self.manager.get_character_tier(char)
            
            self.stats_tree.insert("", "end", values=(
                char,
                wins,
                losses,
                win_rate,
                tier
            ))
        
        # Update common matchups tree
        for item in self.common_tree.get_children():
            self.common_tree.delete(item)
            
        for char in sorted(self.manager.characters):
            most_defeated = self.manager.get_most_defeated_opponent(char)
            most_lost_to = self.manager.get_most_lost_to_opponent(char)
            
            self.common_tree.insert("", "end", values=(
                char,
                most_defeated,
                most_lost_to
            ))
        
        # Update stage stats tree in Stats tab
        for item in self.stage_tree.get_children():
            self.stage_tree.delete(item)
            
        for stage in sorted(self.manager.stages):
            stats = self.manager.stage_stats.get(stage, {})
            times_used = stats.get("times_used", 0)
            last_used = stats.get("last_used", "Never")
            
            self.stage_tree.insert("", "end", values=(
                stage,
                times_used,
                last_used
            ))
            
        # Update average duration
        avg_duration = self.manager.get_average_battle_duration()
        self.summary_labels["avg_duration"].config(text=avg_duration)
        
        # Update Stages tab list
        self._populate_stage_list()

    def _setup_settings_tab(self):
        # General Settings
        general_frame = ttk.LabelFrame(self.tabs["Settings"], text="General Settings")
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
        display_frame = ttk.LabelFrame(self.tabs["Settings"], text="Display Settings")
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
        advanced_frame = ttk.LabelFrame(self.tabs["Settings"], text="Advanced Settings")
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
        twitch_frame = ttk.LabelFrame(self.tabs["Settings"], text="Twitch Integration")
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
        """Browse for MUGEN executable"""
        try:
            path = filedialog.askopenfilename(
                filetypes=[("MUGEN executable", "mugen*.exe"), ("All files", "*.*")],
                title="Select MUGEN Executable"
            )
            if path:
                self.manager.mugen_path = Path(path)
                self.mugen_path_var.set(path)
        except Exception as e:
            messagebox.showerror("Error", "Failed to set MUGEN path: %s" % str(e))

    def save_config(self):
        """Save configuration including tab order"""
        try:
            config = {
                "tab_order": self.settings.get("tab_order", []),
                "autosave": self.settings.get("autosave", True),
                "rounds": self.rounds_var.get(),
                "mode": self.mode_var.get(),
                "time": getattr(self, 'time_var', tk.StringVar(value="99")).get(),
                "continuous_mode": self.continuous_var.get(),
                "random_color": self.random_color_var.get(),
                "team_size": self.team_size_var.get(),
                "team1_size": self.team1_size_var.get(),
                "team2_size": self.team2_size_var.get(),
                "random_team_sizes": self.random_team_sizes_var.get()
            }
            
            # Save to file
            config_path = Path("mugen_battle_config.json")
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
        except Exception as e:
            print(f"Error saving config: {e}")
            traceback.print_exc()

    def load_config(self):
        """Load configuration from file"""
        try:
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
                    self.rounds_var.set(battle_settings.get("rounds", "1"))
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
                    
                    # Refresh UI
                    self._populate_character_list()
                    self._populate_stage_list()
                    self._update_stats_view()
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to load configuration: {str(e)}")
        except Exception as e:
            messagebox.showerror("Error", f"Error selecting file: {str(e)}")

    def export_stats(self):
        """Export battle statistics to a CSV file"""
        try:
            # Get file path from user
            path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                title="Export Statistics"
            )
            
            if path:
                with open(path, 'w', newline='') as f:
                    import csv
                    writer = csv.writer(f)
                    
                    # Write character stats
                    writer.writerow(["Character Statistics"])
                    writer.writerow(["Character", "Wins", "Losses", "Win Rate", "Tier"])
                    
                    for char in sorted(self.manager.characters):
                        stats = self.manager.character_stats.get(char, {})
                        wins = stats.get("wins", 0)
                        losses = stats.get("losses", 0)
                        total_matches = wins + losses
                        win_rate = f"{(wins/total_matches)*100:.1f}%" if total_matches > 0 else "N/A"
                        tier = self.manager.get_character_tier(char)
                        
                        writer.writerow([char, wins, losses, win_rate, tier])
                    
                    # Add a blank line between sections
                    writer.writerow([])
                    
                    # Write stage stats
                    writer.writerow(["Stage Statistics"])
                    writer.writerow(["Stage", "Times Used", "Last Used", "Average Duration"])
                    
                    for stage in sorted(self.manager.stages):
                        stats = self.manager.stage_stats.get(stage, {})
                        times_used = stats.get("times_used", 0)
                        last_used = stats.get("last_used", "Never")
                        avg_duration = stats.get("total_duration", 0) / times_used if times_used > 0 else 0
                        
                        writer.writerow([stage, times_used, last_used, f"{avg_duration:.1f}s"])
                    
                    messagebox.showinfo("Success", "Statistics exported successfully!")
                    
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export statistics: {str(e)}")
            print(f"Error exporting stats: {e}")
            traceback.print_exc()

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
        return self.theme

    def apply_theme(self, theme_name):
        """Apply the selected theme"""
        if not hasattr(self, 'theme'):
            self.load_theme()
            
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
        
        # Configure preview styles
        style.configure("Preview.TFrame", background="black")
        style.configure("Name.TLabel", 
                       font=("Arial Black", 16, "bold"), 
                       background="black", 
                       foreground="white")
        style.configure("Stats.TLabel", 
                       font=("Arial", 12), 
                       background="black", 
                       foreground="white")
        style.configure("Dark.TFrame", background="black")
        
        self.current_theme = theme_name

    def create_menu(self):
        """Create the application menu bar"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File Menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Load Config", command=self.load_config)
        file_menu.add_command(label="Save Config", command=self.save_config)
        file_menu.add_separator()
        file_menu.add_command(label="Export Stats", command=self.export_stats)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        # Battle Menu
        battle_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Battle", menu=battle_menu)
        battle_menu.add_command(label="Start Battle (F5)", command=self._start_battle)
        battle_menu.add_command(label="Stop Battle (F6)", command=self.stop_battle)
        battle_menu.add_command(label="Quick Rematch (F7)", command=self._quick_rematch)
        battle_menu.add_separator()
        battle_menu.add_command(label="Force Next Round (F8)", command=self._force_next_round)
        battle_menu.add_command(label="Reset Scores (F9)", command=self._reset_battle_scores)
        battle_menu.add_command(label="Change Stage (F10)", command=self._change_random_stage)
        
        # View Menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Refresh Stats", command=self._update_stats_view)
        view_menu.add_separator()
        
        # Theme submenu
        theme_menu = tk.Menu(view_menu, tearoff=0)
        view_menu.add_cascade(label="Theme", menu=theme_menu)
        theme_menu.add_command(label="Light", command=lambda: self.apply_theme("light"))
        theme_menu.add_command(label="Dark", command=lambda: self.apply_theme("dark"))
        
        # Help Menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Keyboard Shortcuts", command=self._show_shortcuts)
        help_menu.add_command(label="About", command=self._show_about)

    def _show_shortcuts(self):
        """Display keyboard shortcuts help dialog"""
        shortcuts = """
        Keyboard Shortcuts:
        F5 - Start Battle
        F6 - Stop Battle
        F7 - Quick Rematch
        F8 - Force Next Round
        F9 - Reset Scores
        F10 - Change Stage
        """
        messagebox.showinfo("Keyboard Shortcuts", shortcuts)

    def _show_about(self):
        """Display about dialog"""
        about_text = """
        MUGEN Battle Manager
        
        A comprehensive battle management system for MUGEN
        featuring AI battles, statistics tracking, and
        Twitch integration.
        
        Created with ♥ for the MUGEN community
        """
        messagebox.showinfo("About", about_text)

    def start_auto_save_timer(self):
        """Start the auto-save timer if enabled"""
        if hasattr(self, 'auto_save_timer') and self.auto_save_timer:
            self.root.after_cancel(self.auto_save_timer)
            
        if hasattr(self, 'autosave_var') and self.autosave_var.get():
            # Auto-save every 5 minutes (300000 ms)
            self.auto_save_timer = self.root.after(300000, self._auto_save_callback)

    def _auto_save_callback(self):
        """Callback for auto-saving data"""
        try:
            # Save stats
            self.manager.save_stats()
            
            # Save battle history
            self.manager.save_battle_history()
            
            # Save config
            self.save_config()
            
            print("Auto-saved data successfully")
            
            # Schedule next auto-save
            self.start_auto_save_timer()
        except Exception as e:
            print(f"Error during auto-save: {e}")
            traceback.print_exc()
            
            # Try again in 1 minute if there was an error
            self.auto_save_timer = self.root.after(60000, self._auto_save_callback)

    def _on_mode_change(self, *args):
        """Handle battle mode changes"""
        mode = self.mode_var.get()
        
        # Update team size controls visibility based on mode
        if mode == "simul":
            self.team_size_var.set(2)  # Default for simul battles
            self.random_team_sizes_var.set(True)
        else:
            self.team_size_var.set(1)  # Single character for other modes
            self.random_team_sizes_var.set(False)
        
        # Update manager settings
        self._update_manager_settings()

    def load_config_data(self, config: Dict):
        """Load configuration data from a dictionary"""
        try:
            # Load theme
            if "theme" in config:
                self.apply_theme(config["theme"])
                self.theme_combo.set(config["theme"].capitalize())
            
            # Load battle settings
            if "battle_settings" in config:
                battle_settings = config["battle_settings"]
                self.mode_var.set(battle_settings.get("mode", "single"))
                self.rounds_var.set(battle_settings.get("rounds", "1"))
                self.continuous_var.set(battle_settings.get("continuous", False))
                self.random_color_var.set(battle_settings.get("random_color", True))
                self.team_size_var.set(battle_settings.get("team_size", 2))
                
            # Load Twitch settings
            if "twitch_settings" in config:
                twitch_settings = config["twitch_settings"]
                self.twitch_token_var.set(twitch_settings.get("token", ""))
                self.twitch_channel_var.set(twitch_settings.get("channel", ""))
                self.twitch_botname_var.set(twitch_settings.get("bot_name", "MugenBattleBot"))
                self.betting_duration_var.set(twitch_settings.get("betting_duration", 20))
                self.points_reward_var.set(twitch_settings.get("points_reward", 100))
            
            # Load auto-save settings
            if "auto_save" in config:
                self.autosave_var.set(config["auto_save"])
            
            # Load backup settings
            if "backup_frequency" in config:
                self.backup_freq.set(config["backup_frequency"])
            
            # Load font settings
            if "font_size" in config:
                self.font_size.set(config["font_size"])
            
            # Update UI based on loaded settings
            self._on_mode_change()
            self._update_manager_settings()
            
        except Exception as e:
            print(f"Error loading config data: {e}")
            traceback.print_exc()

    def _stop_battle(self):
        """Stop the current battle"""
        try:
            self.manager._cleanup_battle()
            self.battle_monitor = None
            self.tournament_monitor = None
            
            # Update UI elements
            if hasattr(self, 'battle_log'):
                timestamp = time.strftime("%H:%M:%S")
                log_text = f"[{timestamp}] Battle stopped\n"
                self.battle_log.insert(tk.END, log_text)
                self.battle_log.see(tk.END)
                
        except Exception as e:
            print(f"Error stopping battle: {e}")
            traceback.print_exc()

    def _start_battle(self):
        """Start a new battle with current settings"""
        try:
            # Check if battle is already running
            if self.battle_monitor:
                messagebox.showwarning("Warning", "A battle is already in progress!")
                return

            # Check if there are enough enabled characters
            enabled_chars = [char for char in self.character_tree.get_children() 
                           if self.character_tree.item(char)['values'][0] == '✓']
            if len(enabled_chars) < 2:
                messagebox.showerror("Error", "Not enough characters enabled! Enable at least 2 characters.")
                return

            # Check if there are enabled stages
            enabled_stages = [stage for stage in self.stage_tree.get_children() 
                            if self.stage_tree.item(stage)['values'][0] == '✓']
            if not enabled_stages:
                messagebox.showerror("Error", "No stages enabled! Enable at least one stage.")
                return

            # Prepare battle information
            self.prepared_battle_info = self.manager.prepare_battle()
            
            # Update preview tab with battle info
            self._update_preview_tab(self.prepared_battle_info)

            # If Twitch bot is connected and active, start betting period
            if hasattr(self, 'twitch_bot') and self.twitch_bot and self.twitch_bot.connected:
                # Format battle title based on mode
                if self.prepared_battle_info['mode'] == "single":
                    title = f"{self.prepared_battle_info['p1']} vs {self.prepared_battle_info['p2']}"
                    team1 = self.prepared_battle_info['p1']
                    team2 = self.prepared_battle_info['p2']
                else:
                    team1 = " & ".join(self.prepared_battle_info['p1'])
                    team2 = " & ".join(self.prepared_battle_info['p2'])
                    title = f"Team Battle: {team1} vs {team2}"

                # Start betting period
                betting_duration = self.betting_duration_var.get()
                asyncio.run_coroutine_threadsafe(
                    self.twitch_bot.create_battle_poll(title, team1, team2, betting_duration),
                    self.twitch_bot.loop
                )
                
                # Start betting timer
                self._update_betting_timer(betting_duration)
            else:
                # Start battle immediately if no Twitch integration
                self._start_actual_battle()

        except Exception as e:
            messagebox.showerror("Error", f"Failed to start battle: {str(e)}")
            print(f"Error starting battle: {e}")
            traceback.print_exc()

    def _start_actual_battle(self):
        """Start the actual battle with prepared info"""
        try:
            # Check if battle is already running
            if self.battle_monitor:
                print("Battle already in progress, skipping start")
                return

            if not hasattr(self, 'prepared_battle_info') or not self.prepared_battle_info:
                print("No battle info available")
                return

            # Store battle info and clear prepared info to prevent duplicate starts
            battle_info = self.prepared_battle_info
            self.prepared_battle_info = None

            print("Starting actual battle with:", battle_info)
            self.manager.start_battle(battle_info)
            self.battle_monitor = self.root.after(1000, self._check_battle_result)
            
        except Exception as e:
            print(f"Error starting actual battle: {e}")
            traceback.print_exc()
            if self.battle_monitor:
                self.root.after_cancel(self.battle_monitor)
                self.battle_monitor = None

    def run(self):
        """Start the main application loop"""
        try:
            # Load initial configuration
            self.load_config()
            
            # Load theme
            self.load_theme()
            
            # Start auto-save timer
            self.start_auto_save_timer()
            
            # Update initial stats view
            self._update_stats_view()
            
            # Populate character and stage lists
            self._populate_character_list()
            self._populate_stage_list()
            
            # Update any dependent UI elements
            self._update_manager_settings()
            
            # Start the main event loop
            self.root.mainloop()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start application: {str(e)}")
            print(f"Error starting application: {e}")
            traceback.print_exc()
            sys.exit(1)
        finally:
            # Cleanup on exit
            try:
                self.save_config()
                self.manager.save_stats()
                self.manager.save_battle_history()
                if hasattr(self, 'twitch_bot') and self.twitch_bot:
                    if hasattr(self.twitch_bot, 'loop') and self.twitch_bot.loop:
                        self.twitch_bot.loop.stop()
                    self.twitch_bot = None
            except Exception as e:
                print(f"Error during cleanup: {e}")
                traceback.print_exc()

    def _setup_preview_tab(self):
        """Setup the battle preview tab"""
        preview_frame = self.tabs["Preview"]
        preview_frame.configure(style="Preview.TFrame")
        
        # Create main preview container that will hold the battle preview
        self.preview_display = ttk.Frame(preview_frame, style="Preview.TFrame")
        self.preview_display.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
        
        # Create initial empty frames for teams
        team1_frame = ttk.Frame(self.preview_display, style="Preview.TFrame")
        team1_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        
        vs_frame = ttk.Frame(self.preview_display, style="Preview.TFrame")
        vs_frame.pack(side=tk.LEFT, padx=20)
        ttk.Label(vs_frame, text="VS", style="Name.TLabel").pack(pady=5)
        
        team2_frame = ttk.Frame(self.preview_display, style="Preview.TFrame")
        team2_frame.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH)
        
        # Stage Preview (Bottom)
        stage_frame = ttk.Frame(preview_frame, style="Preview.TFrame")
        stage_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=10)
        
        ttk.Label(stage_frame, text="STAGE", style="Name.TLabel").pack(pady=5)
        self.stage_preview = ttk.Label(stage_frame, image=self.placeholder_stage)
        self.stage_preview.pack()
        
        # Add betting information display if enabled
        if hasattr(self, 'betting_enabled') and self.betting_enabled:
            betting_frame = ttk.Frame(preview_frame, style="Preview.TFrame")
            betting_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=5)
            
            self.team1_bets = ttk.Label(betting_frame, text="Team 1: 0 points", style="Stats.TLabel")
            self.team1_bets.pack(side=tk.LEFT, padx=10)
            
            self.team2_bets = ttk.Label(betting_frame, text="Team 2: 0 points", style="Stats.TLabel")
            self.team2_bets.pack(side=tk.RIGHT, padx=10)
            
            self.betting_timer = ttk.Label(betting_frame, text="", style="Stats.TLabel")
            self.betting_timer.pack()
        
        # Add timer display
        timer_frame = ttk.Frame(preview_frame, style="Preview.TFrame")
        timer_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.preview_timer = ttk.Label(timer_frame, text="", style="Name.TLabel", anchor="center")
        self.preview_timer.pack(fill=tk.X)

    def _create_fighter_display(self, parent, fighter, team_name, side):
        """Create a single fighter display"""
        frame = ttk.Frame(parent, style="Dark.TFrame")
        frame.pack(expand=True, fill="both")

        # Team name with colored background
        team_bg = "#ff3366" if side == "left" else "#3366ff"
        team_frame = tk.Frame(frame, bg=team_bg)
        team_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(team_frame, text=team_name, style="Name.TLabel").pack(pady=5)

        # Portrait with placeholder
        portrait_path = self.manager.chars_path / fighter / "portrait.png"
        try:
            if portrait_path.exists():
                img = Image.open(portrait_path)
                img.thumbnail((300, 300))  # Larger portraits
                photo = ImageTk.PhotoImage(img)
            else:
                photo = self.placeholder_portrait
        except Exception as e:
            print(f"Error loading portrait for {fighter}: {e}")
            photo = self.placeholder_portrait

        portrait_label = ttk.Label(frame, image=photo)
        portrait_label.image = photo
        portrait_label.pack(pady=10)

        # Fighter name
        ttk.Label(frame, text=fighter, style="Name.TLabel").pack()

        # Stats
        stats = self.manager.character_stats.get(fighter, {"wins": 0, "losses": 0})
        total_matches = stats["wins"] + stats["losses"]
        win_rate = f"{(stats['wins']/total_matches)*100:.1f}%" if total_matches > 0 else "No matches"
        
        stats_text = f"Wins: {stats['wins']} | Losses: {stats['losses']}\nWin Rate: {win_rate}"
        ttk.Label(frame, text=stats_text, style="Stats.TLabel").pack(pady=5)

    def _create_team_display(self, parent, team, team_name, side):
        """Create a team display"""
        frame = ttk.Frame(parent, style="Dark.TFrame")
        frame.pack(expand=True, fill="both")

        # Team name with colored background
        team_bg = "#ff3366" if side == "left" else "#3366ff"
        team_frame = tk.Frame(frame, bg=team_bg)
        team_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(team_frame, text=team_name, style="Name.TLabel").pack(pady=5)

        # Calculate team stats
        total_wins = sum(self.manager.character_stats.get(fighter, {"wins": 0})["wins"] for fighter in team)
        total_matches = sum(
            self.manager.character_stats.get(fighter, {"wins": 0, "losses": 0})["wins"] + 
            self.manager.character_stats.get(fighter, {"wins": 0, "losses": 0})["losses"] 
            for fighter in team
        )
        win_rate = f"{(total_wins/total_matches)*100:.1f}%" if total_matches > 0 else "No matches"
        ttk.Label(frame, text=f"Team Win Rate: {win_rate}", style="Stats.TLabel").pack()

        # Create a grid of fighter portraits
        grid_frame = ttk.Frame(frame, style="Dark.TFrame")
        grid_frame.pack(pady=10)
        
        row = 0
        col = 0
        for fighter in team:
            fighter_frame = ttk.Frame(grid_frame, style="Dark.TFrame")
            fighter_frame.grid(row=row, column=col, padx=5, pady=5)
            
            # Portrait
            portrait_path = self.manager.chars_path / fighter / "portrait.png"
            try:
                if portrait_path.exists():
                    img = Image.open(portrait_path)
                    img.thumbnail((150, 150))  # Smaller for team display
                    photo = ImageTk.PhotoImage(img)
                else:
                    photo = self.placeholder_portrait
            except Exception as e:
                print(f"Error loading portrait for {fighter}: {e}")
                photo = self.placeholder_portrait

            portrait_label = ttk.Label(fighter_frame, image=photo)
            portrait_label.image = photo
            portrait_label.pack()

            # Fighter name
            ttk.Label(fighter_frame, text=fighter, style="Stats.TLabel").pack()
            
            # Update grid position
            col += 1
            if col > 1:  # 2 columns
                col = 0
                row += 1

    def _update_betting_timer(self, remaining):
        """Update betting timer and start battle when done"""
        try:
            if remaining > 0:
                # Update timer text
                timer_text = f"Betting closes in: {remaining} seconds"
                
                # Update timer in preview tab
                if hasattr(self, 'preview_timer'):
                    self.preview_timer.config(text=timer_text)
                
                # Schedule next update
                self.root.after(1000, lambda: self._update_betting_timer(remaining - 1))
            else:
                # End betting poll
                if self.twitch_bot and self.twitch_bot.betting_active:
                    asyncio.run_coroutine_threadsafe(
                        self.twitch_bot.end_poll(),
                        self.twitch_bot.loop
                    )
                
                # Update preview tab status
                if hasattr(self, 'preview_timer'):
                    self.preview_timer.config(text="BATTLE STARTING!")
                
                # Start the actual battle
                self._start_actual_battle()
                
        except Exception as e:
            print(f"Timer update error: {e}")
            traceback.print_exc()

    def _setup_draggable_tabs(self):
        """Setup drag and drop functionality for tabs"""
        self.notebook.bind("<Button-1>", self._start_tab_drag)
        self.notebook.bind("<B1-Motion>", self._drag_tab)
        self.notebook.bind("<ButtonRelease-1>", self._release_tab)

    def _start_tab_drag(self, event):
        """Start tab dragging"""
        try:
            # Initialize drag data if not exists
            if not hasattr(self, '_drag_data'):
                self._drag_data = {}
            
            # Get tab number
            try:
                index = self.notebook.index("@%d,%d" % (event.x, event.y))
                self._drag_data.update({
                    "tab": index,
                    "dragging": True
                })
            except tk.TclError:
                pass
                
        except Exception as e:
            print("Error starting tab drag: %s" % str(e))
            traceback.print_exc()

    def _drag_tab(self, event):
        """Handle tab dragging"""
        try:
            if not hasattr(self, '_drag_data') or not self._drag_data.get("dragging"):
                return
                
            # Get current tab
            x, y = event.x_root, event.y_root
            src_tab = self._drag_data.get("tab")
            
            # Find target position
            for i, tab_id in enumerate(self.notebook.tabs()):
                bbox = self.notebook.bbox(tab_id)
                if bbox:
                    tab_x = bbox[0] + self.notebook.winfo_rootx()
                    if x < tab_x + bbox[2]//2:
                        if src_tab != i:
                            self._move_tab(src_tab, i)
                            self._drag_data["tab"] = i
                        break
                        
        except Exception as e:
            print("Error dragging tab: %s" % str(e))
            traceback.print_exc()
            
    def _release_tab(self, event):
        """Handle tab release after drag"""
        try:
            if hasattr(self, '_drag_data') and self._drag_data.get("dragging"):
                self._drag_data["dragging"] = False
                self._update_tab_references()
        except Exception as e:
            print("Error releasing tab: %s" % str(e))
            traceback.print_exc()

    def _move_tab(self, src, dst):
        """Move a tab from source to destination index"""
        try:
            tab = self.notebook.tabs()[src]
            self.notebook.insert(dst, tab)
        except Exception as e:
            print(f"Error moving tab: {e}")
            traceback.print_exc()
            
    def _update_tab_references(self):
        """Update tab references after drag and drop"""
        try:
            # Get the current tab order
            tab_order = []
            for tab_id in self.notebook.tabs():
                tab_text = self.notebook.tab(tab_id, "text")
                tab_order.append(tab_text)
            
            # Update settings
            self.settings["tab_order"] = tab_order
            
            # Save if auto-save enabled
            if self.settings.get("autosave", True):
                self.save_config()
                
        except Exception as e:
            print("Error updating tab references: %s" % str(e))
            traceback.print_exc()
            
    def _save_tab_order(self, auto_save=True):
        """Save current tab order to settings"""
        try:
            if not hasattr(self, 'settings'):
                self.settings = {}
                
            tab_order = []
            for tab in self.notebook.tabs():
                try:
                    tab_text = self.notebook.tab(tab, "text")
                    if tab_text:  # Only add valid tab texts
                        tab_order.append(tab_text)
                except tk.TclError:
                    continue
            
            self.settings["tab_order"] = tab_order
            
            # Auto-save if enabled and requested
            if auto_save and hasattr(self, 'autosave_var') and self.autosave_var.get():
                self.save_config()
                
        except Exception as e:
            print(f"Error saving tab order: {e}")
            traceback.print_exc()

    def _load_tab_order(self):
        """Load and apply saved tab order"""
        try:
            if "tab_order" in self.settings:
                saved_order = self.settings["tab_order"]
                current_tabs = {}
                
                # Create mapping of tab text to index
                for i in range(self.notebook.index('end')):
                    text = self.notebook.tab(i, "text")
                    current_tabs[text] = i
                
                # Reorder tabs according to saved order
                for i, tab_text in enumerate(saved_order):
                    if tab_text in current_tabs:
                        current_index = current_tabs[tab_text]
                        if current_index != i:
                            self._move_tab(current_index, i)
        except Exception as e:
            print(f"Error loading tab order: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    manager = MugenBattleManager()
    gui = BattleGUI(manager)
    gui.run() 