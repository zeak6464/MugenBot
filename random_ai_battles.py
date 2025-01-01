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
            # Start MUGEN process with timeout handling
            process = subprocess.Popen(cmd_str, shell=True, cwd=str(self.mugen_path.parent))
            
            # Wait up to 5 seconds for process to start
            for _ in range(50):
                if self._check_mugen_running():
                    break
                time.sleep(0.1)
            
            if not self._check_mugen_running():
                raise RuntimeError("MUGEN process failed to start")
            
            # Record battle start time
            self.battle_start_time = time.time()
            
            # Store current battle info
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
            mugen_exes = ['mugen.exe', '3v3.exe', '4v4.exe']
            for exe in mugen_exes:
                result = subprocess.run(
                    f'tasklist /FI "IMAGENAME eq {exe}" /NH', 
                    shell=True, 
                    capture_output=True, 
                    text=True
                )
                if exe in result.stdout:
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
        p1_score, p2_score = result
        print(f"Processing battle result: P1={p1_score}, P2={p2_score}")  # Debug print
        
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
                try:
                    self.watcher_log.unlink()
                except PermissionError:
                    print("Warning: Could not delete watcher log")
            
            self.current_battle = None
            
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
            if self.watcher_log.exists():
                try:
                    self.watcher_log.unlink()
                except PermissionError:
                    print("Warning: Could not delete watcher log")
            self.current_battle = None
            
            print(f"Processed team battle result: {battle_result}")  # Debug print
            return battle_result

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
        self.root.title("MUGEN Random AI Battles")
        self.root.geometry("1024x768")
        
        # Set window icon if available
        icon_path = Path("icon.ico")
        if icon_path.exists():
            self.root.iconbitmap(str(icon_path))

        # Initialize variables after root window is created
        self.mode_var = tk.StringVar(value="single")
        self.rounds_var = tk.StringVar(value="1")
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
        
        # Add placeholder image paths
        self.placeholder_portrait = self._create_placeholder_portrait()
        self.placeholder_stage = self._create_placeholder_stage()
        
        # Initialize battle log and status label
        self.battle_log = None
        self.twitch_status_label = None
        
        # Setup GUI components
        self.load_theme()
        self.create_menu()
        self.setup_gui()
        
        self.battle_monitor = None
        self.current_theme = "light"
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
        self.root.bind('<F5>', lambda e: self._start_battle())          # Start battle
        self.root.bind('<F6>', lambda e: self.stop_battle())           # Stop battle
        self.root.bind('<F7>', lambda e: self._quick_rematch())         # Quick rematch
        self.root.bind('<F8>', lambda e: self._force_next_round())      # Force next round
        self.root.bind('<F9>', lambda e: self._reset_battle_scores())   # Reset scores
        self.root.bind('<F10>', lambda e: self._change_random_stage())  # Random stage
        self.root.bind('<F11>', lambda e: self._start_battle_after_betting())  # Force start
        self.root.bind('<F12>', lambda e: self._toggle_ai_level())      # Toggle AI level

    def _create_placeholder_portrait(self):
        """Create a placeholder portrait image"""
        size = (200, 200)
        img = Image.new('RGB', size, '#2D2D2D')
        draw = ImageDraw.Draw(img)
        
        # Draw a character silhouette
        draw.rectangle([40, 40, 160, 160], fill='#3D3D3D')
        draw.ellipse([70, 20, 130, 80], fill='#3D3D3D')  # head
        
        # Add text
        try:
            font = ImageFont.truetype("segoe ui", 14)
        except:
            font = ImageFont.load_default()
        
        draw.text((100, 170), "No Portrait", font=font, fill='#CCCCCC', anchor='ms')
        
        return ImageTk.PhotoImage(img)

    def _create_placeholder_stage(self):
        """Create a placeholder stage preview image"""
        size = (480, 270)  # 16:9 aspect ratio
        img = Image.new('RGB', size, '#2D2D2D')
        draw = ImageDraw.Draw(img)
        
        # Draw a simple stage representation
        draw.rectangle([40, 180, 440, 220], fill='#3D3D3D')  # platform
        draw.rectangle([0, 220, 480, 270], fill='#3D3D3D')   # ground
        
        # Add decorative elements
        draw.rectangle([60, 140, 100, 180], fill='#3D3D3D')  # background element
        draw.rectangle([380, 120, 420, 180], fill='#3D3D3D') # background element
        
        # Add text
        try:
            font = ImageFont.truetype("segoe ui", 20)
        except:
            font = ImageFont.load_default()
        
        draw.text((240, 135), "Stage Preview Not Available", font=font, fill='#CCCCCC', anchor='ms')
        
        return ImageTk.PhotoImage(img)

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

        # Configure styles for preview tab
        style = ttk.Style()
        style.configure("Preview.TFrame", background="black")
        style.configure("Name.TLabel", font=("Arial Black", 16, "bold"), background="black", foreground="white")
        style.configure("Stats.TLabel", font=("Arial", 12), background="black", foreground="white")
        style.configure("Dark.TFrame", background="black")

        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.main_container)
        self.notebook.pack(expand=True, fill="both")

        # Create all tabs first
        self.battle_tab = ttk.Frame(self.notebook)
        self.characters_tab = ttk.Frame(self.notebook)
        self.stages_tab = ttk.Frame(self.notebook)
        self.stats_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.preview_tab = ttk.Frame(self.notebook)

        # Add tabs to notebook
        self.notebook.add(self.battle_tab, text="Battle")
        self.notebook.add(self.characters_tab, text="Characters")
        self.notebook.add(self.stages_tab, text="Stages")
        self.notebook.add(self.stats_tab, text="Statistics")
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.preview_tab, text="Battle Preview")

        # Setup each tab
        self._setup_battle_tab()
        self._setup_characters_tab()
        self._setup_stages_tab()
        self._setup_stats_tab()
        self._setup_settings_tab()
        self._setup_preview_tab()

    def _setup_preview_tab(self):
        """Setup the battle preview tab"""
        # Configure the preview tab with black background
        self.preview_tab.configure(style="Preview.TFrame")
        style = ttk.Style()
        style.configure("Preview.TFrame", background="black")

        # Main display frame
        self.preview_display = tk.Frame(self.preview_tab, bg="black")
        self.preview_display.pack(expand=True, fill="both")

        # Create frames for fighters/teams
        self.preview_left_frame = tk.Frame(self.preview_display, bg="black", width=400)
        self.preview_left_frame.pack(side="left", fill="y", padx=20)
        self.preview_left_frame.pack_propagate(False)

        # VS display in center
        self.preview_vs_frame = tk.Frame(self.preview_display, bg="black")
        self.preview_vs_frame.pack(side="left", expand=True, fill="both")
        self.preview_vs_label = tk.Label(
            self.preview_vs_frame,
            text="VS",
            font=("Impact", 120),
            fg="white",
            bg="black"
        )
        self.preview_vs_label.pack(expand=True)

        self.preview_right_frame = tk.Frame(self.preview_display, bg="black", width=400)
        self.preview_right_frame.pack(side="right", fill="y", padx=20)
        self.preview_right_frame.pack_propagate(False)

        # Bottom frame for betting info
        self.preview_bottom = tk.Frame(self.preview_tab, bg="black")
        self.preview_bottom.pack(side="bottom", fill="x")

        # Status banner
        self.preview_status = tk.Frame(self.preview_bottom, bg="black", height=50)
        self.preview_status.pack(fill="x", pady=(0, 2))
        self.preview_status.pack_propagate(False)
        
        self.preview_status_label = tk.Label(
            self.preview_status,
            text="WAITING FOR BATTLE",
            font=("Arial Black", 32, "bold"),
            bg="black",
            fg="white"
        )
        self.preview_status_label.pack(expand=True)

        # Betting info bar
        self.preview_betting = tk.Frame(self.preview_bottom, bg="black", height=50)
        self.preview_betting.pack(fill="x")
        self.preview_betting.pack_propagate(False)

        # Team 1 total (left)
        self.preview_team1_total = tk.Label(
            self.preview_betting,
            text="0",
            font=("Arial Black", 28, "bold"),
            fg="#FF0000",
            bg="black"
        )
        self.preview_team1_total.pack(side="left", expand=True)

        # Timer (center)
        self.preview_timer = tk.Label(
            self.preview_betting,
            text="",
            font=("Arial Black", 28, "bold"),
            fg="white",
            bg="black"
        )
        self.preview_timer.pack(side="left", expand=True)

        # Team 2 total (right)
        self.preview_team2_total = tk.Label(
            self.preview_betting,
            text="0",
            font=("Arial Black", 28, "bold"),
            fg="#0000FF",
            bg="black"
        )
        self.preview_team2_total.pack(side="right", expand=True)

        # Start the betting timer
        self._update_betting_timer(self.betting_duration)

        # Update bet totals periodically
        self._update_bet_totals()

    def _update_bet_totals(self):
        """Update the bet totals display"""
        try:
            if self.twitch_bot:
                team1_total = sum(self.twitch_bot.current_bets["1"].values())
                team2_total = sum(self.twitch_bot.current_bets["2"].values())
                
                # Update preview tab totals
                if hasattr(self, 'preview_team1_total'):
                    self.preview_team1_total.config(text=f"{team1_total:,}")
                if hasattr(self, 'preview_team2_total'):
                    self.preview_team2_total.config(text=f"{team2_total:,}")
                
                # Schedule next update
                self.root.after(1000, self._update_bet_totals)
                
        except Exception as e:
            print(f"Error updating bet totals: {e}")

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

            # Close preview window before starting battle
            if hasattr(self, 'preview_window') and self.preview_window:
                try:
                    self.preview_window.destroy()
                except:
                    pass
                self.preview_window = None

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

    def stop_battle(self):
        """Stop the current battle and clean up"""
        try:
            # Stop battle monitoring
            if self.battle_monitor:
                self.root.after_cancel(self.battle_monitor)
                self.battle_monitor = None

            # Close preview window if open
            if hasattr(self, 'preview_window') and self.preview_window:
                try:
                    self.preview_window.destroy()
                except:
                    pass
                self.preview_window = None

            # End Twitch poll if active
            if self.twitch_bot and self.twitch_bot.betting_active:
                asyncio.run_coroutine_threadsafe(
                    self.twitch_bot.end_poll(),
                    self.twitch_bot.loop
                )

            # Find and terminate MUGEN process
            try:
                subprocess.run('taskkill /F /IM mugen.exe', shell=True, stderr=subprocess.DEVNULL)
                time.sleep(0.5)  # Give process time to terminate
            except:
                pass

            # Clean up MugenWatcher
            if self.manager.watcher_process:
                try:
                    self.manager.watcher_process.terminate()
                    time.sleep(0.5)  # Give process time to terminate
                    self.manager.watcher_process = None
                except:
                    pass

            # Clear all battle states
            self.manager.current_battle = None
            self.prepared_battle_info = None
            self.manager.battle_start_time = None
            
            # Reset Twitch betting state
            if self.twitch_bot:
                self.twitch_bot.betting_active = False
                self.twitch_bot.current_bets = {"1": {}, "2": {}}

            # Log battle stop
            timestamp = time.strftime("%H:%M:%S")
            if hasattr(self, 'battle_log'):
                self.battle_log.insert(tk.END, f"[{timestamp}] Battle stopped\n")
                self.battle_log.see(tk.END)

        except Exception as e:
            print(f"Failed to stop battle: {str(e)}")
            traceback.print_exc()  # Add stack trace for debugging
            # Try emergency cleanup
            self.manager.current_battle = None
            self.prepared_battle_info = None
            self.battle_monitor = None

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
            # Clear existing content
            for widget in self.preview_display.winfo_children():
                widget.destroy()

            # Create frames for fighters/teams
            left_frame = tk.Frame(self.preview_display, bg="black", width=400)
            left_frame.pack(side="left", fill="y", padx=20)
            left_frame.pack_propagate(False)

            # VS display in center
            vs_frame = tk.Frame(self.preview_display, bg="black")
            vs_frame.pack(side="left", expand=True, fill="both")
            tk.Label(
                vs_frame,
                text="VS",
                font=("Impact", 120),
                fg="white",
                bg="black"
            ).pack(expand=True)

            right_frame = tk.Frame(self.preview_display, bg="black", width=400)
            right_frame.pack(side="right", fill="y", padx=20)
            right_frame.pack_propagate(False)

            # Create fighter displays
            if battle_info['mode'] == "single":
                self._create_fighter_display(left_frame, battle_info['p1'], "RED TEAM", "left")
                self._create_fighter_display(right_frame, battle_info['p2'], "BLUE TEAM", "right")
            else:
                self._create_team_display(left_frame, battle_info['p1'], "RED TEAM", "left")
                self._create_team_display(right_frame, battle_info['p2'], "BLUE TEAM", "right")

        except Exception as e:
            print(f"Error updating preview tab: {e}")

    def _setup_battle_tab(self):
        """Setup the battle tab with all controls"""
        battle_frame = ttk.Frame(self.battle_tab)
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
                    "time": self.time_var.get(),  # Add time setting
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
        """Start the GUI with memory monitoring"""
        # Start memory monitoring
        self._monitor_memory_usage()
        # Run the main loop
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
        total_battles = 0
        for stats in self.manager.character_stats.values():
            if isinstance(stats, dict):
                total_battles += stats.get("wins", 0) + stats.get("losses", 0)
            elif isinstance(stats, list) and len(stats) >= 2:
                total_battles += stats[0] + stats[1]  # Assuming [wins, losses]
        self.summary_labels["total_battles"].config(text=str(total_battles))
        
        # Find top winner
        if self.manager.character_stats:
            try:
                def get_wins(stats):
                    if isinstance(stats, dict):
                        return stats.get("wins", 0)
                    elif isinstance(stats, list) and len(stats) >= 1:
                        return stats[0]  # Assuming [wins, losses]
                    return 0

                top_winner = max(
                    self.manager.character_stats.items(),
                    key=lambda x: get_wins(x[1])
                )[0]
                self.summary_labels["top_winner"].config(text=top_winner)
            except Exception as e:
                print(f"Error finding top winner: {e}")
                self.summary_labels["top_winner"].config(text="N/A")
        
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
        
        # Add character statistics
        for char in sorted(self.manager.characters):
            stats = self.manager.character_stats.get(char, {})
            
            # Handle both dict and list formats
            if isinstance(stats, dict):
                wins = stats.get("wins", 0)
                losses = stats.get("losses", 0)
            elif isinstance(stats, list) and len(stats) >= 2:
                wins = stats[0]
                losses = stats[1]
            else:
                wins = 0
                losses = 0
            
            total_matches = wins + losses
            win_rate = f"{(wins/total_matches)*100:.1f}%" if total_matches > 0 else "N/A"
            tier = self.manager.get_character_tier(char)
            
            # TODO: Implement most defeated/lost to tracking
            most_defeated = "N/A"
            most_lost_to = "N/A"
            
            self.stats_tree.insert("", "end", values=(
                char,
                wins,
                losses,
                win_rate,
                tier,
                most_defeated,
                most_lost_to
            ))
        
        # Update average duration
        avg_duration = self.manager.get_average_battle_duration()
        self.summary_labels["avg_duration"].config(text=avg_duration)
        
        # Update stage statistics
        for item in self.stage_tree.get_children():
            stage = self.stage_tree.item(item)["values"][0]
            stats = self.manager.stage_stats.get(stage, {})
            
            self.stage_tree.set(item, "Times Used", stats.get("times_used", 0))
            self.stage_tree.set(item, "Last Used", stats.get("last_used", "Never"))

    def _sort_stats(self, column):
        """Sort statistics by the selected column"""
        # Store the current sort column and order
        if not hasattr(self, '_sort_column'):
            self._sort_column = None
            self._sort_reverse = False
        
        # Toggle sort order if clicking the same column
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False

        # Get all items with their values
        items = [(self.stats_tree.set(item, column), item) for item in self.stats_tree.get_children("")]
        
        # Convert values for proper sorting
        def convert_value(value):
            try:
                # Handle percentage values
                if isinstance(value, str) and value.endswith('%'):
                    return float(value.rstrip('%'))
                # Handle numeric values
                return float(value) if value.replace('.', '').isdigit() else value
            except:
                return value
                
        # Sort items
        items.sort(key=lambda x: convert_value(x[0]), reverse=self._sort_reverse)
        
        # Rearrange items in sorted positions
        for index, (_, item) in enumerate(items):
            self.stats_tree.move(item, "", index)
        
        # Update the heading text to show sort direction
        for col in self.stats_tree["columns"]:
            if col == column:
                direction = " ▼" if self._sort_reverse else " ▲"
                self.stats_tree.heading(col, text=col + direction)
            else:
                # Remove sort indicator from other columns
                self.stats_tree.heading(col, text=col.rstrip(" ▼▲"))

    def _start_battle(self):
        """Start a new battle"""
        try:
            # Don't start a new battle if one is already in progress
            if self.battle_monitor:
                print("Battle already in progress")
                return

            # Clear any existing battle state
            self.stop_battle()
            time.sleep(0.5)  # Give time for cleanup

            # Prepare the battle info
            try:
                self.prepared_battle_info = self.manager.prepare_battle()
                print("Prepared battle info:", self.prepared_battle_info)
            except Exception as e:
                print(f"Error preparing battle: {e}")
                traceback.print_exc()
                return

            # Show battle preview and start betting period
            self.show_battle_preview(self.prepared_battle_info)
            
            # If Twitch bot is connected, create poll
            if self.twitch_bot and self.twitch_bot.connected:
                if self.prepared_battle_info['mode'] == "single":
                    title = f"{self.prepared_battle_info['p1']} vs {self.prepared_battle_info['p2']}"
                    option1 = self.prepared_battle_info['p1']
                    option2 = self.prepared_battle_info['p2']
                else:
                    title = "Team Battle"
                    option1 = " & ".join(self.prepared_battle_info['p1'])
                    option2 = " & ".join(self.prepared_battle_info['p2'])
                
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.twitch_bot.create_battle_poll(title, option1, option2, self.betting_duration),
                        self.twitch_bot.loop
                    )
                    # Start the betting timer
                    self._update_betting_timer(self.betting_duration)
                except Exception as e:
                    print(f"Error creating Twitch poll: {e}")
                    traceback.print_exc()
                    # Start battle without betting if poll creation fails
                    self._start_actual_battle()
            else:
                # If no Twitch bot, start battle immediately
                self._start_actual_battle()

        except Exception as e:
            print(f"Failed to start battle: {str(e)}")
            traceback.print_exc()
            self.battle_monitor = None  # Reset battle monitor on error
            # Try to clean up
            self.stop_battle()

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

            # Close preview window before starting battle
            if hasattr(self, 'preview_window') and self.preview_window:
                try:
                    self.preview_window.destroy()
                except:
                    pass
                self.preview_window = None

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

    def stop_battle(self):
        """Stop the current battle and clean up"""
        try:
            # Stop battle monitoring
            if self.battle_monitor:
                self.root.after_cancel(self.battle_monitor)
                self.battle_monitor = None

            # Close preview window if open
            if hasattr(self, 'preview_window') and self.preview_window:
                try:
                    self.preview_window.destroy()
                except:
                    pass
                self.preview_window = None

            # End Twitch poll if active
            if self.twitch_bot and self.twitch_bot.betting_active:
                asyncio.run_coroutine_threadsafe(
                    self.twitch_bot.end_poll(),
                    self.twitch_bot.loop
                )

            # Find and terminate MUGEN process
            try:
                subprocess.run('taskkill /F /IM mugen.exe', shell=True, stderr=subprocess.DEVNULL)
                time.sleep(0.5)  # Give process time to terminate
            except:
                pass

            # Clean up MugenWatcher
            if self.manager.watcher_process:
                try:
                    self.manager.watcher_process.terminate()
                    time.sleep(0.5)  # Give process time to terminate
                    self.manager.watcher_process = None
                except:
                    pass

            # Clear all battle states
            self.manager.current_battle = None
            self.prepared_battle_info = None
            self.manager.battle_start_time = None
            
            # Reset Twitch betting state
            if self.twitch_bot:
                self.twitch_bot.betting_active = False
                self.twitch_bot.current_bets = {"1": {}, "2": {}}

            # Log battle stop
            timestamp = time.strftime("%H:%M:%S")
            if hasattr(self, 'battle_log'):
                self.battle_log.insert(tk.END, f"[{timestamp}] Battle stopped\n")
                self.battle_log.see(tk.END)

        except Exception as e:
            print(f"Failed to stop battle: {str(e)}")
            traceback.print_exc()  # Add stack trace for debugging
            # Try emergency cleanup
            self.manager.current_battle = None
            self.prepared_battle_info = None
            self.battle_monitor = None

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

    def _quick_rematch(self):
        """Quickly restart the last battle with same fighters"""
        if hasattr(self.manager, 'current_battle') and self.manager.current_battle:
            self._stop_battle()
            time.sleep(0.5)  # Brief pause
            self.manager.start_battle()
            self.battle_monitor = self.root.after(1000, self._check_battle_result)

    def _force_next_round(self):
        """Force the current battle to proceed to next round"""
        try:
            subprocess.run('taskkill /F /IM mugen.exe', shell=True)
            time.sleep(0.5)
            self.manager.start_battle()
            self.battle_monitor = self.root.after(1000, self._check_battle_result)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to force next round: {str(e)}")

    def _reset_battle_scores(self):
        """Reset the current battle scores"""
        if hasattr(self.manager, 'current_battle') and self.manager.current_battle:
            self._stop_battle()
            time.sleep(0.5)
            self.manager.start_battle()
            self.battle_monitor = self.root.after(1000, self._check_battle_result)

    def _change_random_stage(self):
        """Change to a random stage while keeping same fighters"""
        if hasattr(self.manager, 'current_battle') and self.manager.current_battle:
            current_battle = self.manager.current_battle.copy()
            current_battle['stage'] = random.choice(list(self.manager.settings['enabled_stages']))
            self._stop_battle()
            time.sleep(0.5)
            self.manager.start_battle()
            self.battle_monitor = self.root.after(1000, self._check_battle_result)

    def _toggle_ai_level(self):
        """Toggle AI difficulty level"""
        try:
            # You might need to modify this based on how MUGEN handles AI levels
            current_level = self.manager.settings.get('ai_level', 4)
            new_level = 8 if current_level == 4 else 4  # Toggle between 4 and 8
            self.manager.settings['ai_level'] = new_level
            messagebox.showinfo("AI Level", f"AI Level set to {new_level}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to toggle AI level: {str(e)}")

    def _start_battle_after_betting(self):
        """Force start the battle immediately after betting"""
        if hasattr(self, 'preview_window') and self.preview_window.winfo_exists():
            self.preview_window.destroy()
        if self.twitch_bot:
            asyncio.run_coroutine_threadsafe(
                self.twitch_bot.end_poll(),
                self.twitch_bot.loop
            )
        self.manager.start_battle(self.manager.current_battle)
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
            # First disconnect existing bot if any
            if self.twitch_bot:
                self.update_twitch_status(False)
                self.twitch_bot = None
            
            self.betting_duration = self.betting_duration_var.get()
            self.setup_twitch_bot(token, channel, bot_name)
            # Don't show success message here - wait for event_ready
        except Exception as e:
            messagebox.showerror("Error", f"Failed to connect to Twitch: {str(e)}")
            self.update_twitch_status(False)

    def setup_twitch_bot(self, token, channel, bot_name):
        """Initialize Twitch bot with credentials and custom name"""
        try:
            # Format token if needed
            if not token.startswith('oauth:'):
                token = 'oauth:' + token
                
            # Create the bot instance
            self.twitch_bot = TwitchBot(token, channel, self, bot_name)
            
            # Start the bot in a separate thread
            threading.Thread(target=self._run_twitch_bot, daemon=True).start()
            
        except Exception as e:
            print(f"Error setting up Twitch bot: {e}")
            traceback.print_exc()
            self.update_twitch_status(False)

    def _run_twitch_bot(self):
        """Run Twitch bot in a separate thread"""
        try:
            self.twitch_bot.run()
        except Exception as e:
            print(f"Error running Twitch bot: {e}")
            traceback.print_exc()
            self.root.after(0, self.update_twitch_status, False)

    def _check_battle_result(self):
        """Check battle result and handle updates with automatic recovery"""
        try:
            # Check if MUGEN is still running
            mugen_running = self.manager._check_mugen_running()
            if not mugen_running:
                print("MUGEN process ended, checking for final result")
                
                # Give the watcher a moment to write the final result
                time.sleep(0.5)
                
                # Try to get the final result
                result = self.manager.check_battle_result()
                if result:
                    print(f"Final battle result received: {result}")
                    self._handle_battle_result(result)
                else:
                    print("No final result available")
                    # Try to recover from failed battle
                    self._handle_failed_battle()
                    # If continuous mode is on, prepare next battle
                    if self.continuous_var.get():
                        self.root.after(2000, self._prepare_next_battle)
                
                self.battle_monitor = None
                return
            
            # Check for result while battle is running
            result = self.manager.check_battle_result()
            if result:
                print(f"Battle result received during battle: {result}")
                self._handle_battle_result(result)
                self.battle_monitor = None
                return
            
            # Continue monitoring if no result yet
            if self.battle_monitor is not None:
                self.battle_monitor = self.root.after(1000, self._check_battle_result)
                
        except Exception as e:
            print(f"Error checking battle result: {e}")
            traceback.print_exc()
            # Try to recover from error
            self._handle_failed_battle()
            # If continuous mode is on, prepare next battle
            if hasattr(self, 'continuous_var') and self.continuous_var.get():
                self.root.after(2000, self._prepare_next_battle)
            self.battle_monitor = None

    def _handle_failed_battle(self):
        """Handle and recover from failed battles"""
        try:
            timestamp = time.strftime("%H:%M:%S")
            # Check if battle_log exists and create it if it doesn't
            if not hasattr(self, 'battle_log') or not self.battle_log:
                print("Warning: battle_log not initialized, creating it now")
                log_frame = ttk.LabelFrame(self.battle_tab, text="Battle Log")
                log_frame.pack(expand=True, fill="both", padx=10, pady=5)
                self.battle_log = tk.Text(log_frame, height=10, wrap=tk.WORD)
                self.battle_log.pack(side="left", expand=True, fill="both", padx=5, pady=5)
                log_scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.battle_log.yview)
                log_scrollbar.pack(side="right", fill="y")
                self.battle_log.configure(yscrollcommand=log_scrollbar.set)
            
            self.battle_log.insert(tk.END, f"[{timestamp}] Battle failed - attempting recovery\n")
            self.battle_log.see(tk.END)
            
            # Stop any existing processes
            self.stop_battle()
            
            # Clear battle states
            self.manager.current_battle = None
            self.prepared_battle_info = None
            self.battle_monitor = None
            
            # Reset Twitch betting if active
            if self.twitch_bot and self.twitch_bot.betting_active:
                asyncio.run_coroutine_threadsafe(
                    self.twitch_bot.end_poll(),
                    self.twitch_bot.loop
                )
            
            # If in continuous mode, try to start next battle
            if self.continuous_var.get():
                self.battle_log.insert(tk.END, f"[{timestamp}] Attempting to start next battle\n")
                self.battle_log.see(tk.END)
                self.root.after(2000, self._prepare_next_battle)  # Wait 2 seconds before next battle
                
        except Exception as e:
            print(f"Error in handle_failed_battle: {e}")
            traceback.print_exc()

    def _prepare_next_battle(self):
        """Prepare and set up the next battle with error handling"""
        try:
            # Check if we already have a prepared battle
            if hasattr(self, 'prepared_battle_info') and self.prepared_battle_info:
                print("Battle already prepared, skipping preparation")
                return

            # Verify manager state
            if not self.manager or not hasattr(self.manager, 'prepare_battle'):
                raise RuntimeError("Battle manager not properly initialized")

            # Verify we have enabled characters and stages
            if not self.manager.settings["enabled_characters"]:
                raise ValueError("No characters are enabled")
            if not self.manager.settings["enabled_stages"]:
                raise ValueError("No stages are enabled")

            # Prepare next battle
            self.prepared_battle_info = self.manager.prepare_battle()
            print("Preparing next battle:", self.prepared_battle_info)
            
            if self.twitch_bot and self.twitch_bot.connected:
                # Show preview and start betting for next battle
                self.show_battle_preview(self.prepared_battle_info)
                
                if self.prepared_battle_info['mode'] == "single":
                    title = f"{self.prepared_battle_info['p1']} vs {self.prepared_battle_info['p2']}"
                    option1 = self.prepared_battle_info['p1']
                    option2 = self.prepared_battle_info['p2']
                else:
                    title = "Team Battle"
                    option1 = " & ".join(self.prepared_battle_info['p1'])
                    option2 = " & ".join(self.prepared_battle_info['p2'])
                
                # Create poll with retry mechanism
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        poll_created = asyncio.run_coroutine_threadsafe(
                            self.twitch_bot.create_battle_poll(title, option1, option2, self.betting_duration),
                            self.twitch_bot.loop
                        ).result(timeout=5)
                        
                        if poll_created:
                            # Start betting timer
                            self._update_betting_timer(self.betting_duration)
                            break
                        elif attempt < max_retries - 1:
                            print(f"Failed to create poll, retrying ({attempt + 1}/{max_retries})")
                            time.sleep(1)
                        else:
                            print("Failed to create poll after all retries")
                            self._start_actual_battle()  # Start battle without betting
                    except Exception as e:
                        print(f"Error creating poll (attempt {attempt + 1}): {e}")
                        if attempt == max_retries - 1:
                            self._start_actual_battle()  # Start battle without betting
            else:
                # Start next battle immediately
                self._start_actual_battle()
                
        except Exception as e:
            print(f"Error preparing next battle: {e}")
            traceback.print_exc()
            # Log error
            timestamp = time.strftime("%H:%M:%S")
            if hasattr(self, 'battle_log'):
                self.battle_log.insert(tk.END, f"[{timestamp}] Failed to prepare next battle: {str(e)}\n")
                self.battle_log.see(tk.END)
            # Clear the prepared battle info in case of error
            self.prepared_battle_info = None
            # Try again in 5 seconds if continuous mode is on
            if self.continuous_var.get():
                self.root.after(5000, self._prepare_next_battle)

    def show_battle_preview(self, battle_info):
        """Show battle preview in tab"""
        try:
            # Update the preview tab
            self._update_preview_tab(battle_info)
            
        except Exception as e:
            print(f"Error updating preview tab: {e}")
            traceback.print_exc()

    def _handle_battle_result(self, result):
        """Handle the battle result and prepare next battle if needed"""
        try:
            # Update Twitch poll and handle payouts
            if self.twitch_bot and hasattr(self, 'prepared_battle_info'):
                winner = result.get('winner')
                if winner:
                    print(f"Processing winner: {winner}")
                    # Use the bot's event loop to handle the battle result
                    future = asyncio.run_coroutine_threadsafe(
                        self.twitch_bot.handle_battle_result(
                            winner,
                            self.prepared_battle_info['p1'],
                            self.prepared_battle_info['p2']
                        ),
                        self.twitch_bot.loop
                    )
                    # Wait for the result to be processed with timeout
                    try:
                        future.result(timeout=10)  # Wait up to 10 seconds
                    except asyncio.TimeoutError:
                        print("Timeout while processing battle result")
                    except Exception as e:
                        print(f"Error processing battle result: {e}")
                        traceback.print_exc()
            
            # Format and display result
            timestamp = time.strftime("%H:%M:%S")
            if result.get("mode") == "single":
                result_text = (
                    f"[{timestamp}] Battle Result:\n"
                    f"{result['winner']} defeated {result['loser']}\n"
                    f"Score: {result['p1_score']}-{result['p2_score']}\n"
                )
            else:
                winner_team = " & ".join(result['winner'])
                loser_team = " & ".join(result['loser'])
                result_text = (
                    f"[{timestamp}] Battle Result:\n"
                    f"Team {winner_team} defeated Team {loser_team}\n"
                    f"Score: {result['p1_score']}-{result['p2_score']}\n"
                )
            
            # Log result to battle log
            if hasattr(self, 'battle_log'):
                self.battle_log.insert(tk.END, result_text)
                self.battle_log.see(tk.END)
            
            # Update statistics view if available
            if hasattr(self, '_update_stats_view'):
                self._update_stats_view()
            
            # Clear current battle state
            self.prepared_battle_info = None
            self.manager.current_battle = None
            
            # If continuous mode is enabled, prepare next battle
            if hasattr(self, 'continuous_var') and self.continuous_var.get():
                # Add a small delay before preparing next battle
                self.root.after(1000, self._prepare_next_battle)
            else:
                self.battle_monitor = None
                
        except Exception as e:
            print(f"Error handling battle result: {e}")
            traceback.print_exc()
            # Try to recover
            self._handle_failed_battle()
            # Clear states
            self.prepared_battle_info = None
            self.manager.current_battle = None
            self.battle_monitor = None

    def _monitor_memory_usage(self):
        """Monitor memory usage and clean up if necessary"""
        try:
            process = psutil.Process()
            memory_usage = process.memory_info().rss / 1024 / 1024  # Convert to MB
            
            # Log high memory usage
            if memory_usage > 500:  # Warning at 500MB
                print(f"Warning: High memory usage detected: {memory_usage:.2f}MB")
                
            # Force cleanup at critical levels
            if memory_usage > 1000:  # Critical at 1GB
                print(f"Critical: Memory usage too high ({memory_usage:.2f}MB). Initiating cleanup...")
                self.stop_battle()  # Stop current battle
                self._force_cleanup()  # Additional cleanup
                
            # Schedule next check
            self.root.after(30000, self._monitor_memory_usage)  # Check every 30 seconds
            
        except Exception as e:
            print(f"Error monitoring memory: {e}")
            traceback.print_exc()
    
    def _force_cleanup(self):
        """Force cleanup of resources when memory usage is critical"""
        try:
            # Clear GUI elements
            if hasattr(self, 'battle_log'):
                self.battle_log.delete('1.0', tk.END)
            
            # Reset all battle states
            self.manager.current_battle = None
            self.prepared_battle_info = None
            self.battle_monitor = None
            
            # Force garbage collection
            import gc
            gc.collect()
            
            # Kill any hanging processes
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if proc.name().lower() in ['mugen.exe', 'mugenwatcher.exe']:
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
        except Exception as e:
            print(f"Error during force cleanup: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    manager = MugenBattleManager()
    gui = BattleGUI(manager)
    gui.run() 