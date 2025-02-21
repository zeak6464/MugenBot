import random
import time
from pathlib import Path
from typing import List, Dict, Optional
from enum import Enum
import math

class TournamentFormat(Enum):
    SINGLE_ELIMINATION = "Single Elimination"

class MugenTournament:
    def __init__(self, battle_manager, players: List[str]):
        """Initialize tournament with battle manager and players"""
        self.manager = battle_manager
        self.players = players
        self.format = TournamentFormat.SINGLE_ELIMINATION
        
        # Tournament state
        self.current_round = 0
        self.current_match = 0
        self.current_battle = None
        self.matches = []
        self.results = {}
        
        # Set up the tournament bracket
        self._setup_bracket()
    
    def _setup_bracket(self):
        """Setup the tournament bracket structure"""
        num_players = len(self.players)
        num_rounds = math.ceil(math.log2(num_players))
        total_slots = 2 ** num_rounds
        
        # Initialize matches list
        self.matches = []
        
        # First round setup
        first_round = []
        byes = total_slots - num_players
        
        # Shuffle players
        random.shuffle(self.players)
        
        # Create first round matches
        for i in range(0, num_players, 2):
            if i + 1 < num_players:
                # Regular match
                first_round.append({
                    'p1': self.players[i],
                    'p2': self.players[i + 1],
                    'winner': None,
                    'round': 0
                })
            else:
                # Bye match
                first_round.append({
                    'p1': self.players[i],
                    'p2': None,
                    'winner': self.players[i],  # Automatic win
                    'round': 0
                })
        
        # Add remaining byes
        while len(first_round) < total_slots // 2:
            first_round.append({
                'p1': None,
                'p2': None,
                'winner': None,
                'round': 0
            })
        
        self.matches.append(first_round)
        
        # Setup subsequent rounds
        for r in range(1, num_rounds):
            round_matches = []
            prev_round = self.matches[r - 1]
            
            for i in range(0, len(prev_round), 2):
                round_matches.append({
                    'p1': None,  # Will be filled by winners
                    'p2': None,
                    'winner': None,
                    'round': r
                })
            
            self.matches.append(round_matches)
    
    def get_bracket_display(self) -> str:
        """Get a text representation of the tournament bracket"""
        display = []
        
        # Show tournament type header
        display.append(f"Tournament Type: {self.format.value}")
        display.append("-" * 40)
        
        # Show each round
        for round_num, round_matches in enumerate(self.matches):
            display.append(f"\nRound {round_num + 1}:")
            display.append("-" * 20)
            
            for match_num, match in enumerate(round_matches, 1):
                p1 = match['p1'] or "BYE"
                p2 = match['p2'] or "BYE"
                winner = match['winner'] or "?"
                
                display.append(f"Match {match_num}: {p1} vs {p2}")
                if winner != "?":
                    display.append(f"Winner: {winner}")
                display.append("")
        
        return "\n".join(display)
    
    def is_complete(self) -> bool:
        """Check if tournament is complete"""
        if not self.matches:
            return False
        final_match = self.matches[-1][0]
        return final_match['winner'] is not None
    
    def get_winner(self) -> Optional[str]:
        """Get the tournament winner if tournament is complete"""
        if self.is_complete():
            return self.matches[-1][0]['winner']
        return None
    
    def start_next_match(self) -> Optional[Dict]:
        """Start the next match in the tournament"""
        if self.is_complete():
            return None
            
        # Find next unplayed match
        for round_num, round_matches in enumerate(self.matches):
            for match_num, match in enumerate(round_matches):
                if match['winner'] is None and match['p1'] is not None and match['p2'] is not None:
                    # Found next match to play
                    
                    # Select random stage from enabled stages
                    enabled_stages = list(self.manager.settings.get("enabled_stages", []))
                    if not enabled_stages:
                        raise ValueError("No stages are enabled for tournament play!")
                    selected_stage = random.choice(enabled_stages)
                    
                    # Clean up character paths - remove any 'chars/' prefix if present
                    p1_char = match['p1'].replace('chars/', '') if match['p1'].startswith('chars/') else match['p1']
                    p2_char = match['p2'].replace('chars/', '') if match['p2'].startswith('chars/') else match['p2']
                    
                    # Verify character paths exist
                    p1_path = Path(f"chars/{p1_char}/{p1_char}.def")
                    p2_path = Path(f"chars/{p2_char}/{p2_char}.def")
                    
                    if not p1_path.exists():
                        raise ValueError(f"Character not found: {p1_char}")
                    if not p2_path.exists():
                        raise ValueError(f"Character not found: {p2_char}")
                    
                    battle_info = {
                        'mode': "single",
                        'p1': p1_char,
                        'p2': p2_char,
                        'stage': selected_stage,
                        'tournament_match': True,
                        'round': round_num,
                        'match': match_num
                    }
                    
                    # Start the battle
                    self.current_battle = self.manager.start_battle(battle_info)
                    return battle_info
                    
        return None
    
    def check_match_result(self) -> Optional[Dict]:
        """Check the result of the current match"""
        if not self.current_battle:
            return None
            
        result = self.manager.check_battle_result()
        if not result:
            return None
            
        # Process match result
        winner = result['winner']
        current_round = self.matches[self.current_battle['round']]
        current_match = current_round[self.current_battle['match']]
        
        # Update match result
        current_match['winner'] = winner
        
        # Update next round if necessary
        if self.current_battle['round'] < len(self.matches) - 1:
            next_round = self.matches[self.current_battle['round'] + 1]
            next_match_index = self.current_battle['match'] // 2
            next_match = next_round[next_match_index]
            
            # Determine which slot to fill (p1 or p2)
            if self.current_battle['match'] % 2 == 0:
                next_match['p1'] = winner
            else:
                next_match['p2'] = winner
        
        # Clear current battle
        self.current_battle = None
        
        return result

def create_tournament(battle_manager, num_players: int = 8) -> MugenTournament:
    """Create a new tournament with random selection of enabled characters"""
    enabled_chars = list(battle_manager.settings.get("enabled_characters", []))
    
    if len(enabled_chars) < num_players:
        raise ValueError(f"Not enough enabled characters for {num_players}-player tournament")
    
    # Select random characters
    players = random.sample(enabled_chars, num_players)
    
    # Create tournament
    return MugenTournament(battle_manager, players)

if __name__ == "__main__":
    # Test tournament creation and display
    from random_ai_battles import MugenBattleManager
    
    manager = MugenBattleManager()
    
    # Create tournament with 8 random characters
    tournament = create_tournament(manager, 8)
    
    print("Tournament Bracket:")
    print(tournament.get_bracket_display())
    
    # Main tournament loop
    while not tournament.is_complete():
        # Start next match
        match_info = tournament.start_next_match()
        if match_info:
            print(f"\nStarting match: {match_info['p1']} vs {match_info['p2']}")
        
        # Wait for result
        while True:
            result = tournament.check_match_result()
            if result:
                break
    
    print("\nTournament Complete!")
    print(f"Winner: {tournament.get_winner()}")
    
    print(tournament.get_bracket_display()) 