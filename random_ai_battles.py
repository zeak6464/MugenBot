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
        # Initialize stats dictionary
        self.stats = {
            'characters': {},
            'stages': {},
            'battles': []
        }
        
        # Local betting system
        self.local_betting_enabled = True
        self.local_betting_active = False
        self.local_bets = {"1": {}, "2": {}}
        self.local_user_points = {"Player": 1000}  # Default user with 1000 points
        self.pending_bet_results = False  # Flag to track if there are pending bets to be processed
        
        # Initialize current battle
        self.current_battle = None
        
        # Load existing stats if available
        stats_path = Path('stats.json')
        if stats_path.exists():
            try:
                self.stats = self.load_stats(stats_path)
            except Exception as e:
                print(f"Error loading stats: {e}")
                traceback.print_exc()
        
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
            
            # Clean up watcher with proper termination
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

            if result:
                # Process the result before clearing current_battle
                processed_result = self._process_battle_result(result)
                return processed_result
            
            # Only clear current battle if no result was found
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
        if not hasattr(self, 'current_battle') or self.current_battle is None or getattr(self, '_battle_processed', False):
            print("Warning: No current battle to process or battle already processed")
            return None
            
        p1_score, p2_score = result
        print(f"Processing battle result: P1={p1_score}, P2={p2_score}")  # Debug print
        
        # Set processed flag
        self._battle_processed = True
        
        # Save stage information
        stage = None
        if "stage" in self.current_battle:
            stage = self.current_battle["stage"]
        
        if self.current_battle["mode"] == "single":
            if p1_score > p2_score:
                winner = self.current_battle["p1"]
                loser = self.current_battle["p2"]
            else:
                winner = self.current_battle["p2"]
                loser = self.current_battle["p1"]
            
            # Update statistics
            self.update_stats(winner, loser)
            
            # Update stage stats if stage is available
            if stage:
                self.update_stage_stats(stage)

            battle_result = {
                "winner": winner,
                "loser": loser,
                "p1_score": p1_score,
                "p2_score": p2_score,
                "mode": self.current_battle["mode"],
                "stage": stage
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
            
            # Update stage stats if stage is available
            if stage:
                self.update_stage_stats(stage)

            battle_result = {
                "winner": winner,
                "loser": loser,
                "p1_score": p1_score,
                "p2_score": p2_score,
                "mode": self.current_battle["mode"],
                "stage": stage
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
                
                # Check for the specific format from MugenWatcher
                if "RESULT:" in last_line:
                    try:
                        # Parse the format: "[date time] RESULT: 1 - 0"
                        result_part = last_line.split("RESULT:")[-1].strip()
                        scores = result_part.split("-")
                        p1_score = int(scores[0].strip())
                        p2_score = int(scores[1].strip())
                        print(f"Parsed battle result: P1={p1_score}, P2={p2_score}")
                        return [p1_score, p2_score]
                    except (ValueError, IndexError) as e:
                        print(f"Error parsing battle result: {e}")
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
        """Save battle history to file"""
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
        }
        
        # Add stage if available in battle_result
        if "stage" in battle_result:
            battle_record["stage"] = battle_result["stage"]
        # Otherwise try to get it from current_battle
        elif hasattr(self, 'current_battle') and self.current_battle is not None:
            if 'stage' in self.current_battle:
                battle_record["stage"] = self.current_battle["stage"]
        else:
            battle_record["stage"] = "Unknown"  # Default value if stage is not available
            
        self.battle_history["battles"].append(battle_record)
        self.battle_history["last_save"] = timestamp
        self.save_battle_history()
    
    def start_local_betting(self):
        """Start local betting period"""
        self.local_betting_active = True
        self.local_bets = {"1": {}, "2": {}}
        return True
    
    def place_local_bet(self, username: str, team: str, amount: int) -> bool:
        """Place a local bet
        
        Args:
            username: Name of the user placing the bet
            team: Team number (1 or 2)
            amount: Bet amount
            
        Returns:
            bool: True if bet was placed successfully
        """
        if not self.local_betting_active:
            return False
            
        if team not in ["1", "2"]:
            return False
            
        if amount <= 0:
            return False
            
        # Create user if doesn't exist
        if username not in self.local_user_points:
            self.local_user_points[username] = 1000
            
        # Check if user has enough points
        if amount > self.local_user_points[username]:
            return False
            
        # Remove any existing bet
        for team_bets in self.local_bets.values():
            if username in team_bets:
                old_bet = team_bets.pop(username)
                self.local_user_points[username] += old_bet
                
        # Place new bet
        self.local_bets[team][username] = amount
        self.local_user_points[username] -= amount
        return True
        
    def end_local_betting(self):
        """End local betting period"""
        self.local_betting_active = False
        # Set pending flag if there are any bets to process
        if self.local_bets["1"] or self.local_bets["2"]:
            self.pending_bet_results = True
            print("Betting closed with pending bets to process")
        
    def process_local_betting_results(self, winning_team: str):
        """Process local betting results
        
        Args:
            winning_team: The winning team (1 or 2)
            
        Returns:
            Dict: Betting results information
        """
        print(f"Processing betting results for winning team: {winning_team}")
        print(f"Current bets: {self.local_bets}")
        
        if winning_team not in ["1", "2"]:
            print(f"Invalid winning team: {winning_team}")
            return {"error": "Invalid winning team"}
            
        losing_team = "2" if winning_team == "1" else "1"
        
        # Reset pending flag after processing
        self.pending_bet_results = False
        
        # Calculate pools
        winning_pool = sum(self.local_bets[winning_team].values())
        losing_pool = sum(self.local_bets[losing_team].values())
        
        print(f"Winning pool: {winning_pool}, Losing pool: {losing_pool}")
        
        results = {
            "winning_team": winning_team,
            "winning_pool": winning_pool,
            "losing_pool": losing_pool,
            "winners": [],
            "total_winnings": 0
        }
        
        # If no bets on winning team, everyone loses
        if winning_pool == 0:
            print("No bets on winning team, everyone loses")
            # Reset betting state
            self.local_betting_active = False
            self.local_bets = {"1": {}, "2": {}}
            return results
            
        # Calculate payout ratio (minimum 1.1x)
        payout_ratio = max(1.1, (winning_pool + losing_pool) / winning_pool)
        results["payout_ratio"] = payout_ratio
        
        print(f"Payout ratio: {payout_ratio}")
        
        # Process winners
        for username, bet in self.local_bets[winning_team].items():
            winnings = int(bet * payout_ratio)
            self.local_user_points[username] += winnings
            profit = winnings - bet
            
            print(f"Winner: {username}, Bet: {bet}, Winnings: {winnings}, Profit: {profit}")
            
            results["winners"].append({
                "username": username,
                "bet": bet,
                "winnings": winnings,
                "profit": profit
            })
            results["total_winnings"] += winnings
            
        # Sort winners by winnings
        results["winners"].sort(key=lambda x: x["winnings"], reverse=True)
        
        # Reset betting state
        self.local_betting_active = False
        self.local_bets = {"1": {}, "2": {}}
        
        print(f"Final results: {results}")
        print(f"Updated user points: {self.local_user_points}")
        
        return results
    
    def get_local_betting_stats(self):
        """Get current local betting statistics
        
        Returns:
            Dict: Current betting statistics
        """
        team1_total = sum(self.local_bets["1"].values())
        team2_total = sum(self.local_bets["2"].values())
        total_pool = team1_total + team2_total
        
        # Calculate potential payouts
        team1_payout = 0 if team1_total == 0 else max(1.1, total_pool / team1_total)
        team2_payout = 0 if team2_total == 0 else max(1.1, total_pool / team2_total)
        
        return {
            "active": self.local_betting_active or self.pending_bet_results,
            "team1_total": team1_total,
            "team2_total": team2_total,
            "total_pool": total_pool,
            "team1_payout": team1_payout,
            "team2_payout": team2_payout
        }
        
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
        """Initialize the Battle GUI"""
        self.manager = manager
        
        # Initialize root window
        self.root = tk.Tk()
        self.root.title("Random AI Battles")
        self.root.geometry("800x600")
        
        # Initialize settings dictionary
        self.settings = {
            "tab_order": ["Preview", "Battle", "Tournament", "Characters", "Stages", "Stats", "Settings"],
            "theme": "default",
            "mugen_path": "",
            "battle_mode": "single",
            "ai_level": "4",
            "random_stage": True,
            "betting_enabled": False,
            "betting_duration": "30",
            "tournament_size": 8
        }
        
        # Initialize variables
        self.mode_var = tk.StringVar(value=self.settings["battle_mode"])
        self.ai_level_var = tk.StringVar(value=self.settings["ai_level"])
        self.random_stage_var = tk.BooleanVar(value=self.settings["random_stage"])
        self.betting_enabled_var = tk.BooleanVar(value=self.settings["betting_enabled"])
        self.betting_duration_var = tk.StringVar(value=self.settings["betting_duration"])
        self.tournament_size_var = tk.IntVar(value=self.settings["tournament_size"])
        
        # Initialize Twitch bot (set to None by default)
        self.twitch_bot = None
        self.twitch_connected_var = tk.BooleanVar(value=False)
        
        # Initialize continuous battles
        self.continuous_battles = False
        self.continuous_battles_var = tk.BooleanVar(value=self.continuous_battles)
        
        # Tournament state
        self.tournament = None
        self.tournament_running = False
        
        # Create placeholder images
        self._create_placeholder_images()
        
        # Load existing config if available
        try:
            self.load_config()
        except Exception as e:
            print(f"Error loading config: {e}")
            traceback.print_exc()
        
        # Setup GUI components
        self.setup_gui()
        
        # Start auto-save timer
        self.start_auto_save_timer()

    def setup_gui(self):
        """Setup the main GUI window"""
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True)
        
        # Initialize tabs dictionary
        self.tabs = {}
        
        # Create tabs
        self.tabs['Preview'] = ttk.Frame(self.notebook)
        self.tabs['Battle'] = ttk.Frame(self.notebook)
        self.tabs['Tournament'] = ttk.Frame(self.notebook)  # Add Tournament tab
        self.tabs['Characters'] = ttk.Frame(self.notebook)
        self.tabs['Stages'] = ttk.Frame(self.notebook)
        self.tabs['Stats'] = ttk.Frame(self.notebook)
        self.tabs['Settings'] = ttk.Frame(self.notebook)
        
        # Add tabs to notebook
        for name, frame in self.tabs.items():
            self.notebook.add(frame, text=name)
            
        # Setup individual tabs
        self._setup_preview_tab()
        self._setup_battle_tab()
        self._setup_tournament_tab()  # Add tournament tab setup
        self._setup_characters_tab()
        self._setup_stages_tab()
        self._setup_stats_tab()
        self._setup_settings_tab()
        
        # Setup draggable tabs
        self._setup_draggable_tabs()
        
        # Load saved tab order
        self._load_tab_order()
        
        # Create menu
        self.create_menu()

    def _setup_tournament_tab(self):
        """Setup the tournament tab with bracket display and controls"""
        tournament_frame = self.tabs['Tournament']
        
        # Create left control panel
        control_panel = ttk.Frame(tournament_frame)
        control_panel.pack(side='left', fill='y', padx=10, pady=5)
        
        # Tournament size selection
        size_frame = ttk.LabelFrame(control_panel, text="Tournament Size")
        size_frame.pack(fill='x', pady=5)
        
        sizes = [4, 8, 16, 32]
        for size in sizes:
            ttk.Radiobutton(
                size_frame,
                text=f"{size} Players",
                variable=self.tournament_size_var,
                value=size
            ).pack(anchor='w', padx=5, pady=2)
        
        # Tournament controls
        controls_frame = ttk.LabelFrame(control_panel, text="Controls")
        controls_frame.pack(fill='x', pady=5)
        
        self.start_tournament_btn = ttk.Button(
            controls_frame,
            text="Start Tournament",
            command=self._start_tournament
        )
        self.start_tournament_btn.pack(fill='x', padx=5, pady=2)
        
        self.stop_tournament_btn = ttk.Button(
            controls_frame,
            text="Stop Tournament",
            command=self._stop_tournament,
            state='disabled'
        )
        self.stop_tournament_btn.pack(fill='x', padx=5, pady=2)
        
        # Tournament status
        status_frame = ttk.LabelFrame(control_panel, text="Status")
        status_frame.pack(fill='x', pady=5)
        
        self.tournament_status = ttk.Label(
            status_frame,
            text="No tournament in progress",
            wraplength=200
        )
        self.tournament_status.pack(padx=5, pady=5)
        
        # Create right bracket display
        bracket_frame = ttk.LabelFrame(tournament_frame, text="Tournament Bracket")
        bracket_frame.pack(side='right', fill='both', expand=True, padx=10, pady=5)
        
        # Bracket display
        self.bracket_display = ScrolledText(
            bracket_frame,
            wrap=tk.WORD,
            width=50,
            height=20
        )
        self.bracket_display.pack(fill='both', expand=True, padx=5, pady=5)
        
        # Make bracket display read-only
        self.bracket_display.configure(state='disabled')

    def _start_tournament(self):
        """Start a new tournament"""
        try:
            if self.tournament_running:
                messagebox.showwarning("Warning", "Tournament already in progress!")
                return
            
            # Get tournament size
            size = self.tournament_size_var.get()
            
            # Get enabled characters
            enabled_chars = list(self.manager.settings.get("enabled_characters", []))
            if len(enabled_chars) < size:
                messagebox.showerror(
                    "Error",
                    f"Not enough enabled characters for {size}-player tournament!\n"
                    f"Need {size} characters, but only {len(enabled_chars)} are enabled."
                )
                return
            
            # Select random characters for tournament
            tournament_chars = random.sample(enabled_chars, size)
            
            # Create tournament
            from tournament import MugenTournament
            self.tournament = MugenTournament(self.manager, tournament_chars)
            self.tournament_running = True
            
            # Update UI
            self.start_tournament_btn.config(state='disabled')
            self.stop_tournament_btn.config(state='normal')
            self.tournament_status.config(text="Tournament started")
            
            # Update bracket display
            self._update_bracket_display()
            
            # Start tournament monitor
            self._monitor_tournament()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start tournament: {str(e)}")
            print(f"Tournament start error: {e}")
            traceback.print_exc()

    def _stop_tournament(self):
        """Stop the current tournament"""
        if not self.tournament_running:
            return
            
        if messagebox.askyesno("Confirm", "Stop current tournament? This cannot be undone."):
            self.tournament = None
            self.tournament_running = False
            
            # Update UI
            self.start_tournament_btn.config(state='normal')
            self.stop_tournament_btn.config(state='disabled')
            self.tournament_status.config(text="Tournament stopped")
            
            # Clear bracket display
            self.bracket_display.configure(state='normal')
            self.bracket_display.delete('1.0', tk.END)
            self.bracket_display.configure(state='disabled')

    def _monitor_tournament(self):
        """Monitor tournament progress and update display"""
        if not self.tournament_running or not self.tournament:
            return
            
        try:
            if self.tournament.is_complete():
                # Tournament is complete
                winner = self.tournament.get_winner()
                self.tournament_status.config(text=f"Tournament Complete!\nWinner: {winner}")
                self._update_bracket_display()
                
                # Reset tournament state
                self.tournament_running = False
                self.start_tournament_btn.config(state='normal')
                self.stop_tournament_btn.config(state='disabled')
                return
            
            # Check if we need to start a new match
            if not self.tournament.current_battle:
                match_info = self.tournament.start_next_match()
                if match_info:
                    self.tournament_status.config(
                        text=f"Current Match:\n{match_info['p1']} vs {match_info['p2']}"
                    )
            
            # Check current match result
            result = self.tournament.check_match_result()
            if result:
                self._update_bracket_display()
            
            # Continue monitoring
            self.root.after(1000, self._monitor_tournament)
            
        except Exception as e:
            print(f"Tournament monitor error: {e}")
            traceback.print_exc()
            self.root.after(1000, self._monitor_tournament)

    def _update_bracket_display(self):
        """Update the tournament bracket display"""
        if not self.tournament:
            return
            
        try:
            # Get bracket display text
            bracket_text = self.tournament.get_bracket_display()
            
            # Update text widget
            self.bracket_display.configure(state='normal')
            self.bracket_display.delete('1.0', tk.END)
            self.bracket_display.insert('1.0', bracket_text)
            self.bracket_display.configure(state='disabled')
            
        except Exception as e:
            print(f"Error updating bracket display: {e}")
            traceback.print_exc()

    def _create_placeholder_images(self):
        """Create simple placeholder images"""
        try:
            # Create solid color images
            self.placeholder_char = tk.PhotoImage(master=self.root, width=200, height=200)
            self.placeholder_stage = tk.PhotoImage(master=self.root, width=200, height=200)
            
            # Create a dark background with white border
            for y in range(200):
                for x in range(200):
                    # Dark gray background
                    color = '#333333'
                    # White border (10 pixels thick)
                    if x < 10 or x > 189 or y < 10 or y > 189:
                        color = '#ffffff'
                    self.placeholder_char.put(color, (x, y))
                    self.placeholder_stage.put(color, (x, y))
            
            # Store references to prevent garbage collection
            self._placeholder_images = [self.placeholder_char, self.placeholder_stage]
            print("Successfully created placeholder images")
            
        except Exception as e:
            print(f"Error creating placeholder images: {e}")
            traceback.print_exc()
            # Create minimal fallback images
            self.placeholder_char = tk.PhotoImage(master=self.root, width=1, height=1)
            self.placeholder_stage = tk.PhotoImage(master=self.root, width=1, height=1)
            self._placeholder_images = [self.placeholder_char, self.placeholder_stage]

    def _setup_preview_tab(self):
        """Setup the preview tab with character and stage displays"""
        preview_frame = self.tabs['Preview']
        
        # Configure preview frame style
        style = ttk.Style()
        style.configure('Preview.TFrame', background='black')
        style.configure('Preview.TLabel', background='black', foreground='white')
        preview_frame.configure(style='Preview.TFrame')
        
        # Create top frame for character displays
        top_frame = ttk.Frame(preview_frame, style='Preview.TFrame')
        top_frame.pack(fill='x', padx=10, pady=10)
        
        # Create left team display
        self.team1_frame = ttk.Frame(top_frame, style='Preview.TFrame')
        self.team1_frame.pack(side='left', expand=True, fill='both')
        
        # Create VS label
        vs_label = ttk.Label(
            top_frame,
            text="VS",
            style="Preview.TLabel",
            font=("Arial", 24, "bold")
        )
        vs_label.pack(side='left', padx=20)
        
        # Create right team display
        self.team2_frame = ttk.Frame(top_frame, style='Preview.TFrame')
        self.team2_frame.pack(side='right', expand=True, fill='both')
        
        # Create stage preview frame
        stage_frame = ttk.Frame(preview_frame, style='Preview.TFrame')
        stage_frame.pack(fill='x', padx=10, pady=10)
        
        # Add stage label
        stage_label = ttk.Label(
            stage_frame,
            text="Stage",
            style="Preview.TLabel",
            font=("Arial", 16, "bold")
        )
        stage_label.pack()
        
        # Add stage preview
        self.stage_preview = ttk.Label(
            stage_frame,
            image=self.placeholder_stage,
            style="Preview.TLabel"
        )
        self.stage_preview.pack(pady=5)
        
        # Create initial empty team displays
        self._create_team_display(self.team1_frame, [], "Team 1", "left")
        self._create_team_display(self.team2_frame, [], "Team 2", "right")
        
        # Add betting timer
        self.preview_timer = ttk.Label(
            preview_frame,
            text="",
            style="Preview.TLabel",
            font=("Arial", 14, "bold")
        )
        self.preview_timer.pack(pady=10)
        
        # Add local betting section
        betting_frame = ttk.Frame(preview_frame, style='Preview.TFrame')
        betting_frame.pack(fill='x', padx=10, pady=10)
        
        # Add betting header
        betting_label = ttk.Label(
            betting_frame,
            text="Local Betting",
            style="Preview.TLabel",
            font=("Arial", 16, "bold")
        )
        betting_label.pack(pady=5)
        
        # Add betting stats
        self.betting_stats_frame = ttk.Frame(betting_frame, style='Preview.TFrame')
        self.betting_stats_frame.pack(fill='x', pady=5)
        
        # Team 1 betting stats
        team1_bet_frame = ttk.Frame(self.betting_stats_frame, style='Preview.TFrame')
        team1_bet_frame.pack(side='left', expand=True, fill='both', padx=5)
        
        team1_bet_label = ttk.Label(
            team1_bet_frame,
            text="Team 1 Pool",
            style="Preview.TLabel",
            font=("Arial", 12)
        )
        team1_bet_label.pack()
        
        self.team1_bet_amount = ttk.Label(
            team1_bet_frame,
            text="0",
            style="Preview.TLabel",
            font=("Arial", 14, "bold")
        )
        self.team1_bet_amount.pack()
        
        self.team1_odds = ttk.Label(
            team1_bet_frame,
            text="Odds: 1.0x",
            style="Preview.TLabel",
            font=("Arial", 12)
        )
        self.team1_odds.pack()
        
        # Team 2 betting stats
        team2_bet_frame = ttk.Frame(self.betting_stats_frame, style='Preview.TFrame')
        team2_bet_frame.pack(side='right', expand=True, fill='both', padx=5)
        
        team2_bet_label = ttk.Label(
            team2_bet_frame,
            text="Team 2 Pool",
            style="Preview.TLabel",
            font=("Arial", 12)
        )
        team2_bet_label.pack()
        
        self.team2_bet_amount = ttk.Label(
            team2_bet_frame,
            text="0",
            style="Preview.TLabel",
            font=("Arial", 14, "bold")
        )
        self.team2_bet_amount.pack()
        
        self.team2_odds = ttk.Label(
            team2_bet_frame,
            text="Odds: 1.0x",
            style="Preview.TLabel",
            font=("Arial", 12)
        )
        self.team2_odds.pack()
        
        # Add betting controls
        betting_controls = ttk.Frame(betting_frame, style='Preview.TFrame')
        betting_controls.pack(fill='x', pady=10)
        
        # User points display
        self.user_points_label = ttk.Label(
            betting_controls,
            text="Your Points: 1000",
            style="Preview.TLabel",
            font=("Arial", 12, "bold")
        )
        self.user_points_label.pack(pady=5)
        
        # Bet amount entry
        bet_amount_frame = ttk.Frame(betting_controls, style='Preview.TFrame')
        bet_amount_frame.pack(fill='x', pady=5)
        
        bet_amount_label = ttk.Label(
            bet_amount_frame,
            text="Bet Amount:",
            style="Preview.TLabel"
        )
        bet_amount_label.pack(side='left', padx=5)
        
        self.bet_amount_var = tk.StringVar(value="100")
        bet_amount_entry = ttk.Entry(
            bet_amount_frame,
            textvariable=self.bet_amount_var,
            width=10
        )
        bet_amount_entry.pack(side='left', padx=5)
        
        # Bet buttons
        bet_buttons_frame = ttk.Frame(betting_controls, style='Preview.TFrame')
        bet_buttons_frame.pack(fill='x', pady=5)
        
        # Team 1 bet button
        self.team1_bet_button = ttk.Button(
            bet_buttons_frame,
            text="Bet on Team 1",
            command=lambda: self._place_local_bet("1")
        )
        self.team1_bet_button.pack(side='left', expand=True, padx=5)
        
        # Team 2 bet button
        self.team2_bet_button = ttk.Button(
            bet_buttons_frame,
            text="Bet on Team 2",
            command=lambda: self._place_local_bet("2")
        )
        self.team2_bet_button.pack(side='right', expand=True, padx=5)

    def _create_fighter_display(self, parent, fighter, team_name, side):
        """Create a display for a single fighter"""
        frame = ttk.Frame(parent, style='Preview.TFrame')
        frame.pack(side=side, padx=5, pady=5)
        
        # Add character portrait
        portrait = ttk.Label(
            frame,
            image=self.placeholder_char,
            style='Preview.TLabel'
        )
        portrait.pack(pady=2)
        
        # Add character name
        name = ttk.Label(
            frame,
            text=fighter if fighter else "???",
            style='Preview.TLabel',
            font=("Arial", 12)
        )
        name.pack(pady=2)
        
        return frame

    def _create_team_display(self, parent, team, team_name, side):
        """Create a display for a team of fighters"""
        # Clear existing widgets
        for widget in parent.winfo_children():
            widget.destroy()
        
        # Add team name
        team_label = ttk.Label(
            parent,
            text=team_name,
            style='Preview.TLabel',
            font=("Arial", 14, "bold")
        )
        team_label.pack(pady=5)
        
        # Create frame for fighters
        fighters_frame = ttk.Frame(parent, style='Preview.TFrame')
        fighters_frame.pack(expand=True, fill='both')
        
        # Add fighters (or placeholder if empty)
        if not team:
            self._create_fighter_display(fighters_frame, None, team_name, side)
        else:
            for fighter in team:
                self._create_fighter_display(fighters_frame, fighter, team_name, side)

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
                # End Twitch betting poll if it exists
                if hasattr(self, 'twitch_bot') and self.twitch_bot and hasattr(self.twitch_bot, 'betting_active') and self.twitch_bot.betting_active:
                    asyncio.run_coroutine_threadsafe(
                        self.twitch_bot.end_poll(),
                        self.twitch_bot.loop
                    )
                
                # End local betting
                if hasattr(self.manager, 'local_betting_active') and self.manager.local_betting_active:
                    self.manager.end_local_betting()
                    self._update_local_betting_ui(betting_active=False)
                
                # Update preview tab status
                if hasattr(self, 'preview_timer'):
                    self.preview_timer.config(text="BATTLE STARTING!")
                
                # Start the actual battle with the stored battle info
                self._start_actual_battle(self.current_battle_info)
                
        except Exception as e:
            print(f"Timer update error: {e}")
            traceback.print_exc()
            
    def _place_local_bet(self, team):
        """Place a local bet on the specified team"""
        try:
            # Get bet amount
            try:
                amount = int(self.bet_amount_var.get())
            except ValueError:
                messagebox.showerror("Invalid Bet", "Please enter a valid bet amount")
                return
                
            # Place bet
            username = "Player"  # Default username
            success = self.manager.place_local_bet(username, team, amount)
            
            if success:
                # Update UI
                self._update_local_betting_ui()
                messagebox.showinfo("Bet Placed", f"Bet of {amount} placed on Team {team}")
            else:
                if not self.manager.local_betting_active:
                    messagebox.showerror("Betting Closed", "Betting is currently closed")
                elif amount > self.manager.local_user_points.get(username, 0):
                    messagebox.showerror("Insufficient Points", "You don't have enough points for this bet")
                else:
                    messagebox.showerror("Invalid Bet", "Unable to place bet")
                    
        except Exception as e:
            print(f"Error placing bet: {e}")
            traceback.print_exc()
            
    def _update_local_betting_ui(self, betting_active=None):
        """Update the local betting UI with current stats"""
        try:
            # Get betting stats
            stats = self.manager.get_local_betting_stats()
            
            # Update betting status if provided
            if betting_active is not None:
                stats["active"] = betting_active
                
            # Update team 1 stats
            self.team1_bet_amount.config(text=f"{stats['team1_total']}")
            self.team1_odds.config(text=f"Odds: {stats['team1_payout']:.2f}x")
            
            # Update team 2 stats
            self.team2_bet_amount.config(text=f"{stats['team2_total']}")
            self.team2_odds.config(text=f"Odds: {stats['team2_payout']:.2f}x")
            
            # Update user points
            username = "Player"  # Default username
            points = self.manager.local_user_points.get(username, 0)
            self.user_points_label.config(text=f"Your Points: {points}")
            
            # Enable/disable betting controls based on betting status
            state = "normal" if stats["active"] else "disabled"
            self.team1_bet_button.config(state=state)
            self.team2_bet_button.config(state=state)
            
        except Exception as e:
            print(f"Error updating betting UI: {e}")
            traceback.print_exc()
            
    def _toggle_local_betting(self):
        """Toggle local betting on/off"""
        try:
            # Update manager setting
            self.manager.local_betting_enabled = self.local_betting_enabled_var.get()
            
            # Update UI if we're in preview tab
            if hasattr(self, 'team1_bet_button') and hasattr(self, 'team2_bet_button'):
                if self.manager.local_betting_enabled:
                    # Only enable buttons if betting is active
                    state = "normal" if self.manager.local_betting_active else "disabled"
                else:
                    state = "disabled"
                    
                self.team1_bet_button.config(state=state)
                self.team2_bet_button.config(state=state)
                
        except Exception as e:
            print(f"Error toggling local betting: {e}")
            traceback.print_exc()
            
    def _reset_local_betting_points(self):
        """Reset local betting points"""
        try:
            # Confirm reset
            if not messagebox.askyesno("Reset Points", "Are you sure you want to reset all betting points?"):
                return
                
            # Reset points
            self.manager.local_user_points = {"Player": 1000}  # Default user with 1000 points
            
            # Update UI if we're in preview tab
            if hasattr(self, 'user_points_label'):
                self.user_points_label.config(text="Your Points: 1000")
                
            messagebox.showinfo("Points Reset", "Betting points have been reset to default values.")
            
        except Exception as e:
            print(f"Error resetting betting points: {e}")
            traceback.print_exc()

    def _process_local_betting_results(self, battle_result):
        """Process local betting results after a battle"""
        try:
            print("Processing local betting results...")
            
            if not hasattr(self.manager, 'local_betting_enabled') or not self.manager.local_betting_enabled:
                print("Local betting is disabled, skipping processing")
                return
                
            # Check for either active betting or pending bet results
            if not (hasattr(self.manager, 'local_betting_active') and self.manager.local_betting_active) and \
               not (hasattr(self.manager, 'pending_bet_results') and self.manager.pending_bet_results):
                print("No active betting session or pending bets, skipping processing")
                return
                
            # Determine winning team
            if battle_result["p1_score"] > battle_result["p2_score"]:
                winning_team = "1"
            else:
                winning_team = "2"
                
            print(f"Winning team: {winning_team}")
            
            # Process results
            results = self.manager.process_local_betting_results(winning_team)
            print(f"Betting results: {results}")
            
            # Show results if there were any bets
            if results["winning_pool"] > 0 or results["losing_pool"] > 0:
                result_text = f"Team {winning_team} won!\n\n"
                
                if results["winners"]:
                    result_text += f"Payout: {results.get('payout_ratio', 0):.2f}x\n\n"
                    result_text += "Winners:\n"
                    
                    for winner in results["winners"]:
                        result_text += f"{winner['username']}: {winner['bet']} → {winner['winnings']} (+{winner['profit']})\n"
                else:
                    result_text += "No winners this round."
                    
                messagebox.showinfo("Betting Results", result_text)
            else:
                print("No bets were placed for this battle")
                
            # Update UI
            self._update_local_betting_ui(betting_active=False)
            
        except Exception as e:
            print(f"Error processing betting results: {e}")
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

    def _filter_characters(self, *args):
        """Filter characters based on search text"""
        try:
            search_text = self.char_search_var.get().lower()
            
            # Clear existing items
            for item in self.character_tree.get_children():
                self.character_tree.delete(item)
            
            # Get character stats
            char_stats = self.manager.character_stats
            enabled_chars = self.manager.settings.get("enabled_characters", [])
            
            # Add filtered characters
            for char in sorted(self.manager.scan_characters()):
                # Skip if doesn't match search
                if search_text and search_text not in char.lower():
                    continue
                    
                stats = char_stats.get(char, {"wins": 0, "losses": 0})
                total_matches = stats["wins"] + stats["losses"]
                win_rate = f"{(stats['wins']/total_matches)*100:.1f}%" if total_matches > 0 else "0.0%"
                
                self.character_tree.insert(
                    '',
                    'end',
                    iid=char,
                    values=(
                        '✓' if char in enabled_chars else '',
                        char,
                        stats["wins"],
                        stats["losses"],
                        win_rate
                    )
                )
                
        except Exception as e:
            print(f"Error filtering characters: {e}")
            traceback.print_exc()

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
            
            # Get all items
            item_list = [(self.character_tree.set(item, column), item) for item in self.character_tree.get_children('')]
            
            # Sort items
            if column in ('Wins', 'Losses'):
                # Sort numerically
                item_list.sort(key=lambda x: int(x[0]) if x[0].isdigit() else 0, reverse=(new_order == 'desc'))
            elif column == 'Win Rate':
                # Sort by win rate percentage
                item_list.sort(key=lambda x: float(x[0].rstrip('%')) if x[0] != '0.0%' else 0, reverse=(new_order == 'desc'))
            else:
                # Sort alphabetically
                item_list.sort(reverse=(new_order == 'desc'))
            
            # Rearrange items in sorted order
            for index, (_, item) in enumerate(item_list):
                self.character_tree.move(item, '', index)

        except Exception as e:
            print(f"Error sorting characters: {e}")
            traceback.print_exc()

    def _toggle_character_status(self, event):
        """Toggle character enabled/disabled status"""
        try:
            # Get clicked item
            item = self.character_tree.identify('item', event.x, event.y)
            if not item:
                return
                
            # Get current values
            values = list(self.character_tree.item(item)['values'])
            char_name = values[1]  # Name is in second column
            
            # Toggle enabled status
            enabled_chars = set(self.manager.settings.get("enabled_characters", []))
            if char_name in enabled_chars:
                enabled_chars.remove(char_name)
                values[0] = ''  # Clear checkmark
            else:
                enabled_chars.add(char_name)
                values[0] = '✓'  # Add checkmark
            
            # Update tree and settings
            self.character_tree.item(item, values=values)
            self.manager.settings["enabled_characters"] = list(enabled_chars)
            
            # Save settings if auto-save is enabled
            if self.settings.get("autosave", True):
                self.save_config()
                
        except Exception as e:
            print(f"Error toggling character status: {e}")
            traceback.print_exc()

    def _select_all_chars(self):
        """Enable all characters"""
        try:
            # Get all characters
            all_chars = [self.character_tree.item(item)["values"][1] 
                        for item in self.character_tree.get_children()]
            
            # Update settings
            self.manager.settings["enabled_characters"] = all_chars
            
            # Update display
            self._populate_character_list()
            
            # Save if auto-save enabled
            if self.settings.get("autosave", True):
                self.save_config()
                
        except Exception as e:
            print(f"Error selecting all characters: {e}")
            traceback.print_exc()

    def _deselect_all_chars(self):
        """Disable all characters"""
        try:
            # Clear enabled characters
            self.manager.settings["enabled_characters"] = []
            
            # Update display
            self._populate_character_list()
            
            # Save if auto-save enabled
            if self.settings.get("autosave", True):
                self.save_config()
                
        except Exception as e:
            print(f"Error deselecting all characters: {e}")
            traceback.print_exc()

    def _invert_char_selection(self):
        """Invert the selection of characters"""
        try:
            # Get all characters and currently enabled ones
            all_chars = [self.character_tree.item(item)["values"][1] 
                        for item in self.character_tree.get_children()]
            enabled_chars = set(self.manager.settings.get("enabled_characters", []))
            
            # Invert selection
            new_enabled = [char for char in all_chars if char not in enabled_chars]
            self.manager.settings["enabled_characters"] = new_enabled
            
            # Update display
            self._populate_character_list()
            
            # Save if auto-save enabled
            if self.settings.get("autosave", True):
                self.save_config()
                
        except Exception as e:
            print(f"Error inverting character selection: {e}")
            traceback.print_exc()

    def _check_battle_result(self):
        """Check for battle results and update stats"""
        try:
            # Check if battle is running
            mugen_running = self.manager._check_mugen_running()
            if not mugen_running:
                print("MUGEN is no longer running, checking for final results...")
                
            # Save stage information before checking result
            current_stage = None
            if hasattr(self.manager, 'current_battle') and self.manager.current_battle is not None:
                if 'stage' in self.manager.current_battle:
                    current_stage = self.manager.current_battle["stage"]
                
            # Try to get battle result
            result = self.manager.check_battle_result()
            if not result:
                if not mugen_running:
                    print("No result found but MUGEN has stopped. Battle may have been terminated.")
                    # Re-enable battle button
                    self.start_battle_btn.config(state='normal')
                    
                    # Make sure to clean up any active betting
                    if hasattr(self.manager, 'local_betting_active') and self.manager.local_betting_active:
                        print("Cleaning up active betting session")
                        self.manager.local_betting_active = False
                        self.manager.local_bets = {"1": {}, "2": {}}
                        self._update_local_betting_ui(betting_active=False)
                    
                    # Make sure watcher is terminated
                    if hasattr(self.manager, 'watcher_process') and self.manager.watcher_process:
                        try:
                            self.manager.watcher_process.terminate()
                            self.manager.watcher_process = None
                        except:
                            pass
                    
                    return
                
                # Schedule another check
                self.root.after(500, self._check_battle_result)
                return
                
            # Process battle result
            print(f"Battle result: {result}")
            
            # Update character stats
            self.manager.update_stats(result["winner"], result["loser"])
            
            # Update stage stats using saved stage information
            if current_stage:
                self.manager.update_stage_stats(current_stage)
            else:
                print("Warning: current_battle is None, skipping stage stats update")
            
            # Record battle in history
            self.manager.record_battle(result)
            
            # Update stats display
            self._populate_stats()
            
            # Process local betting results
            if hasattr(self.manager, 'local_betting_enabled') and self.manager.local_betting_enabled:
                self._process_local_betting_results(result)
            
            # If Twitch bot is connected, handle betting results
            if hasattr(self, 'twitch_bot') and self.twitch_bot and hasattr(self.twitch_bot, 'connected') and self.twitch_bot.connected:
                try:
                    # Format team names
                    if result['mode'] == "single":
                        p1_name = result['p1']
                        p2_name = result['p2']
                    else:
                        p1_name = " & ".join(result['p1'])
                        p2_name = " & ".join(result['p2'])
                    
                    # Send result to Twitch
                    asyncio.run_coroutine_threadsafe(
                        self.twitch_bot.handle_battle_result(
                            result["winner"],
                            p1_name,
                            p2_name
                        ),
                        self.twitch_bot.loop
                    )
                except Exception as e:
                    print(f"Error handling Twitch result: {e}")
                    traceback.print_exc()
            
            # Re-enable battle button
            self.start_battle_btn.config(state='normal')
            
            # Make sure watcher is terminated
            if hasattr(self.manager, 'watcher_process') and self.manager.watcher_process:
                try:
                    self.manager.watcher_process.terminate()
                    self.manager.watcher_process = None
                except:
                    pass
                    
            print("Battle processing complete")
            
            # Start another battle if continuous mode is enabled
            if hasattr(self, 'continuous_battles_var') and self.continuous_battles_var.get():
                print("Continuous battles mode is enabled, starting next battle in 3 seconds...")
                self.root.after(3000, self._start_battle)
            
        except Exception as e:
            print(f"Error checking battle result: {e}")
            traceback.print_exc()
            
            # Re-enable battle button in case of error
            self.start_battle_btn.config(state='normal')

    def _sort_stats(self, column):
        """Sort statistics list by column"""
        try:
            # Get current sort column and order
            current_sort = getattr(self, '_stats_sort_column', None)
            current_order = getattr(self, '_stats_sort_order', 'asc')
            
            # Update sort order
            if current_sort == column:
                new_order = 'desc' if current_order == 'asc' else 'asc'
            else:
                new_order = 'asc'
            
            # Store new sort settings
            self._stats_sort_column = column
            self._stats_sort_order = new_order
            
            # Update column header
            for col in self.stats_tree['columns']:
                if col == column:
                    self.stats_tree.heading(col, text=f"{col} {'↓' if new_order == 'desc' else '↑'}")
                else:
                    self.stats_tree.heading(col, text=col)
            
            # Get all items
            item_list = [(self.stats_tree.set(item, column), item) for item in self.stats_tree.get_children('')]
            
            # Sort items
            if column in ('Wins', 'Losses'):
                # Sort numerically
                item_list.sort(key=lambda x: int(x[0]) if x[0].isdigit() else 0, reverse=(new_order == 'desc'))
            elif column == 'Win Rate':
                # Sort by win rate percentage
                item_list.sort(key=lambda x: float(x[0].rstrip('%')) if x[0] != '0.0%' else 0, reverse=(new_order == 'desc'))
            else:
                # Sort alphabetically
                item_list.sort(reverse=(new_order == 'desc'))
            
            # Rearrange items in sorted order
            for index, (_, item) in enumerate(item_list):
                self.stats_tree.move(item, '', index)

        except Exception as e:
            print(f"Error sorting stats: {e}")
            traceback.print_exc()

    def save_config(self):
        """Save application configuration to file"""
        try:
            config = {
                "enabled_characters": list(self.manager.settings["enabled_characters"]),
                "enabled_stages": list(self.manager.settings["enabled_stages"]),
                "battle_mode": self.mode_var.get() if hasattr(self, 'mode_var') else "single",
                "ai_level": self.ai_level_var.get() if hasattr(self, 'ai_level_var') else "4",
                "random_stage": self.random_stage_var.get() if hasattr(self, 'random_stage_var') else True,
                "continuous_mode": self.continuous_battles_var.get() if hasattr(self, 'continuous_battles_var') else False,
                "autosave": self.autosave_var.get() if hasattr(self, 'autosave_var') else True,
                "tab_order": self.tab_order if hasattr(self, 'tab_order') else None,
                "betting_enabled": self.betting_enabled_var.get() if hasattr(self, 'betting_enabled_var') else False,
                "betting_duration": self.betting_duration_var.get() if hasattr(self, 'betting_duration_var') else "30",
                "local_betting_enabled": self.local_betting_enabled_var.get() if hasattr(self, 'local_betting_enabled_var') else True,
                "local_user_points": self.manager.local_user_points if hasattr(self.manager, 'local_user_points') else {"Player": 1000},
                "tournament_size": self.tournament_size_var.get() if hasattr(self, 'tournament_size_var') else 8
            }
            
            with open('config.json', 'w') as f:
                json.dump(config, f, indent=2)
                
            print("Configuration saved")
            
        except Exception as e:
            print(f"Error saving configuration: {e}")
            traceback.print_exc()

    def load_config(self):
        """Load application configuration from file"""
        try:
            if not Path('config.json').exists():
                print("No configuration file found")
                return
                
            with open('config.json', 'r') as f:
                config = json.load(f)
                
            # Load character and stage settings
            if "enabled_characters" in config:
                self.manager.settings["enabled_characters"] = set(config["enabled_characters"])
                
            if "enabled_stages" in config:
                self.manager.settings["enabled_stages"] = set(config["enabled_stages"])
                
            # Load battle settings
            if "battle_mode" in config and hasattr(self, 'mode_var'):
                self.mode_var.set(config["battle_mode"])
                
            if "ai_level" in config and hasattr(self, 'ai_level_var'):
                self.ai_level_var.set(config["ai_level"])
                
            if "random_stage" in config and hasattr(self, 'random_stage_var'):
                self.random_stage_var.set(config["random_stage"])
                
            if "continuous_mode" in config and hasattr(self, 'continuous_battles_var'):
                self.continuous_battles_var.set(config["continuous_mode"])
                self.continuous_battles = config["continuous_mode"]
                
            if "tournament_size" in config and hasattr(self, 'tournament_size_var'):
                self.tournament_size_var.set(config["tournament_size"])
                
            # Load UI settings
            if "autosave" in config and hasattr(self, 'autosave_var'):
                self.autosave_var.set(config["autosave"])
                
            if "tab_order" in config and config["tab_order"] and hasattr(self, '_load_tab_order'):
                self.tab_order = config["tab_order"]
                self._load_tab_order()
                
            # Load Twitch settings
            if "betting_enabled" in config and hasattr(self, 'betting_enabled_var'):
                self.betting_enabled_var.set(config["betting_enabled"])
                
            if "betting_duration" in config and hasattr(self, 'betting_duration_var'):
                self.betting_duration_var.set(config["betting_duration"])
                
            # Load local betting settings
            if "local_betting_enabled" in config:
                if hasattr(self, 'local_betting_enabled_var'):
                    self.local_betting_enabled_var.set(config["local_betting_enabled"])
                self.manager.local_betting_enabled = config["local_betting_enabled"]
                
            if "local_user_points" in config:
                self.manager.local_user_points = config["local_user_points"]
                
            # Update UI
            self._populate_character_list()
            self._populate_stage_list()
            
            print("Configuration loaded")
            
        except Exception as e:
            print(f"Error loading configuration: {e}")
            traceback.print_exc()

    def setup_gui(self):
        """Setup the main GUI window"""
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True)
        
        # Initialize tabs dictionary
        self.tabs = {}
        
        # Create tabs
        self.tabs['Preview'] = ttk.Frame(self.notebook)
        self.tabs['Battle'] = ttk.Frame(self.notebook)
        self.tabs['Tournament'] = ttk.Frame(self.notebook)  # Add Tournament tab
        self.tabs['Characters'] = ttk.Frame(self.notebook)
        self.tabs['Stages'] = ttk.Frame(self.notebook)
        self.tabs['Stats'] = ttk.Frame(self.notebook)
        self.tabs['Settings'] = ttk.Frame(self.notebook)
        
        # Add tabs to notebook
        for name, frame in self.tabs.items():
            self.notebook.add(frame, text=name)
            
        # Setup individual tabs
        self._setup_preview_tab()
        self._setup_battle_tab()
        self._setup_tournament_tab()  # Add tournament tab setup
        self._setup_characters_tab()
        self._setup_stages_tab()
        self._setup_stats_tab()
        self._setup_settings_tab()
        
        # Setup draggable tabs
        self._setup_draggable_tabs()
        
        # Load saved tab order
        self._load_tab_order()
        
        # Create menu
        self.create_menu()

    def _setup_battle_tab(self):
        """Setup the battle tab with controls and battle log"""
        battle_frame = self.tabs['Battle']
        
        # Create control frame
        control_frame = ttk.Frame(battle_frame)
        control_frame.pack(fill='x', padx=10, pady=5)
        
        # Add battle mode selection
        mode_label = ttk.Label(control_frame, text="Battle Mode:")
        mode_label.pack(side='left', padx=5)
        
        mode_combo = ttk.Combobox(control_frame, textvariable=self.mode_var, 
                                 values=["single", "simul"], state='readonly')
        mode_combo.pack(side='left', padx=5)
        
        # Add AI level selection
        ai_label = ttk.Label(control_frame, text="AI Level:")
        ai_label.pack(side='left', padx=5)
        
        ai_combo = ttk.Combobox(control_frame, textvariable=self.ai_level_var,
                               values=["1", "2", "3", "4", "5", "6", "7", "8"], state='readonly')
        ai_combo.pack(side='left', padx=5)
        
        # Add random stage toggle
        random_stage_check = ttk.Checkbutton(control_frame, text="Random Stage",
                                           variable=self.random_stage_var)
        random_stage_check.pack(side='left', padx=5)
        
        # Add continuous battles toggle
        continuous_check = ttk.Checkbutton(control_frame, text="Continuous Battles",
                                         variable=self.continuous_battles_var,
                                         command=self._toggle_continuous_battles)
        continuous_check.pack(side='left', padx=5)
        
        # Add start battle button
        self.start_battle_btn = ttk.Button(control_frame, text="Start Battle",
                                         command=self._start_battle)
        self.start_battle_btn.pack(side='right', padx=5)
        
        # Add battle log
        log_frame = ttk.Frame(battle_frame)
        log_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        log_label = ttk.Label(log_frame, text="Battle Log:")
        log_label.pack(anchor='w')
        
        self.battle_log = ScrolledText(log_frame, height=20)
        self.battle_log.pack(fill='both', expand=True)

    def _setup_characters_tab(self):
        """Setup the characters tab with character list and controls"""
        char_frame = self.tabs['Characters']
        
        # Create search frame
        search_frame = ttk.Frame(char_frame)
        search_frame.pack(fill='x', padx=10, pady=5)
        
        search_label = ttk.Label(search_frame, text="Search:")
        search_label.pack(side='left', padx=5)
        
        self.char_search_var = tk.StringVar()
        self.char_search_var.trace('w', self._filter_characters)
        search_entry = ttk.Entry(search_frame, textvariable=self.char_search_var)
        search_entry.pack(side='left', fill='x', expand=True, padx=5)
        
        # Create button frame
        button_frame = ttk.Frame(char_frame)
        button_frame.pack(fill='x', padx=10, pady=5)
        
        select_all_btn = ttk.Button(button_frame, text="Select All",
                                  command=self._select_all_chars)
        select_all_btn.pack(side='left', padx=5)
        
        deselect_all_btn = ttk.Button(button_frame, text="Deselect All",
                                    command=self._deselect_all_chars)
        deselect_all_btn.pack(side='left', padx=5)
        
        invert_btn = ttk.Button(button_frame, text="Invert Selection",
                              command=self._invert_char_selection)
        invert_btn.pack(side='left', padx=5)
        
        # Create character tree
        self.character_tree = ttk.Treeview(char_frame, columns=('enabled', 'name', 'wins', 'losses', 'win_rate'),
                                         show='headings', selectmode='none')
        
        self.character_tree.heading('enabled', text='')
        self.character_tree.heading('name', text='Character')
        self.character_tree.heading('wins', text='Wins')
        self.character_tree.heading('losses', text='Losses')
        self.character_tree.heading('win_rate', text='Win Rate')
        
        self.character_tree.column('enabled', width=30, anchor='center')
        self.character_tree.column('name', width=200)
        self.character_tree.column('wins', width=80, anchor='center')
        self.character_tree.column('losses', width=80, anchor='center')
        self.character_tree.column('win_rate', width=80, anchor='center')
        
        self.character_tree.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Bind click event for toggling
        self.character_tree.bind('<Button-1>', self._toggle_character_status)
        
        # Initial population
        self._populate_character_list()

    def _setup_stages_tab(self):
        """Setup the stages tab with stage list and controls"""
        stage_frame = self.tabs['Stages']
        
        # Create button frame
        button_frame = ttk.Frame(stage_frame)
        button_frame.pack(fill='x', padx=10, pady=5)
        
        select_all_btn = ttk.Button(button_frame, text="Select All",
                                  command=self._select_all_stages)
        select_all_btn.pack(side='left', padx=5)
        
        deselect_all_btn = ttk.Button(button_frame, text="Deselect All",
                                    command=self._deselect_all_stages)
        deselect_all_btn.pack(side='left', padx=5)
        
        invert_btn = ttk.Button(button_frame, text="Invert Selection",
                              command=self._invert_stage_selection)
        invert_btn.pack(side='left', padx=5)
        
        # Create stage tree
        self.stage_tree = ttk.Treeview(stage_frame, columns=('enabled', 'name', 'times_used', 'last_used'),
                                     show='headings', selectmode='none')
        
        self.stage_tree.heading('enabled', text='')
        self.stage_tree.heading('name', text='Stage')
        self.stage_tree.heading('times_used', text='Times Used')
        self.stage_tree.heading('last_used', text='Last Used')
        
        self.stage_tree.column('enabled', width=30, anchor='center')
        self.stage_tree.column('name', width=200)
        self.stage_tree.column('times_used', width=100, anchor='center')
        self.stage_tree.column('last_used', width=150, anchor='center')
        
        self.stage_tree.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Bind click event for toggling
        self.stage_tree.bind('<Button-1>', self._toggle_stage_status)
        
        # Initial population
        self._populate_stage_list()

    def _setup_stats_tab(self):
        """Setup the stats tab with character statistics"""
        stats_frame = self.tabs['Stats']
        
        # Create stats tree
        self.stats_tree = ttk.Treeview(stats_frame, columns=('character', 'wins', 'losses', 'win_rate', 'tier'),
                                     show='headings')
        
        self.stats_tree.heading('character', text='Character')
        self.stats_tree.heading('wins', text='Wins')
        self.stats_tree.heading('losses', text='Losses')
        self.stats_tree.heading('win_rate', text='Win Rate')
        self.stats_tree.heading('tier', text='Tier')
        
        self.stats_tree.column('character', width=200)
        self.stats_tree.column('wins', width=80, anchor='center')
        self.stats_tree.column('losses', width=80, anchor='center')
        self.stats_tree.column('win_rate', width=80, anchor='center')
        self.stats_tree.column('tier', width=50, anchor='center')
        
        self.stats_tree.pack(fill='both', expand=True, padx=10, pady=5)
        
        # Add sorting functionality
        for col in ('character', 'wins', 'losses', 'win_rate', 'tier'):
            self.stats_tree.heading(col, command=lambda c=col: self._sort_stats(c))
        
        # Initial population
        self._populate_stats()

    def _setup_settings_tab(self):
        """Setup the settings tab with configuration options"""
        settings_frame = self.tabs['Settings']
        
        # Create local betting section
        local_betting_frame = ttk.LabelFrame(settings_frame, text="Local Betting")
        local_betting_frame.pack(fill='x', padx=10, pady=5)
        
        # Create local betting enabled variable if it doesn't exist
        if not hasattr(self, 'local_betting_enabled_var'):
            self.local_betting_enabled_var = tk.BooleanVar(value=self.manager.local_betting_enabled)
            
        # Add local betting toggle
        local_betting_check = ttk.Checkbutton(
            local_betting_frame, 
            text="Enable local betting",
            variable=self.local_betting_enabled_var,
            command=self._toggle_local_betting
        )
        local_betting_check.pack(anchor='w', padx=5, pady=5)
        
        # Add reset points button
        reset_points_button = ttk.Button(
            local_betting_frame,
            text="Reset Local Betting Points",
            command=self._reset_local_betting_points
        )
        reset_points_button.pack(anchor='w', padx=5, pady=5)
        
        # Add Twitch integration settings
        twitch_frame = ttk.LabelFrame(settings_frame, text="Twitch Integration")
        twitch_frame.pack(fill='x', padx=10, pady=5)
        
        # Add Twitch connection controls
        connection_frame = ttk.Frame(twitch_frame)
        connection_frame.pack(fill='x', padx=5, pady=5)
        
        # Create Twitch connection status label
        self.twitch_status_label = ttk.Label(
            connection_frame, 
            text="Status: Disconnected",
            foreground="red"
        )
        self.twitch_status_label.pack(side='left', padx=5)
        
        # Create Twitch connection button
        self.twitch_connect_button = ttk.Button(
            connection_frame,
            text="Connect to Twitch",
            command=self._connect_to_twitch
        )
        self.twitch_connect_button.pack(side='right', padx=5)
        
        # Initialize Twitch betting variables if they don't exist
        if not hasattr(self, 'betting_enabled_var'):
            self.betting_enabled_var = tk.BooleanVar(value=False)
            
        if not hasattr(self, 'betting_duration_var'):
            self.betting_duration_var = tk.StringVar(value="30")
        
        # Betting settings
        betting_check = ttk.Checkbutton(twitch_frame, text="Enable betting",
                                      variable=self.betting_enabled_var)
        betting_check.pack(anchor='w', padx=5, pady=5)
        
        duration_frame = ttk.Frame(twitch_frame)
        duration_frame.pack(fill='x', padx=5, pady=5)
        
        duration_label = ttk.Label(duration_frame, text="Betting duration (seconds):")
        duration_label.pack(side='left', padx=5)
        
        duration_entry = ttk.Entry(duration_frame, textvariable=self.betting_duration_var,
                                 width=10)
        duration_entry.pack(side='left', padx=5)

    def create_menu(self):
        """Create the application menu"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Save Configuration", command=self.save_config)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Refresh Character List", command=self._populate_character_list)
        view_menu.add_command(label="Refresh Stage List", command=self._populate_stage_list)
        view_menu.add_command(label="Refresh Statistics", command=self._populate_stats)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self._show_about)

    def start_auto_save_timer(self):
        """Start timer for auto-saving configuration"""
        if self.settings.get("autosave", True):
            self.save_config()
        self.root.after(300000, self.start_auto_save_timer)  # Save every 5 minutes

    def run(self):
        """Start the GUI application"""
        self.root.mainloop()

    def _populate_character_list(self):
        """Populate the character list with current data"""
        # Clear existing items
        for item in self.character_tree.get_children():
            self.character_tree.delete(item)
        
        # Get character stats
        char_stats = self.manager.character_stats
        enabled_chars = self.manager.settings.get("enabled_characters", [])
        
        # Add characters
        for char in sorted(self.manager.scan_characters()):
            stats = char_stats.get(char, {"wins": 0, "losses": 0})
            total_matches = stats["wins"] + stats["losses"]
            win_rate = f"{(stats['wins']/total_matches)*100:.1f}%" if total_matches > 0 else "0.0%"
            
            self.character_tree.insert(
                '',
                'end',
                iid=char,
                values=(
                    '✓' if char in enabled_chars else '',
                    char,
                    stats["wins"],
                    stats["losses"],
                    win_rate
                )
            )

    def _populate_stage_list(self):
        """Populate the stage list with current data"""
        # Clear existing items
        for item in self.stage_tree.get_children():
            self.stage_tree.delete(item)
        
        # Get stage stats
        stage_stats = self.manager.stage_stats
        enabled_stages = self.manager.settings.get("enabled_stages", [])
        
        # Add stages
        for stage in sorted(self.manager.scan_stages()):
            stats = stage_stats.get(stage, {
                "times_used": 0,
                "last_used": "Never",
                "total_duration": 0
            })
            
            self.stage_tree.insert(
                '',
                'end',
                iid=stage,
                values=(
                    '✓' if stage in enabled_stages else '',
                    stage,
                    stats["times_used"],
                    stats["last_used"]
                )
            )

    def _populate_stats(self):
        """Populate the stats tree with current statistics"""
        # Clear existing items
        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)
        
        # Get character stats
        char_stats = self.manager.character_stats
        
        # Add characters
        for char in sorted(self.manager.scan_characters()):
            stats = char_stats.get(char, {"wins": 0, "losses": 0})
            total_matches = stats["wins"] + stats["losses"]
            win_rate = f"{(stats['wins']/total_matches)*100:.1f}%" if total_matches > 0 else "0.0%"
            tier = self.manager.get_character_tier(char)
            
            self.stats_tree.insert(
                '',
                'end',
                values=(char, stats["wins"], stats["losses"], win_rate, tier)
            )

    def _select_all_stages(self):
        """Enable all stages"""
        try:
            # Get all stages
            all_stages = [self.stage_tree.item(item)["values"][1] 
                         for item in self.stage_tree.get_children()]
            
            # Update settings
            self.manager.settings["enabled_stages"] = all_stages
            
            # Update display
            self._populate_stage_list()
            
            # Save if auto-save enabled
            if self.settings.get("autosave", True):
                self.save_config()
                
        except Exception as e:
            print(f"Error selecting all stages: {e}")
            traceback.print_exc()

    def _deselect_all_stages(self):
        """Disable all stages"""
        try:
            # Clear enabled stages
            self.manager.settings["enabled_stages"] = []
            
            # Update display
            self._populate_stage_list()
            
            # Save if auto-save enabled
            if self.settings.get("autosave", True):
                self.save_config()
                
        except Exception as e:
            print(f"Error deselecting all stages: {e}")
            traceback.print_exc()

    def _invert_stage_selection(self):
        """Invert the selection of stages"""
        try:
            # Get all stages and currently enabled ones
            all_stages = [self.stage_tree.item(item)["values"][1] 
                         for item in self.stage_tree.get_children()]
            enabled_stages = set(self.manager.settings.get("enabled_stages", []))
            
            # Invert selection
            new_enabled = [stage for stage in all_stages if stage not in enabled_stages]
            self.manager.settings["enabled_stages"] = new_enabled
            
            # Update display
            self._populate_stage_list()
            
            # Save if auto-save enabled
            if self.settings.get("autosave", True):
                self.save_config()
                
        except Exception as e:
            print(f"Error inverting stage selection: {e}")
            traceback.print_exc()

    def _toggle_stage_status(self, event):
        """Toggle stage enabled/disabled status"""
        try:
            # Get clicked item
            item = self.stage_tree.identify('item', event.x, event.y)
            if not item:
                return
                
            # Get current values
            values = list(self.stage_tree.item(item)['values'])
            stage_name = values[1]  # Name is in second column
            
            # Toggle enabled status
            enabled_stages = set(self.manager.settings.get("enabled_stages", []))
            if stage_name in enabled_stages:
                enabled_stages.remove(stage_name)
                values[0] = ''  # Clear checkmark
            else:
                enabled_stages.add(stage_name)
                values[0] = '✓'  # Add checkmark
            
            # Update tree and settings
            self.stage_tree.item(item, values=values)
            self.manager.settings["enabled_stages"] = list(enabled_stages)
            
            # Save settings if auto-save is enabled
            if self.settings.get("autosave", True):
                self.save_config()
                
        except Exception as e:
            print(f"Error toggling stage status: {e}")
            traceback.print_exc()

    def _start_battle(self):
        """Start a battle with selected characters and settings"""
        try:
            # Prepare battle info
            battle_info = self.manager.prepare_battle()
            if not battle_info:
                messagebox.showerror("Battle Error", "Failed to prepare battle. Check logs for details.")
                return
                
            # Store battle info for timer callback
            self.current_battle_info = battle_info
                
            # Update preview with battle info
            self._update_preview(battle_info)
            
            # Switch to preview tab
            self.notebook.select(self.tabs['Preview'])
            
            # Start Twitch betting if enabled
            twitch_betting_started = False
            if (hasattr(self, 'twitch_connected_var') and self.twitch_connected_var.get() and 
                hasattr(self, 'betting_enabled_var') and self.betting_enabled_var.get() and
                hasattr(self, 'twitch_bot') and self.twitch_bot):
                try:
                    # Get betting duration
                    duration = int(self.betting_duration_var.get())
                    
                    # Format team names
                    if battle_info['mode'] == 'single':
                        team1_name = battle_info['p1']
                        team2_name = battle_info['p2']
                    else:
                        team1_name = f"Team 1 ({len(battle_info['p1'])} fighters)"
                        team2_name = f"Team 2 ({len(battle_info['p2'])} fighters)"
                    
                    # Start betting poll
                    asyncio.run_coroutine_threadsafe(
                        self.twitch_bot.create_battle_poll(
                            "Who will win?", 
                            team1_name, 
                            team2_name,
                            duration
                        ),
                        self.twitch_bot.loop
                    )
                    twitch_betting_started = True
                    
                except Exception as e:
                    print(f"Error starting Twitch betting: {e}")
                    traceback.print_exc()
            
            # Start local betting if enabled
            if hasattr(self.manager, 'local_betting_enabled') and self.manager.local_betting_enabled:
                try:
                    # Start local betting
                    self.manager.start_local_betting()
                    
                    # Update UI
                    self._update_local_betting_ui(betting_active=True)
                    
                    # Get betting duration (use same as Twitch if available)
                    if hasattr(self, 'betting_duration_var'):
                        duration = int(self.betting_duration_var.get())
                    else:
                        duration = 30  # Default duration
                        
                except Exception as e:
                    print(f"Error starting local betting: {e}")
                    traceback.print_exc()
            
            # Start betting timer if either betting system is active
            if (twitch_betting_started or 
                (hasattr(self.manager, 'local_betting_active') and self.manager.local_betting_active)):
                # Get betting duration
                if hasattr(self, 'betting_duration_var'):
                    duration = int(self.betting_duration_var.get())
                else:
                    duration = 30  # Default duration
                
                # Start timer
                self._update_betting_timer(duration)
            else:
                # Start battle immediately if no betting
                self._start_actual_battle(battle_info)
                
        except Exception as e:
            print(f"Error starting battle: {e}")
            traceback.print_exc()
            messagebox.showerror("Battle Error", f"An error occurred: {e}")

    def _start_actual_battle(self, battle_info=None):
        """Start the actual battle after betting period (if any)"""
        try:
            # Start the battle
            battle_info = self.manager.start_battle(battle_info)
            
            # Update preview
            self._update_preview(battle_info)
            
            # Start battle monitor
            self._check_battle_result()
            
            # Log battle start
            if hasattr(self, 'battle_log'):
                timestamp = time.strftime("%H:%M:%S")
                if battle_info['mode'] == "single":
                    log_text = f"[{timestamp}] Battle Started: {battle_info['p1']} vs {battle_info['p2']} on {battle_info['stage']}\n"
                else:
                    team1 = " & ".join(battle_info['p1'])
                    team2 = " & ".join(battle_info['p2'])
                    log_text = f"[{timestamp}] Team Battle Started: {team1} vs {team2} on {battle_info['stage']}\n"
                self.battle_log.insert(tk.END, log_text)
                self.battle_log.see(tk.END)
                
        except Exception as e:
            print(f"Error starting actual battle: {e}")
            traceback.print_exc()
            self.start_battle_btn.config(state='normal')

    def _update_preview(self, battle_info):
        """Update the preview tab with current battle information"""
        try:
            if battle_info['mode'] == "single":
                # Update team displays
                self._create_team_display(self.team1_frame, [battle_info['p1']], "Team 1", "left")
                self._create_team_display(self.team2_frame, [battle_info['p2']], "Team 2", "right")
            else:
                # Update team displays for team battle
                self._create_team_display(self.team1_frame, battle_info['p1'], "Team 1", "left")
                self._create_team_display(self.team2_frame, battle_info['p2'], "Team 2", "right")
            
            # Update stage preview
            self.stage_preview.configure(image=self.placeholder_stage)
            
            # Initialize local betting UI if enabled
            if hasattr(self.manager, 'local_betting_enabled') and self.manager.local_betting_enabled:
                # Reset betting stats
                self.team1_bet_amount.config(text="0")
                self.team1_odds.config(text="Odds: 1.0x")
                self.team2_bet_amount.config(text="0")
                self.team2_odds.config(text="Odds: 1.0x")
                
                # Update user points
                username = "Player"  # Default username
                points = self.manager.local_user_points.get(username, 0)
                self.user_points_label.config(text=f"Your Points: {points}")
            
        except Exception as e:
            print(f"Error updating preview: {e}")
            traceback.print_exc()

    def _show_about(self):
        """Show about dialog"""
        about_text = """Random AI Battles
