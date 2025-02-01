import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Dict, Optional
import random
from tournament import MugenTournament, create_tournament
from enum import Enum

class TournamentFormat(Enum):
    SINGLE_ELIMINATION = "Single Elimination"
    DOUBLE_ELIMINATION = "Double Elimination"
    ROUND_ROBIN = "Round Robin"
    SWISS = "Swiss System"
    GROUP_STAGE = "Group Stage + Knockout"

class TournamentFrame(ttk.Frame):
    def __init__(self, parent, battle_manager):
        super().__init__(parent)
        self.manager = battle_manager
        self.tournament = None
        self.selected_chars = set()
        self.selected_stages = set()
        self.tournament_size = tk.IntVar(value=8)
        
        self.setup_gui()

    def setup_gui(self):
        # Main container with left and right panes
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(expand=True, fill="both", padx=5, pady=5)

        # Left side - Tournament setup
        setup_frame = ttk.LabelFrame(paned, text="Tournament Setup")
        paned.add(setup_frame, weight=1)

        # Tournament size selection
        size_frame = ttk.Frame(setup_frame)
        size_frame.pack(fill="x", padx=5, pady=5)
        ttk.Label(size_frame, text="Tournament Size:").pack(side="left")
        sizes = [4, 8, 16, 32]
        size_combo = ttk.Combobox(size_frame, values=sizes, 
                                 textvariable=self.tournament_size, 
                                 state="readonly", width=10)
        size_combo.pack(side="left", padx=5)

        # Character selection
        char_frame = ttk.LabelFrame(setup_frame, text="Select Characters")
        char_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Character list with checkboxes
        self.char_tree = ttk.Treeview(char_frame, columns=("Character", "Tier", "Selected"),
                                    show="headings", selectmode="browse")
        self.char_tree.heading("Character", text="Character")
        self.char_tree.heading("Tier", text="Tier")
        self.char_tree.heading("Selected", text="Selected")
        self.char_tree.column("Character", width=150)
        self.char_tree.column("Tier", width=50)
        self.char_tree.column("Selected", width=70)
        
        char_scroll = ttk.Scrollbar(char_frame, orient="vertical", 
                                  command=self.char_tree.yview)
        self.char_tree.configure(yscrollcommand=char_scroll.set)
        
        self.char_tree.pack(side="left", fill="both", expand=True)
        char_scroll.pack(side="right", fill="y")
        
        # Character selection buttons
        char_btn_frame = ttk.Frame(setup_frame)
        char_btn_frame.pack(fill="x", padx=5, pady=5)
        ttk.Button(char_btn_frame, text="Random Select", 
                  command=self._random_select).pack(side="left", padx=2)
        ttk.Button(char_btn_frame, text="Clear Selection", 
                  command=self._clear_selection).pack(side="left", padx=2)

        # Stage selection
        stage_frame = ttk.LabelFrame(setup_frame, text="Select Stages")
        stage_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Stage list with checkboxes
        self.stage_tree = ttk.Treeview(stage_frame, columns=("Stage", "Selected"),
                                     show="headings", selectmode="browse")
        self.stage_tree.heading("Stage", text="Stage")
        self.stage_tree.heading("Selected", text="Selected")
        self.stage_tree.column("Stage", width=200)
        self.stage_tree.column("Selected", width=70)
        
        stage_scroll = ttk.Scrollbar(stage_frame, orient="vertical", 
                                   command=self.stage_tree.yview)
        self.stage_tree.configure(yscrollcommand=stage_scroll.set)
        
        self.stage_tree.pack(side="left", fill="both", expand=True)
        stage_scroll.pack(side="right", fill="y")

        # Tournament controls
        control_frame = ttk.Frame(setup_frame)
        control_frame.pack(fill="x", padx=5, pady=5)
        
        ttk.Button(control_frame, text="Start Tournament", 
                  command=self.start_tournament).pack(side="left", padx=2)
        ttk.Button(control_frame, text="Stop Tournament", 
                  command=self.stop_tournament).pack(side="left", padx=2)

        # Simplified tournament format section
        format_frame = ttk.LabelFrame(setup_frame, text="Tournament Format")
        format_frame.pack(fill="x", padx=5, pady=5)
        ttk.Label(format_frame, text="Single Elimination Tournament").pack(anchor="w", padx=5)

        # Right side - Tournament bracket display
        bracket_frame = ttk.LabelFrame(paned, text="Tournament Bracket")
        paned.add(bracket_frame, weight=2)

        # Bracket display
        self.bracket_text = tk.Text(bracket_frame, wrap=tk.WORD, width=50)
        bracket_scroll = ttk.Scrollbar(bracket_frame, orient="vertical",
                                     command=self.bracket_text.yview)
        self.bracket_text.configure(yscrollcommand=bracket_scroll.set)
        
        self.bracket_text.pack(side="left", fill="both", expand=True)
        bracket_scroll.pack(side="right", fill="y")

        # Match status
        self.status_label = ttk.Label(self, text="No tournament in progress")
        self.status_label.pack(fill="x", padx=5, pady=5)

        # Populate lists
        self._populate_characters()
        self._populate_stages()

        # Bind events
        self.char_tree.bind("<Double-1>", self._toggle_char_selection)
        self.stage_tree.bind("<Double-1>", self._toggle_stage_selection)

    def _populate_characters(self):
        """Populate character list with enabled characters"""
        for char in sorted(self.manager.characters):
            tier = self.manager.get_character_tier(char)
            self.char_tree.insert("", "end", values=(char, tier, ""))

    def _populate_stages(self):
        """Populate stage list with enabled stages"""
        for stage in sorted(self.manager.stages):
            self.stage_tree.insert("", "end", values=(stage, ""))

    def _toggle_char_selection(self, event):
        """Toggle character selection on double-click"""
        item = self.char_tree.selection()[0]
        char = self.char_tree.item(item)["values"][0]
        
        if char in self.selected_chars:
            self.selected_chars.remove(char)
            self.char_tree.set(item, "Selected", "")
        else:
            self.selected_chars.add(char)
            self.char_tree.set(item, "Selected", "✓")

    def _toggle_stage_selection(self, event):
        """Toggle stage selection on double-click"""
        item = self.stage_tree.selection()[0]
        stage = self.stage_tree.item(item)["values"][0]
        
        if stage in self.selected_stages:
            self.selected_stages.remove(stage)
            self.stage_tree.set(item, "Selected", "")
        else:
            self.selected_stages.add(stage)
            self.stage_tree.set(item, "Selected", "✓")

    def _random_select(self):
        """Randomly select characters for tournament"""
        size = self.tournament_size.get()
        enabled_chars = list(self.manager.settings["enabled_characters"])
        
        if len(enabled_chars) < size:
            messagebox.showerror("Error", 
                               f"Not enough enabled characters for {size}-player tournament")
            return
            
        self.selected_chars = set(random.sample(enabled_chars, size))
        self._update_selection_display()

    def _clear_selection(self):
        """Clear character and stage selections"""
        self.selected_chars.clear()
        self.selected_stages.clear()
        self._update_selection_display()

    def _update_selection_display(self):
        """Update the display of selected characters and stages"""
        for item in self.char_tree.get_children():
            char = self.char_tree.item(item)["values"][0]
            self.char_tree.set(item, "Selected", 
                             "✓" if char in self.selected_chars else "")
            
        for item in self.stage_tree.get_children():
            stage = self.stage_tree.item(item)["values"][0]
            self.stage_tree.set(item, "Selected",
                              "✓" if stage in self.selected_stages else "")

    def start_tournament(self):
        """Start a new tournament"""
        try:
            # Validate selections
            size = self.tournament_size.get()
            if len(self.selected_chars) < size:
                messagebox.showerror("Error", f"Please select at least {size} characters")
                return
            
            if not self.selected_stages:
                messagebox.showerror("Error", "Please select at least one stage")
                return
            
            # Get selected players and limit to tournament size
            selected_players = list(self.selected_chars)[:size]
            
            # Create new tournament
            self.tournament = MugenTournament(
                self.manager,
                selected_players,
                list(self.selected_stages)
            )
            
            # Update display
            self.bracket_text.delete('1.0', tk.END)
            self.bracket_text.insert('1.0', self.tournament.get_bracket_display())
            self.status_label.config(text="Tournament started")
            
            # Start monitoring
            self._monitor_tournament()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start tournament: {str(e)}")
            import traceback
            traceback.print_exc()

    def stop_tournament(self):
        """Stop the current tournament"""
        if self.tournament:
            if messagebox.askyesno("Confirm", "Stop current tournament?"):
                self.tournament = None
                self.status_label.config(text="Tournament stopped")

    def _monitor_tournament(self):
        """Monitor tournament progress and update display"""
        if not self.tournament:
            return
            
        if self.tournament.is_complete():
            winner = self.tournament.get_winner()
            self.status_label.config(text=f"Tournament Complete! Winner: {winner}")
            self.bracket_text.delete('1.0', tk.END)
            self.bracket_text.insert('1.0', self.tournament.get_bracket_display())
            return
            
        # Start next match if needed
        if not self.tournament.current_battle:
            match_info = self.tournament.start_next_match()
            if match_info:
                self.status_label.config(
                    text=f"Match in progress: {match_info['p1']} vs {match_info['p2']}"
                )
        
        # Check current match result
        result = self.tournament.check_match_result()
        if result:
            self.bracket_text.delete('1.0', tk.END)
            self.bracket_text.insert('1.0', self.tournament.get_bracket_display())
            
        # Continue monitoring
        self.after(1000, self._monitor_tournament)

if __name__ == "__main__":
    # Create test window
    root = tk.Tk()
    root.title("Tournament Test")
    
    # Import and create test battle manager
    try:
        from random_ai_battles import MugenBattleManager
        manager = MugenBattleManager()
        
        # Create and pack tournament frame
        tournament_frame = TournamentFrame(root, manager)
        tournament_frame.pack(expand=True, fill="both", padx=10, pady=10)
        
        # Start GUI
        root.mainloop()
    except Exception as e:
        print(f"Error starting tournament GUI: {e}")
        import traceback
        traceback.print_exc() 