Version 1.0

A GUI application for managing MUGEN AI battles.
Features:
- Character and stage management
- Battle statistics tracking
- Twitch integration with betting system
- Automatic configuration saving

Created with Python and Tkinter."""

        messagebox.showinfo("About", about_text)

    def _toggle_continuous_battles(self):
        """Toggle continuous battles mode"""
        self.continuous_battles = self.continuous_battles_var.get()
        print(f"Continuous battles: {'enabled' if self.continuous_battles else 'disabled'}")
        
    def update_twitch_status(self, connected):
        """Update the Twitch connection status
        
        Args:
            connected (bool): Whether the Twitch bot is connected
        """
        self.twitch_connected_var.set(connected)
        status = "Connected" if connected else "Disconnected"
        print(f"Twitch status: {status}")
        
        # Update any UI elements that show Twitch status
        # This will be called from the Twitch bot thread, so we need to use after()
        # to ensure it runs on the main thread
        
        # If there's a status label in the UI, update it
        if hasattr(self, 'twitch_status_label'):
            self.twitch_status_label.config(
                text=f"Status: {status}",
                foreground="green" if connected else "red"
            )
            
        # Enable/disable Twitch-related controls based on connection status
        if hasattr(self, 'betting_enabled_var'):
            # Only allow betting if connected
            if not connected:
                self.betting_enabled_var.set(False)
                
        # Log the status change
        self.log(f"Twitch bot {status}")
    
    def _reset_local_betting_points(self):
        """Reset local betting points"""
        try:
            # Confirm reset
            if not messagebox.askyesno("Reset Points", "Are you sure you want to reset all betting points?"):
                return
                
            # Reset points
            self.manager.local_user_points = {"Player": 1000}  # Default user with 1000 points
            
            # Update UI if we're in preview tab
            if hasattr(self, 'user_points_label'):
                self.user_points_label.config(text="Your Points: 1000")
                
            messagebox.showinfo("Points Reset", "Betting points have been reset to default values.")
            
        except Exception as e:
            print(f"Error resetting betting points: {e}")
            traceback.print_exc()

    def _connect_to_twitch(self):
        """Connect to Twitch using credentials from a dialog"""
        # If already connected, disconnect
        if self.twitch_connected_var.get():
            if hasattr(self, 'twitch_bot') and self.twitch_bot:
                # Close the connection
                try:
                    self.twitch_bot.loop.create_task(self.twitch_bot.close())
                    self.twitch_bot = None
                    self.update_twitch_status(False)
                    self.twitch_connect_button.config(text="Connect to Twitch")
                except Exception as e:
                    print(f"Error disconnecting from Twitch: {e}")
                    traceback.print_exc()
            return
            
        # Show dialog to get Twitch credentials
        dialog = tk.Toplevel(self.root)
        dialog.title("Twitch Connection")
        dialog.geometry("400x200")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Create form
        ttk.Label(dialog, text="Bot Username:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        username_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=username_var, width=30).grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(dialog, text="OAuth Token:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
        token_var = tk.StringVar()
        token_entry = ttk.Entry(dialog, textvariable=token_var, width=30, show="*")
        token_entry.grid(row=1, column=1, padx=5, pady=5)
        
        ttk.Label(dialog, text="Channel:").grid(row=2, column=0, padx=5, pady=5, sticky='w')
        channel_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=channel_var, width=30).grid(row=2, column=1, padx=5, pady=5)
        
        # Add help text
        help_text = "OAuth token can be generated at https://twitchapps.com/tmi/"
        ttk.Label(dialog, text=help_text, foreground="blue").grid(row=3, column=0, columnspan=2, padx=5, pady=5)
        
        # Add buttons
        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=4, column=0, columnspan=2, padx=5, pady=10)
        
        def on_connect():
            # Get values
            username = username_var.get().strip()
            token = token_var.get().strip()
            channel = channel_var.get().strip()
            
            # Validate
            if not username or not token or not channel:
                messagebox.showerror("Error", "All fields are required")
                return
                
            # Close dialog
            dialog.destroy()
            
            # Connect to Twitch
            try:
                # Initialize the bot
                self.twitch_bot = TwitchBot(token, channel, self, username)
                
                # Start the bot in a separate thread
                threading.Thread(target=self._run_twitch_bot, daemon=True).start()
                
                # Update button text
                self.twitch_connect_button.config(text="Disconnect from Twitch")
                
                # Log the connection attempt
                self.log(f"Connecting to Twitch as {username} in channel {channel}...")
                
            except Exception as e:
                messagebox.showerror("Connection Error", f"Failed to connect to Twitch: {e}")
                print(f"Error connecting to Twitch: {e}")
                traceback.print_exc()
                self.twitch_bot = None
        
        def on_cancel():
            dialog.destroy()
            
        ttk.Button(button_frame, text="Connect", command=on_connect).pack(side='left', padx=5)
        ttk.Button(button_frame, text="Cancel", command=on_cancel).pack(side='left', padx=5)
        
        # Center the dialog
        dialog.update_idletasks()
        width = dialog.winfo_width()
        height = dialog.winfo_height()
        x = (dialog.winfo_screenwidth() // 2) - (width // 2)
        y = (dialog.winfo_screenheight() // 2) - (height // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        
    def _run_twitch_bot(self):
        """Run the Twitch bot in a separate thread"""
        try:
            # Run the bot
            self.twitch_bot.run()
        except Exception as e:
            print(f"Error running Twitch bot: {e}")
            traceback.print_exc()
            
            # Update status on the main thread
            self.root.after(0, self.update_twitch_status, False)
            self.root.after(0, lambda: self.twitch_connect_button.config(text="Connect to Twitch"))
            self.twitch_bot = None
            
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